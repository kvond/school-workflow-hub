"""Synthetic roster parsing and conservative identity matching; no live calls."""
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters.roster_matching import (match_files, match_respondent,
    parse_roster, read_roster_snapshot, CLASS_TABS)
from school_workflow_hub.adapters.hosted_agenda import ConnectionError


def sheet(title, rows, start_row=0):
    return {"properties": {"title": title}, "data": [{"startRow": start_row,
        "rowData": [{"values": [{"formattedValue": cell} for cell in row]}
                    for row in rows]}]}


class RosterParsingTests(unittest.TestCase):
    def test_embedded_section_stays_separate_and_repeated_block_deduplicates(self):
        canonical = sheet("A3 Forensics", [["A3 Forensics"],
            ["Last Name", "First Name"], ["Example", "Alex (Lex)"],
            ["Fiction", "Jordan"]])
        embedded = sheet("A2 Forensics", [["A2 Forensics"],
            ["Last Name", "First Name"], ["Sample", "Morgan"], [],
            ["A3 Forensics"], ["Last Name", "First Name"],
            ["Fiction", "Jordan"], ["Example", "Alex (Lex)"]])
        rows = parse_roster({"sheets": [embedded, canonical]})
        self.assertEqual(len(rows), 3)
        self.assertEqual([(r["first"], r["section_name"]) for r in rows],
            [("Morgan", "A2 Forensics"), ("Alex (Lex)", "A3 Forensics"),
             ("Jordan", "A3 Forensics")])
        expected = parse_roster({"sheets": [canonical]})
        self.assertEqual(rows[1:], expected)
        person, reason = match_respondent("s.lex.example@school.example", rows,
                                          ["school.example"])
        self.assertIsNone(reason)
        self.assertEqual(person["section_name"], "A3 Forensics")
        self.assertEqual(person["roster_tab"], "A3 Forensics")
        self.assertEqual(person["roster_row"], 3)

    def test_embedded_only_section_uses_existing_canonical_identity(self):
        embedded = parse_roster({"sheets": [sheet("A2 Forensics", [
            ["A3 Forensics"], ["Example", "Alex"]], start_row=10)]})[0]
        canonical = parse_roster({"sheets": [sheet("A3 Forensics", [
            ["Example", "Alex"]])]})[0]
        self.assertEqual(embedded["section_name"], "A3 Forensics")
        self.assertEqual(embedded["learner_key"], canonical["learner_key"])
        self.assertEqual(embedded["display_id"], canonical["display_id"])
        self.assertEqual(embedded["roster_tab"], "A2 Forensics")
        self.assertEqual(embedded["roster_row"], 12)

    def test_conflicting_section_snapshots_hold_instead_of_merging(self):
        embedded = sheet("A2 Forensics", [["A3 Forensics"],
            ["Example", "Alex (Lex)"]])
        for canonical_rows in ([['Fiction', 'Jordan']],
                               [['Example', 'Alex (Al)']],
                               [['A3 Forensics'], ['Last Name', 'First Name']], []):
            with self.subTest(rows=canonical_rows):
                with self.assertRaisesRegex(ConnectionError, "section_blocks_conflict"):
                    parse_roster({"sheets": [embedded,
                        sheet("A3 Forensics", canonical_rows)]})

    def test_unknown_cross_class_or_conflicting_headings_hold(self):
        for heading in (["A9 Forensics"], ["A9 Forensics", "Comment"],
                        ["A4 Biology"], ["A3 Forensics", "A2 Forensics"],
                        ["First Name", "Last Name"], ["Full Name", "Section"]):
            with self.subTest(heading=heading):
                with self.assertRaisesRegex(ConnectionError, "layout_needs_review"):
                    parse_roster({"sheets": [sheet("A2 Forensics", [
                        ["Example", "Alex"], heading, ["Fiction", "Jordan"]])]})

    def test_identical_names_across_sections_are_not_deduplicated(self):
        rows = parse_roster({"sheets": [sheet("A2 Forensics", [
            ["Example", "Alex"], ["A3 Forensics"], ["Example", "Alex"]])]})
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["learner_key"] for r in rows}), 2)
        self.assertEqual(match_respondent("s.alex.example@school.example", rows,
            ["school.example"])[1], "roster_identity_ambiguous")

    def test_duplicate_names_within_a_block_remain_ambiguous(self):
        rows = parse_roster({"sheets": [sheet("A3 Forensics", [
            ["Example", "Alex"], ["Example", "Alex"]])]})
        self.assertEqual(len(rows), 2)
        self.assertEqual(match_respondent("s.alex.example@school.example", rows,
            ["school.example"])[1], "roster_identity_ambiguous")

    def test_headerless_layouts_keep_first_student_and_identity(self):
        pair = parse_roster({"sheets": [sheet("B3 A&P", [
            ["Example", "Alex Middle (Lex)"], [], ["Fiction", "Jordan"]])]})
        single = parse_roster({"sheets": [sheet("B3 A&P", [
            ["Example, Alex Middle (Lex)"], [], ["Fiction, Jordan"]])]})
        self.assertEqual(len(pair), 2)
        self.assertEqual([r["roster_row"] for r in pair], [1, 3])
        self.assertEqual([r["learner_key"] for r in pair],
                         [r["learner_key"] for r in single])
        self.assertEqual(pair[0]["layout"], "last_first_columns")
        self.assertEqual(single[0]["layout"], "last_comma_full_first")

    def test_first_last_headers_preserve_canonical_section_and_identity(self):
        expected = parse_roster({"sheets": [sheet("A3 Forensics", [["Example", "Alex"]])]})[0]
        actual = parse_roster({"sheets": [sheet("Renamed section", [["A3 Forensics"],
            ["First Name", "Last Name"], ["Alex", "Example"]])]},
            tab_mapping={"A3 Forensics": "Renamed section"})[0]
        self.assertEqual(actual["learner_key"], expected["learner_key"])
        self.assertEqual(actual["section_name"], "A3 Forensics")
        self.assertEqual(actual["roster_tab"], "Renamed section")
        self.assertEqual(actual["layout"], "first_last_columns")

    def test_private_renamed_tab_mapping_cannot_conflict_with_first_section_heading(self):
        with self.assertRaisesRegex(ConnectionError, "roster_tab_mapping_conflict"):
            parse_roster({"sheets": [sheet("Renamed section", [["A3 Forensics"],
                ["Example", "Alex"]])]}, tab_mapping={"A2 Forensics": "Renamed section"})

    def test_explicit_headerless_column_layout_is_respected(self):
        actual = parse_roster({"sheets": [sheet("A4 Biology", [["Alex", "Example"]])]},
                             column_layouts={"A4 Biology": "first_last_columns"})[0]
        expected = parse_roster({"sheets": [sheet("A4 Biology", [["Example", "Alex"]])]})[0]
        self.assertEqual(actual["learner_key"], expected["learner_key"])


