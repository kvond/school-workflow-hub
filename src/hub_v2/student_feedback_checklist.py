"""One private, stable feedback-created checklist per existing student folder.

This is an archive derivative, never a delivery record. All report versions stay
listed; source PDFs, teacher marks and feedback generation are untouched.
"""
from datetime import datetime, timezone
import hashlib
from html import escape
from io import BytesIO
from urllib.parse import quote

from . import google, student_archive as archive
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, key
from school_workflow_hub.core.runs import content_hash

NAME = "00 — Feedback created checklist.pdf"
SCHEMA = "student-feedback-checklist/v1"
MAX_STUDENTS = 25


def _snapshot(run, student_key):
    index = archive._read(run.port, run.index_key)
    owner = archive._read(run.port, student_key)
    if (not index or index.value.get("schema") != archive.SCHEMA or not owner
            or owner.value.get("schema") != archive.SCHEMA or owner.value.get("status") != "verified"
            or owner.value.get("record_key") != student_key or owner.value.get("student_key") != student_key
            or index.value.get("students", {}).get(student_key) != owner.value.get("drive_file_id")):
        raise archive.Held("This checklist needs one unchanged, existing indexed student folder.")
    student = owner.value
    items = student.get("items")
    if not isinstance(items, dict) or len(items) > archive.MAX_ITEMS:
        raise archive.Held("This student's archive listing is incomplete or exceeds its verified limit.")
    documents = archive._batch_read(run.port, list(items))
    metadata = {}
    for ident, parent in ((student["parent"], run.course["id"]), (student["drive_file_id"], student["parent"])):
        metadata[ident] = archive._private(google.drive_file(run.token, ident, archive.FIELDS), parent=parent, folder=True, write=ident == student["drive_file_id"])
    if (metadata[student["drive_file_id"]].get("properties") or {}).get(archive.TAG) != student_key:
        raise archive.Held("The checklist's student folder no longer matches its private archive identity.")
    children = google.folder_children(run.token, student["drive_file_id"])
    if any(str((item.get("properties") or {}).get(archive.TAG, "")).startswith("archivefeedback_")
           and (item.get("properties") or {})[archive.TAG] not in items for item in children):
        raise archive.Held("An unattached feedback artifact needs archive verification before refreshing the checklist.")
    rows, report_ids = [], set()
    for ident in sorted(items):
        run.heartbeat()
        doc = documents.get(ident)
        if (not doc or doc.value.get("schema") != archive.SCHEMA or doc.value.get("status") != "verified"
                or doc.value.get("record_key") != ident or doc.value.get("student_key") != student_key
                or doc.value.get("class_key") != run.cls or doc.value.get("kind") not in {"work", "feedback"}):
            raise archive.Held("The complete student archive could not be verified. Keep the previous checklist.")
        artifact = doc.value
        if artifact["kind"] != "feedback":
            continue
        report_id = artifact.get("feedback_id")
        if not report_id or ident != key("archivefeedback", [run.root, report_id]) or report_id in report_ids:
            raise archive.Held("A feedback version has no unique, verified archive identity.")
        report_ids.add(report_id)
        meta = archive._feedback_file(run.token, run.root, run.course, student, artifact)
        metadata[meta["id"]] = meta
        if not archive._feedback_replacement_ready(run.token, run.root, run.course, student, artifact, documents):
            raise archive.Held("A feedback replacement is not fully verified. Keep the previous checklist.")
        if artifact.get("superseded_by"):
            archive._feedback_successor(run.token, run.root, run.course, student, artifact, documents)
        version = artifact.get("presentation_version", 1)
        if type(version) is not int or version < 1:
            raise archive.Held("A saved feedback version needs verification before listing it.")
        report_type = artifact.get("report_type") or ("critical_aspects"
            if artifact.get("feedback_origin", "native_saved") == "native_saved" else "feedback")
        label = {"critical_aspects": "Critical aspects", "science_writing": "Science writing"}.get(
            report_type, str(report_type).replace("_", " ").capitalize())
        lessons = [source.get("assignment") for source in (artifact.get("source_records") or []) if source.get("assignment")]
        rows.append({"report_id": report_id, "record_key": ident, "made": True,
            "saved_at": artifact.get("saved_at"), "date": archive._date(artifact.get("saved_at")),
            "lesson": "; ".join(dict.fromkeys(lessons)) or artifact.get("assignment") or "Lesson not recorded",
            "type": report_type, "type_label": label, "version": version,
            "historical": bool(artifact.get("superseded_by")), "url": archive._url(artifact["drive_file_id"]),
            "pdf_sha256": artifact.get("pdf_sha256")})
    # Bookend the full class membership and selected student's complete index.
    baseline = {run.index_key: index, student_key: owner, **documents}
    fresh = archive._batch_read(run.port, list(baseline))
    if any(fresh.get(ident) != doc for ident, doc in baseline.items()):
        raise archive.Held("The archive changed during checklist discovery. Keep the previous checklist.")
    for ident, meta in metadata.items():
        if google.drive_file(run.token, ident, archive.FIELDS) != meta:
            raise archive.Held("A report or private folder changed during checklist discovery.")
    rows.sort(key=lambda row: (row["saved_at"], row["type"], row["report_id"]))
    # The derivative's pointer is not part of its source revision.
    source_student = {k: v for k, v in student.items() if k != "feedback_checklist"}
    proof = content_hash({"student": source_student, "documents": {k: d.value for k, d in documents.items()},
                          "membership": index.value.get("students", {}), "metadata": metadata})
    return student, rows, proof


