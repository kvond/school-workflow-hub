"""One integrated synthetic acceptance preview, reusing every shared ledger."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import tempfile

from school_workflow_hub.core.config import load_config
from school_workflow_hub.core.database import PROJECT_ROOT, initialize_database, require_synthetic_runtime_path
from school_workflow_hub.core.runs import canonical_json, content_hash
from school_workflow_hub.core.schedule import preview_job_schedule
from school_workflow_hub.core.workflow import combine_previews, persist_preview, validate_preview, assert_safe_preview
from school_workflow_hub.workflows.agenda_interconnection import AssignmentFolderObservation, SchoologyObservation, InfiniteCampusObservation
from school_workflow_hub.workflows.curriculum import Course, OriginalMaterialRights, ForensicsActivityPlan
from school_workflow_hub.workflows.curriculum_pipeline import AgendaSlot, AssignmentObservation, CalendarDay, CalendarRevision, CurriculumPipelineInput, plan_curriculum_pipeline
from school_workflow_hub.workflows.feedback_pipeline import load_feedback_fixture, plan_feedback_pipeline
from school_workflow_hub.workflows.lesson_generator import LessonCandidate, LessonGenerationInput, SubjectLessonConfig, PromptOccurrence, FORENSICS_REVISION_PROMPT, inventory_forensics_prompts, plan_two_lessons_ahead, subject_configs_from_rules
from school_workflow_hub.workflows.reporting_pipeline import plan_reporting_pipeline, render_report_packet
from school_workflow_hub.workflows.research_pipeline import CategoryVersion, SyntheticFieldNote, plan_research_pipeline
from school_workflow_hub.workflows.schoology_gradebook import DesiredPlatformAssignment, ObservedPlatformAssignment, LinkOccurrence, audit_link_occurrences, reconcile_platform_assignments
from school_workflow_hub.core.workflow import EvidenceRef, PreviewChange, WorkflowPreview


TABLES = ("runs", "source_revisions", "proposed_actions", "review_items", "artifacts", "audit_events", "run_steps")


def ledger_counts(connection):
    return {table: connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in TABLES}


def ledger_fingerprint(connection):
    return content_hash({table: [dict(row) for row in connection.execute("SELECT * FROM " + table + " ORDER BY id")] for table in TABLES})


def _write_once(path: Path, text: str) -> None:
    if path.exists():
        if path.is_symlink() or path.read_text(encoding="utf-8") != text:
            raise FileExistsError("refusing to overwrite different preview content")
        return
    # Publish only a fully written file. A failed write never leaves a torn
    # final artifact that would prevent safe same-directory resumption.
    descriptor, temporary = tempfile.mkstemp(prefix=".preview-pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)  # Atomic create; never replaces a target.
        except FileExistsError:
            if path.is_symlink() or path.read_text(encoding="utf-8") != text:
                raise FileExistsError("refusing to overwrite different preview content")
    finally:
        os.unlink(temporary)


def _feedback_projection(connection, feedback):
    """Render current pending proposals, independently of this run's delta."""
    changes = []
    for record in feedback.records:
        row = connection.execute(
            "SELECT * FROM proposed_actions WHERE action_type = 'feedback_draft_review' "
            "AND source_revision_id = ? AND state = 'proposed' ORDER BY id",
            (record.source_revision_id,),
        ).fetchone()
        if row:
            changes.append(PreviewChange(row["id"], record.assignment.stable_id,
                row["target_system"], row["target_stable_id"], "feedback_and_grade_proposal",
                json.loads(row["expected_state_json"]), json.loads(row["proposed_state_json"]),
                record.evidence, 1.0, True))
    result = WorkflowPreview("feedback_pipeline_current", tuple(changes), feedback.preview.issues)
    validate_preview(result)
    return result


