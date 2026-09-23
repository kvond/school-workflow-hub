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


def named_forms(form_ids=(FORM_A, FORM_B)):
    labels = {FORM_A: "Observation — submit", FORM_B: "Reasoning — submit"}
    text, runs = "", []
    for fid in form_ids:
        if text:
            runs.append({"startIndex": len(text.encode("utf-16-le")) // 2, "format": {}})
            text += "\n"
        runs.append({"startIndex": len(text.encode("utf-16-le")) // 2,
                     "format": {"link": {"uri": form_url(fid)}}})
        text += labels.get(fid, "Other assignment — submit")
    return {"formattedValue": text, "userEnteredValue": {"stringValue": text}, "textFormatRuns": runs}


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

    def visible_link(self, data, **cell):
        sheet = next(s for s in data["sheets"] if s["properties"]["title"] == "🔬 Forensics")
        rows = sheet["data"][0]["rowData"]
        rows[0]["values"][19] = {"formattedValue": "📤 Turn it in"}
        rows[1]["values"][19] = {"formattedValue": "📤 Submit ↗", **cell}
        return data

    def test_visible_t_hyperlink_crosschecks_u_and_keeps_both_sources(self):
        data = self.visible_link(agenda(form=form_url()), hyperlink=form_url() + "?usp=sharing")
        entry = catalog(data=data)[0]
        self.assertTrue(entry["form_mapping_verified"])
        self.assertEqual(entry["form_id"], FORM_A)
        self.assertEqual(entry["form_source"], "agenda_t")
        self.assertEqual(entry["form_source_reference"], "🔬 Forensics!T4")
        self.assertEqual(entry["form_source_references"], ["🔬 Forensics!T4", "🔬 Forensics!U4"])
        self.assertEqual(entry["form_review_action"], "")

    def test_t_only_literal_formula_needs_no_hidden_url_or_private_id(self):
        data = self.visible_link(agenda(form=""), userEnteredValue={"formulaValue":
            '=HYPERLINK("' + form_url() + '","📤 Submit ↗")'})
        sheet = next(s for s in data["sheets"] if s["properties"]["title"] == "🔬 Forensics")
        sheet["data"][0]["rowData"][0]["values"][20] = {"formattedValue": ""}
        entry = catalog(data=data)[0]
        self.assertTrue(entry["form_mapping_verified"])
        self.assertEqual(entry["form_id"], FORM_A)
        self.assertEqual(entry["form_source_references"], ["🔬 Forensics!T4"])

    def test_t_u_mismatch_is_actionable_and_private_mapping_cannot_override_it(self):
        data = self.visible_link(agenda(form=form_url(FORM_B)), hyperlink=form_url(FORM_A))
        for private in (None, {KEY_A: FORM_A}, {KEY_A: FORM_B}):
            with self.subTest(private=private):
                entry = catalog(data=data, forms=private)[0]
                self.assertFalse(entry["form_mapping_verified"])
                self.assertEqual(entry["form_id"], "")
                self.assertEqual(entry["form_review_reason"], "assignment_form_turn_in_links_disagree")
                self.assertIn("T4", entry["form_review_action"])
                self.assertIn("U4", entry["form_review_action"])

    def test_one_visible_link_cannot_hide_missing_secondary_assignment_form(self):
        data = self.visible_link(self.mapped_agenda(label="Two synthetic assignments",
            additional=url(ADDITIONAL)), hyperlink=form_url())
        entries = catalog(data=data, forms={KEY_A: FORM_A, KEY_B: FORM_B})
        self.assertTrue(all(not e["form_mapping_verified"] for e in entries))
        self.assertTrue(all(e["form_review_reason"] == "assignment_form_turn_in_links_disagree" for e in entries))

    def test_ambiguous_or_computed_visible_links_do_not_fall_back_to_u(self):
        variants = [{"textFormatRuns": [{"format": {"link": {"uri": form_url()}}},
                                        {"format": {"link": {"uri": form_url(FORM_B)}}}]},
                    {"userEnteredValue": {"formulaValue": '=HYPERLINK(U4,"Submit")'}},
                    {"hyperlink": "https://example.invalid/not-a-form"},
                    {"hyperlink": form_url(), "userEnteredValue": {"formulaValue":
                        '=HYPERLINK("' + form_url(FORM_B) + '","Submit")'}}]
        for cell in variants:
            with self.subTest(cell=cell):
                entry = catalog(data=self.visible_link(agenda(form=form_url()), **cell))[0]
                self.assertFalse(entry["form_mapping_verified"])
                self.assertIn(entry["form_review_reason"], {
                    "assignment_form_turn_in_links_disagree", "assignment_form_turn_in_link_invalid"})

    def test_bad_visible_link_on_one_continuation_row_cannot_hide_behind_good_row(self):
        data = self.visible_link(agenda(form=form_url()), hyperlink=form_url())
        sheet = next(s for s in data["sheets"] if s["properties"]["title"] == "🔬 Forensics")
        later = copy.deepcopy(sheet["data"][0]["rowData"][1])
        later["values"][19]["hyperlink"] = form_url(FORM_B)
        sheet["data"][0]["rowData"].append(later)
        entry = catalog(data=data)[0]
        self.assertFalse(entry["form_mapping_verified"])
        self.assertEqual(entry["form_review_reason"], "assignment_form_turn_in_links_disagree")
        self.assertIn("T5", entry["form_review_action"])

    def test_visible_mismatch_does_not_cancel_another_assignment_binding(self):
        data = self.visible_link(agenda(form=form_url()), hyperlink=form_url(FORM_B))
        sheet = next(s for s in data["sheets"] if s["properties"]["title"] == "🔬 Forensics")
        other = copy.deepcopy(sheet["data"][0]["rowData"][1])
        other["values"][15]["formattedValue"] = url(ADDITIONAL)
        other["values"][20]["formattedValue"] = form_url(FORM_B)
        sheet["data"][0]["rowData"].append(other)
        entries = catalog(data=data)
        self.assertFalse(entries[0]["form_mapping_verified"])
        self.assertTrue(entries[1]["form_mapping_verified"])
        self.assertEqual(entries[1]["form_id"], FORM_B)

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
        entries = catalog(data=self.visible_link(self.mapped_agenda(label="Two synthetic assignments",
                                                  additional=url(ADDITIONAL)), **named_forms()),
                          forms={KEY_A: FORM_A, KEY_B: FORM_B})
        self.assertEqual([entry["form_id"] for entry in entries], [FORM_A, FORM_B])
        for entry in entries:
            self.assertTrue(entry["form_mapping_verified"])
            self.assertEqual(entry["form_url"], form_url(entry["form_id"]))
            self.assertEqual(entry["form_raw_url"], form_url(FORM_A))
            self.assertEqual(entry["form_source"], "private_assignment_mapping")
            self.assertFalse(entry["form_binding_ambiguous"])
            self.assertEqual(entry["form_review_reason"], "")

    def test_two_named_forms_bind_by_id_not_label_order_or_week_map(self):
        for ids in ((FORM_A, FORM_B), (FORM_B, FORM_A)):
            with self.subTest(ids=ids):
                data = self.visible_link(agenda(additional=url(ADDITIONAL), form=form_url()), **named_forms(ids))
                entries = catalog(data=data, forms={KEY_A: FORM_A, KEY_B: FORM_B})
                self.assertEqual([e["form_id"] for e in entries], [FORM_A, FORM_B])
                self.assertTrue(all(e["form_mapping_verified"] for e in entries))
                self.assertEqual(entries[0]["form_source_references"], ["🔬 Forensics!T4", "🔬 Forensics!U4"])
                self.assertEqual(entries[1]["form_source_references"], ["🔬 Forensics!T4"])
                self.assertEqual(entries[1]["form_visible_url"], form_url(FORM_B))
                self.assertEqual(set(entries[1]["form_visible_urls"]), {form_url(FORM_A), form_url(FORM_B)})

    def test_actual_catalog_reads_each_assignment_form_and_scopes_exact_selection(self):
        from school_workflow_hub.adapters.assignment_form_intake import collect_assignment_forms
        from tests.test_assignment_form_intake import Reader, response
        data = self.visible_link(agenda(additional=url(ADDITIONAL), form=form_url()), **named_forms((FORM_B, FORM_A)))
        entries = catalog(data=data, forms={KEY_A: FORM_A, KEY_B: FORM_B})
        uploads = {FORM_A: "x" * 24, FORM_B: "y" * 24}
        for selected in ("all", KEY_A, KEY_B):
            with self.subTest(selected=selected):
                reader = Reader({(fid, None): {"responses": [response(upload)]} for fid, upload in uploads.items()})
                refs, evidence = collect_assignment_forms(reader, entries, assignment_key=selected)
                expected = {entry["form_id"]: entry["assignment_key"] for entry in entries
                            if selected in ("all", entry["assignment_key"])}
                self.assertEqual(set(reader.calls), {(fid, None) for fid in expected})
                self.assertEqual({ref["form_id"]: ref["assignment_key"] for ref in refs.values()}, expected)
                self.assertTrue(evidence["complete"])

    def test_multiform_missing_extra_wrong_primary_or_reused_form_stays_held(self):
        variants = [((FORM_A,), FORM_A, {KEY_A: FORM_A, KEY_B: FORM_B}),
                    ((FORM_A, FORM_B, "h" * 24), FORM_A, {KEY_A: FORM_A, KEY_B: FORM_B}),
                    ((FORM_A, FORM_B), FORM_B, {KEY_A: FORM_A, KEY_B: FORM_B}),
                    ((FORM_A, FORM_B), FORM_A, {KEY_A: FORM_A, KEY_B: FORM_A}),
                    ((FORM_A, FORM_B), FORM_A, {KEY_A: FORM_A})]
        for ids, hidden, bindings in variants:
            with self.subTest(ids=ids, hidden=hidden, bindings=bindings):
                data = self.visible_link(agenda(additional=url(ADDITIONAL), form=form_url(hidden)), **named_forms(ids))
                entries = catalog(data=data, forms=bindings)
                self.assertTrue(all(not e["form_mapping_verified"] and not e["form_id"] for e in entries))
                self.assertTrue(all(e["form_review_action"] for e in entries))

    def test_multiform_requires_exact_primary_deck_binding_and_direct_form_ids(self):
        data = self.visible_link(agenda(additional=url(ADDITIONAL), topic="Synthetic observation", form=form_url()), **named_forms())
        with self.assertRaisesRegex(ConnectionError, "intake_registry_mapping_required"):
            catalog(data=data, bindings={ADDITIONAL: KEY_B}, forms={KEY_A: FORM_A, KEY_B: FORM_B})
        data = self.visible_link(agenda(additional=url(ADDITIONAL), form=form_url()), **named_forms())
        row = next(s for s in data["sheets"] if s["properties"]["title"] == "🔬 Forensics")["data"][0]["rowData"][1]
        row["values"][19]["textFormatRuns"][-1]["format"]["link"]["uri"] = form_url(FORM_B, published=True)
        entries = catalog(data=data, forms={KEY_A: FORM_A, KEY_B: FORM_B})
        self.assertTrue(all(e["form_review_reason"] == "assignment_form_api_id_required" for e in entries))

    def test_bad_multiform_continuation_cannot_hide_behind_verified_row(self):
        data = self.visible_link(agenda(additional=url(ADDITIONAL), form=form_url()), **named_forms())
        rows = next(s for s in data["sheets"] if s["properties"]["title"] == "🔬 Forensics")["data"][0]["rowData"]
        repeated = copy.deepcopy(rows[1])
        repeated["values"][19] = {"formattedValue": "Missing links"}
        rows.append(repeated)
        entries = catalog(data=data, forms={KEY_A: FORM_A, KEY_B: FORM_B})
        self.assertTrue(all(not e["form_mapping_verified"] for e in entries))
        self.assertTrue(all("T5" in e["form_review_action"] for e in entries))

    def test_multiform_rejects_editor_links_and_incompatible_formula_cells(self):
        for problem in ("editor", "formula"):
            with self.subTest(problem=problem):
                cell = named_forms()
                if problem == "editor":
                    cell["textFormatRuns"][-1]["format"]["link"]["uri"] = form_url(FORM_B).replace('/viewform', '/edit')
                else:
                    cell["userEnteredValue"] = {"formulaValue": '=HYPERLINK("' + form_url() + '","Submit")'}
                data = self.visible_link(agenda(additional=url(ADDITIONAL), form=form_url()), **cell)
                entries = catalog(data=data, forms={KEY_A: FORM_A, KEY_B: FORM_B})
                self.assertTrue(all(not e["form_mapping_verified"] for e in entries))
                self.assertTrue(all(e["form_review_reason"] == "assignment_form_turn_in_link_invalid" for e in entries))

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


class DiscoveryCatalogTests(unittest.TestCase):
    def read(self, data, records=None, config=None):
        reader = Mock()
        reader.read.return_value = data
        with patch.object(intake, "authenticated_reader", return_value=(reader, "synthetic-agenda")):
            return intake.agenda_catalog(None, config or {}, records or registry(),
                                        class_key="forensics", discovery=True)

    def test_two_new_same_week_lessons_are_distinct_without_registry_or_config_edits(self):
        data = agenda(topic="First current lesson", form=form_url())
        course = next(s for s in data["sheets"] if s["properties"]["title"] == "🔬 Forensics")
        second = copy.deepcopy(course["data"][0]["rowData"][1])
        second["values"][2]["formattedValue"] = "Second current lesson"
        second["values"][15]["formattedValue"] = url(ADDITIONAL)
        second["values"][20]["formattedValue"] = form_url(FORM_B)
        course["data"][0]["rowData"].append(second)
        result = self.read(data)
        self.assertEqual(len({e["assignment_key"] for e in result}), 2)
        self.assertEqual([e["form_id"] for e in result], [FORM_A, FORM_B])
        self.assertEqual([e["assignment_title"] for e in result], ["First current lesson", "Second current lesson"])
        for entry in result:
            self.assertTrue(entry["assignment_key"].startswith("discovery:forensics:"))
            self.assertTrue(entry["discovery_only"])
            self.assertFalse(entry["registry_mapping_verified"])
            self.assertEqual(entry["registry_assignment_key"], "")
            self.assertIn("assignment_registry_binding_required", entry["review_reasons"])
        course["data"][0]["rowData"][1]["values"][0]["formattedValue"] = "Wk 04"
        course["data"][0]["rowData"][1]["values"][2]["formattedValue"] = "Updated exact Agenda title"
        self.assertEqual(self.read(data)[0]["assignment_key"], result[0]["assignment_key"])

    def test_title_or_week_does_not_bind_new_source_to_existing_approval_key(self):
        result = self.read(agenda(topic="Synthetic observation", form=form_url()))
        self.assertNotEqual(result[0]["assignment_key"], KEY_A)
        self.assertTrue(result[0]["discovery_only"])
        bound = self.read(agenda(form=form_url()), config={"HUB_INTAKE_DECK_ASSIGNMENTS": {PRIMARY: KEY_A}})
        self.assertEqual(bound[0]["assignment_key"], KEY_A)
        self.assertFalse(bound[0]["discovery_only"])

    def test_same_master_and_form_continuing_next_week_is_one_lesson(self):
        data = agenda(topic="Current lesson", form=form_url())
        course = next(s for s in data["sheets"] if s["properties"]["title"] == "🔬 Forensics")
        continued = copy.deepcopy(course["data"][0]["rowData"][1])
        continued["values"][0]["formattedValue"] = "Wk 04"
        continued["values"][2]["formattedValue"] = "Current lesson continued"
        course["data"][0]["rowData"].append(continued)
        result = self.read(data)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source_references"], ["🔬 Forensics!P4", "🔬 Forensics!P5"])
        self.assertEqual(result[0]["form_source_references"], ["🔬 Forensics!U4", "🔬 Forensics!U5"])
        self.assertTrue(result[0]["form_mapping_verified"])
        self.assertEqual(result[0]["form_id"], FORM_A)

    def test_malformed_new_row_is_visible_gap_without_canceling_valid_form(self):
        data = agenda(form=form_url())
        course = next(s for s in data["sheets"] if s["properties"]["title"] == "🔬 Forensics")
        invalid = copy.deepcopy(course["data"][0]["rowData"][1])
        invalid["values"][15]["formattedValue"] = "https://example.invalid/not-a-student-deck"
        course["data"][0]["rowData"].append(invalid)
        result = self.read(data)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["form_mapping_verified"])
        self.assertEqual(result[0]["catalog_issues"][0]["source_reference"], "🔬 Forensics!P5")
        self.assertTrue(result[0]["catalog_issues"][0]["next_action"])

    def test_exact_saved_source_binding_needs_no_per_lesson_config(self):
        records = registry()
        records["assignments"][0]["source_url"] = url(PRIMARY)
        entry = self.read(agenda(form=form_url()), records)[0]
        self.assertEqual(entry["registry_assignment_key"], KEY_A)
        self.assertTrue(entry["registry_mapping_verified"])

    def test_additional_resource_is_not_declared_second_assignment(self):
        result = self.read(agenda(additional=url(ADDITIONAL), form=form_url()))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source_deck_id"], PRIMARY)


if __name__ == "__main__":
    unittest.main()
