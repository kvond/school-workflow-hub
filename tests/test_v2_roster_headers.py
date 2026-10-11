"""A roster header determines preceding rows only within its section block."""
import unittest
from hub_v2 import roster


def grid(rows):
    return {(row, column): {"formattedValue": value}
            for row, values in enumerate(rows, 1)
            for column, value in enumerate(values, 1) if value}


class RosterHeaderTests(unittest.TestCase):
    def setUp(self):
        roster._ORIENTATION.clear()

    def tearDown(self):
        roster._ORIENTATION.clear()

    def parse(self, rows, layouts=None):
        return roster.parse_tab(grid(rows), "forensics", "A2 Forensics", layouts)

    def test_sorted_first_last_header_applies_to_preceding_students(self):
        students, skipped = self.parse([["A2 Forensics"], ["Ana", "Rivera"],
            ["Chidi (CJ)", "Okafor"], ["First Name", "Last Name"], ["Sam", "Lee"]])
        self.assertEqual(skipped, 0)
        self.assertEqual([(row["last"], row["first"], row["order"]) for row in students],
            [("Rivera", "Ana", "header"), ("Okafor", "Chidi (CJ)", "header"), ("Lee", "Sam", "header")])
        roster._ORIENTATION[("forensics", "A2 Forensics")] = True
        roster.orient(students, {"riveraana", "okaforchidi"})
        self.assertEqual(students[0]["last"], "Rivera", "Explicit header does not depend on username cache.")

    def test_sorted_last_first_header_keeps_preceding_students(self):
        students, skipped = self.parse([["Rivera", "Ana"], ["Last Name", "First Name"], ["Lee", "Sam"]])
        self.assertEqual(skipped, 0)
        self.assertEqual([(row["last"], row["first"], row["order"]) for row in students],
                         [("Rivera", "Ana", "header"), ("Lee", "Sam", "header")])

    def test_other_section_header_cannot_orient_an_earlier_block(self):
        students, skipped = self.parse([["A2 Forensics"], ["Rivera", "Ana"],
            ["A3 Forensics"], ["Chidi", "Okafor"], ["First Name", "Last Name"]])
        self.assertEqual(skipped, 0)
        self.assertEqual([(row["last"], row["section"], row["order"]) for row in students],
                         [("Rivera", "A2 Forensics", "assumed"), ("Okafor", "A3 Forensics", "header")])

    def test_conflicting_headers_do_not_infer_earlier_rows(self):
        students, skipped = self.parse([["A2 Forensics"], ["Rivera", "Ana"],
            ["First Name", "Last Name"], ["Chidi", "Okafor"],
            ["Last Name", "First Name"], ["Lee", "Sam"]])
        self.assertEqual(skipped, 0)
        self.assertEqual([(row["last"], row["first"], row["order"]) for row in students],
                         [("Rivera", "Ana", "assumed"), ("Okafor", "Chidi", "header"), ("Lee", "Sam", "header")])

    def test_repeated_consistent_headers_are_unambiguous(self):
        students, _ = self.parse([["Ana", "Rivera"], ["First Name", "Last Name"],
                                 ["Chidi", "Okafor"], ["First Name", "Last Name"]])
        self.assertEqual([(row["last"], row["order"]) for row in students],
                         [("Rivera", "header"), ("Okafor", "header")])

    def test_explicit_layout_before_header_and_forward_header_behavior_are_preserved(self):
        students, _ = self.parse([["Rivera", "Ana"], ["First Name", "Last Name"],
                                 ["Chidi", "Okafor"]], {"A2 Forensics": "last_first_columns"})
        self.assertEqual([(row["last"], row["first"], row["order"]) for row in students],
                         [("Rivera", "Ana", "config"), ("Okafor", "Chidi", "header")])

    def test_comma_names_and_unreadable_rows_remain_explicit(self):
        students, skipped = self.parse([["Rivera, Ana"], ["Chidi", "Okafor"],
                                       ["First Name", "Last Name"], ["Only one name"]])
        self.assertEqual(skipped, 1)
        self.assertEqual([(row["last"], row["first"], row["order"]) for row in students],
                         [("Rivera", "Ana", "comma"), ("Okafor", "Chidi", "header")])


if __name__ == "__main__":
    unittest.main()
