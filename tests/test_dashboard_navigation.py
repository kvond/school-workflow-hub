import copy
import unittest
from school_workflow_hub.adapters.dashboard_navigation import read_dashboard_navigation

SOURCE = "synthetic_agenda_source_12345"
TITLE = "Dashboard fixture"
LINK = "https://docs.google.com/document/d/synthetic_daily/edit"


class Reader:
    def __init__(self, cells):
        self.calls = []
        self.meta = {"spreadsheetId": SOURCE, "sheets": [{"properties": {"sheetId": 7, "title": TITLE}}]}
        self.grid = copy.deepcopy(self.meta)
        self.grid["sheets"][0]["data"] = [{"startRow": 1, "startColumn": 0, "rowData": [{"values": cells}]}]

    def read(self, source, ranges=()):
        self.calls.append((source, ranges))
        return copy.deepcopy(self.grid if ranges else self.meta)


class DashboardNavigationTests(unittest.TestCase):
    def read(self, reader):
        return read_dashboard_navigation(reader, spreadsheet_id=SOURCE, dashboard_title=TITLE)

    def test_reads_metadata_then_row_two_without_following(self):
        reader = Reader([{"formattedValue": "Mon14", "hyperlink": LINK}])
        result = self.read(reader)
        self.assertEqual(reader.calls, [(SOURCE, ()), (SOURCE, ("'Dashboard fixture'!A2:Z2",))])
        self.assertEqual(result["entries"][0]["destinations"], [LINK])
        self.assertFalse(result["daily_lessons_read"])
        self.assertFalse(result["link_access_verified"])

    def test_rich_text_and_literal_formula_links(self):
        cells = [{"formattedValue": "Mon14", "textFormatRuns": [{"format": {"link": {"uri": LINK}}}]},
                 {"formattedValue": "Tue15", "userEnteredValue": {"formulaValue": '=HYPERLINK("' + LINK + '","Tue15")'}}]
        self.assertEqual(len(self.read(Reader(cells))["entries"]), 2)

    def test_internal_gid_is_resolved_only_against_same_source(self):
        for cell in ({"formattedValue": "Mon14", "hyperlink": "#gid=8"},
                     {"formattedValue": "Mon14", "userEnteredValue": {"formulaValue": '=HYPERLINK("#gid=8","Mon14")'}}):
            result = self.read(Reader([cell]))
            self.assertEqual(result["entries"][0]["destinations"], [
                "https://docs.google.com/spreadsheets/d/" + SOURCE + "/edit#gid=8"])

    def test_multiple_links_are_not_silently_selected(self):
        cell = {"formattedValue": "Mon14 Tue15", "hyperlink": LINK,
                "textFormatRuns": [{"format": {"link": {"uri": LINK + "#second"}}}]}
        result = self.read(Reader([cell]))
        self.assertEqual(result["entries"][0]["status"], "ambiguous")
        self.assertEqual(result["status"], "needs_review")

    def test_unsafe_computed_and_prohibited_links_do_not_escape(self):
        for cell in ({"formattedValue": "Mon14", "hyperlink": "https://answerableteaching.com/private"},
                     {"formattedValue": "Mon14", "userEnteredValue": {"formulaValue": '=HYPERLINK(A1,"Mon14")'}}):
            result = self.read(Reader([cell]))
            self.assertEqual(result["entries"], [])
            self.assertEqual(result["status"], "needs_review")

    def test_bad_source_and_scope_are_not_empty_success(self):
        reader = Reader([])
        reader.grid["spreadsheetId"] = "different_source"
        self.assertEqual(self.read(reader)["status"], "blocked")
        reader = Reader([{"formattedValue": "Mon14"}])
        reader.grid["sheets"][0]["data"][0]["startRow"] = 2
        self.assertEqual(self.read(reader)["status"], "blocked")

    def test_unlinked_label_does_not_manufacture_url(self):
        result = self.read(Reader([{"formattedValue": "Mon14"}]))
        self.assertEqual(result["entries"][0]["status"], "unlinked")
        self.assertEqual(result["entries"][0]["destinations"], [])

    def test_missing_grid_is_not_a_verified_empty_row(self):
        reader = Reader([])
        reader.grid["sheets"][0]["data"] = []
        result = self.read(reader)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["issue_codes"], ["dashboard_row_data_unavailable"])
        self.assertEqual(self.read(Reader([]))["status"], "observed")

    def test_empty_out_of_scope_rows_and_duplicate_grids_are_rejected(self):
        for changes in ({"startRow": 2}, {"startRow": True}, {"startColumn": 1},
                        {"rowData": [{"values": []}, {"values": []}]}):
            reader = Reader([])
            reader.grid["sheets"][0]["data"][0].update(changes)
            result = self.read(reader)
            self.assertEqual(result["issue_codes"], ["dashboard_grid_scope_mismatch"])
        reader = Reader([])
        reader.grid["sheets"][0]["data"] *= 2
        self.assertEqual(self.read(reader)["status"], "blocked")

    def test_subsequent_requested_segment_retains_actual_cell_coordinates(self):
        reader = Reader([{"formattedValue": "Mon14", "hyperlink": LINK}])
        reader.grid["sheets"][0]["data"][0]["startColumn"] = 26
        result = read_dashboard_navigation(reader, spreadsheet_id=SOURCE,
                                           dashboard_title=TITLE, first_column=26, last_column=52)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["entries"][0]["cell"], "AA2")
        self.assertEqual(reader.calls[-1], (SOURCE, ("'Dashboard fixture'!AA2:AZ2",)))
