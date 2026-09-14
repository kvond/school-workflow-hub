import copy
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest

from school_workflow_hub.adapters.agenda_rows import AgendaLayout, normalize_agenda_rows, cell_links
from school_workflow_hub.adapters.google_sheets import fingerprint_snapshot
from school_workflow_hub.adapters.submission_pages import SubmissionPage, collect_submission_window
from school_workflow_hub.core.database import initialize_database, transaction
from school_workflow_hub.core.models import SubmissionRevision
from school_workflow_hub.core.observation_baseline import checkpoint_observation, read_baseline
from school_workflow_hub.workflows.daily_notes import plan_daily_notes

CLASS = "class:synthetic:biology-a"
ASSIGNMENT = "assignment:synthetic:lesson-a"
AS_OF = datetime(2026, 9, 14, 20, tzinfo=timezone.utc)
REVISION = "a" * 64
LAYOUT = AgendaLayout(7, 1, 4, 0, 1, 2, 3, 4)
COVERAGE = (1, 4, 0, 5)


def agenda(values=None):
    values = values or [ASSIGNMENT, CLASS, "Synthetic lesson", "2026-09-16", "https://example.invalid/deck/copy"]
    return {"spreadsheetId": "synthetic_sheet_0123456789", "properties": {"title": "Synthetic Agenda"},
            "sheets": [{"properties": {"sheetId": 7, "title": "Synthetic Biology"},
                        "data": [{"startRow": 1, "rowData": [{"values": [
                            {"formattedValue": value} for value in values]}]}]}]}


def normalize(data, layout=LAYOUT, coverage=COVERAGE):
    return normalize_agenda_rows(data, layout, source_revision=REVISION, coverage=coverage)


