"""Synthetic multi-assignment intake. No school sources or external writes."""
import copy
import re
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from school_workflow_hub.adapters import shared_form_intake as intake
from school_workflow_hub.adapters.intake_records import plan
from school_workflow_hub.adapters.hosted_agenda import ConnectionError


PRIMARY, ADDITIONAL = "p" * 24, "a" * 24
KEY_A, KEY_B = "forensics:F03-SyntheticA", "forensics:F03SyntheticB"


def url(source_id):
    return "https://docs.google.com/presentation/d/" + source_id + "/copy"


def registry():
    return {"assignments": [
        {"assignment_key": KEY_A, "class_key": "forensics", "assignment_title": "Synthetic observation"},
        {"assignment_key": KEY_B, "class_key": "forensics", "assignment_title": "Synthetic reasoning"},
    ]}


def agenda(primary=None, additional=None, topic="Two synthetic assignments"):
    sheets = []
    for course, title in intake.COURSES:
        header = [""] * 21
        header[15] = "_studentURL"
        rows = [header]
        if course == "forensics":
            row = [""] * 21
            row[0], row[2] = "Wk 03", topic
            row[15], row[14] = primary or url(PRIMARY), additional or ""
            rows.append(row)
        sheets.append({"properties": {"title": title}, "data": [{"startRow": 2,
            "rowData": [{"values": [{"formattedValue": v} for v in row]} for row in rows]}]})
    return {"sheets": sheets}


def catalog(data=None, records=None, bindings=None):
    reader = Mock()
    reader.read.return_value = data or agenda(additional=url(ADDITIONAL))
    config = {"HUB_INTAKE_DECK_ASSIGNMENTS": bindings if bindings is not None else {
        PRIMARY: KEY_A, ADDITIONAL: KEY_B}}
    with patch.object(intake, "authenticated_reader", return_value=(reader, "synthetic-agenda")):
        return intake.agenda_catalog(None, config, records if records is not None else registry())


def source_text(tag):
    return "\n".join(
        f"What distinguishes synthetic {tag} question {n} from the other examples?\n[[NOTES:{tag}:Q{n}]]"
        for n in range(3))


def person():
    return {"class_key": "forensics", "section_name": "A2 Forensics", "first": "Alex",
            "last": "Example", "learner_key": "roster:" + "a" * 64}


def workbook(rows=()):
    headers = ["record_id", "assignment_key", "section_id", "section_name", "student_id",
               "learner_key", "display_name", "source_url", "source_file_id", "source_revision",
               "status", "content_score", "revision_score", "hold_reason", "feedback", "checked_at",
               "ic_posting_status", "superseded_by"]
    return {"Assignments": {"sheet_id": 1,
                "headers": ["assignment_key", "class_key", "assignment_title"],
                "rows": list(enumerate(registry()["assignments"], 2))},
            "Submissions": {"sheet_id": 2, "headers": headers, "rows": list(enumerate(rows, 2))}}


def upload(file_id, key, version="1"):
    return {"id": file_id, "assignment_key": key, "roster_match": person(),
            "section_id": "synthetic-section", "version": version, "intake_holds": [],
            "parents": ["synthetic-shared-folder"]}


