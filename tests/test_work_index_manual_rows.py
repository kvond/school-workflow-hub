"""Manual index history stays in place; all Sheets and Drive calls are fictional."""
from copy import deepcopy
from contextlib import ExitStack
import unittest
from unittest.mock import patch

from hub_v2 import google, student_archive as archive
from school_workflow_hub.core.cloud_state import CloudState, Conflict
from test_cloud_state import MemoryCAS
from test_student_archive import FakeDrive, ROOT, config

SHEET = "FictionalManualIndexSheet001"


def generated(key, **changes):
    row = ["Biology", "Fictional section", "Fictional learner", "Fictional lesson", "Wk 04",
           46302, "", "", "", "", "", "Work saved", "", key]
    for column, value in changes.items():
        row[int(column)] = value
    return row


def cells(values):
    return {"values": [archive._index_cell(v) for v in values]}


def manual():
    row = cells(["Biology", "", "", "Historical class note"] + [""] * 10)
    row["values"][3].update(note="Historical context stays here",
        textFormatRuns=[{"startIndex": 0, "format": {"bold": True}}],
        userEnteredFormat={"backgroundColor": {"red": 0.8}, "wrapStrategy": "WRAP"})
    row["values"][12] = {"userEnteredValue": {"boolValue": False},
        "dataValidation": {"condition": {"type": "BOOLEAN"}, "strict": True},
        "note": "Teacher-controlled historical mark"}
    return row


