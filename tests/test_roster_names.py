"""Synthetic tests for the single approved roster source: Rosters."""
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import roster_names as roster
from school_workflow_hub.adapters.hosted_agenda import ConnectionError


class FakeDrive:
    def __init__(self, files):
        self.files = files
    def get(self, path, params):
        self.path = path
        self.params = params
        return {"files": self.files, "incompleteSearch": False}


def sheet(title, rows):
    return {"properties": {"sheetId": 1 if title == "A2 Forensics" else 2,
                            "title": title},
            "data": [{"rowData": [{"values": [
                {"formattedValue": cell} for cell in row]} for row in rows]}]}


class RosterSourceTests(unittest.TestCase):
    def test_only_exact_rosters_title_is_accepted(self):
        exact = {"id": "r" * 24, "name": "Rosters",
                 "mimeType": roster.SPREADSHEET_MIME, "trashed": False}
        other = {"id": "x" * 24, "name": "Rosters Attendance and Grades",
                 "mimeType": roster.SPREADSHEET_MIME, "trashed": False}
        self.assertEqual(roster._find_exact_rosters(FakeDrive([exact, other])), exact["id"])

    def test_duplicate_exact_rosters_is_ambiguous(self):
        rows = [{"id": "r" * 24, "name": "Rosters",
                 "mimeType": roster.SPREADSHEET_MIME, "trashed": False},
                {"id": "s" * 24, "name": "Rosters",
                 "mimeType": roster.SPREADSHEET_MIME, "trashed": False}]
        with self.assertRaisesRegex(ConnectionError, "ambiguous"):
            roster._find_exact_rosters(FakeDrive(rows))

    def test_section_parser_stops_before_embedded_next_section(self):
        a2 = sheet("A2 Forensics", [
            ["Last Name", "First Name"],
            ["Del Rosario Davila", "Sabrina (Bri)"],
            ["Mestre Terreros", "Jamilet"],
            ["", ""],
            ["A3 Forensics", ""],
            ["Riggs", "Kamiyah"],
        ])
        parsed = dict(roster._parse_section(a2, "A2 Forensics"))
        self.assertIn("sabrina del rosario davila", parsed)
        self.assertIn("bri del rosario davila", parsed)
        self.assertIn("jamilet mestre terreros", parsed)
        self.assertNotIn("kamiyah riggs", parsed)

    def test_match_hint_returns_opaque_roster_identity_and_section(self):
        source = {"by_name": {
            "sabrina del rosario davila":
                {"display_name": "Sabrina Del Rosario Davila",
                 "section_name": "A2 Forensics"}}}
        result = roster.match_hint(source, "Sabrina Del Rosario Davila")
        self.assertTrue(result["student_id"].startswith("roster:"))
        self.assertEqual(result["student_name"], "Sabrina Del Rosario Davila")
        self.assertEqual(result["section_name"], "A2 Forensics")
        self.assertEqual(result["marker_precision"], "unknown")


if __name__ == "__main__":
    unittest.main()
