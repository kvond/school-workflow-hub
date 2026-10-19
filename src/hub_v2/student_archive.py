"""Private student records: verified work copies, original links and feedback.

Original Form uploads, assessments, delivery ticks and sharing never change.
IC identity is preferred. Exact school-email/course evidence or the teacher's
authorized unique-name fallback can file work. Name filing stays explicitly
identity-unverified and never authorizes feedback, grading or an IC write.
The index uses the existing private CAS store.
"""
import hashlib
import json
import posixpath
import re
import uuid
from copy import deepcopy
from io import BytesIO
from urllib.parse import quote, urlsplit
from zipfile import ZipFile, BadZipFile
import xml.etree.ElementTree as ET
from PIL import Image, ImageOps
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from time import monotonic

from . import google, work, named_archival_work
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import CloudState, Conflict, StoreUnavailable, key
from school_workflow_hub.core.runs import content_hash

ROOT_CONFIG = "HUB_STUDENT_RECORDS_ROOT_FOLDER"
RECORDS_CONFIG = "HUB_STUDENT_RECORDS_RECORDS_FOLDER"
SCOPE_ROOT_CONFIG = "HUB_STUDENT_RECORDS_IDENTITY_SCOPE_ROOT"
COURSES_CONFIG = "HUB_STUDENT_RECORDS_COURSE_FOLDERS"
ROSTER_CONFIG = "HUB_STUDENT_ARCHIVE_IC_ROSTER"
WORK_INDEX_CONFIG = "HUB_STUDENT_WORK_INDEX_URL"
CLASSES = {"biology": "Biology", "forensics": "Forensics", "anatomy": "A&P"}
SCHEMA = "hub-student-archive/v1"
SHORTCUT = "application/vnd.google-apps.shortcut"
PDF = "application/pdf"
TAG = "hubArchiveKey"
IMPORTED_ORIGINAL = "authenticated_schoology_original"
NAMED_ARCHIVE = named_archival_work.KIND
FIELDS = "id,name,mimeType,parents,driveId,trashed,permissions,capabilities,properties,shortcutDetails,createdTime,modifiedTime,version,md5Checksum,size"
# Original uploads need read access, not authority to list their private ACL.
# Keep full access checks on every archive destination and saved artifact.
SOURCE_FIELDS = ",".join(f for f in FIELDS.split(",") if f not in {"permissions", "capabilities", "shortcutDetails"})
MAX_STUDENTS, MAX_ITEMS = 500, 500
MAX_SCANS, MAX_HOLDS = 100, 500
LEASE_HEARTBEAT_SECONDS = 40


def _source_revision(meta):
    return {k: meta.get(k) for k in ("id", "mimeType", "version", "modifiedTime", "createdTime", "parents", "md5Checksum", "size", "trashed")}


def _native_parts_proof(raw, prefix):
    """Bind native text, styles, page order, notes and relationship-resolved images.

    Google's exporter renumbers media/themes, rewrites image container metadata
    and re-encodes embedded fonts. Native font declarations remain in XML.
    Neither ZIP names nor export-only font encodings are a content identity.
    """
    try:
        with ZipFile(BytesIO(raw)) as archive:
            names = sorted(n for n in archive.namelist() if n.startswith(prefix) and not n.endswith("/"))
            if not names or sum(archive.getinfo(n).file_size for n in names) > 120_000_000:
                raise Held("This native submission has no complete content within the verified size limit.")
            parts = {n: archive.read(n) for n in names}
        resources = {}
        for name, content in parts.items():
            if "/media/" in name:
                try:
                    with Image.open(BytesIO(content)) as image:
                        if image.width * image.height > 30_000_000 or getattr(image, "n_frames", 1) != 1:
                            raise ValueError("Use exact bytes for large or animated media")
                        image.load()
                        resources[name] = "image:" + content_hash([image.size, hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()])
                except Exception:
                    resources[name] = "media:" + hashlib.sha256(content).hexdigest()
            elif "/theme/" in name and name.endswith(".xml"):
                resources[name] = "theme:" + hashlib.sha256(ET.canonicalize(content.decode()).encode()).hexdigest()
            elif "/fonts/" in name:
                resources[name] = "native-font:" + posixpath.basename(name)
        normalized = []
        for name, content in parts.items():
            if name in resources:
                if not name.startswith(prefix + "fonts/"):
                    normalized.append([resources[name], resources[name]])
                continue
            if name.endswith(".rels"):
                tree = ET.fromstring(content)
                owner = name.replace("/_rels/", "/")[:-5]
                base = posixpath.dirname(owner)
                for relationship in tree:
                    if relationship.get("TargetMode") != "External":
                        target = posixpath.normpath(posixpath.join(base, relationship.get("Target", "")))
                        if target in resources:
                            relationship.set("Target", resources[target])
                tree[:] = sorted(tree, key=lambda child: child.get("Id", ""))
                content = ET.canonicalize(ET.tostring(tree, encoding="unicode")).encode()
            elif name.endswith(".xml"):
                content = ET.canonicalize(content.decode()).encode()
            normalized.append([name, hashlib.sha256(content).hexdigest()])
        normalized.sort()
        return {"sha256": content_hash(normalized), "basis": "native_document_content/v1", "parts": len(normalized)}
    except (BadZipFile, KeyError, ET.ParseError, UnicodeError):
        raise Held("This native submission did not export as a complete readable document.") from None


def _native_image_proof(url):
    location = urlsplit(url)
    if (location.scheme != "https" or not location.hostname or not location.hostname.endswith(".googleusercontent.com")
            or location.username or location.password or location.port not in (None, 443)):
        raise Held("The native presentation supplied an unverified image location; its copy remains held.")
    try:
        response = google._session().get(url, timeout=(5, 30), stream=True, allow_redirects=False)
        with response:
            if response.status_code != 200:
                raise Held("One native presentation image could not be verified.")
            chunks, size = [], 0
            for chunk in response.iter_content(1 << 20):
                size += len(chunk)
                if size > 15_000_000:
                    raise Held("A native presentation image exceeds the verified size limit.")
                chunks.append(chunk)
        raw = b"".join(chunks)
        with Image.open(BytesIO(raw)) as image:
            if image.width * image.height > 30_000_000 or getattr(image, "n_frames", 1) != 1:
                return {"basis": "exact_image_bytes", "sha256": hashlib.sha256(raw).hexdigest()}
            image.load(); pixels = ImageOps.exif_transpose(image).convert("RGBA")
            return {"basis": "image_pixels", "size": list(pixels.size), "sha256": hashlib.sha256(pixels.tobytes()).hexdigest(),
                    "color_profile_sha256": hashlib.sha256(image.info.get("icc_profile", b"")).hexdigest()}
    except Held:
        raise
    except Exception:
        raise Held("One native presentation image could not be read completely.") from None


def _presentation_content_proof(presentation, images):
    count = 0
    def normalize(value):
        nonlocal count
        if isinstance(value, dict):
            result = {}
            for name, item in value.items():
                if name == "contentUrl" and item:
                    count += 1
                    if item not in images:
                        raise Held("A native presentation image is missing from its exact content proof.")
                    result[name] = images[item]
                else:
                    result[name] = normalize(item)
            return result
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value
    normalized = normalize(presentation)
    if not isinstance(normalized.get("slides"), list) or not normalized["slides"]:
        raise Held("The native presentation has no complete slide content.")
    return {"sha256": content_hash(normalized), "basis": "native_presentation/v1", "slides": len(normalized["slides"]),
            "image_instances": count}


def _native_presentation_proof(token, ident, heartbeat=None):
    presentation = google.get_json("https://slides.googleapis.com/v1/presentations/" + quote(ident), token,
        {"fields": "pageSize,slides,masters,layouts,notesMaster"}, timeout=30)
    urls = set()
    def collect(value):
        if isinstance(value, dict):
            if value.get("contentUrl"):
                urls.add(value["contentUrl"])
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(presentation)
    if len(urls) > 500:
        raise Held("This native presentation exceeds the verified image limit.")
    ordered = sorted(urls)
    with ThreadPoolExecutor(max_workers=4) as pool:
        images = {}
        for url, proof in zip(ordered, pool.map(_native_image_proof, ordered)):
            if heartbeat:
                heartbeat()
            images[url] = proof
    return _presentation_content_proof(presentation, images)


def _work_bytes(token, meta, heartbeat=None):
    """One bounded exact-file download/export; this never writes or follows redirects."""
    mime, ident = meta["mimeType"], meta["id"]
    native = {"application/vnd.google-apps.presentation": google.PPTX,
              "application/vnd.google-apps.document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    if mime in native:
        url, params = "https://www.googleapis.com/drive/v3/files/%s/export" % quote(ident), {"mimeType": native[mime]}
    else:
        url, params = "https://www.googleapis.com/drive/v3/files/" + quote(ident), {"alt": "media", "supportsAllDrives": "true"}
    try:
        response = google._session().get(url, params=params, timeout=(5, 60), stream=True, allow_redirects=False,
                                         headers={"Authorization": "Bearer " + token})
        with response:
            if response.status_code != 200:
                raise Held("This submitted work could not be downloaded for copy verification.")
            chunks, size = [], 0
            for chunk in response.iter_content(1 << 20):
                if heartbeat:
                    heartbeat()
                size += len(chunk)
                if size > google.MAX_DECK_BYTES:
                    raise Held("This submitted work exceeds the verified copy size limit.")
                chunks.append(chunk)
    except Held:
        raise
    except Exception:
        raise Held("This submitted work could not be read completely; no new copy is confirmed.") from None
    raw = b"".join(chunks)
    if not raw:
        raise Held("This submitted work has no readable content to copy.")
    return raw


def _work_content(token, meta, heartbeat=None):
    """Read a bounded source/copy; native export ZIP timestamps are not content."""
    mime = meta["mimeType"]
    if mime == "application/vnd.google-apps.presentation":
        return _native_presentation_proof(token, meta["id"], heartbeat)
    raw = _work_bytes(token, meta, heartbeat)
    native = {"application/vnd.google-apps.presentation", "application/vnd.google-apps.document"}
    if mime in native:
        prefix = "ppt/" if mime.endswith("presentation") else "word/"
        return _native_parts_proof(raw, prefix)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "basis": "file_bytes", "size": len(raw),
            "md5": hashlib.md5(raw).hexdigest()}


def _verify_work_copy(meta, record):
    _private(meta, parent=record["parent"])
    props = meta.get("properties") or {}
    if (meta.get("mimeType") != record["mime"] or props.get(TAG) != record["record_key"]
            or props.get("hubCopiedSource") != record["source_file_id"]
            or props.get("hubSourceRevision") != record["source_revision_key"]
            or not re.fullmatch(r"[a-f0-9]{64}", (record.get("content_proof") or {}).get("sha256", ""))):
        raise Held("A copied submission no longer matches its verified source or student folder.")
    if record.get("copied_modified_at") and meta.get("modifiedTime") != record["copied_modified_at"]:
        raise Held("A student's filed work copy changed after verification. The original remains available.")
    proof = record.get("content_proof") or {}
    if proof.get("basis") == "file_bytes" and (meta.get("md5Checksum") != proof.get("md5")
            or str(meta.get("size")) != str(proof.get("size"))):
        raise Held("A student's filed work copy changed after verification. The original remains available.")
    return meta


def _verify_work_export(meta, record):
    _private(meta, parent=record["parent"])
    revision = record.get("source_revision") or {}
    if (record.get("source_kind") != "submitted_work_export" or record.get("mime") != google.PPTX
            or meta.get("mimeType") != google.PPTX or (meta.get("properties") or {}).get(TAG) != record["record_key"]
            or record.get("source_mime") != "application/vnd.google-apps.presentation"
            or revision.get("id") != record.get("source_file_id") or revision.get("mimeType") != record.get("source_mime")
            or str(revision.get("version")) != str(record.get("source_version")) or revision.get("trashed") is not False
            or not revision.get("modifiedTime") or record.get("source_revision_key") != content_hash(revision)
            or (record.get("source_content_proof") or {}).get("basis") != "native_presentation/v1"
            or not re.fullmatch(r"[a-f0-9]{64}", (record.get("source_content_proof") or {}).get("sha256", ""))
            or not re.fullmatch(r"[a-f0-9]{64}", record.get("media_sha256", ""))
            or meta.get("md5Checksum") != record.get("media_md5")
            or str(meta.get("size")) != str(record.get("size"))):
        raise Held("An exported work snapshot no longer matches its verified private source and bytes.")
    return meta


class Held(ValueError):
    pass


def _problem(error):
    if isinstance(error, Conflict):
        return "Another student-records operation is running or its index changed. Reload after it finishes."
    if isinstance(error, StoreUnavailable):
        return "The private archive index could not be confirmed. Sources and saved Hub feedback are retained."
    return str(error)


def _configuration(config):
    try:
        root = config.get(ROOT_CONFIG, "")
        courses = json.loads(config.get(COURSES_CONFIG, "{}"))
        if (not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", root)
                or not isinstance(courses, dict) or set(courses) != set(CLASSES)
                or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", v)
                       for v in courses.values()) or len(set(courses.values())) != 3 or root in courses.values()):
            raise ValueError()
        records = config.get(RECORDS_CONFIG)
        if records and (not isinstance(records, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", records)
                        or records == root or records in courses.values()):
            raise ValueError()
        return root, courses
    except (ValueError, TypeError):
        raise Held("Student records are not connected to their private class folders yet.") from None


def enabled(config):
    try:
        actual, _ = _configuration(config)
        _scope_root(config, actual)
        return True
    except Held:
        return False


def _scope_root(config, actual_root):
    root = config.get(SCOPE_ROOT_CONFIG) or actual_root
    if not isinstance(root, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", root):
        raise Held("The archive's stable identity scope needs checking before filing.")
    return root


def _clean(value, limit=180):
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).split())[:limit]


def _date(stamp):
    moment = work.when(stamp)
    if moment is None:
        raise Held("This record has no verified turn-in or saved-feedback date.")
    return work.school_day(moment).isoformat()


def _url(ident, folder=False):
    return "https://drive.google.com/drive/folders/" + ident if folder else "https://drive.google.com/open?id=" + ident


def _read(port, ident):
    return port.read([ident]).documents[ident]


def _put(port, ident, value, old=None):
    try:
        port.commit({ident: (value, old.version if old else None)})
    except (Conflict, StoreUnavailable):
        actual = _read(port, ident)
        if not actual or actual.value != value:
            raise
    actual = _read(port, ident)
    if not actual or actual.value != value:
        raise StoreUnavailable("archive_index_readback_unconfirmed")
    return actual


def _private(meta, *, parent=None, folder=False, write=False):
    if (meta.get("trashed") is not False or not meta.get("id")
            or (folder and meta.get("mimeType") != google.FOLDER)
            or (parent and meta.get("parents") != [parent])):
        raise Held("A student-records folder or file changed. Its location needs checking.")
    permissions = meta.get("permissions")
    if (not isinstance(permissions, list) or not permissions
            or any(p.get("type") != "user" or not (p.get("emailAddress") or p.get("id"))
                   for p in permissions)):
        raise Held("Student records need verified private, named-user access before filing.")
    if write and not (meta.get("capabilities") or {}).get("canAddChildren"):
        raise Held("The Hub's existing account cannot file in this private folder yet.")
    return meta


def _import_provenance(value):
    """An audited attachment receipt, never a Form event or grade identity."""
    fields = {"provider", "original_url", "assignment_id", "user_id", "revision", "attachment_path",
              "archive_sha256", "source_receipt_sha256", "identity_proof_sha256", "submitted_local_date",
              "submitted_local_time", "submitted_timezone", "worksheet_teacher_id", "worksheet_role"}
    if not isinstance(value, dict) or set(value) != fields or value.get("provider") != "schoology":
        raise Held("Imported work needs its exact authenticated original attachment receipt.")
    if not isinstance(value.get("original_url"), str):
        raise Held("The imported original's authenticated URL needs checking.")
    try:
        address = urlsplit(value["original_url"])
        port = address.port
    except ValueError:
        raise Held("The imported original's authenticated URL needs checking.") from None
    if (address.scheme != "https" or not address.hostname or not address.hostname.endswith(".schoology.com")
            or address.username or address.password or port is not None or address.query or address.fragment
            or not re.fullmatch(r"[1-9][0-9]{0,19}", str(value.get("assignment_id", "")))
            or not re.fullmatch(r"[1-9][0-9]{0,19}", str(value.get("user_id", "")))
            or address.path != "/assignment/" + str(value["assignment_id"]) + "/dropbox/view/" + str(value["user_id"])
            or not isinstance(value.get("revision"), int) or isinstance(value["revision"], bool) or not 1 <= value["revision"] <= 100):
        raise Held("The imported original URL, student submission and revision do not agree.")
    for field in ("archive_sha256", "source_receipt_sha256", "identity_proof_sha256"):
        if not isinstance(value.get(field), str) or not re.fullmatch(r"[a-f0-9]{64}", value[field]):
            raise Held("Imported work needs retained source and identity proof hashes.")
    path = value.get("attachment_path")
    if (not isinstance(path, str) or not 1 <= len(path) <= 1500 or path.startswith("/") or "\\" in path
            or any(part in {"", ".", ".."} for part in path.split("/")) or any(ord(c) < 32 for c in path)):
        raise Held("The original attachment path needs checking.")
    try:
        day = datetime.strptime(value["submitted_local_date"], "%Y-%m-%d").date()
        if day.isoformat() != value["submitted_local_date"]:
            raise ValueError()
        if value["submitted_local_time"] is not None:
            if datetime.strptime(value["submitted_local_time"], "%H:%M").strftime("%H:%M") != value["submitted_local_time"]:
                raise ValueError()
    except (ValueError, TypeError):
        raise Held("The imported original's observed submission date needs checking.") from None
    if (value["submitted_timezone"] not in {None, "America/New_York"}
            or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", value.get("worksheet_teacher_id") or "")
            or not isinstance(value.get("worksheet_role"), str) or value["worksheet_role"] != _clean(value["worksheet_role"], 300)
            or not value["worksheet_role"]):
        raise Held("The imported worksheet's source role needs checking.")
    return dict(value)


def _verify_imported_original(meta, record):
    _private(meta, parent=record.get("parent"))
    provenance = _import_provenance(record.get("import_provenance"))
    proof = record.get("copied_content_proof") or {}
    if (record.get("kind") != "work" or record.get("source_kind") != IMPORTED_ORIGINAL
            or meta.get("id") != record.get("drive_file_id") or meta.get("mimeType") not in {PDF, "image/png"}
            or meta.get("mimeType") != record.get("mime") or record.get("date") != provenance["submitted_local_date"]
            or record.get("date_basis") != "authenticated_external_local_date"
            or any(record.get(k) for k in ("source_file_id", "form_id", "assignment_form_id", "submitted_at", "source_response_id"))
            or proof.get("basis") != "file_bytes" or not re.fullmatch(r"[a-f0-9]{64}", proof.get("sha256") or "")
            or not re.fullmatch(r"[a-f0-9]{32}", proof.get("md5") or "")
            or meta.get("md5Checksum") != proof.get("md5") or str(meta.get("size")) != str(proof.get("size"))
            or _source_revision(meta) != record.get("imported_file_revision")
            or meta.get("properties", {}) != record.get("imported_properties")
            or meta.get("permissions") != record.get("imported_permissions")
            or (meta.get("properties") or {}).get(TAG)):
        raise Held("This imported original changed or no longer matches its private source receipt.")
    return meta