def render(student, rows):
    """Large readable rows and clickable report links; deterministic PDF bytes."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether
    from reportlab.pdfgen.canvas import Canvas
    out = BytesIO()
    style = ParagraphStyle("checklist", fontName="Helvetica", fontSize=13, leading=19, spaceAfter=9)
    heading = ParagraphStyle("heading", parent=style, fontName="Helvetica-Bold", fontSize=19, leading=24)
    story = [Paragraph("Feedback created", heading), Paragraph(escape(student["name"]), style),
             Paragraph("A checked item means the report was made and its saved file was verified. "
                       "It does not mean printed, sent or handed to the student.", style), Spacer(1, 10)]
    for row in rows:
        label = "[x] %s — %s — version %s%s" % (row["date"], row["type_label"], row["version"],
                                              " (previous version)" if row["historical"] else "")
        story.append(KeepTogether([Paragraph(escape(label), style), Paragraph(escape(row["lesson"]), style),
            Paragraph('<link href="%s" color="#1457a6">Open saved feedback</link>' % escape(row["url"], quote=True), style),
            Spacer(1, 12)]))
    if not rows:
        story.append(Paragraph("No verified feedback reports are indexed for this student yet.", style))
    document = SimpleDocTemplate(out, pagesize=(612, 792), rightMargin=45, leftMargin=45, topMargin=42, bottomMargin=42)
    document.build(story, canvasmaker=lambda *args, **kwargs: Canvas(*args, **{**kwargs, "invariant": 1}))
    return out.getvalue()


def _pdf(run, record):
    meta = archive._private(google.drive_file(run.token, record["drive_file_id"], archive.FIELDS), parent=record["parent"])
    if (meta.get("mimeType") != archive.PDF or (meta.get("properties") or {}).get(archive.TAG) != record["record_key"]
            or meta.get("name") != NAME):
        raise archive.Held("The existing feedback checklist changed identity or location. Preserve it for review.")
    raw = archive._work_bytes(run.token, meta, run.heartbeat)
    return meta, raw


def _conditional_update(run, meta, raw):
    """One conditional media PATCH; callers fence it durably before this call."""
    endpoint = "https://www.googleapis.com/drive/v3/files/" + quote(meta["id"])
    try:
        response = google._session().get(endpoint, params={"fields": archive.FIELDS, "supportsAllDrives": "true"},
            headers={"Authorization": "Bearer " + run.token}, timeout=(5, 20), allow_redirects=False)
        if response.status_code != 200 or response.json() != meta or not response.headers.get("ETag"):
            raise archive.Held("The checklist changed or its conditional-write revision is unavailable.")
        run.heartbeat(force=True)
        response = google._session().patch("https://www.googleapis.com/upload/drive/v3/files/" + quote(meta["id"]),
            params={"uploadType": "media", "supportsAllDrives": "true", "keepRevisionForever": "true"},
            headers={"Authorization": "Bearer " + run.write_token, "If-Match": response.headers["ETag"],
                     "Content-Type": archive.PDF}, data=raw, timeout=(5, 30), allow_redirects=False)
        # Even a lost/failed reply is decided only by the exact readback below.
        return response.status_code
    except archive.Held:
        raise
    except Exception:
        return None


def _publish(run, student_key):
    student, rows, proof = _snapshot(run, student_key)
    ident = key("archivefeedbackchecklist", [run.root, student_key])
    old = archive._read(run.port, ident)
    raw = render(student, rows)
    digest = hashlib.sha256(raw).hexdigest()
    desired = {"rows": rows, "source_proof": proof, "sha256": digest}
    if not old and any(item.get("name") == NAME for item in google.folder_children(run.token, student["drive_file_id"])):
        raise archive.Held("An existing checklist has no verified index. Preserve it; do not create a duplicate.")
    if old and (old.value.get("student_key") != student_key or old.value.get("parent") != student["drive_file_id"]):
        raise archive.Held("This checklist reservation belongs to a different student folder.")
    prior_rows = (old.value.get("confirmed") or {}).get("rows", []) if old else []
    if not {row["report_id"] for row in prior_rows}.issubset({row["report_id"] for row in rows}):
        raise archive.Held("The fresh listing omits previously verified feedback. Keep the complete previous checklist.")
    if _snapshot(run, student_key) != (student, rows, proof):
        raise archive.Held("The source archive changed before checklist publication.")
    changed = not old or (old.value.get("confirmed") or {}).get("sha256") != digest
    if not old or old.value.get("status") in {"reserved", "creating"}:
        if old and old.value.get("pdf_sha256") != digest:
            raise archive.Held("A previous checklist creation is unresolved; do not create or retry a replacement.")
        record = run.create(ident, NAME, student["drive_file_id"], archive.PDF, pdf=raw,
            attributes={"kind": "feedback_checklist", "student_key": student_key, "class_key": run.cls, "pending": desired})
        old = archive._read(run.port, ident)
    else:
        record = old.value
    meta, observed = _pdf(run, record)
    pending = record.get("pending")
    if pending:
        if pending != desired or hashlib.sha256(observed).hexdigest() != pending["sha256"]:
            raise archive.Held("The checklist's earlier update is uncertain. Inspect its exact saved contents; no automatic write retry.")
    elif observed != raw:
        if (hashlib.sha256(observed).hexdigest() != record.get("pdf_sha256")
                or (record.get("confirmed") or {}).get("sha256") != record.get("pdf_sha256")):
            raise archive.Held("The saved checklist differs from its last verified contents. Preserve the edit for review.")
        run.heartbeat(force=True)
        record = {**record, "pending": desired}
        old = run.put(ident, record, old)
        try:
            _conditional_update(run, meta, raw)
        except archive.Held:
            # This typed refusal occurs before PATCH. It is not an uncertain
            # provider write, so preserve the prior verified state unchanged.
            run.put(ident, {**record, "pending": None}, old)
            raise
        meta, observed = _pdf(run, record)
    if observed != raw or meta.get("md5Checksum") != hashlib.md5(raw).hexdigest() or str(meta.get("size")) != str(len(raw)):
        raise archive.Held("The feedback checklist's exact content did not read back. No automatic write retry.")
    if _snapshot(run, student_key) != (student, rows, proof):
        raise archive.Held("The source archive changed during checklist publication. The previous verified history is retained.")
    # Every verified content version has an immutable private record. The Drive
    # file ID remains stable, and media updates retain old Drive revisions.
    history_key = key("archivechecklistversion", [ident, digest, proof])
    history = archive._read(run.port, history_key)
    history_value = {"schema": SCHEMA, "student_key": student_key, "drive_file_id": record["drive_file_id"], **desired}
    if history and history.value != history_value:
        raise archive.Held("The checklist history does not match its saved content version.")
    if not history:
        run.put(history_key, history_value)
    record = {**record, "confirmed": desired, "pending": None, "pdf_sha256": digest,
        "pdf_md5": hashlib.md5(raw).hexdigest(), "size": len(raw), "drive_version": meta.get("version"),
        "status": "verified", "checked_at": datetime.now(timezone.utc).isoformat()}
    run.put(ident, record, old)
    receipt = {"status": "verified", "record_key": ident, "drive_file_id": record["drive_file_id"],
        "url": archive._url(record["drive_file_id"]), "reports": len(rows), "changed": changed,
        "source_proof": proof, "sha256": digest, "checked_at": record["checked_at"]}
    owner = archive._read(run.port, student_key)
    if {k: v for k, v in owner.value.items() if k != "feedback_checklist"} != {k: v for k, v in student.items() if k != "feedback_checklist"}:
        raise archive.Held("The student's archive changed before its checklist pointer was saved.")
    run.put(student_key, {**owner.value, "feedback_checklist": receipt}, owner)
    return receipt


def refresh_run(run, student_keys):
    """Refresh exact existing students under the caller's current archive lease."""
    keys = list(dict.fromkeys(student_keys))
    result = {"status": "verified", "checked": 0, "updated": 0, "unchanged": 0, "held": [], "checklists": []}
    if len(keys) > MAX_STUDENTS:
        return {**result, "status": "held", "problem": "Select at most 25 existing student folders per checklist batch."}
    for student_key in keys:
        try:
            receipt = _publish(run, student_key)
            result["checked"] += 1
            result["updated" if receipt["changed"] else "unchanged"] += 1
            result["checklists"].append({"student_key": student_key, **receipt})
        except (archive.Held, google.GoogleError, Conflict, StoreUnavailable) as error:
            result["held"].append({"student_key": student_key, "problem": archive._problem(error)})
            result["status"] = "held"
            # A lost lease blocks all further writes, including other students.
            if isinstance(error, (Conflict, StoreUnavailable)):
                break
    return result


