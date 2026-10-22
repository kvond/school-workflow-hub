"""Synthetic append-only receipt recovery; no browser or school writes."""
import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from hub_v2 import morning


def report(number=1, *, code="running", day="2026-10-01"):
    return {"day": day, "run_id": "11111111-1111-4111-8111-%012d" % number,
            "code": code, "roster": "unavailable", "items": []}


def row(value):
    return ["2026-10-01 07:00", morning.KIND, "", value["day"], "Morning run",
            value["run_id"], json.dumps(value)]


class MorningReportReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.rows = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.find = self.stack.enter_context(patch.object(morning.ledger, "find", return_value={"id": "FICTIONAL_LEDGER"}))
        self.tab = self.stack.enter_context(patch.object(morning.ledger, "_first_tab", return_value="'Receipts'"))
        self.read = self.stack.enter_context(patch.object(morning.google, "sheet_values", side_effect=lambda *a: copy.deepcopy(self.rows)))
        self.append = self.stack.enter_context(patch.object(morning.google, "append_rows", side_effect=self._append))
        self.scorer = self.stack.enter_context(patch.object(morning, "make_list"))

    def _append(self, token, sheet, target, values):
        self.assertEqual((sheet, target), ("FICTIONAL_LEDGER", "'Receipts'!A1"))
        self.rows.extend(copy.deepcopy(values))

    def save(self, value):
        return morning.record("read-token", "write-token", {}, value)

    def test_retry_of_report_outside_latest_ten_is_read_only(self):
        old = report()
        self.rows = [row(old)] + [row(report(n)) for n in range(2, 13)]
        self.assertNotIn(old, morning.reports("t", {}))
        self.save(old)
        self.append.assert_not_called()
        self.assertEqual(len(self.rows), 12)

    def test_delayed_known_starting_receipt_does_not_replace_finished_status(self):
        started, finished = report(), report(code="finished")
        self.rows = [row(started), row(finished)]
        self.save(started)
        self.append.assert_not_called()
        self.assertEqual(morning.reports("t", {}), [finished])

    def test_old_outbox_report_can_be_saved_and_verified_outside_dashboard_window(self):
        self.rows = [row(report(n, day="2026-10-02")) for n in range(2, 13)]
        old = report(code="missed_noon")
        self.save(old)
        self.append.assert_called_once()
        self.assertNotIn(old, morning.reports("t", {}))
        self.save(old)
        self.append.assert_called_once()
        self.scorer.assert_not_called()

    def test_lost_append_response_is_reconciled_by_exact_readback_without_rewrite(self):
        value = report()
        def uncertain(*args):
            self._append(*args)
            raise morning.google.GoogleError("Private provider detail", status=503)
        self.append.side_effect = uncertain
        self.save(value)
        self.append.assert_called_once()
        self.assertEqual(morning.reports("t", {}), [value])
        self.scorer.assert_not_called()

    def test_uncertain_append_without_exact_saved_value_is_not_retried(self):
        self.append.side_effect = morning.google.GoogleError("Private provider detail", status=503)
        with self.assertRaises(morning.google.GoogleError):
            self.save(report())
        self.append.assert_called_once()
        self.assertEqual(self.rows, [])
        self.scorer.assert_not_called()

    def test_readback_failure_never_acknowledges_or_repeats_write(self):
        self.read.side_effect = [[], morning.google.GoogleError("Private read error", status=429)]
        with self.assertRaises(morning.google.GoogleError):
            self.save(report())
        self.append.assert_called_once()
        self.scorer.assert_not_called()

    def test_bounded_history_full_or_overflow_stops_new_append(self):
        with patch.object(morning.ledger, "MAX_ROWS", 2):
            for rows in ([row(report(2)), row(report(3))], [row(report(2)), row(report(3)), row(report(4))]):
                self.rows = rows
                with self.assertRaises(morning.google.GoogleError):
                    self.save(report())
        self.append.assert_not_called()

    def test_diagnostics_omit_payload_provider_text_and_tokens(self):
        self.append.side_effect = morning.google.GoogleError("PRIVATE_RESPONSE_TEXT", status=503)
        with self.assertLogs("hub_v2.morning", level="INFO") as captured:
            with self.assertRaises(morning.google.GoogleError):
                self.save(report())
        logs = "\n".join(captured.output)
        self.assertIn("append", logs)
        self.assertIn("readback", logs)
        self.assertNotIn("PRIVATE_RESPONSE_TEXT", logs)
        self.assertNotIn("read-token", logs)
        self.assertNotIn("write-token", logs)
        self.assertNotIn("FICTIONAL_LEDGER", logs)

    def test_destination_is_resolved_once_and_reused_for_readback(self):
        self.save(report())
        self.find.assert_called_once()
        self.tab.assert_called_once()
        self.assertTrue(all(call.args[1] == "FICTIONAL_LEDGER" for call in self.read.call_args_list))