class AgendaRowsTests(unittest.TestCase):
    def test_explicit_stable_mapping_preserves_copy_mode(self):
        result = normalize(agenda())
        self.assertTrue(result.complete)
        self.assertEqual(result.rows[0].assignment.stable_id, ASSIGNMENT)
        self.assertEqual(result.rows[0].assignment.intended_due_date, "2026-09-16")
        self.assertTrue(result.rows[0].deck_reference.endswith("/copy"))
        self.assertIsNone(result.rows[0].assignment.deck_file_id)
        self.assertNotIn("Synthetic lesson", repr(result))

    def test_moved_or_renamed_row_keeps_identity_and_link(self):
        before = normalize(agenda()).rows[0]
        data = agenda()
        grid = data["sheets"][0]["data"][0]
        grid["startRow"] = 2
        grid["rowData"][0]["values"][2]["formattedValue"] = "Renamed synthetic lesson"
        grid["rowData"][0]["values"][3]["formattedValue"] = "2026-09-18"
        after = normalize(data).rows[0]
        self.assertEqual(before.assignment.stable_id, after.assignment.stable_id)
        self.assertEqual(before.deck_reference, after.deck_reference)
        self.assertNotEqual(before.row_index, after.row_index)

    def test_no_identity_is_invented_from_title_or_row(self):
        for identity in ("", "UNVERIFIED", "contains space"):
            data = agenda()
            data["sheets"][0]["data"][0]["rowData"][0]["values"][0]["formattedValue"] = identity
            result = normalize(data)
            self.assertFalse(result.complete)
            self.assertEqual(result.rows, ())

    def test_dates_need_explicit_format_and_real_calendar_date(self):
        for value in ("9/16/26", "September 16", "2026-02-30", ""):
            data = agenda()
            data["sheets"][0]["data"][0]["rowData"][0]["values"][3]["formattedValue"] = value
            self.assertFalse(normalize(data).complete)
        data = agenda([ASSIGNMENT, CLASS, "Synthetic", "09/16/2026", "https://example.invalid/copy"])
        self.assertFalse(normalize(data).complete)
        self.assertTrue(normalize(data, replace(LAYOUT, date_format="%m/%d/%Y")).complete)

    def test_rich_links_and_literal_formula_preserve_all_destinations(self):
        self.assertEqual(cell_links({"userEnteredValue": {"formulaValue": '=HYPERLINK("https://example.invalid/copy","Open")'}}),
                         ("https://example.invalid/copy",))
        links = cell_links({"hyperlink": "https://example.invalid/copy", "textFormatRuns": [
            {"format": {"link": {"uri": "https://example.invalid/edit"}}}]})
        self.assertEqual(len(links), 2)
        for formula in ('=HYPERLINK(A1,"Open")', '=IMPORTXML("https://example.invalid","//x")'):
            with self.assertRaises(ValueError):
                cell_links({"userEnteredValue": {"formulaValue": formula}})

    def test_ambiguous_or_unsafe_links_never_pass(self):
        for cell in ({"hyperlink": "http://example.invalid"}, {"hyperlink": "https://example.invalid?token=private"},
                     {"hyperlink": "https://example.invalid/copy", "formattedValue": "https://example.invalid/edit"}, {}):
            data = agenda()
            data["sheets"][0]["data"][0]["rowData"][0]["values"][4] = cell
            result = normalize(data)
            self.assertFalse(result.complete)
            self.assertNotIn("private", repr(result))

    def test_duplicate_identity_blocks_even_across_class_names(self):
        data = agenda()
        rows = data["sheets"][0]["data"][0]["rowData"]
        rows.append(copy.deepcopy(rows[0]))
        rows[1]["values"][1]["formattedValue"] = "class:synthetic:other"
        self.assertFalse(normalize(data).complete)
        self.assertEqual(normalize(data).rows, ())

    def test_sparse_empty_rows_are_not_missing_assignments(self):
        data = agenda()
        data["sheets"][0]["data"] = []
        result = normalize(data)
        self.assertTrue(result.complete)
        self.assertEqual(result.rows, ())

    def test_partial_duplicate_missing_or_overlapping_grid_is_held(self):
        self.assertFalse(normalize(agenda(), coverage=(1, 2, 0, 5)).complete)
        for mutate in (lambda d: d["sheets"].clear(),
                       lambda d: d["sheets"].append(copy.deepcopy(d["sheets"][0])),
                       lambda d: d["sheets"][0]["data"].append(copy.deepcopy(d["sheets"][0]["data"][0]))):
            data = agenda()
            mutate(data)
            self.assertFalse(normalize(data).complete)


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.db = initialize_database(self.path)
        self.observation = fingerprint_snapshot(agenda(), b"s" * 32)
        self.time = "2026-09-14T20:00:00+00:00"

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def save(self, observation=None, expected=None, at=None, db=None):
        return checkpoint_observation(db or self.db, "agenda-primary", observation or self.observation,
            observed_at=at or self.time, now="2026-09-14T22:00:00+00:00", expected_checkpoint=expected)

    def test_initial_baseline_then_rerun_no_duplicate(self):
        first = self.save()
        self.assertEqual(first["status"], "baseline_created")
        self.assertFalse(first["change_proven"])
        second = self.save(expected=first["checkpoint_token"], at="2026-09-14T21:00:00Z")
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(self.db.execute("SELECT count(*) FROM source_revisions").fetchone()[0], 1)
        self.assertEqual(read_baseline(self.db, "agenda-primary")["observed_at"], "2026-09-14T21:00:00+00:00")

    def test_content_change_and_return_to_prior_version_preserve_history(self):
        first = self.save()
        changed = dict(self.observation, fingerprint="b" * 64)
        second = self.save(changed, first["checkpoint_token"], "2026-09-14T21:00:00Z")
        third = self.save(self.observation, second["checkpoint_token"], "2026-09-14T21:30:00Z")
        self.assertEqual(second["status"], "changed")
        self.assertEqual(third["status"], "changed")
        self.assertEqual(first["revision_id"], third["revision_id"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM source_revisions").fetchone()[0], 2)

    def test_scope_key_or_range_change_does_not_replace_good_baseline(self):
        first = self.save()
        result = self.save(dict(self.observation, scope="c" * 64), first["checkpoint_token"], "2026-09-14T21:00:00Z")
        self.assertEqual(result["status"], "scope_changed")
        self.assertFalse(result["saved"])
        self.assertEqual(read_baseline(self.db, "agenda-primary")["revision_id"], first["revision_id"])

    def test_old_or_same_time_different_observation_cannot_advance(self):
        first = self.save()
        changed = dict(self.observation, fingerprint="b" * 64)
        for at, status in (("2026-09-14T19:00:00Z", "stale_observation"), (self.time, "conflicting_observation_time")):
            self.assertEqual(self.save(changed, first["checkpoint_token"], at)["status"], status)

    def test_competing_reader_uses_compare_and_set(self):
        first = self.save()
        other = initialize_database(self.path)
        try:
            self.save(dict(self.observation, fingerprint="b" * 64), first["checkpoint_token"], "2026-09-14T21:00:00Z", other)
            result = self.save(dict(self.observation, fingerprint="c" * 64), first["checkpoint_token"], "2026-09-14T21:30:00Z")
            self.assertEqual(result["status"], "baseline_conflict")
        finally:
            other.close()

    def test_old_checkpoint_cannot_pass_after_content_changes_then_returns(self):
        first = self.save()
        second = self.save(dict(self.observation, fingerprint="b" * 64), first["checkpoint_token"], "2026-09-14T20:30:00Z")
        reverted = self.save(self.observation, second["checkpoint_token"], "2026-09-14T21:00:00Z")
        self.assertEqual(reverted["revision_id"], first["revision_id"])
        self.assertNotEqual(reverted["checkpoint_token"], first["checkpoint_token"])
        stale = self.save(dict(self.observation, fingerprint="c" * 64), first["checkpoint_token"], "2026-09-14T21:30:00Z")
        self.assertEqual(stale["status"], "baseline_conflict")

    def test_invalid_or_future_observation_preserves_state(self):
        for invalid in (dict(self.observation, raw_text="private"), dict(self.observation, external_writes=1)):
            with self.assertRaises(ValueError):
                self.save(invalid)
        for at in ("2026-09-15T20:00:00Z", "2026-09-10T20:00:00Z", "2026-09-14T20:00:00"):
            with self.assertRaises(ValueError):
                self.save(at=at)
        self.assertIsNone(read_baseline(self.db, "agenda-primary"))

    def test_outer_rollback_undoes_successful_nested_checkpoint(self):
        with self.assertRaises(RuntimeError):
            with transaction(self.db):
                self.save()
                raise RuntimeError("simulated caller failure")
        self.assertIsNone(read_baseline(self.db, "agenda-primary"))

    def test_inner_rollback_preserves_outer_transaction(self):
        with transaction(self.db):
            first = self.save()
            try:
                with transaction(self.db):
                    self.save(dict(self.observation, fingerprint="b" * 64), first["checkpoint_token"], "2026-09-14T21:00:00Z")
                    raise RuntimeError("simulated inner failure")
            except RuntimeError:
                pass
            self.assertEqual(read_baseline(self.db, "agenda-primary")["revision_id"], first["revision_id"])
        self.assertIsNotNone(read_baseline(self.db, "agenda-primary"))


def submission(revision="synthetic-revision-1", **kwargs):
    record = SubmissionRevision("submission:synthetic:one", ASSIGNMENT, "learner:synthetic:one", revision, AS_OF.isoformat())
    return replace(record, **kwargs)


class SubmissionPageTests(unittest.TestCase):
    def collect(self, pages, **kwargs):
        delivered = iter(pages)
        return collect_submission_window(lambda cursor, since, until: next(delivered), class_stable_id=CLASS,
            assignment_ids=frozenset({ASSIGNMENT}), as_of=AS_OF, **kwargs)

    def test_two_pages_deduplicate_and_preserve_resubmission_history(self):
        one, two = submission(), submission("synthetic-revision-2", observed_at="2026-09-13T20:00:00Z")
        split = self.collect([SubmissionPage(CLASS, "synthetic-snapshot", (one,), "next"),
                              SubmissionPage(CLASS, "synthetic-snapshot", (one, two))])
        combined = self.collect([SubmissionPage(CLASS, "synthetic-snapshot", (two, one))])
        self.assertTrue(split.complete)
        self.assertEqual(len(split.records), 2)
        self.assertEqual(split.receipt_id, combined.receipt_id)

    def test_partial_failure_returns_no_records_or_receipt(self):
        result = self.collect([SubmissionPage(CLASS, "synthetic-snapshot", (submission(),), "missing-page")])
        self.assertFalse(result.complete)
        self.assertEqual(result.records, ())
        self.assertIsNone(result.receipt_id)
        self.assertNotIn("missing-page", repr(result))

    def test_snapshot_change_wrong_class_and_incomplete_flag_hold_all(self):
        first = SubmissionPage(CLASS, "synthetic-snapshot", (submission(),), "next")
        for page in (SubmissionPage(CLASS, "changed", ()), SubmissionPage("other-class", "synthetic-snapshot", ()),
                     SubmissionPage(CLASS, "synthetic-snapshot", (), complete=False)):
            self.assertEqual(self.collect([first, page]).records, ())
            self.assertFalse(self.collect([first, page]).complete)

    def test_repeated_cursor_and_budgets_stop(self):
        page = SubmissionPage(CLASS, "synthetic-snapshot", (submission(),), "next")
        self.assertFalse(self.collect([page, page]).complete)
        self.assertIn("submission_page_limit", self.collect([page], max_pages=1).issue_codes)
        self.assertIn("submission_record_limit", self.collect([replace(page, items=(submission(), submission()))], max_records=1).issue_codes)

    def test_identity_conflicts_wrong_assignments_and_bad_dates_block(self):
        for record in (submission(assignment_stable_id="assignment:synthetic:other"),
                       submission(observed_at="2026-09-15T20:00:00Z"),
                       submission(observed_at="2026-08-01T20:00:00Z"),
                       submission(observed_at="2026-09-14T20:00:00"),
                       submission("new", learner_stable_id="learner:synthetic:other")):
            result = self.collect([SubmissionPage(CLASS, "synthetic-snapshot", (submission(), record))])
            self.assertFalse(result.complete)
        self.assertFalse(self.collect([SubmissionPage(CLASS, "synthetic-snapshot", (
            submission(), submission(observed_at="2026-09-13T20:00:00Z")))]).complete)

    def test_empty_complete_window_is_observation_not_grade(self):
        result = self.collect([SubmissionPage(CLASS, "synthetic-snapshot", ())])
        self.assertTrue(result.complete)
        self.assertEqual(result.records, ())
        self.assertEqual(result.external_writes, 0)


def note(**kwargs):
    result = {"id": "synthetic-note-1", "content": "Synthetic discussion note", "description": "Keep observation separate.",
              "labels": ["biology"], "due": {"date": "2026-09-14"}}
    result.update(kwargs)
    return result


class DailyNotesTests(unittest.TestCase):
    def route(self, tasks, **kwargs):
        return plan_daily_notes(tuple(tasks), local_day=date(2026, 9, 14), timezone_name="America/New_York",
            class_labels={"biology": CLASS, "forensics": "class:synthetic:forensics"},
            meeting_class_ids=frozenset({CLASS}), **kwargs)

    def test_routes_by_explicit_date_and_class_and_keeps_source_unchanged(self):
        task = note()
        before = copy.deepcopy(task)
        result = self.route([task])
        self.assertEqual(len(result.notes), 1)
        self.assertEqual(task, before)
        self.assertEqual(result.notes[0].todoist_task_ids, (task["id"],))
        self.assertIsNone(result.notes[0].interpretation)
        self.assertNotIn("Synthetic discussion note", repr(result))

    def test_timezone_controls_day_without_host_timezone(self):
        self.assertEqual(len(self.route([note(due={"datetime": "2026-09-15T01:00:00Z"})]).notes), 1)
        self.assertEqual(self.route([note(due={"datetime": "2026-09-14T01:00:00Z"})]).deferred_count, 1)
        self.assertTrue(self.route([note(due={"datetime": "2026-09-14T12:00:00"})]).issues)

    def test_replay_skips_accepted_versions_and_edit_creates_new_revision(self):
        first = self.route([note(), note()])
        self.assertEqual(len(first.notes), 1)
        seen = frozenset({first.notes[0].stable_id})
        self.assertEqual(self.route([note()], seen_versions=seen).already_seen_count, 1)
        edited = self.route([note(content="Changed synthetic observation")], seen_versions=seen)
        self.assertEqual(len(edited.notes), 1)
        self.assertNotEqual(first.notes[0].stable_id, edited.notes[0].stable_id)

    def test_ambiguous_undated_or_conflicting_notes_go_to_review(self):
        for tasks in ([note(labels=[])], [note(labels=["biology", "forensics"])], [note(due=None)],
                      [note(), note(content="conflicting copy")], [note(content=123)]):
            result = self.route(tasks)
            self.assertFalse(result.notes)
            self.assertTrue(result.issues)

    def test_holiday_other_classes_old_future_and_closed_notes_not_routed(self):
        self.assertEqual(self.route([note()], no_school=True).deferred_count, 1)
        self.assertEqual(self.route([note(labels=["forensics"])]).deferred_count, 1)
        self.assertEqual(self.route([note(due={"date": "2026-09-13"})]).deferred_count, 1)
        self.assertEqual(self.route([note(due={"date": "2026-09-15"})]).deferred_count, 1)
        self.assertEqual(self.route([note(checked=True)]).closed_count, 1)


if __name__ == "__main__":
    unittest.main()
