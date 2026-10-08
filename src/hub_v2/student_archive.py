"""Private student records: source shortcuts and immutable saved feedback PDFs.

Original Form uploads, assessments, delivery ticks and sharing never change.
IC identity is preferred. An exact, unique school-email/course match is a
filing-only fallback when the native number is unavailable; names alone never
authorize filing or an IC write. The index uses the existing private CAS store.
"""
import hashlib
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import google, work
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import CloudState, Conflict, StoreUnavailable, key
from school_workflow_hub.core.runs import content_hash

ROOT_CONFIG = "HUB_STUDENT_RECORDS_ROOT_FOLDER"
COURSES_CONFIG = "HUB_STUDENT_RECORDS_COURSE_FOLDERS"
ROSTER_CONFIG = "HUB_STUDENT_ARCHIVE_IC_ROSTER"
CLASSES = {"biology": "Biology", "forensics": "Forensics", "anatomy": "A&P"}
SCHEMA = "hub-student-archive/v1"
SHORTCUT = "application/vnd.google-apps.shortcut"
PDF = "application/pdf"
TAG = "hubArchiveKey"
FIELDS = "id,name,mimeType,parents,trashed,permissions,capabilities,properties,shortcutDetails,createdTime,modifiedTime,version,md5Checksum,size"
MAX_STUDENTS, MAX_ITEMS = 500, 500
MAX_SCANS, MAX_HOLDS = 100, 500


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
        return root, courses
    except (ValueError, TypeError):
        raise Held("Student records are not connected to their private class folders yet.") from None


def enabled(config):
    try:
        _configuration(config)
        return True
    except Held:
        return False


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
    course = _private(google.drive_file(token, courses[cls], FIELDS), parent=root, folder=True, write=write)
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
            "student_id": native if verified_native else "", "school_email": email if trusted_email else "",
            "first": first, "last": last, "section": section, "name": last + ", " + first}


