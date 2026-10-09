"""Stale teacher clients cannot record IC entry for an excluded teaching week."""
import unittest
from unittest.mock import patch

from hub_v2 import app, google, ledger, work


class RecordPolicyTests(unittest.TestCase):
    def request(self, policy, kind="ic", found=None):
        payload = {"class": "biology", "week": "Wk 01",
                   "form": "FICTIONAL_FORM_123456", "kind": kind}
        with patch.object(app, "_same_origin_json", return_value=None), \
                patch.object(app, "_json_body", return_value=payload), \
                patch.object(google, "service_token", return_value="token") as tokens, \
                patch.object(app, "load_agenda", return_value=object()) as agenda, \
                patch.object(work, "week_policy", return_value=policy), \
                patch.object(app, "_form_context", return_value=({}, {}, found or {})) as context, \
                patch.object(ledger, "record") as record, \
                patch.object(ledger, "read", return_value={}):
            result = app.record_post({}, lambda status, value: (status, value), {})
        return result, record, context, tokens, agenda

    def test_excluded_week_rejects_stale_ic_receipt_without_write_token(self):
        result, record, context, tokens, agenda = self.request({
            "excluded": True, "reason": "This week is not assigned this year.", "problem": None})
        self.assertEqual(result[0], "409 Conflict")
        self.assertEqual(result[1]["code"], "excluded_week")
        record.assert_not_called(); context.assert_not_called()
        self.assertEqual(tokens.call_count, 1)
        self.assertTrue(agenda.call_args.kwargs["fresh"])

    def test_invalid_policy_rejects_ic_receipt_before_context_or_writes(self):
        result, record, context, tokens, _ = self.request({
            "excluded": False, "reason": "", "problem": "The week policy needs checking."})
        self.assertEqual(result[1]["code"], "coverage_incomplete")
        record.assert_not_called(); context.assert_not_called()
        self.assertEqual(tokens.call_count, 1)

    def test_eligible_week_keeps_normal_recording(self):
        result, record, _, _, _ = self.request({"excluded": False, "problem": None})
        self.assertEqual(result[0], "200 OK")
        record.assert_called_once()

    def test_invalid_alternate_intake_rejects_receipt_without_write_token(self):
        result, record, _, tokens, _ = self.request({"excluded": False, "problem": None},
            found={"assessment_problem": "The alternate Form mapping needs checking."})
        self.assertEqual(result[1]["code"], "coverage_incomplete")
        record.assert_not_called()
        self.assertEqual(tokens.call_count, 1)

    def test_historical_feedback_recording_remains_available(self):
        self.assertIn("feedback", ledger.KINDS)
        result, record, _, _, _ = self.request({"excluded": True, "problem": None}, "feedback")
        self.assertEqual(result[0], "200 OK")
        record.assert_called_once()


if __name__ == "__main__":
    unittest.main()
