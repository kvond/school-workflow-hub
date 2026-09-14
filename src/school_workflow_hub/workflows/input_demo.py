"""Runnable fixture input-to-report slice; never invokes a real provider."""
import copy
from dataclasses import replace
import json
from pathlib import Path

from school_workflow_hub.adapters.agenda_rows import AgendaLayout
from school_workflow_hub.adapters.submission_pages import collect_submission_window, SubmissionPage
from school_workflow_hub.core.database import PROJECT_ROOT, initialize_database, require_synthetic_runtime_path
from school_workflow_hub.core.observation_baseline import read_baseline
from school_workflow_hub.core.runs import canonical_json
from school_workflow_hub.workflows.agenda_observer import observe_agenda
from school_workflow_hub.workflows.daily_notes import plan_daily_notes
from school_workflow_hub.workflows.feedback_pipeline import load_feedback_fixture, plan_feedback_pipeline
from school_workflow_hub.workflows.print_packet import render_print_packet
from school_workflow_hub.workflows.reporting_pipeline import plan_reporting_pipeline
from school_workflow_hub.workflows.system_demo import _write_once, ledger_counts


def run_input_demo(output_directory: Path):
    out = require_synthetic_runtime_path(output_directory)
    out.mkdir(parents=True, exist_ok=True)
    payload = json.loads((PROJECT_ROOT / "tests/fixtures/feedback_pipeline_cases.json").read_text())
    assignments, observations, request = load_feedback_fixture(payload)
    selected = assignments[0]
    cells = [selected.stable_id, selected.class_stable_id, selected.title,
             selected.intended_due_date, "https://example.invalid/synthetic-deck/copy"]
    grid = {"spreadsheetId": "synthetic_sheet_0123456789", "properties": {"title": "Synthetic Agenda"},
            "sheets": [{"properties": {"sheetId": 7, "title": "Synthetic Course"},
                        "data": [{"startRow": 1, "rowData": [{"values": [
                            {"formattedValue": value} for value in cells]}]}]}]}

    class FixtureReader:
        fail = False

        def read(self, spreadsheet_id, ranges):
            if self.fail:
                raise RuntimeError("synthetic provider failure")
            return copy.deepcopy(grid)

    db = initialize_database(out / "state.sqlite3")
    try:
        reader = FixtureReader()
        args = dict(spreadsheet_id=grid["spreadsheetId"], sheet_title="Synthetic Course",
                    layout=AgendaLayout(7, 1, 2, 0, 1, 2, 3, 4), key=b"f" * 32,
                    stream_key="agenda-fixture-demo", clock=lambda: request.as_of)
        first = observe_agenda(reader, db, **args)
        second = observe_agenda(reader, db, **args)
        if not first.saved or second.status != "unchanged":
            raise ValueError("synthetic_agenda_replay_failed")
        before_failure = read_baseline(db, args["stream_key"])
        reader.fail = True
        failed = observe_agenda(reader, db, **args)
        if failed.saved or read_baseline(db, args["stream_key"]) != before_failure:
            raise ValueError("synthetic_baseline_recovery_failed")

        # Join by stable ID and class, carrying the observed date/title into the
        # existing fixture feedback pipeline. Preserve separately verified
        # deck/Form/external identities; a date-only observation cannot change them.
        observed = {row.assignment.stable_id: row.assignment for row in second.rows}
        if any(a.class_stable_id != observed[a.stable_id].class_stable_id
               for a in assignments if a.stable_id in observed):
            raise ValueError("synthetic_agenda_assignment_scope_mismatch")
        assignments = tuple(replace(a, title=observed[a.stable_id].title,
                                    intended_due_date=observed[a.stable_id].intended_due_date)
                            if a.stable_id in observed else a for a in assignments)

        pages = [SubmissionPage(request.class_stable_id, "synthetic-demo-snapshot",
                                tuple(o.submission for o in observations[:3]), "synthetic-next"),
                 SubmissionPage(request.class_stable_id, "synthetic-demo-snapshot",
                                tuple(o.submission for o in observations[3:]))]
        fetch = lambda cursor, since, until: pages[0 if cursor is None else 1]
        collected = collect_submission_window(fetch, class_stable_id=request.class_stable_id,
            assignment_ids=frozenset(a.stable_id for a in assignments), as_of=request.as_of)
        if not collected.complete:
            raise ValueError("synthetic_submission_collection_failed")
        keys = {(r.stable_id, r.revision_hash) for r in collected.records}
        accepted = tuple(o for o in observations if (o.submission.stable_id, o.submission.revision_hash) in keys)
        feedback = plan_feedback_pipeline(db, assignments, accepted, request)
        packet = render_print_packet(plan_reporting_pipeline(feedback, friday_class_ids=(request.class_stable_id,)).preview)
        repeated = plan_feedback_pipeline(db, assignments, accepted, request)
        packet2 = render_print_packet(plan_reporting_pipeline(repeated, friday_class_ids=(request.class_stable_id,)).preview)
        if packet != packet2 or repeated.preview.changes:
            raise ValueError("synthetic_feedback_replay_failed")

        notes = ({"id": "synthetic-today-note", "content": "Discuss the strongest counterexample",
                  "labels": ["synthetic-course"], "due": {"date": request.as_of.date().isoformat()}},)
        note_args = dict(local_day=request.as_of.date(), timezone_name="America/New_York",
            class_labels={"synthetic-course": request.class_stable_id},
            meeting_class_ids=frozenset({request.class_stable_id}))
        routed = plan_daily_notes(notes, **note_args)
        replayed = plan_daily_notes(notes, seen_versions=frozenset(n.stable_id for n in routed.notes), **note_args)
        if len(routed.notes) != 1 or replayed.notes:
            raise ValueError("synthetic_note_replay_failed")
        summary = {"schema_version": "input-demo/v1", "fixture_only": True,
            "agenda_rows": len(second.rows), "identical_agenda_rerun": True,
            "failed_read_preserves_baseline": True, "submission_records": len(collected.records),
            "submission_collection_complete": True, "report_packet_hash": packet.content_hash,
            "duplicate_free_feedback_replay": True, "notes_routed": len(routed.notes),
            "accepted_note_version_skipped": replayed.already_seen_count,
            "ledger_counts": ledger_counts(db), "external_writes": 0, "live_source_reads": 0,
            "live_model_calls": 0, "delivery_state": "not_delivered"}
        _write_once(out / "REPORT_PREVIEW.html", packet.html)
        _write_once(out / "INPUT_RECEIPT.json", canonical_json(summary) + "\n")
        return summary
    finally:
        db.close()