class _Run:
    def __init__(self, token, write_token, config, cls):
        self.token, self.write_token, self.config, self.cls = token, write_token, config, cls
        self.root, self.course = _location(token, config, cls, write=True)
        self.port = configured_store(config)
        self.cloud = CloudState(self.port)
        task = self.cloud.create_task({"workflow": "student-archive", "class_key": cls,
                                      "assignment_key": key("archivescope", self.root)})
        request = "archive-" + uuid.uuid4().hex
        self.claim = self.cloud.claim(task, owner="student-archive", request_id=request,
                                      input_hash=content_hash([self.root, cls, request]))
        self.index_key = key("archiveclass", [self.root, cls])

    def renew(self):
        self.claim = self.cloud.renew(self.claim)

    def finish(self, result):
        self.cloud.finish(self.claim, records={"archive": result})

    def create(self, ident, name, parent, mime, *, target=None, pdf=None, attributes=None):
        old = _read(self.port, ident)
        expected = {"name": name, "parent": parent, "mime": mime, "target": target}
        if old:
            record = old.value
            if any(record.get(k) != v for k, v in expected.items() if k != "name"):
                raise Held("An archive record already belongs to a different source or student. It needs review.")
            # A repeated Drive upload can appear in several Form revisions.
            # Preserve every observation below, while its one shortcut keeps
            # the latest confirmed submission label instead of an older date.
            if (mime == SHORTCUT and attributes
                    and work.when(record.get("submitted_at"))
                    and work.when(attributes.get("submitted_at"))
                    and work.when(record["submitted_at"]) > work.when(attributes["submitted_at"])):
                name = record["name"]
                attributes = {k: record.get(k) for k in attributes}
        else:
            existing = [item for item in google.folder_children(self.token, parent)
                        if (item.get("properties") or {}).get(TAG) == ident
                        or (mime == google.FOLDER and item.get("mimeType") == google.FOLDER
                            and item.get("name") == name and not (item.get("properties") or {}).get(TAG))]
            if existing:
                raise Held("An existing archive folder or record has no confirmed index. Preserve it for identity review.")
            self.renew()
            record = {"schema": SCHEMA, "record_key": ident,
                      "drive_file_id": google.archive_file_id(self.write_token, shortcut=mime == SHORTCUT),
                      "status": "reserved", **expected, **(attributes or {})}
            if pdf is not None:
                record.update(pdf_sha256=hashlib.sha256(pdf).hexdigest(), pdf_md5=hashlib.md5(pdf).hexdigest(), size=len(pdf))
            old = _put(self.port, ident, record)
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
            old = _put(self.port, ident, record, old)
            metadata = {"id": file_id, "name": name, "mimeType": mime, "parents": [parent], "properties": {TAG: ident}}
            if target:
                metadata["shortcutDetails"] = {"targetId": target}
            creation_error = None
            try:
                google.archive_create(self.write_token, metadata, pdf)
            except google.GoogleError as error:
                creation_error = str(error)  # Helpers only emit bounded, redacted explanations.
            try:
                meta = google.drive_file(self.token, file_id, FIELDS)
            except google.GoogleError:
                raise Held(creation_error or "Drive did not confirm the reserved archive file. Its creation is held for review.") from None
        _private(meta, parent=parent, folder=mime == google.FOLDER)
        if (meta.get("mimeType") != mime or (meta.get("properties") or {}).get(TAG) != ident
                or (target and (meta.get("shortcutDetails") or {}).get("targetId") != target)):
            raise Held("The archive file's source, type or identity did not read back correctly.")
        if mime == PDF and (meta.get("md5Checksum") != record.get("pdf_md5")
                           or int(meta.get("size", -1)) != record.get("size")):
            raise Held("This saved feedback PDF changed after filing. The original report needs checking.")
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
                      url=_url(file_id, mime == google.FOLDER))
        if mime == SHORTCUT:
            record.update(attributes or {})
        return _put(self.port, ident, record, old).value

    def student(self, person):
        section_key = key("archivesection", [self.root, self.cls, person["section"]])
        section = self.create(section_key, person["section"], self.course["id"], google.FOLDER)
        student_key = key("archivestudent", [self.root, self.cls, person["identity"]])
        prior = _read(self.port, student_key)
        if prior and prior.value.get("alias_of"):
            student_key = prior.value["alias_of"]
            prior = _read(self.port, student_key)
        # Link a newly verified native ID to its existing email-owned folder.
        if person["student_id"] and person["school_email"]:
            email_key = key("archivestudent", [self.root, self.cls, "email:" + person["school_email"]])
            email_doc = _read(self.port, email_key)
            if email_doc and not prior:
                _put(self.port, student_key, {"alias_of": email_key})
                student_key, prior = email_key, email_doc
            elif email_doc and prior and email_doc.value.get("drive_file_id") != prior.value.get("drive_file_id"):
                raise Held("Two archive folders claim this student identity. Preserve both for review.")
        if prior and prior.value.get("student_id") and person["student_id"] \
                and prior.value["student_id"] != person["student_id"]:
            raise Held("This archive has a different verified IC identity. It needs review.")
        if prior:
            person = dict(person)
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
            prior = _put(self.port, student_key, {**prior.value, "parent": section["drive_file_id"]}, prior)
        student = self.create(student_key, person["name"], section["drive_file_id"], google.FOLDER,
                              attributes={**person, "items": {}})
        doc = _read(self.port, student_key)
        student = _put(self.port, student_key, {**student, **person, "student_key": student_key}, doc).value
        index = _read(self.port, self.index_key)
        values = dict((index.value if index else {}).get("students", {}))
        values[student_key] = student["drive_file_id"]
        if len(values) > MAX_STUDENTS:
            raise Held("The student archive exceeds its verified class size limit.")
        _put(self.port, self.index_key, {**(index.value if index else {}), "schema": SCHEMA, "students": values}, index)
        return student

    def filing_receipt(self, found, result):
        week = _scan_scope(self.cls, found)
        receipt = _filing_receipt(self.cls, week, result)
        ident = key("archivescan", [self.root, self.cls, week])
        self.renew()
        _put(self.port, ident, receipt, _read(self.port, ident))
        index = _read(self.port, self.index_key)
        scans = dict((index.value if index else {}).get("filing_scans", {}))
        scans[week] = ident
        if len(scans) > MAX_SCANS:
            raise Held("The archive's recorded week coverage needs its size checked.")
        _put(self.port, self.index_key, {**(index.value if index else {}), "schema": SCHEMA,
                                       "filing_scans": scans}, index)
        return receipt

    def attach(self, student, artifact):
        ident = student["student_key"]
        doc = _read(self.port, ident)
        items = dict(doc.value.get("items", {}))
        items[artifact["record_key"]] = {"kind": artifact["kind"], "date": artifact["date"]}
        if len(items) > MAX_ITEMS:
            raise Held("This student's archive exceeds its verified record limit.")
        _put(self.port, ident, {**doc.value, "items": items}, doc)


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


