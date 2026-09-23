"""Synthetic read-only discovery evidence; no school identifiers or API calls."""
from contextlib import ExitStack
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from school_workflow_hub.adapters import submission_refresh as refresh
from school_workflow_hub.adapters import shared_form_intake as intake
from school_workflow_hub.adapters import intake_records, assessment_save
from school_workflow_hub.adapters.hosted_agenda import ConnectionError
from school_workflow_hub.adapters.roster_matching import parse_roster


ROOT = "r" * 24
FOLDER = "z" * 24
FILE_A, FILE_B, FILE_C = (c * 24 for c in "abc")
FORM_A, FORM_B = (c * 24 for c in "gh")
CHECKED = "2026-09-20T10:00:00Z"
CHANGED = "2026-09-21T10:00:00Z"


def sheet(title, first, last):
    return {"properties": {"title": title}, "data": [{"rowData": [
        {"values": [{"formattedValue": last}, {"formattedValue": first}]}]}]}


def lesson(key, family):
    text = "\n".join(
        f"Which synthetic {family} evidence distinguishes example {n} from another?\n"
        f"[[NOTES:{family}:Q{n}]]" for n in range(3))
    form_id = FORM_A if family == "observation" else FORM_B
    return {"assignment_key": key, "class_key": "forensics",
            "assignment_title": "Synthetic " + family,
            "source_deck_id": ("t" if family == "observation" else "u") * 24,
            "source_url": "https://example.invalid/" + family,
            "source_text": text, "form_id": form_id,
            "form_url": "https://docs.google.com/forms/d/" + form_id + "/viewform",
            "form_source": "agenda_u", "form_mapping_verified": True,
            "form_source_reference": "Synthetic Forensics!U6"}


class Forms:
    """Synthetic transport exercising both real Form collection layers."""
    def __init__(self, harness, error=None, errors=None):
        self.harness, self.error, self.errors = harness, error, errors or {}
        self.calls = []

    def responses(self, form_id, cursor=None):
        self.calls.append((form_id, cursor))
        error = self.errors.get(form_id) or self.error
        if error:
            raise error
        if (form_id, cursor) in self.harness.form_pages:
            return deepcopy(self.harness.form_pages[(form_id, cursor)])
        assert cursor is None
        responses = []
        for ref in list(self.harness.refs.values()) + self.harness.extra_refs:
            if ref["form_id"] != form_id:
                continue
            answers = {qid: {"textAnswers": {"answers": [{"value": v} for v in values]}}
                       for qid, values in ref["typed_answers"].items()}
            answers["upload"] = {"fileUploadAnswers": {"answers": [{
                "fileId": ref["id"], "fileName": ref["form_name"],
                "mimeType": ref["form_mime_type"]}]}}
            responses.append({"responseId": ref["response_id"],
                              "respondentEmail": ref["respondent_email"],
                              "createTime": ref["submitted_at"], "answers": answers})
        return {"responses": responses}


def file_meta(fid=FILE_A, parent=ROOT, version="1"):
    return {"id": fid, "name": "An untrusted filename", "mimeType": intake.NATIVE,
            "trashed": False, "parents": [parent], "version": version,
            "modifiedTime": CHANGED}


class Drive:
    def __init__(self, files, pages=None):
        self.items = {ROOT: {"id": ROOT, "mimeType": refresh.FOLDER, "trashed": False}}
        self.items.update({item["id"]: deepcopy(item) for item in files})
        self.pages = pages or {(tuple([ROOT]), None): {"files": deepcopy(files)}}
        self.calls = []

    def check(self):
        pass

    def form(self, form_id, *, fresh=False):
        return {"formId": form_id, "items": [{"questionItem": {"question": {
            "fileUploadQuestion": {"folderId": ROOT}}}}]}

    def read_deck(self, item):
        return intake.read_deck("synthetic-token", item)

    def metadata(self, fid):
        self.calls.append(("metadata", fid))
        if fid not in self.items:
            raise ConnectionError("submission_folder_access_required")
        return deepcopy(self.items[fid])

    def children_of(self, parents, cursor=None):
        self.calls.append(("children", tuple(parents), cursor))
        page = self.pages[(tuple(parents), cursor)]
        if isinstance(page, Exception):
            raise page
        return deepcopy(page)


class Harness:
    def __init__(self, files=None, pages=None):
        self.catalog = [lesson("forensics:F03A", "observation"),
                        lesson("forensics:F03B", "reasoning")]
        self.roster = parse_roster({"sheets": [
            sheet("A2 Forensics", "Alex", "Example"),
            sheet("A3 Forensics", "Morgan", "Sample")]})
        self.config = {"HUB_SUBMISSIONS_ROOT_FOLDER": ROOT,
                       "HUB_TURNIN_FORM_ID": "f" * 24,
                       "HUB_SCHOOL_EMAIL_DOMAINS": "school.example",
                       "HUB_ROSTER_SECTION_IDS": {"A2 Forensics": "section-alpha",
                                                  "A3 Forensics": "section-beta"},
                       "HUB_ASSIGNMENT_FORM_QUESTION_IDS": {
                           src["assignment_key"]: {"name": "n", "section": "s", "assignment": "a"}
                           for src in self.catalog},
                       # Discovery must stay read-only even with the intake writer enabled.
                       "HUB_INTAKE_WORKBOOK_WRITES": "true"}
        self.records = {"assignments": deepcopy(self.catalog), "submissions": [],
                        "checked_at": CHECKED}
        self.files = files if files is not None else [file_meta()]
        self.drive = Drive(self.files, pages)
        self.refs, self.texts = {}, {}
        self.extra_refs, self.form_pages = [], {}
        for item in self.files:
            self.route(item["id"])

    def route(self, fid, person=0, assignment=0):
        row, src = self.roster[person], self.catalog[assignment]
        self.refs[fid] = {"id": fid, "form_id": src["form_id"],
            "form_name": "Synthetic upload.pptx", "form_mime_type": intake.PPTX,
            "submitted_at": CHANGED, "response_id": "response-" + fid,
            "respondent_email":
            "s." + row["first"].lower() + "." + row["last"].lower() + "@school.example",
            "typed_answers": {"n": [row["first"] + " " + row["last"]],
                              "s": [row["section_name"]], "a": [src["assignment_key"]]}}
        self.texts[fid] = src["source_text"]

    def saved(self, fid=FILE_A, person=0, assignment=0, status="assessed", revision="1"):
        row = self.roster[person]
        return {"record_id": "synthetic-" + fid, "source_file_id": fid,
                "source_url": "https://drive.google.com/file/d/" + fid + "/view",
                "class_key": "forensics", "assignment_key": self.catalog[assignment]["assignment_key"],
                "section_id": self.config["HUB_ROSTER_SECTION_IDS"][row["section_name"]],
                "section_name": row["section_name"], "learner_key": row["learner_key"],
                "student_id": row["display_id"], "status": status, "source_revision": revision,
                "hold_reason": "", "checked_at": CHECKED, "content_score": "3",
                "revision_score": "2", "feedback": "Preserved synthetic feedback"}

    def run(self, *, assignment="all", form_error=None, form_errors=None,
            catalog_error=None, master_error=None, source_read=None, catalog_reader=None):
        before = deepcopy((self.records, self.refs, self.roster, self.files, self.config))
        forms = Forms(self, form_error, form_errors)
        with ExitStack() as stack:
            replacements = {"read_records": lambda *args: deepcopy(self.records),
                            "service_token": lambda *args: "synthetic-token",
                            "DriveReader": lambda *args: self.drive,
                            "FormsReader": lambda *args: forms,
                            "read_roster": lambda *args: deepcopy(self.roster)}
            for name, value in replacements.items():
                stack.enter_context(patch.object(refresh, name, value))
            catalog_call = stack.enter_context(patch.object(intake, "agenda_catalog",
                side_effect=catalog_error or catalog_reader, return_value=deepcopy(self.catalog)))
            stack.enter_context(patch.object(intake, "read_catalog_decks",
                side_effect=master_error or (lambda token, drive, entries, **kwargs:
                    [deepcopy(src) for src in self.catalog
                     if src["assignment_key"] in {e["assignment_key"] for e in entries}])) )
            stack.enter_context(patch.object(intake, "read_deck",
                side_effect=source_read or (lambda token, item: self.texts[item["id"]])))
            writes = [stack.enter_context(patch.object(module, name)) for module, name in
                      ((intake_records, "reconcile"), (intake_records, "_batch"),
                       (assessment_save, "save"), (assessment_save, "_batch"))]
            result = refresh.refresh("synthetic-assertion",
                {"class_key": "forensics", "assignment_key": assignment}, self.config)
            for write in writes:
                write.assert_not_called()
            self.catalog_calls = catalog_call.call_args_list
            self.form_calls = forms.calls
        assert (self.records, self.refs, self.roster, self.files, self.config) == before
        return result