def register_imported_original(token, config, class_key, student_key, file_id, *, provenance,
                               original_bytes, expected_metadata, expected_student,
                               assignment, source_context, filing_note=""):
    """Trusted filing worker only: register an already uploaded, audited original.

    The caller must authenticate the external receipt and existing student-folder
    association. This adds one CAS artifact and its student attachment atomically;
    it creates no Drive file, property, Form event, feedback, assessment or grade.
    A lost CAS response is read back once, never retried. Date is the observed
    external local day; registration time remains a separate private fact.
    """
    provenance = _import_provenance(provenance)
    if (not isinstance(original_bytes, bytes) or not 0 < len(original_bytes) <= google.MAX_DECK_BYTES
            or not isinstance(expected_metadata, dict) or not isinstance(expected_student, dict)
            or not isinstance(assignment, str) or not assignment or assignment != _clean(assignment, 500)
            or not isinstance(filing_note, str) or filing_note != _clean(filing_note, 1500)
            or not isinstance(source_context, dict) or set(source_context) != {"class_key", "school_year", "week"}
            or source_context["class_key"] != class_key or not re.fullmatch(r"20[0-9]{2}-[0-9]{2}", source_context.get("school_year", ""))
            or not source_context.get("week") or source_context["week"] != _clean(source_context["week"], 100)):
        raise Held("The imported original's exact bytes and filing context need checking.")
    actual_root, course = _location(token, config, class_key)
    root, port = _scope_root(config, actual_root), configured_store(config)
    index_key = key("archiveclass", [root, class_key])
    ident = key("archiveimportedwork", [root, class_key, student_key, provenance["original_url"],
                provenance["revision"], provenance["attachment_path"]])
    snapshot = port.read([index_key, student_key, ident])
    index, owner, existing = (snapshot.documents[k] for k in (index_key, student_key, ident))
    student = owner.value if owner else {}
    if (not index or index.value.get("schema") != SCHEMA or not owner or student != expected_student
            or student.get("status") != "verified" or student.get("schema") != SCHEMA
            or student.get("student_key") != student_key or student.get("record_key") != student_key
            or student.get("class_key", class_key) != class_key
            or index.value.get("students", {}).get(student_key) != student.get("drive_file_id")):
        raise Held("Imported work must belong to one unchanged existing indexed student folder.")
    section = _private(google.drive_file(token, student["parent"], FIELDS), parent=course["id"], folder=True)
    folder = _private(google.drive_file(token, student["drive_file_id"], FIELDS), parent=section["id"], folder=True)
    meta = google.drive_file(token, file_id, FIELDS)
    if meta != expected_metadata or meta.get("id") != file_id:
        raise Held("The uploaded original's full metadata changed since its filing readback.")
    if not ((meta.get("mimeType") == PDF and original_bytes.startswith(b"%PDF-"))
            or (meta.get("mimeType") == "image/png" and original_bytes.startswith(b"\x89PNG\r\n\x1a\n"))):
        raise Held("The imported original's actual file format needs checking.")
    record = {"schema": SCHEMA, "record_key": ident, "status": "verified", "kind": "work",
        "source_kind": IMPORTED_ORIGINAL, "class_key": class_key, "student_key": student_key,
        "drive_file_id": file_id, "parent": folder["id"], "mime": meta.get("mimeType"), "name": meta.get("name"),
        "url": _url(file_id), "date": provenance["submitted_local_date"], "date_basis": "authenticated_external_local_date",
        "assignment": assignment, "source_context": dict(source_context), "lesson_status": "verified",
        "import_provenance": provenance, "imported_file_revision": _source_revision(meta),
        "imported_properties": meta.get("properties", {}), "imported_permissions": meta.get("permissions"),
        "copied_content_proof": {"basis": "file_bytes", "sha256": hashlib.sha256(original_bytes).hexdigest(),
                                "md5": hashlib.md5(original_bytes).hexdigest(), "size": len(original_bytes)},
        "filing_note": filing_note, "registered_at": existing.value.get("registered_at") if existing else snapshot.read_time}
    _verify_imported_original(meta, record)
    items = dict(student.get("items", {}))
    if len(items) > MAX_ITEMS:
        raise Held("This student's archive exceeds its verified record limit.")
    attached = _batch_read(port, list(items))
    if any(doc and doc.value.get("drive_file_id") == file_id and other != ident for other, doc in attached.items()):
        raise Held("This filed original is already attached to a different source record.")
    if existing and existing.value != record:
        raise Held("This original attachment is already registered with different bytes, provenance or location.")
    expected_item = {"kind": "work", "date": record["date"]}
    if ident in items and (not existing or items[ident] != expected_item):
        raise Held("This student's imported-original attachment needs checking.")
    items[ident] = expected_item
    if len(items) > MAX_ITEMS:
        raise Held("This student's archive exceeds its verified record limit.")
    # Bookend all destination resources before the atomic, version-bound CAS.
    if (google.drive_file(token, file_id, FIELDS) != meta
            or google.drive_file(token, student["drive_file_id"], FIELDS) != folder
            or google.drive_file(token, student["parent"], FIELDS) != section):
        raise Held("The original or existing private destination changed during registration.")
    desired = {index_key: index.value, student_key: {**student, "items": items}, ident: record}
    reused = bool(existing and desired[student_key] == student)
    if not reused:
        try:
            port.commit({k: (v, snapshot.documents[k].version if snapshot.documents[k] else None) for k, v in desired.items()})
        except (Conflict, StoreUnavailable):
            actual = port.read(list(desired)).documents
            if any(not actual[k] or actual[k].value != v for k, v in desired.items()):
                raise
    actual = port.read(list(desired)).documents
    if any(not actual[k] or actual[k].value != v for k, v in desired.items()):
        raise StoreUnavailable("imported_original_registration_readback_unconfirmed")
    if (google.drive_file(token, file_id, FIELDS) != meta
            or google.drive_file(token, student["drive_file_id"], FIELDS) != folder
            or google.drive_file(token, student["parent"], FIELDS) != section):
        raise Held("The registered original or private destination changed; preserve its record for review.")
    return {"status": "verified", "record_key": ident, "drive_file_id": file_id,
            "student_key": student_key, "filed": 0 if reused else 1, "reused": reused,
            "source_kind": IMPORTED_ORIGINAL, "registered_at": record["registered_at"]}


def _location(token, config, cls, write=False):
    if cls not in CLASSES:
        raise Held("Choose one class for student records.")
    root, courses = _configuration(config)
    root_meta = _private(google.drive_file(token, root, FIELDS), folder=True, write=write)
    # A separate private archive must never become a source-discovery tree.
    current, seen = root_meta, set()
    turnin = config.get("HUB_SUBMISSIONS_ROOT_FOLDER")
    for _ in range(12):
        if current["id"] == turnin:
            raise Held("Student records must stay outside the Turn-In source folders.")
        parents = current.get("parents") or []
        if not parents:
            break
        if len(parents) != 1 or parents[0] in seen:
            raise Held("The private archive's parent location needs checking.")
        seen.add(parents[0])
        current = google.drive_file(token, parents[0], "id,parents,trashed")
        if current.get("trashed") is not False:
            raise Held("The private archive has an unavailable parent folder.")
    else:
        raise Held("The private archive's parent location could not be fully checked.")
    parent = root
    if config.get(RECORDS_CONFIG):
        records = _private(google.drive_file(token, config[RECORDS_CONFIG], FIELDS), parent=root, folder=True, write=write)
        parent = records["id"]
    course = _private(google.drive_file(token, courses[cls], FIELDS), parent=parent, folder=True, write=write)
    return root, course


def feedback_location(token, config, cls):
    """Verified private course location for legacy packets and indexed PDFs; no writes."""
    return _location(token, config, cls)


def _roster(config):
    raw = config.get(ROSTER_CONFIG)
    if not raw:
        return None
    try:
        value = json.loads(raw)
        if not isinstance(value, list) or len(value) > MAX_STUDENTS:
            raise ValueError()
        for row in value:
            if (not isinstance(row, dict) or row.get("class_key") not in CLASSES
                    or not isinstance(row.get("active", True), bool)):
                raise ValueError()
        return value
    except (ValueError, TypeError):
        raise Held("The verified IC roster mapping needs checking before student filing.") from None


def _identity(config, cls, row):
    email = str(row.get("school_email") or row.get("email") or "").strip().lower()
    native = str(row.get("ic_student_id") or row.get("student_id") or "").strip()
    verified_native = row.get("ic_identity_verified") is True and bool(re.fullmatch(r"\d{2,20}", native))
    trusted_email = row.get("matched_by") == "email" and row.get("source") == "form"
    domains = {d.strip().lower().lstrip("@") for d in str(config.get("HUB_SCHOOL_EMAIL_DOMAINS", "")).split(",") if d.strip()}
    trusted_email = bool(trusted_email and re.fullmatch(r"[^@\s]+@[^@\s]+", email)
                         and email.rsplit("@", 1)[1] in domains)
    canonical = _roster(config)
    if canonical is not None:
        matches = [p for p in canonical if p["class_key"] == cls and p.get("active", True)
                   and ((trusted_email and str(p.get("school_email", "")).lower() == email)
                        or (verified_native and str(p.get("student_id", "")) == native))]
        if len(matches) != 1:
            raise Held("This submission is not matched to exactly one current IC roster student.")
        person = matches[0]
        email = str(person.get("school_email") or email).lower()
        native = str(person.get("student_id") or "")
        verified_native = bool(re.fullmatch(r"\d{2,20}", native))
        row = {**row, **{k: person[k] for k in ("first", "last", "section") if person.get(k)}}
    if not trusted_email and not verified_native:
        raise Held("This record needs a verified school email or IC student identity; a name alone is not enough.")
    first, last, section = (_clean(row.get(k)) for k in ("first", "last", "section"))
    if not first or not last or not section:
        raise Held("This student's current class, section and roster display name need checking.")
    return {"identity": "ic:" + native if verified_native else "email:" + email,
            "basis": "verified_ic_student_id" if verified_native else "school_email_and_course",
            "identity_status": "verified",
            "student_id": native if verified_native else "", "school_email": email if trusted_email else "",
            "first": first, "last": last, "section": section, "name": last + ", " + first}


def _name_key(person):
    return "name:" + content_hash([person["section"], work.roster.plain(person["last"]),
                                  work.roster.plain(person["first"])])


def _same_name(a, b):
    return all(work.roster.plain(a.get(k)) == work.roster.plain(b.get(k))
               for k in ("section", "first", "last"))


def _filing_roster(token, config, cls):
    """Fresh course roster with duplicate display names retained for safety.

    The general work loader deduplicates display names; that is unsuitable
    evidence for a filing-only unique-name decision.
    """
    roster_id = config.get("HUB_ROSTER_SPREADSHEET_ID")
    if not roster_id:
        raise Held("The current course roster is unavailable. Keep this upload and ask for a named resubmission.")
    try:
        choices = json.loads(config.get("HUB_ROSTER_TABS") or "{}")
        layouts = json.loads(config.get("HUB_ROSTER_COLUMN_LAYOUTS") or "{}")
        if not isinstance(choices, dict) or not isinstance(layouts, dict):
            raise ValueError()
    except (ValueError, TypeError):
        raise Held("The course roster's tab or name-column configuration needs checking before filing by name.") from None
    _, tabs = google.sheet_tabs(token, roster_id)
    by_id = {ident: title for title, ident in tabs.items()}
    wanted = {}
    for section in work.roster.SECTIONS[cls]:
        choice = choices.get(section, section)
        title = by_id.get(choice) if isinstance(choice, int) else (choice if choice in tabs else
            next((t for t in tabs if work.roster.plain(t) == work.roster.plain(choice)), None))
        if not title:
            raise Held("The current %s roster tab could not be confirmed. Keep its uploads for review." % section)
        if title in wanted and wanted[title] != section:
            raise Held("Two course sections point to one roster tab. Confirm their separate rosters before filing by name.")
        wanted[title] = section
    grids = google.sheet_grid(token, roster_id, ["'%s'!A1:B200" % t.replace("'", "''") for t in sorted(wanted)])
    rows = []
    for title, section in wanted.items():
        if title not in grids:
            raise Held("The current course roster could not be read completely. Keep its uploads for review.")
        if any(r >= 200 and (cell.get("formattedValue") or cell.get("userEnteredValue"))
               for (r, _), cell in grids[title].items()):
            raise Held("The course roster reaches the reading limit. Confirm its complete roster before filing by name.")
        parsed, skipped = work.roster.parse_tab(grids[title], cls, section, layouts)
        if skipped or any(p["section"] not in work.roster.SECTIONS[cls] for p in parsed):
            raise Held("The current course roster has unreadable names or sections. Keep its uploads for review.")
        rows.extend(parsed)
    work.roster.orient(rows, set())  # Reuse the source reader's confirmed orientation, if any.
    if not rows:
        raise Held("The current course roster has no confirmed student names. Keep its uploads for review.")
    if any(p.get("order") == "assumed" for p in rows):
        raise Held("The course roster's first/last name columns are unconfirmed. Add name-column headers or confirm its configured column order before filing by name.")
    return rows


def _filename_owner(config, meta, students):
    """A full name/known username in a filename, never a surname guess."""
    prefix = config.get("HUB_EMAIL_USERNAME_PREFIX", "s.")
    name = str(meta.get("name") or "")
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name)
    candidates = [(work.roster.uploader_name(name), "uploader_name"), (stem, "full_name")]
    lead = re.match(r"^(.+?)\s+[—–-]\s+", stem)
    if lead:
        candidates.append((lead.group(1), "full_name"))
    if meta.get("shown_name"):
        candidates.append((str(meta["shown_name"]).split(".", 1)[0], "teacher_named_prefix"))
    username = work.roster.file_username(name, prefix)
    found, surname = {}, False
    for text, basis in candidates + ([(username, "school_username")] if username else []):
        target = work.roster.username_key(text, prefix) if basis == "school_username" else work.roster.compact(text)
        if not target:
            continue
        for index, student in enumerate(students):
            if target in work.roster.aliases(student) or target == work.roster.compact(student["last"] + " " + student["first"]):
                found[index] = basis
            elif target == work.roster.compact(student["last"]):
                surname = True
    if len(found) > 1:
        raise Held("This filename matches more than one roster student. Ask for a resubmission with an unambiguous full name and section.")
    if not found:
        if surname:
            raise Held("This upload gives only a surname. Ask the student to resubmit with first and last name and section.")
        raise Held("This filename does not match a student in this class's roster. Check the student's name, class and section; request a corrected named resubmission if needed.")
    index, basis = next(iter(found.items()))
    return students[index], basis


def _filing_identity(config, cls, row, roster_reader, source_reader):
    # Strict identity remains the only path used by saved native feedback.
    email = str(row.get("school_email") or row.get("email") or "").lower().strip()
    domains = {d.strip().lower().lstrip("@") for d in config.get("HUB_SCHOOL_EMAIL_DOMAINS", "").split(",") if d.strip()}
    native = str(row.get("ic_student_id") or row.get("student_id") or "")
    strong = (row.get("ic_identity_verified") is True and bool(re.fullmatch(r"\d{2,20}", native))) or (
        row.get("matched") is True and row.get("matched_by") == "email" and row.get("source") == "form"
        and bool(re.fullmatch(r"[^@\s]+@[^@\s]+", email)) and email.rsplit("@", 1)[1] in domains)
    if strong:
        return {**_identity(config, cls, row), "identity_status": "verified"}
    students = roster_reader()
    owners = []
    for file in row.get("files", []):
        if file.get("trashed") or file.get("problem"):
            continue
        owners.append(_filename_owner(config, source_reader(file["id"]), students))
    if not owners:
        raise Held("This upload needs a readable file with a clear full student name. Ask for a named resubmission.")
    student, basis = owners[0]
    if student.get("class_key") != cls or student.get("section") not in work.roster.SECTIONS[cls]:
        raise Held("This filename's roster match belongs to another course or section. Ask for a corrected named resubmission.")
    if any(not _same_name(student, other) for other, _ in owners[1:]):
        raise Held("This submission contains files named for different students. Ask for separate named resubmissions.")
    if row.get("matched") and not _same_name(student, row):
        raise Held("The filename disagrees with its recorded student or section. Ask for a corrected named resubmission.")
    if row.get("section") and work.roster.plain(row["section"]) != work.roster.plain(student["section"]):
        raise Held("The filename disagrees with its recorded section. Ask for a corrected named resubmission.")
    canonical = _roster(config)
    if canonical is not None:
        matches = [p for p in canonical if p["class_key"] == cls and p.get("active", True) and _same_name(p, student)]
        if len(matches) != 1:
            raise Held("This filename does not match exactly one current IC course and section student. Keep it for review.")
    first, last, section = (_clean(student[k]) for k in ("first", "last", "section"))
    person = {"basis": "unique_name_for_filing", "identity_status": "identity_unverified", "student_id": "",
              "school_email": "", "first": first, "last": last, "section": section, "name": last + ", " + first,
              "name_evidence": basis}
    person["identity"] = _name_key(person)
    return person


def _work_date(version, source):
    if version.get("source") == "form":
        return _date(version.get("submitted_at")), "form_receipt", version.get("submitted_at")
    if version.get("source") != "folder":
        raise Held("This work's original source and date could not be confirmed. Keep it for review.")
    stamped = work.name_time(source.get("name"), source.get("createdTime"))
    if stamped:
        return _date(stamped), "filename_submission", stamped
    return _date(source.get("createdTime")), "file_created", None


def filing_person_for_legacy_label(token, config, class_key, label, *, section=None):
    """Filing-only identity for an independently audited source-packet label.

    The caller must bind the resulting PDF to the original source/version,
    verified date and exact student span. This never creates a native draft
    identity, authorizes a grade, or infers a missing first name.
    """
    if class_key not in CLASSES or (section and section not in work.roster.SECTIONS[class_key]):
        raise Held("The legacy feedback's course and section need checking before filing.")
    rows = _filing_roster(token, config, class_key)
    target = work.roster.compact(re.sub(r"^student\s*:\s*", "", _clean(label), flags=re.I))
    matches = []
    for row in rows:
        if section and row["section"] != section:
            continue
        first = work.roster.first_names(row["first"])
        legal = work.roster.plain(re.sub(r"\([^)]*\)", " ", row["first"])).split()
        if len(legal) > 1:
            first.add(legal[0] + " " + " ".join(part[0] for part in legal[1:]))
        last = work.roster.plain(row["last"])
        surnames = {last, last[:1]}
        variants = {work.roster.compact(f + " " + s) for f in first for s in surnames if f and s}
        variants.add(work.roster.compact(last + " " + row["first"]))
        if target and target in variants:
            matches.append(row)
    if len(matches) != 1:
        raise Held("This legacy feedback label does not identify one unique course student. Retain its original packet for teacher identity review.")
    row = matches[0]
    canonical = _roster(config)
    if canonical is not None and len([p for p in canonical if p["class_key"] == class_key
                                     and p.get("active", True) and _same_name(p, row)]) != 1:
        raise Held("The legacy label does not match one current IC course and section student. Retain the original packet.")
    person = {"first": _clean(row["first"]), "last": _clean(row["last"]), "section": row["section"],
              "name": _clean(row["last"]) + ", " + _clean(row["first"]), "student_id": "", "school_email": "",
              "basis": "unique_name_for_filing", "identity_status": "identity_unverified",
              "name_evidence": "audited_legacy_full_first_name_and_surname_or_initial"}
    person["identity"] = _name_key(person)
    return person


def _source_id(url):
    for pattern in (r"https://drive\.google\.com/open\?id=([A-Za-z0-9_-]{10,200})",
                    r"https://docs\.google\.com/presentation/d/([A-Za-z0-9_-]{10,200})/edit"):
        found = re.fullmatch(pattern, str(url or ""))
        if found:
            return found.group(1)
    return None