def _hold_context(row, assignment=""):
    first, last = _clean(row.get("first"), 100), _clean(row.get("last"), 100)
    name = last + ", " + first if first and last else last or first or _clean(row.get("who"), 100)
    return {k: value for k, value in {"name": name, "section": _clean(row.get("section"), 100),
                                    "assignment": _clean(assignment, 160)}.items() if value}


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
        held.append({k: _clean(item[k], 400 if k == "problem" else 160)
                     for k in ("name", "section", "assignment", "problem") if item.get(k)})
    problems = [_clean(p, 400) for p in raw_problems[:MAX_HOLDS] if p]
    held_count = len(raw_held) + len([p for p in raw_problems if p])
    if result.get("status") != "verified" and not held_count:
        problems.append("This filing scan did not finish with a confirmed receipt. Its source work is preserved.")
        held_count = 1
    truncated = len(raw_held) > MAX_HOLDS or len(raw_problems) > MAX_HOLDS
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
        candidates, emails, conflicts = [], {}, set()
        for lesson in found.get("lessons", []):
            if lesson.get("problem"):
                result["held"].append({"assignment": _clean(lesson.get("topic")), "problem": lesson["problem"]})
            for form in lesson.get("forms", []):
                assignment = form.get("label") or lesson.get("topic") or ""
                if form.get("problem"):
                    result["held"].append({"assignment": form.get("label", ""), "problem": form["problem"]})
                    continue
                for row in form.get("students", []) + form.get("late", []):
                    if not row.get("matched"):
                        if row.get("files") or any(v.get("files") for v in row.get("earlier", [])):
                            result["held"].append({**_hold_context(row, assignment),
                                "problem": "This submission is not matched to a verified roster student. Its source files are preserved."})
                        continue
                    try:
                        person = _identity(config, class_key, row)
                        previous = emails.setdefault(person["school_email"] or person["identity"], person)
                        if any(previous[k] != person[k] for k in ("first", "last", "section")):
                            conflicts.add(person["school_email"] or person["identity"])
                            raise Held("The school email matches more than one roster identity. Keep its files for review.")
                        candidates.append((lesson, form, row, person))
                    except Held as error:
                        result["held"].append({**_hold_context(row, assignment), "problem": str(error)})
                for row in form.get("elsewhere", []):
                    if "match not confirmed" in str(row.get("to", "")).casefold():
                        result["held"].append({**_hold_context(row, assignment),
                            "problem": "This upload came through another lesson's Form, and its lesson match is not confirmed. The original remains there."})
        # Duplicate roster identities must hold all their submissions, including
        # the first candidate encountered, rather than creating a first-wins folder.
        filed_ids = set()
        for lesson, form, row, person in candidates:
            context = _hold_context(row, form.get("label") or lesson.get("topic") or "")
            if (person["school_email"] or person["identity"]) in conflicts:
                continue
            try:
                student = run.student(person)
                for version in [row] + row.get("earlier", []):
                    if version.get("source") != "form":
                        result["held"].append({**context, "problem": "Earlier folder-only work needs its school-email identity verified."})
                        continue
                    if version is not row:
                        # Each earlier Form version must carry its own trusted
                        # email. Current-row IC identity is never evidence for
                        # an earlier upload that only matched a filename.
                        evidence = {**row, **version, "email": version.get("email", ""),
                                    "school_email": version.get("school_email") or version.get("email", ""),
                                    "matched_by": version.get("matched_by", ""),
                                    "ic_identity_verified": False, "ic_student_id": "", "student_id": ""}
                        prior_person = _identity(config, class_key, evidence)
                        if (not prior_person["school_email"]
                                or prior_person["school_email"] != person["school_email"]):
                            raise Held("An earlier Form upload does not match this student's verified identity. Preserve it for review.")
                    date = _date(version.get("submitted_at", ""))
                    for file in version.get("files", []):
                        if file.get("other_week") or file.get("trashed") or file.get("problem"):
                            result["held"].append({**context, "problem": "This source file's lesson or availability needs checking."})
                            continue
                        source = google.drive_file(token, file["id"], FIELDS)
                        if source.get("trashed") is not False or source.get("mimeType") == SHORTCUT:
                            raise Held("A source upload is unavailable or is itself a shortcut. Leave it for review.")
                        artifact_key = key("archivework", [run.root, class_key, file["id"]])
                        old = _read(run.port, artifact_key)
                        if old and old.value.get("student_key") != student["student_key"]:
                            raise Held("This source file is already indexed for a different student. Keep it for review.")
                        title = work.assignment_title(form.get("label", ""))
                        artifact = run.create(artifact_key, date + " — " + _clean(title) + " — Work",
                            student["drive_file_id"], SHORTCUT, target=file["id"], attributes={
                                "kind": "work", "date": date, "student_key": student["student_key"],
                                "class_key": class_key, "source_file_id": file["id"], "form_id": form.get("form_id"),
                                "source_modified_at": source.get("modifiedTime", ""), "source_version": source.get("version", ""),
                                "submitted_at": version.get("submitted_at"), "assignment": title})
                        # Keep observations as immutable index records; the shortcut
                        # remains one per original Drive ID as that file changes.
                        observation = {"source_file_id": file["id"], "modified_at": source.get("modifiedTime", ""),
                                       "version": source.get("version", ""), "submitted_at": version.get("submitted_at")}
                        observation_key = key("archiveobservation", [artifact_key, observation])
                        if not _read(run.port, observation_key):
                            _put(run.port, observation_key, observation)
                        run.attach(student, artifact)
                        filed_ids.add(artifact["drive_file_id"])
                        result["filed"] = len(filed_ids)
            except (Held, google.GoogleError) as error:
                result["held"].append({**context, "problem": str(error)})
        for row in found.get("other_files", []):
            result["held"].append({**_hold_context(row),
                "problem": "This submission's lesson match is not confirmed. Its files remain in their source folders."})
        result["problems"].extend(_clean(p, 400) for p in found.get("problems", []) if p)
        if found.get("folder_note"):
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
                "source_sha256": (saved_value.get("source") or {}).get("sha256"), "assignment": title})
        run.attach(student, artifact)
        value = _feedback_metadata(artifact, student, run.course)
        run.finish({"status": "verified", "feedback_id": saved_value["id"], "drive_file_id": artifact["drive_file_id"]})
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
            "class_folder_id": course["id"], "class_folder_name": course.get("name", ""),
            "created_at": artifact["created_at"], "saved_at": artifact["saved_at"],
            "date": artifact["date"], "mime": PDF, "url": artifact["url"]}


