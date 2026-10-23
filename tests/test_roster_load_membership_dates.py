"""Synthetic exact Sheets transport; no live records or student identity inference."""
from copy import deepcopy
import json
import re
import unittest
from unittest.mock import patch

from hub_v2 import google, roster


class RosterLoadMembershipDatesTests(unittest.TestCase):
    def setUp(self):
        names = [name for sections in roster.SECTIONS.values() for name in sections]
        self.tabs = {name: 100 + i for i, name in enumerate(names)}
        self.config = {"HUB_ROSTER_SPREADSHEET_ID": "synthetic_roster_workbook",
                       "HUB_ROSTER_TABS": json.dumps(self.tabs),
                       "HUB_ROSTER_STATUS_COLUMNS": json.dumps({n: 3 for n in names}),
                       "HUB_ROSTER_COLUMN_LAYOUTS": json.dumps({n: "first_last_columns" for n in names})}
        self.sheets = {}
        for name, ident in self.tabs.items():
            self.sheets[name] = {"properties": {"title": name, "sheetId": ident,
                "gridProperties": {"rowCount": 200, "columnCount": 8}}, "values": [
                    ["First Name", "Last Name", "Roster status", "IC local student number", "IC membership key", "Start date", "End date", "IC checked at"],
                    ["Ada", "Fictional", "active", "00001", "exact-key", "2026-10-12", "", "2026-10-23T12:00:00Z"],
                    ["Bea", "Fictional", "incoming", "00002", "incoming-key", "2026-10-23", "", "2026-10-23T12:00:00Z"],
                    ["Cyd", "Fictional", "withdrawn", "00003", "withdrawn-key", "2026-08-24", "2026-10-01", "2026-10-23T12:00:00Z"]]}
        self.sheets["Study Hall"] = {"properties": {"title": "Study Hall", "sheetId": 999,
            "gridProperties": {"rowCount": 200, "columnCount": 9}}, "values": [["Not an instructional source"]]}
        self.requested = []

    def transport(self, url, token, params=None, **kwargs):
        self.assertEqual(token, "synthetic")
        if isinstance(params, dict):
            return {"properties": {"title": "Synthetic workbook"},
                    "sheets": [{"properties": deepcopy(s["properties"])} for s in self.sheets.values()]}
        ranges = [value for key, value in params if key == "ranges"]
        self.requested.extend(ranges)
        result = []
        for value in ranges:
            match = re.fullmatch(r"'(.+)'!A1:([A-Z])(\d+)", value)
            self.assertIsNotNone(match)
            title, column, row = match.groups()
            source = self.sheets[title.replace("''", "'")]
            columns, rows = ord(column)-64, int(row)
            grid = source["properties"]["gridProperties"]
            self.assertLessEqual(columns, grid["columnCount"], "Native range exceeds actual columns")
            self.assertLessEqual(rows, grid["rowCount"], "Native range exceeds actual rows")
            result.append({"properties": {"title": title}, "data": [{"rowData": [
                {"values": [{"formattedValue": str(v)} for v in r[:columns]]}
                for r in source["values"][:rows]]}]})
        return {"sheets": result}

    def load(self):
        with patch.object(google, "get_json", side_effect=self.transport):
            return roster.load("synthetic", self.config)

    def test_real_load_carries_start_dates_and_filters_status_without_study_hall(self):
        rows, problems = self.load()
        self.assertEqual(problems, [])
        self.assertEqual(sum(map(len, rows.values())), 4)
        self.assertTrue(all(s["added"] == "2026-10-12" for values in rows.values() for s in values))
        self.assertTrue(all(s["first"] == "Ada" for values in rows.values() for s in values))
        self.assertTrue(all(r.endswith("!A1:H200") for r in self.requested))
        self.assertFalse(any("Study Hall" in r for r in self.requested))
        self.assertTrue(all("student_id" not in s and "email" not in s for values in rows.values() for s in values))

    def test_existing_added_in_parser_scope_keeps_precedence_over_start_date(self):
        sheet = self.sheets["A4 Biology"]
        sheet["properties"]["gridProperties"]["columnCount"] = 10
        sheet["values"][0].extend(["Teacher field", "Added"])
        sheet["values"][1].extend(["Keep", "2026-09-02"])
        rows, problems = self.load()
        self.assertEqual(problems, [])
        self.assertEqual(rows["biology"][0]["added"], "2026-09-02")
        self.assertIn("'A4 Biology'!A1:J200", self.requested)

    def test_short_status_grid_never_requests_nonexistent_date_columns(self):
        for name in self.tabs:
            self.sheets[name]["properties"]["gridProperties"] = {"rowCount": 4, "columnCount": 3}
        rows, problems = self.load()
        self.assertEqual(problems, [])
        self.assertTrue(all(r.endswith("!A1:C4") for r in self.requested))
        self.assertTrue(all(s["added"] == "" for values in rows.values() for s in values))

    def test_status_outside_date_scope_is_still_read(self):
        name = "A4 Biology"
        sheet = self.sheets[name]
        sheet["properties"]["gridProperties"]["columnCount"] = 12
        for row in sheet["values"]:
            row.extend([""] * (12 - len(row)))
            row[11], row[2] = row[2], ""
        columns = json.loads(self.config["HUB_ROSTER_STATUS_COLUMNS"]); columns[name] = 12
        self.config["HUB_ROSTER_STATUS_COLUMNS"] = json.dumps(columns)
        rows, problems = self.load()
        self.assertEqual(problems, [])
        self.assertEqual(rows["biology"][0]["added"], "2026-10-12")
        self.assertIn("'A4 Biology'!A1:L200", self.requested)

    def test_unverified_grid_or_status_holds_complete_load(self):
        for change in ("missing_grid", "short_grid", "unknown_status"):
            with self.subTest(change=change):
                original = deepcopy(self.sheets["A4 Biology"])
                if change == "missing_grid":
                    self.sheets["A4 Biology"]["properties"].pop("gridProperties")
                elif change == "short_grid":
                    self.sheets["A4 Biology"]["properties"]["gridProperties"]["columnCount"] = 2
                else:
                    self.sheets["A4 Biology"]["values"][1][2] = "unavailable"
                rows, problems = self.load()
                self.assertEqual(rows, {})
                self.assertTrue(problems)
                self.sheets["A4 Biology"] = original

    def test_unconfigured_legacy_transport_keeps_original_range(self):
        self.config["HUB_ROSTER_STATUS_COLUMNS"] = "{}"
        rows, problems = self.load()
        self.assertEqual(problems, [])
        self.assertEqual(sum(map(len, rows.values())), 12)
        self.assertTrue(all(r.endswith("!A1:B200") for r in self.requested))


if __name__ == "__main__":
    unittest.main()