class _Run:
    def __init__(self, token, write_token, config, cls):
        self.token, self.write_token, self.config, self.cls = token, write_token, config, cls
        self.actual_root, self.course = _location(token, config, cls, write=True)
        self.root = _scope_root(config, self.actual_root)
        self.port = configured_store(config)
        self.cloud = CloudState(self.port)
        task = self.cloud.create_task({"workflow": "student-archive", "class_key": cls,
                                      "assignment_key": key("archivescope", self.root)})
        request = "archive-" + uuid.uuid4().hex
        self.claim = self.cloud.claim(task, owner="student-archive", request_id=request,
                                      input_hash=content_hash([self.root, cls, request]))
        self._renewed_at, self._lease_error = monotonic(), None
        self.index_key = key("archiveclass", [self.root, cls])
        self.student_records = None
        self.feedback_students = set()
        self.feedback_checklists = None

    def renew(self):
        if self._lease_error is not None:
            raise self._lease_error
        try:
            self.claim = self.cloud.renew(self.claim)
        except (Conflict, StoreUnavailable) as error:
            self._lease_error = error
            raise
        self._renewed_at = monotonic()

    def heartbeat(self, *, force=False):
        if self._lease_error is not None:
            raise self._lease_error
        # Monotonic time only schedules checks. The cloud's current ownership,
        # fencing counter and server clock decide whether renewal is allowed.
        if force or monotonic() - self._renewed_at >= LEASE_HEARTBEAT_SECONDS:
            self.renew()

    def put(self, ident, value, old=None):
        self.heartbeat()
        return _put(self.port, ident, value, old)

    def finish(self, result):
        if result.get("status") == "verified" and self.feedback_students:
            from . import student_feedback_checklist
            self.feedback_checklists = student_feedback_checklist.refresh_run(self, sorted(self.feedback_students))
            result["feedback_checklists"] = self.feedback_checklists
            self.feedback_students.clear()
        self.heartbeat(force=True)
        try:
            self.cloud.finish(self.claim, records={"archive": result})
        except (Conflict, StoreUnavailable) as error:
            self._lease_error = error
            raise

    def create(self, ident, name, parent, mime, *, target=None, pdf=None, media=None, attributes=None):
        self.heartbeat()
        old = _read(self.port, ident)
        expected = {"name": name, "parent": parent, "mime": mime, "target": target}
        if old:
            record = old.value
            if media is not None and hashlib.sha256(media).hexdigest() != record.get("media_sha256"):
                raise Held("This reserved work snapshot has different bytes. Preserve its existing reservation.")
            if any(record.get(k) != v for k, v in expected.items() if k != "name"):
                raise Held("An archive record already belongs to a different source or student. It needs review.")
            # A repeated Drive upload can appear in several Form revisions.
            # Preserve every observation below, while its one shortcut keeps
            # the latest confirmed submission label instead of an older date.
            if (mime == SHORTCUT and attributes
                    and ((work.when(record.get("submitted_at"))
                          and (attributes.get("date_basis") == "file_created"
                               or (work.when(attributes.get("submitted_at"))
                                   and work.when(record["submitted_at"]) > work.when(attributes["submitted_at"]))))
                         or (record.get("lesson_status") == "verified" and attributes.get("lesson_status") == "unverified")
                         or (isinstance(record.get("form_id"), str)
                             and re.fullmatch(r"[A-Za-z0-9_-]{10,200}", record["form_id"])
                             and not attributes.get("form_id")))):
                name = record["name"]
                attributes = {k: record[k] if k in record else v for k, v in attributes.items()}
                if work.when(record.get("submitted_at")) and "date_basis" not in record:
                    attributes["date_basis"] = "form_receipt"
            if (mime == SHORTCUT and attributes and record.get("identity_status", "verified") == "verified"
                    and attributes.get("identity_status") == "identity_unverified"):
                attributes = {**attributes, "identity_status": "verified",
                              "identity_basis": record.get("identity_basis", "retained_verified_source")}
        else:
            existing = [item for item in google.folder_children(self.token, parent)
                        if (item.get("properties") or {}).get(TAG) == ident
                        or (mime == google.FOLDER and item.get("mimeType") == google.FOLDER
                            and item.get("name") == name)]
            if existing:
                raise Held("An existing archive folder or record has no confirmed index. Preserve it for identity review.")
            self.renew()
            record = {"schema": SCHEMA, "record_key": ident,
                      "drive_file_id": google.archive_file_id(self.write_token, shortcut=mime == SHORTCUT),
                      "status": "reserved", **expected, **(attributes or {})}
            if pdf is not None:
                record.update(pdf_sha256=hashlib.sha256(pdf).hexdigest(), pdf_md5=hashlib.md5(pdf).hexdigest(), size=len(pdf))
            elif media is not None:
                record.update(media_sha256=hashlib.sha256(media).hexdigest(), media_md5=hashlib.md5(media).hexdigest(), size=len(media))
            old = self.put(ident, record)
        file_id = record["drive_file_id"]
        try:
            meta = google.drive_file(self.token, file_id, FIELDS)
        except google.GoogleError as error:
            if error.status != 404:
                raise Held("The Hub could not verify this existing archive file.") from None
            if record["status"] != "reserved":
                raise Held("An earlier archive creation was not confirmed. Check its reserved file before retrying.") from None
            self.renew()
            record = {**record, "status": "creating"}
            old = self.put(ident, record, old)
            metadata = {"id": file_id, "name": name, "mimeType": mime, "parents": [parent], "properties": {TAG: ident}}
            if target:
                metadata["shortcutDetails"] = {"targetId": target}
            creation_error = None
            known_quota = False
            self.renew()
            try:
                google.archive_create(self.write_token, metadata, pdf if pdf is not None else media)
            except google.GoogleError as error:
                creation_error = str(error)  # Helpers only emit bounded, redacted explanations.
                known_quota = (mime == PDF and error.status == 403
                               and creation_error.startswith("Drive storage quota prevents this PDF upload."))
            try:
                meta = google.drive_file(self.token, file_id, FIELDS)
            except google.GoogleError as read_error:
                if known_quota and read_error.status == 404:
                    record = {**record, "creation_failure": "storage_quota", "failure_absence_verified": True,
                              "failure_checked_at": datetime.now(timezone.utc).isoformat(),
                              "attempt_parent_drive_id": self.course.get("driveId")}
                    old = self.put(ident, record, old)
                raise Held(creation_error or "Drive did not confirm the reserved archive file. Its creation is held for review.") from None
        _private(meta, parent=parent, folder=mime == google.FOLDER)
        if (meta.get("mimeType") != mime or (meta.get("properties") or {}).get(TAG) != ident
                or (target and (meta.get("shortcutDetails") or {}).get("targetId") != target)):
            raise Held("The archive file's source, type or identity did not read back correctly.")
        if mime == PDF and (meta.get("md5Checksum") != record.get("pdf_md5")
                           or int(meta.get("size", -1)) != record.get("size")):
            raise Held("This saved feedback PDF changed after filing. The original report needs checking.")
        if record.get("media_sha256") and (meta.get("md5Checksum") != record.get("media_md5")
                                          or str(meta.get("size")) != str(record.get("size"))):
            raise Held("This exported work snapshot changed after filing. Its original work is preserved.")
        if mime != PDF and meta.get("name") != name:
            self.renew()
            try:
                google.rename_item(self.write_token, file_id, name)
            except google.GoogleError:
                pass
            meta = google.drive_file(self.token, file_id, FIELDS)
            if meta.get("name") != name:
                raise Held("Drive did not confirm this archive label update.")
        record.update(status="verified", name=meta.get("name", name), created_at=meta.get("createdTime", ""),
                      drive_version=meta.get("version"), url=_url(file_id, mime == google.FOLDER))
        if mime == SHORTCUT:
            record.update(attributes or {})
        return self.put(ident, record, old).value

    def _student_record(self, ident):
        seen = set()
        for _ in range(6):
            if ident in seen:
                break
            seen.add(ident)
            doc = _read(self.port, ident)
            if not doc or not doc.value.get("alias_of"):
                return ident, doc
            ident = doc.value["alias_of"]
        raise Held("The student archive's identity links need review. Existing folders are preserved.")

    def navigation_folder(self):
        ident = key("archivesourcelinks", [self.root, self.cls])
        return self.create(ident, "Original work links", self.course["id"], google.FOLDER)

    def feedback_history_folder(self):
        return self.create(key("archivefeedbackhistory", [self.root, self.cls]),
                           "Previous feedback versions", self.course["id"], google.FOLDER)

    def verify_historical_work(self, source, binding, row):
        """A teacher-retained exact source is history, never current roster authority."""
        if not isinstance(binding, dict):
            raise Held("This former-student source has no verified historical filing binding.")
        owner_doc = _read(self.port, binding.get("student_key", ""))
        copy_doc = _read(self.port, binding.get("copy_record_key", ""))
        index = _read(self.port, self.index_key)
        owner, copied = (owner_doc.value if owner_doc else None), (copy_doc.value if copy_doc else None)
        teacher = binding.get("teacher_status")
        if (binding.get("basis") != "teacher_confirmed_former_student_with_verified_work_copy"
                or not isinstance(teacher, dict) or teacher.get("basis") != "teacher_confirmed_former_student"
                or teacher.get("status") != "former_student" or teacher.get("class_key") != self.cls
                or teacher.get("current_ic_verified") is not False or not teacher.get("confirmed_on")
                or not owner or owner.get("status") != "verified" or owner.get("schema") != SCHEMA
                or owner.get("record_key") != binding.get("student_key") or owner.get("student_key") != binding.get("student_key")
                or owner.get("class_key") != self.cls or owner.get("historical_status") != "former_student"
                or owner.get("former_student_proof") != teacher or owner.get("current_roster_member") is not False
                or owner.get("grade_authority") is not False
                or owner.get("identity_status") != "identity_unverified"
                or work.roster.compact(teacher.get("name")) != work.roster.compact(owner.get("last", "") + owner.get("first", ""))
                or not index or index.value.get("students", {}).get(owner["student_key"]) != owner.get("drive_file_id")
                or binding.get("student_folder_id") != owner.get("drive_file_id")
                or not copied or copied.get("schema") != SCHEMA or copied.get("status") != "verified"
                or copied.get("record_key") != binding.get("copy_record_key") or copied.get("kind") != "work"
                or copied.get("class_key") != self.cls or copied.get("student_key") != owner["student_key"]
                or copied.get("source_file_id") != source.get("id") or copied.get("drive_file_id") != binding.get("copy_id")
                or copied.get("record_key") not in owner.get("items", {})
                or copied.get("historical_status") != "former_student"
                or copied.get("source_kind") not in {"submitted_work_copy", "submitted_work_export"}
                or copied.get("source_revision") != binding.get("source_revision")
                or _source_revision(source) != binding.get("source_revision")
                or copied.get("parent") != owner.get("drive_file_id")):
            raise Held("This former-student source or private copy changed; preserve its history for verification.")
        if row.get("matched") and row.get("first") and row.get("last") and (
                work.roster.compact(row["first"] + row["last"]) != work.roster.compact(owner["first"] + owner["last"])):
            raise Held("This historical file is now attributed to another student; check that source without changing history.")
        container = _private(google.drive_file(self.token, owner["parent"], FIELDS), parent=self.course["id"], folder=True)
        if (container.get("properties") or {}).get(TAG) != key("archiveformersection", [self.root, self.cls]):
            raise Held("The former-student container no longer matches its tagged private class location.")
        folder = _private(google.drive_file(self.token, owner["drive_file_id"], FIELDS), parent=owner["parent"], folder=True)
        if (folder.get("properties") or {}).get(TAG) != owner["student_key"]:
            raise Held("The former student's folder no longer matches its private archive key.")
        meta = google.drive_file(self.token, copied["drive_file_id"], FIELDS)
        if copied["source_kind"] == "submitted_work_copy":
            if not isinstance(copied.get("content_proof"), dict) or not re.fullmatch(r"[a-f0-9]{64}", str(copied["content_proof"].get("sha256"))):
                raise Held("The historical work copy has no immutable original-content proof.")
            _verify_work_copy(meta, copied)
        else:
            _verify_work_export(meta, copied)
        return copied

    def retire_navigation(self, student_key, shortcut_key, copy_key):
        """Move only a verified source shortcut; preserve its ID and original source."""
        student_doc, shortcut_doc, copy_doc = (_read(self.port, i) for i in (student_key, shortcut_key, copy_key))
        if not student_doc or not shortcut_doc or not copy_doc:
            raise Held("This original-work link needs its saved student and verified work copy before cleanup.")
        student, shortcut, copied = student_doc.value, shortcut_doc.value, copy_doc.value
        if (student.get("student_key") != student_key or student.get("status") != "verified"
                or shortcut.get("mime") != SHORTCUT or shortcut.get("status") != "verified"
                or shortcut.get("kind") != "work" or shortcut.get("student_key") != student_key
                or copied.get("status") != "verified" or copied.get("student_key") != student_key
                or copied.get("source_kind") not in {"submitted_work_copy", "submitted_work_export", "teacher_attributed_joint_copy"}
                or copied.get("source_file_id") != shortcut.get("source_file_id")):
            raise Held("The original-work link and real copy do not bind the same student, source and revision.")
        _private(google.drive_file(self.token, student["parent"], FIELDS), parent=self.course["id"], folder=True)
        _private(google.drive_file(self.token, student["drive_file_id"], FIELDS), parent=student["parent"], folder=True)
        copy_meta = _private(google.drive_file(self.token, copied["drive_file_id"], FIELDS), parent=student["drive_file_id"])
        if copied["source_kind"] == "submitted_work_copy":
            _verify_work_copy(copy_meta, copied)
        elif copied["source_kind"] == "submitted_work_export":
            _verify_work_export(copy_meta, copied)
        elif ((copy_meta.get("properties") or {}).get(TAG) != copy_key
              or (copy_meta.get("properties") or {}).get("hubCopiedSource") != copied["source_file_id"]
              or (copy_meta.get("properties") or {}).get("hubSharedSource") != copied.get("shared_source_key")
              or not re.fullmatch(r"sharedfeedbacksource_[a-f0-9]{64}", copied.get("shared_source_key") or "")
              or copy_meta.get("mimeType") != copied.get("mime")
              or copy_meta.get("version") != copied.get("drive_version")
              or copied.get("teacher_joint_attribution") is not True
              or not re.fullmatch(r"[a-f0-9]{64}", copied.get("copied_slide_content_sha256") or "")):
            raise Held("The attributed joint copy needs its verified private content before cleanup.")
        source = google.drive_file(self.token, shortcut["source_file_id"], SOURCE_FIELDS)
        if (source.get("trashed") is not False or source.get("mimeType") not in work.STUDENT_WORK
                or str(source.get("version")) != str(copied.get("source_version"))
                or (copied["source_kind"] in {"submitted_work_copy", "submitted_work_export"}
                    and _source_revision(source) != copied.get("source_revision"))):
            raise Held("The original submission has a newer revision. File its real copy before removing this link.")
        self.heartbeat(force=True)
        source_proof = _work_content(self.token, source, self.heartbeat)
        copy_proof = _work_content(self.token, copy_meta, self.heartbeat)
        export_matches = (copied["source_kind"] == "submitted_work_export"
                          and source_proof == copied.get("source_content_proof")
                          and copy_proof.get("sha256") == copied.get("media_sha256"))
        if (not export_matches and (source_proof != copy_proof or (copied["source_kind"] == "submitted_work_copy"
                                          and source_proof != copied.get("content_proof")))):
            raise Held("The real copied work does not match the original content. Preserve the navigation link.")
        if (_source_revision(google.drive_file(self.token, source["id"], SOURCE_FIELDS)) != _source_revision(source)
                or google.drive_file(self.token, copied["drive_file_id"], FIELDS) != copy_meta):
            raise Held("The original or copied work changed during cleanup verification. Preserve both.")
        navigation = self.navigation_folder()
        meta = google.drive_file(self.token, shortcut["drive_file_id"], FIELDS)
        _private(meta)
        if (meta.get("mimeType") != SHORTCUT or (meta.get("properties") or {}).get(TAG) != shortcut_key
                or (meta.get("shortcutDetails") or {}).get("targetId") != source["id"]
                or meta.get("parents") not in ([student["drive_file_id"]], [navigation["drive_file_id"]])):
            raise Held("This source link changed or left its verified private folders. No cleanup was applied.")
        if meta["parents"] != [navigation["drive_file_id"]]:
            self.renew()
            try:
                google.move_item(self.write_token, meta["id"], navigation["drive_file_id"], meta["parents"])
            except google.GoogleError:
                pass
            _private(google.drive_file(self.token, meta["id"], FIELDS), parent=navigation["drive_file_id"])
        index = _read(self.port, self.index_key)
        value = dict(index.value if index else {"schema": SCHEMA, "students": {}})
        links = dict(value.get("source_links", {})); links[shortcut_key] = shortcut["drive_file_id"]
        items = dict(student.get("items", {})); items.pop(shortcut_key, None)
        retired = {**shortcut, "retired_navigation": True, "archiveparent": navigation["drive_file_id"],
                   "parent": navigation["drive_file_id"], "work_copy_record_key": copy_key,
                   "retired_content_proof": source_proof, "retired_source_revision": _source_revision(source)}
        changes = {shortcut_key: (retired, shortcut_doc.version),
                   student_key: ({**student, "items": items}, student_doc.version),
                   self.index_key: ({**value, "source_links": links}, index.version if index else None)}
        self.heartbeat(force=True)
        try:
            self.port.commit(changes)
        except (Conflict, StoreUnavailable):
            actual = self.port.read(list(changes)).documents
            if any(actual[i] is None or actual[i].value != v for i, (v, _) in changes.items()):
                raise
        actual = self.port.read(list(changes)).documents
        if any(actual[i] is None or actual[i].value != v for i, (v, _) in changes.items()):
            raise Held("The source-link cleanup index did not confirm. Original and copied work are preserved.")
        return retired

    def copy_work(self, student, source, attributes):
        """One copy POST per source revision; lost responses recover by its tag."""
        revision = _source_revision(source)
        if (source.get("trashed") is not False or source.get("mimeType") not in work.STUDENT_WORK
                or not source.get("version") or not source.get("modifiedTime")):
            raise Held("This original submission needs a complete revision before it can be copied.")
        revision_key = content_hash(revision)
        ident = key("archiveworkcopy", [self.root, self.cls, source["id"], revision_key, student["student_key"]])
        old = _read(self.port, ident)
        expected = {"parent": student["drive_file_id"], "student_key": student["student_key"],
                    "source_file_id": source["id"], "source_revision_key": revision_key, "mime": source["mimeType"]}
        if old and any(old.value.get(k) != value for k, value in expected.items()):
            raise Held("This work-copy reservation belongs to another source or student. Keep it for review.")
        if old and old.value.get("status") == "verified":
            record = old.value
            _verify_work_copy(google.drive_file(self.token, record["drive_file_id"], FIELDS), record)
            if record.get("identity_status") != "verified" and attributes.get("identity_status") == "verified":
                record = self.put(ident, {**record, "identity_status": "verified",
                    "identity_basis": attributes.get("identity_basis")}, old).value
            return record
        # Native copy metadata can finalize its version without a content edit.
        # The immutable content proof and copied modifiedTime bind the snapshot.
        self.heartbeat(force=True)
        proof = _work_content(self.token, source, self.heartbeat)
        self.heartbeat(force=True)
        if _source_revision(google.drive_file(self.token, source["id"], SOURCE_FIELDS)) != revision:
            raise Held("This original submission changed during verification. Its next stable revision can be filed later.")
        if old:
            record = dict(old.value)
            if record.get("content_proof") != proof:
                raise Held("This reserved source revision now has different content. Preserve it for review.")
        else:
            provenance = {k: v for k, v in attributes.items() if k not in
                          {"drive_file_id", "drive_version", "created_at", "url", "record_key", "status", "target"}}
            record = {**provenance, **expected, "schema": SCHEMA, "record_key": ident,
                      "status": "creating", "source_kind": "submitted_work_copy", "source_revision": revision,
                      "source_version": source.get("version"), "source_modified_at": source.get("modifiedTime"),
                      "content_proof": proof, "name": attributes["name"].replace(" — Work", " — Work copy"),
                      "copy_requested_at": datetime.now(timezone.utc).isoformat()}
            self.renew()
            old = self.put(ident, record)
            try:
                copied = google.get_json("https://www.googleapis.com/drive/v3/files/%s/copy" % quote(source["id"]),
                    self.write_token, {"supportsAllDrives": "true", "fields": "id"}, method="POST", body={
                        "name": record["name"], "parents": [record["parent"]],
                        "properties": {TAG: ident, "hubCopiedSource": source["id"], "hubSourceRevision": revision_key}})
                if copied.get("id"):
                    record["drive_file_id"] = copied["id"]
                    old = self.put(ident, record, old)
            except google.GoogleError as error:
                record["copy_error_status"] = error.status
                old = self.put(ident, record, old)  # No response body or source data; never repeat this POST.
        file_id = record.get("drive_file_id")
        if not file_id:
            candidates = [item for item in google.folder_children(self.token, record["parent"])
                          if (item.get("properties") or {}).get(TAG) == ident]
            if len(candidates) != 1:
                raise Held("A work-copy request has no single confirmed result. Its original is safe; the request will not be repeated.")
            file_id = candidates[0]["id"]
        meta = google.drive_file(self.token, file_id, FIELDS)
        _verify_work_copy(meta, record)
        self.heartbeat(force=True)
        if _work_content(self.token, meta, self.heartbeat) != proof:
            raise Held("The filed copy does not contain the exact original submission. Keep it for review.")
        final_source = google.drive_file(self.token, source["id"], SOURCE_FIELDS)
        final_copy = google.drive_file(self.token, file_id, FIELDS)
        _verify_work_copy(final_copy, record)
        if (_source_revision(final_source) != revision
                or final_copy.get("modifiedTime") != meta.get("modifiedTime")):
            raise Held("The source or copied submission changed during readback. No copy is confirmed yet.")
        record.update(status="verified", drive_file_id=file_id, drive_version=final_copy.get("version"),
                      copied_modified_at=final_copy.get("modifiedTime"), created_at=final_copy.get("createdTime"),
                      copied_at=datetime.now(timezone.utc).isoformat(), url=_url(file_id))
        return self.put(ident, record, old).value

    def export_work(self, student, source, artifact, blocked_copy_key):
        """Explicit independent snapshot; never change or repeat an uncertain native COPY."""
        revision = _source_revision(source)
        if (source.get("mimeType") != "application/vnd.google-apps.presentation" or source.get("trashed") is not False
                or not source.get("version") or not source.get("modifiedTime")):
            raise Held("Only a readable native presentation can use this explicit exported-work snapshot.")
        blocked = _read(self.port, blocked_copy_key)
        if (not blocked or blocked.value.get("schema") != SCHEMA or blocked.value.get("record_key") != blocked_copy_key
                or not re.fullmatch(r"archiveworkcopy_[a-f0-9]{64}", blocked_copy_key)
                or blocked.value.get("status") != "creating" or blocked.value.get("drive_file_id")
                or blocked.value.get("source_kind") != "submitted_work_copy"
                or blocked.value.get("source_file_id") != source["id"]
                or blocked.value.get("student_key") != student["student_key"]
                or blocked.value.get("parent") != student["drive_file_id"]
                or blocked.value.get("source_revision") != revision
                or blocked.value.get("source_revision_key") != content_hash(revision)
                or blocked_copy_key != key("archiveworkcopy", [self.root, self.cls, source["id"],
                    content_hash(revision), student["student_key"]])):
            raise Held("This export requires the exact unresolved native-copy reservation; it never replaces a confirmed copy.")
        if any((item.get("properties") or {}).get(TAG) == blocked_copy_key
               for item in google.folder_children(self.token, student["drive_file_id"])):
            raise Held("The native-copy request now has a tagged result. Verify that existing copy before creating an export.")
        ident = key("archiveworkexport", [self.root, self.cls, source["id"], content_hash(revision), student["student_key"]])
        proof = _work_content(self.token, source, self.heartbeat)
        blocked_proof = blocked.value.get("content_proof") or {}
        if (blocked_proof.get("basis") not in {"native_document_content/v1", "native_presentation/v1"}
                or (blocked_proof.get("basis") == "native_presentation/v1" and proof != blocked_proof)):
            raise Held("This source content differs from the unresolved copy request. Preserve that request for review.")
        old = _read(self.port, ident)
        if old:
            record = old.value
            try:
                meta = _verify_work_export(google.drive_file(self.token, record["drive_file_id"], FIELDS), record)
            except google.GoogleError as error:
                if error.status == 404:
                    raise Held("The exported-work upload is unconfirmed. Its reserved ID will not be replaced or uploaded again.") from None
                raise
            raw = _work_bytes(self.token, meta, self.heartbeat)
            if (record.get("source_revision") != revision or record.get("source_content_proof") != proof
                    or hashlib.sha256(raw).hexdigest() != record["media_sha256"]
                    or record.get("unresolved_native_copy_key") != blocked_copy_key):
                raise Held("The existing exported snapshot changed. Preserve its stable file ID for review.")
            if blocked_proof.get("basis") == "native_document_content/v1" and _native_parts_proof(raw, "ppt/") != blocked_proof:
                raise Held("The exported content does not confirm the unresolved request's original source proof.")
            if record.get("status") != "verified":
                record = self.create(ident, record["name"], record["parent"], google.PPTX, media=raw)
        else:
            raw = _work_bytes(self.token, source, self.heartbeat)
            exported_proof = _native_parts_proof(raw, "ppt/")
            if blocked_proof.get("basis") == "native_document_content/v1" and exported_proof != blocked_proof:
                raise Held("The current exported content does not confirm the unresolved request's original source proof.")
            provenance = {k: v for k, v in artifact.items() if k not in {"drive_file_id", "drive_version", "created_at",
                "url", "record_key", "status", "target", "mime", "parent", "name", "retired_navigation", "archiveparent",
                "work_copy_record_key", "retired_content_proof", "retired_source_revision"}}
            self.heartbeat(force=True)
            if (_source_revision(google.drive_file(self.token, source["id"], SOURCE_FIELDS)) != revision
                    or _work_content(self.token, source, self.heartbeat) != proof):
                raise Held("This original changed during export. No work snapshot upload was requested.")
            record = self.create(ident, artifact["name"].replace(" — Work", " — Exported work snapshot") + ".pptx",
                student["drive_file_id"], google.PPTX, media=raw, attributes={**provenance,
                    "source_kind": "submitted_work_export", "source_mime": source["mimeType"], "source_revision": revision,
                    "source_revision_key": content_hash(revision), "source_content_proof": proof,
                    "source_version": source["version"], "source_modified_at": source["modifiedTime"],
                    "unresolved_native_copy_key": blocked_copy_key})
            meta = _verify_work_export(google.drive_file(self.token, record["drive_file_id"], FIELDS), record)
            if _work_content(self.token, meta, self.heartbeat).get("sha256") != record["media_sha256"]:
                raise Held("The exported snapshot did not read back as the exact uploaded bytes.")
        if (_source_revision(google.drive_file(self.token, source["id"], SOURCE_FIELDS)) != revision
                or _work_content(self.token, source, self.heartbeat) != proof
                or _read(self.port, blocked_copy_key) != blocked):
            raise Held("The source or unresolved copy record changed during snapshot verification. Preserve both.")
        return record

    def student(self, person):
        self.heartbeat()
        section_key = key("archivesection", [self.root, self.cls, person["section"]])
        section = self.create(section_key, person["section"], self.course["id"], google.FOLDER)
        primary_key = key("archivestudent", [self.root, self.cls, person["identity"]])
        name_key = key("archivestudent", [self.root, self.cls, _name_key(person)])
        identities = [primary_key, name_key]
        if person["school_email"]:
            identities.append(key("archivestudent", [self.root, self.cls, "email:" + person["school_email"]]))
        records = {}
        for ident in dict.fromkeys(identities):
            canonical, doc = self._student_record(ident)
            if doc:
                records[canonical] = doc
        # Existing email archives predate name aliases. Bind only one exact
        # course/section/display-name folder; never merge two histories.
        if self.student_records is None:
            index = _read(self.port, self.index_key)
            ids = list((index.value if index else {}).get("students", {}))
            self.student_records = _batch_read(self.port, ids)
        for ident, doc in self.student_records.items():
            if doc and _same_name(doc.value, person):
                records[ident] = doc
        folders = {doc.value.get("drive_file_id") for doc in records.values()}
        if len(folders) > 1:
            raise Held("Two archive folders claim this course, section and student name. Preserve both for identity review.")
        student_key, prior = next(iter(records.items())) if records else (primary_key, None)
        if prior and person.get("identity_status") == "identity_unverified" and not _same_name(prior.value, person):
            raise Held("The filename points to an archive with a different current roster name or section. Keep it for identity review.")
        if prior and prior.value.get("student_id") and person["student_id"] \
                and prior.value["student_id"] != person["student_id"]:
            raise Held("This archive has a different verified IC identity. It needs review.")
        if prior:
            person = dict(person)
            if person.get("identity_status") == "identity_unverified" and prior.value.get("basis") != "unique_name_for_filing":
                person.update({k: prior.value.get(k, person.get(k)) for k in
                               ("identity", "basis", "student_id", "school_email")})
                person["identity_status"] = prior.value.get("identity_status", "verified")
            if prior.value.get("student_id") and not person["student_id"]:
                person.update(student_id=prior.value["student_id"], identity="ic:" + prior.value["student_id"],
                              basis="verified_ic_student_id")
            if not person["school_email"] and prior.value.get("school_email"):
                person["school_email"] = prior.value["school_email"]
        if prior and prior.value.get("parent") != section["drive_file_id"]:
            old_parent = prior.value["parent"]
            meta = _private(google.drive_file(self.token, prior.value["drive_file_id"], FIELDS),
                            parent=old_parent, folder=True)
            old_section = _private(google.drive_file(self.token, old_parent, FIELDS), parent=self.course["id"], folder=True)
            self.renew()
            try:
                google.move_item(self.write_token, meta["id"], section["drive_file_id"], [old_section["id"]])
            except google.GoogleError:
                pass
            _private(google.drive_file(self.token, meta["id"], FIELDS), parent=section["drive_file_id"], folder=True)
            prior = self.put(student_key, {**prior.value, "parent": section["drive_file_id"]}, prior)
        student = self.create(student_key, person["name"], section["drive_file_id"], google.FOLDER,
                              attributes={**person, "items": {}})
        doc = _read(self.port, student_key)
        student = self.put(student_key, {**student, **person, "student_key": student_key}, doc).value
        self.student_records[student_key] = _read(self.port, student_key)
        for ident in dict.fromkeys(identities):
            if ident != student_key:
                old = _read(self.port, ident)
                self.put(ident, {"schema": SCHEMA, "alias_of": student_key}, old)
        index = _read(self.port, self.index_key)
        values = dict((index.value if index else {}).get("students", {}))
        values[student_key] = student["drive_file_id"]
        if len(values) > MAX_STUDENTS:
            raise Held("The student archive exceeds its verified class size limit.")
        self.put(self.index_key, {**(index.value if index else {}), "schema": SCHEMA, "students": values}, index)
        return student

    def filing_receipt(self, found, result):
        week = _scan_scope(self.cls, found)
        receipt = _filing_receipt(self.cls, week, result)
        ident = key("archivescan", [self.root, self.cls, week])
        self.heartbeat(force=True)
        self.put(ident, receipt, _read(self.port, ident))
        index = _read(self.port, self.index_key)
        scans = dict((index.value if index else {}).get("filing_scans", {}))
        scans[week] = ident
        if len(scans) > MAX_SCANS:
            raise Held("The archive's recorded week coverage needs its size checked.")
        self.put(self.index_key, {**(index.value if index else {}), "schema": SCHEMA,
                                  "filing_scans": scans}, index)
        return receipt

    def attach(self, student, artifact):
        self.heartbeat()
        ident = student["student_key"]
        doc = _read(self.port, ident)
        items = dict(doc.value.get("items", {}))
        items[artifact["record_key"]] = {"kind": artifact["kind"], "date": artifact["date"]}
        if len(items) > MAX_ITEMS:
            raise Held("This student's archive exceeds its verified record limit.")
        self.put(ident, {**doc.value, "items": items}, doc)
        if artifact["kind"] == "feedback":
            self.feedback_students.add(ident)