class RefreshTests(unittest.TestCase):
    def test_replacement_form_keeps_exact_saved_original_outside_current_upload_tree(self):
        h = Harness([file_meta(FILE_B)])
        old_form = h.catalog[0]["form_id"]
        new_form = "n" * 24
        h.catalog[0].update(form_id=new_form,
            form_url="https://docs.google.com/forms/d/" + new_form + "/viewform")
        h.route(FILE_B, person=1)
        h.records["submissions"] = [h.saved(FILE_A)]
        h.drive.items[FILE_A] = file_meta(FILE_A, parent=FOLDER)
        h.texts[FILE_A] = h.catalog[0]["source_text"]
        result = h.run(assignment=h.catalog[0]["assignment_key"])
        rows = {row["file_id"]: row for row in result["files"]}
        self.assertEqual(set(rows), {FILE_A, FILE_B})
        self.assertEqual(rows[FILE_A]["discovery_status"], "unchanged")
        self.assertEqual(rows[FILE_A]["saved_record_ids"], [h.records["submissions"][0]["record_id"]])
        self.assertEqual(rows[FILE_B]["discovery_status"], "new_ungraded")
        self.assertEqual(rows[FILE_B]["form_ids"], [new_form])
        self.assertEqual(h.form_calls, [(new_form, None)])
        self.assertNotEqual(old_form, new_form)
        self.assertTrue(result["coverage"]["saved_sources_complete"])
        self.assertEqual(result["coverage"]["saved_sources_checked"], 1)
        self.assertFalse(any(call[0] == "children" and FOLDER in call[1] for call in h.drive.calls))

    def test_deleted_and_intentionally_trashed_saved_originals_remain_held(self):
        h = Harness([file_meta(FILE_C)])
        h.route(FILE_C, person=1)
        h.records["submissions"] = [h.saved(FILE_A), h.saved(FILE_B, status="superseded")]
        h.drive.items[FILE_A] = {**file_meta(FILE_A, parent=FOLDER), "trashed": True}
        result = h.run(assignment=h.catalog[0]["assignment_key"])
        rows = {row["file_id"]: row for row in result["files"]}
        self.assertEqual(set(rows), {FILE_A, FILE_B, FILE_C})
        self.assertIn("submission_folder_inactive", rows[FILE_A]["review_reasons"])
        self.assertIn("submission_folder_access_required", rows[FILE_B]["review_reasons"])
        self.assertIn("prior_submission_superseded", rows[FILE_B]["review_reasons"])
        for fid in (FILE_A, FILE_B):
            self.assertEqual(rows[fid]["discovery_status"], "needs_review")
            self.assertEqual(rows[fid]["version"], "")
            self.assertFalse(rows[fid]["content_read"])
        self.assertEqual(rows[FILE_A]["source_availability"], "trashed")
        self.assertEqual(rows[FILE_B]["source_availability"], "unavailable")
        self.assertEqual(rows[FILE_C]["discovery_status"], "new_ungraded")
        self.assertEqual(rows[FILE_C]["source_availability"], "active")
        self.assertTrue(rows[FILE_C]["content_read"])
        self.assertFalse(result["coverage"]["saved_sources_complete"])
        self.assertFalse(result["coverage"]["complete"])
        self.assertTrue(h.drive.items[FILE_A]["trashed"])

    def test_saved_source_reads_remain_scoped_and_do_not_follow_unrelated_parents(self):
        h = Harness([])
        h.records["submissions"] = [h.saved(FILE_A), h.saved(FILE_B, assignment=1),
                                    {**h.saved(FILE_C), "class_key": "biology"}]
        h.drive.items[FILE_A] = file_meta(FILE_A, parent=FOLDER)
        h.texts[FILE_A] = h.catalog[0]["source_text"]
        result = h.run(assignment=h.catalog[0]["assignment_key"])
        self.assertEqual([row["file_id"] for row in result["files"]], [FILE_A])
        self.assertEqual(result["coverage"]["saved_sources_checked"], 1)
        self.assertFalse(any(call[0] == "metadata" and call[1] in (FILE_B, FILE_C, FOLDER)
                             for call in h.drive.calls))

    def test_saved_source_budget_failure_is_partial_without_school_writes(self):
        h = Harness([])
        h.records["submissions"] = [h.saved(FILE_A)]
        original = h.drive.metadata
        def exhausted(fid):
            if fid == FILE_A:
                raise ConnectionError("submission_refresh_budget_exhausted")
            return original(fid)
        h.drive.metadata = exhausted
        result = h.run(assignment=h.catalog[0]["assignment_key"])
        self.assertFalse(result["coverage"]["saved_sources_complete"])
        self.assertIn("submission_refresh_budget_exhausted", result["coverage"]["errors"])
        self.assertEqual(result["files"][0]["discovery_status"], "needs_review")
        self.assertEqual(result["external_writes"], 0)

    def test_actual_catalog_replacement_form_keeps_existing_deck_bound_assignment_key(self):
        from tests.test_shared_intake_catalog import agenda, url, form_url
        h = Harness([file_meta(FILE_A), file_meta(FILE_B)])
        old_form, new_form = h.catalog[0]["form_id"], "n" * 24
        h.catalog[0].update(form_id=new_form, form_url=form_url(new_form))
        h.catalog[0]["source_url"] = url(h.catalog[0]["source_deck_id"])
        h.records["submissions"] = [h.saved(FILE_A)]
        del h.refs[FILE_A]  # The replacement has no old response history.
        h.route(FILE_B, person=1)
        h.config["HUB_INTAKE_DECK_ASSIGNMENTS"] = {
            h.catalog[0]["source_deck_id"]: h.catalog[0]["assignment_key"]}
        data = agenda(primary=h.catalog[0]["source_url"], form=form_url(new_form))
        real_catalog = intake.agenda_catalog
        reader = Mock(); reader.read.return_value = data
        with patch.object(intake, "authenticated_reader", return_value=(reader, "synthetic-agenda")):
            result = h.run(assignment=h.catalog[0]["assignment_key"], catalog_reader=real_catalog)
        self.assertEqual([entry["assignment_key"] for entry in result["assignments"]],
                         [h.catalog[0]["assignment_key"]])
        self.assertEqual(result["assignments"][0]["form_id"], new_form)
        self.assertFalse(result["assignments"][0]["discovery_only"])
        self.assertTrue(all(row["assignment_key"] == h.catalog[0]["assignment_key"]
                            for row in result["files"]))
        self.assertEqual(h.form_calls, [(new_form, None)])
        self.assertNotEqual(old_form, new_form)

    def test_old_private_form_pin_cannot_silently_override_replacement_agenda_link(self):
        from tests.test_shared_intake_catalog import agenda, url, form_url
        h = Harness([file_meta(FILE_A)])
        old_form, new_form = h.catalog[0]["form_id"], "n" * 24
        h.config.update(HUB_INTAKE_DECK_ASSIGNMENTS={h.catalog[0]["source_deck_id"]: h.catalog[0]["assignment_key"]},
                        HUB_ASSIGNMENT_FORM_IDS={h.catalog[0]["assignment_key"]: old_form})
        h.records["submissions"] = [h.saved(FILE_A)]
        real_catalog = intake.agenda_catalog
        reader = Mock(); reader.read.return_value = agenda(primary=url(h.catalog[0]["source_deck_id"]), form=form_url(new_form))
        with patch.object(intake, "authenticated_reader", return_value=(reader, "synthetic-agenda")):
            result = h.run(assignment=h.catalog[0]["assignment_key"], catalog_reader=real_catalog)
        self.assertFalse(result["coverage"]["form_complete"])
        self.assertIn("assignment_form_mapping_conflict", result["coverage"]["errors"])
        self.assertEqual(h.form_calls, [])
        self.assertEqual(result["files"][0]["saved_record_ids"], [h.records["submissions"][0]["record_id"]])

    def test_new_lesson_discovery_key_survives_form_replacement_without_registration(self):
        from tests.test_shared_intake_catalog import agenda, url, form_url
        h = Harness([])
        h.records["assignments"] = []
        old_form, new_form = h.catalog[0]["form_id"], "n" * 24
        reader = Mock(); real_catalog = intake.agenda_catalog
        with patch.object(intake, "authenticated_reader", return_value=(reader, "synthetic-agenda")):
            reader.read.return_value = agenda(primary=url(h.catalog[0]["source_deck_id"]), form=form_url(old_form))
            before = h.run(catalog_reader=real_catalog,
                           form_errors={old_form: ConnectionError("submission_form_not_found")})
            reader.read.return_value = agenda(primary=url(h.catalog[0]["source_deck_id"]), form=form_url(new_form))
            after = h.run(catalog_reader=real_catalog)
        self.assertEqual(before["assignments"][0]["assignment_key"], after["assignments"][0]["assignment_key"])
        self.assertTrue(after["assignments"][0]["discovery_only"])
        self.assertEqual(after["assignments"][0]["registry_assignment_key"], "")
        self.assertEqual(after["assignments"][0]["form_id"], new_form)
        self.assertEqual(h.form_calls, [(new_form, None)])

    def test_unbound_current_catalog_cannot_hide_exact_saved_source_in_selected_lesson(self):
        from tests.test_shared_intake_catalog import agenda, url, form_url
        h = Harness([])
        selected = h.catalog[0]["assignment_key"]
        h.records["submissions"] = [h.saved(FILE_A)]
        h.records["assignments"] = [{key: row[key] for key in ("assignment_key", "class_key", "assignment_title")}
                                    for row in h.records["assignments"]]
        h.drive.items[FILE_A] = file_meta(FILE_A, parent=FOLDER)
        h.texts[FILE_A] = h.catalog[0]["source_text"]
        def master(token, drive, entries, **kwargs):
            return [{**entry, "source_text": h.catalog[0]["source_text"]} for entry in entries]
        reader = Mock(); real_catalog = intake.agenda_catalog
        reader.read.return_value = agenda(primary=url(h.catalog[0]["source_deck_id"]), form=form_url("n" * 24))
        with patch.object(intake, "authenticated_reader", return_value=(reader, "synthetic-agenda")):
            result = h.run(assignment=selected, catalog_reader=real_catalog, master_error=master)
        self.assertEqual(len(result["files"]), 1)
        row = result["files"][0]
        self.assertEqual(row["saved_assignment_keys"], [selected])
        self.assertEqual(row["assignment_key"], "")
        self.assertEqual(row["content_assignment_key"], "")
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("intake_catalog_needs_review", row["review_reasons"])
        self.assertFalse(result["coverage"]["catalog_complete"])
        self.assertIn("assignment_form_mapping_required", result["coverage"]["errors"])

    def test_new_lesson_without_saved_rows_is_visible_but_not_registered(self):
        h = Harness([])
        entry = h.catalog[0]
        key = "discovery:forensics:" + "f" * 32
        entry.update(assignment_key=key, discovery_only=True, registry_assignment_key="",
                     registry_mapping_verified=False, review_reasons=["assignment_registry_binding_required"])
        h.records["assignments"] = []
        result = h.run(assignment=key)
        self.assertEqual(result["files"], [])
        self.assertEqual([r["assignment_key"] for r in result["assignments"]], [key])
        self.assertTrue(result["assignments"][0]["discovery_only"])
        self.assertEqual(result["external_writes"], 0)
        with self.assertRaisesRegex(ValueError, "invalid_submission_scope"):
            h.run(assignment="discovery:forensics:" + "a" * 32)

    def test_form_declared_upload_folder_is_fully_paginated_outside_root(self):
        h = Harness([])
        h.drive.items[FOLDER] = {"id": FOLDER, "mimeType": refresh.FOLDER, "trashed": False}
        first, second = file_meta(FILE_A, FOLDER), file_meta(FILE_B, FOLDER)
        h.drive.items.update({FILE_A: first, FILE_B: second})
        h.drive.pages.update({((FOLDER,), None): {"files": [first], "nextPageToken": "next"},
                              ((FOLDER,), "next"): {"files": [second]}})
        h.drive.form = lambda form_id, **kwargs: {"formId": form_id, "items": [
            {"questionItem": {"question": {"fileUploadQuestion": {"folderId": FOLDER}}}}]}
        h.route(FILE_A)
        h.route(FILE_B, person=1)
        result = h.run()
        self.assertEqual([r["file_id"] for r in result["files"]], [FILE_A, FILE_B])
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["form_refs_outside_root"], 2)
        self.assertEqual(sum(call == ("children", (FOLDER,), "next") for call in h.drive.calls), 1)
        self.assertEqual(result["external_writes"], 0)

    def test_unreadable_upload_reference_remains_visible_with_exact_form_and_fix(self):
        h = Harness([])
        h.route(FILE_A)
        result = h.run()
        row = result["files"][0]
        self.assertEqual(row["file_id"], FILE_A)
        self.assertEqual(row["assignment_key"], "forensics:F03A")
        self.assertEqual(row["version"], "")
        self.assertEqual(row["source_availability"], "unavailable")
        self.assertFalse(row["content_read"])
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("submission_folder_access_required", row["review_reasons"])
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["form_refs_unlocated"], 1)
        self.assertTrue(result["coverage"]["source_results"][-1]["next_action"])

    def test_trashed_form_folder_does_not_hide_another_forms_original_uploads(self):
        h = Harness([file_meta(FILE_B)])
        h.route(FILE_B, person=1, assignment=1)
        h.drive.items[FOLDER] = {"id": FOLDER, "mimeType": refresh.FOLDER, "trashed": True}
        h.drive.form = lambda form_id, **kwargs: {"formId": form_id, "items": [
            {"questionItem": {"question": {"fileUploadQuestion": {
                "folderId": FOLDER if form_id == FORM_A else ROOT}}}}]}
        result = h.run()
        self.assertFalse(result["coverage"]["upload_folders_complete"])
        self.assertEqual(result["coverage"]["source_results"][0]["error"], "submission_folder_inactive")
        self.assertTrue(result["coverage"]["source_results"][1]["complete"])
        self.assertEqual(result["files"][0]["file_id"], FILE_B)
        self.assertEqual(result["files"][0]["discovery_status"], "new_ungraded")
        self.assertTrue(h.drive.items[FOLDER]["trashed"])

    def test_new_source_file_keeps_discovery_registry_hold_without_saved_target(self):
        h = Harness()
        entry = h.catalog[0]
        key = "discovery:forensics:" + "f" * 32
        entry.update(assignment_key=key, discovery_only=True, registry_assignment_key="",
                     registry_mapping_verified=False, review_reasons=["assignment_registry_binding_required"])
        h.records["assignments"] = []
        h.config["HUB_ASSIGNMENT_FORM_QUESTION_IDS"] = {}
        row = h.run(assignment=key)["files"][0]
        self.assertEqual(row["assignment_key"], key)
        self.assertTrue(row["discovery_only"])
        self.assertEqual(row["registry_assignment_key"], "")
        self.assertIn("assignment_registry_binding_required", row["review_reasons"])
        self.assertEqual(row["discovery_status"], "needs_review")

    def test_unavailable_master_does_not_cancel_other_forms_content_evidence(self):
        h = Harness([file_meta(FILE_A), file_meta(FILE_B)])
        h.route(FILE_B, person=1, assignment=1)
        def read(token, drive, entries, **kwargs):
            if entries[0]["assignment_key"] == h.catalog[0]["assignment_key"]:
                raise ConnectionError("intake_source_deck_unavailable")
            return [deepcopy(h.catalog[1])]
        result = h.run(master_error=read)
        self.assertFalse(result["coverage"]["catalog_complete"])
        self.assertEqual(result["files"][1]["content_assignment_key"], "forensics:F03B")
        self.assertEqual(result["files"][1]["discovery_status"], "new_ungraded")
        self.assertEqual(result["files"][0]["discovery_status"], "needs_review")

    def test_form_structure_change_during_discovery_holds_only_its_uploads(self):
        other_folder = "y" * 24
        items = [file_meta(FILE_A, FOLDER), file_meta(FILE_B, other_folder)]
        folders = [{"id": fid, "mimeType": refresh.FOLDER, "parents": [ROOT], "trashed": False}
                   for fid in (FOLDER, other_folder)]
        h = Harness(items, {((ROOT,), None): {"files": folders},
                            ((FOLDER, other_folder), None): {"files": items}})
        h.route(FILE_B, person=1, assignment=1)
        def changed(form_id, *, fresh=False):
            return {"formId": form_id,
                    "revisionId": "changed" if fresh and form_id == FORM_A else "original",
                    "items": [{"questionItem": {"question": {"fileUploadQuestion": {
                        "folderId": FOLDER if form_id == FORM_A else other_folder}}}}]}
        h.drive.form = changed
        result = h.run()
        self.assertFalse(result["coverage"]["complete"])
        self.assertIn("submission_form_changed", result["files"][0]["review_reasons"])
        self.assertEqual(result["files"][1]["discovery_status"], "new_ungraded")
        self.assertNotIn("form_snapshot", repr(result))

    def test_two_assignment_forms_are_fully_read_without_a_shared_form(self):
        h = Harness([file_meta(FILE_A), file_meta(FILE_B)])
        h.route(FILE_B, assignment=1)
        last_page = Forms(h).responses(FORM_A)
        h.form_pages = {(FORM_A, None): {"responses": [], "nextPageToken": "next"},
                        (FORM_A, "next"): last_page}
        h.config.pop("HUB_TURNIN_FORM_ID")
        result = h.run()
        self.assertEqual(h.form_calls, [(FORM_A, None), (FORM_A, "next"), (FORM_B, None)])
        self.assertEqual(result["coverage"]["forms_complete"], 2)
        self.assertEqual(result["coverage"]["responses_checked"], 2)
        self.assertEqual([row["form_ids"] for row in result["files"]], [[FORM_A], [FORM_B]])
        self.assertEqual([row["assignment_key"] for row in result["files"]],
                         ["forensics:F03A", "forensics:F03B"])
        self.assertTrue(result["coverage"]["complete"])

    def test_one_unavailable_form_keeps_other_assignments_verified_evidence(self):
        h = Harness([file_meta(FILE_A), file_meta(FILE_B)])
        h.route(FILE_B, assignment=1)
        result = h.run(form_errors={FORM_A: ConnectionError("submission_form_access_required")})
        self.assertEqual(h.form_calls, [(FORM_A, None), (FORM_B, None)])
        self.assertFalse(result["coverage"]["form_complete"])
        self.assertEqual(result["coverage"]["forms_complete"], 1)
        rows = {row["file_id"]: row for row in result["files"]}
        self.assertEqual(rows[FILE_A]["student_id"], "")
        self.assertIn("form_identity_unavailable", rows[FILE_A]["review_reasons"])
        self.assertEqual(rows[FILE_B]["assignment_key"], "forensics:F03B")
        self.assertEqual(rows[FILE_B]["form_ids"], [FORM_B])
        self.assertEqual(rows[FILE_B]["discovery_status"], "new_ungraded")

    def test_selected_assignment_does_not_depend_on_unrelated_form_access(self):
        h = Harness([file_meta(FILE_A), file_meta(FILE_B)])
        h.route(FILE_B, assignment=1)
        result = h.run(assignment="forensics:F03A",
                       form_errors={FORM_B: ConnectionError("submission_form_access_required")})
        self.assertEqual(h.form_calls, [(FORM_A, None)])
        self.assertTrue(result["coverage"]["form_complete"])
        self.assertTrue(result["coverage"]["complete"])
        rows = {row["file_id"]: row for row in result["files"]}
        self.assertEqual(rows[FILE_A]["discovery_status"], "new_ungraded")
        # Its other Form/master was deliberately not read. Keep the root-visible
        # file unresolved rather than guessing another lesson from its filename.
        self.assertEqual(rows[FILE_B]["assignment_key"], "")
        self.assertEqual(rows[FILE_B]["discovery_status"], "needs_review")

    def test_selected_lesson_never_reads_unrelated_unavailable_master(self):
        h = Harness()
        selected = h.catalog[0]["assignment_key"]
        calls = []
        def master(token, drive, entries, **kwargs):
            calls.extend(entry["assignment_key"] for entry in entries)
            if any(entry["assignment_key"] != selected for entry in entries):
                raise ConnectionError("intake_source_deck_unavailable")
            return [deepcopy(h.catalog[0])]
        result = h.run(assignment=selected, master_error=master)
        self.assertEqual(calls, [selected])
        self.assertTrue(result["coverage"]["catalog_complete"])
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual([row["assignment_key"] for row in result["coverage"]["catalog_results"]], [selected])
        self.assertEqual(result["files"][0]["discovery_status"], "new_ungraded")

    def test_selected_editable_form_does_not_resolve_unrelated_future_published_forms(self):
        h = Harness()
        h.catalog[1].update(form_id="", form_mapping_verified=False,
            form_url="https://docs.google.com/forms/d/e/" + "q" * 24 + "/viewform",
            form_review_reason="assignment_form_api_id_required")
        h.drive.pages[((ROOT,), None)]["files"].append({"id": FORM_B, "mimeType": refresh.FORM,
                                                        "parents": [ROOT], "trashed": False})
        calls = []
        original = h.drive.form
        def body(form_id, **kwargs):
            calls.append(form_id)
            if form_id != FORM_A:
                raise ConnectionError("submission_form_access_required")
            return original(form_id, **kwargs)
        h.drive.form = body
        result = h.run(assignment=h.catalog[0]["assignment_key"])
        self.assertEqual(set(calls), {FORM_A})
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["files"][0]["discovery_status"], "new_ungraded")

    def test_selected_form_still_detects_same_form_published_alias_on_other_lesson(self):
        h = Harness()
        published = "https://docs.google.com/forms/d/e/" + "q" * 24 + "/viewform"
        h.catalog[1].update(form_id="", form_mapping_verified=False,
            form_url=published, form_review_reason="assignment_form_api_id_required")
        original = h.drive.form
        h.drive.form = lambda form_id, **kwargs: {**original(form_id, **kwargs), "responderUri": published}
        result = h.run(assignment=h.catalog[0]["assignment_key"])
        self.assertEqual(h.form_calls, [])
        self.assertFalse(result["coverage"]["form_complete"])
        self.assertIn("assignment_form_mapping_conflict", result["coverage"]["errors"])

    def test_selected_lesson_ignores_unrelated_future_catalog_issue_but_all_keeps_it(self):
        h = Harness()
        h.catalog[0]["source_references"] = ["Synthetic course!P4"]
        issue = {"source_reference": "Synthetic course!P50", "error": "intake_source_deck_unavailable",
                 "next_action": "Check that future source."}
        for entry in h.catalog:
            entry["catalog_issues"] = [issue]
        selected = h.run(assignment=h.catalog[0]["assignment_key"])
        self.assertEqual(selected["coverage"]["catalog_issues"], [])
        self.assertTrue(selected["coverage"]["complete"])
        all_lessons = h.run()
        self.assertEqual(all_lessons["coverage"]["catalog_issues"], [issue])
        self.assertFalse(all_lessons["coverage"]["complete"])

    def test_selected_current_form_retains_saved_other_lesson_conflict_without_other_master(self):
        h = Harness()
        h.records["submissions"] = [h.saved(assignment=1)]
        selected = h.catalog[0]["assignment_key"]
        def master(token, drive, entries, **kwargs):
            self.assertEqual([entry["assignment_key"] for entry in entries], [selected])
            return [deepcopy(h.catalog[0])]
        row = h.run(assignment=selected, master_error=master)["files"][0]
        self.assertEqual(row["assignment_key"], selected)
        self.assertEqual(row["saved_assignment_keys"], [h.catalog[1]["assignment_key"]])
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("saved_assignment_conflict", row["review_reasons"])

    def test_selected_lesson_skips_saved_other_lesson_source_without_current_form_reference(self):
        h = Harness([file_meta(FILE_A), file_meta(FILE_B)])
        h.route(FILE_B, assignment=1)
        h.records["submissions"] = [h.saved(FILE_B, assignment=1)]
        h.drive.items[FOLDER] = {"id": FOLDER, "mimeType": refresh.FOLDER, "trashed": False}
        h.drive.pages[((FOLDER,), None)] = {"files": []}
        h.drive.form = lambda form_id, **kwargs: {"formId": form_id, "items": [
            {"questionItem": {"question": {"fileUploadQuestion": {"folderId": FOLDER}}}}]}
        calls = []
        def source(token, item):
            calls.append(item["id"])
            return h.texts[item["id"]]
        result = h.run(assignment=h.catalog[0]["assignment_key"], source_read=source)
        self.assertEqual(calls, [FILE_A])
        self.assertEqual([row["file_id"] for row in result["files"]], [FILE_A])

    def test_moved_import_in_selected_form_folder_retains_other_saved_lesson_conflict(self):
        h = Harness()
        h.refs.clear()
        h.records["submissions"] = [h.saved(assignment=1)]
        row = h.run(assignment="forensics:F03A")["files"][0]
        self.assertEqual(row["assignment_key"], "forensics:F03A")
        self.assertEqual(row["saved_assignment_keys"], ["forensics:F03B"])
        self.assertIn("saved_assignment_conflict", row["review_reasons"])
        self.assertEqual(row["discovery_status"], "needs_review")

    def test_moved_import_in_selected_form_folder_retains_cross_class_identity_hold(self):
        h = Harness()
        h.refs.clear()
        h.roster[0]["class_key"] = "biology"
        saved = h.saved()
        saved.update(class_key="biology", assignment_key="biology:synthetic")
        h.records["submissions"] = [saved]
        row = h.run(assignment="forensics:F03A")["files"][0]
        self.assertEqual(row["class_key"], "forensics")
        self.assertEqual(row["assignment_key"], "forensics:F03A")
        self.assertEqual(row["student_id"], "")
        self.assertEqual(row["section_id"], "")
        self.assertIn("roster_form_class_disagreement", row["review_reasons"])
        self.assertIn("saved_assignment_conflict", row["review_reasons"])

    def test_imported_saved_identity_is_held_if_final_form_folder_readback_changes_or_fails(self):
        for error in (None, "submission_form_access_required"):
            with self.subTest(error=error):
                h = Harness()
                h.refs.clear()
                h.records["submissions"] = [h.saved()]
                def form(form_id, *, fresh=False):
                    if fresh and error:
                        raise ConnectionError(error)
                    return {"formId": form_id, "items": [{"questionItem": {"question": {
                        "fileUploadQuestion": {"folderId": FOLDER if fresh else ROOT}}}}]}
                h.drive.form = form
                result = h.run(assignment="forensics:F03A")
                row = result["files"][0]
                self.assertEqual(row["content_assignment_key"], "forensics:F03A")
                self.assertEqual(row["form_ids"], [])
                self.assertEqual(row["discovery_status"], "needs_review")
                self.assertIn(error or "submission_form_changed", row["review_reasons"])
                self.assertFalse(result["coverage"]["complete"])
                self.assertFalse(result["coverage"]["upload_folders_complete"])

    def test_missing_assignment_binding_does_not_fall_back_to_global_form(self):
        h = Harness()
        h.catalog[0].update(form_mapping_verified=False, form_id="",
                            form_review_reason="assignment_form_mapping_required")
        result = h.run()
        self.assertEqual(h.form_calls, [(FORM_B, None)])
        self.assertNotEqual(FORM_B, h.config["HUB_TURNIN_FORM_ID"])
        self.assertFalse(result["coverage"]["form_complete"])
        self.assertIn("assignment_form_mapping_required", result["coverage"]["errors"])
        row = result["files"][0]
        self.assertEqual(row["form_ids"], [])
        self.assertEqual(row["student_id"], "")
        self.assertEqual(row["discovery_status"], "needs_review")

    def test_weekly_turn_in_link_repair_reaches_form_coverage(self):
        h = Harness()
        action = "Verify the visible Turn it in link at Synthetic course!T6 and raw link at U6; both must identify the same assignment Form."
        h.catalog[0].update(form_mapping_verified=False, form_id="",
                            form_review_reason="assignment_form_turn_in_links_disagree",
                            form_review_action=action)
        result = h.run()
        self.assertEqual(h.form_calls, [(FORM_B, None)])
        held = result["coverage"]["form_results"][0]
        self.assertEqual(held["next_action"], action)
        self.assertFalse(held["complete"])
        self.assertEqual(result["external_writes"], 0)

    def test_unavailable_master_retains_exact_form_assignment_and_identity_with_hold(self):
        h = Harness([file_meta(FILE_A), file_meta(FILE_B)])
        h.route(FILE_B, assignment=1)
        result = h.run(master_error=ConnectionError("intake_source_deck_unavailable"))
        self.assertTrue(result["coverage"]["form_complete"])
        self.assertFalse(result["coverage"]["catalog_complete"])
        self.assertEqual([row["assignment_key"] for row in result["files"]],
                         ["forensics:F03A", "forensics:F03B"])
        for row in result["files"]:
            self.assertEqual(row["section_id"], "section-alpha")
            self.assertEqual(row["content_assignment_key"], "")
            self.assertEqual(row["discovery_status"], "needs_review")
            self.assertIn("intake_catalog_needs_review", row["review_reasons"])

    def test_form_assignment_disagreement_with_content_is_never_relabelled(self):
        h = Harness()
        h.texts[FILE_A] = h.catalog[1]["source_text"]
        row = h.run()["files"][0]
        self.assertEqual(row["assignment_key"], "forensics:F03A")
        self.assertEqual(row["form_assignment_keys"], ["forensics:F03A"])
        self.assertEqual(row["content_assignment_key"], "forensics:F03B")
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("form_deck_assignment_disagreement", row["review_reasons"])

    def test_selected_form_file_from_other_class_remains_visible_but_identity_is_held(self):
        h = Harness()
        h.roster.extend(parse_roster({"sheets": [sheet("A4 Biology", "Taylor", "Other")]}))
        h.config["HUB_ROSTER_SECTION_IDS"]["A4 Biology"] = "section-biology"
        h.route(FILE_A, person=2)
        result = h.run(assignment="forensics:F03A")
        self.assertEqual(len(result["files"]), 1)
        row = result["files"][0]
        self.assertEqual(row["assignment_key"], "forensics:F03A")
        self.assertEqual(row["student_id"], "")
        self.assertEqual(row["section_id"], "")
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("roster_form_class_disagreement", row["review_reasons"])

    def test_exact_form_class_scopes_same_student_in_two_different_courses(self):
        h = Harness()
        h.roster.extend(parse_roster({"sheets": [sheet("A4 Biology", "Alex", "Example")]}))
        h.config["HUB_ROSTER_SECTION_IDS"]["A4 Biology"] = "section-biology"
        row = h.run()["files"][0]
        self.assertEqual(row["assignment_key"], "forensics:F03A")
        self.assertEqual(row["section_id"], "section-alpha")
        self.assertEqual(row["discovery_status"], "new_ungraded")

    def test_same_student_in_two_sections_of_form_class_stays_ambiguous(self):
        h = Harness()
        h.roster.extend(parse_roster({"sheets": [sheet("A3 Forensics", "Alex", "Example")]}))
        row = h.run()["files"][0]
        self.assertEqual(row["assignment_key"], "forensics:F03A")
        self.assertEqual(row["student_id"], "")
        self.assertEqual(row["section_id"], "")
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("roster_identity_ambiguous", row["review_reasons"])

    def test_file_claimed_by_two_assignment_forms_preserves_conflicting_evidence(self):
        h = Harness()
        ref = deepcopy(h.refs[FILE_A])
        ref.update(form_id=FORM_B, response_id="response-second-form")
        h.extra_refs.append(ref)
        row = h.run()["files"][0]
        self.assertEqual(row["assignment_key"], "")
        self.assertEqual(row["form_assignment_keys"], ["forensics:F03A", "forensics:F03B"])
        self.assertEqual(row["form_ids"], [FORM_A, FORM_B])
        self.assertEqual(row["content_assignment_key"], "forensics:F03A")
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("form_assignment_conflict", row["review_reasons"])

    def test_form_without_typed_assignment_question_still_matches_exact_binding(self):
        h = Harness()
        h.config["HUB_ASSIGNMENT_FORM_QUESTION_IDS"]["forensics:F03A"] = {"name": "n", "section": "s"}
        del h.refs[FILE_A]["typed_answers"]["a"]
        row = h.run()["files"][0]
        self.assertEqual(row["assignment_key"], "forensics:F03A")
        self.assertEqual(row["discovery_status"], "new_ungraded")
        self.assertEqual(row["review_reasons"], [])

    def test_new_file_is_discovered_on_later_page_and_nested_folder(self):
        known, new = file_meta(), file_meta(FILE_B, parent=FOLDER)
        folder = {"id": FOLDER, "mimeType": refresh.FOLDER, "parents": [ROOT], "trashed": False}
        pages = {((ROOT,), None): {"files": [known], "nextPageToken": "later"},
                 ((ROOT,), "later"): {"files": [folder]},
                 ((FOLDER,), None): {"files": [new]}}
        h = Harness([known, new], pages)
        h.records["submissions"] = [h.saved()]
        h.route(FILE_B, person=1)
        result = h.run()
        self.assertEqual({row["file_id"] for row in result["files"]}, {FILE_A, FILE_B})
        self.assertEqual(result["coverage"]["pages_checked"], 3)
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["files"][1]["discovery_status"], "new_ungraded")
        self.assertEqual((result["external_writes"], result["live_model_calls"]), (0, 0))
        self.assertTrue(all(call.kwargs == {"class_key": "forensics", "discovery": True}
                            for call in h.catalog_calls))

    def test_same_week_assignments_and_sections_remain_distinct(self):
        h = Harness([file_meta(FILE_A), file_meta(FILE_B), file_meta(FILE_C)])
        h.route(FILE_B, assignment=1)
        h.route(FILE_C, person=1)
        rows = h.run()["files"]
        self.assertEqual([(r["assignment_key"], r["section_id"]) for r in rows], [
            ("forensics:F03A", "section-alpha"), ("forensics:F03B", "section-alpha"),
            ("forensics:F03A", "section-beta")])
        self.assertEqual({r["discovery_status"] for r in rows}, {"new_ungraded"})
        selected = h.run(assignment="forensics:F03B")["files"]
        self.assertEqual({r["file_id"] for r in selected if r["assignment_key"]}, {FILE_B})
        self.assertTrue(all(r["discovery_status"] == "needs_review" for r in selected if r["file_id"] != FILE_B))

    def test_current_drive_revision_flags_change_without_rewriting_assessment(self):
        h = Harness([file_meta(version="2")])
        h.records["submissions"] = [h.saved(revision="1")]
        row = h.run()["files"][0]
        self.assertEqual(row["discovery_status"], "changed_since_assessment")
        self.assertTrue(row["source_changed_since_assessment"])
        self.assertEqual(row["saved_record_ids"], [h.records["submissions"][0]["record_id"]])

    def test_equal_verified_drive_revision_is_unchanged(self):
        h = Harness()
        h.records["submissions"] = [h.saved()]
        row = h.run()["files"][0]
        self.assertEqual(row["discovery_status"], "unchanged")
        self.assertFalse(row["source_changed_since_assessment"])

    def test_legacy_hash_is_not_treated_as_a_drive_version(self):
        h = Harness()
        h.records["submissions"] = [h.saved(revision="sha256:synthetic-content-hash")]
        h.records["submissions"][0]["checked_at"] = CHANGED
        row = h.run()["files"][0]
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("assessment_revision_unverified", row["review_reasons"])

    def test_form_unavailable_keeps_discovered_content_with_identity_held(self):
        h = Harness()
        result = h.run(form_error=ConnectionError("forms_read_forbidden"))
        self.assertFalse(result["coverage"]["complete"])
        self.assertFalse(result["coverage"]["form_complete"])
        row = result["files"][0]
        self.assertEqual(row["assignment_key"], "forensics:F03A")
        self.assertEqual(row["student_id"], "")
        self.assertIn("form_identity_unavailable", row["review_reasons"])
        self.assertEqual(row["discovery_status"], "needs_review")

    def test_catalog_unavailable_cannot_invent_form_binding_or_identity(self):
        h = Harness()
        result = h.run(catalog_error=ConnectionError("intake_catalog_needs_review"))
        self.assertFalse(result["coverage"]["catalog_complete"])
        row = result["files"][0]
        self.assertEqual(row["file_id"], FILE_A)
        self.assertEqual(row["assignment_key"], "")
        self.assertEqual(row["section_id"], "")
        self.assertIn("intake_catalog_needs_review", row["review_reasons"])
        self.assertIn("form_identity_unavailable", row["review_reasons"])
        self.assertEqual(h.form_calls, [])

    def test_collected_email_conflict_holds_saved_identity(self):
        h = Harness()
        h.records["submissions"] = [h.saved(person=0)]
        h.route(FILE_A, person=1)
        row = h.run()["files"][0]
        self.assertEqual(row["student_id"], "")
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("saved_identity_conflict", row["review_reasons"])

    def test_saved_assignment_conflict_cannot_relabel_prior_grade(self):
        h = Harness()
        h.records["submissions"] = [h.saved(assignment=1)]
        row = h.run()["files"][0]
        self.assertEqual(row["assignment_key"], "forensics:F03A")
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("saved_assignment_conflict", row["review_reasons"])

    def test_superseded_file_is_retained_and_not_a_new_competing_submission(self):
        h = Harness([file_meta(FILE_A), file_meta(FILE_B)])
        h.records["submissions"] = [h.saved(FILE_A, status="superseded")]
        rows = h.run()["files"]
        self.assertEqual([r["file_id"] for r in rows], [FILE_A, FILE_B])
        self.assertIn("prior_submission_superseded", rows[0]["review_reasons"])
        self.assertEqual(rows[0]["discovery_status"], "needs_review")
        self.assertEqual(rows[0]["saved_record_ids"], [h.records["submissions"][0]["record_id"]])
        self.assertEqual(rows[1]["discovery_status"], "new_ungraded")

    def test_two_files_for_same_learner_and_assignment_are_both_retained(self):
        rows = Harness([file_meta(FILE_A), file_meta(FILE_B)]).run()["files"]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["discovery_status"], "needs_review")
            self.assertIn("competing_submission_versions", row["review_reasons"])
            self.assertEqual(row["related_file_ids"], [FILE_B if row["file_id"] == FILE_A else FILE_A])

    def test_resubmission_groups_by_learner_not_native_or_roster_display_id(self):
        h = Harness([file_meta(FILE_A), file_meta(FILE_B)])
        saved = h.saved()
        saved["student_id"] = "synthetic-native-student-id"
        h.records["submissions"] = [saved]
        rows = h.run()["files"]
        self.assertEqual(rows[0]["student_id"], "synthetic-native-student-id")
        self.assertEqual(rows[1]["student_id"], h.roster[0]["display_id"])
        self.assertNotEqual(rows[0]["student_id"], rows[1]["student_id"])
        self.assertEqual(rows[0]["learner_key"], rows[1]["learner_key"])
        for row in rows:
            self.assertEqual(row["discovery_status"], "needs_review")
            self.assertIn("competing_submission_versions", row["review_reasons"])

    def test_existing_saved_hold_survives_current_matching_sources(self):
        h = Harness()
        saved = h.saved()
        saved["hold_reason"] = "Synthetic private reason retained only in the saved record"
        h.records["submissions"] = [saved]
        row = h.run()["files"][0]
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("saved_record_needs_review", row["review_reasons"])
        self.assertNotIn(saved["hold_reason"], repr(row))

    def test_file_change_during_read_retains_form_binding_but_holds_content(self):
        h = Harness()
        def changed(token, item):
            h.drive.items[item["id"]]["version"] = "2"
            return h.texts[item["id"]]
        row = h.run(source_read=changed)["files"][0]
        self.assertEqual(row["assignment_key"], "forensics:F03A")
        self.assertEqual(row["content_assignment_key"], "")
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("intake_upload_changed", row["review_reasons"])
        self.assertEqual(row["source_availability"], "active")
        self.assertFalse(row["content_read"])

    def test_export_failure_does_not_claim_readable_content_from_active_metadata(self):
        h = Harness()
        row = h.run(source_read=ConnectionError("uploaded_deck_unreadable"))["files"][0]
        self.assertEqual(row["source_availability"], "active")
        self.assertFalse(row["content_read"])
        self.assertIn("uploaded_deck_unreadable", row["review_reasons"])

    def test_latest_source_metadata_controls_availability_after_content_read(self):
        for trashed in (True, None):
            with self.subTest(trashed=trashed):
                h = Harness()
                def changed(token, item):
                    if trashed:
                        h.drive.items[item["id"]]["trashed"] = True
                    else:
                        del h.drive.items[item["id"]]
                    return h.texts[item["id"]]
                row = h.run(source_read=changed)["files"][0]
                self.assertEqual(row["source_availability"], "trashed" if trashed else "unavailable")
                self.assertFalse(row["content_read"])
                self.assertEqual(row["discovery_status"], "needs_review")

    def test_trashed_current_form_reference_is_not_a_readable_or_historical_submission(self):
        h = Harness([])
        h.route(FILE_A)
        h.drive.items[FILE_A] = {**file_meta(), "trashed": True}
        row = h.run()["files"][0]
        self.assertEqual(row["source_availability"], "trashed")
        self.assertFalse(row["content_read"])
        self.assertEqual(row["saved_record_ids"], [])
        self.assertTrue(h.drive.items[FILE_A]["trashed"])

    def test_imported_deck_is_read_when_get_omits_listed_parent(self):
        h = Harness()
        h.refs.clear()  # An imported deck has no current Form response.
        metadata = h.drive.metadata
        def viewer_metadata(fid):
            result = metadata(fid)
            if fid == FILE_A:
                result.pop("parents", None)
            return result
        h.drive.metadata = viewer_metadata
        read = Mock(side_effect=lambda token, item: h.texts[item["id"]])
        result = h.run(assignment="forensics:F03A", source_read=read)
        row = result["files"][0]
        self.assertEqual(read.call_count, 1)
        self.assertEqual(row["content_assignment_key"], "forensics:F03A")
        self.assertEqual(row["review_reasons"], ["form_identity_unavailable"])
        self.assertEqual(row["section_id"], "")
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertEqual(row["source_availability"], "active")
        self.assertTrue(row["content_read"])
        self.assertTrue(result["coverage"]["drive_complete"])
        self.assertEqual(h.files[0]["parents"], [ROOT])

    def test_missing_get_parent_does_not_hide_changed_file_evidence(self):
        for field, value in (("id", FILE_B), ("version", "2"),
                             ("modifiedTime", CHECKED), ("trashed", True),
                             ("mimeType", intake.PPTX), ("name", "Changed name")):
            with self.subTest(field=field):
                h = Harness()
                h.refs.clear()
                h.drive.items[FILE_A].pop("parents")
                h.drive.items[FILE_A][field] = value
                read = Mock()
                row = h.run(assignment="forensics:F03A", source_read=read)["files"][0]
                read.assert_not_called()
                self.assertEqual(row["content_assignment_key"], "")
                self.assertIn("intake_upload_changed", row["review_reasons"])

    def test_observed_parent_change_during_deck_read_is_held(self):
        h = Harness()
        def moved(token, item):
            h.drive.items[item["id"]]["parents"] = [FOLDER]
            return h.texts[item["id"]]
        row = h.run(source_read=moved)["files"][0]
        self.assertEqual(row["content_assignment_key"], "")
        self.assertIn("intake_upload_changed", row["review_reasons"])

    def test_parent_visibility_change_during_read_is_not_a_revision_change(self):
        h = Harness()
        h.refs.clear()
        def hidden_parent(token, item):
            h.drive.items[item["id"]].pop("parents")
            return h.texts[item["id"]]
        row = h.run(assignment="forensics:F03A", source_read=hidden_parent)["files"][0]
        self.assertEqual(row["content_assignment_key"], "forensics:F03A")
        self.assertEqual(row["review_reasons"], ["form_identity_unavailable"])

    def test_upload_budget_exhaustion_preserves_partial_review_preview(self):
        h = Harness([file_meta(FILE_A), file_meta(FILE_B)])
        def limited_read(token, item):
            if item["id"] == FILE_B:
                raise ConnectionError("submission_refresh_budget_exhausted")
            return h.texts[item["id"]]
        result = h.run(source_read=limited_read)
        self.assertFalse(result["coverage"]["complete"])
        self.assertIn("submission_refresh_budget_exhausted", result["coverage"]["errors"])
        self.assertEqual([row["file_id"] for row in result["files"]], [FILE_A, FILE_B])
        self.assertEqual(result["files"][0]["assignment_key"], "forensics:F03A")
        self.assertEqual(result["files"][1]["assignment_key"], "forensics:F03A")
        self.assertEqual(result["files"][1]["content_assignment_key"], "")
        self.assertIn("submission_refresh_budget_exhausted", result["files"][1]["review_reasons"])
        self.assertEqual(result["external_writes"], 0)

    def test_selected_form_upload_and_active_history_read_before_unknown_root_files(self):
        folder = {"id": FOLDER, "mimeType": refresh.FOLDER, "parents": [ROOT], "trashed": False}
        items = [file_meta(FILE_A), file_meta(FILE_B), file_meta(FILE_C, FOLDER)]
        h = Harness(items, {((ROOT,), None): {"files": items[:2] + [folder]},
                            ((FOLDER,), None): {"files": [items[2]]}})
        h.drive.form = lambda form_id, **kwargs: {"formId": form_id, "items": [
            {"questionItem": {"question": {"fileUploadQuestion": {"folderId": FOLDER}}}}]}
        h.refs.pop(FILE_A)
        h.refs.pop(FILE_B)
        h.records["submissions"] = [h.saved(FILE_B, person=1)]
        calls, exhausted = [], False

        def limited_read(token, item):
            nonlocal exhausted
            calls.append(item["id"])
            if len(calls) > 2:
                exhausted = True
                raise ConnectionError("submission_refresh_budget_exhausted")
            return h.texts[item["id"]]

        def check():
            if exhausted:
                raise ConnectionError("submission_refresh_budget_exhausted")

        h.drive.check = check
        result = h.run(assignment="forensics:F03A", source_read=limited_read)
        self.assertEqual(calls, [FILE_C, FILE_B])
        rows = {row["file_id"]: row for row in result["files"]}
        for fid in (FILE_C, FILE_B):
            self.assertEqual(rows[fid]["content_assignment_key"], "forensics:F03A")
            self.assertNotIn("submission_refresh_budget_exhausted", rows[fid]["review_reasons"])
        self.assertEqual(rows[FILE_A]["content_assignment_key"], "")
        self.assertIn("submission_assignment_source_unverified", rows[FILE_A]["review_reasons"])
        self.assertEqual(rows[FILE_A]["source_availability"], "active")
        self.assertFalse(rows[FILE_A]["content_read"])
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["deck_contents_checked"], 2)
        self.assertEqual(result["coverage"]["unscoped_deck_content_skipped"], 1)
        self.assertEqual(result["external_writes"], 0)
        self.assertEqual(result["live_model_calls"], 0)

    def test_nested_form_folder_import_is_read_before_unrelated_root_budget_is_spent(self):
        child = "y" * 24
        folder = {"id": FOLDER, "mimeType": refresh.FOLDER, "parents": [ROOT], "trashed": False}
        nested = {"id": child, "mimeType": refresh.FOLDER, "parents": [FOLDER], "trashed": False}
        unrelated, imported = file_meta(FILE_A), file_meta(FILE_B, child)
        h = Harness([unrelated, imported], {
            ((ROOT,), None): {"files": [unrelated, folder]},
            ((FOLDER,), None): {"files": [nested]},
            ((child,), None): {"files": [imported]}})
        h.refs.clear()
        h.drive.form = lambda form_id, **kwargs: {"formId": form_id, "items": [
            {"questionItem": {"question": {"fileUploadQuestion": {"folderId": FOLDER}}}}]}
        calls = []
        def limited_read(token, item):
            calls.append(item["id"])
            if len(calls) > 1:
                raise ConnectionError("submission_refresh_budget_exhausted")
            return h.texts[item["id"]]
        result = h.run(assignment="forensics:F03A", source_read=limited_read)
        self.assertEqual(calls, [FILE_B])
        rows = {row["file_id"]: row for row in result["files"]}
        self.assertEqual(rows[FILE_B]["content_assignment_key"], "forensics:F03A")
        self.assertEqual(rows[FILE_B]["review_reasons"], ["form_identity_unavailable"])
        self.assertEqual(rows[FILE_B]["section_id"], "")
        self.assertEqual(rows[FILE_B]["form_ids"], [])
        self.assertIn("submission_assignment_source_unverified", rows[FILE_A]["review_reasons"])
        source = result["coverage"]["source_results"][0]
        self.assertTrue(source["complete"])
        self.assertEqual(source["files_checked"], 1)
        self.assertTrue(result["coverage"]["complete"])
        self.assertNotIn("folder_parents", result["coverage"])
        self.assertNotIn("form_upload_file_ids", result["coverage"])
        self.assertEqual(sum(call == ("children", (FOLDER,), None) for call in h.drive.calls), 1)
        self.assertEqual((result["external_writes"], result["live_model_calls"]), (0, 0))

    def test_unreferenced_import_from_form_folder_outside_root_is_prioritized(self):
        h = Harness([file_meta(FILE_A)])
        h.refs.clear()
        h.drive.items[FOLDER] = {"id": FOLDER, "mimeType": refresh.FOLDER, "trashed": False}
        imported = file_meta(FILE_B, FOLDER)
        h.drive.items[FILE_B] = imported
        h.texts[FILE_B] = h.catalog[0]["source_text"]
        h.drive.pages[((FOLDER,), None)] = {"files": [imported]}
        h.drive.form = lambda form_id, **kwargs: {"formId": form_id, "items": [
            {"questionItem": {"question": {"fileUploadQuestion": {"folderId": FOLDER}}}}]}
        calls = []
        def read(token, item):
            calls.append(item["id"])
            return h.texts[item["id"]]
        result = h.run(assignment="forensics:F03A", source_read=read)
        self.assertEqual(calls, [FILE_B])
        row = next(row for row in result["files"] if row["file_id"] == FILE_B)
        self.assertEqual(row["review_reasons"], ["form_identity_unavailable"])
        self.assertEqual(result["coverage"]["source_results"][0]["files_checked"], 1)

    def test_ten_moved_imports_are_read_and_rechecked_without_exporting_unrelated_root_decks(self):
        imported = [file_meta("imported" + str(n).zfill(17), FOLDER) for n in range(10)]
        unrelated = [file_meta("other" + str(n).zfill(19)) for n in range(20)]
        folder = {"id": FOLDER, "mimeType": refresh.FOLDER, "parents": [ROOT], "trashed": False}
        h = Harness(imported + unrelated, {
            ((ROOT,), None): {"files": unrelated + [folder]},
            ((FOLDER,), None): {"files": imported}})
        h.refs.clear()
        form_reads = []
        def form(form_id, *, fresh=False):
            form_reads.append((form_id, fresh))
            return {"formId": form_id, "items": [{"questionItem": {"question": {
                "fileUploadQuestion": {"folderId": FOLDER}}}}]}
        h.drive.form = form
        calls = []
        def read(token, item):
            calls.append(item["id"])
            if len(calls) > 10:
                raise ConnectionError("submission_refresh_budget_exhausted")
            return h.texts[item["id"]]
        result = h.run(assignment="forensics:F03A", source_read=read)
        self.assertEqual(set(calls), {item["id"] for item in imported})
        self.assertEqual(len(calls), 10)
        self.assertEqual(len(result["files"]), 30)
        for row in result["files"]:
            if row["file_id"] in calls:
                self.assertEqual(row["content_assignment_key"], "forensics:F03A")
                self.assertEqual(row["review_reasons"], ["form_identity_unavailable"])
            else:
                self.assertEqual(row["content_assignment_key"], "")
                self.assertIn("submission_assignment_source_unverified", row["review_reasons"])
            self.assertEqual(row["student_id"], "")
            self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn((FORM_A, True), form_reads)
        self.assertEqual(len(h.catalog_calls), 2)
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["content_scope"], "selected_submission_sources")
        self.assertEqual(result["coverage"]["deck_contents_checked"], 10)
        self.assertEqual(result["coverage"]["unscoped_deck_content_skipped"], 20)
        self.assertEqual(result["coverage"]["source_results"][0]["files_checked"], 10)
        all_calls = []
        h.run(source_read=lambda token, item: (all_calls.append(item["id"]), h.texts[item["id"]])[1])
        self.assertEqual(len(all_calls), 30)  # All-lessons still performs broad discovery.

    def test_final_catalog_recheck_budget_exhaustion_holds_all_preview_rows(self):
        h = Harness()
        expired = False
        def check():
            if expired:
                raise ConnectionError("submission_refresh_budget_exhausted")
        def finish_read(token, item):
            nonlocal expired
            expired = True
            return h.texts[item["id"]]
        h.drive.check = check
        result = h.run(source_read=finish_read)
        self.assertFalse(result["coverage"]["complete"])
        self.assertIn("submission_refresh_budget_exhausted", result["coverage"]["errors"])
        row = result["files"][0]
        self.assertEqual(row["assignment_key"], "forensics:F03A")
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("catalog_recheck_incomplete", row["review_reasons"])
        self.assertEqual(result["external_writes"], 0)

    def test_typed_period_is_a_crosscheck_not_a_section_override(self):
        h = Harness()
        h.refs[FILE_A]["typed_answers"]["s"] = ["A3 Forensics"]
        row = h.run()["files"][0]
        self.assertEqual(row["section_id"], "section-alpha")
        self.assertIn("typed_section_disagreement", row["review_reasons"])

    def test_saved_wrong_class_does_not_hide_current_selected_class_evidence(self):
        h = Harness()
        saved = h.saved()
        saved.update(class_key="biology", assignment_key="biology:W03")
        h.records["submissions"] = [saved]
        row = h.run()["files"][0]
        self.assertEqual(row["class_key"], "forensics")
        self.assertEqual(row["assignment_key"], "forensics:F03A")
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("saved_assignment_conflict", row["review_reasons"])

    def test_conflicting_assessed_revisions_are_not_unchanged(self):
        h = Harness()
        previous = h.saved(revision="2")
        previous["record_id"] += "-other"
        h.records["submissions"] = [h.saved(revision="1"), previous]
        row = h.run()["files"][0]
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("assessment_versions_differ", row["review_reasons"])
        self.assertEqual(len(row["saved_record_ids"]), 2)

    def test_invalid_question_mapping_keeps_review_preview(self):
        h = Harness()
        h.config["HUB_ASSIGNMENT_FORM_QUESTION_IDS"] = "invalid json"
        result = h.run()
        self.assertTrue(result["coverage"]["form_complete"])
        row = result["files"][0]
        self.assertEqual(row["assignment_key"], "forensics:F03A")
        self.assertEqual(row["discovery_status"], "needs_review")
        self.assertIn("typed_crosscheck_configuration_invalid", row["review_reasons"])

    def test_incomplete_root_keeps_exact_readable_form_references(self):
        items = [file_meta(FILE_A), file_meta(FILE_B)]
        pages = {((ROOT,), None): {"files": [items[0]], "nextPageToken": "later"},
                 ((ROOT,), "later"): ConnectionError("drive_read_failed")}
        result = Harness(items, pages).run()
        coverage = result["coverage"]
        self.assertFalse(coverage["complete"])
        self.assertEqual(coverage["form_refs_outside_root"], 0)
        self.assertEqual(coverage["form_refs_unlocated"], 0)
        self.assertIn("drive_read_failed", coverage["errors"])
        self.assertNotIn("form_reference_outside_approved_root", coverage["errors"])
        self.assertEqual([row["file_id"] for row in result["files"]], [FILE_A, FILE_B])

    def test_unreferenced_report_is_ignored_by_mime_type_not_filename(self):
        document = {**file_meta(FILE_B), "mimeType": "application/vnd.google-apps.document"}
        deck = {**file_meta(FILE_A), "name": "Feedback report"}
        h = Harness([deck, document])
        del h.refs[FILE_B]
        result = h.run()
        self.assertEqual([row["file_id"] for row in result["files"]], [FILE_A])
        self.assertEqual(result["files"][0]["name"], "Feedback report")
        self.assertEqual(result["coverage"]["ignored_non_deck_files"], 1)
        self.assertTrue(result["coverage"]["complete"])

    def test_unsupported_file_with_saved_or_form_provenance_stays_in_review(self):
        for provenance in ("form", "saved"):
            with self.subTest(provenance=provenance):
                document = {**file_meta(), "mimeType": "application/vnd.google-apps.document"}
                h = Harness([document])
                if provenance == "saved":
                    h.records["submissions"] = [h.saved()]
                    h.refs.clear()
                def unsupported(token, item):
                    raise ConnectionError("intake_deck_unsupported")
                result = h.run(source_read=unsupported)
                row = result["files"][0]
                self.assertEqual(row["file_id"], FILE_A)
                self.assertEqual(row["discovery_status"], "needs_review")
                self.assertIn("intake_deck_unsupported", row["review_reasons"])
                self.assertEqual(result["coverage"]["ignored_non_deck_files"], 0)