class ManualIndexTests(unittest.TestCase):
    def setUp(self):
        self.drive, self.port, self.config = FakeDrive(), MemoryCAS(), config()
        self.config[archive.WORK_INDEX_CONFIG] = "https://docs.google.com/spreadsheets/d/%s/edit" % SHEET
        self.drive.add(SHEET, "Fictional work index", "application/vnd.google-apps.spreadsheet", [ROOT])[
            "properties"]["hubStudentWorkIndex"] = ROOT
        self.rows = [cells(archive.INDEX_HEADERS)]
        self.grid_rows = 20
        self.generated = [generated("FictionalKeyA")]
        self.holds = []
        self.reads, self.writes, self.requests = 0, 0, []
        self.before_read, self.after_write = None, None
        self.fail_write, self.apply_write = False, True
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.object(archive, "configured_store", return_value=self.port))
        stack.enter_context(patch.object(google, "drive_file", side_effect=self.drive.metadata))
        stack.enter_context(patch.object(google, "get_json", side_effect=self.sheet))
        stack.enter_context(patch.object(archive, "work_index_rows", side_effect=lambda *args: {
            "rows": deepcopy(self.generated), "summary": {"biology": {"work_copies": len(self.generated)}},
            "holds": self.holds, "checked_at": "2026-10-19T10:30:00Z", "coverage": "verified_archive_only"}))

    def sheet(self, url, token, params=None, timeout=20, method="GET", body=None):
        if method == "POST":
            self.writes += 1
            self.requests.extend(deepcopy(body["requests"]))
            if self.apply_write:
                for request in body["requests"]:
                    if "appendDimension" in request:
                        self.grid_rows += request["appendDimension"]["length"]
                    if "updateCells" not in request:
                        continue
                    update = request["updateCells"]
                    self.assertEqual(update["fields"], "userEnteredValue")
                    start, end = update["range"]["startRowIndex"], update["range"]["endRowIndex"]
                    self.assertEqual(len(update["rows"]), end - start)
                    while len(self.rows) < end:
                        self.rows.append({"values": []})
                    for number, source in enumerate(update["rows"], start):
                        target = self.rows[number].setdefault("values", [])
                        while len(target) < 14:
                            target.append({})
                        for column, value in enumerate(source["values"]):
                            if "userEnteredValue" in value:
                                target[column]["userEnteredValue"] = deepcopy(value["userEnteredValue"])
                            else:
                                target[column].pop("userEnteredValue", None)
            if self.after_write:
                self.after_write()
            if self.fail_write:
                raise google.GoogleError("Write response unavailable")
            return {}
        self.reads += 1
        if self.before_read:
            self.before_read(self.reads)
        self.assertIn("note", params["fields"])
        self.assertIn("dataValidation", params["fields"])
        self.assertIn("userEnteredFormat", params["fields"])
        return {"spreadsheetId": SHEET, "sheets": [{"properties": {"title": "Records", "sheetId": 17,
            "gridProperties": {"rowCount": self.grid_rows, "columnCount": 14}},
            "data": [{"rowData": deepcopy(self.rows)}]}]}

    def refresh(self):
        return archive.refresh_work_index("read", "write", self.config)

    def assert_never_wrote(self, *positions):
        for request in self.requests:
            if "updateCells" in request:
                area = request["updateCells"]["range"]
                self.assertFalse(set(positions) & set(range(area["startRowIndex"], area["endRowIndex"])))

    def test_interleaved_manual_rows_keep_every_cell_and_keyed_notes_follow_the_record(self):
        old_a = cells(self.generated[0]); old_a["values"][12] = archive._index_cell("Existing keyed teacher note")
        self.rows += [manual(), old_a, manual()]
        original = {n: deepcopy(self.rows[n]) for n in (1, 3)}
        self.generated = [generated("FictionalKeyB"), generated("FictionalKeyA")]
        result = self.refresh()
        self.assertEqual((result["status"], result["rows"], result["retained_manual_rows"]), ("verified", 2, 2))
        for n, before in original.items():
            self.assertEqual(self.rows[n], before)
        self.assertEqual(archive._index_entered(self.rows[2])[13], {"stringValue": "FictionalKeyB"})
        self.assertEqual(archive._index_entered(self.rows[4])[12], {"stringValue": "Existing keyed teacher note"})
        self.assert_never_wrote(1, 3)
        again = self.refresh()
        self.assertFalse(again["changed"]); self.assertEqual(self.writes, 1)

    def test_growth_then_shrink_leaves_manual_history_in_its_original_position(self):
        self.rows += [cells(self.generated[0]), {"values": []}, manual()]
        before = deepcopy(self.rows[3]); self.grid_rows = 4
        self.generated = [generated("FictionalKey%d" % n) for n in range(5)]
        self.assertEqual(self.refresh()["status"], "verified")
        self.assertEqual(self.grid_rows, 7)
        self.assertEqual(self.rows[3], before)
        self.generated = []
        result = self.refresh()
        self.assertEqual((result["status"], result["rows"], result["retained_manual_rows"]), ("verified", 0, 1))
        self.assertEqual(self.rows[3], before)
        self.assertFalse(any(any(archive._index_entered(r)) for n, r in enumerate(self.rows) if n not in (0, 3)))
        self.assert_never_wrote(3)
        self.assertFalse(self.refresh()["changed"])

    def test_note_only_and_validation_only_rows_are_reserved_and_formula_history_is_retained(self):
        self.rows += [{"values": [{"note": "Keep this historical annotation"}]},
                      {"values": [{"dataValidation": {"condition": {"type": "BOOLEAN"}}}]},
                      {"values": [{"userEnteredValue": {"formulaValue": '=HYPERLINK("https://example.org","History")'}}]}]
        before = deepcopy(self.rows)
        result = self.refresh()
        self.assertEqual(result["retained_manual_rows"], 3)
        self.assertEqual(self.rows[:4], before); self.assert_never_wrote(1, 2, 3)

    def test_blank_template_format_is_preserved_when_a_generated_record_uses_the_row(self):
        formatting = {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}
        self.rows += [{"values": [{"userEnteredFormat": formatting}]}]
        result = self.refresh()
        self.assertEqual(result["retained_manual_rows"], 0)
        self.assertEqual(self.rows[1]["values"][0]["userEnteredFormat"], formatting)

    def test_duplicate_keys_missing_keyed_notes_and_keyed_note_validation_still_hold(self):
        for cause in ("duplicate", "missing_note", "note_validation", "invalid_key"):
            with self.subTest(cause=cause):
                self.rows = [cells(archive.INDEX_HEADERS), manual(), cells(generated("FictionalKeyA"))]
                self.generated = [generated("FictionalKeyA")]
                if cause == "duplicate": self.rows.append(deepcopy(self.rows[2]))
                if cause == "missing_note":
                    self.rows[2]["values"][12] = archive._index_cell("Keep my note")
                    self.generated = []
                if cause == "note_validation": self.rows[2]["values"][12]["dataValidation"] = {"strict": True}
                if cause == "invalid_key": self.rows[2]["values"][13] = {"userEnteredValue": {"formulaValue": '="key"'}}
                before = deepcopy(self.rows)
                self.assertEqual(self.refresh()["status"], "held")
                self.assertEqual(self.rows, before); self.assertEqual(self.writes, 0)

    def test_a_concurrent_manual_edit_before_writing_is_preserved_and_stops_the_refresh(self):
        for field, value in (("userEnteredValue", {"stringValue": "Teacher edit"}), ("note", "New note"),
                             ("userEnteredFormat", {"textFormat": {"italic": True}}),
                             ("dataValidation", {"condition": {"type": "BOOLEAN"}})):
            with self.subTest(field=field):
                self.rows = [cells(archive.INDEX_HEADERS), manual()]; self.reads = 0
                def change(number):
                    if number == 2: self.rows[1]["values"][0][field] = deepcopy(value)
                self.before_read = change
                result = self.refresh()
                self.assertEqual(result["status"], "held"); self.assertIn("changed during preparation", result["problem"])
                self.assertEqual(self.rows[1]["values"][0][field], value); self.assertEqual(self.writes, 0)

    def test_manual_readback_change_is_unconfirmed_and_never_retried(self):
        self.rows += [manual()]
        self.after_write = lambda: self.rows[1]["values"][0].update(note="Concurrent teacher note")
        result = self.refresh()
        self.assertEqual(result["status"], "held"); self.assertIn("not confirmed", result["problem"])
        self.assertEqual(self.writes, 1); self.assert_never_wrote(1)
        self.assertEqual(self.rows[1]["values"][0]["note"], "Concurrent teacher note")

    def test_uncertain_write_uses_exact_readback_without_a_second_write(self):
        self.rows += [manual()]; self.fail_write = True
        result = self.refresh()
        self.assertEqual(result["status"], "verified"); self.assertEqual(self.writes, 1); self.assert_never_wrote(1)

    def test_failed_write_without_saved_cells_is_held_without_retry(self):
        self.rows += [manual()]; before = deepcopy(self.rows)
        self.fail_write = True; self.apply_write = False
        result = self.refresh()
        self.assertEqual(result["status"], "held"); self.assertEqual(self.writes, 1)
        self.assertEqual(self.rows, before)

    def test_lost_claim_stops_before_any_sheet_write(self):
        self.rows += [manual()]
        with patch.object(CloudState, "renew", side_effect=Conflict("Claim changed")):
            self.assertEqual(self.refresh()["status"], "held")
        self.assertEqual(self.writes, 0)

    def test_destination_parent_or_ownership_change_stops_before_writing(self):
        for change in ("parent", "ownership", "access"):
            with self.subTest(change=change):
                self.rows = [cells(archive.INDEX_HEADERS), manual()]
                original = deepcopy(self.drive.items[SHEET]); reads = []
                def metadata(token, ident, fields=None):
                    if ident == SHEET:
                        reads.append(ident)
                        if len(reads) == 3:
                            if change == "parent": self.drive.items[SHEET]["parents"] = ["FictionalOtherParent"]
                            if change == "ownership": self.drive.items[SHEET]["properties"]["hubStudentWorkIndex"] = "FictionalOtherScope"
                            if change == "access": self.drive.items[SHEET]["permissions"].append({"type": "anyone", "role": "reader"})
                    return self.drive.metadata(token, ident, fields)
                with patch.object(google, "drive_file", side_effect=metadata):
                    self.assertEqual(self.refresh()["status"], "held")
                self.assertEqual(self.writes, 0)
                self.drive.items[SHEET] = original

    def test_partial_source_coverage_remains_held_and_manual_rows_are_not_verified_records(self):
        self.rows += [manual()]; self.holds = [{"class_key": "biology", "problem": "Source unavailable"}]
        result = self.refresh()
        self.assertEqual((result["status"], result["rows"], result["retained_manual_rows"]), ("held", 1, 1))
        self.assertEqual(result["holds"], self.holds)
        self.assertEqual(result["summary"], {"biology": {"work_copies": 1}})

    def test_retained_manual_rows_cannot_push_generated_records_beyond_the_limit(self):
        self.rows += [manual()]; self.generated = [generated("FictionalKey%d" % n) for n in range(3)]
        with patch.object(archive, "INDEX_ROW_LIMIT", 3):
            result = self.refresh()
        self.assertEqual(result["status"], "held"); self.assertIn("row limit", result["problem"])
        self.assertEqual(self.writes, 0)


if __name__ == "__main__":
    unittest.main()
