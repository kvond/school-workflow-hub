"""Idempotent, unscored intake registration into the existing private workbook.

Plan first; one Sheets batch appends new intake rows and supersedes matching
unscored stopgaps atomically. Never touch assessments or infer zero scores.
"""
import re
from .assessment_save import _workbook, _append, _set_cell, _batch, WRITE_SCOPE
from .google_sheets import SheetsReader
from .grading_records import read_records
from .hosted_agenda import service_token, ConnectionError
from ..core.runs import content_hash


def file_id(row):
    explicit = row.get("source_file_id")
    if explicit:
        return explicit
    match = re.search(r"/d/([A-Za-z0-9_-]{20,128})(?:/|$)", row.get("source_url", ""))
    return match[1] if match else None


def plan(workbook, catalog, files, now):
    assignments, submissions = workbook["Assignments"], workbook["Submissions"]
    if "learner_key" not in submissions["headers"]:
        raise ConnectionError("intake_workbook_learner_header_required")
    existing = {}
    for _, row in assignments["rows"]:
        key = row["assignment_key"]
        if not key or key in existing:
            raise ConnectionError("intake_registry_conflict")
        existing[key] = row
    requests, proposed, superseded = [], [], []
    catalog_sources = {}
    for item in catalog:
        key = item["assignment_key"]
        source_id = item.get("source_deck_id")
        if key in catalog_sources and catalog_sources[key] != source_id:
            raise ConnectionError("intake_registry_conflict")
        catalog_sources[key] = source_id
        if key in existing:
            if existing[key]["class_key"] != item["class_key"]:
                raise ConnectionError("intake_registry_conflict")
        else:
            row = {k:item[k] for k in ("assignment_key", "class_key", "assignment_title")}
            requests.append(_append(assignments, row))
            existing[key] = row
    for item in files:
        person = item.get("roster_match")
        if not person or not item.get("section_id") or not item.get("assignment_key"):
            continue
        key = item["assignment_key"]
        if key not in existing or existing[key]["class_key"] != person["class_key"]:
            raise ConnectionError("intake_registry_conflict")
        rid = "shared-intake-" + content_hash({"file": item["id"], "assignment": key,
                                               "learner": person["learner_key"]})[:32]
        matches = [(pos,r) for pos,r in submissions["rows"] if file_id(r) == item["id"]]
        # Reviewed records always retain history; a disagreement is never resolved
        # by overwriting them with a new unscored intake row.
        if any(r.get("status") not in {"identity_unverified", "superseded"}
               and (r.get("record_id") != rid or r.get("status") != "roster_matched")
               for _,r in matches):
            continue
        row = {"record_id": rid, "assignment_key": key, "section_id": item["section_id"],
               "section_name": person["section_name"], "student_id": "",
               "learner_key": person["learner_key"],
               "display_name": person["first"] + " " + person["last"],
               "source_url": "https://drive.google.com/file/d/" + item["id"] + "/view",
               "source_file_id": item["id"], "source_revision": str(item.get("version") or ""),
               "status": "roster_matched", "content_score": "", "revision_score": "",
               "hold_reason": "; ".join(item.get("intake_holds", [])), "feedback": "",
               "checked_at": now, "ic_posting_status": "not_posted"}
        prior = [(pos,r) for pos,r in matches if r["record_id"] == rid]
        if len(prior) > 1:
            raise ConnectionError("intake_duplicate_record")
        if prior:
            # Same ID with different evidence is a review, not a silent rewrite.
            for field in ("assignment_key", "section_id", "learner_key", "hold_reason", "source_revision"):
                if prior[0][1].get(field, "") != row.get(field, ""):
                    raise ConnectionError("intake_existing_evidence_changed")
        else:
            requests.append(_append(submissions, row))
        proposed.append(row)
        for position, old in matches:
            if not old["record_id"].startswith("intake-") or old.get("status") != "identity_unverified":
                continue
            if (old.get("assignment_key") != key or old.get("content_score")
                    or old.get("revision_score") or old.get("ic_posting_status") != "not_posted"):
                raise ConnectionError("intake_stopgap_conflict")
            requests.append(_set_cell(submissions, position, "status", "superseded"))
            if "superseded_by" in submissions["headers"]:
                requests.append(_set_cell(submissions, position, "superseded_by", rid))
            superseded.append(old["record_id"])
    return requests, proposed, superseded


def reconcile(assertion, config, catalog, files, now, renew):
    """Caller holds the shared intake lease. Fresh-read and verify every write.

    Runtime activation remains explicit private configuration; dry runs return
    proposals only. An uncertain batch is reconciled by rereading on the next
    teacher action, never by automatically repeating the request.
    """
    if config.get("HUB_INTAKE_WORKBOOK_WRITES") != "true" or config.get("HUB_DRY_RUN") == "true":
        return {"status": "dry_run", "external_writes": 0}
    source = config.get("HUB_GRADING_SPREADSHEET_ID", "")
    service = config.get("HUB_READER_SERVICE_ACCOUNT", "")
    reader = SheetsReader(service_token(service))
    before = _workbook(reader, source)
    requests, expected, superseded = plan(before, catalog, files, now)
    if requests:
        if _workbook(reader, source) != before:
            raise ConnectionError("intake_workbook_changed")
        renew()
        _batch(source, service_token(service, WRITE_SCOPE), requests)
    readback = read_records(assertion, config)
    rows = {r["record_id"]:r for r in readback["submissions"]}
    for expected_row in expected:
        actual = rows.get(expected_row["record_id"], {})
        for field in ("assignment_key", "section_id", "learner_key", "hold_reason", "source_revision", "status"):
            if actual.get(field) != expected_row.get(field):
                raise ConnectionError("intake_readback_failed")
    if any(rows.get(rid, {}).get("status") != "superseded" for rid in superseded):
        raise ConnectionError("intake_readback_failed")
    keys = {r["assignment_key"] for r in readback["assignments"]}
    if not all(r["assignment_key"] in keys for r in catalog):
        raise ConnectionError("intake_readback_failed")
    return {"status": "verified", "external_writes": int(bool(requests)),
            "registered": len(expected), "superseded": len(superseded)}