def _result(cls, **more):
    return {"schema": SCHEMA, "class_key": cls, "status": "verified", "configured": True,
            "filed": 0, "held": [], "problems": [], **more}


def _scan_scope(cls, found):
    week = found.get("week")
    if found.get("class_key") != cls:
        raise Held("The student-work scan belongs to a different class.")
    if (not isinstance(week, str) or not 1 <= len(week) <= 100
            or week != _clean(week, 100)):
        raise Held("This filing scan needs its exact Agenda week before a receipt can be recorded.")
    return week


def _forms_not_required(config, cls, found):
    policy = found.get("week_policy") or {}
    if policy.get("excluded") is not True or policy.get("problem"):
        return False
    try:
        settings = work._scoped_config(config, "HUB_WEEK_EXCLUSIONS",
            {"school_year", "class_key", "weeks", "reason"})
        matches = [p for p in settings if p["class_key"] == cls
            and p["school_year"] == policy.get("school_year")
            and isinstance(p["weeks"], list) and found.get("week") in p["weeks"]
            and isinstance(p["reason"], str) and p["reason"].strip() == policy.get("reason")]
        return len(matches) == 1
    except (ValueError, TypeError, KeyError):
        return False


def _held_source_files(value):
    """Exact source locators for review, never a student identity or supplied URL."""
    sources, seen = [], set()
    for source in (value if isinstance(value, list) else [])[:8]:
        if not isinstance(source, dict):
            continue
        ident = source.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", ident) or ident in seen:
            continue
        seen.add(ident)
        sources.append({"id": ident, "name": _clean(source.get("name"), 400)})
    return sources


def _hold_context(row, assignment=""):
    first, last = _clean(row.get("first"), 100), _clean(row.get("last"), 100)
    name = last + ", " + first if first and last else last or first or _clean(row.get("who"), 100)
    source_names = list(dict.fromkeys(_clean(file.get("name"), 160)
        for version in [row] + row.get("earlier", []) for file in version.get("files", [])
        if isinstance(file, dict) and file.get("name")))
    sources = _held_source_files([file for version in [row] + row.get("earlier", [])
        for file in version.get("files", []) if isinstance(file, dict)])
    return {k: value for k, value in {"name": name, "section": _clean(row.get("section"), 100),
        "assignment": _clean(assignment, 160), "source_file_names": _clean("; ".join(source_names[:8]), 400),
        "source_files": sources}.items() if value}


def _filing_receipt(cls, week, result):
    if result.get("class_key", cls) != cls:
        raise Held("A filing result cannot be recorded for a different class.")
    count = result.get("filed", 0)
    if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= 10000:
        raise Held("This filing result needs a verified filed-record count.")
    raw_held, raw_problems = result.get("held") or [], result.get("problems") or []
    if not isinstance(raw_held, list) or not isinstance(raw_problems, list):
        raise Held("This filing result's review details could not be recorded.")
    held = []
    for item in raw_held[:MAX_HOLDS]:
        if not isinstance(item, dict) or not item.get("problem"):
            raise Held("This filing result's review details need a readable reason.")
        detail = {k: _clean(item[k], 400 if k in ("problem", "source_file_names") else 160)
                  for k in ("name", "section", "assignment", "source_file_names", "problem") if item.get(k)}
        sources = _held_source_files(item.get("source_files"))
        if sources:
            detail["source_files"] = sources
        held.append(detail)
    problems = [_clean(p, 400) for p in raw_problems[:MAX_HOLDS] if p]
    held_count = len(raw_held) + len([p for p in raw_problems if p])
    if result.get("status") != "verified" and not held_count:
        problems.append("This filing scan did not finish with a confirmed receipt. Its source work is preserved.")
        held_count = 1
    truncated = (len(raw_held) > MAX_HOLDS or len(raw_problems) > MAX_HOLDS
                 or any(isinstance(item.get("source_files"), list) and len(item["source_files"]) > 8
                        for item in raw_held if isinstance(item, dict)))
    if truncated:
        problems.append("Further filing checks are retained in the private run receipt; this list reached its safe display limit.")
    return {"schema": SCHEMA, "class_key": cls, "class_name": CLASSES[cls], "week": week,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": "held" if held_count or result.get("status") != "verified" else "verified",
            "filed": count, "held_count": held_count, "held": held, "problems": problems,
            "details_truncated": truncated}


def record_filing_receipt(token, write_token, config, class_key, found, result):
    """Record a source-verified completed scan; no source reads, moves or AI calls.

    This supports an earlier live backfill whose verified private run result
    predates the receipt index. Only its exact class/week entry is replaced.
    """
    if not enabled(config):
        return _result(class_key, configured=False, status="unconfigured")
    _scan_scope(class_key, found)
    run = _Run(token, write_token, config, class_key)
    try:
        receipt = run.filing_receipt(found, result)
        run.finish({"status": receipt["status"], "week": receipt["week"], "filed": receipt["filed"]})
        return receipt
    except (Held, google.GoogleError, Conflict, StoreUnavailable) as error:
        try:
            run.finish({"status": "held", "problems": [_problem(error)]})
        except (Conflict, StoreUnavailable):
            pass
        raise