def run_system_preview(config_path: Path, output_directory: Path) -> dict:
    """Create only fixture previews; an identical replay verifies and reuses them."""
    config = load_config(config_path)
    out = require_synthetic_runtime_path(output_directory)
    fixture_path = PROJECT_ROOT / "tests/fixtures/feedback_pipeline_cases.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    calendars = json.loads((PROJECT_ROOT / "tests/fixtures/curriculum_calendars.json").read_text())
    code = {str(path.relative_to(PROJECT_ROOT)): content_hash(path.read_text()) for path in sorted((PROJECT_ROOT / "src").rglob("*.py"))}
    fingerprint = content_hash({"version": 2, "code": code, "config": asdict(config), "fixture": fixture, "calendar": calendars["normal"]})
    out.mkdir(parents=True, exist_ok=True)
    receipt_path = out / "receipt.json"
    db_path = require_synthetic_runtime_path(out / "state.sqlite3")
    preview_path = require_synthetic_runtime_path(out / "preview.json")
    summary_path = require_synthetic_runtime_path(out / "PREVIEW.md")
    reports_path = require_synthetic_runtime_path(out / "REPORTS.md")
    if any(path.is_symlink() for path in (receipt_path, preview_path, summary_path, reports_path)):
        raise ValueError("preview files must not be symlinks")
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt["input_fingerprint"] != fingerprint:
            raise ValueError("inputs changed; choose a new output directory for this preview")
        if not db_path.is_file():
            raise ValueError("saved preview ledger is missing")
        connection = initialize_database(db_path)
        try:
            if ledger_counts(connection) != receipt["ledger_counts"]:
                raise ValueError("saved preview ledger differs; reconciliation required")
            if ledger_fingerprint(connection) != receipt["ledger_fingerprint"]:
                raise ValueError("saved preview ledger content differs; reconciliation required")
            if content_hash(preview_path.read_text()) != receipt["preview_fingerprint"]:
                raise ValueError("saved preview content differs; reconciliation required")
            if content_hash(summary_path.read_text()) != receipt["summary_fingerprint"]:
                raise ValueError("saved preview summary differs; reconciliation required")
            if content_hash(reports_path.read_text()) != receipt["reports_fingerprint"]:
                raise ValueError("saved report packet differs; reconciliation required")
        finally:
            connection.close()
        return receipt
    connection = initialize_database(db_path)
    try:
        assignments, observations, request = load_feedback_fixture(fixture)
        if request.class_stable_id not in {item.stable_id for item in config.classes}:
            raise ValueError("fixture course is not in the shared configuration")
        request = replace(request, retention_days=config.workflow_policy.get("retention_days", 90))
        base = assignments[0]
        route = {"assignment_folder": "synthetic:folder-item", "schoology": "synthetic:page-one", "infinite_campus": "synthetic:ic-one"}
        intended = replace(base, intended_due_date=None, external_ids=route)
        observed_assignment = replace(intended, title="Synthetic old reasoning", intended_due_date="2026-09-14", deck_file_id="deck:synthetic:old")
        calendar = CalendarRevision("synthetic:calendar", "synthetic:calendar-v1", tuple(CalendarDay(**day) for day in calendars["normal"]))
        curriculum_input = CurriculumPipelineInput(calendar,
            (AgendaSlot("synthetic:lesson-one", intended, "synthetic:agenda-v1", "A", 1, 2),),
            (AssignmentObservation(observed_assignment, "synthetic:master-v1"),),
            (AssignmentFolderObservation(base.class_stable_id, base.stable_id, route["assignment_folder"], "2026-09-14", "Synthetic old message", "deck:synthetic:old", base.form_file_id, "synthetic:folder-v1"),),
            (SchoologyObservation(base.class_stable_id, base.stable_id, route["schoology"], "2026-09-14", "Synthetic old message", "synthetic:schoology-v1"),),
            (InfiniteCampusObservation(base.class_stable_id, base.stable_id, route["infinite_campus"], "2026-09-14", "synthetic:ic-v1"),))
        curriculum = plan_curriculum_pipeline(curriculum_input)
        due = curriculum.artifacts[0]["intended_due_date"]
        deck_ref = "https://example.invalid/slides/reasoning/copy"
        desired = DesiredPlatformAssignment(replace(intended, intended_due_date=due), "week-03", "mp-01", deck_ref, "synthetic:week-folder", "draft", route["infinite_campus"], "synthetic:desired-v1")
        current = tuple(ObservedPlatformAssignment(base.stable_id, base.class_stable_id, system, route[system], "synthetic:" + system + "-v1",
            {"title": "Synthetic old reasoning", "due_date": "2026-09-14", "deck_reference": "https://example.invalid/slides/old/copy", "week_key": "week-02", "marking_period": "mp-01", "destination_id": "synthetic:old-folder", "publication": "draft"}) for system in ("schoology", "infinite_campus"))
        platform = reconcile_platform_assignments((desired,), current)
        combined = combine_previews("agenda_platform_integration", (curriculum, platform))
        interconnection_receipt = persist_preview(connection, combined)

        subject = next(item for item in subject_configs_from_rules(config.course_rules) if item.course == Course.FORENSICS)
        activity = ForensicsActivityPlan("Synthetic source observations", "Compare supported observations", "Name an alternative explanation", "Sort synthetic evidence cards")
        candidates = tuple(LessonCandidate("synthetic:lesson-" + str(index), assignment, Course.FORENSICS, index,
            "Which conclusion does the evidence support?", EvidenceRef("curriculum", "synthetic:source-" + str(index), "synthetic:source-v1", "synthetic://curriculum/" + str(index)),
            OriginalMaterialRights("synthetic://curriculum/" + str(index), "Synthetic fixture for local classroom planning"), forensics_activity=activity)
            for index, assignment in enumerate(assignments[:2], 1))
        generator = plan_two_lessons_ahead(LessonGenerationInput(candidates, (subject,), {"forensics": 0}))
        generator_receipt = persist_preview(connection, generator)
        prompt_inventory = inventory_forensics_prompts((PromptOccurrence(base.stable_id, "synthetic:deck-reasoning", "synthetic:response-one", "student_revision_prompt", "What does photosynthesis do with the Sun's energy?", "synthetic:deck-v1", "synthetic://prompts/old"),))
        prompt_receipt = persist_preview(connection, prompt_inventory)

        # The new intended date comes from the same Agenda evidence used for
        # the platform proposal. Reports do not retain a stale master due date.
        assignments = tuple(replace(item, intended_due_date=due) if item.stable_id == base.stable_id else item for item in assignments)
        feedback = plan_feedback_pipeline(connection, assignments, observations, request)
        validate_preview(feedback.preview)
        reporting = plan_reporting_pipeline(feedback)
        reports_text = render_report_packet(reporting.preview)
        note = SyntheticFieldNote("field-note:synthetic:001", request.class_stable_id, request.as_of.date(), "pause_before_comparison", "transition_may_need_support", "try_partner_comparison")
        research = plan_research_pipeline(feedback, (CategoryVersion("category:synthetic:evidence", 1, "evidence_boundary"),), (note,), research_assignment_threshold=config.workflow_policy.get("research_assignment_threshold", 3))
        report_receipt = persist_preview(connection, reporting.preview)
        research_receipt = persist_preview(connection, research.preview)
        expected_links = (LinkOccurrence("synthetic:link-one", base.stable_id, "schoology_page", "synthetic:page-one/deck", deck_ref, "synthetic:r2"),)
        actual_links = (replace(expected_links[0], destination="https://example.invalid/slides/old/copy"),)
        links = audit_link_occurrences(expected_links, actual_links)
        link_receipt = persist_preview(connection, links.preview)
        review_queue = [dict(row) for row in connection.execute(
            "SELECT id, run_id, action_id, kind, severity, status FROM review_items ORDER BY id"
        )]
        assert_safe_preview(review_queue)
        reports_text += "\n## Shared review queue\n\nAll items below remain review work; none is approval or delivery.\n\n"
        reports_text += "\n".join("- `{kind}` ({severity}, {status}); review `{id}`; linked proposal `{action_id}`.".format(**item) for item in review_queue) + "\n"
        payload = {
            "profile": "synthetic_only", "input_fingerprint": fingerprint,
            "interconnection": asdict(combined), "generator": asdict(generator),
            "forensics_prompt_inventory": asdict(prompt_inventory),
            "feedback": {"records": [asdict(record) for record in feedback.records], "preview": asdict(_feedback_projection(connection, feedback))},
            "reporting": asdict(reporting), "research": asdict(research), "link_audit": asdict(links),
            "shared_review_queue": review_queue,
            "schedules": [preview_job_schedule(config, key, as_of=request.as_of) for key in sorted(config.schedules)],
            "receipts": [interconnection_receipt, generator_receipt, prompt_receipt, report_receipt, research_receipt, link_receipt],
            "external_writes": 0, "live_model_calls": 0, "live_readers": "not_configured",
        }
        preview_text = json.dumps(payload, indent=2, sort_keys=True, default=lambda value: value.isoformat()) + "\n"
        assert_safe_preview(json.loads(preview_text))
        summary = ("# Synthetic AIHS workflow preview\n\n"
                   "All examples are fixtures. No real class, student, link, grade, or login was tested.\n\n"
                   f"- Agenda and platform changes proposed: {len(combined.changes)}\n"
                   f"- Lesson outlines: {len(generator.artifacts)}\n"
                   f"- Forensics prompt replacements proposed: {len(prompt_inventory.changes)}\n"
                   f"- Latest synthetic submission records: {len(feedback.records)}\n"
                   f"- Folder plans: {len(reporting.folder_plans)}\n"
                   f"- Offline link occurrences checked: {len(links.occurrences)}\n"
                   "- External writes, live model calls, active schedules: 0\n\n"
                   "Open REPORTS.md for readable, print-ready synthetic report previews (nothing printed).\n\n"
                   "Open preview.json for old/proposed values, evidence, pending approvals, feedback drafts, reports, exceptions and retention plans.\n"
                   "A second identical command reuses the saved artifacts after verifying their fingerprints and ledger counts.\n")
        _write_once(preview_path, preview_text)
        _write_once(summary_path, summary)
        _write_once(reports_path, reports_text)
        receipt = {"status": "synthetic_preview_ready", "input_fingerprint": fingerprint,
                   "preview_fingerprint": content_hash(preview_text), "summary_fingerprint": content_hash(summary),
                   "reports_fingerprint": content_hash(reports_text), "reports": str(reports_path),
                   "ledger_counts": ledger_counts(connection), "ledger_fingerprint": ledger_fingerprint(connection), "preview": str(preview_path),
                   "summary": str(summary_path), "external_writes": 0, "live_model_calls": 0}
        _write_once(receipt_path, canonical_json(receipt) + "\n")
        return receipt
    finally:
        connection.close()
