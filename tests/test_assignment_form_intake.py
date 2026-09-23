"""Synthetic assignment-specific Form responses; no live sources or writes."""
import json
import unittest

from school_workflow_hub.adapters.assignment_form_intake import (
    collect_assignment_forms, cross_checks, resolve_published_forms, upload_folders)
from school_workflow_hub.adapters.hosted_agenda import ConnectionError

FORM_A, FORM_B = "form_a_" + "x" * 20, "form_b_" + "y" * 20
FILE_A, FILE_B = "file_a_" + "a" * 20, "file_b_" + "b" * 20
KEY_A, KEY_B = "forensics:F03-SyntheticA", "forensics:F03SyntheticB"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def binding(key, form_id):
    return {"assignment_key": key, "form_id": form_id,
            "form_url": "https://docs.google.com/forms/d/" + form_id + "/viewform",
            "form_source": "agenda_u_turnin_map", "form_mapping_verified": True,
            "form_source_reference": "Synthetic course!U6",
            "form_source_references": ["Synthetic course!U6"]}


def response(fid, *, email="s.alex.example@school.example", response_id="synthetic-response",
             name="Synthetic upload.pptx", typed=None, time="2026-09-23T10:00:00Z"):
    answers = {"upload": {"fileUploadAnswers": {"answers": [
        {"fileId": fid, "fileName": name, "mimeType": PPTX}]}}}
    for qid, answer in (typed or {}).items():
        answers[qid] = {"textAnswers": {"answers": [{"value": answer}]}}
    return {"responseId": response_id, "respondentEmail": email, "createTime": time,
            "answers": answers}


class Reader:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def responses(self, form_id, cursor=None):
        self.calls.append((form_id, cursor))
        result = self.pages[(form_id, cursor)]
        if isinstance(result, Exception):
            raise result
        return result