def archive_work(token, write_token, config, class_key, found):
    if not enabled(config):
        return _result(class_key, configured=False, status="unconfigured")
    result = _result(class_key)
    run = None
    try:
        _scan_scope(class_key, found)
        run = _Run(token, write_token, config, class_key)
        roster_rows, source_metadata = None, {}

        def roster_reader():
            nonlocal roster_rows
            if roster_rows is None:
                roster_rows = _filing_roster(token, config, class_key)
            return roster_rows

        def source_reader(ident):
            run.heartbeat()
            if ident not in source_metadata:
                source_metadata[ident] = google.drive_file(token, ident, SOURCE_FIELDS)
            return source_metadata[ident]

        candidates, emails, conflicts = [], {}, set()
        index = _read(run.port, run.index_key)
        historical = (index.value if index else {}).get("historical_sources", {})
        if not isinstance(historical, dict):
            raise Held("The class's retained former-student source index needs verification.")
        preserved_history = set()
        lessons = list(found.get("lessons", []))
        forms_not_required = _forms_not_required(config, class_key, found)
        calendar_week = bool(lessons) and all(l.get("calendar_only") is True and not l.get("forms") for l in lessons)
        loose = list(found.get("other_files", []))
        for lesson in lessons:
            for form in lesson.get("forms", []):
                for item in form.get("elsewhere", []):
                    ident = _source_id(item.get("url"))
                    if "match not confirmed" in str(item.get("to", "")).casefold() and ident:
                        loose.append({"matched": False, "source": item.get("source"), "email": "", "matched_by": None,
                            "first": "", "last": "", "section": item.get("section", ""), "earlier": [],
                            "submitted_at": item.get("submitted_at"), "files": [{"id": ident, "name": item.get("file", ""),
                                                                                  "via": item.get("to", "")} ]})
        if loose:
            lessons.append({"topic": "Lesson not confirmed", "forms": [{"label": "Lesson not confirmed",
                "form_id": None, "lesson_unverified": True, "students": loose}]})
        for lesson in lessons:
            if (lesson.get("problem") and not ((lesson.get("calendar_only") is True or forms_not_required) and not lesson.get("forms")
                    and lesson["problem"] == "This lesson has no Form link in its 'Turn it in' column.")):
                result["held"].append({"assignment": _clean(lesson.get("topic")), "problem": lesson["problem"]})
            for form in lesson.get("forms", []):
                assignment = form.get("label") or lesson.get("topic") or ""
                if form.get("problem"):
                    result["held"].append({"assignment": form.get("label", ""), "problem": form["problem"]})
                    continue
                for row in form.get("students", []) + form.get("late", []):
                    run.heartbeat()
                    if not row.get("files") and not any(v.get("files") for v in row.get("earlier", [])):
                        continue
                    try:
                        # Keep captured source rows unchanged. Remove only exact
                        # former-student sources with current original-revision,
                        # private-copy and teacher-status proof before asking
                        # the active roster to own them.
                        versions = []
                        for version in [row] + row.get("earlier", []):
                            kept = []
                            for file in version.get("files", []):
                                binding = historical.get(file.get("id"))
                                if binding is None:
                                    kept.append(file)
                                    continue
                                if file.get("trashed") or file.get("problem"):
                                    raise Held("This retained historical source is unavailable; preserve it for verification.")
                                copied = run.verify_historical_work(source_reader(file["id"]), binding, {**row, **version})
                                preserved_history.add(copied["record_key"])
                            versions.append({**version, "files": kept})
                        if not any(v["files"] for v in versions):
                            continue
                        row = {**versions[0], "earlier": versions[1:]}
                        person = _filing_identity(config, class_key, row, roster_reader, source_reader)
                        previous = emails.setdefault(person["school_email"] or person["identity"], person)
                        if any(previous[k] != person[k] for k in ("first", "last", "section")):
                            conflicts.add(person["school_email"] or person["identity"])
                            raise Held("The school email matches more than one roster identity. Keep its files for review.")
                        candidates.append((lesson, form, row, person))
                    except (Held, google.GoogleError) as error:
                        result["held"].append({**_hold_context(row, assignment), "problem": str(error)})
                for row in form.get("elsewhere", []):
                    if "match not confirmed" in str(row.get("to", "")).casefold() and not _source_id(row.get("url")):
                        result["held"].append({**_hold_context(row, assignment),
                            "problem": "This upload came through another lesson's Form, and its lesson match is not confirmed. The original remains there."})
        # Duplicate roster identities must hold all their submissions, including
        # the first candidate encountered, rather than creating a first-wins folder.
        filed_ids = set()
        for lesson, form, row, person in candidates:
            run.heartbeat()
            context = _hold_context(row, form.get("label") or lesson.get("topic") or "")
            if (person["school_email"] or person["identity"]) in conflicts:
                continue
            try:
                student = run.student(person)
                for version in [row] + row.get("earlier", []):
                    run.heartbeat()
                    try:
                        evidence = row if version is row else {**row, **version, "email": version.get("email", ""),
                            "school_email": version.get("school_email") or version.get("email", ""),
                            "matched_by": version.get("matched_by", ""),
                            "ic_identity_verified": False, "ic_student_id": "", "student_id": ""}
                        version_person = person if version is row else _filing_identity(
                            config, class_key, evidence, roster_reader, source_reader)
                        if not _same_name(version_person, person):
                            raise Held("An earlier upload belongs to a different student or section. Ask for separate named resubmissions.")
                        if (version_person.get("student_id") and person.get("student_id")
                                and version_person["student_id"] != person["student_id"]):
                            raise Held("An earlier upload has a different verified IC student identity. Keep it for review.")
                        if (version is not row and version_person.get("school_email") and person.get("school_email")
                                and version_person["school_email"] != person["school_email"]):
                            actual = work.roster.match_email(version_person["school_email"], roster_reader(),
                                {d.strip().lower().lstrip("@") for d in config.get("HUB_SCHOOL_EMAIL_DOMAINS", "").split(",") if d.strip()},
                                config.get("HUB_EMAIL_USERNAME_PREFIX", "s."))
                            if not actual or not _same_name(actual, person):
                                raise Held("An earlier Form email names a different student. Ask for separate named resubmissions.")
                        # A verified earlier Form receipt can upgrade a name-only
                        # folder through its stable alias, without duplicating it.
                        if version is not row:
                            student = run.student(version_person)
                    except (Held, google.GoogleError) as error:
                        result["held"].append({**context, "problem": str(error)})
                        continue
                    for file in version.get("files", []):
                        run.heartbeat()
                        if file.get("trashed") or file.get("problem"):
                            result["held"].append({**context, "problem": "This source file's lesson or availability needs checking."})
                            continue
                        source = google.drive_file(token, file["id"], SOURCE_FIELDS)
                        if source.get("trashed") is not False or source.get("mimeType") not in work.STUDENT_WORK:
                            raise Held("A source upload is unavailable or is itself a shortcut. Leave it for review.")
                        if version_person["identity_status"] == "identity_unverified":
                            actual_owner, _ = _filename_owner(config, source, roster_reader())
                            if not _same_name(actual_owner, version_person):
                                raise Held("This file's student name changed while filing. Keep it for a corrected named resubmission.")
                        original_event = {**version, "submitted_at": file["source_submitted_at"]} if file.get("source_submitted_at") else version
                        date, date_basis, submitted_at = _work_date(original_event, source)
                        source_form_id = file.get("source_form_id") or (form.get("form_id") if version.get("source") == "form" else None)
                        assignment_form_id = file.get("assignment_form_id") or form.get("form_id")
                        lesson_status = "unverified" if file.get("other_week") or form.get("lesson_unverified") else "verified"
                        artifact_key = key("archivework", [run.root, class_key, file["id"]])
                        old = _read(run.port, artifact_key)
                        if old and old.value.get("student_key") != student["student_key"]:
                            raise Held("This source file is already indexed for a different student. Keep it for review.")
                        title = source.get("name", "Original work") if lesson_status == "unverified" else work.assignment_title(form.get("label", ""))
                        markers = ([] if version_person["identity_status"] == "verified" else ["filed by name"]) + (
                            ["lesson not confirmed"] if lesson_status == "unverified" else []) + (
                            ["file created"] if date_basis == "file_created" else [])
                        label = date + " — " + student["name"] + " — " + _clean(title) + " — Work" + (
                            " (" + "; ".join(markers) + ")" if markers else "")
                        central_navigation = ((bool(config.get(RECORDS_CONFIG)) and not old)
                                              or bool(old and old.value.get("retired_navigation")))
                        navigation_parent = run.navigation_folder()["drive_file_id"] if central_navigation else student["drive_file_id"]
                        artifact = run.create(artifact_key, label,
                            navigation_parent, SHORTCUT, target=file["id"], attributes={
                                "kind": "work", "date": date, "student_key": student["student_key"],
                                "class_key": class_key, "source_file_id": file["id"], "form_id": source_form_id,
                                "assignment_form_id": assignment_form_id, "source_response_id": file.get("source_response_id"),
                                "source_modified_at": source.get("modifiedTime", ""), "source_version": source.get("version", ""),
                                "submitted_at": submitted_at, "assignment": title, "date_basis": date_basis,
                                "identity_status": version_person["identity_status"], "identity_basis": version_person["basis"],
                                "lesson_status": lesson_status, "original_filename": source.get("name", ""),
                                "source_context": {"week": found["week"], "form_label": form.get("label", ""),
                                                   "source": version.get("source"), "source_created_at": source.get("createdTime"),
                                                   "parents": source.get("parents", []), "via": file.get("via"),
                                                   "named_week": file.get("other_week"), "source_form_id": source_form_id,
                                                   "assignment_form_id": assignment_form_id, "accepted_intake": file.get("accepted_intake") is True,
                                                   "source_response_id": file.get("source_response_id"), "source_submitted_at": file.get("source_submitted_at")}})
                        # Keep observations as immutable index records; the shortcut
                        # remains one per original Drive ID as that file changes.
                        observation = {"source_file_id": file["id"], "modified_at": source.get("modifiedTime", ""),
                                       "version": source.get("version", ""), "submitted_at": submitted_at,
                                       "identity_status": version_person["identity_status"], "lesson_status": lesson_status,
                                       "date_basis": date_basis, "source_context": {"week": found["week"], "form_id": source_form_id,
                                           "assignment_form_id": assignment_form_id, "accepted_intake": file.get("accepted_intake") is True,
                                           "source_response_id": file.get("source_response_id"), "source_submitted_at": file.get("source_submitted_at")}}
                        observation_key = key("archiveobservation", [artifact_key, observation])
                        if not _read(run.port, observation_key):
                            run.put(observation_key, observation)
                        if not central_navigation:
                            run.attach(student, artifact)
                        # Originals remain in their Form upload/intake tree. A
                        # dated, content-verified snapshot lives with feedback.
                        existing_joint = None
                        student_doc = _read(run.port, student["student_key"])
                        for candidate in _batch_read(run.port, list(student_doc.value.get("items", {}))).values():
                            if (candidate and candidate.value.get("source_kind") in {"teacher_attributed_joint_copy", "submitted_work_export"}
                                    and candidate.value.get("source_file_id") == source["id"]
                                    and candidate.value.get("source_version") == source.get("version")
                                    and candidate.value.get("source_modified_at") == source.get("modifiedTime")):
                                if candidate.value["source_kind"] == "submitted_work_export":
                                    if candidate.value.get("source_revision") != _source_revision(source):
                                        continue
                                    _verify_work_export(google.drive_file(token, candidate.value["drive_file_id"], FIELDS), candidate.value)
                                existing_joint = candidate.value
                        copied = existing_joint or run.copy_work(student, source, artifact)
                        run.attach(student, copied)
                        if central_navigation:
                            run.retire_navigation(student["student_key"], artifact_key, copied["record_key"])
                        filed_ids.add(copied["drive_file_id"])
                        result["filed"] = len(filed_ids)
            except (Held, google.GoogleError) as error:
                result["held"].append({**context, "problem": str(error)})
        result["problems"].extend(_clean(p, 400) for p in found.get("problems", []) if p)
        if preserved_history:
            result["historical_preserved"] = len(preserved_history)
        calendar_not_found = (calendar_week and not found.get("other_files") and not found.get("folder_files")
            and found.get("folder_note") == "No turn-in folder for %s %s was found in %s." % (
                CLASSES[class_key], found["week"], CLASSES[class_key]))
        if found.get("folder_note") and not calendar_not_found:
            result["problems"].append(_clean(found["folder_note"], 400))
        result["status"] = "held" if result["held"] or result["problems"] else "verified"
        run.filing_receipt(found, result)
        run.finish(result)
    except (Held, google.GoogleError, Conflict, StoreUnavailable) as error:
        result.update(status="held", problems=[_problem(error)])
        if run:
            try:
                run.filing_receipt(found, result)
            except (Held, google.GoogleError, Conflict, StoreUnavailable):
                result["problems"].append("The filing receipt could not be confirmed; previous recorded weeks remain unchanged.")
            try:
                run.finish(result)
            except (Conflict, StoreUnavailable):
                pass
    result["index_sync"] = refresh_work_index(token, write_token, config)
    return result


def file_work_export(token, write_token, config, class_key, student_key, source_file_id, *,
                     expected_revision, blocked_copy_key):
    """Explicit operator-only export fallback; no source, native-copy or week-receipt writes."""
    run = None
    try:
        run = _Run(token, write_token, config, class_key)
        index = _read(run.port, run.index_key)
        student_doc = _read(run.port, student_key)
        artifact_doc = _read(run.port, key("archivework", [run.root, class_key, source_file_id]))
        if (not index or index.value.get("schema") != SCHEMA or not student_doc
                or student_doc.value.get("schema") != SCHEMA or student_doc.value.get("record_key") != student_key
                or student_doc.value.get("status") != "verified"
                or student_doc.value.get("student_key") != student_key
                or index.value.get("students", {}).get(student_key) != student_doc.value.get("drive_file_id")
                or not artifact_doc or artifact_doc.value.get("status") != "verified"
                or artifact_doc.value.get("kind") != "work" or artifact_doc.value.get("mime") != SHORTCUT
                or artifact_doc.value.get("schema") != SCHEMA or artifact_doc.value.get("class_key") != class_key
                or artifact_doc.value.get("record_key") != key("archivework", [run.root, class_key, source_file_id])
                or artifact_doc.value.get("source_file_id") != source_file_id
                or artifact_doc.value.get("student_key") != student_key):
            raise Held("The export source must already belong to this exact verified student archive.")
        student, artifact = student_doc.value, artifact_doc.value
        _private(google.drive_file(token, student["parent"], FIELDS), parent=run.course["id"], folder=True)
        _private(google.drive_file(token, student["drive_file_id"], FIELDS), parent=student["parent"], folder=True, write=True)
        source = google.drive_file(token, source_file_id, SOURCE_FIELDS)
        if _source_revision(source) != expected_revision:
            raise Held("The source revision changed after the explicit export plan. No snapshot was requested.")
        meta = _private(google.drive_file(token, artifact["drive_file_id"], FIELDS), parent=artifact["parent"])
        if ((meta.get("properties") or {}).get(TAG) != artifact["record_key"] or meta.get("mimeType") != SHORTCUT
                or (meta.get("shortcutDetails") or {}).get("targetId") != source_file_id):
            raise Held("The existing source link changed. Preserve it and the unresolved native request.")
        exported = run.export_work(student, source, artifact, blocked_copy_key)
        run.attach(student, exported)
        if config.get(RECORDS_CONFIG) or artifact.get("retired_navigation"):
            run.retire_navigation(student_key, artifact["record_key"], exported["record_key"])
        result = {"status": "verified", "class_key": class_key, "record_key": exported["record_key"],
                  "drive_file_id": exported["drive_file_id"], "source_kind": exported["source_kind"], "filed": 1}
        run.finish(result)
        return result
    except (Held, google.GoogleError, Conflict, StoreUnavailable) as error:
        result = {"status": "held", "class_key": class_key, "problem": _problem(error)}
        if run:
            try:
                run.finish(result)
            except (Conflict, StoreUnavailable):
                pass
        return result


def retire_source_links(token, write_token, config, class_key):
    """Authorized private layout cleanup, with one class claim and exact copy proof."""
    if not config.get(RECORDS_CONFIG):
        raise Held("Source-link cleanup needs the explicitly configured private Hub records container.")
    run = _Run(token, write_token, config, class_key)
    result = {"class_key": class_key, "status": "verified", "retired": 0, "held": []}
    index = _read(run.port, run.index_key)
    for student_key in (index.value if index else {}).get("students", {}):
        run.heartbeat()
        student = _read(run.port, student_key)
        records = _batch_read(run.port, list(student.value.get("items", {})))
        for shortcut_key, doc in records.items():
            if not doc or doc.value.get("mime") != SHORTCUT or doc.value.get("kind") != "work":
                continue
            item = doc.value
            try:
                source = google.drive_file(token, item["source_file_id"], SOURCE_FIELDS)
                candidates = [copy_key for copy_key, copy_doc in records.items() if copy_doc
                    and copy_doc.value.get("source_kind") in {"submitted_work_copy", "submitted_work_export", "teacher_attributed_joint_copy"}
                    and copy_doc.value.get("status") == "verified"
                    and copy_doc.value.get("source_file_id") == item.get("source_file_id")
                    and str(copy_doc.value.get("source_version")) == str(source.get("version"))]
                ordinary = [i for i in candidates if records[i].value["source_kind"] == "submitted_work_copy"]
                exported = [i for i in candidates if records[i].value["source_kind"] == "submitted_work_export"]
                candidates = ordinary or exported or candidates
                if len(candidates) != 1:
                    raise Held("This original-work link needs one verified real copy for its exact revision before cleanup.")
                run.retire_navigation(student_key, shortcut_key, candidates[0])
                result["retired"] += 1
            except (Held, google.GoogleError) as error:
                result["held"].append({"student_key": student_key, "source_file_id": item.get("source_file_id"), "problem": str(error)})
    if result["held"]: result["status"] = "held"
    run.finish(result)
    return result


def file_feedback(token, write_token, config, saved_value, pdf_bytes):
    cls = (saved_value.get("scope") or {}).get("class")
    if not enabled(config):
        return _result(cls, configured=False, status="unconfigured")
    run = None
    try:
        if (not saved_value.get("saved_at") or not re.fullmatch(r"feedbackdraft_[a-f0-9]{64}", str(saved_value.get("id", "")))
                or not isinstance(pdf_bytes, bytes) or not pdf_bytes.startswith(b"%PDF") or len(pdf_bytes) > 25_000_000):
            raise Held("Only verified saved feedback with a complete PDF can be archived.")
        person = _identity(config, cls, saved_value.get("archive_identity") or {})
        run = _Run(token, write_token, config, cls)
        student = run.student(person)
        date = _date(saved_value["saved_at"])
        title = _clean((saved_value.get("assignment") or {}).get("assignment_title"))
        ident = key("archivefeedback", [run.root, saved_value["id"]])
        old = _read(run.port, ident)
        if old and old.value.get("student_key") != student["student_key"]:
            raise Held("This saved report already belongs to another verified student archive.")
        artifact = run.create(ident, date + " — " + title + " — Feedback.pdf", student["drive_file_id"], PDF,
            pdf=pdf_bytes, attributes={"kind": "feedback", "date": date, "feedback_id": saved_value["id"],
                "student_key": student["student_key"], "class_key": cls, "saved_at": saved_value["saved_at"],
                "source_file_id": (saved_value.get("scope") or {}).get("file"),
                "source_sha256": (saved_value.get("source") or {}).get("sha256"), "assignment": title,
                "source_version": ((saved_value.get("source") or {}).get("file") or {}).get("version"),
                "source_modified_at": ((saved_value.get("source") or {}).get("file") or {}).get("modified_at"),
                "assignment_form_id": (saved_value.get("scope") or {}).get("form"),
                "source_context": {"week": (saved_value.get("scope") or {}).get("week"),
                                   "assignment_form_id": (saved_value.get("scope") or {}).get("form")}})
        run.attach(student, artifact)
        value = _feedback_metadata(artifact, student, run.course)
        run.finish({"status": "verified", "feedback_id": saved_value["id"], "drive_file_id": artifact["drive_file_id"]})
        value["feedback_checklists"] = run.feedback_checklists
        value["index_sync"] = refresh_work_index(token, write_token, config)
        from . import print_ready_email
        value['email_outbox'] = print_ready_email.notify_ready(token, config, [saved_value['id']])
        return value
    except (Held, google.GoogleError, Conflict, StoreUnavailable) as error:
        result = {"status": "held", "class_key": cls, "feedback_id": saved_value.get("id"), "problem": _problem(error)}
        if run:
            try:
                run.finish(result)
            except (Conflict, StoreUnavailable):
                pass
        return result


def _feedback_metadata(artifact, student, course):
    return {"status": "verified", "drive_file_id": artifact["drive_file_id"], "name": artifact["name"],
            "feedback_id": artifact["feedback_id"], "class_key": artifact["class_key"],
            "student_folder_id": student["drive_file_id"], "student_folder_name": student["name"],
            "student_key": student["student_key"],
            "student_section": student.get("section"), "assignment": artifact.get("assignment"),
            "class_folder_id": course["id"], "class_folder_name": course.get("name", ""),
            "created_at": artifact["created_at"], "saved_at": artifact["saved_at"],
            "source_sha256": artifact.get("source_sha256"), "drive_md5": artifact.get("pdf_md5"),
            "pdf_sha256": artifact.get("pdf_sha256"),
            "shared_source_key": artifact.get("shared_source_key"),
            "shared_source_document_id": artifact.get("shared_source_document_id"),
            "shared_pdf_sha256": artifact.get("shared_pdf_sha256"),
            "covered_by_packet_manifest": artifact.get("covered_by_packet_manifest"),
            "covered_by_packet_id": artifact.get("covered_by_packet_id"),
            "drive_version": artifact.get("drive_version"),
            "source_file_id": artifact.get("source_file_id"), "source_span": artifact.get("source_span"),
            "feedback_origin": artifact.get("feedback_origin", "native_saved"),
            "report_type": artifact.get("report_type"),
            "presentation_version": artifact.get("presentation_version", 1),
            "supersedes_report_id": artifact.get("supersedes_report_id"),
            "superseded_by": artifact.get("superseded_by"),
            "supersession_verified": artifact.get("supersession_verified", False),
            "source_records": artifact.get("source_records"),
            "contract_keys": artifact.get("contract_keys"),
            "contract_status": artifact.get("contract_status"),
            "contract_checked_at": artifact.get("contract_checked_at"),
            "original_due_date": artifact.get("original_due_date"),
            "source_proof": artifact.get("source_proof"),
            "identity_status": artifact.get("identity_status", "verified"),
            "date": artifact["date"], "mime": PDF, "url": artifact["url"]}