class MultiAssignmentCatalogTests(unittest.TestCase):
    def test_anatomy_and_biology_keys_are_not_replaced_by_new_week_keys(self):
        for course, prefix in (("anatomy", "A"), ("biology", "B")):
            key = course + ":" + prefix + "02"
            records = {key: {"assignment_key": key, "class_key": course,
                             "assignment_title": "Existing synthetic title"}}
            with self.assertRaisesRegex(ConnectionError, "intake_registry_mapping_required"):
                intake._catalog_assignment(course, re.fullmatch(r"(\d+)([a-z]?)", "02"),
                    "New synthetic title", PRIMARY, records, {})

    def test_selected_class_keeps_all_its_candidates_without_other_class_layouts(self):
        data = agenda(additional=url(ADDITIONAL))
        # An inaccessible unrelated course is absent from this selected read.
        data["sheets"] = [s for s in data["sheets"]
                          if s["properties"]["title"] == "🔬 Forensics"]
        reader = Mock()
        reader.read.return_value = data
        config = {"HUB_INTAKE_DECK_ASSIGNMENTS": {PRIMARY: KEY_A, ADDITIONAL: KEY_B}}
        with patch.object(intake, "authenticated_reader", return_value=(reader, "synthetic-agenda")):
            result = intake.agenda_catalog(None, config, registry(), class_key="forensics")
            self.assertEqual([row["assignment_key"] for row in result], [KEY_A, KEY_B])
            reader.read.assert_called_once_with("synthetic-agenda", ("'🔬 Forensics'!A3:U60",))
            with self.assertRaisesRegex(ConnectionError, "agenda_layout_changed"):
                intake.agenda_catalog(None, config, registry())

    def test_invalid_selected_class_is_rejected_before_reading(self):
        with patch.object(intake, "authenticated_reader") as reader:
            with self.assertRaisesRegex(ConnectionError, "invalid_submission_scope"):
                intake.agenda_catalog(None, {}, {}, class_key="unconfigured")
            reader.assert_not_called()

    def test_two_decks_one_week_keep_distinct_existing_keys_and_titles(self):
        result = catalog()
        self.assertEqual([r["assignment_key"] for r in result], [KEY_A, KEY_B])
        self.assertEqual([r["assignment_title"] for r in result],
                         [r["assignment_title"] for r in registry()["assignments"]])
        self.assertEqual({r["week"] for r in result}, {"Wk 03"})
        for entry in result:
            entry["source_text"] = source_text(entry["source_deck_id"])
        for entry in result:
            matched, error = intake.match_deck(entry["source_text"], result)
            self.assertIsNone(error)
            self.assertEqual(matched["assignment_key"], entry["assignment_key"])

    def test_unmapped_multiple_assignments_never_create_third_week_key(self):
        with self.assertRaisesRegex(ConnectionError, "intake_registry_mapping_required"):
            catalog(bindings={})

    def test_additional_link_is_not_automatically_an_assignment(self):
        for resource in (url(ADDITIONAL), "https://example.invalid/article"):
            with self.subTest(resource=resource):
                result = catalog(data=agenda(additional=resource), bindings={PRIMARY: KEY_A})
                self.assertEqual([r["assignment_key"] for r in result], [KEY_A])

    def test_single_existing_week_with_different_title_requires_binding(self):
        records = {"assignments": registry()["assignments"][:1]}
        with self.assertRaisesRegex(ConnectionError, "intake_registry_mapping_required"):
            catalog(data=agenda(), records=records, bindings={})

    def test_single_primary_retains_unambiguous_registry_key(self):
        records = {"assignments": registry()["assignments"][:1]}
        result = catalog(data=agenda(topic="Synthetic observation"), records=records, bindings={})
        self.assertEqual(result[0]["assignment_key"], KEY_A)

    def test_two_decks_cannot_collapse_to_one_key(self):
        with self.assertRaisesRegex(ConnectionError, "intake_registry_ambiguous"):
            catalog(bindings={PRIMARY: KEY_A, ADDITIONAL: KEY_A})

    def test_unknown_or_wrong_class_binding_fails_closed(self):
        with self.assertRaisesRegex(ConnectionError, "intake_registry_mapping_required"):
            catalog(bindings={PRIMARY: "forensics:unverified"})
        records = registry()
        records["assignments"][0]["class_key"] = "biology"
        with self.assertRaisesRegex(ConnectionError, "intake_registry_conflict"):
            catalog(records=records)

    def test_duplicate_registry_key_is_rejected(self):
        records = registry()
        records["assignments"].append(dict(records["assignments"][0]))
        with self.assertRaisesRegex(ConnectionError, "intake_registry_ambiguous"):
            catalog(records=records)

    def test_identical_templates_remain_ambiguous(self):
        result = catalog()
        for entry in result:
            entry["source_text"] = source_text("SAME")
        self.assertEqual(intake.match_deck(source_text("SAME"), result), (None, "deck_ambiguous"))

    def test_catalog_reader_preserves_input_and_checks_source_revision(self):
        entries = catalog()
        original = copy.deepcopy(entries)
        drive = Mock()
        drive.metadata.side_effect = lambda fid: {"id": fid, "trashed": False, "version": "1"}
        with patch.object(intake, "read_deck", side_effect=lambda _, item: source_text(item["id"])):
            enriched = intake.read_catalog_decks("synthetic-token", drive, entries)
        self.assertEqual(entries, original)
        self.assertEqual(len(enriched), 2)
        self.assertTrue(all("source_text" in row for row in enriched))
        drive.metadata.side_effect = [
            {"id": PRIMARY, "trashed": False, "version": "1"},
            {"id": PRIMARY, "trashed": False, "version": "2"},
        ]
        with patch.object(intake, "read_deck", return_value=source_text("PRIMARY")):
            with self.assertRaisesRegex(ConnectionError, "intake_source_deck_changed"):
                intake.read_catalog_decks("synthetic-token", drive, entries)

    def test_unreadable_second_catalog_deck_does_not_prove_first_unique(self):
        sources = catalog()
        class Drive:
            def __init__(self, *_):
                pass

            def metadata(self, file_id):
                return {"id": file_id, "trashed": False,
                        "mimeType": intake.FOLDER if file_id == "r" * 24 else intake.NATIVE}

        def read_deck(_, item):
            if item["id"] == ADDITIONAL:
                raise ConnectionError("intake_source_deck_unavailable")
            return source_text("PRIMARY")

        with ExitStack() as stack:
            for name, replacement in {
                "service_token": lambda *_: "synthetic-token", "FormsReader": lambda *_: object(),
                "response_file_refs": lambda *a, **kw: ({}, {"responses_checked": 0}),
                "DriveReader": Drive, "agenda_catalog": lambda *_: copy.deepcopy(sources),
                "read_deck": read_deck,
            }.items():
                stack.enter_context(patch.object(intake, name, replacement))
            with self.assertRaisesRegex(ConnectionError, "intake_source_deck_unavailable"):
                intake.collect_shared(None, {"HUB_TURNIN_FORM_ID": "f" * 24,
                                             "HUB_SUBMISSIONS_ROOT_FOLDER": "r" * 24}, {})