class RosterReadTests(unittest.TestCase):
    def test_stable_sheet_ids_follow_renames_and_empty_sections_remain_authoritative(self):
        names = [name for sections in CLASS_TABS.values() for name in sections]
        data = {"sheets": [sheet("Renamed " + str(i), []) for i in range(4)]}
        for i, tab in enumerate(data["sheets"]):
            tab["properties"]["sheetId"] = i + 80
        data["sheets"][1] = sheet("Renamed 1", [["A3 Forensics"],
            ["First Name", "Last Name"], ["Alex", "Example"]])
        data["sheets"][1]["properties"]["sheetId"] = 81
        config = {"HUB_ROSTER_SPREADSHEET_ID": "r" * 24,
                  "HUB_ROSTER_TABS": {name: i + 80 for i, name in enumerate(names)},
                  "HUB_ROSTER_SECTION_IDS": {name: "section-" + str(i) for i, name in enumerate(names)}}
        with patch("school_workflow_hub.adapters.roster_matching.service_token"), \
             patch("school_workflow_hub.adapters.roster_matching.SheetsReader") as reader:
            reader.return_value.read.return_value = data
            result = read_roster_snapshot(config)
        self.assertEqual(len(result["sections"]), 4)
        self.assertTrue(result["section_mapping_complete"])
        self.assertEqual(result["entries"][0]["section_name"], "A3 Forensics")
        self.assertEqual(reader.return_value.read.call_args_list[1].args[1],
                         tuple("'Renamed %d'!A1:B200" % i for i in range(4)))

    def test_missing_or_reused_private_section_ids_are_not_authoritative(self):
        names = [name for sections in CLASS_TABS.values() for name in sections]
        data = {"sheets": [sheet(name, []) for name in names]}
        config = {"HUB_ROSTER_SPREADSHEET_ID": "r" * 24,
                  "HUB_ROSTER_SECTION_IDS": {"A2 Forensics": "all"}}
        with patch("school_workflow_hub.adapters.roster_matching.service_token"), \
             patch("school_workflow_hub.adapters.roster_matching.SheetsReader") as reader:
            reader.return_value.read.return_value = data
            result = read_roster_snapshot(config)
            self.assertEqual(result["sections"], [])
            self.assertFalse(result["section_mapping_complete"])
            config["HUB_ROSTER_SECTION_IDS"] = {name: "reused-id" for name in names}
            with self.assertRaisesRegex(ConnectionError, "roster_section_mapping_conflict"):
                read_roster_snapshot(config)