class TreeTests(unittest.TestCase):
    def test_malformed_drive_items_or_cursor_remain_explicit_partial_read(self):
        for page in ({"files": [None]}, {"files": [{**file_meta(), "parents": [{}]}]},
                     {"files": [{**file_meta(), "id": []}]}, {"files": [], "nextPageToken": 0}):
            with self.subTest(page=page):
                files, coverage = refresh.discover_tree(Drive([], {((ROOT,), None): page}), ROOT)
                self.assertFalse(coverage["drive_complete"])
                self.assertIn("submission_scan_incomplete", coverage["errors"])

    def test_later_page_failure_returns_partial_evidence_explicitly(self):
        item = file_meta()
        reader = Drive([item], {((ROOT,), None): {"files": [item], "nextPageToken": "later"},
                                ((ROOT,), "later"): ConnectionError("drive_read_failed")})
        files, coverage = refresh.discover_tree(reader, ROOT)
        self.assertEqual([r["id"] for r in files], [FILE_A])
        self.assertFalse(coverage["drive_complete"])
        self.assertIn("drive_read_failed", coverage["errors"])

    def test_repeated_cursor_cannot_be_claimed_complete(self):
        item = file_meta()
        page = {"files": [item], "nextPageToken": "again"}
        reader = Drive([item], {((ROOT,), None): page, ((ROOT,), "again"): page})
        files, coverage = refresh.discover_tree(reader, ROOT)
        self.assertEqual(len(files), 1)
        self.assertFalse(coverage["drive_complete"])
        self.assertEqual(coverage["pages_checked"], 2)

    def test_shortcut_is_not_traversed_and_marks_coverage_incomplete(self):
        shortcut = {**file_meta(), "mimeType": refresh.SHORTCUT}
        reader = Drive([shortcut])
        files, coverage = refresh.discover_tree(reader, ROOT)
        self.assertEqual(files, [])
        self.assertIn("submission_shortcut_needs_review", coverage["errors"])
        self.assertEqual(reader.calls, [("metadata", ROOT), ("children", (ROOT,), None)])

    def test_wrong_parent_and_incomplete_search_cannot_establish_presence(self):
        for page in ({"files": [file_meta(parent="q" * 24)]},
                     {"files": [file_meta()], "incompleteSearch": True}):
            with self.subTest(page=page):
                reader = Drive([], {((ROOT,), None): page})
                files, coverage = refresh.discover_tree(reader, ROOT)
                self.assertEqual(files, [])
                self.assertFalse(coverage["drive_complete"])

    def test_inactive_root_fails_before_children_read(self):
        reader = Drive([])
        reader.items[ROOT]["trashed"] = True
        with self.assertRaisesRegex(ConnectionError, "submission_folder_inactive"):
            refresh.discover_tree(reader, ROOT)
        self.assertEqual(reader.calls, [("metadata", ROOT)])


if __name__ == "__main__":
    unittest.main()
