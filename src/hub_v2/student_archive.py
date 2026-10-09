"""Private student records: source shortcuts and immutable saved feedback PDFs.

Original Form uploads, assessments, delivery ticks and sharing never change.
IC identity is preferred. Exact school-email/course evidence or the teacher's
authorized unique-name fallback can file work. Name filing stays explicitly
identity-unverified and never authorizes feedback, grading or an IC write.
The index uses the existing private CAS store.
"""
import hashlib
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from time import monotonic

from . import google, work
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import CloudState, Conflict, StoreUnavailable, key
from school_workflow_hub.core.runs import content_hash

ROOT_CONFIG = "HUB_STUDENT_RECORDS_ROOT_FOLDER"
SCOPE_ROOT_CONFIG = "HUB_STUDENT_RECORDS_IDENTITY_SCOPE_ROOT"
COURSES_CONFIG = "HUB_STUDENT_RECORDS_COURSE_FOLDERS"
ROSTER_CONFIG = "HUB_STUDENT_ARCHIVE_IC_ROSTER"
CLASSES = {"biology": "Biology", "forensics": "Forensics", "anatomy": "A&P"}
SCHEMA = "hub-student-archive/v1"
SHORTCUT = "application/vnd.google-apps.shortcut"
PDF = "application/pdf"
TAG = "hubArchiveKey"
FIELDS = "id,name,mimeType,parents,driveId,trashed,permissions,capabilities,properties,shortcutDetails,createdTime,modifiedTime,version,md5Checksum,size"
# Original uploads need read access, not authority to list their private ACL.
# Keep full access checks on every archive destination and saved artifact.
SOURCE_FIELDS = ",".join(f for f in FIELDS.split(",") if f not in {"permissions", "capabilities", "shortcutDetails"})
MAX_STUDENTS, MAX_ITEMS = 500, 500
MAX_SCANS, MAX_HOLDS = 100, 500
LEASE_HEARTBEAT_SECONDS = 40


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
        self.heartbeat(force=True)
        try:
            self.cloud.finish(self.claim, records={"archive": result})
        except (Conflict, StoreUnavailable) as error:
            self._lease_error = error
            raise

    def create(self, ident, name, parent, mime, *, target=None, pdf=None, attributes=None):
        self.heartbeat()
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
                google.archive_create(self.write_token, metadata, pdf)
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
    source_names = list(dict.fromkeys(_clean(file.get("name"), 160)
        for version in [row] + row.get("earlier", []) for file in version.get("files", [])
        if isinstance(file, dict) and file.get("name")))
    return {k: value for k, value in {"name": name, "section": _clean(row.get("section"), 100),
        "assignment": _clean(assignment, 160), "source_file_names": _clean("; ".join(source_names[:8]), 400)}.items() if value}


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
        held.append({k: _clean(item[k], 400 if k in ("problem", "source_file_names") else 160)
                     for k in ("name", "section", "assignment", "source_file_names", "problem") if item.get(k)})
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
        lessons = list(found.get("lessons", []))
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
            if (lesson.get("problem") and not (lesson.get("calendar_only") is True and not lesson.get("forms")
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
                        date, date_basis, submitted_at = _work_date(version, source)
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
                        artifact = run.create(artifact_key, label,
                            student["drive_file_id"], SHORTCUT, target=file["id"], attributes={
                                "kind": "work", "date": date, "student_key": student["student_key"],
                                "class_key": class_key, "source_file_id": file["id"], "form_id": form.get("form_id"),
                                "source_modified_at": source.get("modifiedTime", ""), "source_version": source.get("version", ""),
                                "submitted_at": submitted_at, "assignment": title, "date_basis": date_basis,
                                "identity_status": version_person["identity_status"], "identity_basis": version_person["basis"],
                                "lesson_status": lesson_status, "original_filename": source.get("name", ""),
                                "source_context": {"week": found["week"], "form_label": form.get("label", ""),
                                                   "source": version.get("source"), "source_created_at": source.get("createdTime"),
                                                   "parents": source.get("parents", []), "via": file.get("via"),
                                                   "named_week": file.get("other_week")}})
                        # Keep observations as immutable index records; the shortcut
                        # remains one per original Drive ID as that file changes.
                        observation = {"source_file_id": file["id"], "modified_at": source.get("modifiedTime", ""),
                                       "version": source.get("version", ""), "submitted_at": submitted_at,
                                       "identity_status": version_person["identity_status"], "lesson_status": lesson_status,
                                       "date_basis": date_basis, "source_context": {"week": found["week"], "form_id": form.get("form_id")}}
                        observation_key = key("archiveobservation", [artifact_key, observation])
                        if not _read(run.port, observation_key):
                            run.put(observation_key, observation)
                        run.attach(student, artifact)
                        filed_ids.add(artifact["drive_file_id"])
                        result["filed"] = len(filed_ids)
            except (Held, google.GoogleError) as error:
                result["held"].append({**context, "problem": str(error)})
        result["problems"].extend(_clean(p, 400) for p in found.get("problems", []) if p)
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
            "source_sha256": artifact.get("source_sha256"), "drive_md5": artifact.get("pdf_md5"),
            "drive_version": artifact.get("drive_version"),
            "source_file_id": artifact.get("source_file_id"), "source_span": artifact.get("source_span"),
            "feedback_origin": artifact.get("feedback_origin", "native_saved"),
            "identity_status": artifact.get("identity_status", "verified"),
            "date": artifact["date"], "mime": PDF, "url": artifact["url"]}


def _batch_read(port, ids):
    result = {}
    for i in range(0, len(ids), 40):
        result.update(port.read(ids[i:i + 40]).documents)
    return result


def _indexed(token, config, cls, *, feedback_only=False):
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
            if (not item or item.value.get("student_key") != student["student_key"]
                    or item.value.get("parent") != student["drive_file_id"]):
                raise Held("An archived record no longer belongs to its indexed student folder.")
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
            meta = _private(metadata[artifact["drive_file_id"]], parent=student["drive_file_id"])
            if (meta.get("properties") or {}).get(TAG) != item_id:
                raise Held("An archived record no longer matches its private index.")
            if artifact["kind"] == "feedback":
                if meta.get("mimeType") != PDF or meta.get("md5Checksum") != artifact.get("pdf_md5"):
                    raise Held("An archived feedback PDF changed and needs review.")
                artifact["drive_version"] = meta.get("version")
            elif (meta.get("mimeType") != SHORTCUT
                  or (meta.get("shortcutDetails") or {}).get("targetId") != artifact["source_file_id"]):
                raise Held("An archived work shortcut no longer points to its verified source.")
            artifacts.append(artifact)
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
                        "original_filename": a.get("original_filename"), "source_context": a.get("source_context")} for a in artifacts]
            records.sort(key=lambda a: (a["date"], a["name"]), reverse=True)
            base["students"].append({"student_key": student["student_key"], "name": student["name"],
                "section": student["section"], "status": "identity_unverified" if student.get("identity_status") == "identity_unverified"
                    else "unverified" if active is None else "current" if active else "retained",
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
        course, indexed = _indexed(token, config, cls, feedback_only=True)
        for student, artifacts in indexed:
            result.extend(_feedback_metadata(a, student, course) for a in artifacts if a["kind"] == "feedback")
    return sorted(result, key=lambda a: (a["created_at"], a["name"]), reverse=True)


def lookup_report(token, config, feedback_id):
    return next((item for item in list_feedback(token, config) if item["feedback_id"] == feedback_id), None)