class AssignmentFormsTests(unittest.TestCase):
    def test_two_assignments_read_their_own_forms_and_complete_pagination(self):
        reader = Reader({
            (FORM_A, None): {"responses": [], "nextPageToken": "next"},
            (FORM_A, "next"): {"responses": [response(FILE_A, typed={"work": "unrelated typed text"})]},
            (FORM_B, None): {"responses": [response(FILE_B)]},
        })
        refs, evidence = collect_assignment_forms(reader, [binding(KEY_A, FORM_A), binding(KEY_B, FORM_B)])
        self.assertEqual(reader.calls, [(FORM_A, None), (FORM_A, "next"), (FORM_B, None)])
        self.assertEqual((refs[FILE_A]["assignment_key"], refs[FILE_B]["assignment_key"]), (KEY_A, KEY_B))
        self.assertEqual(refs[FILE_A]["form_id"], FORM_A)
        self.assertEqual(refs[FILE_A]["source_events"][0]["form_source_reference"], "Synthetic course!U6")
        self.assertEqual(refs[FILE_A]["source_events"][0]["form_source_references"], ["Synthetic course!U6"])
        self.assertTrue(evidence["complete"])
        self.assertEqual((evidence["forms_complete"], evidence["responses_checked"], evidence["pages"]), (2, 2, 3))

    def test_unavailable_form_does_not_hide_other_assignment(self):
        reader = Reader({(FORM_A, None): ConnectionError("submission_form_not_found"),
                         (FORM_B, None): {"responses": [response(FILE_B)]}})
        refs, evidence = collect_assignment_forms(reader, [binding(KEY_A, FORM_A), binding(KEY_B, FORM_B)])
        self.assertEqual(set(refs), {FILE_B})
        self.assertFalse(evidence["complete"])
        self.assertEqual(evidence["assignments"][0]["error"], "submission_form_not_found")
        self.assertTrue(evidence["assignments"][1]["complete"])

    def test_incomplete_form_discards_its_partial_refs_only(self):
        reader = Reader({(FORM_A, None): {"responses": [response(FILE_A)], "nextPageToken": "next"},
                         (FORM_A, "next"): {"responses": [], "nextPageToken": "next"},
                         (FORM_B, None): {"responses": [response(FILE_B)]}})
        refs, evidence = collect_assignment_forms(reader, [binding(KEY_A, FORM_A), binding(KEY_B, FORM_B)])
        self.assertEqual(set(refs), {FILE_B})
        self.assertIn("submission_form_scan_incomplete", evidence["errors"])

    def test_missing_binding_never_uses_global_shared_form_fallback(self):
        reader = Reader({})
        refs, evidence = collect_assignment_forms(reader, [{"assignment_key": KEY_A}],
                                                  {"HUB_TURNIN_FORM_ID": FORM_A})
        self.assertEqual(refs, {})
        self.assertEqual(reader.calls, [])
        self.assertEqual(evidence["errors"], ["assignment_form_mapping_required"])

    def test_conflicting_weekly_links_preserve_exact_repair_action_without_reading_form(self):
        entry = binding(KEY_A, FORM_A)
        action = "Verify the visible Turn it in link at Synthetic course!T6 and raw link at U6; both must identify the same assignment Form."
        entry.update(form_mapping_verified=False, form_review_reason="assignment_form_turn_in_links_disagree",
                     form_review_action=action)
        reader = Reader({})
        refs, evidence = collect_assignment_forms(reader, [entry])
        self.assertEqual(refs, {})
        self.assertEqual(reader.calls, [])
        self.assertEqual(evidence["assignments"][0]["next_action"], action)
        self.assertFalse(evidence["complete"])

    def test_published_url_requires_verified_private_editable_mapping(self):
        entry = binding(KEY_A, FORM_A)
        entry["form_url"] = "https://docs.google.com/forms/d/e/" + "published_" + "p" * 20 + "/viewform"
        reader = Reader({(FORM_A, None): {"responses": [response(FILE_A)]}})
        refs, evidence = collect_assignment_forms(reader, [entry])
        self.assertFalse(refs)
        self.assertEqual(evidence["errors"], ["assignment_form_mapping_conflict"])
        self.assertEqual(reader.calls, [])
        entry["form_source"] = "private_assignment_mapping"
        refs, evidence = collect_assignment_forms(reader, [entry])
        self.assertEqual(refs[FILE_A]["form_id"], FORM_A)
        self.assertTrue(evidence["complete"])

    def test_private_mapping_can_disambiguate_a_multi_assignment_agenda_row(self):
        entry = binding(KEY_B, FORM_B)
        entry.update(form_url=binding(KEY_A, FORM_A)["form_url"], form_binding_ambiguous=True,
                     form_source="private_assignment_mapping")
        reader = Reader({(FORM_B, None): {"responses": [response(FILE_B)]}})
        refs, evidence = collect_assignment_forms(reader, [entry])
        self.assertEqual(refs[FILE_B]["assignment_key"], KEY_B)
        self.assertEqual(refs[FILE_B]["source_events"][0]["form_url"], entry["form_url"])
        self.assertTrue(evidence["complete"])

    def test_one_form_cannot_be_reinterpreted_as_two_assignment_forms(self):
        reader = Reader({})
        refs, evidence = collect_assignment_forms(reader, [binding(KEY_A, FORM_A), binding(KEY_B, FORM_A)])
        self.assertFalse(refs)
        self.assertEqual(reader.calls, [])
        self.assertEqual(evidence["errors"], ["assignment_form_mapping_conflict"])

    def test_conflicting_duplicate_assignment_binding_is_not_first_wins(self):
        reader = Reader({})
        _, evidence = collect_assignment_forms(reader, [binding(KEY_A, FORM_A), binding(KEY_A, FORM_B)])
        self.assertEqual(reader.calls, [])
        self.assertEqual(evidence["errors"], ["assignment_form_mapping_conflict"])

    def test_repeated_agenda_rows_preserve_all_source_references(self):
        repeated = binding(KEY_A, FORM_A)
        repeated.update(form_source_reference="Synthetic course!U7",
                        form_source_references=["Synthetic course!U7"])
        reader = Reader({(FORM_A, None): {"responses": [response(FILE_A)]}})
        refs, evidence = collect_assignment_forms(reader, [binding(KEY_A, FORM_A), repeated])
        self.assertEqual(reader.calls, [(FORM_A, None)])
        self.assertEqual(refs[FILE_A]["source_events"][0]["form_source_references"],
                         ["Synthetic course!U6", "Synthetic course!U7"])
        self.assertEqual(len(evidence["assignments"]), 1)

    def test_same_file_in_different_forms_preserves_both_events_and_conflicts(self):
        reader = Reader({
            (FORM_A, None): {"responses": [response(FILE_A, response_id="response-a")]},
            (FORM_B, None): {"responses": [response(FILE_A, email="s.other.example@school.example",
                                                       response_id="response-b", time="2026-09-23T11:00:00Z")]},
        })
        refs, evidence = collect_assignment_forms(reader, [binding(KEY_A, FORM_A), binding(KEY_B, FORM_B)])
        ref = refs[FILE_A]
        self.assertEqual(ref["assignment_key"], "")
        self.assertEqual(ref["form_id"], "")
        self.assertEqual({e["response_id"] for e in ref["source_events"]}, {"response-a", "response-b"})
        self.assertTrue(ref["identity_conflict"])
        self.assertTrue(ref["assignment_conflict"])
        self.assertEqual({r["reason"] for r in evidence["needs_review"]},
                         {"respondent_evidence_conflict", "form_assignment_conflict"})

    def test_different_question_ids_across_forms_are_not_identity_conflict(self):
        reader = Reader({
            (FORM_A, None): {"responses": [response(FILE_A, typed={"q-a": "Alex Example"})]},
            (FORM_B, None): {"responses": [response(FILE_A, typed={"q-b": "Alex Example"})]},
        })
        refs, _ = collect_assignment_forms(reader, [binding(KEY_A, FORM_A), binding(KEY_B, FORM_B)])
        self.assertFalse(refs[FILE_A]["identity_conflict"])
        self.assertTrue(refs[FILE_A]["assignment_conflict"])

    def test_within_form_identity_conflict_is_not_lost(self):
        reader = Reader({(FORM_A, None): {"responses": [response(FILE_A),
            response(FILE_A, email="s.other.example@school.example", response_id="response-two")]}})
        refs, _ = collect_assignment_forms(reader, [binding(KEY_A, FORM_A)])
        self.assertTrue(refs[FILE_A]["identity_conflict"])
        self.assertTrue(refs[FILE_A]["source_events"][0]["identity_conflict"])

    def test_exact_assignment_scope_reads_only_its_verified_form(self):
        reader = Reader({(FORM_B, None): {"responses": [response(FILE_B)]}})
        refs, evidence = collect_assignment_forms(reader, [binding(KEY_A, FORM_A), binding(KEY_B, FORM_B)],
                                                  assignment_key=KEY_B)
        self.assertEqual(reader.calls, [(FORM_B, None)])
        self.assertEqual(set(refs), {FILE_B})
        self.assertEqual(len(evidence["assignments"]), 1)


