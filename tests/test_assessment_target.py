"""Synthetic section safety at inventory, paid-draft and save boundaries."""
import copy
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import assessment_target as target
from school_workflow_hub.adapters import assessment_provider as provider, assessment_save as save
from tests.test_assessment_provider import drafting, request, CONFIG
from tests.test_assessment_records import packet, CAPTURE
from school_workflow_hub.workflows import assessment_records


def records():
    p = packet()
    return {"assignments": [{"class_key": p["class_key"], "assignment_key": p["assignment_key"]}],
            "sections": [{"class_key": p["class_key"], "section_id": p["section_id"],
                          "section_name": p["section_name"]}], "submissions": []}


class AssessmentTargetTests(unittest.TestCase):
    def test_blank_all_unknown_and_wrong_name_sections_are_not_assessment_targets(self):
        p = packet()
        for section, name in (("", p["section_name"]), ("all", p["section_name"]),
                              ("other", p["section_name"]), (p["section_id"], "Different section")):
            with self.subTest(section=section, name=name), self.assertRaises(ValueError):
                target.section_scope(records(), p["class_key"], p["assignment_key"], section, name)
        with self.assertRaisesRegex(ValueError, "assignment_unknown"):
            target.section_scope(records(), p["class_key"], "discovery-only", p["section_id"])

    def test_other_section_saved_file_binding_blocks_draft_before_capture_or_model(self):
        ask = request()
        data = records()
        data["submissions"] = [{"source_file_id": ask["file_id"], "class_key": ask["class_key"],
            "assignment_key": ask["assignment_key"], "section_id": "another-section", "status": "assessed"}]
        with patch.object(provider, "_identity_configuration", return_value=("a", ["t"])), \
             patch.object(provider, "authorize"), \
             patch("school_workflow_hub.adapters.grading_records.read_records", return_value=data), \
             patch.object(save, "capture") as capture, patch.object(provider, "_call") as model:
            with self.assertRaisesRegex(ValueError, "assessment_target_conflict"):
                provider.draft(None, ask, CONFIG)
            capture.assert_not_called()
            model.assert_not_called()

    def test_cached_draft_cannot_be_reused_for_other_section_or_assignment(self):
        base = packet()
        for field in ("section_id", "assignment_key", "stage"):
            ask = request()
            ask[field] = "another-target"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "cached_target_conflict"):
                target.check_cached(base, ask, ask["identity"])

    def test_same_file_conflicting_saved_identity_blocks_blank_identity_before_capture(self):
        for key in ("student_id", "learner_key"):
            ask = request()
            ask["identity"] = {}
            row = {"source_file_id": ask["file_id"], "class_key": ask["class_key"],
                   "assignment_key": ask["assignment_key"], "section_id": ask["section_id"],
                   "status": "assessed", key: "synthetic-one"}
            data = {**records(), "submissions": [row, {**row, key: "synthetic-two", "checked_at": "later"}]}
            with self.subTest(key=key), patch.object(provider, "_identity_configuration", return_value=("a", ["t"])), \
                 patch.object(provider, "authorize"), \
                 patch("school_workflow_hub.adapters.grading_records.read_records", return_value=data), \
                 patch.object(save, "capture") as capture, patch.object(provider, "_call") as model:
                with self.assertRaisesRegex(ValueError, "assessment_identity_conflict"):
                    provider.draft(None, ask, CONFIG)
                capture.assert_not_called()
                model.assert_not_called()

    def test_saved_native_and_learner_keys_need_an_existing_bridge(self):
        rows = [{"student_id": "synthetic-number"}, {"learner_key": "roster:abcdef12"}]
        with self.assertRaisesRegex(ValueError, "identity_conflict"):
            target.compatible_identity(rows)
        rows.append({**rows[0], **rows[1]})
        self.assertEqual(target.compatible_identity(rows), rows[-1])
        with self.assertRaisesRegex(ValueError, "identity_unverified"):
            target.compatible_identity([{"display_name": "Synthetic name"}])

    def test_filled_identity_is_rechecked_before_capture(self):
        prior = {"student_id": request()["identity"]["student_id"]}
        with drafting('unused', prior=prior), \
             patch.object(provider, "_filled_identity", return_value={"student_id": "different"}), \
             patch.object(save, "capture") as capture, patch.object(provider, "_call") as model:
            with self.assertRaisesRegex(ValueError, "identity_conflict"):
                provider.draft(None, request(), CONFIG)
            capture.assert_not_called()
            model.assert_not_called()

    def test_save_does_not_supersede_same_student_in_another_section(self):
        p = packet()
        same = {"assignment_key": p["assignment_key"], "section_id": p["section_id"],
                "student_id": p["identity"]["student_id"], "learner_key": p["identity"]["learner_key"]}
        other = {**same, "section_id": "another-section"}
        table = {"rows": [(2, same), (3, other)]}
        self.assertEqual(save._matching_rows(table, p), [(2, same)])

    def test_save_target_failure_precedes_capture_or_write(self):
        with patch.object(target, "read_target", side_effect=ValueError("assessment_section_unverified")), \
             patch.object(save, "capture") as capture, patch.object(save, "_batch") as batch:
            with self.assertRaisesRegex(ValueError, "section_unverified"):
                save.save(None, {"request_id": "synthetic-request-id", "packet": packet()}, {})
            capture.assert_not_called()
            batch.assert_not_called()

    def test_readback_checks_section_identity_in_addition_to_scores(self):
        p = packet()
        rows = assessment_records.build_rows(p, assessment_records.completion(p), CAPTURE,
                                             now="2026-09-23T12:00:00Z")
        saved = {**rows["submission"], "answers": rows["answers"], "included_in_counts": True}
        data = {"submissions": [saved]}
        save._verify_readback(data, rows, [])
        for field in ("section_id", "assignment_key", "student_id"):
            wrong = copy.deepcopy(data)
            wrong["submissions"][0][field] = "different"
            with self.subTest(field=field), self.assertRaisesRegex(save.ConnectionError, "readback_failed"):
                save._verify_readback(wrong, rows, [])


if __name__ == "__main__":
    unittest.main()
