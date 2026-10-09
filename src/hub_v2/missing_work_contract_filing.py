"""Host-only filing of a fresh, source-verified student's print-only work check.

There is no browser endpoint or roster/grade authority here. The trusted host
must provide the complete captured inputs used to derive its current plan.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import re
from zoneinfo import ZoneInfo

from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable
from school_workflow_hub.core.runs import content_hash

from . import google, missing_work_contract_pdf, missing_work_contracts as policy
from . import student_archive as archive

PROOF_SCHEMA = "hub-current-contract-bundle/v1"
INPUT_FIELDS = {"found", "roster_proof", "source_proof", "due_proofs", "archive_proof", "checked_at"}


def _now(value):
    if value is None:
        return datetime.now(timezone.utc)
    return policy._instant(value.isoformat() if isinstance(value, datetime) else value)


def _validated_plan(verified_plan, now):
    if not isinstance(verified_plan, dict):
        raise archive.Held("The host's complete current work-check plan is required.")
    inputs = verified_plan.get("inputs")
    plans = verified_plan.get("plans")
    if (not isinstance(inputs, list) or not 1 <= len(inputs) <= 50 or not isinstance(plans, list)
            or len(plans) != len(inputs)):
        raise archive.Held("The current plan needs its exact complete source, IC and private-folder inputs.")
    all_contracts, evidence_times, classes, check_times = [], [], set(), []
    for captured, expected in zip(inputs, plans):
        if not isinstance(captured, dict) or set(captured) != INPUT_FIELDS:
            raise archive.Held("A work-check input is incomplete or contains unsupported proof fields.")
        checked = policy._instant(captured["checked_at"])
        check_times.append(checked)
        classes.add(captured["found"].get("class_key"))
        age = (now - checked).total_seconds()
        if not 0 <= age <= policy.MAX_AGE_SECONDS or checked.astimezone(ZoneInfo("America/New_York")).date() != now.astimezone(ZoneInfo("America/New_York")).date():
            raise archive.Held("The current work check needs fresh complete evidence from today, within one hour.")
        policy._proof(captured["roster_proof"], now, "Infinite Campus roster", complete=True)
        policy._proof(captured["source_proof"], now, "Submitted-work scan", complete=True)
        policy._proof(captured["archive_proof"], now, "Private student archive")
        due = captured["due_proofs"]
        if not isinstance(due, dict) or not due:
            raise archive.Held("The original assignment and due-date checks are missing.")
        for proof in due.values():
            policy._proof(proof, now, "Assignment and due date")
        evidence_times.extend(policy._instant(p["checked_at"]) for p in
            [captured["roster_proof"], captured["source_proof"], captured["archive_proof"], *due.values()])
        actual = policy.derive_plan(captured["found"], **{k: captured[k] for k in INPUT_FIELDS - {"found"}})
        if actual != expected or actual.get("coverage_holds"):
            raise archive.Held("The current source inputs do not reproduce a complete verified work-check plan.")
        all_contracts.extend(actual["contracts"])
    ids = [c["id"] for c in all_contracts]
    if len(ids) != len(set(ids)):
        raise archive.Held("The current plan repeats an assignment/student check.")
    if len(classes) != 1 or next(iter(classes)) not in archive.CLASSES:
        raise archive.Held("Publish one verified class's current work check at a time.")
    for item in all_contracts:
        if item["id"] != "feedbacksource_" + content_hash({k: v for k, v in item.items() if k != "id"}):
            raise archive.Held("A current work-contract payload changed after its source checks.")
    inputs_sha = content_hash(inputs)
    return {"contracts": all_contracts, "class_key": next(iter(classes)), "inputs_sha256": inputs_sha,
            "plan_sha256": content_hash({"inputs_sha256": inputs_sha, "plans": plans}),
            "checked_at": max(check_times).isoformat(), "evidence_checked_at": min(evidence_times).isoformat(),
            "expires_at": (min(evidence_times) + timedelta(seconds=policy.MAX_AGE_SECONDS)).isoformat(),
            "date": now.astimezone(ZoneInfo("America/New_York")).date().isoformat()}


def _validated_group(contracts, verified_plan, now):
    if not isinstance(contracts, list) or not 1 <= len(contracts) <= 8 or not all(isinstance(c, dict) for c in contracts):
        raise archive.Held("Choose one student and a bounded current work-contract bundle.")
    plan = _validated_plan(verified_plan, now)
    first = contracts[0]
    student = first.get("student") or {}
    group_identity = (first.get("class_key"), student.get("student_key"), student.get("name"), student.get("section"))
    if not re.fullmatch(r"archivestudent_[a-f0-9]{64}", str(student.get("student_key", ""))):
        raise archive.Held("The current IC student needs one verified existing private archive folder before printing.")
    group = [c for c in plan["contracts"] if (c.get("class_key"), (c.get("student") or {}).get("student_key"),
        (c.get("student") or {}).get("name"), (c.get("student") or {}).get("section")) == group_identity]
    ordered = sorted(contracts, key=lambda c: str(c.get("contract_key", "")))
    if ordered != sorted(group, key=lambda c: c["contract_key"]):
        raise archive.Held("This bundle must exactly match every currently verified work check for that student.")
    return ordered, plan


def _bundle_identity(group, plan, pdf_bytes):
    payload_hash = content_hash({"schema": PROOF_SCHEMA, "contracts": group, "inputs_sha256": plan["inputs_sha256"]})
    return "feedbacksource_" + content_hash([PROOF_SCHEMA, payload_hash, hashlib.sha256(pdf_bytes).hexdigest()]), payload_hash


def file_current_contract_bundle(token, config, contracts, *, verified_plan, run_id,
                                 write_token=None, pdf_bytes=None, now=None):
    """Save one immutable current snapshot in an existing student's private folder.

    Call only from an authorized host after fresh complete source/IC checks.
    A future run must derive a new current plan; old contracts are never used
    as absence evidence. ``run_id`` records attribution, not artifact identity.
    """
    run = None
    try:
        checked_now = _now(now)
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", run_id):
            raise archive.Held("The current work-check filing needs its bounded host run reference.")
        group, plan = _validated_group(contracts, verified_plan, checked_now)
        cls, named = group[0]["class_key"], group[0]["student"]
        root, course = archive._location(token, config, cls)
        scope = archive._scope_root(config, root)
        port = archive.configured_store(config)
        index = archive._read(port, archive.key("archiveclass", [scope, cls]))
        owner = archive._read(port, named["student_key"])
        if (not index or index.value.get("schema") != archive.SCHEMA or not owner
                or owner.value.get("status") != "verified" or owner.value.get("schema") != archive.SCHEMA
                or owner.value.get("student_key") != named["student_key"]
                or owner.value.get("record_key") != named["student_key"]
                or index.value.get("students", {}).get(named["student_key"]) != owner.value.get("drive_file_id")
                or named.get("student_folder_id") != owner.value.get("drive_file_id")
                or not archive._same_name(owner.value, named)
                or owner.value.get("name") != named.get("name")
                or (named.get("ic_student_id") and owner.value.get("student_id")
                    and str(named["ic_student_id"]) != str(owner.value["student_id"]))):
            raise archive.Held("This current IC student does not match one existing private class/student archive.")
        student = owner.value
        archive._private(google.drive_file(token, student["parent"], archive.FIELDS), parent=course["id"], folder=True)
        archive._private(google.drive_file(token, student["drive_file_id"], archive.FIELDS), parent=student["parent"], folder=True, write=True)
        canonical_pdf = missing_work_contract_pdf.render(group)
        if pdf_bytes is None:
            pdf_bytes = canonical_pdf
        if not isinstance(pdf_bytes, bytes) or pdf_bytes != canonical_pdf or len(pdf_bytes) > 25_000_000:
            raise archive.Held("The current contract PDF must match this exact verified student's complete lesson bundle.")
        feedback_id, payload_hash = _bundle_identity(group, plan, pdf_bytes)
        ident = archive.key("archivefeedback", [scope, feedback_id])
        keys = [c["contract_key"] for c in group]
        checked_at = min(c["checked_at"] for c in group)
        proof = {"schema": PROOF_SCHEMA, "inputs_sha256": plan["inputs_sha256"], "payload_sha256": payload_hash,
                 "plan_sha256": plan["plan_sha256"], "plan_id": archive.key("currentcontractplan", [scope, cls]),
                 "checked_at": checked_at, "contracts": [{"contract_key": c["contract_key"], "report_id": c["id"],
                    "assignment_key": c["assignment_key"], "source_proof": c["source_proof"]} for c in group]}
        run = archive._Run(token, write_token or token, config, cls)
        archive._private(google.drive_file(token, student["parent"], archive.FIELDS), parent=run.course["id"], folder=True)
        archive._private(google.drive_file(token, student["drive_file_id"], archive.FIELDS), parent=student["parent"], folder=True, write=True)
        old = archive._read(run.port, ident)
        if old and (old.value.get("student_key") != named["student_key"]
                    or old.value.get("source_sha256") != payload_hash or old.value.get("contract_keys") != keys):
            raise archive.Held("This immutable work-check snapshot already has different student or lesson evidence.")
        day = group[0]["prepared_at"]
        artifact = run.create(ident, day + " - " + named["name"] + " - Current work contract.pdf",
            student["drive_file_id"], archive.PDF, pdf=pdf_bytes, attributes={"kind": "feedback", "date": day,
                "feedback_id": feedback_id, "report_type": "missing_work_contract", "feedback_origin": "current_source_work_check",
                "student_key": named["student_key"], "class_key": cls, "saved_at": checked_now.isoformat(),
                "prepared_at": day, "checked_at": checked_at, "contract_checked_at": checked_at, "contract_status": "open",
                "contract_keys": keys, "original_due_date": [c["original_due_date"] for c in group],
                "assignment": "Current work contract — " + "; ".join(c["assignment_title"] for c in group),
                "source_sha256": payload_hash, "source_proof": proof, "filing_run_id": run_id,
                "identity_status": student.get("identity_status", "identity_unverified"),
                "print_identity_basis": named["identity_basis"], "grade_change": False, "delivery_changed": False})
        _validated_group(contracts, verified_plan, _now(now))
        run.attach(student, artifact)
        run.finish({"status": "verified", "report_type": "missing_work_contract", "feedback_id": feedback_id,
                    "drive_file_id": artifact["drive_file_id"], "contract_count": len(group), "grade_change": False,
                    "delivery_changed": False})
        result = archive._feedback_metadata(artifact, student, run.course)
        result.update(contract_count=len(group), prepared_at=day, checked_at=checked_at, source_sha256=payload_hash)
        result["index_sync"] = archive.refresh_work_index(token, write_token or token, config)
        return result
    except (archive.Held, policy.CoverageError, google.GoogleError, Conflict, StoreUnavailable,
            KeyError, TypeError, ValueError) as error:
        result = {"status": "held", "report_type": "missing_work_contract", "problem": archive._problem(error),
                  "grade_change": False, "delivery_changed": False}
        if run:
            try:
                run.finish(result)
            except (Conflict, StoreUnavailable):
                pass
        return result


def publish_current_plan(token, config, verified_plan, bundle_results, *, write_token=None, now=None):
    """CAS-only publication after every current bundle's private readback.

    Readers may use the dated pointer as an as-of snapshot; a new daily build
    must also enforce ``expires_at`` and recheck submitted sources. Old saved
    packets remain explicit historical downloads, never current absence proof.
    """
    run = None
    try:
        plan = _validated_plan(verified_plan, _now(now))
        cls = plan["class_key"]
        root, course = archive._location(token, config, cls)
        scope = archive._scope_root(config, root)
        pointer_id = archive.key("currentcontractplan", [scope, cls])
        groups = {}
        for contract in plan["contracts"]:
            groups.setdefault(contract["student"]["student_key"], []).append(contract)
        if not isinstance(bundle_results, list) or len(bundle_results) != len(groups):
            raise archive.Held("The current plan cannot publish until every checked student's bundle is verified.")
        supplied = {r.get("student_key"): r for r in bundle_results if isinstance(r, dict)}
        if len(supplied) != len(bundle_results) or set(supplied) != set(groups):
            raise archive.Held("The saved bundles do not exactly cover this current student plan.")
        port = archive.configured_store(config)
        index = archive._read(port, archive.key("archiveclass", [scope, cls]))
        if not index or index.value.get("schema") != archive.SCHEMA:
            raise archive.Held("The private class archive index needs verification before publishing its work check.")
        feedback_ids, contracts = [], []
        for student_key, current in sorted(groups.items()):
            group = sorted(current, key=lambda c: c["contract_key"])
            pdf_bytes = missing_work_contract_pdf.render(group)
            feedback_id, payload_hash = _bundle_identity(group, plan, pdf_bytes)
            result = supplied[student_key]
            ident = archive.key("archivefeedback", [scope, feedback_id])
            artifact_doc, owner = archive._read(port, ident), archive._read(port, student_key)
            artifact = artifact_doc.value if artifact_doc else None
            if (result.get("status") != "verified" or result.get("feedback_id") != feedback_id or not artifact or not owner
                    or owner.value.get("status") != "verified" or owner.value.get("schema") != archive.SCHEMA
                    or owner.value.get("student_key") != student_key
                    or index.value.get("students", {}).get(student_key) != owner.value.get("drive_file_id")
                    or not archive._same_name(owner.value, group[0]["student"])
                    or group[0]["student"].get("student_folder_id") != owner.value.get("drive_file_id")
                    or artifact.get("status") != "verified" or artifact.get("schema") != archive.SCHEMA
                    or artifact.get("kind") != "feedback" or artifact.get("report_type") != "missing_work_contract"
                    or artifact.get("record_key") != ident or artifact.get("feedback_id") != feedback_id
                    or artifact.get("student_key") != student_key or artifact.get("class_key") != cls
                    or ident not in owner.value.get("items", {}) or artifact.get("contract_status") != "open"
                    or artifact.get("contract_keys") != [c["contract_key"] for c in group]
                    or artifact.get("source_sha256") != payload_hash
                    or artifact.get("pdf_sha256") != hashlib.sha256(pdf_bytes).hexdigest()
                    or artifact.get("pdf_md5") != hashlib.md5(pdf_bytes).hexdigest()
                    or artifact.get("size") != len(pdf_bytes)
                    or (artifact.get("source_proof") or {}).get("plan_sha256") != plan["plan_sha256"]
                    or (artifact.get("source_proof") or {}).get("plan_id") != pointer_id
                    or artifact.get("drive_file_id") != result.get("drive_file_id")):
                raise archive.Held("A current work-check PDF, owner attachment or immutable source binding is unverified.")
            archive._private(google.drive_file(token, owner.value["parent"], archive.FIELDS), parent=course["id"], folder=True)
            archive._private(google.drive_file(token, owner.value["drive_file_id"], archive.FIELDS), parent=owner.value["parent"], folder=True)
            archive._feedback_file(token, scope, course, owner.value, artifact)
            feedback_ids.append(feedback_id); contracts.extend(artifact["contract_keys"])
        # Recheck real capture ages after the readback, before reserving a CAS
        # publication. This cannot make an older proof fresh by relabeling it.
        _validated_plan(verified_plan, _now(now))
        run = archive._Run(token, write_token or token, config, cls)
        old = archive._read(run.port, pointer_id)
        if old and policy._instant(old.value["checked_at"]) > policy._instant(plan["checked_at"]):
            raise archive.Held("A newer current work-check plan is already saved; preserve it.")
        _validated_plan(verified_plan, _now(now))
        value = {"schema": "hub-current-contract-plan/v1", "status": "verified", "record_key": pointer_id,
                 "class_key": cls, "date": plan["date"], "checked_at": plan["checked_at"],
                 "evidence_checked_at": plan["evidence_checked_at"], "expires_at": plan["expires_at"],
                 "plan_sha256": plan["plan_sha256"], "inputs_sha256": plan["inputs_sha256"],
                 "contract_keys": sorted(contracts), "feedback_ids": sorted(feedback_ids),
                 "grade_change": False, "delivery_changed": False}
        if not old or old.value != value:
            run.put(pointer_id, value, old)
        stored = archive._read(run.port, pointer_id)
        if not stored or stored.value != value:
            raise archive.Held("The current work-check pointer did not read back exactly.")
        run.finish({"status": "verified", "current_contract_plan": pointer_id,
                    "student_bundles": len(groups), "contracts": len(contracts)})
        return {**value, "student_bundles": len(groups), "contracts": len(contracts)}
    except (archive.Held, policy.CoverageError, google.GoogleError, Conflict, StoreUnavailable,
            KeyError, TypeError, ValueError) as error:
        result = {"status": "held", "problem": archive._problem(error), "grade_change": False, "delivery_changed": False}
        if run:
            try:
                run.finish(result)
            except (Conflict, StoreUnavailable):
                pass
        return result