def _science_payload(report):
    return {k: v for k, v in report.items() if k not in {"pdf_path", "pdf_bytes", "supersession_previous_payload"}}


def _science_assessment(report):
    return {k: v for k, v in _science_payload(report).items() if k not in {
        "id", "presentation_version", "supersedes_report_id", "prepared_at", "saved_at", "pdf_sha256",
        "report_source_sha256", "presentation_context"}}


def _science_presentation_context(report, sources):
    context = report.get("presentation_context")
    if context is None:
        return
    if (not isinstance(context, dict) or "original_submission_times" not in context
            or set(context) - {"original_submission_times", "writing_display"}):
        raise Held("This presentation context contains unsupported assessment changes.")
    events = context["original_submission_times"]
    if not isinstance(events, list) or len(events) > 500:
        raise Held("This presentation needs bounded original submission times.")
    known = {(r["source_file_id"], str(r["source_version"])): r for r in sources}
    seen = set()
    for event in events:
        if not isinstance(event, dict) or set(event) != {"source_file_id", "source_revision", "submitted_at"}:
            raise Held("This presentation contains an unverified submission-time field.")
        ident = (event["source_file_id"], str(event["source_revision"]))
        original = known.get(ident)
        if (ident in seen or not original or event["submitted_at"] != original.get("submitted_at")
                or (event["submitted_at"] and (_date(event["submitted_at"]) != original.get("date")
                                              or original.get("date_basis") != "form_receipt"))):
            raise Held("This chart's submission order does not match the original verified Form receipt.")
        seen.add(ident)
    if "writing_display" in context:
        # Teaching practice is a source-bound presentation overlay. It cannot
        # replace the immutable saved levels, quotations or assessment target.
        from .science_writing_display import validate
        try:
            validate(report)
        except ValueError as error:
            raise Held(str(error)) from None


def _feedback_file(token, scope, course, student, artifact, metadata=None):
    """Verify current or explicitly relocated historical feedback, never any folder."""
    parent = artifact["parent"]
    if parent != student["drive_file_id"]:
        history_key = key("archivefeedbackhistory", [scope, artifact["class_key"]])
        if (artifact.get("report_type") != "science_writing" or not artifact.get("superseded_by")
                or artifact.get("history_original_parent") != student["drive_file_id"]
                or artifact.get("history_folder_key") != history_key):
            raise Held("This feedback left its student folder without a verified previous-version location.")
        history = (metadata or {}).get(parent) or google.drive_file(token, parent, FIELDS)
        _private(history, parent=course["id"], folder=True)
        if (history.get("properties") or {}).get(TAG) != history_key:
            raise Held("The previous-feedback folder is not the tagged private class history folder.")
    meta = (metadata or {}).get(artifact["drive_file_id"]) or google.drive_file(token, artifact["drive_file_id"], FIELDS)
    _private(meta, parent=parent)
    if ((meta.get("properties") or {}).get(TAG) != artifact["record_key"] or meta.get("mimeType") != PDF
            or meta.get("md5Checksum") != artifact.get("pdf_md5")
            or str(meta.get("size")) != str(artifact.get("size"))):
        raise Held("A saved feedback PDF changed or no longer matches its immutable private artifact.")
    return meta


def _feedback_successor(token, scope, course, student, artifact, documents, metadata=None):
    current, seen = artifact, set()
    for _ in range(8):
        ident = current["record_key"]
        if (current.get("status") != "verified" or current.get("schema") != SCHEMA
                or current.get("kind") != "feedback" or current.get("report_type") != "science_writing"
                or current.get("class_key") != artifact.get("class_key")
                or current.get("student_key") != student["student_key"]
                or ident != key("archivefeedback", [scope, current.get("feedback_id")])):
            raise Held("This feedback version does not match its verified student archive.")
        if ident in seen:
            raise Held("This feedback's previous-version chain contains a cycle; history is preserved.")
        seen.add(ident)
        _feedback_file(token, scope, course, student, current, metadata)
        successor_id = current.get("superseded_by")
        if not successor_id:
            if current["parent"] != student["drive_file_id"]:
                raise Held("A current feedback version is outside its verified student folder.")
            return current
        successor_key = key("archivefeedback", [scope, successor_id])
        doc = documents.get(successor_key)
        successor = doc.value if doc else None
        proof = current.get("supersession")
        if (not re.fullmatch(r"feedbacksource_[a-f0-9]{64}", str(successor_id))
                or current.get("superseded_by_record_key") != successor_key
                or not successor or successor.get("status") != "verified" or successor.get("schema") != SCHEMA
                or current.get("kind") != "feedback" or successor.get("kind") != "feedback"
                or successor.get("record_key") != successor_key or successor.get("feedback_id") != successor_id
                or current.get("report_type") != successor.get("report_type") or successor.get("report_type") != "science_writing"
                or current.get("student_key") != student["student_key"] or successor.get("student_key") != student["student_key"]
                or current.get("class_key") != successor.get("class_key")
                or successor.get("supersedes_report_id") != current.get("feedback_id")
                or current.get("source_records") != successor.get("source_records")
                or successor.get("supersession_prior_payload_sha256") != current.get("report_source_sha256")
                or not isinstance(proof, dict)
                or proof.get("prior_payload_sha256") != current.get("report_source_sha256")
                or proof.get("assessment_sha256") != successor.get("presentation_assessment_sha256")
                or proof.get("successor_pdf_sha256") != successor.get("pdf_sha256")
                or not re.fullmatch(r"[a-f0-9]{64}", str(proof.get("assessment_sha256")))
                or type(successor.get("presentation_version", 1)) is not int
                or type(current.get("presentation_version", 1)) is not int
                or successor.get("presentation_version", 1) <= current.get("presentation_version", 1)):
            raise Held("The saved successor is not verified for this same student's unchanged assessment. Previous feedback is preserved.")
        current = successor
    raise Held("This feedback's previous-version chain exceeds its verified limit.")


def _feedback_replacement_ready(token, scope, course, student, artifact, documents, metadata=None):
    """A saved replacement is current only after its incoming pointer is confirmed."""
    prior_id = artifact.get("supersedes_report_id")
    if prior_id is None:
        return True
    prior_key = key("archivefeedback", [scope, prior_id])
    prior_doc = documents.get(prior_key)
    prior = prior_doc.value if prior_doc else None
    if (not re.fullmatch(r"feedbacksource_[a-f0-9]{64}", str(prior_id)) or not prior
            or prior.get("record_key") != prior_key or prior.get("feedback_id") != prior_id
            or prior.get("status") != "verified" or prior.get("schema") != SCHEMA
            or prior.get("kind") != "feedback" or prior.get("report_type") != "science_writing"
            or prior.get("class_key") != artifact.get("class_key")
            or prior.get("student_key") != student["student_key"]
            or prior.get("source_records") != artifact.get("source_records")
            or artifact.get("supersession_prior_payload_sha256") != prior.get("report_source_sha256")
            or type(prior.get("presentation_version", 1)) is not int
            or type(artifact.get("presentation_version", 1)) is not int
            or artifact.get("presentation_version", 1) <= prior.get("presentation_version", 1)):
        raise Held("This replacement's saved prior version is not verified for the same unchanged student assessment.")
    _feedback_file(token, scope, course, student, prior, metadata)
    if not prior.get("superseded_by"):
        # The PDF upload/attachment may have succeeded before a CAS response
        # failed. Keep the prior version current; do not print both or repeat
        # the upload while the same-ID save recovery remains unresolved.
        return False
    if prior["superseded_by"] != artifact.get("feedback_id"):
        raise Held("Two replacements claim the same prior feedback; history is preserved for review.")
    _feedback_successor(token, scope, course, student, prior, documents, metadata)
    return True


def verify_feedback_successor(token, config, class_key, prior_feedback_id):
    """Read-only proof for the exact saved chain; no movement, filing or assessment."""
    try:
        _, course = _location(token, config, class_key)
        root, _ = _configuration(config); scope = _scope_root(config, root)
        port = configured_store(config); prior_key = key("archivefeedback", [scope, prior_feedback_id])
        prior = _read(port, prior_key)
        if (not prior or prior.value.get("record_key") != prior_key or prior.value.get("feedback_id") != prior_feedback_id
                or prior.value.get("class_key") != class_key or not prior.value.get("superseded_by")):
            raise Held("This report has no confirmed saved successor.")
        student = _read(port, prior.value["student_key"])
        index = _read(port, key("archiveclass", [scope, class_key]))
        if (not student or student.value.get("status") != "verified" or not index
                or index.value.get("students", {}).get(prior.value["student_key"]) != student.value.get("drive_file_id")
                or prior_key not in student.value.get("items", {})):
            raise Held("The successor's existing student archive is unavailable.")
        documents = _batch_read(port, list(student.value.get("items", {})))
        _private(google.drive_file(token, student.value["parent"], FIELDS), parent=course["id"], folder=True)
        _private(google.drive_file(token, student.value["drive_file_id"], FIELDS), parent=student.value["parent"], folder=True)
        current = _feedback_successor(token, scope, course, student.value, prior.value, documents)
        return {"status": "verified", "class_key": class_key, "student_key": student.value["student_key"],
                "prior_report_id": prior_feedback_id, "prior_record_key": prior_key, "prior_drive_file_id": prior.value["drive_file_id"],
                "current_report_id": current["feedback_id"], "current_record_key": current["record_key"],
                "current_drive_file_id": current["drive_file_id"], "current_url": current["url"],
                "prior_pdf_sha256": prior.value["pdf_sha256"], "current_pdf_sha256": current["pdf_sha256"]}
    except (Held, google.GoogleError, Conflict, StoreUnavailable) as error:
        return {"status": "held", "problem": _problem(error)}


def file_science_writing(token, write_token, config, saved_report, pdf_bytes, *,
                         imported_review_sha256=None, imported_teacher_reference_sha256=None, refresh_index=True):
    """File teacher-authorized science-writing feedback in one existing folder."""
    cls, run = saved_report.get("class_key"), None
    try:
        if type(refresh_index) is not bool:
            raise Held("The trusted filing worker must explicitly preserve or defer the index refresh.")
        if (saved_report.get("schema") != "science-writing/v1" or saved_report.get("report_type") != "science_writing"
                or not re.fullmatch(r"feedbacksource_[a-f0-9]{64}", str(saved_report.get("id", "")))
                or not isinstance(pdf_bytes, bytes) or not pdf_bytes.startswith(b"%PDF") or len(pdf_bytes) > 25_000_000
                or saved_report.get("pdf_sha256") != hashlib.sha256(pdf_bytes).hexdigest()):
            raise Held("Only a complete source-bound science-writing report can be filed.")
        saved_at = saved_report.get("saved_at") or saved_report.get("prepared_at")
        date = _date(saved_at)
        named = saved_report.get("student") or {}
        root, _ = _configuration(config); scope = _scope_root(config, root)
        port = configured_store(config)
        index = _read(port, key("archiveclass", [scope, cls]))
        student_doc = _read(port, named.get("student_key", ""))
        if (not index or not student_doc or named.get("student_key") not in index.value.get("students", {})
                or student_doc.value.get("drive_file_id") != named.get("student_folder_id")
                or student_doc.value.get("name") != named.get("name") or student_doc.value.get("section") != named.get("section")):
            raise Held("This science-writing report does not match its existing verified class/student folder.")
        student = student_doc.value
        artifacts = [d.value for d in _batch_read(port, list(student.get("items", {}))).values() if d and d.value.get("kind") == "work"]
        observations = (saved_report.get("provenance") or {}).get("source_observations", [])
        if not isinstance(observations, list) or not observations or len(observations) > 500:
            raise Held("The science-writing report has no bounded source observations.")
        sources, imported_metadata = {}, {}
        for observation in observations:
            ident = observation.get("source_file_id")
            revision = str(observation.get("source_revision") or "")
            if (observation.get("student_key") != student["student_key"] or observation.get("section") != student["section"]
                    or not ident or not revision):
                raise Held("A science-writing observation belongs to a different student or has no source revision.")
            if ident in sources:
                if (sources[ident]["source_version"] != revision
                        or (observation.get("source_kind") == IMPORTED_ORIGINAL
                            and any(sources[ident].get(k) != observation.get(o) for k, o in (
                                ("source_record_key", "source_record_key"), ("source_sha256", "source_sha256"),
                                ("source_review_sha256", "source_review_sha256"), ("date", "date"), ("date_basis", "date_basis"))))):
                    raise Held("This science-writing report combines unconfirmed revisions of one submission.")
                continue
            if observation.get("source_kind") == IMPORTED_ORIGINAL:
                owned = [a for a in artifacts if a.get("status") == "verified"
                         and a.get("source_kind") == IMPORTED_ORIGINAL
                         and a.get("record_key") == observation.get("source_record_key")
                         and a.get("drive_file_id") == ident and a.get("student_key") == student["student_key"]]
                if len(owned) != 1:
                    raise Held("This writing source has no single verified imported-original record.")
                artifact = owned[0]
                meta = google.drive_file(token, ident, FIELDS)
                _verify_imported_original(meta, artifact)
                from . import imported_original_feedback
                evidence = imported_original_feedback.validate_saved_report(saved_report,
                    expected_review_sha256=imported_review_sha256,
                    expected_teacher_reference_sha256=imported_teacher_reference_sha256)
                if (str(meta.get("version")) != revision or meta.get("modifiedTime") != observation.get("source_modified_at")
                        or observation.get("source_sha256") != artifact["copied_content_proof"]["sha256"]
                        or observation.get("date") != artifact["date"] or observation.get("date_basis") != artifact["date_basis"]
                        or observation.get("submitted_at") is not None
                        or evidence.get("record_key") != artifact["record_key"] or evidence.get("source_metadata") != meta
                        or evidence.get("import_provenance") != artifact["import_provenance"]
                        or evidence.get("source_sha256") != observation.get("source_sha256")
                        or evidence.get("review_sha256") != observation.get("source_review_sha256")
                        or evidence.get("student", {}).get("student_key") != student["student_key"]):
                    raise Held("This imported worksheet's exact writing evidence or external submission date changed.")
                imported_metadata[ident] = meta
                sources[ident] = {"source_kind": IMPORTED_ORIGINAL, "source_record_key": artifact["record_key"],
                    "source_file_id": ident, "source_version": revision, "source_modified_at": meta.get("modifiedTime"),
                    "source_sha256": artifact["copied_content_proof"]["sha256"], "source_review_sha256": observation.get("source_review_sha256"),
                    "date": artifact["date"], "date_basis": artifact["date_basis"], "submitted_at": None,
                    "assignment": artifact["assignment"], "lesson_status": artifact["lesson_status"],
                    "source_context": artifact["source_context"], "import_provenance": artifact["import_provenance"]}
                continue
            owned = [a for a in artifacts if a.get("status") == "verified" and a.get("source_kind") != NAMED_ARCHIVE
                     and a.get("source_file_id") == ident and a.get("source_version") == revision]
            if not owned:
                raise Held("This science-writing source is not yet filed for the report's verified student.")
            meta = google.drive_file(token, ident, SOURCE_FIELDS)
            if (meta.get("trashed") is not False or str(meta.get("version")) != revision
                    or meta.get("modifiedTime") != observation.get("source_modified_at")):
                raise Held("This student's work changed after the science-writing report was prepared. Preserve the report for review.")
            artifact = max(owned, key=lambda a: (a.get("submitted_at", ""), a.get("record_key", "")))
            sources[ident] = {k: artifact.get(k) for k in ("source_file_id", "source_version", "source_modified_at",
                "submitted_at", "date", "date_basis", "form_id", "assignment_form_id", "assignment", "lesson_status", "source_context")}
        version = saved_report.get("presentation_version", 1)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise Held("This report's presentation version is invalid.")
        _science_presentation_context(saved_report, list(sources.values()))
        prior_id = saved_report.get("supersedes_report_id")
        prior_key, prior, assessment_hash = None, None, None
        if prior_id is not None:
            if (not re.fullmatch(r"feedbacksource_[a-f0-9]{64}", str(prior_id)) or prior_id == saved_report["id"]):
                raise Held("A presentation replacement needs a distinct saved prior report ID.")
            prior_key = key("archivefeedback", [scope, prior_id]); prior = _read(port, prior_key)
            previous_payload = saved_report.get("supersession_previous_payload")
            if (not prior or prior.value.get("status") != "verified" or prior.value.get("kind") != "feedback"
                    or prior.value.get("report_type") != "science_writing"
                    or prior_key not in student.get("items", {})
                    or prior.value.get("feedback_id") != prior_id or prior.value.get("student_key") != student["student_key"]
                    or prior.value.get("class_key") != cls or prior.value.get("source_records") != list(sources.values())
                    or type(prior.value.get("presentation_version", 1)) is not int
                    or version <= prior.value.get("presentation_version", 1)
                    or prior.value.get("superseded_by") not in (None, saved_report["id"])
                    or not isinstance(previous_payload, dict)
                    or content_hash(_science_payload(previous_payload)) != prior.value.get("report_source_sha256")
                    or _science_assessment(previous_payload) != _science_assessment(saved_report)):
                raise Held("This replacement must preserve the same saved student's exact evidence, assessment and target.")
            _, course = _location(token, config, cls)
            _feedback_file(token, scope, course, student, prior.value)
            assessment_hash = content_hash(_science_assessment(saved_report))
        elif version != 1 or saved_report.get("supersession_previous_payload") is not None:
            raise Held("A later presentation version needs its exact prior saved report proof.")
        payload = _science_payload(saved_report)
        report_hash = content_hash(payload)
        run = _Run(token, write_token, config, cls)
        _private(google.drive_file(token, student["parent"], FIELDS), parent=run.course["id"], folder=True)
        _private(google.drive_file(token, student["drive_file_id"], FIELDS), parent=student["parent"], folder=True, write=True)
        if any(google.drive_file(token, ident, FIELDS) != meta for ident, meta in imported_metadata.items()):
            raise Held("An imported writing source changed before feedback filing. No report was created.")
        ident = key("archivefeedback", [scope, saved_report["id"]]); old = _read(run.port, ident)
        if old and (old.value.get("student_key") != student["student_key"]
                    or old.value.get("report_source_sha256") != report_hash
                    or old.value.get("pdf_sha256") != saved_report["pdf_sha256"]):
            raise Held("This report ID already has different student feedback content. Preserve the original PDF.")
        if old and old.value.get("superseded_by"):
            docs = _batch_read(port, list(student.get("items", {})))
            _feedback_successor(token, scope, run.course, student, old.value, docs)
            historical = {**old.value, "supersession_verified": True}
            run.finish({"status": "verified", "historical_replay": saved_report["id"], "new_files": 0})
            return _feedback_metadata(historical, student, run.course)
        artifact = run.create(ident, date + " — Science writing — " + student["name"] + " — Feedback.pdf",
            student["drive_file_id"], PDF, pdf=pdf_bytes, attributes={"kind": "feedback", "date": date,
                "feedback_id": saved_report["id"], "report_type": "science_writing", "feedback_origin": "teacher_science_writing",
                "student_key": student["student_key"], "class_key": cls, "saved_at": saved_at,
                "assignment": "Science writing across %d verified submission%s" % (len(sources), "" if len(sources) == 1 else "s"),
                "source_records": list(sources.values()), "report_source_sha256": report_hash,
                "presentation_version": version, "supersedes_report_id": prior_id,
                "supersession_prior_payload_sha256": prior.value.get("report_source_sha256") if prior else None,
                "presentation_assessment_sha256": assessment_hash,
                "source_sha256": report_hash, "identity_status": student.get("identity_status", "identity_unverified")})
        run.attach(student, artifact)
        _feedback_file(token, scope, run.course, student, artifact)
        if any(google.drive_file(token, ident, FIELDS) != meta for ident, meta in imported_metadata.items()):
            raise Held("An imported writing source changed after filing; preserve the report for review.")
        if prior:
            # A single fenced class claim serializes replacements. Never retire
            # the current version before the new PDF and its owner are verified.
            current_prior = _read(run.port, prior_key)
            if not current_prior or current_prior.value != prior.value:
                raise Held("The saved prior report changed while its successor was being verified.")
            revised = {**prior.value, "superseded_by": saved_report["id"], "superseded_by_record_key": ident,
                "supersession": {"prior_payload_sha256": prior.value["report_source_sha256"],
                    "assessment_sha256": assessment_hash, "successor_pdf_sha256": artifact["pdf_sha256"],
                    "confirmed_at": datetime.now(timezone.utc).isoformat()}}
            if current_prior.value.get("superseded_by") != saved_report["id"]:
                run.put(prior_key, revised, current_prior)
            owner = _read(run.port, student["student_key"])
            docs = _batch_read(run.port, list(owner.value.get("items", {})))
            _feedback_successor(token, scope, run.course, owner.value, docs[prior_key].value, docs)
        run.finish({"status": "verified", "report_type": "science_writing", "feedback_id": saved_report["id"], "drive_file_id": artifact["drive_file_id"]})
        value = _feedback_metadata(artifact, student, run.course)
        value["feedback_checklists"] = run.feedback_checklists
        value["index_sync"] = (refresh_work_index(token, write_token, config) if refresh_index else
            {"status": "deferred", "native_index_write": False, "problem": "The Root worker will verify the existing source rows before updating their feedback links."})
        from . import print_ready_email
        value['email_outbox'] = print_ready_email.notify_ready(token, config, [saved_report['id']])
        return value
    except (Held, google.GoogleError, Conflict, StoreUnavailable) as error:
        result = {"status": "held", "class_key": cls, "feedback_id": saved_report.get("id"), "problem": _problem(error)}
        if run:
            try:
                run.finish(result)
            except (Conflict, StoreUnavailable):
                pass
        return result


