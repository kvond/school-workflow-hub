"""Trusted registration of an existing named native archival copy.

This is archival evidence, never an authenticated submission or grade source.
The Root worker supplies the verified canonical identity and copy provenance.
Only private CAS records change; no Drive copy, property, share or grade writes.
"""
import re
from datetime import datetime

KIND = "named_native_original_archive"
MIME = "application/vnd.google-apps.presentation"
STATUS = "Private named archival copy; submission receipt unavailable; grading eligibility not established"
PROVENANCE_FIELDS = {"provider", "original_id", "original_created_at", "canonical_identity_sha256",
                     "copy_reservation_sha256", "private_copy_proof_sha256"}


def provenance(value):
    from . import student_archive as a
    if (not isinstance(value, dict) or set(value) != PROVENANCE_FIELDS
            or value.get("provider") != "named_drive_original_archive"
            or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", value.get("original_id") or "")
            or any(not re.fullmatch(r"[a-f0-9]{64}", value.get(k) or "") for k in
                   ("canonical_identity_sha256", "copy_reservation_sha256", "private_copy_proof_sha256"))):
        raise a.Held("This named archive needs exact original, identity and private-copy evidence.")
    try:
        stamp = datetime.fromisoformat(value["original_created_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
    except (KeyError, ValueError, TypeError, AttributeError):
        raise a.Held("The original file's creation time must remain explicitly separate from submission time.") from None
    return dict(value)


def verify(meta, record, *, scope=None):
    from . import student_archive as a
    p = provenance(record.get("archive_provenance"))
    proof = record.get("copied_content_proof") or {}
    a._private(meta, parent=record.get("parent"))
    if (record.get("source_kind") != KIND or record.get("kind") != "work"
            or record.get("schema") != a.SCHEMA or record.get("status") != "verified"
            or record.get("drive_file_id") == p["original_id"] or meta.get("id") != record.get("drive_file_id")
            or meta.get("mimeType") != MIME or record.get("mime") != MIME
            or meta.get("name") != record.get("name")
            or record.get("source_file_id") != p["original_id"]
            or record.get("source_created_at") != p["original_created_at"]
            or record.get("date") is not None or record.get("date_basis") != "original_file_created_time"
            or record.get("lesson_status") != "archival_only" or record.get("grade_eligible") is not False
            or record.get("identity_status") != "named_archive_destination_verified"
            or any(record.get(k) is not None for k in
                   ("form_id", "assignment_form_id", "submitted_at", "source_response_id"))
            or proof.get("basis") != "native_presentation/v1"
            or not re.fullmatch(r"[a-f0-9]{64}", proof.get("sha256") or "")
            or type(proof.get("slides")) is not int or not 0 < proof["slides"] <= 500
            or type(proof.get("image_instances")) is not int or proof["image_instances"] < 0
            or a._source_revision(meta) != record.get("copied_file_revision")
            or meta.get("properties", {}) != record.get("copied_properties")
            or meta.get("permissions") != record.get("copied_permissions")
            or (meta.get("properties") or {}).get(a.TAG)
            or (scope is not None and record.get("identity_scope_root") != scope)
            or record.get("record_key") != a.key("archivenamedwork", [record.get("identity_scope_root"),
                record.get("class_key"), record.get("student_key"), p["original_id"]])):
        raise a.Held("The named archival copy changed or acquired an unsupported submission or grading claim.")
    return meta


def register(token, config, class_key, student_key, file_id, *, archive_provenance,
             expected_original, expected_metadata, expected_student, expected_content_proof,
             assignment, source_context, filing_note):
    """One CAS attachment of an already copied native file; unknown commit is GET-only.

    The supplied proof is reproduced from both complete native presentations and
    their image content. Full original/copy/private-location metadata bookends
    precede the single version-bound commit. Repeated exact registration reuses
    the same immutable key; a second copy or changed provenance cannot replace it.
    """
    from . import google, student_archive as a
    p = provenance(archive_provenance)
    if (not all(isinstance(v, dict) for v in (expected_original, expected_metadata, expected_student, expected_content_proof))
            or not isinstance(assignment, str) or not assignment or assignment != a._clean(assignment, 500)
            or not isinstance(filing_note, str) or not filing_note or filing_note != a._clean(filing_note, 1500)
            or not isinstance(source_context, dict) or set(source_context) != {"class_key", "school_year"}
            or source_context["class_key"] != class_key
            or not re.fullmatch(r"20[0-9]{2}-[0-9]{2}", source_context.get("school_year", ""))):
        raise a.Held("The named archive needs exact metadata, private destination and archival context.")
    actual_root, course = a._location(token, config, class_key)
    root, port = a._scope_root(config, actual_root), a.configured_store(config)
    index_key = a.key("archiveclass", [root, class_key])
    ident = a.key("archivenamedwork", [root, class_key, student_key, p["original_id"]])
    snapshot = port.read([index_key, student_key, ident])
    index, owner, existing = (snapshot.documents[k] for k in (index_key, student_key, ident))
    student = owner.value if owner else {}
    if (not index or index.value.get("schema") != a.SCHEMA or not owner or student != expected_student
            or student.get("status") != "verified" or student.get("schema") != a.SCHEMA
            or student.get("student_key") != student_key or student.get("record_key") != student_key
            or student.get("class_key", class_key) != class_key
            or index.value.get("students", {}).get(student_key) != student.get("drive_file_id")):
        raise a.Held("The named archive must belong to one unchanged existing indexed canonical student.")
    section = a._private(google.drive_file(token, student["parent"], a.FIELDS), parent=course["id"], folder=True)
    folder = a._private(google.drive_file(token, student["drive_file_id"], a.FIELDS), parent=section["id"], folder=True)
    original = google.drive_file(token, p["original_id"], a.FIELDS)
    meta = google.drive_file(token, file_id, a.FIELDS)
    if (original != expected_original or meta != expected_metadata
            or original.get("id") != p["original_id"] or original.get("mimeType") != MIME
            or original.get("trashed") is not False or original.get("createdTime") != p["original_created_at"]
            or file_id == p["original_id"]):
        raise a.Held("The exact original or private copy changed since its authenticated archival readback.")
    a._private(meta, parent=folder["id"])
    original_content = a._native_presentation_proof(token, p["original_id"])
    copied_content = a._native_presentation_proof(token, file_id)
    if original_content != copied_content or copied_content != expected_content_proof:
        raise a.Held("The complete native archival copy does not preserve the original text, styles and images.")
    record = {"schema": a.SCHEMA, "record_key": ident, "status": "verified", "kind": "work",
        "source_kind": KIND, "identity_scope_root": root, "class_key": class_key, "student_key": student_key,
        "drive_file_id": file_id, "parent": folder["id"], "mime": MIME, "name": meta.get("name"),
        "url": a._url(file_id), "source_file_id": p["original_id"], "source_created_at": p["original_created_at"],
        "date": None, "date_basis": "original_file_created_time", "submitted_at": None,
        "form_id": None, "assignment_form_id": None, "source_response_id": None, "grade_eligible": False,
        "identity_status": "named_archive_destination_verified", "lesson_status": "archival_only",
        "assignment": assignment, "source_context": dict(source_context), "archive_provenance": p,
        "original_file_revision": a._source_revision(original), "copied_file_revision": a._source_revision(meta),
        "copied_properties": meta.get("properties", {}), "copied_permissions": meta.get("permissions"),
        "copied_content_proof": copied_content, "filing_note": filing_note,
        "registered_at": existing.value.get("registered_at") if existing else snapshot.read_time}
    verify(meta, record, scope=root)
    items = dict(student.get("items", {}))
    if len(items) > a.MAX_ITEMS:
        raise a.Held("This student's archive exceeds its verified record limit.")
    attached = a._batch_read(port, list(items))
    if any(doc and doc.value.get("drive_file_id") == file_id and other != ident for other, doc in attached.items()):
        raise a.Held("The private copy is already attached to another archival record.")
    if existing and existing.value != record:
        raise a.Held("This original already has a different immutable named archive; do not duplicate or replace it.")
    expected_item = {"kind": "work", "date": None}
    if ident in items and (not existing or items[ident] != expected_item):
        raise a.Held("This student's named archival attachment needs checking.")
    items[ident] = expected_item
    if len(items) > a.MAX_ITEMS:
        raise a.Held("This student's archive exceeds its verified record limit.")
    def unchanged():
        return (google.drive_file(token, p["original_id"], a.FIELDS) == original
                and google.drive_file(token, file_id, a.FIELDS) == meta
                and google.drive_file(token, student["drive_file_id"], a.FIELDS) == folder
                and google.drive_file(token, student["parent"], a.FIELDS) == section)
    if not unchanged():
        raise a.Held("The original or private destination changed before registration; no CAS write was made.")
    desired = {index_key: index.value, student_key: {**student, "items": items}, ident: record}
    reused = bool(existing and desired[student_key] == student)
    if not reused:
        try:
            port.commit({k: (v, snapshot.documents[k].version if snapshot.documents[k] else None) for k, v in desired.items()})
        except (a.Conflict, a.StoreUnavailable):
            actual = port.read(list(desired)).documents
            if any(not actual[k] or actual[k].value != v for k, v in desired.items()):
                raise
    actual = port.read(list(desired)).documents
    if any(not actual[k] or actual[k].value != v for k, v in desired.items()):
        raise a.StoreUnavailable("named_archival_registration_readback_unconfirmed")
    if not unchanged():
        raise a.Held("The named archive was registered but its source or private destination changed; preserve history for review.")
    return {"status": "verified", "record_key": ident, "drive_file_id": file_id,
            "student_key": student_key, "filed": 0 if reused else 1, "reused": reused,
            "source_kind": KIND, "registered_at": record["registered_at"], "grade_eligible": False,
            "submitted_at": None, "source_created_at": record["source_created_at"]}
