"""Synthetic multi-assignment intake. No school sources or external writes."""
import copy
import re
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch, call

from school_workflow_hub.adapters import shared_form_intake as intake
from school_workflow_hub.adapters.intake_records import plan
from school_workflow_hub.adapters.hosted_agenda import ConnectionError


PRIMARY, ADDITIONAL = "p" * 24, "a" * 24
FORM_A, FORM_B = "f" * 24, "g" * 24
KEY_A, KEY_B = "forensics:F03-SyntheticA", "forensics:F03SyntheticB"


def url(source_id):
    return "https://docs.google.com/presentation/d/" + source_id + "/copy"


def registry():
    return {"assignments": [
        {"assignment_key": KEY_A, "class_key": "forensics", "assignment_title": "Synthetic observation"},
        {"assignment_key": KEY_B, "class_key": "forensics", "assignment_title": "Synthetic reasoning"},
    ]}


def form_url(form_id=FORM_A, *, published=False):
    return "https://docs.google.com/forms/d/" + ("e/" if published else "") + form_id + "/viewform"


def agenda(primary=None, additional=None, topic="Two synthetic assignments", form="", map_rows=None):
    sheets = []
    for course, title in intake.COURSES:
        header = [""] * 21
        header[15] = "_studentURL"
        header[20] = "_turnInURL"
        rows = [header]
        if course == "forensics":
            row = [""] * 21
            row[0], row[2] = "Wk 03", topic
            row[15], row[14] = primary or url(PRIMARY), additional or ""
            row[20] = form
            rows.append(row)
        sheets.append({"properties": {"title": title}, "data": [{"startRow": 2,
            "rowData": [{"values": [{"formattedValue": v} for v in row]} for row in rows]}]})
    if map_rows is not None:
        rows = [["assignment key", "target folder id", "course", "week"], *map_rows]
        sheets.append({"properties": {"title": "🗂 TurnIn Map"}, "data": [{"rowData": [
            {"values": [{"formattedValue": v} for v in row]} for row in rows]}]})
    return {"sheets": sheets}


def catalog(data=None, records=None, bindings=None, forms=None):
    reader = Mock()
    reader.read.return_value = data or agenda(additional=url(ADDITIONAL))
    config = {"HUB_INTAKE_DECK_ASSIGNMENTS": bindings if bindings is not None else {
        PRIMARY: KEY_A, ADDITIONAL: KEY_B}}
    if forms is not None:
        config["HUB_ASSIGNMENT_FORM_IDS"] = forms
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
            self.assertEqual(reader.read.call_args_list, [call("synthetic-agenda"),
                call("synthetic-agenda", ("'🔬 Forensics'!A3:U60",))])
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