class OptionalTypedCrosschecksTests(unittest.TestCase):
    def setUp(self):
        self.person = {"first": "Alex Middle (Lex)", "last": "Example", "section_name": "A2 Forensics"}
        self.lesson = {"assignment_key": KEY_A, "assignment_title": "Synthetic observation",
                       "source_url": "https://example.invalid/synthetic-deck"}
        self.ref = {"assignment_key": KEY_A, "typed_answers": {"n": ["Lex Example"], "p": ["A2 Forensics"]}}

    def test_forms_without_typed_assignment_question_are_valid(self):
        config = {"HUB_ASSIGNMENT_FORM_QUESTION_IDS": {KEY_A: {"name": "n", "section": "p"}}}
        self.assertEqual(cross_checks(self.ref, self.person, self.lesson, config), [])
        self.assertEqual(cross_checks(self.ref, self.person, self.lesson, {}), [])

    def test_only_exact_assignment_question_ids_are_checked(self):
        config = {"HUB_ASSIGNMENT_FORM_QUESTION_IDS": json.dumps({KEY_A: {"name": "n"},
                                                               KEY_B: {"assignment": "other-question"}})}
        self.assertEqual(cross_checks(self.ref, self.person, self.lesson, config), [])
        config = {"HUB_ASSIGNMENT_FORM_QUESTION_IDS": {KEY_A: {"assignment": "missing"}}}
        self.assertEqual(cross_checks(self.ref, self.person, self.lesson, config), ["typed_assignment_disagreement"])

    def test_global_shared_question_configuration_is_not_used(self):
        config = {"HUB_TURNIN_QUESTION_IDS": {"assignment": "missing", "name": "missing-name"}}
        self.assertEqual(cross_checks(self.ref, self.person, self.lesson, config), [])

    def test_invalid_role_maps_are_held(self):
        for value in ({KEY_A: {"unknown": "q"}}, {KEY_A: {"name": "q", "section": "q"}}, "not-json"):
            with self.subTest(value=value):
                self.assertEqual(cross_checks(self.ref, self.person, self.lesson,
                    {"HUB_ASSIGNMENT_FORM_QUESTION_IDS": value}), ["typed_crosscheck_configuration_invalid"])

    def test_form_and_deck_disagreement_cannot_use_wrong_question_map(self):
        self.ref["assignment_key"] = KEY_B
        self.assertEqual(cross_checks(self.ref, self.person, self.lesson, {}), ["form_deck_assignment_disagreement"])