class RosterMatchingTests(unittest.TestCase):
    def entries(self):
        return [
            {"class_key": "forensics", "section_name": "A2 Forensics",
             "first": "Alex (Lex)", "last": "Example Vale",
             "name_key": "alex example vale",
             "learner_key": "roster:" + "a" * 64,
             "display_id": "roster:" + "a" * 24},
            {"class_key": "forensics", "section_name": "A3 Forensics",
             "first": "Kai-Rae'", "last": "Sample",
             "name_key": "kai rae sample",
             "learner_key": "roster:" + "b" * 64,
             "display_id": "roster:" + "b" * 24},
        ]

    def test_google_submitter_suffix_matches_current_roster(self):
        files = [
            {"id": "1" * 24,
             "name": "s.alex.examplevale — 09-21 16.59 — Copy - Alex Example Vale"},
            {"id": "2" * 24,
             "name": "s.kairae.sample — 09-18 22.10 — Copy - Kai-Rae' Sample"},
        ]
        result = match_files(files, "forensics", self.entries(),
                             {"A2 Forensics": "section-alpha", "A3 Forensics": "section-beta"})
        self.assertEqual(set(result), {"1" * 24, "2" * 24})
        self.assertEqual(result["1" * 24]["section_id"], "section-alpha")
        self.assertEqual(result["2" * 24]["section_id"], "section-beta")

    def test_multiple_files_keep_one_student_identity(self):
        files = [
            {"id": "1" * 24, "name": "deck one - Alex Example Vale"},
            {"id": "2" * 24, "name": "deck two - Alex Example Vale"},
        ]
        result = match_files(files, "forensics", self.entries())
        self.assertEqual(len(result), 2)
        self.assertEqual(len({row["student_id"] for row in result.values()}), 1)

    def test_non_roster_name_stays_unmatched(self):
        files = [{"id": "3" * 24, "name": "old deck - Former Student"}]
        self.assertEqual(match_files(files, "forensics", self.entries()), {})

    def test_ambiguous_roster_name_stays_unmatched(self):
        entries = self.entries() + [dict(self.entries()[0], section_name="A3 Forensics")]
        files = [{"id": "4" * 24, "name": "deck - Alex Example Vale"}]
        self.assertEqual(match_files(files, "forensics", entries), {})


if __name__ == "__main__":
    unittest.main()