def _batch_read(port, ids):
    result = {}
    for i in range(0, len(ids), 40):
        result.update(port.read(ids[i:i + 40]).documents)
    return result


def _indexed(token, config, cls, *, feedback_only=False, include_history=False):
    actual_root, course = _location(token, config, cls)
    root = _scope_root(config, actual_root)
    port = configured_store(config)
    index = _read(port, key("archiveclass", [root, cls]))
    indexed_students = (index.value if index else {}).get("students", {})
    if (not isinstance(indexed_students, dict) or (index and index.value.get("schema") != SCHEMA)):
        raise Held("The private class archive index needs checking.")
    ids = list(indexed_students)
    if len(ids) > MAX_STUDENTS:
        raise Held("The student archive index needs its size checked.")
    students = _batch_read(port, ids)
    checked, item_ids, metadata_ids = [], [], set()
    for ident in ids:
        doc = students.get(ident)
        if (not doc or doc.value.get("status") != "verified" or doc.value.get("schema") != SCHEMA
                or doc.value.get("record_key") != ident or doc.value.get("student_key") != ident
                or doc.value.get("drive_file_id") != indexed_students[ident]):
            raise Held("One student folder is not confirmed in the private archive index.")
        student = doc.value
        student_items = list(student.get("items", {}))
        if len(student_items) > MAX_ITEMS:
            raise Held("One student's archive has too many unverified records.")
        checked.append((student, student_items))
        item_ids.extend(student_items)
        metadata_ids.update((student["parent"], student["drive_file_id"]))
    # Read all indexed artifacts in the same bounded sequential CAS batches.
    # Repeated section parents need just one fresh metadata read per request.
    documents = _batch_read(port, list(dict.fromkeys(item_ids)))
    for ident, item in documents.items():
        if (not item or item.value.get("status") != "verified" or item.value.get("schema") != SCHEMA
                or item.value.get("record_key") != ident or item.value.get("class_key") != cls
                or item.value.get("kind") not in {"feedback", "work"}):
            raise Held("An archived record has not been verified yet.")
        if not feedback_only or item.value["kind"] == "feedback":
            metadata_ids.add(item.value["drive_file_id"])
    for student, student_items in checked:
        for ident in student_items:
            item = documents.get(ident)
            historical = bool(item and item.value.get("kind") == "feedback" and item.value.get("superseded_by")
                and item.value.get("history_original_parent") == student["drive_file_id"]
                and item.value.get("history_folder_key") == key("archivefeedbackhistory", [root, cls]))
            if (not item or item.value.get("student_key") != student["student_key"]
                    or (item.value.get("parent") != student["drive_file_id"] and not historical)):
                raise Held("An archived record no longer belongs to its indexed student folder.")
            if historical:
                metadata_ids.add(item.value["parent"])
    fetch_ids = sorted(metadata_ids)
    # These are independent GETs only. Google's sessions are thread-local;
    # no index writes or checks run in workers and errors propagate unchanged.
    with ThreadPoolExecutor(max_workers=4) as pool:
        metadata = dict(zip(fetch_ids, pool.map(lambda ident: google.drive_file(token, ident, FIELDS), fetch_ids)))
    output = []
    for student, student_items in checked:
        _private(metadata[student["parent"]], parent=course["id"], folder=True)
        _private(metadata[student["drive_file_id"]], parent=student["parent"], folder=True)
        artifacts = []
        for item_id in student_items:
            item = documents.get(item_id)
            if not item or item.value.get("status") != "verified":
                raise Held("An archived record has not been verified yet.")
            artifact = dict(item.value)
            if feedback_only and artifact["kind"] != "feedback":
                continue
            meta = (_feedback_file(token, root, course, student, artifact, metadata) if artifact["kind"] == "feedback"
                    else _private(metadata[artifact["drive_file_id"]], parent=student["drive_file_id"]))
            if artifact.get("source_kind") not in {IMPORTED_ORIGINAL, NAMED_ARCHIVE} and (meta.get("properties") or {}).get(TAG) != item_id:
                raise Held("An archived record no longer matches its private index.")
            if artifact["kind"] == "feedback":
                if meta.get("mimeType") != PDF or meta.get("md5Checksum") != artifact.get("pdf_md5"):
                    raise Held("An archived feedback PDF changed and needs review.")
                artifact["drive_version"] = meta.get("version")
                if not _feedback_replacement_ready(token, root, course, student, artifact, documents, metadata):
                    continue
                if artifact.get("superseded_by"):
                    _feedback_successor(token, root, course, student, artifact, documents, metadata)
                    artifact["supersession_verified"] = True
                    if not include_history:
                        continue
            elif artifact.get("source_kind") == "teacher_attributed_joint_copy":
                if (meta.get("mimeType") not in work.STUDENT_WORK
                        or meta.get("mimeType") != artifact.get("mime")
                        or meta.get("version") != artifact.get("drive_version")
                        or (meta.get("properties") or {}).get("hubCopiedSource") != artifact.get("source_file_id")
                        or (meta.get("properties") or {}).get("hubSharedSource") != artifact.get("shared_source_key")
                        or not re.fullmatch(r"sharedfeedbacksource_[a-f0-9]{64}", artifact.get("shared_source_key") or "")
                        or not re.fullmatch(r"[a-f0-9]{64}", artifact.get("copied_slide_content_sha256") or "")
                        or artifact.get("teacher_joint_attribution") is not True):
                    raise Held("An archived joint work copy changed or no longer matches its verified source.")
            elif artifact.get("source_kind") == "submitted_work_copy":
                _verify_work_copy(meta, artifact)
            elif artifact.get("source_kind") == "submitted_work_export":
                _verify_work_export(meta, artifact)
            elif artifact.get("source_kind") == IMPORTED_ORIGINAL:
                _verify_imported_original(meta, artifact)
            elif artifact.get("source_kind") == NAMED_ARCHIVE:
                named_archival_work.verify(meta, artifact, scope=root)
            elif (meta.get("mimeType") != SHORTCUT
                  or (meta.get("shortcutDetails") or {}).get("targetId") != artifact["source_file_id"]):
                raise Held("An archived work shortcut no longer points to its verified source.")
            artifacts.append(artifact)
        # The original link is carried on the copy's source_file_id. Keeping
        # both in the private CAS history must not double the displayed work.
        copied_sources = {a.get("source_file_id") for a in artifacts
                          if a.get("source_kind") in {"submitted_work_copy", "submitted_work_export", "teacher_attributed_joint_copy"}}
        artifacts = [a for a in artifacts if not (a["kind"] == "work" and a.get("mime") == SHORTCUT
                                                  and a.get("source_file_id") in copied_sources)]
        output.append((student, artifacts))
    return course, output


def _filing(config, cls):
    actual_root, _ = _configuration(config)
    root = _scope_root(config, actual_root)
    port = configured_store(config)
    index = _read(port, key("archiveclass", [root, cls]))
    scans = (index.value if index else {}).get("filing_scans", {})
    if not isinstance(scans, dict) or len(scans) > MAX_SCANS:
        raise Held("The archive's recorded week coverage needs checking.")
    docs = _batch_read(port, list(scans.values()))
    receipts = []
    for week, ident in scans.items():
        doc = docs.get(ident)
        if (not doc or doc.value.get("schema") != SCHEMA
                or doc.value.get("class_key") != cls or doc.value.get("week") != week
                or ident != key("archivescan", [root, cls, week])
                or not isinstance(doc.value.get("held_count"), int)
                or doc.value["held_count"] < 0):
            raise Held("A filing receipt no longer matches its exact class and week. Recorded work is preserved.")
        receipts.append(doc.value)
    receipts.sort(key=lambda r: (work.week_number(r["week"]) or -1, r["week"]), reverse=True)
    held_count = sum(r["held_count"] for r in receipts)
    return {"scans": receipts, "held_count": held_count,
            "coverage": "holds_remain" if held_count else "scanned_weeks_only" if receipts else "unverified"}


def archive_listing(token, config, class_key, section=None):
    base = {"schema": SCHEMA, "class_key": class_key, "class_name": CLASSES.get(class_key, ""),
            "configured": enabled(config), "status": "unconfigured", "students": [],
            "filing": {"scans": [], "held_count": 0, "coverage": "unverified"},
            "teacher_reports_url": None, "problems": [], "counts": {"students": 0, "work": 0, "feedback": 0, "held": 0}}
    if not base["configured"]:
        return base
    try:
        course, indexed = _indexed(token, config, class_key)
        canonical = _roster(config)
        for student, artifacts in indexed:
            if section and student["section"] != section:
                continue
            active = None if canonical is None else any(p["class_key"] == class_key and p.get("active", True)
                and ((student.get("student_id") and str(p.get("student_id", "")) == student["student_id"])
                     or str(p.get("school_email", "")).lower() == student.get("school_email")) for p in canonical)
            records = [{"kind": a["kind"], "name": a["name"], "date": a["date"], "url": a["url"],
                        "drive_file_id": a["drive_file_id"], "source_file_id": a.get("source_file_id"),
                        "feedback_id": a.get("feedback_id"), "identity_status": a.get("identity_status", "verified"),
                        "date_basis": a.get("date_basis"), "lesson_status": a.get("lesson_status", "verified"),
                        "original_filename": a.get("original_filename"), "source_context": a.get("source_context"),
                        **({"source_created_at": a["source_created_at"], "grade_eligible": False,
                            "date_label": "Original file created " + a["source_created_at"][:10]}
                           if a.get("source_kind") == NAMED_ARCHIVE else {})} for a in artifacts]
            records.sort(key=lambda a: (a["date"] or "", a["name"]), reverse=True)
            roster_status = _confirmed_roster_status(student, class_key)
            base["students"].append({"student_key": student["student_key"], "name": student["name"],
                "section": student["section"], "status": "withdrawn" if roster_status and roster_status["status"] == "withdrawn"
                    else "identity_unverified" if student.get("identity_status") == "identity_unverified"
                    else "unverified" if active is None else "current" if active else "retained",
                "roster_status": roster_status,
                "identity_basis": student.get("basis"),
                "folder_url": student["url"], "latest_record_date": records[0]["date"] if records else None,
                "records": records})
            for item in records:
                base["counts"][item["kind"]] += 1
        base["students"].sort(key=lambda s: (s["section"], s["name"].casefold()))
        base["counts"]["students"] = len(base["students"])
        base.update(status="verified", class_folder_url=_url(course["id"], True), roster_verified=canonical is not None)
        try:
            base["filing"] = _filing(config, class_key)
            base["counts"]["held"] = base["filing"]["held_count"]
            if base["filing"]["held_count"]:
                base["status"] = "held"
        except (Held, Conflict, StoreUnavailable) as error:
            # A missing filing receipt cannot hide folders that independently
            # passed their private parent, artifact and checksum readbacks.
            base.update(status="held", problems=[_problem(error)])
            base["counts"]["held"] = 1
            base["filing"] = {"scans": [], "held_count": 1, "coverage": "unverified"}
        raw = config.get("HUB_TEACHER_REPORTS_COURSE_FOLDERS")
        if raw:
            folders = json.loads(raw)
            ident = folders.get(class_key)
            if ident:
                _private(google.drive_file(token, ident, FIELDS), folder=True)
                base["teacher_reports_url"] = _url(ident, True)
        else:
            matches = [f for f in google.folder_children(token, course["id"])
                       if f.get("mimeType") == google.FOLDER and f.get("name", "").casefold() == "teacher reports"]
            if len(matches) == 1:
                _private(google.drive_file(token, matches[0]["id"], FIELDS), parent=course["id"], folder=True)
                base["teacher_reports_url"] = _url(matches[0]["id"], True)
    except (Held, google.GoogleError, Conflict, StoreUnavailable, ValueError, TypeError) as error:
        base.update(status="held", students=[], problems=[_problem(error)],
                    counts={"students": 0, "work": 0, "feedback": 0, "held": 1})
    return base


def _confirmed_roster_status(student, cls):
    value = student.get("roster_status")
    if (not isinstance(value, dict) or value.get("verified") is not True
            or value.get("basis") != "verified_selected_ic_roster" or value.get("class_key") != cls
            or value.get("section") != student.get("section")
            or value.get("status") not in ("active", "withdrawn")
            or value.get("active") is not (value.get("status") == "active")):
        return None
    if value["status"] == "withdrawn":
        try:
            datetime.strptime(value.get("end_date", ""), "%Y-%m-%d")
        except (ValueError, TypeError):
            return None
    return {k: value.get(k) for k in ("status", "active", "end_date", "school_year", "checked_at", "basis", "verified")}