class RegistryHistoryTests(unittest.TestCase):
    def test_same_folder_two_assignments_do_not_merge_records(self):
        book = workbook()
        before = copy.deepcopy(book)
        files = [upload("x" * 24, KEY_A), upload("y" * 24, KEY_B)]
        requests, proposed, superseded = plan(book, catalog(), files, "2026-09-22T12:00:00Z")
        self.assertEqual(len(requests), 2)
        self.assertEqual({r["assignment_key"] for r in proposed}, {KEY_A, KEY_B})
        self.assertEqual(len({r["record_id"] for r in proposed}), 2)
        self.assertEqual(superseded, [])
        self.assertEqual(book, before)
        again, _, _ = plan(workbook(proposed), catalog(), files, "2026-09-22T13:00:00Z")
        self.assertEqual(again, [])

    def test_new_file_version_preserves_prior_reviewed_source(self):
        old = {"record_id": "reviewed-synthetic", "assignment_key": KEY_A,
               "source_file_id": "x" * 24, "source_revision": "1", "status": "assessed",
               "content_score": "7", "revision_score": "", "ic_posting_status": "verified"}
        book = workbook([old])
        before = copy.deepcopy(book)
        requests, proposed, superseded = plan(book, catalog(), [upload("y" * 24, KEY_A, "2")], "now")
        self.assertEqual(len(requests), 1)
        self.assertEqual(proposed[0]["source_revision"], "2")
        self.assertEqual(proposed[0]["content_score"], "")
        self.assertEqual(superseded, [])
        self.assertEqual(book, before)

    def test_same_file_revision_change_is_review_not_overwrite(self):
        _, rows, _ = plan(workbook(), catalog(), [upload("x" * 24, KEY_A)], "now")
        book = workbook(rows)
        before = copy.deepcopy(book)
        with self.assertRaisesRegex(ConnectionError, "intake_existing_evidence_changed"):
            plan(book, catalog(), [upload("x" * 24, KEY_A, "2")], "later")
        self.assertEqual(book, before)

    def test_registered_then_assessed_record_is_not_reset_on_refresh(self):
        files = [upload("x" * 24, KEY_A)]
        _, rows, _ = plan(workbook(), catalog(), files, "now")
        rows[0].update(status="assessed", content_score="7", ic_posting_status="verified")
        book = workbook(rows)
        before = copy.deepcopy(book)
        requests, proposed, superseded = plan(book, catalog(), files, "later")
        self.assertEqual((requests, proposed, superseded), ([], [], []))
        self.assertEqual(book, before)


if __name__ == "__main__":
    unittest.main()
