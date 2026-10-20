"""A generational name suffix (Jr., Sr., II-V) is optional when matching a student."""
import unittest

from hub_v2 import roster


def student(first, last, section="A4 Biology"):
    return {"class_key": "biology", "section": section, "first": first, "last": last}


class SuffixMatchTests(unittest.TestCase):
    students = [student("Leon", "Harrigan Jr."), student("Lah-Majj", "Harrigan"), student("Ana", "Rivera")]

    def test_upload_without_suffix_matches_the_one_suffixed_student(self):
        self.assertIs(roster.match_name("Leon Harrigan", self.students), self.students[0])
        self.assertIs(roster.match_name("Leon Harrigan Jr", self.students), self.students[0])

    def test_shared_surname_still_needs_the_first_name(self):
        self.assertIs(roster.match_name("Lah-Majj Harrigan", self.students), self.students[1])
        self.assertIsNone(roster.match_name("Harrigan", self.students))

    def test_suffix_is_never_a_wildcard_for_two_students(self):
        pair = [student("Leon", "Harrigan Jr."), student("Leon", "Harrigan")]
        self.assertIsNone(roster.match_name("Leon Harrigan", pair))

    def test_username_without_suffix_matches(self):
        self.assertIs(roster.match_username("s.leon.harrigan", self.students), self.students[0])

    def test_only_trailing_generational_suffixes_are_optional(self):
        self.assertEqual(roster.last_names("Harrigan Jr."), {"harrigan jr", "harrigan"})
        self.assertEqual(roster.last_names("Juniper"), {"juniper"})
        self.assertEqual(roster.last_names("Del Rosario Davila"), {"del rosario davila"})


if __name__ == "__main__":
    unittest.main()