def review_former_sources(token, config, class_key, source_ids, roster_snapshot):
    """Read exact existing historical bindings; never construct an archive run.

    The fresh, complete Hub roster is also bookended by the review endpoint.
    A past teacher-confirmed withdrawal is not a fresh native IC identity scan.
    """
    from types import SimpleNamespace
    if not enabled(config) or not source_ids:
        return {}
    if (not isinstance(roster_snapshot, tuple) or len(roster_snapshot) != 2
            or not isinstance(roster_snapshot[0], dict) or roster_snapshot[1]
            or not roster_snapshot[0].get(class_key)):
        raise Held("The current complete class roster is unavailable for historical-source review.")
    if (len(source_ids) > MAX_ITEMS or any(not isinstance(fid, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", fid) for fid in source_ids)):
        raise Held("The historical-source review scope is incomplete or invalid.")
    source_ids = sorted(set(source_ids))
    actual_root, _ = _configuration(config)
    root = _scope_root(config, actual_root)
    port = configured_store(config)
    index_key = key("archiveclass", [root, class_key])
    before = _read(port, index_key)
    if not before:
        return {}
    if before.value.get("schema") != SCHEMA:
        raise Held("The private former-student source index could not be verified.")
    historical = before.value.get("historical_sources", {})
    if not isinstance(historical, dict):
        raise Held("The private former-student source index could not be verified.")
    requested = {fid: historical[fid] for fid in source_ids if fid in historical}
    if not requested:
        return {}
    _, course = _location(token, config, class_key)
    # This read-only context has no write token, lease, task, or run constructor.
    reader = SimpleNamespace(token=token, port=port, root=root, cls=class_key,
                             course=course, index_key=index_key)
    active = roster_snapshot[0][class_key]
    configured = _roster(config)
    if configured is not None:
        active = active + [p for p in configured if p["class_key"] == class_key and p.get("active", True)]
    output, documents, source_metadata, copy_metadata = {}, {}, {}, {}
    for fid, binding in requested.items():
        if not isinstance(binding, dict):
            continue
        owner_doc = _read(port, binding.get("student_key", ""))
        copy_doc = _read(port, binding.get("copy_record_key", ""))
        if not owner_doc or not copy_doc:
            continue
        owner = owner_doc.value
        if any(not isinstance(owner.get(k), str) or not owner[k].strip() for k in ("first", "last", "section")):
            continue
        email = str(owner.get("school_email") or "").strip().lower()
        native = str(owner.get("student_id") or owner.get("ic_student_id") or "")
        conflicts = any(
            (work.roster.compact(p.get("first")) == work.roster.compact(owner.get("first"))
             and work.roster.compact(p.get("last")) == work.roster.compact(owner.get("last")))
            or (email and email == str(p.get("school_email") or p.get("email") or "").strip().lower())
            or (native and native == str(p.get("student_id") or p.get("ic_student_id") or ""))
            for p in active)
        if conflicts:
            continue
        source = google.drive_file(token, fid, SOURCE_FIELDS)
        if source.get("id") != fid:
            continue
        try:
            copied = _Run.verify_historical_work(reader, source, binding, {})
        except Held:
            # A changed proof remains an actual source review, never history.
            continue
        copy_meta = google.drive_file(token, copied["drive_file_id"], FIELDS)
        if (copy_meta.get("id") != copied["drive_file_id"] or not copied.get("drive_version")
                or copy_meta.get("version") != copied["drive_version"]):
            continue
        selected = _confirmed_roster_status(owner, class_key)
        if selected and selected["status"] != "withdrawn":
            continue
        documents[fid] = (owner_doc, copy_doc)
        source_metadata[fid], copy_metadata[fid] = source, copy_meta
        output[fid] = {"first": owner["first"], "last": owner["last"], "section": owner["section"],
                       "student_key": owner["student_key"], "copy_record_key": copied["record_key"],
                       "copy_id": copied["drive_file_id"], "student_folder_id": owner["drive_file_id"],
                       "teacher_status": dict(binding["teacher_status"])}
    # Complete verifier reads before taking the final record/metadata bookends.
    for fid in list(output):
        try:
            _Run.verify_historical_work(reader, source_metadata[fid], requested[fid], {})
        except Held:
            del output[fid]
            continue
        owner_doc, copy_doc = documents[fid]
        owner_after = _read(port, owner_doc.value["student_key"])
        copy_after = _read(port, copy_doc.value["record_key"])
        if (not owner_after or not copy_after or owner_after.version != owner_doc.version
                or owner_after.value != owner_doc.value or copy_after.version != copy_doc.version
                or copy_after.value != copy_doc.value):
            del output[fid]
            continue
        source_after = google.drive_file(token, fid, SOURCE_FIELDS)
        copy_after_meta = google.drive_file(token, output[fid]["copy_id"], FIELDS)
        if source_after != source_metadata[fid] or copy_after_meta != copy_metadata[fid]:
            del output[fid]
    after = _read(port, index_key)
    if not after or after.version != before.version or after.value != before.value:
        return {}
    return output

def list_feedback(token, config):
    if not enabled(config):
        return []
    result = []
    for cls in CLASSES:
        course, indexed = _indexed(token, config, cls, feedback_only=True)
        for student, artifacts in indexed:
            result.extend(_feedback_metadata(a, student, course) for a in artifacts if a["kind"] == "feedback")
    return sorted(result, key=lambda a: (a["created_at"], a["name"]), reverse=True)


def lookup_report(token, config, feedback_id):
    return next((item for item in list_feedback(token, config) if item["feedback_id"] == feedback_id), None)


def report_roster(token, config, class_key):
    """Fresh filing-roster names bound to existing archive keys, without exports.

    This selects a teacher report population; it does not certify IC identity.
    Retained student histories are never removed by a roster departure.
    """
    root, _ = _location(token, config, class_key)
    scope = _scope_root(config, root); port = configured_store(config)
    current = _filing_roster(token, config, class_key)
    index = _read(port, key("archiveclass", [scope, class_key]))
    if not index or len(index.value.get("students", {})) > MAX_STUDENTS:
        raise Held("The current class report roster needs its existing student archive index.")
    records = _batch_read(port, list(index.value.get("students", {})))
    result, seen = [], set()
    for person in current:
        matches = [doc.value for ident, doc in records.items() if doc and doc.value.get("status") == "verified"
                   and doc.value.get("schema") == SCHEMA and doc.value.get("student_key") == ident
                   and doc.value.get("drive_file_id") == index.value["students"][ident] and _same_name(doc.value, person)]
        if len(matches) != 1 or matches[0]["student_key"] in seen:
            raise Held("A current class roster name has no single verified archive folder. Preserve its history for review.")
        student = matches[0]; seen.add(student["student_key"])
        result.append({k: student.get(k) for k in ("student_key", "first", "last", "name", "section")})
    return result


INDEX_HEADERS = ("Class", "Section", "Student", "Lesson", "Week", "Work submitted", "Feedback saved",
                 "Work copy", "Original submission", "Feedback", "Student folder", "Status", "Teacher note", "Record key")
INDEX_ROW_LIMIT = 20_000


def _link(url, label):
    if not url:
        return ""
    return '=HYPERLINK("%s","%s")' % (url.replace('"', '""'), label.replace('"', '""'))


def _sheet_date(value):
    try:
        day = datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return ""
    return (day - datetime(1899, 12, 30)).days


def _index_row(class_name, student, submitted, report, status, ident):
    submitted, report = submitted or {}, report or {}
    context = submitted.get("source_context") or report.get("source_context") or {}
    imported = submitted.get("source_kind") == IMPORTED_ORIGINAL
    named_archive = submitted.get("source_kind") == NAMED_ARCHIVE
    original_url = ((submitted.get("import_provenance") or {}).get("original_url") if imported
                    else _url(submitted["source_file_id"]) if submitted.get("source_file_id") else None)
    return [class_name, student.get("section", ""), student["name"], submitted.get("assignment") or report.get("assignment", ""),
            context.get("week", ""), "" if named_archive else _sheet_date(submitted.get("date")), _sheet_date(report.get("date")),
            _link(submitted.get("url") if submitted.get("mime") != SHORTCUT else None, "Open work copy"),
            _link(original_url, "Open original"),
            _link(report.get("url"), "Open feedback"), _link(_url(student["drive_file_id"], True), "Open student folder"),
            status, submitted.get("filing_note", "") if imported or named_archive else "", ident]


def work_index_rows(token, config):
    """All verified histories; pair by source/student/assignment IDs only."""
    rows, summary, holds = [], {}, []
    for cls, class_name in CLASSES.items():
        try:
            _, students = _indexed(token, config, cls, include_history=True)
            copied, feedback_count = 0, 0
            history_count = 0
            categories = {"both": 0, "work_only": 0, "feedback_only": 0, "empty": 0}
            for student, artifacts in students:
                works = [a for a in artifacts if a["kind"] == "work"]
                feedback = [a for a in artifacts if a["kind"] == "feedback"]
                current_feedback = [a for a in feedback if not a.get("supersession_verified")]
                actual = [a for a in works if a.get("mime") != SHORTCUT]
                copied += len(actual); feedback_count += len(current_feedback)
                history_count += len(feedback) - len(current_feedback)
                categories["both" if actual and current_feedback else "work_only" if actual else "feedback_only" if current_feedback else "empty"] += 1
                used = set()
                for report in feedback:
                    if report.get("report_type") == "science_writing" and report.get("source_records"):
                        for source_record in report["source_records"]:
                            imported = source_record.get("source_kind") == IMPORTED_ORIGINAL
                            if imported:
                                candidates = [a for a in works if a.get("source_kind") == IMPORTED_ORIGINAL
                                    and a.get("record_key") == source_record.get("source_record_key")
                                    and a.get("drive_file_id") == source_record.get("source_file_id")
                                    and str((a.get("imported_file_revision") or {}).get("version")) == source_record.get("source_version")
                                    and (a.get("copied_content_proof") or {}).get("sha256") == source_record.get("source_sha256")
                                    and a.get("date") == source_record.get("date")
                                    and a.get("import_provenance") == source_record.get("import_provenance")]
                            else:
                                candidates = [a for a in works if a.get("source_kind") not in {IMPORTED_ORIGINAL, NAMED_ARCHIVE}
                                    and a.get("source_file_id") == source_record.get("source_file_id")
                                    and a.get("source_version") == source_record.get("source_version")]
                            linked = next((a for a in candidates if a.get("mime") != SHORTCUT), next(iter(candidates), None))
                            if linked:
                                used.add(linked["record_key"])
                            status = "Science writing feedback covers this submission" if linked else "Science writing feedback; work not located"
                            if report.get("supersession_verified"):
                                status = "Superseded feedback; previous version retained. " + status
                            # The current imported-work row keeps its original key
                            # and teacher note; historical reports retain separate keys.
                            row_key = (key("workindexrow", [student["student_key"], linked["record_key"]])
                                if imported and linked and not report.get("supersession_verified")
                                else key("workindexrow", [student["student_key"], report["record_key"], source_record.get("source_file_id")]))
                            rows.append(_index_row(class_name, student, linked, report, status, row_key))
                        continue
                    candidates = [a for a in works if a.get("source_kind") not in {IMPORTED_ORIGINAL, NAMED_ARCHIVE}
                        and a.get("source_file_id") == report.get("source_file_id")
                        and a.get("assignment_form_id") and a.get("assignment_form_id") == report.get("assignment_form_id")
                        and a.get("lesson_status") == "verified"
                        and (not report.get("source_version") or a.get("source_version") == report["source_version"])]
                    if report.get("shared_source_key"):
                        candidates = [a for a in works if a.get("shared_source_key") == report["shared_source_key"]
                                      and a.get("teacher_joint_attribution") is True]
                    if len({a.get("source_file_id") for a in candidates}) > 1:
                        candidates = []
                    linked = max(candidates, key=lambda a: (a.get("source_modified_at", ""), a.get("source_version", "")), default=None)
                    if linked:
                        used.add(linked["record_key"])
                    status = "Shared work + feedback" if linked and report.get("shared_source_key") else (
                        "Work + feedback (same original submission)" if linked else "Feedback saved; work not located")
                    source_span = report.get("source_span") or {}
                    pages = source_span.get("source_pdf_pages_1_based") or source_span.get("pages")
                    if pages and not linked:
                        status += "; original report pages " + ", ".join(map(str, pages))
                    selected_status = _confirmed_roster_status(student, cls)
                    if selected_status and selected_status["status"] == "withdrawn":
                        status += "; withdrawn history retained"
                    rows.append(_index_row(class_name, student, linked, report, status,
                        key("workindexrow", [student["student_key"], report["record_key"]])))
                for submitted in works:
                    if submitted["record_key"] in used:
                        continue
                    status = "Work saved; feedback not located" if submitted.get("mime") != SHORTCUT else "Original link only; work copy pending"
                    if submitted.get("source_kind") == IMPORTED_ORIGINAL:
                        status = "Original filed; worksheet feedback pending"
                    if submitted.get("source_kind") == NAMED_ARCHIVE:
                        status = named_archival_work.STATUS
                    elif submitted.get("lesson_status") != "verified":
                        status += "; lesson not confirmed"
                    rows.append(_index_row(class_name, student, submitted, None, status,
                        key("workindexrow", [student["student_key"], submitted["record_key"]])))
                if not artifacts:
                    rows.append(_index_row(class_name, student, None, None, "No verified records filed",
                        key("workindexrow", [student["student_key"], "empty"])))
            summary[cls] = {"student_folders": len(students), "work_copies": copied,
                            "feedback_files": feedback_count, "historical_feedback_files": history_count, **categories}
        except (Held, google.GoogleError, Conflict, StoreUnavailable) as error:
            holds.append({"class_key": cls, "problem": _problem(error)})
            rows.append([class_name, "", "", "", "", "", "", "", "", "", "", "Listing held: " + _problem(error), "",
                         key("workindexrow", [cls, "listing-held"])] )
        filing = _filing(config, cls)
        for scan in filing.get("scans", []):
            for number, held in enumerate(scan.get("held", [])):
                problem = _clean(held.get("problem"), 600)
                holds.append({"class_key": cls, "week": scan.get("week"), **held})
                sources = _held_source_files(held.get("source_files"))
                # The first source row keeps the pre-locator key, protecting
                # a teacher note when an older receipt gains exact source IDs.
                prior_detail = {k: v for k, v in held.items() if k != "source_files"}
                for source_number, source in enumerate(sources or [{}]):
                    filename = source.get("name") or held.get("source_file_names", "")
                    student_label = held.get("name", "") or ("Unconfirmed submission: " + filename if filename else "")
                    status = "Filing held: " + problem + (" Source filename: " + filename if filename else "")
                    row_key = key("workindexrow", [cls, scan.get("week"), number, prior_detail] + (
                        [source["id"]] if source_number else []))
                    rows.append([class_name, held.get("section", ""), student_label, held.get("assignment", ""),
                        scan.get("week", ""), "", "", "", _link(_url(source["id"]), "Open original") if source else "",
                        "", "", status, "", row_key])
            for number, problem in enumerate(scan.get("problems", [])):
                holds.append({"class_key": cls, "week": scan.get("week"), "problem": problem})
                rows.append([class_name, "", "", "", scan.get("week", ""), "", "", "", "", "", "",
                    "Filing held: " + _clean(problem, 600), "", key("workindexrow", [cls, scan.get("week"), "problem", number, problem])])
    if len(rows) > INDEX_ROW_LIMIT:
        raise Held("The work-and-feedback index exceeds its verified row limit.")
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[4], r[3], str(r[5]), str(r[6]), r[13]))
    if len({r[13] for r in rows}) != len(rows):
        raise Held("Two student-record index rows claim the same immutable record.")
    return {"headers": list(INDEX_HEADERS), "rows": rows, "summary": summary, "holds": holds,
            "checked_at": datetime.now(timezone.utc).isoformat(), "coverage": "verified_archive_and_recorded_scans_only"}


def _index_sheet(config):
    value = config.get(WORK_INDEX_CONFIG, "")
    if not value:
        return None
    matched = re.fullmatch(r"https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{10,200})/(?:edit)?(?:\?[^#]*)?(?:#.*)?", value)
    if not matched:
        raise Held("The configured work-and-feedback index needs its verified Google Sheet link.")
    return matched.group(1)


def _index_cell(value, *, link=False):
    if isinstance(value, dict):
        return {"userEnteredValue": value} if value else {}
    if value == "" or value is None:
        return {}
    if isinstance(value, (int, float)):
        return {"userEnteredValue": {"numberValue": value}}
    return {"userEnteredValue": {"formulaValue" if link else "stringValue": str(value)}}


def _index_entered(row):
    values = [c.get("userEnteredValue", {}) for c in row.get("values", [])]
    return values + [{}] * max(0, 14 - len(values))


def _index_records(sheet, ident):
    sheets = [s for s in sheet.get("sheets", []) if s.get("properties", {}).get("title") == "Records"]
    if len(sheets) != 1 or sheet.get("spreadsheetId") != ident:
        raise Held("The index's Records tab needs checking before refresh.")
    return sheets[0]


def _index_layout(cells, generated):
    """Reserve manual rows in place; only keyed rows participate in the index."""
    notes, old_keys, manual = {}, set(), {}
    for number, row in enumerate(cells[1:], 1):
        values = _index_entered(row)
        record_key = values[13].get("stringValue")
        if not record_key:
            if values[13] and values[13] != {"stringValue": ""}:
                raise Held("An index row has an invalid record key. Preserve teacher notes before refreshing.")
            # Formatting alone can be a blank template row. Written values,
            # cell notes and input controls instead reserve the entire row.
            if any(values) or any(c.get("note") or c.get("dataValidation") or c.get("textFormatRuns")
                                  for c in row.get("values", [])):
                manual[number] = deepcopy(row)
            continue
        if record_key in old_keys:
            raise Held("An index row has no unique record key. Preserve teacher notes before refreshing.")
        old_keys.add(record_key)
        if len(row.get("values", [])) > 12 and row["values"][12].get("dataValidation"):
            raise Held("The index's teacher-note field has a new constraint; preserve it for review.")
        notes[record_key] = values[12]
    fresh_keys = {r[13] for r in generated}
    if any(value and record_key not in fresh_keys for record_key, value in notes.items()):
        raise Held("A noted student-record row is not in the fresh verified listing. Its teacher note is preserved.")
    layout = {0: {"values": [_index_cell(v) for v in INDEX_HEADERS]}, **manual}
    position = 1
    for row in generated:
        while position in manual:
            position += 1
        if position > INDEX_ROW_LIMIT:
            raise Held("The work-and-feedback index and retained manual rows exceed its verified row limit.")
        values = [_index_cell(value, link=col in {7, 8, 9, 10} and bool(value)) for col, value in enumerate(row)]
        values[12] = _index_cell(notes.get(row[13], row[12] if row[11] == named_archival_work.STATUS else {}))
        layout[position] = {"values": values}
        position += 1
    prepared = [layout.get(number, {"values": [{}] * 14}) for number in range(max(layout) + 1)]
    return prepared, manual


def refresh_work_index(token, write_token, config):
    """Refresh keyed records, retaining keyed notes and untouched manual rows."""
    claim = cloud = None
    try:
        ident = _index_sheet(config)
        if not ident:
            return {"configured": False, "status": "unconfigured"}
        root, _ = _configuration(config); scope = _scope_root(config, root)
        _private(google.drive_file(token, root, FIELDS), folder=True)
        meta = _private(google.drive_file(token, ident, FIELDS), parent=root)
        if (meta.get("id") != ident or meta.get("mimeType") != "application/vnd.google-apps.spreadsheet"
                or (meta.get("properties") or {}).get("hubStudentWorkIndex") != scope):
            raise Held("The work-and-feedback index is not the verified private student-records Sheet.")
        # Archive reads may be long. Acquire the write lease and read editable
        # notes only after that work, keeping the actual Sheet mutation bounded.
        data = work_index_rows(token, config)
        _private(google.drive_file(token, ident, FIELDS), parent=root)
        port = configured_store(config); cloud = CloudState(port)
        task = cloud.create_task({"workflow": "student-work-index", "class_key": "all-classes",
                                  "assignment_key": key("workindexscope", [scope, ident])})
        request = "work-index-" + uuid.uuid4().hex
        claim = cloud.claim(task, owner="student-work-index", request_id=request, input_hash=content_hash([ident, request]))
        endpoint = "https://sheets.googleapis.com/v4/spreadsheets/" + quote(ident)
        read_params = {"ranges": "Records!A1:N%d" % (INDEX_ROW_LIMIT + 1), "includeGridData": "true",
            "fields": "spreadsheetId,sheets(properties,data(startRow,startColumn,rowData(values(userEnteredValue,note,textFormatRuns,userEnteredFormat,dataValidation))))"}
        current = _index_records(google.get_json(endpoint, token, read_params), ident)
        cells = (current.get("data") or [{}])[0].get("rowData", [])
        if not cells or [c.get("stringValue", "") for c in _index_entered(cells[0])] != list(INDEX_HEADERS):
            raise Held("The index's column headers changed. Existing teacher entries are preserved.")
        prepared, manual = _index_layout(cells, data["rows"])
        needed = max(len(prepared), len(cells))
        normalized = [_index_entered(row) for row in prepared] + [[{}] * 14 for _ in range(needed - len(prepared))]
        existing = [_index_entered(row) for row in cells] + [[{}] * 14 for _ in range(needed - len(cells))]
        changed_rows = [number for number in range(needed) if number not in manual and existing[number] != normalized[number]]
        changed = bool(changed_rows)
        if changed:
            claim = cloud.renew(claim)
            _private(google.drive_file(token, root, FIELDS), folder=True)
            destination = _private(google.drive_file(token, ident, FIELDS), parent=root)
            if (destination.get("id") != ident or destination.get("mimeType") != meta.get("mimeType")
                    or (destination.get("properties") or {}).get("hubStudentWorkIndex") != scope):
                raise Held("The private work-and-feedback index destination changed before refresh.")
            if _index_records(google.get_json(endpoint, token, read_params), ident) != current:
                raise Held("The work-and-feedback index changed during preparation. Existing teacher entries are preserved; refresh from a fresh read.")
            props = current["properties"]; sheet_id = props["sheetId"]
            requests = []
            if props.get("gridProperties", {}).get("rowCount", 0) < needed:
                requests.append({"appendDimension": {"sheetId": sheet_id, "dimension": "ROWS",
                    "length": needed - props["gridProperties"]["rowCount"]}})
            rows = prepared + [{"values": [{}] * 14} for _ in range(needed - len(prepared))]
            # Never send a manual row back in an update, even unchanged.
            # This preserves its rich text, notes, validation and formatting.
            spans = []
            for number in changed_rows:
                if spans and number == spans[-1][1]:
                    spans[-1][1] = number + 1
                else:
                    spans.append([number, number + 1])
            for start, end in spans:
                requests.append({"updateCells": {"range": {"sheetId": sheet_id, "startRowIndex": start, "endRowIndex": end,
                    "startColumnIndex": 0, "endColumnIndex": 14}, "rows": rows[start:end], "fields": "userEnteredValue"}})
            requests.append({"setBasicFilter": {"filter": {"range": {"sheetId": sheet_id, "startRowIndex": 0,
                "endRowIndex": len(prepared), "startColumnIndex": 0, "endColumnIndex": 13}}}})
            try:
                google.get_json(endpoint + ":batchUpdate", write_token, method="POST", body={"requests": requests})
            except google.GoogleError:
                pass  # Exact readback decides; never blind retry a Sheet write.
            fresh = _index_records(google.get_json(endpoint, token, {**read_params, "ranges": "Records!A1:N%d" % needed}), ident)
            actual = (fresh.get("data") or [{}])[0].get("rowData", [])
            observed = [_index_entered(row) for row in actual]
            observed += [[{}] * 14 for _ in range(needed - len(observed))]
            if (fresh["properties"].get("sheetId") != sheet_id or observed != normalized
                    or any(number >= len(actual) or actual[number] != row for number, row in manual.items())):
                raise Held("The work-and-feedback index update was not confirmed. Teacher notes remain protected; no automatic write retry.")
        result = {"configured": True, "status": "held" if data["holds"] else "verified", "url": config[WORK_INDEX_CONFIG],
                  "rows": len(data["rows"]), "retained_manual_rows": len(manual), "summary": data["summary"], "holds": data["holds"],
                  "changed": changed, "checked_at": data["checked_at"], "coverage": data["coverage"]}
        doc_key = key("workindexreceipt", [scope, ident]); _put(port, doc_key, result, _read(port, doc_key))
        cloud.finish(claim, records={"work_index": result})
        return result
    except (Held, google.GoogleError, Conflict, StoreUnavailable) as error:
        result = {"configured": True, "status": "held", "url": config.get(WORK_INDEX_CONFIG), "problem": _problem(error)}
        if cloud and claim:
            try:
                cloud.finish(claim, records={"work_index": result})
            except (Conflict, StoreUnavailable):
                pass
        return result