class FormSourceDiscoveryTests(unittest.TestCase):
    def test_published_binding_resolves_only_exact_responder_uri_not_title(self):
        public = "https://docs.google.com/forms/d/e/" + "published_" + "p" * 20 + "/viewform"
        entry = {**binding(KEY_A, ""), "form_url": public, "form_mapping_verified": False,
                 "form_review_reason": "assignment_form_api_id_required"}
        documents = {FORM_A: {"formId": FORM_A, "responderUri": public + "?usp=sharing"},
                     FORM_B: {"formId": FORM_B, "responderUri": binding(KEY_B, FORM_B)["form_url"]}}
        resolved = resolve_published_forms([entry], documents, documents.__getitem__)
        self.assertEqual(resolved[0]["form_id"], FORM_A)
        self.assertEqual(resolved[0]["form_source"], "agenda_responder_uri")
        refs, evidence = collect_assignment_forms(Reader({(FORM_A, None): {"responses": []}}), resolved)
        self.assertTrue(evidence["complete"])
        self.assertFalse(entry["form_mapping_verified"])
        documents[FORM_A]["responderUri"] = binding(KEY_B, FORM_B)["form_url"]
        self.assertFalse(resolve_published_forms([entry], documents, documents.__getitem__)[0]["form_mapping_verified"])

    def test_selected_scope_does_not_bypass_form_reuse_conflict(self):
        reader = Reader({})
        refs, evidence = collect_assignment_forms(reader, [binding(KEY_A, FORM_A), binding(KEY_B, FORM_A)],
                                                  assignment_key=KEY_A)
        self.assertFalse(evidence["complete"])
        self.assertEqual(reader.calls, [])
        self.assertEqual(refs, {})
        self.assertIn("assignment_form_mapping_conflict", evidence["errors"])

    def test_upload_folder_ids_are_exact_structure_fields_not_text_links(self):
        item = {"questionItem": {"question": {"fileUploadQuestion": {"folderId": FILE_A}}}}
        self.assertEqual(upload_folders({"items": [item, item, {"description": FILE_B}]}), [FILE_A])
        for form in ({"items": [{"questionItem": None}]},
                     {"items": [{"questionItem": {"question": {"fileUploadQuestion": {}}}}]}):
            with self.assertRaises(ConnectionError):
                upload_folders(form)


if __name__ == "__main__":
    unittest.main()
