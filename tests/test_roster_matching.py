import unittest

from school_workflow_hub.adapters.roster_matching import match_files


class RosterMatchingTests(unittest.TestCase):
    def entries(self):
        return [
            {"class_key": "forensics", "section_name": "A2 Forensics",
             "first": "Sabrina (Bri)", "last": "Del Rosario Davila",
             "name_key": "sabrina del rosario davila",
             "learner_key": "roster:" + "a" * 64,
             "display_id": "roster:" + "a" * 24},
            {"class_key": "forensics", "section_name": "A3 Forensics",
             "first": "Ava-Kasi'", "last": "Spraulding",
             "name_key": "ava kasi spraulding",
             "learner_key": "roster:" + "b" * 64,
             "display_id": "roster:" + "b" * 24},
        ]

    def test_google_submitter_suffix_matches_current_roster(self):
        files = [
            {"id": "1" * 24,
             "name": "s.sabrina.delrosariodavila — 09-21 16.59 — Copy - Sabrina Del Rosario Davila"},
            {"id": "2" * 24,
             "name": "s.avakasi.spraulding — 09-18 22.10 — Copy - Ava-Kasi' Spraulding"},
        ]
        result = match_files(files, "forensics", self.entries(),
                             {"A2 Forensics": "26738", "A3 Forensics": "26739"})
        self.assertEqual(set(result), {"1" * 24, "2" * 24})
        self.assertEqual(result["1" * 24]["section_id"], "26738")
        self.assertEqual(result["2" * 24]["section_id"], "26739")

    def test_multiple_files_keep_one_student_identity(self):
        files = [
            {"id": "1" * 24, "name": "deck one - Sabrina Del Rosario Davila"},
            {"id": "2" * 24, "name": "deck two - Sabrina Del Rosario Davila"},
        ]
        result = match_files(files, "forensics", self.entries())
        self.assertEqual(len(result), 2)
        self.assertEqual(len({row["student_id"] for row in result.values()}), 1)

    def test_non_roster_name_stays_unmatched(self):
        files = [{"id": "3" * 24, "name": "old deck - Former Student"}]
        self.assertEqual(match_files(files, "forensics", self.entries()), {})

    def test_ambiguous_roster_name_stays_unmatched(self):
        entries = self.entries() + [dict(self.entries()[0], section_name="A3 Forensics")]
        files = [{"id": "4" * 24, "name": "deck - Sabrina Del Rosario Davila"}]
        self.assertEqual(match_files(files, "forensics", entries), {})


if __name__ == "__main__":
    unittest.main()