class AssignmentFormCatalogTests(unittest.TestCase):
    def mapped_agenda(self, *, label=KEY_A, additional=None, form=None):
        return agenda(additional=additional, form=form if form is not None else form_url(),
            map_rows=[[label, "z" * 24, "🔬 Forensics", "Wk 03"]])

    def test_exact_map_key_binds_ordinary_form_and_retains_source_reference(self):
        entry = catalog(data=self.mapped_agenda())[0]
        self.assertTrue(entry["form_mapping_verified"])
        self.assertEqual(entry["form_id"], FORM_A)
        self.assertEqual(entry["form_url"], form_url())
        self.assertEqual(entry["form_source"], "agenda_u_turnin_map")
        self.assertEqual(entry["form_source_reference"], "🔬 Forensics!U4")
        self.assertEqual(entry["form_source_references"], ["🔬 Forensics!U4"])
        self.assertEqual(entry["form_review_reason"], "")
        self.assertFalse(entry["form_binding_ambiguous"])

    def test_ordinary_single_assignment_direct_u_accepts_combined_week_label(self):
        accepted = catalog(data=self.mapped_agenda(label="Synthetic observation"))[0]
        self.assertTrue(accepted["form_mapping_verified"])
        combined = catalog(data=self.mapped_agenda(label="Synthetic observation and reasoning week"))[0]
        self.assertTrue(combined["form_mapping_verified"])
        self.assertEqual(combined["form_id"], FORM_A)
        self.assertEqual(combined["form_source"], "agenda_u")

    def test_two_assignments_do_not_inherit_one_u_form_without_exact_association(self):
        entries = catalog(data=self.mapped_agenda(label="Two synthetic assignments",
                                                  additional=url(ADDITIONAL)))
        for entry in entries:
            self.assertEqual(entry["form_url"], form_url())
            self.assertEqual(entry["form_id"], "")
            self.assertTrue(entry["form_binding_ambiguous"])
            self.assertFalse(entry["form_mapping_verified"])
            self.assertEqual(entry["form_review_reason"], "assignment_form_mapping_ambiguous")

    def test_exact_map_disambiguates_only_one_assignment_in_multi_deck_row(self):
        entries = catalog(data=self.mapped_agenda(additional=url(ADDITIONAL)))
        self.assertTrue(entries[0]["form_mapping_verified"])
        self.assertEqual(entries[0]["form_id"], FORM_A)
        self.assertFalse(entries[1]["form_mapping_verified"])
        self.assertEqual(entries[1]["form_id"], "")
        self.assertEqual(entries[1]["form_review_reason"], "assignment_form_belongs_to_other_assignment")

    def test_reviewed_private_ids_resolve_each_ambiguous_assignment_separately(self):
        entries = catalog(data=self.mapped_agenda(label="Two synthetic assignments",
                                                  additional=url(ADDITIONAL)),
                          forms={KEY_A: FORM_A, KEY_B: FORM_B})
        self.assertEqual([entry["form_id"] for entry in entries], [FORM_A, FORM_B])
        for entry in entries:
            self.assertTrue(entry["form_mapping_verified"])
            self.assertEqual(entry["form_url"], form_url())  # Preserve observed U, not a constructed link.
            self.assertEqual(entry["form_source"], "private_assignment_mapping")
            self.assertTrue(entry["form_binding_ambiguous"])
            self.assertEqual(entry["form_review_reason"], "")

    def test_published_identifier_is_never_treated_as_editable_api_id(self):
        published = form_url("h" * 24, published=True)
        data = self.mapped_agenda(form=published)
        entry = catalog(data=data)[0]
        self.assertEqual(entry["form_url"], published)
        self.assertEqual(entry["form_id"], "")
        self.assertEqual(entry["form_review_reason"], "assignment_form_api_id_required")
        resolved = catalog(data=data, forms={KEY_A: FORM_A})[0]
        self.assertEqual(resolved["form_url"], published)
        self.assertEqual(resolved["form_id"], FORM_A)
        self.assertTrue(resolved["form_mapping_verified"])

    def test_ordinary_single_assignment_direct_u_needs_no_map(self):
        missing = catalog(data=agenda(form=form_url()))[0]
        self.assertEqual(missing["source_deck_id"], PRIMARY)
        self.assertTrue(missing["form_mapping_verified"])
        self.assertEqual(missing["form_id"], FORM_A)
        self.assertEqual(missing["form_source"], "agenda_u")

    def test_duplicate_map_does_not_bind_multi_assignment_row(self):
        data = agenda(form=form_url(), map_rows=[
            [KEY_A, "z" * 24, "🔬 Forensics", "Wk 03"],
            [KEY_B, "y" * 24, "🔬 Forensics", "Wk 03"]], additional=url(ADDITIONAL))
        duplicate = catalog(data=data)[0]
        self.assertFalse(duplicate["form_mapping_verified"])
        self.assertEqual(duplicate["form_review_reason"], "assignment_form_mapping_ambiguous")

    def test_wrong_course_or_week_map_does_not_bind_multi_assignment_form(self):
        for course, week in (("🌿 Biology", "Wk 03"), ("🔬 Forensics", "Wk 04")):
            with self.subTest(course=course, week=week):
                data = agenda(form=form_url(), additional=url(ADDITIONAL),
                              map_rows=[[KEY_A, "z" * 24, course, week]])
                entry = catalog(data=data)[0]
                self.assertFalse(entry["form_mapping_verified"])
                self.assertEqual(entry["form_review_reason"], "assignment_form_mapping_required")

    def test_exact_map_other_known_assignment_is_a_contradiction(self):
        entry = catalog(data=self.mapped_agenda(label=KEY_B))[0]
        self.assertFalse(entry["form_mapping_verified"])
        self.assertEqual(entry["form_review_reason"], "assignment_form_mapping_conflict")

    def test_explicit_mapping_conflicting_with_verified_agenda_form_is_held(self):
        entry = catalog(data=self.mapped_agenda(), forms={KEY_A: FORM_B})[0]
        self.assertFalse(entry["form_mapping_verified"])
        self.assertEqual(entry["form_id"], "")
        self.assertEqual(entry["form_review_reason"], "assignment_form_mapping_conflict")

    def test_bad_private_id_does_not_hide_other_assignment_or_decks(self):
        entries = catalog(forms={KEY_A: "invalid/id", KEY_B: FORM_B})
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["form_review_reason"], "assignment_form_mapping_invalid")
        self.assertFalse(entries[0]["form_mapping_verified"])
        self.assertEqual(entries[1]["form_id"], FORM_B)
        self.assertTrue(entries[1]["form_mapping_verified"])

    def test_one_private_form_cannot_be_reused_for_two_assignments(self):
        entries = catalog(forms={KEY_A: FORM_A, KEY_B: FORM_A})
        for entry in entries:
            self.assertFalse(entry["form_mapping_verified"])
            self.assertEqual(entry["form_id"], "")
            self.assertEqual(entry["form_review_reason"], "assignment_form_reused")

    def test_repeat_deck_cannot_silently_hide_conflicting_u_forms(self):
        data = self.mapped_agenda()
        course = next(s for s in data["sheets"] if s["properties"]["title"] == "🔬 Forensics")
        repeated = copy.deepcopy(course["data"][0]["rowData"][1])
        repeated["values"][20]["formattedValue"] = form_url(FORM_B)
        course["data"][0]["rowData"].append(repeated)
        entries = catalog(data=data)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["form_review_reason"], "assignment_form_mapping_conflict")
        self.assertFalse(entries[0]["form_mapping_verified"])
        self.assertEqual(entries[0]["form_source_references"], ["🔬 Forensics!U4", "🔬 Forensics!U5"])

    def test_optional_map_read_is_bounded_and_selected_class_stays_scoped(self):
        reader = Mock()
        reader.read.return_value = self.mapped_agenda()
        with patch.object(intake, "authenticated_reader", return_value=(reader, "synthetic-agenda")):
            intake.agenda_catalog(None, {"HUB_INTAKE_DECK_ASSIGNMENTS": {PRIMARY: KEY_A}},
                                  registry(), class_key="forensics")
        self.assertEqual(reader.read.call_args_list, [call("synthetic-agenda"),
            call("synthetic-agenda", ("'🔬 Forensics'!A3:U60", "'🗂 TurnIn Map'!A1:D160"))])


if __name__ == "__main__":
    unittest.main()