def _batch_read(port, ids):
    result = {}
    for i in range(0, len(ids), 40):
        result.update(port.read(ids[i:i + 40]).documents)
    return result


def _indexed(token, config, cls):
    root, course = _location(token, config, cls)
    port = configured_store(config)
    index = _read(port, key("archiveclass", [root, cls]))
    ids = list((index.value if index else {}).get("students", {}))
    if len(ids) > MAX_STUDENTS:
        raise Held("The student archive index needs its size checked.")
    students = _batch_read(port, ids)
    checked, item_ids, metadata_ids = [], [], set()
    for ident in ids:
        doc = students.get(ident)
        if not doc or doc.value.get("status") != "verified":
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
    for item in documents.values():
        if not item or item.value.get("status") != "verified":
            raise Held("An archived record has not been verified yet.")
        metadata_ids.add(item.value["drive_file_id"])
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
            artifact = item.value
            meta = _private(metadata[artifact["drive_file_id"]], parent=student["drive_file_id"])
            if (meta.get("properties") or {}).get(TAG) != item_id:
                raise Held("An archived record no longer matches its private index.")
            if artifact["kind"] == "feedback":
                if meta.get("mimeType") != PDF or meta.get("md5Checksum") != artifact.get("pdf_md5"):
                    raise Held("An archived feedback PDF changed and needs review.")
            elif (meta.get("mimeType") != SHORTCUT
                  or (meta.get("shortcutDetails") or {}).get("targetId") != artifact["source_file_id"]):
                raise Held("An archived work shortcut no longer points to its verified source.")
            artifacts.append(artifact)
        output.append((student, artifacts))
    return course, output


def _filing(config, cls):
    root, _ = _configuration(config)
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
                        "feedback_id": a.get("feedback_id")} for a in artifacts]
            records.sort(key=lambda a: (a["date"], a["name"]), reverse=True)
            base["students"].append({"student_key": student["student_key"], "name": student["name"],
                "section": student["section"], "status": "unverified" if active is None else "current" if active else "retained",
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


def list_feedback(token, config):
    if not enabled(config):
        return []
    result = []
    for cls in CLASSES:
        course, indexed = _indexed(token, config, cls)
        for student, artifacts in indexed:
            result.extend(_feedback_metadata(a, student, course) for a in artifacts if a["kind"] == "feedback")
    return sorted(result, key=lambda a: (a["created_at"], a["name"]), reverse=True)


def lookup_report(token, config, feedback_id):
    return next((item for item in list_feedback(token, config) if item["feedback_id"] == feedback_id), None)
