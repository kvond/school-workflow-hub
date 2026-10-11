"""Former-source review on fictional private archive evidence, with writes denied."""
from contextlib import ExitStack
from copy import deepcopy
import unittest
from unittest.mock import patch

import test_student_archive as af
from hub_v2 import google, review_context as rc, source_history_dispositions as history, student_archive as archive
from school_workflow_hub.core.cloud_state import CloudState, StoreUnavailable
from test_hub_v2 import fixture


class FormerStudentReviewTests(unittest.TestCase):
    def setUp(self):
        af.StudentArchiveTests.setUp(self)
        self.owner, self.copied, self.folder = af.StudentArchiveTests.former_student_fixture(self)
        self.roster = ({"biology": [{"first": "Current", "last": "Student", "section": "A4 Biology"}]}, [])
        self.current = {"class_key": "biology", "class_name": "Biology", "week": "Wk 04",
                        "form_id": "FictionalForm000000001", "lesson": "Energy Flow", "sources": []}
        self.data = af.found(af.row(matched=False, first="", last="", section="", email=""))
        self.before_state, self.before_drive = deepcopy(self.port.rows), deepcopy(self.drive.items)
        self.denial = ExitStack()
        self.addCleanup(self.denial.close)
        for target, name in [(archive._Run, "__init__"), (archive, "_put"), (archive, "_work_content"),
                             (CloudState, "create_task"), (CloudState, "claim"), (self.port, "commit"),
                             (google, "archive_create"), (google, "archive_file_id"), (google, "get_json"),
                             (google, "rename_item"), (google, "move_item")]:
            self.denial.enter_context(patch.object(target, name, side_effect=AssertionError("Review must not write or assess")))

    def archive(self, data=None):
        return af.StudentArchiveTests.archive(self, data)

    def render(self, data=None):
        source = data or self.data
        result = rc.build(self.current, source)
        return rc._former_context("read", self.config, self.current, source, result, self.roster)

    def test_exact_verified_history_has_original_and_copy_links_without_identity_or_grade_authority(self):
        before = deepcopy(self.data)
        result = self.render()
        self.assertEqual(result["notices"], [])
        item = result["former_student_work"][0]
        self.assertEqual(item["student"], {"first": "Ana", "last": "Rivera", "section": "Former students",
                         "verified": False, "former": True, "history_verified": True})
        self.assertEqual([s["file_id"] for s in item["sources"]], [af.SOURCE, self.copied["drive_file_id"]])
        self.assertNotIn("next_action", item)
        self.assertFalse(item["slides"][0]["verified"])
        self.assertFalse(result["source_selection"]["assessment_performed"])
        self.assertEqual(self.data, before)
        self.assertEqual(self.port.rows, self.before_state)
        self.assertEqual(self.drive.items, self.before_drive)

    def test_changed_source_copy_or_tag_retains_actual_review(self):
        mutations = [lambda: self.drive.items[af.SOURCE].update(version="2"),
                     lambda: self.drive.items[self.copied["drive_file_id"]].update(version="2"),
                     lambda: self.drive.items[self.copied["drive_file_id"]].update(md5Checksum="changed"),
                     lambda: self.drive.items[self.folder["drive_file_id"]]["properties"].update({archive.TAG: "foreign"}),
                     lambda: self.owner.update(current_roster_member=True),
                     lambda: self.owner.update(grade_authority=True)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.port.rows, self.drive.items = deepcopy(self.before_state), deepcopy(self.before_drive)
                self.owner = self.port.rows[self.owner["student_key"]].value
                mutate()
                result = self.render()
                self.assertNotIn("former_student_work", result)
                self.assertEqual(result["notices"][0]["issue_kind"], "identity_unverified")

    def test_active_current_name_school_email_or_native_identity_prevents_history(self):
        for current in [{"first": "Ana", "last": "Rivera", "section": "Different section"},
                        {"first": "Different", "last": "Name", "email": "S.ANA.RIVERA@school.example"},
                        {"first": "Different", "last": "Name", "ic_student_id": "fictional-native-id"}]:
            with self.subTest(current=current):
                self.owner["school_email"] = "s.ana.rivera@school.example"
                self.owner["student_id"] = "fictional-native-id"
                self.roster = ({"biology": [current]}, [])
                self.assertNotIn("former_student_work", self.render())

    def test_active_or_assessment_or_lesson_notices_are_never_converted(self):
        for source in [af.found(af.row(first="Different", last="Student")),
                       af.found(af.row(matched=False, first="", last="", section="", email=""))]:
            result = rc.build(self.current, source)
            result["notices"] = [rc.notice(self.current, {}, "Lesson availability failed", files=[{"id": af.SOURCE}]),
                rc.notice(self.current, af.row(), "Slide 2: A marked response needs review", assessed=True)]
            reviewed = rc._former_context("read", self.config, self.current, source, result, self.roster)
            self.assertEqual(reviewed, result)

    def test_unknown_or_mixed_originals_are_not_hidden_and_other_collection_history_is_visible(self):
        unknown = af.row(matched=False, first="", last="", section="", email="", files=[{"id": af.EARLIER, "mime": google.PPTX}])
        source = af.found(unknown)
        source["other_files"] = [self.data["lessons"][0]["forms"][0]["students"][0]]
        result = self.render(source)
        self.assertEqual(result["notices"][0]["sources"][0]["file_id"], af.EARLIER)
        self.assertEqual(result["former_student_work"][0]["sources"][0]["file_id"], af.SOURCE)
        mixed = deepcopy(self.data)
        mixed["lessons"][0]["forms"][0]["students"][0]["files"].append({"id": af.EARLIER})
        self.assertFalse(self.render(mixed).get("former_student_work"))

    def test_unavailable_private_store_or_google_is_availability_without_named_identity_request(self):
        for target, name, error in [(archive, "configured_store", StoreUnavailable("temporarily unavailable")),
                                     (google, "drive_file", google.GoogleError("Google 429", 429))]:
            with self.subTest(error=error), patch.object(target, name, side_effect=error):
                result = self.render()
                self.assertEqual(result["status"], "unavailable")
                self.assertFalse(result["notices"][0]["student"]["verified"])
                self.assertEqual(result["notices"][0]["affected_scope"], "lesson")
                self.assertNotIn("former_student_work", result)
                self.assertIn("student identity decision", " ".join(result["limitations"]))

    def test_source_or_copy_changes_during_bookend_retain_review(self):
        for ident in (af.SOURCE, self.copied["drive_file_id"]):
            calls = 0
            def metadata(token, fid, fields=None):
                nonlocal calls
                if fid == ident:
                    calls += 1
                    if calls == 2:
                        self.drive.items[fid]["version"] = "2"
                return self.drive.metadata(token, fid, fields)
            with self.subTest(ident=ident):
                self.drive.items = deepcopy(self.before_drive)
                with patch.object(google, "drive_file", side_effect=metadata):
                    result = self.render()
                self.assertTrue(result["notices"])
                self.assertNotIn("former_student_work", result)

    def test_empty_or_incomplete_roster_cannot_publish_history(self):
        for roster in [({}, []), ({"biology": []}, []), (self.roster[0], ["partial read"])]:
            with self.subTest(roster=roster):
                self.roster = roster
                result = self.render()
                self.assertEqual(result["status"], "unavailable")
                self.assertNotIn("former_student_work", result)

    def test_final_current_roster_drift_discards_named_former_history(self):
        catalog = {"assignments": [{"class": "biology", "week": "Wk 04", "form": self.current["form_id"]}]}
        after = ({"biology": [{"first": "Ana", "last": "Rivera", "section": "A4 Biology"}]}, [])
        agenda = fixture()
        with patch.object(rc, "scope", return_value=self.current), patch.object(rc.morning, "catalog", return_value=catalog), \
             patch.object(rc.work.roster, "load", side_effect=[self.roster, after]), \
             patch.object(rc.work, "lesson_work", return_value=self.data), \
             patch.object(rc, "cached_reviews", return_value=({}, [])), \
             patch.object(history, "apply", side_effect=lambda token, config, agenda, scope, found, result, roster: result):
            result = rc.read("read", self.config, agenda, "biology", "Wk 04", self.current["form_id"], reload_agenda=lambda: agenda)
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("former_student_work", result)
        self.assertIn("roster changed", " ".join(result["limitations"]))


    def test_foreign_original_or_copy_metadata_id_cannot_borrow_history(self):
        for ident in (af.SOURCE, self.copied["drive_file_id"]):
            def metadata(token, fid, fields=None):
                value = self.drive.metadata(token, fid, fields)
                if fid == ident:
                    value["id"] = "FictionalForeignMetadata01"
                return value
            with self.subTest(ident=ident), patch.object(google, "drive_file", side_effect=metadata):
                result = self.render()
                self.assertTrue(result["notices"])
                self.assertFalse(result.get("former_student_work"))


    def test_index_change_during_final_metadata_read_cannot_publish_history(self):
        calls = 0
        def metadata(token, fid, fields=None):
            nonlocal calls
            if fid == af.SOURCE:
                calls += 1
                if calls == 2:
                    index_key = archive.key("archiveclass", [af.ROOT, "biology"])
                    self.port.rows[index_key].value["historical_sources"].clear()
            return self.drive.metadata(token, fid, fields)
        with patch.object(google, "drive_file", side_effect=metadata):
            result = self.render()
        self.assertTrue(result["notices"])
        self.assertFalse(result.get("former_student_work"))