def refresh(token, write_token, config, class_key, *, student_keys=None):
    """Bounded backfill; exact keys or one complete class of at most 25 students.

    Existing trusted runtime entrypoint only: no generation, email, roster or
    grade write, and no folder creation. Larger classes use explicit key batches.
    """
    run = None
    try:
        if not archive.enabled(config):
            return {"status": "unconfigured", "configured": False}
        run = archive._Run(token, write_token, config, class_key)
        index = archive._read(run.port, run.index_key)
        if not index or index.value.get("schema") != archive.SCHEMA:
            raise archive.Held("The existing class archive index is unavailable; no checklist coverage is confirmed.")
        keys = list(index.value.get("students", {})) if student_keys is None else student_keys
        if not isinstance(keys, (list, tuple)) or any(not isinstance(k, str) for k in keys):
            raise archive.Held("Choose exact existing student archive keys for this bounded backfill.")
        result = refresh_run(run, keys)
        run.finish(result)
        return {"class_key": class_key, "configured": True, **result}
    except (archive.Held, google.GoogleError, Conflict, StoreUnavailable) as error:
        result = {"status": "held", "class_key": class_key, "problem": archive._problem(error)}
        if run:
            try:
                run.finish(result)
            except (Conflict, StoreUnavailable):
                pass
        return result
