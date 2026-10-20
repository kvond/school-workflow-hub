"""Private archival acceptance using fictional Drive and disposable CAS state."""
from copy import deepcopy
from itertools import permutations
from collections import Counter
from concurrent.futures import Future
from datetime import datetime, timezone
import hashlib
import json
from io import BytesIO
from zipfile import ZipFile
from PIL import Image
from threading import Barrier, Lock
import unittest
from unittest.mock import Mock, patch

from hub_v2 import google, student_archive as archive, student_feedback_checklist
from school_workflow_hub.core.cloud_state import CloudState, StoreUnavailable
from tests.test_cloud_state import MemoryCAS

ROOT = "FictionalArchiveRoot00001"
COURSES = {"biology": "FictionalBiologyFolder001", "forensics": "FictionalForensicsFolder01",
           "anatomy": "FictionalAnatomyFolder001"}
SOURCE = "FictionalStudentSource001"
DOCS_IMAGE = "https://docs.google.com/slides-images-rt/Fictional-Opaque_Image0001=s1600?key=fictional"
EARLIER = "FictionalStudentEarlier01"
PERMISSIONS = [{"type": "user", "role": "owner", "emailAddress": "reader@fictional.example"},
               {"type": "user", "role": "writer", "emailAddress": "teacher@school.example"}]
REAL_ARCHIVE_CREATE = google.archive_create
REAL_ARCHIVE_FILE_ID = google.archive_file_id


class FakeDrive:
    def __init__(self):
        self.items, self.created, self.renamed, self.moved = {}, [], [], []
        self.copied, self.fail_copy, self.fail_after_copy, self.contents = [], False, False, {}
        self.sequence, self.fail_create, self.fail_after_create = 0, False, False
        self.add(ROOT, "Student records — Private 2026–27", google.FOLDER)
        for cls, ident in COURSES.items():
            self.add(ident, archive.CLASSES[cls], google.FOLDER, [ROOT])
        self.add(SOURCE, "Copy of Fictional Energy Flow - Ana Rivera.pptx", google.PPTX, ["OriginalFormUploadFolder"])
        self.add(EARLIER, "Earlier Fictional Energy Flow.pptx", google.PPTX, ["OriginalFormUploadFolder"])

    def add(self, ident, name, mime, parents=None):
        self.items[ident] = {"id": ident, "name": name, "mimeType": mime, "parents": parents or [],
            "trashed": False, "permissions": deepcopy(PERMISSIONS), "capabilities": {"canAddChildren": True},
            "properties": {}, "createdTime": "2026-10-08T12:00:00Z", "modifiedTime": "2026-10-08T12:00:00Z",
            "version": "1"}
        self.contents[ident] = ident.encode()
        return self.items[ident]

    def metadata(self, token, ident, fields=None):
        if ident not in self.items:
            raise google.GoogleError("Not found", 404)
        return deepcopy(self.items[ident])

    def children(self, token, parent):
        return [deepcopy(item) for item in self.items.values() if parent in item["parents"] and not item["trashed"]]

    def reserve(self, token, *, shortcut=False):
        self.sequence += 1
        return "FictionalReservedFile%05d" % self.sequence

    def create(self, token, metadata, pdf=None):
        self.created.append(deepcopy(metadata))
        if self.fail_create:
            raise google.GoogleError("Drive storage quota prevents this PDF upload. The saved feedback is still downloadable from the Hub.", 403)
        item = self.add(metadata["id"], metadata["name"], metadata["mimeType"], metadata["parents"])
        item["properties"] = deepcopy(metadata.get("properties", {}))
        if metadata.get("shortcutDetails"):
            item["shortcutDetails"] = deepcopy(metadata["shortcutDetails"])
        if pdf is not None:
            self.contents[metadata["id"]] = pdf
            item.update(md5Checksum=hashlib.md5(pdf).hexdigest(), size=str(len(pdf)))
        if self.fail_after_create:
            self.fail_after_create = False
            raise google.GoogleError("Drive response was lost")
        return deepcopy(item)

    def rename(self, token, ident, name):
        self.renamed.append(ident)
        self.items[ident]["name"] = name
        return deepcopy(self.items[ident])

    def move(self, token, ident, new_parent, old_parents):
        self.moved.append(ident)
        self.items[ident]["parents"] = [new_parent]
        return deepcopy(self.items[ident])

    def copy(self, url, token, params=None, timeout=20, method="GET", body=None):
        if method != "POST" or not url.endswith("/copy"):
            raise AssertionError("Unexpected Google write")
        source_id = url.split("/")[-2]
        self.copied.append(source_id)
        if self.fail_copy:
            raise google.GoogleError("Drive did not confirm this copy")
        ident = self.reserve(token)
        item = self.add(ident, body["name"], self.items[source_id]["mimeType"], body["parents"])
        item["properties"] = deepcopy(body["properties"])
        self.contents[ident] = self.contents[source_id]
        item.update(md5Checksum=hashlib.md5(self.contents[ident]).hexdigest(), size=str(len(self.contents[ident])))
        if self.fail_after_copy:
            self.fail_after_copy = False
            raise google.GoogleError("Copy response lost")
        return {"id": ident}

    def content(self, token, meta, heartbeat=None):
        raw = self.contents[meta["id"]]
        return {"sha256": hashlib.sha256(raw).hexdigest(), "basis": "file_bytes", "size": len(raw),
                "md5": hashlib.md5(raw).hexdigest()}


def config():
    return {archive.ROOT_CONFIG: ROOT, archive.COURSES_CONFIG: json.dumps(COURSES),
            "HUB_SCHOOL_EMAIL_DOMAINS": "school.example", "HUB_SUBMISSIONS_ROOT_FOLDER": "FictionalTurnInRoot00001"}


def row(**updates):
    return {"matched": True, "matched_by": "email", "source": "form", "email": "s.ana.rivera@school.example",
            "first": "Ana", "last": "Rivera", "section": "A4 Biology", "submitted_at": "2026-10-07T13:00:00Z",
            "files": [{"id": SOURCE, "name": "Fictional Energy Flow", "mime": google.PPTX}], "earlier": [], **updates}


def found(*rows):
    return {"class_key": "biology", "week": "Wk 04", "lessons": [{"topic": "Energy Flow", "forms": [{
        "form_id": "FictionalForm000000001", "label": "Energy Flow — Turn In", "problem": None,
        "students": list(rows or [row()]), "late": []}]}], "other_files": [], "problems": []}


def saved():
    identity = row()
    return {"id": "feedbackdraft_" + "a" * 64, "saved_at": "2026-10-07T18:00:00Z",
            "scope": {"class": "biology", "form": "FictionalForm000000001", "file": SOURCE, "week": "Wk 04"},
            "assignment": {"assignment_title": "Energy Flow"}, "source": {"sha256": "b" * 64},
            "archive_identity": {"school_email": identity["email"], "first": "Ana", "last": "Rivera",
                                 "section": "A4 Biology", "matched_by": "email", "source": "form"}}


def name_roster(*extra):
    return [{"class_key": "biology", "first": "Ana", "last": "Rivera", "section": "A4 Biology", "order": "header"}, *extra]


class StudentArchiveTests(unittest.TestCase):
    def test_listing_feedback_dates_and_types_use_saved_artifact_metadata_only(self):
        self.assertEqual(self.archive()["status"], "verified")
        self.assertEqual(archive.file_feedback("read", "write", self.config, saved(), b"%PDF saved report")["status"], "verified")
        listing = archive.archive_listing("read", self.config, "biology")
        records = listing["students"][0]["records"]
        report = next(item for item in records if item["kind"] == "feedback")
        self.assertEqual((report["report_type"], report["feedback_date"], report["feedback_date_basis"]),
                         ("critical_aspects", "2026-10-07", "saved_at"))
        self.assertNotIn("feedback_date", next(item for item in records if item["kind"] == "work"))

    def test_listing_report_dates_never_substitute_submission_or_legacy_file_dates(self):
        base = {"kind": "feedback", "name": "Science writing contract", "date": "1999-01-01",
                "created_at": "2000-01-01T12:00:00Z", "source_modified_at": "2001-01-01T12:00:00Z",
                "saved_at": "2026-10-09T01:00:00Z"}
        for report_type, origin in (("science_writing", "teacher_science_writing"),
                                    ("missing_work_contract", "current_source_work_check")):
            result = archive._listing_feedback({**base, "report_type": report_type, "feedback_origin": origin})
            self.assertEqual((result["report_type"], result["feedback_date"], result["feedback_date_basis"]),
                             (report_type, "2026-10-08", "saved_at"))
        published = archive._listing_feedback({**base, "report_type": "science_writing", "feedback_origin": "teacher_science_writing",
            "publication_day_basis": "native-writing-first-drive-created-at/v1"})
        self.assertEqual((published["feedback_date"], published["feedback_date_basis"]),
                         ("2000-01-01", "native-writing-first-drive-created-at/v1"))
        for metadata in ({**base, "feedback_origin": "legacy_packet"},
                         {**base, "record_key": "archivelegacyfeedback_" + "e" * 64},
                         {**base, "saved_at": None}, {**base, "saved_at": "unavailable"}):
            result = archive._listing_feedback(metadata)
            self.assertIsNone(result["report_type"])
            self.assertIsNone(result["feedback_date"])
            self.assertEqual(result["feedback_date_basis"], "unverified")

    def setUp(self):
        self.drive, self.port, self.config = FakeDrive(), MemoryCAS(), config()
        self.patches = [patch.object(student_feedback_checklist, "refresh_run", return_value={"status": "verified"}),
                        patch.object(archive, "configured_store", return_value=self.port),
                        patch.object(google, "drive_file", side_effect=self.drive.metadata),
                        patch.object(google, "folder_children", side_effect=self.drive.children),
                        patch.object(google, "archive_file_id", side_effect=self.drive.reserve),
                        patch.object(google, "archive_create", side_effect=self.drive.create),
                        patch.object(google, "rename_item", side_effect=self.drive.rename),
                        patch.object(google, "move_item", side_effect=self.drive.move)]
        self.patches += [patch.object(google, "get_json", side_effect=self.drive.copy),
                         patch.object(archive, "_work_content", side_effect=self.drive.content)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def archive(self, data=None):
        return archive.archive_work("read", "write", self.config, "biology", data or found())

    def former_student_fixture(self):
        self.assertEqual(self.archive()["status"], "verified")
        owner_doc = next(d for d in self.port.rows.values() if d.value.get("student_key") and d.value.get("items"))
        copied_doc = next(d for d in self.port.rows.values() if d.value.get("source_kind") == "submitted_work_copy")
        teacher = {"basis": "teacher_confirmed_former_student", "status": "former_student", "class_key": "biology",
                   "name": "Rivera, Ana", "current_ic_verified": False, "confirmed_on": "2026-10-09"}
        run = archive._Run("read", "write", self.config, "biology")
        folder = run.create(archive.key("archiveformersection", [ROOT, "biology"]), "Former students", COURSES["biology"], google.FOLDER)
        self.drive.move("write", owner_doc.value["drive_file_id"], folder["drive_file_id"], [owner_doc.value["parent"]])
        owner_doc.value.update(parent=folder["drive_file_id"], class_key="biology", section="Former students",
            historical_status="former_student", current_roster_member=False, grade_authority=False,
            identity_status="identity_unverified", former_student_proof=teacher)
        copied_doc.value.update(historical_status="former_student", identity_status="identity_unverified")
        index = self.port.rows[run.index_key].value
        index["historical_sources"] = {SOURCE: {"basis": "teacher_confirmed_former_student_with_verified_work_copy",
            "teacher_status": teacher, "source_revision": deepcopy(copied_doc.value["source_revision"]),
            "student_key": owner_doc.value["student_key"], "student_folder_id": owner_doc.value["drive_file_id"],
            "copy_record_key": copied_doc.value["record_key"], "copy_id": copied_doc.value["drive_file_id"]}}
        run.finish({"status": "verified"})
        return owner_doc.value, copied_doc.value, folder

    def test_known_unchanged_former_source_stays_historical_without_current_roster_or_copy_retry(self):
        owner, copied, folder = self.former_student_fixture()
        data = found(row(matched=False, first="", last="", section="", source="folder", email=""))
        original_data = deepcopy(data); original_source = deepcopy(self.drive.items[SOURCE]); prior_owner = deepcopy(owner)
        copies, folders = len(self.drive.copied), len(self.drive.created)
        with patch.object(archive, "_filing_roster", side_effect=AssertionError("Historical work must not reclaim the active roster")):
            result = self.archive(data)
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual(result["historical_preserved"], 1)
        self.assertEqual(result["filed"], 0)
        self.assertEqual(result["held"], [])
        self.assertEqual(data, original_data)
        self.assertEqual(self.drive.items[SOURCE], original_source)
        self.assertEqual(self.port.rows[owner["student_key"]].value, prior_owner)
        self.assertEqual((len(self.drive.copied), len(self.drive.created)), (copies, folders))

    def test_historical_revision_owner_copy_and_private_location_changes_cannot_skip_filing(self):
        owner, copied, folder = self.former_student_fixture()
        snapshot = deepcopy(self.port.rows); drive = deepcopy(self.drive.items)
        data = found(row(matched=False, first="", last="", section="", source="folder", email=""))
        index_key = archive.key("archiveclass", [ROOT, "biology"])
        mutations = [lambda: self.drive.items[SOURCE].update(version="2"),
            lambda: self.drive.items[copied["drive_file_id"]].update(modifiedTime="2026-10-10T12:00:00Z"),
            lambda: self.drive.items[copied["drive_file_id"]].update(md5Checksum="ChangedContent"),
            lambda: self.drive.items[copied["drive_file_id"]]["permissions"].append({"type": "anyone", "role": "reader"}),
            lambda: self.port.rows[owner["student_key"]].value.update(current_roster_member=True),
            lambda: self.port.rows[owner["student_key"]].value.update(grade_authority=True),
            lambda: self.port.rows[owner["student_key"]].value["items"].pop(copied["record_key"]),
            lambda: self.port.rows[index_key].value["historical_sources"][SOURCE].update(copy_id="DifferentCopy"),
            lambda: self.drive.items[folder["drive_file_id"]]["properties"].update({archive.TAG: "DifferentHistoryFolder"})]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.port.rows = deepcopy(snapshot); self.drive.items = deepcopy(drive); mutate()
                before = len(self.drive.copied)
                result = self.archive(data)
                self.assertEqual(result["status"], "held")
                self.assertNotIn("historical_preserved", result)
                self.assertEqual(len(self.drive.copied), before)

    def test_historical_skip_does_not_hide_a_new_source_or_conflicting_current_student(self):
        owner, copied, folder = self.former_student_fixture()
        unknown = row(matched=False, first="", last="", section="", source="folder", email="",
            files=[{"id": EARLIER, "name": "Unconfirmed current student submission", "mime": google.PPTX}])
        with patch.object(archive, "_filing_roster", return_value=name_roster()):
            result = self.archive(found(row(matched=False, first="", last="", section="", source="folder", email=""), unknown))
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["historical_preserved"], 1)
        self.assertEqual(len(result["held"]), 1)
        conflicting = self.archive(found(row(first="Different", last="Student")))
        self.assertEqual(conflicting["status"], "held")
        self.assertIn("attributed to another student", conflicting["held"][0]["problem"])

    def test_unconfigured_archive_makes_no_drive_or_state_write(self):
        result = archive.archive_work("read", "write", {}, "biology", found())
        self.assertFalse(result["configured"])
        self.assertEqual(self.drive.created, [])
        self.assertEqual(self.port.rows, {})

    def test_actual_work_copy_contains_source_and_repeat_does_not_copy_again(self):
        original = deepcopy(self.drive.items[SOURCE])
        self.assertEqual(self.archive()["status"], "verified")
        self.assertEqual(self.drive.items[SOURCE], original)
        copied = next(doc.value for doc in self.port.rows.values() if doc.value.get("source_kind") == "submitted_work_copy")
        self.assertEqual(self.drive.contents[copied["drive_file_id"]], self.drive.contents[SOURCE])
        self.assertEqual(copied["source_file_id"], SOURCE)
        self.assertEqual(copied["source_revision"]["parents"], ["OriginalFormUploadFolder"])
        self.assertEqual(self.archive()["status"], "verified")
        self.assertEqual(self.drive.copied, [SOURCE])
        records = archive.archive_listing("read", self.config, "biology")["students"][0]["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["drive_file_id"], copied["drive_file_id"])

    def records_layout(self):
        records = "FictionalHiddenHubRecords01"
        self.drive.add(records, "Hub Records", google.FOLDER, [ROOT])
        self.config[archive.RECORDS_CONFIG] = records
        for ident in COURSES.values(): self.drive.items[ident]["parents"] = [records]
        return records

    def test_explicit_private_records_container_and_old_default_are_both_supported(self):
        self.assertEqual(archive.feedback_location("read", self.config, "biology")[1]["parents"], [ROOT])
        records = self.records_layout()
        self.assertEqual(archive.feedback_location("read", self.config, "biology")[1]["parents"], [records])
        self.assertEqual(self.archive()["status"], "verified")
        self.assertEqual(archive.archive_listing("read", self.config, "biology")["status"], "verified")
        self.drive.items[records]["parents"] = ["WrongPrivateParent00001"]
        with self.assertRaises(archive.Held): archive.feedback_location("read", self.config, "biology")

    def test_public_records_container_is_held_before_any_source_or_destination_write(self):
        records = self.records_layout()
        self.drive.items[records]["permissions"] = [{"type": "anyone", "role": "reader"}]
        original = deepcopy(self.drive.items[SOURCE])
        self.assertEqual(self.archive()["status"], "held")
        self.assertEqual(self.drive.created, [])
        self.assertEqual(self.drive.items[SOURCE], original)

    def test_retirement_preserves_source_history_and_never_recreates_student_navigation(self):
        original = deepcopy(self.drive.items[SOURCE])
        self.assertEqual(self.archive()["status"], "verified")
        navigation = next(d.value for d in self.port.rows.values() if d.value.get("mime") == archive.SHORTCUT)
        source_id = navigation["drive_file_id"]
        created_shortcuts = sum(item["mimeType"] == archive.SHORTCUT for item in self.drive.created)
        self.records_layout()
        result = archive.retire_source_links("read", "write", self.config, "biology")
        self.assertEqual((result["status"], result["retired"]), ("verified", 1))
        retired = self.port.rows[navigation["record_key"]].value
        self.assertTrue(retired["retired_navigation"])
        self.assertEqual(retired["drive_file_id"], source_id)
        self.assertEqual(retired["submitted_at"], navigation["submitted_at"])
        self.assertEqual(self.drive.items[source_id]["parents"], [retired["archiveparent"]])
        student = self.port.rows[retired["student_key"]].value
        self.assertNotIn(retired["record_key"], student["items"])
        self.assertEqual(archive.archive_listing("read", self.config, "biology")["counts"]["work"], 1)
        self.assertEqual(self.archive()["status"], "verified")
        self.assertEqual(archive.retire_source_links("read", "write", self.config, "biology")["retired"], 0)
        self.assertEqual(sum(item["mimeType"] == archive.SHORTCUT for item in self.drive.created), created_shortcuts)
        self.assertEqual(self.drive.items[SOURCE], original)

    def test_new_navigation_is_central_only_after_a_matching_content_copy(self):
        self.records_layout()
        self.assertEqual(self.archive()["status"], "verified")
        navigation = next(d.value for d in self.port.rows.values() if d.value.get("mime") == archive.SHORTCUT)
        self.assertTrue(navigation["retired_navigation"])
        student = self.port.rows[navigation["student_key"]].value
        self.assertNotIn(navigation["record_key"], student["items"])
        copied = self.port.rows[navigation["work_copy_record_key"]].value
        self.assertEqual(self.drive.contents[copied["drive_file_id"]], self.drive.contents[SOURCE])
        self.assertEqual(archive.archive_listing("read", self.config, "biology")["counts"]["work"], 1)

    def central_link_without_retired_flag(self):
        self.records_layout()
        self.assertEqual(self.archive()["status"], "verified")
        doc = next(d for d in self.port.rows.values() if d.value.get("mime") == archive.SHORTCUT)
        self.assertTrue(doc.value["retired_navigation"])
        doc.value.pop("retired_navigation")
        return doc.value

    def test_central_original_link_without_retired_flag_reconciles_with_its_registered_parent(self):
        link = self.central_link_without_retired_flag()
        created, copied, moved = len(self.drive.created), list(self.drive.copied), list(self.drive.moved)
        self.assertEqual(self.archive()["status"], "verified")
        current = self.port.rows[link["record_key"]].value
        self.assertEqual((current["parent"], current["drive_file_id"]), (link["parent"], link["drive_file_id"]))
        self.assertTrue(current["retired_navigation"])
        self.assertEqual(self.drive.items[link["drive_file_id"]]["parents"], [link["parent"]])
        self.assertEqual((len(self.drive.created), self.drive.copied, self.drive.moved), (created, copied, moved))
        student = self.port.rows[link["student_key"]].value
        self.assertNotIn(link["record_key"], student["items"])

    def test_unregistered_or_changed_parent_still_holds_the_original_link(self):
        link = self.central_link_without_retired_flag()
        link["parent"] = "FictionalOtherFolder00001"
        result = self.archive()
        self.assertEqual(result["status"], "held")
        self.assertIn("different source or student", json.dumps(result))
        link["parent"] = self.port.rows[archive.key("archivesourcelinks", [ROOT, "biology"])].value["drive_file_id"]
        self.drive.items[link["drive_file_id"]]["parents"] = ["FictionalOtherFolder00001"]
        self.assertEqual(self.archive()["status"], "held")
        self.assertEqual(self.drive.items[link["drive_file_id"]]["parents"], ["FictionalOtherFolder00001"])

    def test_mismatched_real_copy_cannot_retire_or_detach_original_link(self):
        self.assertEqual(self.archive()["status"], "verified")
        self.records_layout()
        copied = next(d.value for d in self.port.rows.values() if d.value.get("source_kind") == "submitted_work_copy")
        self.drive.contents[copied["drive_file_id"]] = b"Different actual copied content"
        navigation = next(d.value for d in self.port.rows.values() if d.value.get("mime") == archive.SHORTCUT)
        parent = self.drive.items[navigation["drive_file_id"]]["parents"]
        result = archive.retire_source_links("read", "write", self.config, "biology")
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["retired"], 0)
        self.assertEqual(self.drive.items[navigation["drive_file_id"]]["parents"], parent)
        self.assertIn(navigation["record_key"], self.port.rows[navigation["student_key"]].value["items"])

    def test_retirement_requires_verified_copy_and_recovers_a_confirmed_move(self):
        self.assertEqual(self.archive()["status"], "verified")
        self.records_layout()
        navigation = next(d.value for d in self.port.rows.values() if d.value.get("mime") == archive.SHORTCUT)
        copy_key, copied = next((k, d) for k, d in self.port.rows.items()
                               if d.value.get("source_kind") == "submitted_work_copy")
        self.port.commit({copy_key: ({**copied.value, "status": "creating"}, copied.version)})
        result = archive.retire_source_links("read", "write", self.config, "biology")
        self.assertEqual((result["status"], result["retired"]), ("held", 0))
        self.assertEqual(self.drive.moved, [])
        copied = self.port.rows[copy_key]
        self.port.commit({copy_key: ({**copied.value, "status": "verified"}, copied.version)})
        def lost_move(*args):
            self.drive.move(*args)
            raise google.GoogleError("Move response lost")
        with patch.object(google, "move_item", side_effect=lost_move):
            result = archive.retire_source_links("read", "write", self.config, "biology")
        self.assertEqual((result["status"], result["retired"]), ("verified", 1))
        self.assertEqual(self.drive.moved, [navigation["drive_file_id"]])

    def test_retired_link_retains_form_history_when_new_source_revision_is_filed(self):
        self.assertEqual(self.archive()["status"], "verified")
        self.records_layout()
        self.assertEqual(archive.retire_source_links("read", "write", self.config, "biology")["retired"], 1)
        navigation = next(d.value for d in self.port.rows.values() if d.value.get("mime") == archive.SHORTCUT)
        self.drive.items[SOURCE].update(version="2", modifiedTime="2026-10-09T12:00:00Z")
        self.drive.contents[SOURCE] = b"A later source revision"
        data = found(row(source="folder", email="", matched_by="filename", submitted_at=None))
        with patch.object(archive, "_filing_roster", return_value=name_roster()):
            self.assertEqual(self.archive(data)["status"], "verified")
        retired = self.port.rows[navigation["record_key"]].value
        self.assertEqual(retired["submitted_at"], navigation["submitted_at"])
        self.assertEqual(retired["form_id"], navigation["form_id"])
        self.assertEqual(retired["retired_source_revision"]["version"], "2")
        self.assertEqual(sum(item["mimeType"] == archive.SHORTCUT for item in self.drive.created), 1)
        self.assertEqual(archive.archive_listing("read", self.config, "biology")["counts"]["work"], 2)

    def test_new_source_revision_retains_both_exact_dated_copies(self):
        self.assertEqual(self.archive()["status"], "verified")
        self.drive.items[SOURCE]["version"] = "2"
        self.drive.items[SOURCE]["modifiedTime"] = "2026-10-09T12:00:00Z"
        self.drive.contents[SOURCE] = b"Revised fictional work"
        self.assertEqual(self.archive()["status"], "verified")
        copies = [d.value for d in self.port.rows.values() if d.value.get("source_kind") == "submitted_work_copy"]
        self.assertEqual(len(copies), 2)
        self.assertEqual({c["source_revision"]["version"] for c in copies}, {"1", "2"})
        self.assertEqual(len({c["content_proof"]["sha256"] for c in copies}), 2)

    def test_lost_copy_response_recovers_exact_created_file_without_second_post(self):
        self.drive.fail_after_copy = True
        self.assertEqual(self.archive()["status"], "verified")
        self.assertEqual(self.archive()["status"], "verified")
        self.assertEqual(self.drive.copied, [SOURCE])

    def test_known_copy_id_verifies_directly_when_folder_listing_lags(self):
        def lagging_children(token, parent):
            return [item for item in self.drive.children(token, parent)
                    if not (item.get("properties") or {}).get("hubCopiedSource")]
        with patch.object(google, "folder_children", side_effect=lagging_children):
            self.assertEqual(self.archive()["status"], "verified")
        self.assertEqual(self.drive.copied, [SOURCE])
        copied = next(d.value for d in self.port.rows.values() if d.value.get("source_kind") == "submitted_work_copy")
        self.assertEqual(copied["status"], "verified")
        self.assertEqual(self.drive.contents[copied["drive_file_id"]], self.drive.contents[SOURCE])

    def test_unknown_copy_id_with_no_tagged_result_stays_fenced(self):
        self.drive.fail_after_copy = True
        def lagging_children(token, parent):
            return [item for item in self.drive.children(token, parent)
                    if not (item.get("properties") or {}).get("hubCopiedSource")]
        with patch.object(google, "folder_children", side_effect=lagging_children):
            self.assertEqual(self.archive()["status"], "held")
            self.assertEqual(self.archive()["status"], "held")
        self.assertEqual(self.drive.copied, [SOURCE])
        copied = next(d.value for d in self.port.rows.values() if d.value.get("source_kind") == "submitted_work_copy")
        self.assertEqual(copied["status"], "creating")
        self.assertIsNone(copied.get("drive_file_id"))
        self.assertIn("copy_error_status", copied)

    def export_fixture(self):
        self.drive.items[SOURCE]["mimeType"] = "application/vnd.google-apps.presentation"
        native_proof = {"basis": "native_presentation/v1", "sha256": "d" * 64, "slides": 1, "images": 0}
        def content(token, meta, heartbeat=None):
            return native_proof if meta["mimeType"] == "application/vnd.google-apps.presentation" else self.drive.content(token, meta)
        self.content_patch = patch.object(archive, "_work_content", side_effect=content)
        self.content_patch.start(); self.addCleanup(self.content_patch.stop)
        output = BytesIO()
        with ZipFile(output, "w") as z: z.writestr("ppt/slides/slide1.xml", "<slide><text>Fictional answer</text></slide>")
        raw = output.getvalue()
        self.bytes_patch = patch.object(archive, "_work_bytes", side_effect=lambda token, meta, heartbeat=None:
                                      raw if meta["id"] == SOURCE else self.drive.contents[meta["id"]])
        self.bytes_mock = self.bytes_patch.start(); self.addCleanup(self.bytes_patch.stop)
        self.drive.fail_copy = True
        self.assertEqual(self.archive()["status"], "held")
        self.drive.fail_copy = False
        blocked_key = next(k for k, d in self.port.rows.items() if d.value.get("source_kind") == "submitted_work_copy")
        student_key = self.port.rows[blocked_key].value["student_key"]
        return {"student_key": student_key, "blocked_copy_key": blocked_key,
                "expected_revision": archive._source_revision(self.drive.items[SOURCE])}, raw

    def export(self, args):
        return archive.file_work_export("read", "write", self.config, "biology", args["student_key"], SOURCE,
            expected_revision=args["expected_revision"], blocked_copy_key=args["blocked_copy_key"])

    def test_explicit_export_preserves_native_uncertainty_and_reuses_exact_snapshot(self):
        args, raw = self.export_fixture()
        before = deepcopy(self.port.rows[args["blocked_copy_key"]])
        original = deepcopy(self.drive.items[SOURCE])
        result = self.export(args)
        self.assertEqual(result["status"], "verified")
        exported = self.port.rows[result["record_key"]].value
        self.assertEqual(exported["source_kind"], "submitted_work_export")
        self.assertEqual(exported["mime"], google.PPTX)
        self.assertIn("Exported work snapshot", exported["name"])
        self.assertEqual(exported["submitted_at"], row()["submitted_at"])
        self.assertEqual(self.drive.contents[result["drive_file_id"]], raw)
        self.assertEqual(self.port.rows[args["blocked_copy_key"]], before)
        self.assertEqual(self.drive.items[SOURCE], original)
        self.assertEqual(self.export(args)["drive_file_id"], result["drive_file_id"])
        self.assertEqual(self.archive()["status"], "verified")
        self.assertEqual(self.drive.copied, [SOURCE])
        self.assertEqual(sum(a.args[1]["id"] == SOURCE for a in self.bytes_mock.call_args_list), 1)
        self.assertEqual(archive.archive_listing("read", self.config, "biology")["counts"]["work"], 1)
        self.drive.items[result["drive_file_id"]]["md5Checksum"] = "f" * 32
        self.assertEqual(archive.archive_listing("read", self.config, "biology")["status"], "held")

    def test_export_fence_stops_uncertain_upload_retry_and_recovers_known_created_id(self):
        args, _ = self.export_fixture()
        self.drive.fail_after_create = True
        self.assertEqual(self.export(args)["status"], "verified")
        self.assertEqual(self.export(args)["status"], "verified")
        self.assertEqual(sum(i["mimeType"] == google.PPTX for i in self.drive.created), 1)
        self.drive.items[SOURCE].update(version="2", modifiedTime="2026-10-09T12:00:00Z")
        self.assertEqual(self.archive()["status"], "verified")
        self.assertEqual(self.drive.copied, [SOURCE, SOURCE])
        self.assertEqual(archive.archive_listing("read", self.config, "biology")["counts"]["work"], 2)

    def test_absent_export_upload_is_not_repeated(self):
        args, _ = self.export_fixture()
        self.drive.fail_create = True
        self.assertEqual(self.export(args)["status"], "held")
        self.drive.fail_create = False
        self.assertEqual(self.export(args)["status"], "held")
        self.assertEqual(sum(i["mimeType"] == google.PPTX for i in self.drive.created), 1)
        self.assertEqual(self.drive.copied, [SOURCE])

    def test_export_requires_pinned_source_content_revision_and_private_student(self):
        args, _ = self.export_fixture()
        self.drive.items[SOURCE]["version"] = "2"
        self.assertEqual(self.export(args)["status"], "held")
        self.assertFalse(any(i["mimeType"] == google.PPTX for i in self.drive.created))
        self.drive.items[SOURCE]["version"] = "1"
        student = self.port.rows[args["student_key"]].value
        self.drive.items[student["drive_file_id"]]["permissions"] = [{"type": "anyone", "role": "reader"}]
        self.assertEqual(self.export(args)["status"], "held")
        self.assertFalse(any(i["mimeType"] == google.PPTX for i in self.drive.created))

    def test_export_confirms_legacy_canonical_proof_without_changing_native_fence(self):
        args, raw = self.export_fixture()
        blocked = self.port.rows[args["blocked_copy_key"]]
        value = {**blocked.value, "content_proof": archive._native_parts_proof(raw, "ppt/")}
        self.port.commit({args["blocked_copy_key"]: (value, blocked.version)})
        before = deepcopy(self.port.rows[args["blocked_copy_key"]])
        result = self.export(args)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(self.port.rows[args["blocked_copy_key"]], before)
        exported = self.port.rows[result["record_key"]].value
        self.assertEqual(exported["source_content_proof"]["basis"], "native_presentation/v1")
        self.assertEqual(before.value["content_proof"]["basis"], "native_document_content/v1")

    def test_export_recovers_uploaded_reserved_id_before_any_fresh_export(self):
        args, raw = self.export_fixture()
        failed_ids = set()
        def created_then_read_fails(*a):
            result = self.drive.create(*a)
            failed_ids.add(result["id"])
            return result
        def metadata(token, ident, fields=None):
            if ident in failed_ids:
                failed_ids.remove(ident)
                raise google.GoogleError("Temporary read failure", 503)
            return self.drive.metadata(token, ident, fields)
        with patch.object(google, "archive_create", side_effect=created_then_read_fails), \
                patch.object(google, "drive_file", side_effect=metadata):
            self.assertEqual(self.export(args)["status"], "held")
        def changed_export(token, meta, heartbeat=None):
            return b"Different bytes from a later Google export" if meta["id"] == SOURCE else self.drive.contents[meta["id"]]
        self.bytes_mock.side_effect = changed_export
        result = self.export(args)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(self.drive.contents[result["drive_file_id"]], raw)
        self.assertEqual(sum(a.args[1]["id"] == SOURCE for a in self.bytes_mock.call_args_list), 1)
        self.assertEqual(sum(i["mimeType"] == google.PPTX for i in self.drive.created), 1)
        self.assertEqual(self.drive.copied, [SOURCE])

    def test_export_rejects_another_input_revision_even_with_the_same_native_content(self):
        args, _ = self.export_fixture()
        self.drive.items[SOURCE]["createdTime"] = "2026-10-07T12:00:00Z"
        args["expected_revision"] = archive._source_revision(self.drive.items[SOURCE])
        self.assertEqual(self.export(args)["status"], "held")
        self.assertFalse(any(i["mimeType"] == google.PPTX for i in self.drive.created))

    def test_source_edit_during_export_prevents_upload(self):
        args, raw = self.export_fixture()
        def changed(*a):
            self.drive.items[SOURCE]["version"] = "2"
            return raw
        self.bytes_mock.side_effect = changed
        self.assertEqual(self.export(args)["status"], "held")
        self.assertFalse(any(i["mimeType"] == google.PPTX for i in self.drive.created))

    def test_source_edit_after_upload_prevents_attachment_and_native_fence_changes(self):
        args, _ = self.export_fixture()
        blocked = deepcopy(self.port.rows[args["blocked_copy_key"]])
        def upload_then_edit(*a):
            result = self.drive.create(*a)
            self.drive.items[SOURCE]["version"] = "2"
            return result
        with patch.object(google, "archive_create", side_effect=upload_then_edit):
            self.assertEqual(self.export(args)["status"], "held")
        student = self.port.rows[args["student_key"]].value
        self.assertFalse(any(i.startswith("archiveworkexport_") for i in student["items"]))
        self.assertEqual(self.port.rows[args["blocked_copy_key"]], blocked)

    def test_later_tagged_native_result_blocks_export_without_retrying_copy(self):
        args, _ = self.export_fixture()
        blocked = self.port.rows[args["blocked_copy_key"]].value
        result = self.drive.add("FictionalLateNativeCopy001", "A late native result", blocked["mime"], [blocked["parent"]])
        result["properties"][archive.TAG] = args["blocked_copy_key"]
        self.assertEqual(self.export(args)["status"], "held")
        self.assertFalse(any(i["mimeType"] == google.PPTX for i in self.drive.created))
        self.assertEqual(self.drive.copied, [SOURCE])

    def test_export_upload_uses_reserved_id_and_actual_pptx_multipart_type(self):
        response = Mock(status_code=200)
        response.json.return_value = {"id": "FictionalReservedExport001"}
        session = Mock(); session.post.return_value = response
        metadata = {"id": "FictionalReservedExport001", "name": "Exported work snapshot.pptx", "mimeType": google.PPTX,
                    "parents": ["FictionalPrivateStudent001"]}
        raw = b"Fictional exported PPTX bytes"
        with patch.object(google, "_session", return_value=session):
            result = REAL_ARCHIVE_CREATE("fictional-token", metadata, raw)
        self.assertEqual(result["id"], metadata["id"])
        body = session.post.call_args.kwargs["data"]
        self.assertIn(("Content-Type: " + google.PPTX).encode(), body)
        self.assertIn(raw, body)
        self.assertIn(metadata["id"].encode(), body)
        self.assertFalse(session.post.call_args.kwargs["allow_redirects"])
        self.assertEqual(session.post.call_count, 1)

    def test_verified_export_pairs_science_feedback_with_original_form_dates(self):
        args, _ = self.export_fixture()
        self.assertEqual(self.export(args)["status"], "verified")
        student = self.port.rows[args["student_key"]].value
        raw = b"%PDF Fictional source-grounded science writing"
        report = {"schema": "science-writing/v1", "report_type": "science_writing", "class_key": "biology",
            "id": "feedbacksource_" + "c" * 64, "prepared_at": "2026-10-09T12:00:00Z", "pdf_sha256": hashlib.sha256(raw).hexdigest(),
            "student": {"student_key": student["student_key"], "student_folder_id": student["drive_file_id"],
                        "name": student["name"], "section": student["section"]},
            "provenance": {"source_observations": [{"student_key": student["student_key"], "section": student["section"],
                "source_file_id": SOURCE, "source_revision": "1", "source_modified_at": "2026-10-08T12:00:00Z"}]}}
        filed = archive.file_science_writing("read", "write", self.config, report, raw)
        self.assertEqual(filed["status"], "verified")
        self.assertEqual(filed["source_records"][0]["date"], "2026-10-07")
        rows = archive.work_index_rows("read", self.config)["rows"]
        linked = [r for r in rows if "Science writing historical reference; assessment coverage not verified" in r[11]]
        self.assertEqual(len(linked), 1)
        self.assertEqual(len([r for r in rows if "Filing held:" in r[11]]), 1)

    def test_export_can_retire_navigation_only_with_binary_and_native_source_proof(self):
        args, raw = self.export_fixture()
        self.records_layout()
        self.assertEqual(self.export(args)["status"], "verified")
        navigation = next(d.value for d in self.port.rows.values() if d.value.get("mime") == archive.SHORTCUT)
        self.assertTrue(navigation["retired_navigation"])
        exported = self.port.rows[navigation["work_copy_record_key"]].value
        self.assertEqual(exported["source_kind"], "submitted_work_export")
        self.assertEqual(self.drive.contents[exported["drive_file_id"]], raw)
        self.assertEqual(self.archive()["status"], "verified")
        self.assertEqual(self.drive.copied, [SOURCE])

    def test_unconfirmed_absent_copy_is_held_and_never_reposted(self):
        self.drive.fail_copy = True
        self.assertEqual(self.archive()["status"], "held")
        self.drive.fail_copy = False
        self.assertEqual(self.archive()["status"], "held")
        self.assertEqual(self.drive.copied, [SOURCE])
        self.assertFalse(any(d.value.get("source_kind") == "submitted_work_copy" and d.value["status"] == "verified"
                             for d in self.port.rows.values()))

    def test_native_version_only_finalization_is_allowed_but_content_edit_is_held(self):
        self.drive.items[SOURCE]["mimeType"] = "application/vnd.google-apps.presentation"
        proof = {"sha256": "a" * 64, "basis": "native_document_parts", "parts": 3}
        with patch.object(archive, "_work_content", return_value=proof):
            self.assertEqual(self.archive()["status"], "verified")
        copied = next(d.value for d in self.port.rows.values() if d.value.get("source_kind") == "submitted_work_copy")
        meta = self.drive.items[copied["drive_file_id"]]
        meta["version"] = "2"
        self.assertEqual(archive.archive_listing("read", self.config, "biology")["status"], "verified")
        meta["modifiedTime"] = "2026-10-09T12:00:01Z"
        self.assertEqual(archive.archive_listing("read", self.config, "biology")["status"], "held")

    def test_source_change_after_copy_prevents_attachment_without_changing_original_parents(self):
        original_copy = self.drive.copy
        def copy_then_edit(*args, **kwargs):
            result = original_copy(*args, **kwargs)
            self.drive.items[SOURCE]["version"] = "2"
            return result
        with patch.object(google, "get_json", side_effect=copy_then_edit):
            self.assertEqual(self.archive()["status"], "held")
        student = next(d.value for d in self.port.rows.values() if d.value.get("items"))
        self.assertTrue(all(not k.startswith("archiveworkcopy_") for k in student["items"]))
        self.assertEqual(self.drive.items[SOURCE]["parents"], ["OriginalFormUploadFolder"])

    def test_native_proof_resolves_images_per_relationship_and_retains_text_style_order(self):
        def package(image_name, color, text="Fictional student answer", font="Arial", href="https://school.example/source"):
            output = BytesIO(); pixel = BytesIO()
            Image.new("RGB", (2, 2), color).save(pixel, format="PNG")
            with ZipFile(output, "w") as z:
                z.writestr("ppt/slides/slide1.xml", '<s xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><t font="%s">%s</t><image r:embed="rId1"/></s>' % (font, text))
                z.writestr("ppt/slides/_rels/slide1.xml.rels", '<Relationships><Relationship Id="rId1" Target="../media/%s" Type="image"/><Relationship Id="rId2" Target="%s" TargetMode="External" Type="hyperlink"/></Relationships>' % (image_name, href))
                z.writestr("ppt/media/" + image_name, pixel.getvalue())
            return output.getvalue()
        first = archive._native_parts_proof(package("image1.png", "blue"), "ppt/")
        self.assertEqual(first, archive._native_parts_proof(package("image7.png", "blue"), "ppt/"))
        for options in ({"color": "red"}, {"text": "A changed answer"}, {"font": "Calibri"}, {"href": "https://different.example/source"}):
            args = {"image_name": "image7.png", "color": "blue", **options}
            self.assertNotEqual(first["sha256"], archive._native_parts_proof(package(**args), "ppt/")["sha256"])

    def test_native_api_proof_binds_each_image_position_styles_notes_and_page_order(self):
        source = {"pageSize": {"width": 100}, "slides": [{"objectId": "SlideA", "pageElements": [
            {"objectId": "PictureA", "image": {"contentUrl": "https://lh.googleusercontent.com/source-a"}},
            {"objectId": "PictureB", "image": {"contentUrl": "https://lh.googleusercontent.com/source-b"}},
            {"objectId": "Answer", "shape": {"text": "Fictional answer", "font": "Arial"}}], "notes": "Fictional notes"},
            {"objectId": "SlideB", "pageElements": []}]}
        proofs = {"https://lh.googleusercontent.com/source-a": {"sha256": "a" * 64, "size": [10, 10]},
                  "https://lh.googleusercontent.com/source-b": {"sha256": "b" * 64, "size": [10, 10]},
                  "https://lh.googleusercontent.com/copy-a": {"sha256": "a" * 64, "size": [10, 10]}}
        original = archive._presentation_content_proof(source, proofs)
        copied = deepcopy(source); copied["slides"][0]["pageElements"][0]["image"]["contentUrl"] = "https://lh.googleusercontent.com/copy-a"
        self.assertEqual(original, archive._presentation_content_proof(copied, proofs))
        swapped = deepcopy(source)
        first, second = swapped["slides"][0]["pageElements"][:2]
        first["image"]["contentUrl"], second["image"]["contentUrl"] = second["image"]["contentUrl"], first["image"]["contentUrl"]
        self.assertNotEqual(original["sha256"], archive._presentation_content_proof(swapped, proofs)["sha256"])
        for mutate in (lambda d: d["slides"].reverse(), lambda d: d["slides"][0].update(notes="Different notes"),
                       lambda d: d["slides"][0]["pageElements"][2]["shape"].update(font="Calibri")):
            changed = deepcopy(source); mutate(changed)
            self.assertNotEqual(original["sha256"], archive._presentation_content_proof(changed, proofs)["sha256"])
        with self.assertRaises(archive.Held):
            archive._native_image_proof("https://untrusted.example/student-data")

    def image_session(self, *responses):
        session, calls = Mock(), []
        queue = list(responses)
        def get(url, **kwargs):
            calls.append((url, dict(kwargs.get("headers") or {}), kwargs.get("allow_redirects")))
            status, body, location = queue.pop(0)
            response = Mock(status_code=status, headers={"Location": location} if location else {})
            response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
            response.iter_content = Mock(return_value=[body])
            return response
        session.get = Mock(side_effect=get)
        return session, calls

    def png(self):
        raw = BytesIO(); Image.new("RGB", (2, 2), (10, 20, 30)).save(raw, "PNG"); return raw.getvalue()

    def test_docs_image_location_uses_token_only_on_exact_docs_host(self):
        url = DOCS_IMAGE
        session, calls = self.image_session((200, self.png(), None))
        with patch.object(google, "_session", return_value=session):
            proof = archive._native_image_proof(url, "fictional-token")
        self.assertEqual(proof["basis"], "image_pixels")
        self.assertEqual(calls, [(url, {"Authorization": "Bearer fictional-token"}, False)])
        for bad in ("https://docs.google.com.fictional.example/image", "https://fictional.docs.google.com/image",
                    "http://docs.google.com/image", "https://docs.google.com:8443/image",
                    "https://user@docs.google.com/image", "https://fictional.example/image"):
            session, calls = self.image_session((200, self.png(), None))
            with patch.object(google, "_session", return_value=session), self.assertRaises(archive.Held):
                archive._native_image_proof(bad, "fictional-token")
            self.assertEqual(calls, [])
        session, calls = self.image_session((200, self.png(), None))
        with patch.object(google, "_session", return_value=session), self.assertRaises(archive.Held):
            archive._native_image_proof(url)
        self.assertEqual(calls, [])

    def test_docs_image_path_outside_slides_images_route_is_held_before_any_request(self):
        for bad in ("https://docs.google.com/fictional-presentation-image",
                    "https://docs.google.com/slides-images-rt/",
                    "https://docs.google.com/slides-images-rt",
                    "https://docs.google.com/slides-images-rtx/FictionalOpaque",
                    "https://docs.google.com/other/slides-images-rt/FictionalOpaque",
                    "https://docs.google.com/slides-images-rt/FictionalOpaque/extra",
                    "https://docs.google.com/slides-images-rt/FictionalOpaque/",
                    "https://docs.google.com/slides-images-rt/../spreadsheets/d/Fictional",
                    "https://docs.google.com/slides-images-rt/..",
                    "https://docs.google.com/slides-images-rt/.",
                    "https://docs.google.com/slides-images-rt/%2e%2e",
                    "https://docs.google.com/slides-images-rt/Fictional%2Fextra",
                    "https://docs.google.com/slides-images-rt/Fictional\\extra",
                    "https://docs.google.com//slides-images-rt/FictionalOpaque",
                    "https://docs.google.com/slides-images-rt/FictionalOpaque#fragment"):
            session, calls = self.image_session((200, self.png(), None))
            with patch.object(google, "_session", return_value=session), self.assertRaises(archive.Held):
                archive._native_image_proof(bad, "fictional-token")
            self.assertEqual(calls, [], bad)

    def test_docs_image_follows_one_tokenless_redirect_to_content_host_only(self):
        url = DOCS_IMAGE
        target = "https://lh7.googleusercontent.com/fictional-image"
        session, calls = self.image_session((302, b"", target), (200, self.png(), None))
        with patch.object(google, "_session", return_value=session):
            self.assertEqual(archive._native_image_proof(url, "fictional-token")["basis"], "image_pixels")
        self.assertEqual(calls[1], (target, {}, False))
        self.assertNotIn("fictional-token", json.dumps(calls[1:]))
        for location in ("https://fictional.example/image", "http://lh7.googleusercontent.com/image",
                         "https://lh7.googleusercontent.com:8443/image", "https://docs.google.com/again"):
            session, calls = self.image_session((302, b"", location), (200, self.png(), None))
            with patch.object(google, "_session", return_value=session), self.assertRaises(archive.Held):
                archive._native_image_proof(url, "fictional-token")
            self.assertEqual(len(calls), 1)
        session, calls = self.image_session((302, b"", target), (302, b"", "https://lh8.googleusercontent.com/x"))
        with patch.object(google, "_session", return_value=session), self.assertRaises(archive.Held):
            archive._native_image_proof(url, "fictional-token")
        self.assertEqual(len(calls), 2)
        session, calls = self.image_session((200, b"x" * 15_000_001, None))
        with patch.object(google, "_session", return_value=session), self.assertRaises(archive.Held):
            archive._native_image_proof(url, "fictional-token")

    def test_direct_content_image_never_receives_the_token(self):
        url = "https://lh7.googleusercontent.com/fictional-image"
        session, calls = self.image_session((200, self.png(), None))
        with patch.object(google, "_session", return_value=session):
            archive._native_image_proof(url, "fictional-token")
        self.assertEqual(calls, [(url, {}, False)])
        session, calls = self.image_session((302, b"", "https://lh8.googleusercontent.com/x"))
        with patch.object(google, "_session", return_value=session), self.assertRaises(archive.Held):
            archive._native_image_proof(url, "fictional-token")
        self.assertEqual(len(calls), 1)

    def test_native_presentation_proof_passes_its_read_token_to_each_image(self):
        url = DOCS_IMAGE
        native = {"slides": [{"objectId": "SlideA", "pageElements": [{"image": {"contentUrl": url}}]}]}
        seen = []
        def proof(location, token=None):
            seen.append((location, token)); return {"sha256": "a" * 64}
        with patch.object(google, "get_json", return_value=native), patch.object(archive, "_native_image_proof", side_effect=proof):
            self.assertEqual(archive._native_presentation_proof("fictional-token", "FictionalDeck0001")["image_instances"], 1)
        self.assertEqual(seen, [(url, "fictional-token")])

    def test_index_pairs_exact_source_and_form_but_keeps_unmatched_historical_feedback(self):
        self.assertEqual(self.archive()["status"], "verified")
        archive.file_feedback("read", "write", self.config, saved(), b"%PDF fictional")
        first = archive.work_index_rows("read", self.config)
        self.assertEqual(len(first["rows"]), 1)
        self.assertIn("same original submission", first["rows"][0][11])
        self.assertIn(SOURCE, first["rows"][0][8])
        report_key = next(k for k, doc in self.port.rows.items() if doc.value.get("kind") == "feedback")
        old = self.port.rows[report_key]
        self.port.commit({report_key: ({**old.value, "assignment_form_id": "UnrelatedForm00000001"}, old.version)})
        second = archive.work_index_rows("read", self.config)
        self.assertEqual(len(second["rows"]), 2)
        feedback_row = next(r for r in second["rows"] if r[9])
        self.assertEqual(feedback_row[7:9], ["", ""])
        self.assertIn("work not located", feedback_row[11])
        self.assertEqual(second["summary"]["biology"]["feedback_files"], 1)

    def test_index_refresh_preserves_keyed_teacher_notes_and_unchanged_refresh_is_read_only(self):
        ident = "FictionalWorkIndexSheet001"
        self.config[archive.WORK_INDEX_CONFIG] = "https://docs.google.com/spreadsheets/d/%s/edit" % ident
        sheet_file = self.drive.add(ident, "Student work and feedback", "application/vnd.google-apps.spreadsheet", [ROOT])
        sheet_file["properties"]["hubStudentWorkIndex"] = ROOT
        headers = {"values": [archive._index_cell(v) for v in archive.INDEX_HEADERS]}
        row_a = ["Biology", "A4 Biology", "Rivera, Ana", "Energy Flow", "Wk 04", 46302, "", "", "", "", "", "Work saved", "", "FictionalStableRowA"]
        row_b = ["Biology", "A4 Biology", "Jones, Lee", "Energy Flow", "Wk 04", 46303, "", "", "", "", "", "Work saved", "", "FictionalStableRowB"]
        initial_a = {"values": [archive._index_cell(v) for v in row_a]}
        initial_a["values"][12] = archive._index_cell("Teacher's note stays with Ana")
        state = {"rows": [headers, initial_a], "writes": 0}
        def sheet_api(url, token, params=None, timeout=20, method="GET", body=None):
            if method == "POST":
                state["writes"] += 1
                for request in body["requests"]:
                    if "updateCells" not in request:
                        continue
                    update = request["updateCells"]
                    start, end = update["range"]["startRowIndex"], update["range"]["endRowIndex"]
                    state["rows"] += [{"values": []} for _ in range(max(0, end - len(state["rows"])))]
                    state["rows"][start:end] = deepcopy(update["rows"])
                return {}
            return {"spreadsheetId": ident, "sheets": [{"properties": {"title": "Records", "sheetId": 17,
                "gridProperties": {"rowCount": 200, "columnCount": 14}}, "data": [{"rowData": deepcopy(state["rows"])}]}]}
        data = {"rows": [row_b, row_a], "summary": {}, "holds": [], "checked_at": "2026-10-09T12:00:00Z", "coverage": "recorded_scans_only"}
        with patch.object(google, "get_json", side_effect=sheet_api), patch.object(archive, "work_index_rows", return_value=data):
            first = archive.refresh_work_index("read", "write", self.config)
            self.assertEqual(first["status"], "verified")
            self.assertEqual(state["rows"][2]["values"][12]["userEnteredValue"]["stringValue"], "Teacher's note stays with Ana")
            second = archive.refresh_work_index("read", "write", self.config)
            self.assertFalse(second["changed"])
            self.assertEqual(state["writes"], 1)
            data["rows"] = [row_b]
            held = archive.refresh_work_index("read", "write", self.config)
            self.assertEqual(held["status"], "held")
            self.assertIn("teacher note", held["problem"])
            self.assertEqual(state["writes"], 1)

    def test_index_does_not_write_unconfigured_or_nonprivate_sheet(self):
        self.assertEqual(archive.refresh_work_index("read", "write", self.config)["status"], "unconfigured")
        ident = "FictionalWorkIndexSheet001"
        self.config[archive.WORK_INDEX_CONFIG] = "https://docs.google.com/spreadsheets/d/%s/edit" % ident
        meta = self.drive.add(ident, "Student work", "application/vnd.google-apps.spreadsheet", [ROOT])
        meta["properties"]["hubStudentWorkIndex"] = ROOT
        meta["permissions"].append({"type": "anyone", "role": "reader"})
        with patch.object(google, "get_json") as api:
            self.assertEqual(archive.refresh_work_index("read", "write", self.config)["status"], "held")
        api.assert_not_called()

    def test_science_writing_files_in_existing_folder_with_original_source_dates_and_one_id(self):
        self.assertEqual(self.archive()["status"], "verified")
        student = next(d.value for d in self.port.rows.values() if d.value.get("student_key") and d.value.get("items"))
        raw = b"%PDF Fictional source-grounded science writing feedback"
        report = {"schema": "science-writing/v1", "report_type": "science_writing", "class_key": "biology",
            "id": "feedbacksource_" + "c" * 64, "prepared_at": "2026-10-09T12:00:00Z", "pdf_sha256": hashlib.sha256(raw).hexdigest(),
            "student": {"student_key": student["student_key"], "student_folder_id": student["drive_file_id"],
                        "name": student["name"], "section": student["section"]},
            "provenance": {"source_observations": [{"student_key": student["student_key"], "section": student["section"],
                "source_file_id": SOURCE, "source_revision": "1", "source_modified_at": "2026-10-08T12:00:00Z"}]}}
        first = archive.file_science_writing("read", "write", self.config, report, raw)
        self.assertEqual(first["status"], "verified")
        self.assertEqual(first["student_folder_id"], student["drive_file_id"])
        self.assertEqual(first["report_type"], "science_writing")
        self.assertEqual(first["date"], "2026-10-09")
        self.assertEqual(first["source_records"][0]["date"], "2026-10-07")
        self.assertEqual(first["source_records"][0]["assignment_form_id"], "FictionalForm000000001")
        second = archive.file_science_writing("read", "write", self.config, report, raw)
        self.assertEqual(first["drive_file_id"], second["drive_file_id"])
        rows = archive.work_index_rows("read", self.config)["rows"]
        self.assertEqual(len(rows), 1)
        self.assertIn("Science writing historical reference; assessment coverage not verified", rows[0][11])
        self.assertIn(SOURCE, rows[0][8])
        self.drive.items[SOURCE]["version"] = "2"
        held = archive.file_science_writing("read", "write", self.config, report, raw)
        self.assertEqual(held["status"], "held")
        self.assertIn("work changed", held["problem"])

    def presentation_fixture(self):
        self.assertEqual(self.archive()["status"], "verified")
        student = next(d.value for d in self.port.rows.values() if d.value.get("student_key") and d.value.get("items"))
        raw = b"%PDF Fictional original presentation"
        report = {"schema": "science-writing/v1", "report_type": "science_writing", "class_key": "biology",
            "id": "feedbacksource_" + "c" * 64, "prepared_at": "2026-10-09T12:00:00Z", "pdf_sha256": hashlib.sha256(raw).hexdigest(),
            "student": {"student_key": student["student_key"], "student_folder_id": student["drive_file_id"],
                        "name": student["name"], "section": student["section"]},
            "evidence": [{"quote": "Fictional unchanged evidence", "dimension": "evidence"}],
            "target": "Use one observation to support the claim.",
            "provenance": {"source_observations": [{"student_key": student["student_key"], "section": student["section"],
                "source_file_id": SOURCE, "source_revision": "1", "source_modified_at": "2026-10-08T12:00:00Z"}]}}
        filed = archive.file_science_writing("read", "write", self.config, report, raw)
        self.assertEqual(filed["status"], "verified")
        new_raw = b"%PDF Fictional larger presentation; same assessment"
        replacement = {**deepcopy(report), "id": "feedbacksource_" + "d" * 64, "presentation_version": 2,
            "supersedes_report_id": report["id"], "supersession_previous_payload": deepcopy(report),
            "prepared_at": "2026-10-09T15:00:00Z", "pdf_sha256": hashlib.sha256(new_raw).hexdigest(),
            "presentation_context": {"original_submission_times": [{"source_file_id": SOURCE,
                "source_revision": "1", "submitted_at": "2026-10-07T13:00:00Z"}]}}
        return report, raw, replacement, new_raw, filed

    def test_presentation_replacement_is_immutable_idempotent_and_keeps_history_row_key(self):
        original, raw, replacement, new_raw, first = self.presentation_fixture()
        before_source = deepcopy(self.drive.items[SOURCE]); before_pdf = deepcopy(self.drive.items[first["drive_file_id"]])
        old_row = archive.work_index_rows("read", self.config)["rows"][0]
        new = archive.file_science_writing("read", "write", self.config, replacement, new_raw)
        self.assertEqual(new["status"], "verified")
        self.assertNotEqual(new["drive_file_id"], first["drive_file_id"])
        self.assertEqual(self.drive.items[SOURCE], before_source)
        self.assertEqual(self.drive.items[first["drive_file_id"]], before_pdf)
        self.assertEqual(self.drive.contents[first["drive_file_id"]], raw)
        self.assertEqual([f["feedback_id"] for f in archive.list_feedback("read", self.config)], [replacement["id"]])
        rows = archive.work_index_rows("read", self.config)
        historical = next(r for r in rows["rows"] if "Superseded feedback" in r[11])
        self.assertEqual(historical[13], old_row[13])
        self.assertIn(first["drive_file_id"], historical[9])
        self.assertEqual(rows["summary"]["biology"]["historical_feedback_files"], 1)
        repeated = archive.file_science_writing("read", "write", self.config, replacement, new_raw)
        self.assertEqual(repeated["drive_file_id"], new["drive_file_id"])
        replay = archive.file_science_writing("read", "write", self.config, original, raw)
        self.assertEqual(replay["drive_file_id"], first["drive_file_id"])
        self.assertTrue(replay["supersession_verified"])
        self.assertEqual(len([f for f in self.drive.created if f["mimeType"] == archive.PDF]), 2)

    def test_presentation_rejects_changed_assessment_proof_identity_and_unverified_event_order(self):
        original, raw, replacement, new_raw, first = self.presentation_fixture()
        for mutate in (lambda r: r.update(target="A different assessment target."),
                       lambda r: r["evidence"][0].update(quote="Invented evidence"),
                       lambda r: r["student"].update(student_key="AnotherStudent00001"),
                       lambda r: r["supersession_previous_payload"].update(target="Altered prior target"),
                       lambda r: r["presentation_context"]["original_submission_times"][0].update(submitted_at="2026-10-07T12:00:00Z"),
                       lambda r: r["presentation_context"].update(guessed_order=True)):
            with self.subTest(mutate=mutate):
                bad = deepcopy(replacement); mutate(bad)
                result = archive.file_science_writing("read", "write", self.config, bad, new_raw)
                self.assertEqual(result["status"], "held")
                prior = self.port.rows[archive.key("archivefeedback", [ROOT, original["id"]])].value
                self.assertFalse(prior.get("superseded_by"))
        self.assertEqual(len([f for f in self.drive.created if f["mimeType"] == archive.PDF]), 1)

    def test_uncertain_new_pdf_does_not_supersede_old_or_repeat_a_create(self):
        original, raw, replacement, new_raw, first = self.presentation_fixture()
        self.drive.fail_create = True
        self.assertEqual(archive.file_science_writing("read", "write", self.config, replacement, new_raw)["status"], "held")
        self.drive.fail_create = False
        self.assertEqual(archive.file_science_writing("read", "write", self.config, replacement, new_raw)["status"], "held")
        prior = self.port.rows[archive.key("archivefeedback", [ROOT, original["id"]])].value
        self.assertFalse(prior.get("superseded_by"))
        self.assertEqual([f["feedback_id"] for f in archive.list_feedback("read", self.config)], [original["id"]])

    def test_unconfirmed_successor_pointer_keeps_prior_current_and_recovers_the_same_pdf(self):
        original, raw, replacement, new_raw, first = self.presentation_fixture()
        actual_put = archive._Run.put
        def unavailable_pointer(run, ident, value, old=None):
            if value.get("superseded_by"):
                raise StoreUnavailable("Fictional prior-pointer write was not confirmed")
            return actual_put(run, ident, value, old)
        with patch.object(archive._Run, "put", new=unavailable_pointer):
            held = archive.file_science_writing("read", "write", self.config, replacement, new_raw)
        self.assertEqual(held["status"], "held")
        self.assertEqual([f["feedback_id"] for f in archive.list_feedback("read", self.config)], [original["id"]])
        self.assertEqual(len(archive.work_index_rows("read", self.config)["rows"]), 1)
        created = [f["id"] for f in self.drive.created if f["mimeType"] == archive.PDF]
        self.assertEqual(len(created), 2)
        recovered = archive.file_science_writing("read", "write", self.config, replacement, new_raw)
        self.assertEqual(recovered["status"], "verified")
        self.assertEqual(recovered["drive_file_id"], created[-1])
        self.assertEqual([f["id"] for f in self.drive.created if f["mimeType"] == archive.PDF], created)
        self.assertEqual([f["feedback_id"] for f in archive.list_feedback("read", self.config)], [replacement["id"]])
        self.assertEqual(self.drive.contents[first["drive_file_id"]], raw)

    def test_broken_forked_or_cross_student_successor_chains_are_held(self):
        original, raw, replacement, new_raw, first = self.presentation_fixture()
        self.assertEqual(archive.file_science_writing("read", "write", self.config, replacement, new_raw)["status"], "verified")
        prior_key = archive.key("archivefeedback", [ROOT, original["id"]])
        successor_key = archive.key("archivefeedback", [ROOT, replacement["id"]])
        good_prior = deepcopy(self.port.rows[prior_key].value)
        good_successor = deepcopy(self.port.rows[successor_key].value)
        for mutate in (lambda p, s: p.update(supersession="Malformed proof"),
                       lambda p, s: p.update(superseded_by="feedbacksource_" + "e" * 64),
                       lambda p, s: s.update(student_key="AnotherStudent00001"),
                       lambda p, s: s.update(source_records=[]),
                       lambda p, s: s.update(supersedes_report_id="feedbacksource_" + "e" * 64),
                       lambda p, s: s.update(supersession_prior_payload_sha256="e" * 64)):
            with self.subTest(mutate=mutate):
                self.port.rows[prior_key].value.clear(); self.port.rows[prior_key].value.update(deepcopy(good_prior))
                self.port.rows[successor_key].value.clear(); self.port.rows[successor_key].value.update(deepcopy(good_successor))
                mutate(self.port.rows[prior_key].value, self.port.rows[successor_key].value)
                self.assertEqual(archive.verify_feedback_successor("read", self.config, "biology", original["id"])["status"], "held")
                with self.assertRaises(archive.Held): archive.list_feedback("read", self.config)
        self.assertEqual(self.drive.contents[first["drive_file_id"]], raw)

    def test_contract_metadata_is_an_optional_exact_passthrough(self):
        original, raw, replacement, new_raw, first = self.presentation_fixture()
        student = self.port.rows[first["student_key"]].value
        artifact = deepcopy(self.port.rows[archive.key("archivefeedback", [ROOT, original["id"]])].value)
        extra = {"contract_keys": ["FictionalContractKey"], "contract_status": "missing_verified",
            "contract_checked_at": "2026-10-09T15:00:00Z", "original_due_date": "2026-10-01",
            "source_proof": {"schema": "fictional-contract-source/v1", "checked_at": "2026-10-09T15:00:00Z"}}
        artifact.update(extra)
        value = archive._feedback_metadata(artifact, student, self.drive.items[COURSES["biology"]])
        self.assertEqual({k: value[k] for k in extra}, extra)
        self.assertEqual(value["report_type"], "science_writing")

    def test_unattached_prior_is_not_an_authorized_presentation_replacement(self):
        original, raw, replacement, new_raw, first = self.presentation_fixture()
        prior_key = archive.key("archivefeedback", [ROOT, original["id"]])
        owner = self.port.rows[first["student_key"]].value
        del owner["items"][prior_key]
        self.assertEqual(archive.file_science_writing("read", "write", self.config, replacement, new_raw)["status"], "held")
        self.assertEqual(len([f for f in self.drive.created if f["mimeType"] == archive.PDF]), 1)
        owner["items"][prior_key] = {"kind": "feedback", "date": first["date"]}
        self.assertEqual(archive.file_science_writing("read", "write", self.config, replacement, new_raw)["status"], "verified")
        self.port.rows[first["student_key"]].value["items"].pop(prior_key, None)
        self.assertEqual(archive.verify_feedback_successor("read", self.config, "biology", original["id"])["status"], "held")

    def test_verified_history_parent_is_required_and_broken_successor_never_silently_hides_prior(self):
        original, raw, replacement, new_raw, first = self.presentation_fixture()
        self.assertEqual(archive.file_science_writing("read", "write", self.config, replacement, new_raw)["status"], "verified")
        run = archive._Run("read", "write", self.config, "biology")
        history = run.feedback_history_folder()
        prior_key = archive.key("archivefeedback", [ROOT, original["id"]]); prior = self.port.rows[prior_key]
        self.drive.move("write", first["drive_file_id"], history["drive_file_id"], [prior.value["parent"]])
        moved = {**prior.value, "parent": history["drive_file_id"], "history_original_parent": first["student_folder_id"],
                 "history_folder_key": history["record_key"]}
        run.put(prior_key, moved, prior); run.finish({"status": "verified"})
        proof = archive.verify_feedback_successor("read", self.config, "biology", original["id"])
        self.assertEqual(proof["status"], "verified")
        self.assertEqual(proof["current_report_id"], replacement["id"])
        self.assertEqual(len(archive.list_feedback("read", self.config)), 1)
        self.assertEqual(len(archive.work_index_rows("read", self.config)["rows"]), 2)
        before = deepcopy(self.port.rows)
        listing = archive.archive_listing("read", self.config, "biology")
        self.assertEqual(self.port.rows, before)
        reports = [record for record in listing["students"][0]["records"] if record["kind"] == "feedback"]
        self.assertEqual(len(reports), 2)
        self.assertEqual(listing["counts"]["feedback"], 2)
        previous = next(record for record in reports if record["historical"])
        self.assertEqual((previous["feedback_id"], previous["report_type"], previous["feedback_date"]),
                         (original["id"], "science_writing", "2026-10-09"))
        self.assertEqual(previous["url"], archive._url(first["drive_file_id"]))
        self.drive.items[history["drive_file_id"]]["properties"][archive.TAG] = "WrongFolder"
        held_listing = archive.archive_listing("read", self.config, "biology")
        self.assertEqual(held_listing["status"], "held")
        self.assertEqual(held_listing["students"], [])
        self.assertEqual(archive.verify_feedback_successor("read", self.config, "biology", original["id"])["status"], "held")
        self.drive.items[history["drive_file_id"]]["properties"][archive.TAG] = history["record_key"]
        successor_key = archive.key("archivefeedback", [ROOT, replacement["id"]])
        successor = self.port.rows[successor_key]
        self.drive.items[successor.value["drive_file_id"]]["md5Checksum"] = "ChangedBytes"
        self.assertEqual(archive.verify_feedback_successor("read", self.config, "biology", original["id"])["status"], "held")
        with self.assertRaises(archive.Held):
            archive.list_feedback("read", self.config)
        self.assertEqual(self.drive.contents[first["drive_file_id"]], raw)

    def test_report_roster_reuses_only_exact_current_names_and_retains_departed_history(self):
        self.assertEqual(self.archive()["status"], "verified")
        with patch.object(archive, "_filing_roster", return_value=name_roster()):
            selected = archive.report_roster("read", self.config, "biology")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["name"], "Rivera, Ana")
        with patch.object(archive, "_filing_roster", return_value=name_roster({"first": "Lee", "last": "Jones", "section": "A4 Biology"})):
            with self.assertRaises(archive.Held):
                archive.report_roster("read", self.config, "biology")
        before = deepcopy(self.drive.items)
        with patch.object(archive, "_filing_roster", return_value=[]):
            self.assertEqual(archive.report_roster("read", self.config, "biology"), [])
        self.assertEqual(self.drive.items, before)
        self.assertEqual(archive.archive_listing("read", self.config, "biology")["counts"]["students"], 1)

    def test_all_existing_long_scan_renews_server_lease_and_records_one_completed_receipt(self):
        sources = []
        for number in range(29):
            ident = "FictionalExistingSource%03d" % number
            self.drive.add(ident, "Energy Flow - Ana Rivera.pptx", google.PPTX, ["OriginalFormUploadFolder"])
            sources.append({"id": ident, "name": "Energy Flow - Ana Rivera.pptx", "mime": google.PPTX})
        data = found(row(files=sources))
        self.assertEqual(self.archive(data)["filed"], 29)
        created, elapsed, commits = len(self.drive.created), [0.0], []
        source_ids = {item["id"] for item in sources}
        original_commit = self.port.commit

        def metadata(token, ident, fields=None):
            if ident in source_ids:
                elapsed[0] += 9
                self.port.advance(9)
            return self.drive.metadata(token, ident, fields)

        def commit(changes):
            commits.append(deepcopy(changes))
            return original_commit(changes)

        with patch.object(archive, "monotonic", side_effect=lambda: elapsed[0]), \
                patch.object(google, "drive_file", side_effect=metadata), \
                patch.object(self.port, "commit", side_effect=commit):
            result = self.archive(data)
        self.assertEqual((result["status"], result["filed"], result["held"], result["problems"]), ("verified", 29, [], []))
        self.assertGreater(elapsed[0], 240)
        self.assertEqual(len(self.drive.created), created)
        self.assertEqual((self.drive.renamed, self.drive.moved), ([], []))
        receipt_id = archive.key("archivescan", [ROOT, "biology", data["week"]])
        self.assertEqual(sum(receipt_id in change for change in commits), 1)
        self.assertEqual(self.port.rows[receipt_id].value["status"], "verified")
        task_id = next(ident for ident, doc in self.port.rows.items() if ident.startswith("task_")
                       and doc.value["scope"]["workflow"] == "student-archive")
        task = self.port.rows[task_id].value
        self.assertIsNone(task["active_run"])
        completed = self.port.rows[task["last_result"]["record_ids"]["archive"]].value["payload"]
        self.assertEqual((completed["status"], completed["filed"]), ("verified", 29))
        renewals = sum(task_id in change and change[task_id][0].get("status") == "Working" for change in commits)
        self.assertGreaterEqual(renewals, 7)
        self.assertLess(renewals, len(sources))  # Heartbeats are throttled, not one per metadata GET.

    def test_lost_ownership_after_slow_source_read_stops_all_later_drive_and_state_writes(self):
        self.assertEqual(self.archive()["status"], "verified")
        receipt_id = archive.key("archivescan", [ROOT, "biology", "Wk 04"])
        prior_receipt = self.port.rows[receipt_id]
        elapsed, lost = [0.0], {}

        def metadata(token, ident, fields=None):
            if ident == SOURCE and not lost:
                elapsed[0] += 121
                self.port.advance(121)
                task_id = next(i for i, doc in self.port.rows.items() if i.startswith("task_"))
                successor = CloudState(self.port).claim(task_id, owner="other-archive-worker",
                    request_id="separate-live-operation", input_hash="different-input")
                lost.update(claim=successor, rows=deepcopy(self.port.rows), items=deepcopy(self.drive.items),
                            created=len(self.drive.created), renamed=len(self.drive.renamed), moved=len(self.drive.moved))
            return self.drive.metadata(token, ident, fields)

        with patch.object(archive, "monotonic", side_effect=lambda: elapsed[0]), \
                patch.object(google, "drive_file", side_effect=metadata):
            result = self.archive()
        self.assertTrue(lost)
        self.assertEqual(result["status"], "held")
        self.assertIn("Another student-records operation", result["problems"][0])
        self.assertEqual(self.port.rows, lost["rows"])
        self.assertEqual(self.drive.items, lost["items"])
        self.assertEqual((len(self.drive.created), len(self.drive.renamed), len(self.drive.moved)),
                         (lost["created"], lost["renamed"], lost["moved"]))
        self.assertEqual(self.port.rows[receipt_id], prior_receipt)
        task = self.port.rows[lost["claim"].task_id].value
        self.assertEqual(task["active_run"], lost["claim"].run_id)

    def test_finish_forces_server_renewal_even_before_throttled_heartbeat_is_due(self):
        elapsed = [0.0]
        with patch.object(archive, "monotonic", side_effect=lambda: elapsed[0]):
            run = archive._Run("read", "write", self.config, "biology")
            first_expiry = run.claim.expires_at
            # Server time is authoritative, independently of the scheduling clock.
            self.port.advance(119); elapsed[0] = 39
            original_read, delayed = self.port.read, []

            def read(ids):
                if not delayed and any(ident.startswith("record_") for ident in ids):
                    delayed.append(True)
                    self.port.advance(2)
                return original_read(ids)

            with patch.object(self.port, "read", side_effect=read):
                run.finish({"status": "verified"})
        self.assertTrue(delayed)
        self.assertGreater(run.claim.expires_at, first_expiry)
        task = self.port.rows[run.claim.task_id].value
        self.assertIsNone(task["active_run"])
        self.assertEqual(self.port.rows[task["last_result"]["record_ids"]["archive"]].value["payload"], {"status": "verified"})

    def test_uncertain_renewal_is_not_retried_and_blocks_later_archive_mutations(self):
        with patch.object(archive, "monotonic", return_value=0.0):
            run = archive._Run("read", "write", self.config, "biology")
            self.port.fail_after_commit = True
            with self.assertRaises(StoreUnavailable):
                run.renew()
            after_failure = deepcopy(self.port.rows)
            sequence = self.port.sequence
            for operation in (lambda: run.put("archive_unexpected", {}),
                              lambda: run.create("archive_unexpected", "Example", COURSES["biology"], google.FOLDER),
                              lambda: run.student({}), lambda: run.attach({}, {}),
                              lambda: run.filing_receipt(found(), {"status": "verified", "filed": 0}),
                              lambda: run.finish({"status": "held"})):
                with self.assertRaises(StoreUnavailable):
                    operation()
        self.assertEqual(self.port.rows, after_failure)
        self.assertEqual(self.port.sequence, sequence)
        self.assertEqual((self.drive.created, self.drive.renamed, self.drive.moved), ([], [], []))

    def test_default_identity_scope_is_the_actual_root(self):
        self.assertEqual(archive._scope_root(self.config, ROOT), ROOT)
        self.assertEqual(self.archive()["status"], "verified")
        self.assertIn(archive.key("archiveclass", [ROOT, "biology"]), self.port.rows)
        self.assertIn("driveId", archive.FIELDS.split(","))

    def test_rebuilt_private_tree_retains_index_keys_file_ids_receipts_and_task(self):
        self.assertEqual(self.archive()["status"], "verified")
        feedback = archive.file_feedback("read", "write", self.config, saved(), b"%PDF immutable")
        self.assertEqual(feedback["status"], "verified")
        before = archive.archive_listing("read", self.config, "biology")
        class_key = archive.key("archiveclass", [ROOT, "biology"])
        folder_ids = [ident for ident, item in self.drive.items.items()
                      if item["mimeType"] == google.FOLDER]
        replacements = {ident: "SharedDriveFolder%05d" % i for i, ident in enumerate(folder_ids)}
        stable_keys = {ident for ident in self.port.rows if ident.startswith(("archive", "task_"))}
        file_ids = {ident for ident, item in self.drive.items.items()
                    if item["mimeType"] != google.FOLDER}
        # A private migration recreates only folders and moves the archive files;
        # original source IDs, archive file IDs and logical CAS identities remain.
        for ident in folder_ids:
            original = self.drive.items.pop(ident)
            self.drive.items[replacements[ident]] = {**original, "id": replacements[ident],
                "parents": [replacements.get(p, p) for p in original["parents"]], "driveId": "PrivateSharedDrive00001"}
        for item in self.drive.items.values():
            item["parents"] = [replacements.get(p, p) for p in item["parents"]]
        changes = {}
        for ident, doc in self.port.rows.items():
            value = deepcopy(doc.value)
            if value.get("mime") == google.FOLDER:
                value["drive_file_id"] = replacements[value["drive_file_id"]]
                value["url"] = archive._url(value["drive_file_id"], True)
            if value.get("parent") in replacements:
                value["parent"] = replacements[value["parent"]]
            if ident == class_key:
                value["students"] = {k: replacements[v] for k, v in value["students"].items()}
            if value != doc.value:
                changes[ident] = (value, doc.version)
        self.port.commit(changes)
        self.config[archive.ROOT_CONFIG] = replacements[ROOT]
        self.config[archive.COURSES_CONFIG] = json.dumps({c: replacements[v] for c, v in COURSES.items()})
        self.config[archive.SCOPE_ROOT_CONFIG] = ROOT
        count = len(self.drive.created)
        after = archive.archive_listing("read", self.config, "biology")
        self.assertEqual(after["status"], "verified")
        self.assertEqual(after["filing"], before["filing"])
        self.assertEqual(after["students"][0]["student_key"], before["students"][0]["student_key"])
        self.assertNotEqual(after["students"][0]["folder_url"], before["students"][0]["folder_url"])
        self.assertEqual(self.archive()["status"], "verified")
        again = archive.file_feedback("read", "write", self.config, saved(), b"%PDF changed rendering")
        self.assertEqual(again["drive_file_id"], feedback["drive_file_id"])
        self.assertEqual(len(self.drive.created), count)
        self.assertEqual({ident for ident in self.port.rows if ident.startswith(("archive", "task_"))}, stable_keys)
        self.assertTrue(file_ids.issubset(self.drive.items))
        self.assertNotIn(archive.key("archiveclass", [replacements[ROOT], "biology"]), self.port.rows)
        self.assertEqual(archive.feedback_location("read", self.config, "biology")[0], replacements[ROOT])

    def test_identity_scope_never_bypasses_actual_parent_or_private_access_checks(self):
        self.config[archive.SCOPE_ROOT_CONFIG] = "StableOldNamespaceRoot001"
        self.drive.items[COURSES["biology"]]["parents"] = [self.config[archive.SCOPE_ROOT_CONFIG]]
        self.assertEqual(self.archive()["status"], "held")
        self.assertEqual(self.drive.created, [])
        self.config[archive.SCOPE_ROOT_CONFIG] = "bad/root"
        self.assertFalse(archive.enabled(self.config))

    def test_public_or_unwritable_private_parent_holds_before_create(self):
        for mutation in (lambda m: m["permissions"].append({"type": "anyone", "role": "reader"}),
                         lambda m: m["capabilities"].update(canAddChildren=False)):
            original = deepcopy(self.drive.items[ROOT])
            mutation(self.drive.items[ROOT])
            self.assertEqual(self.archive()["status"], "held")
            self.assertEqual(self.drive.created, [])
            self.drive.items[ROOT] = original

    def test_archive_inside_turnin_is_held_even_if_private(self):
        self.drive.items[ROOT]["parents"] = [self.config["HUB_SUBMISSIONS_ROOT_FOLDER"]]
        self.drive.add(self.config["HUB_SUBMISSIONS_ROOT_FOLDER"], "Turn In", google.FOLDER)
        self.assertEqual(self.archive()["status"], "held")
        self.assertEqual(self.drive.created, [])

    def test_current_and_earlier_form_work_have_copies_and_original_links_without_moves(self):
        original = deepcopy(self.drive.items[SOURCE])
        before = deepcopy(self.drive.items[EARLIER])
        prior = {"source": "form", "submitted_at": "2026-10-06T13:00:00Z",
                 "email": "s.ana.rivera@school.example", "matched_by": "email",
                 "files": [{"id": EARLIER, "name": "Earlier deck", "mime": google.PPTX}]}
        result = self.archive(found(row(earlier=[prior])))
        self.assertEqual((result["status"], result["filed"]), ("verified", 2))
        shortcuts = [m for m in self.drive.created if m["mimeType"] == archive.SHORTCUT]
        self.assertEqual({m["shortcutDetails"]["targetId"] for m in shortcuts}, {SOURCE, EARLIER})
        self.assertTrue(all(m["name"].endswith(" — Energy Flow — Work") for m in shortcuts))
        self.assertEqual(self.drive.items[SOURCE], original)
        self.assertEqual(self.drive.items[EARLIER], before)
        self.assertEqual(self.drive.moved, [])
        self.assertEqual(self.drive.renamed, [])
        count = len(self.drive.created)
        self.assertEqual(self.archive(found(row(earlier=[prior])))["status"], "verified")
        self.assertEqual(len(self.drive.created), count)
        listing = archive.archive_listing("read", self.config, "biology")
        self.assertEqual(listing["counts"], {"students": 1, "work": 2, "feedback": 0, "held": 0})
        self.assertEqual(listing["students"][0]["latest_record_date"], "2026-10-07")
        self.assertEqual(listing["students"][0]["status"], "unverified")

    def _reader_only_source_metadata(self, token, ident, fields=None):
        metadata = self.drive.metadata(token, ident, fields)
        if ident in {SOURCE, EARLIER}:
            if "permissions" in (fields or "").split(","):
                raise google.GoogleError("The file reader cannot list its access permissions.", 403)
            # Drive hides an unshared source parent from its file-only reader.
            for field in ("permissions", "capabilities", "shortcutDetails", "parents"):
                metadata.pop(field, None)
        else:
            self.assertIn("permissions", (fields or "").split(","))
        return metadata

    def test_reader_only_original_files_preserve_form_dates_and_source_ids(self):
        self.drive.items[SOURCE]["modifiedTime"] = "2026-10-09T03:39:00Z"
        original = deepcopy(self.drive.items[SOURCE])
        with patch.object(google, "drive_file", side_effect=self._reader_only_source_metadata):
            result = self.archive()
        self.assertEqual((result["status"], result["filed"]), ("verified", 1))
        artifact = self.port.rows[archive.key("archivework", [ROOT, "biology", SOURCE])].value
        self.assertEqual(artifact["source_file_id"], SOURCE)
        self.assertEqual(artifact["form_id"], "FictionalForm000000001")
        self.assertEqual(artifact["submitted_at"], row()["submitted_at"])
        self.assertEqual((artifact["date"], artifact["date_basis"]), ("2026-10-07", "form_receipt"))
        self.assertEqual(artifact["source_context"]["parents"], [])
        self.assertEqual(self.drive.items[SOURCE], original)
        self.assertEqual(self.drive.moved, [])
        shortcut = self.drive.items[artifact["drive_file_id"]]
        self.assertEqual(shortcut["shortcutDetails"]["targetId"], SOURCE)

    def test_reader_only_original_supports_unique_name_filing_with_unverified_identity(self):
        original = deepcopy(self.drive.items[SOURCE])
        candidate = row(email="", matched_by="file name", source="folder")
        with patch.object(google, "drive_file", side_effect=self._reader_only_source_metadata), \
                patch.object(archive, "_filing_roster", return_value=name_roster()):
            result = self.archive(found(candidate))
        self.assertEqual((result["status"], result["filed"]), ("verified", 1))
        artifact = self.port.rows[archive.key("archivework", [ROOT, "biology", SOURCE])].value
        self.assertEqual(artifact["identity_status"], "identity_unverified")
        self.assertEqual((artifact["date"], artifact["date_basis"]), ("2026-10-08", "file_created"))
        self.assertEqual(artifact["source_context"]["parents"], [])
        self.assertEqual(self.drive.items[SOURCE], original)

    def test_reader_only_original_never_weakens_private_destination_checks(self):
        self.drive.items[COURSES["biology"]]["permissions"].append({"type": "anyone", "role": "reader"})
        with patch.object(google, "drive_file", side_effect=self._reader_only_source_metadata):
            result = self.archive()
        self.assertEqual(result["status"], "held")
        self.assertEqual(self.drive.created, [])
        self.assertEqual(self.drive.moved, [])

    def test_earlier_form_version_needs_its_own_email_identity(self):
        prior = {"source": "form", "submitted_at": "2026-10-06T13:00:00Z",
                 "files": [{"id": EARLIER, "name": "Earlier deck", "mime": google.PPTX}]}
        result = self.archive(found(row(earlier=[prior])))
        self.assertEqual(result["status"], "held")
        shortcuts = [m for m in self.drive.created if m["mimeType"] == archive.SHORTCUT]
        self.assertEqual([m["shortcutDetails"]["targetId"] for m in shortcuts], [SOURCE])

    def test_repeated_source_file_keeps_latest_label_and_both_submission_observations(self):
        prior = {"source": "form", "submitted_at": "2026-10-06T13:00:00Z",
                 "email": "s.ana.rivera@school.example", "matched_by": "email",
                 "files": [{"id": SOURCE, "name": "Same original deck", "mime": google.PPTX}]}
        result = self.archive(found(row(earlier=[prior])))
        self.assertEqual((result["status"], result["filed"]), ("verified", 1))
        shortcuts = [m for m in self.drive.created if m["mimeType"] == archive.SHORTCUT]
        self.assertEqual(len(shortcuts), 1)
        self.assertTrue(self.drive.items[shortcuts[0]["id"]]["name"].startswith("2026-10-07"))
        observations = [doc for ident, doc in self.port.rows.items() if ident.startswith("archiveobservation_")]
        self.assertEqual(len(observations), 2)
        self.assertEqual(archive.archive_listing("read", self.config, "biology")["students"][0]["latest_record_date"], "2026-10-07")

    def test_unverified_lesson_with_strong_form_retains_provenance_after_later_loose_name_scan(self):
        self.drive.items[SOURCE]["name"] = "s.ana.rivera — 10-09 10.00 — Wk07 Energy Flow.pptx"
        original = deepcopy(self.drive.items[SOURCE])
        strong = found(row(files=[{"id": SOURCE, "mime": google.PPTX, "other_week": 7}]))
        strong["week"] = "Wk 06"
        form = strong["lessons"][0]["forms"][0]
        form.update(form_id="FictionalVerifiedWeek06Form01", label="Week 06 Energy Flow — Turn In")
        first = self.archive(strong)
        self.assertEqual((first["status"], first["filed"]), ("verified", 1))
        ident = archive.key("archivework", [ROOT, "biology", SOURCE])
        before = deepcopy(self.port.rows[ident].value)
        self.assertEqual((before["lesson_status"], before["identity_status"], before["date_basis"]),
                         ("unverified", "verified", "form_receipt"))
        initial_observations = {key: deepcopy(doc) for key, doc in self.port.rows.items()
                                if key.startswith("archiveobservation_")}
        created = len(self.drive.created)
        weak = row(matched=False, email="", matched_by="file name", source="folder",
                   files=[{"id": SOURCE, "mime": google.PPTX, "other_week": 7}])
        loose = {"class_key": "biology", "week": "Wk 05", "lessons": [], "other_files": [weak], "problems": []}
        with patch.object(archive, "_filing_roster", return_value=name_roster()):
            result = self.archive(loose)
        self.assertEqual((result["status"], result["filed"]), ("verified", 1))
        after = self.port.rows[ident].value
        for field in ("name", "form_id", "source_context", "date", "date_basis", "submitted_at", "assignment",
                      "lesson_status", "identity_status", "identity_basis", "original_filename"):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(after["source_context"]["week"], "Wk 06")
        self.assertNotIn("filed by name", after["name"])
        self.assertEqual(self.drive.items[after["drive_file_id"]]["name"], before["name"])
        self.assertEqual(self.drive.items[SOURCE], original)
        self.assertEqual((len(self.drive.created), self.drive.renamed, self.drive.moved), (created, [], []))
        observations = {key: doc for key, doc in self.port.rows.items() if key.startswith("archiveobservation_")}
        self.assertEqual(len(observations), 2)
        for key, doc in initial_observations.items():
            self.assertEqual(observations[key], doc)
        fresh = next(doc.value for key, doc in observations.items() if key not in initial_observations)
        self.assertEqual(fresh["source_context"]["week"], "Wk 05")
        self.assertIsNone(fresh["source_context"]["form_id"])
        self.assertFalse(fresh["source_context"]["accepted_intake"])
        self.assertEqual((fresh["identity_status"], fresh["lesson_status"], fresh["date_basis"]),
                         ("identity_unverified", "unverified", "filename_submission"))
        self.assertEqual(fresh["submitted_at"], "2026-10-09T14:00:00Z")

    def test_current_native_identity_cannot_authorize_an_unverified_earlier_upload(self):
        prior = {"source": "form", "submitted_at": "2026-10-06T13:00:00Z",
                 "files": [{"id": EARLIER, "mime": google.PPTX}]}
        result = self.archive(found(row(ic_identity_verified=True, ic_student_id="123456", earlier=[prior])))
        self.assertEqual((result["status"], result["filed"]), ("held", 1))
        shortcuts = [m for m in self.drive.created if m["mimeType"] == archive.SHORTCUT]
        self.assertEqual([m["shortcutDetails"]["targetId"] for m in shortcuts], [SOURCE])

    def test_name_filing_requires_a_readable_current_course_roster(self):
        for candidate in (row(email="", matched_by="file name"), row(email="s.ana.rivera@other.example")):
            self.assertEqual(self.archive(found(candidate))["status"], "held")
        self.assertEqual(self.drive.created, [])

    def test_conflicting_email_identities_hold_all_candidates(self):
        result = self.archive(found(row(), row(first="Different", last="Person")))
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["filed"], 0)
        self.assertEqual(self.drive.created, [])

    def test_unconfirmed_lesson_can_file_reachable_work_without_asserting_a_score_match(self):
        data = found(row(files=[{"id": SOURCE, "other_week": 7}], earlier=[{
            "source": "folder", "submitted_at": "2026-10-06T12:00:00Z", "files": [{"id": EARLIER}]}]))
        data["other_files"] = [{"files": [{"id": "Unknown"}]}]
        result = self.archive(data)
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["filed"], 1)
        listing = archive.archive_listing("read", self.config, "biology")
        record = listing["students"][0]["records"][0]
        self.assertEqual(record["lesson_status"], "unverified")
        self.assertIn("lesson not confirmed", record["name"])
        self.assertEqual(record["original_filename"], self.drive.items[SOURCE]["name"])
        self.assertEqual(self.drive.moved, [])

    def test_one_feedback_pdf_per_saved_id_survives_reopen_bytes_difference(self):
        value = saved()
        result = archive.file_feedback("read", "write", self.config, value, b"%PDF fictional first rendering")
        self.assertEqual(result["status"], "verified")
        count = len(self.drive.created)
        again = archive.file_feedback("read", "write", self.config, value, b"%PDF different renderer timestamp")
        self.assertEqual(again["drive_file_id"], result["drive_file_id"])
        self.assertEqual(len(self.drive.created), count)
        self.assertEqual(result["created_at"], "2026-10-08T12:00:00Z")
        self.assertEqual(archive.lookup_report("read", self.config, value["id"])["drive_file_id"], result["drive_file_id"])
        self.assertEqual(len(archive.list_feedback("read", self.config)), 1)
        metadata = archive.list_feedback("read", self.config)[0]
        self.assertEqual(metadata["source_sha256"], "b" * 64)
        self.assertEqual(metadata["drive_md5"], hashlib.md5(b"%PDF fictional first rendering").hexdigest())
        self.assertEqual(metadata["drive_version"], "1")
        self.assertEqual(metadata["date"], "2026-10-07")

    def test_unsaved_or_nameless_feedback_remains_private_without_filing(self):
        for value in ({**saved(), "saved_at": None}, {**saved(), "archive_identity": {"name": "Ana R."}}):
            self.assertEqual(archive.file_feedback("read", "write", self.config, value, b"%PDF x")["status"], "held")
        self.assertEqual(self.drive.created, [])

    def test_tampered_feedback_pdf_cannot_be_printed_from_archive(self):
        result = archive.file_feedback("read", "write", self.config, saved(), b"%PDF original")
        self.drive.items[result["drive_file_id"]]["md5Checksum"] = "wrong"
        with self.assertRaises(archive.Held):
            archive.list_feedback("read", self.config)

    def test_feedback_read_skips_unrelated_missing_work_but_full_archive_still_checks_it(self):
        self.assertEqual(self.archive()["status"], "verified")
        feedback = archive.file_feedback("read", "write", self.config, saved(), b"%PDF original")
        work_key = archive.key("archivework", [ROOT, "biology", SOURCE])
        shortcut_id = self.port.rows[work_key].value["drive_file_id"]
        self.drive.items.pop(shortcut_id)
        with patch.object(google, "drive_file", side_effect=self.drive.metadata) as metadata, \
                patch.object(self.port, "read", wraps=self.port.read) as reads:
            printable = archive.list_feedback("read", self.config)
        self.assertEqual([r["drive_file_id"] for r in printable], [feedback["drive_file_id"]])
        self.assertNotIn(shortcut_id, [call.args[1] for call in metadata.call_args_list])
        self.assertTrue(any(work_key in call.args[0] for call in reads.call_args_list))
        listing = archive.archive_listing("read", self.config, "biology")
        self.assertEqual((listing["status"], listing["students"]), ("held", []))
        self.assertEqual(listing["counts"]["work"], 0)
        self.assertEqual(self.drive.items[SOURCE]["parents"], ["OriginalFormUploadFolder"])

    def test_feedback_read_retains_pdf_and_private_folder_verification(self):
        self.assertEqual(self.archive()["status"], "verified")
        feedback = archive.file_feedback("read", "write", self.config, saved(), b"%PDF original")
        pdf_id, student_id = feedback["drive_file_id"], feedback["student_folder_id"]
        section_id = self.drive.items[student_id]["parents"][0]
        for ident, field, value in (
                (pdf_id, "md5Checksum", "changed"),
                (pdf_id, "properties", {archive.TAG: "DifferentIdentity"}),
                (pdf_id, "parents", [COURSES["biology"]]),
                (pdf_id, "permissions", [{"type": "anyone", "role": "reader"}]),
                (student_id, "parents", [COURSES["biology"]]),
                (section_id, "permissions", [{"type": "anyone", "role": "reader"}])):
            with self.subTest(field=field, ident=ident):
                before = deepcopy(self.drive.items[ident])
                self.drive.items[ident][field] = value
                with self.assertRaises(archive.Held):
                    archive.list_feedback("read", self.config)
                self.drive.items[ident] = before

    def test_feedback_only_keeps_all_work_index_schema_status_and_owner_checks(self):
        self.assertEqual(self.archive()["status"], "verified")
        archive.file_feedback("read", "write", self.config, saved(), b"%PDF original")
        work_key = archive.key("archivework", [ROOT, "biology", SOURCE])
        original = deepcopy(self.port.rows[work_key].value)
        shortcut_id = original["drive_file_id"]
        for field, value in (("schema", "unknown"), ("status", "creating"),
                             ("record_key", "DifferentRecord"), ("class_key", "forensics"),
                             ("kind", "teacher_report"), ("student_key", "DifferentStudent"),
                             ("parent", COURSES["biology"])):
            with self.subTest(field=field):
                doc = self.port.rows[work_key]
                self.port.commit({work_key: ({**original, field: value}, doc.version)})
                with patch.object(google, "drive_file", side_effect=self.drive.metadata) as metadata, self.assertRaises(archive.Held):
                    archive.list_feedback("read", self.config)
                self.assertNotIn(shortcut_id, [call.args[1] for call in metadata.call_args_list])
                doc = self.port.rows[work_key]
                self.port.commit({work_key: (original, doc.version)})

    def test_feedback_only_omits_work_output_and_preserves_filing_coverage(self):
        self.assertEqual(self.archive()["status"], "verified")
        archive.file_feedback("read", "write", self.config, saved(), b"%PDF original")
        before = archive._filing(self.config, "biology")
        _, indexed = archive._indexed("read", self.config, "biology", feedback_only=True)
        self.assertEqual([a["kind"] for _, artifacts in indexed for a in artifacts], ["feedback"])
        self.assertEqual(archive._filing(self.config, "biology"), before)
        listing = archive.archive_listing("read", self.config, "biology")
        self.assertEqual((listing["counts"]["work"], listing["counts"]["feedback"]), (1, 1))
        self.assertEqual(listing["filing"], before)

    def test_feedback_only_holds_class_index_folder_locator_mismatch(self):
        self.assertEqual(self.archive()["status"], "verified")
        archive.file_feedback("read", "write", self.config, saved(), b"%PDF original")
        index_key = archive.key("archiveclass", [ROOT, "biology"])
        doc = self.port.rows[index_key]
        student_key = next(iter(doc.value["students"]))
        self.port.commit({index_key: ({**doc.value, "students": {student_key: "WrongFolderLocator"}}, doc.version)})
        with self.assertRaises(archive.Held):
            archive.list_feedback("read", self.config)

    def test_lost_create_response_recovers_exact_id_without_duplicate(self):
        self.drive.fail_after_create = True
        self.assertEqual(self.archive()["status"], "verified")
        self.assertEqual(len(self.drive.created), 3)

    def test_failed_creation_is_held_and_never_automatically_retried(self):
        self.drive.fail_create = True
        result = self.archive()
        self.assertEqual(result["status"], "held")
        count = len(self.drive.created)
        self.drive.fail_create = False
        again = self.archive()
        self.assertEqual(again["status"], "held")
        self.assertEqual(len(self.drive.created), count)

    def test_known_pdf_quota_records_exact_absence_and_parent_drive_without_retry(self):
        self.assertEqual(self.archive()["status"], "verified")
        self.drive.items[COURSES["biology"]]["driveId"] = "PrivateSharedDrive00001"
        self.drive.fail_create = True
        result = archive.file_feedback("read", "write", self.config, saved(), b"%PDF saved")
        self.assertEqual(result["status"], "held")
        record_key = archive.key("archivefeedback", [ROOT, saved()["id"]])
        value = self.port.rows[record_key].value
        self.assertEqual(value["status"], "creating")
        self.assertEqual(value["creation_failure"], "storage_quota")
        self.assertTrue(value["failure_absence_verified"])
        self.assertEqual(value["attempt_parent_drive_id"], "PrivateSharedDrive00001")
        self.assertNotIn(value["drive_file_id"], self.drive.items)
        count = len(self.drive.created)
        self.drive.fail_create = False
        again = archive.file_feedback("read", "write", self.config, saved(), b"%PDF saved")
        self.assertEqual(again["status"], "held")
        self.assertEqual(len(self.drive.created), count)

    def test_unconfirmed_create_error_has_no_known_quota_recovery_marker(self):
        self.assertEqual(self.archive()["status"], "verified")
        with patch.object(google, "archive_create", side_effect=google.GoogleError("Uncertain network response")):
            result = archive.file_feedback("read", "write", self.config, saved(), b"%PDF saved")
        self.assertEqual(result["status"], "held")
        value = self.port.rows[archive.key("archivefeedback", [ROOT, saved()["id"]])].value
        self.assertNotIn("creation_failure", value)
        self.assertNotIn("failure_absence_verified", value)

    def test_verified_ic_identity_reuses_prior_email_archive_and_retains_departed_history(self):
        self.assertEqual(self.archive()["status"], "verified")
        before = archive.archive_listing("read", self.config, "biology")["students"][0]["folder_url"]
        self.config[archive.ROSTER_CONFIG] = json.dumps([{"class_key": "biology", "student_id": "123456", "active": True,
            "school_email": "s.ana.rivera@school.example", "first": "Ana", "last": "Rivera", "section": "A4 Biology"}])
        self.assertEqual(self.archive()["status"], "verified")
        self.assertEqual(archive.archive_listing("read", self.config, "biology")["students"][0]["folder_url"], before)
        data = json.loads(self.config[archive.ROSTER_CONFIG]); data[0]["active"] = False
        self.config[archive.ROSTER_CONFIG] = json.dumps(data)
        listing = archive.archive_listing("read", self.config, "biology")
        self.assertEqual(listing["students"][0]["status"], "retained")
        self.assertEqual(listing["counts"]["work"], 1)
        self.assertEqual(self.archive()["status"], "held")

    def test_existing_unindexed_named_student_folder_is_not_duplicated(self):
        self.drive.add("ExistingSectionFolder001", "A4 Biology", google.FOLDER, [COURSES["biology"]])
        self.assertEqual(self.archive()["status"], "held")
        self.assertEqual(self.drive.created, [])

    def test_a_new_empty_week_cannot_clear_earlier_filing_holds_or_hide_verified_folders(self):
        complete = found()
        complete["week"] = "Wk 02"
        self.assertEqual(self.archive(complete)["status"], "verified")
        pending = found(row(matched_by="file name"))
        pending["week"] = "Wk 03"
        self.assertEqual(self.archive(pending)["status"], "held")
        empty = {"class_key": "biology", "week": "Wk 06", "lessons": [], "other_files": [], "problems": []}
        self.assertEqual(self.archive(empty)["status"], "verified")
        self.assertEqual(archive.file_feedback("read", "write", self.config, saved(), b"%PDF saved report")["status"], "verified")
        listing = archive.archive_listing("read", self.config, "biology")
        self.assertEqual(listing["status"], "held")
        self.assertEqual((len(listing["students"]), listing["counts"]["work"]), (1, 1))
        self.assertEqual(listing["counts"]["held"], 1)
        self.assertEqual(listing["filing"]["coverage"], "holds_remain")
        scans = {r["week"]: r for r in listing["filing"]["scans"]}
        self.assertEqual(set(scans), {"Wk 02", "Wk 03", "Wk 06"})
        self.assertEqual(scans["Wk 06"]["filed"], 0)
        self.assertEqual(scans["Wk 03"]["held"][0]["assignment"], "Energy Flow — Turn In")
        self.assertEqual(scans["Wk 03"]["held"][0]["name"], "Rivera, Ana")
        # Only a fresh confirmed scan of the held week can replace that hold.
        pending["lessons"][0]["forms"][0]["students"][0]["matched_by"] = "email"
        self.assertEqual(self.archive(pending)["status"], "verified")
        listing = archive.archive_listing("read", self.config, "biology")
        self.assertEqual(listing["counts"]["held"], 0)
        self.assertEqual(listing["filing"]["coverage"], "scanned_weeks_only")
        self.assertEqual({r["week"] for r in listing["filing"]["scans"]}, {"Wk 02", "Wk 03", "Wk 06"})

    def test_receipt_seed_preserves_other_weeks_and_performs_no_drive_mutation(self):
        self.assertEqual(self.archive()["status"], "verified")
        prior = {"class_key": "biology", "week": "Wk 03"}
        result = {"class_key": "biology", "status": "held", "filed": 16, "held": [{
            "name": "Rivera, Ana", "assignment": "Energy Flow", "problem": "The exact school email needs checking."}],
                  "problems": []}
        created = len(self.drive.created)
        receipt = archive.record_filing_receipt("read", "write", self.config, "biology", prior, result)
        self.assertEqual((receipt["week"], receipt["filed"], receipt["held_count"]), ("Wk 03", 16, 1))
        self.assertEqual(len(self.drive.created), created)
        self.assertEqual(self.drive.moved, [])
        self.assertEqual(self.drive.renamed, [])
        listing = archive.archive_listing("read", self.config, "biology")
        self.assertEqual({r["week"] for r in listing["filing"]["scans"]}, {"Wk 03", "Wk 04"})

    def test_missing_exact_week_holds_before_any_archive_create(self):
        data = found(); data.pop("week")
        self.assertEqual(self.archive(data)["status"], "held")
        self.assertEqual(self.drive.created, [])

    def test_unmatched_and_wrong_form_source_holds_have_assignment_context(self):
        data = found(row(matched=False))
        data["lessons"][0]["forms"][0]["elsewhere"] = [{"who": "Rivera, Ana", "section": "A4 Biology",
            "to": "Submitted through Wk 03 · Another Form; lesson match not confirmed"}]
        self.assertEqual(self.archive(data)["status"], "held")
        scan = archive.archive_listing("read", self.config, "biology")["filing"]["scans"][0]
        self.assertEqual(scan["held_count"], 2)
        self.assertTrue(all(h["assignment"] == "Energy Flow — Turn In" for h in scan["held"]))

    def test_listing_deduplicates_fresh_metadata_with_at_most_four_concurrent_reads(self):
        extra = []
        for first, last in (("Ben", "Stone"), ("Cara", "Liu")):
            ident = "FictionalSourceFor" + first
            self.drive.add(ident, "Energy Flow - " + first, google.PPTX, ["OriginalFormUploadFolder"])
            extra.append(row(first=first, last=last, email="s." + first.lower() + "." + last.lower() + "@school.example",
                             files=[{"id": ident, "mime": google.PPTX}]))
        self.assertEqual(self.archive(found(row(), *extra))["status"], "verified")
        watched = {ident for ident, item in self.drive.items.items() if item["properties"].get(archive.TAG)}
        calls, lock, gate = Counter(), Lock(), Barrier(4, timeout=5)
        state = {"active": 0, "peak": 0, "started": 0}

        def fetch(token, ident, fields=None):
            if ident not in watched:
                return self.drive.metadata(token, ident, fields)
            with lock:
                calls[ident] += 1
                state["active"] += 1
                state["started"] += 1
                first_wave = state["started"] <= 4
                state["peak"] = max(state["peak"], state["active"])
            try:
                if first_wave:
                    gate.wait()
                return self.drive.metadata(token, ident, fields)
            finally:
                with lock:
                    state["active"] -= 1

        with patch.object(google, "drive_file", side_effect=fetch), \
                patch.object(self.port, "read", wraps=self.port.read) as reads:
            _, students = archive._indexed("read", self.config, "biology")
        self.assertEqual(len(students), 3)
        self.assertEqual(state["peak"], 4)
        self.assertEqual(set(calls), watched)
        self.assertTrue(all(count == 1 for count in calls.values()))
        artifact_batches = [c.args[0] for c in reads.call_args_list if any(i.startswith("archivework_") for i in c.args[0])]
        self.assertEqual(len(artifact_batches), 1)
        self.assertEqual(len(artifact_batches[0]), 6)  # Three copies plus retained original links.

    def test_concurrent_metadata_still_holds_private_parent_and_shortcut_target_failures(self):
        self.assertEqual(self.archive()["status"], "verified")
        listing = archive.archive_listing("read", self.config, "biology")
        artifact_id = next(item["id"] for item in self.drive.items.values() if item["mimeType"] == archive.SHORTCUT)
        student_id = self.drive.items[artifact_id]["parents"][0]
        section_id = self.drive.items[student_id]["parents"][0]
        for ident, field, value in ((section_id, "permissions", [{"type": "anyone", "role": "reader"}]),
                                    (artifact_id, "parents", [COURSES["biology"]]),
                                    (artifact_id, "shortcutDetails", {"targetId": "DifferentOriginal"})):
            before = deepcopy(self.drive.items[ident])
            self.drive.items[ident][field] = value
            held = archive.archive_listing("read", self.config, "biology")
            self.assertEqual((held["status"], held["students"]), ("held", []))
            self.assertTrue(held["problems"])
            self.drive.items[ident] = before

    def test_concurrent_metadata_read_error_is_not_treated_as_an_empty_archive(self):
        self.assertEqual(self.archive()["status"], "verified")
        artifact = next(item for item in self.drive.items.values() if item["mimeType"] == archive.SHORTCUT)
        self.drive.items.pop(artifact["id"])
        held = archive.archive_listing("read", self.config, "biology")
        self.assertEqual((held["status"], held["students"]), ("held", []))
        self.assertTrue(held["problems"])

    def test_metadata_read_failure_cancels_queued_gets_and_preserves_the_original_error(self):
        self.assertEqual(self.archive()["status"], "verified")

        class QueuedExecutor:
            """Finish one read first; shutdown deterministically drains queued work."""
            def __init__(self, max_workers):
                self.max_workers, self.jobs, self.started = max_workers, [], []
                self.shutdown_args = []
                executors.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.shutdown(wait=True)

            def submit(self, fn, *args):
                future = Future()
                self.jobs.append((future, fn, args))
                if len(self.jobs) == 1:
                    future.set_exception(error)
                return future

            def map(self, fn, ids):
                futures = [self.submit(fn, ident) for ident in ids]
                return (future.result() for future in futures)

            def shutdown(self, wait=True, *, cancel_futures=False):
                self.shutdown_args.append((wait, cancel_futures))
                for future, fn, args in self.jobs:
                    if future.done():
                        continue
                    if cancel_futures:
                        future.cancel()
                    else:
                        self.started.append(args)
                        future.set_result(fn(*args))

        for error in (google.GoogleError("Google didn't answer in time. Try again.", reason="read_timeout"),
                      google.GoogleError("Unavailable", 429, retry_after="7"),
                      google.GoogleError("Not found", 404)):
            with self.subTest(reason=error.reason, status=error.status):
                executors = []
                with patch.object(archive, "ThreadPoolExecutor", QueuedExecutor):
                    with self.assertRaises(google.GoogleError) as result:
                        archive._indexed("read", self.config, "biology")
                    self.assertIs(result.exception, error)
                    held = archive.archive_listing("read", self.config, "biology")
                self.assertEqual((held["status"], held["students"], held["counts"]["held"]), ("held", [], 1))
                self.assertEqual(held["problems"], [str(error)])
                for executor in executors:
                    self.assertEqual(executor.max_workers, 4)
                    self.assertGreater(len(executor.jobs), 1)
                    self.assertEqual(executor.started, [])
                    self.assertTrue(all(future.cancelled() for future, _, _ in executor.jobs[1:]))
                    self.assertEqual(executor.shutdown_args, [(True, True)])

    def test_google_pdf_quota_failure_has_precise_redacted_explanation(self):
        response = Mock(status_code=403)
        response.json.return_value = {"error": {"errors": [{"reason": "storageQuotaExceeded"}], "message": "Private provider payload"}}
        session = Mock()
        session.post.return_value = response
        with patch.object(google, "_session", return_value=session):
            with self.assertRaises(google.GoogleError) as error:
                REAL_ARCHIVE_CREATE("secret-token", {"id": "Reserved", "mimeType": archive.PDF}, b"%PDF test")
        self.assertIn("storage quota", str(error.exception))
        self.assertIn("downloadable from the Hub", str(error.exception))
        self.assertNotIn("Private provider", str(error.exception))
        self.assertNotIn("secret-token", str(error.exception))

    def test_google_id_reservation_distinguishes_shortcuts_from_folders_and_pdfs(self):
        with patch.object(google, "get_json", return_value={"ids": ["Reserved"]}) as request:
            self.assertEqual(REAL_ARCHIVE_FILE_ID("write", shortcut=True), "Reserved")
            self.assertEqual(request.call_args.args[2]["type"], "shortcuts")
            self.assertEqual(REAL_ARCHIVE_FILE_ID("write"), "Reserved")
            self.assertEqual(request.call_args.args[2]["type"], "files")

    def test_unique_full_filename_can_file_provisionally_without_changing_source_or_feedback_identity(self):
        original = deepcopy(self.drive.items[SOURCE])
        candidate = row(email="", matched_by="file name")
        with patch.object(archive, "_filing_roster", return_value=name_roster()):
            result = self.archive(found(candidate))
            again = self.archive(found(candidate))
        self.assertEqual((result["status"], result["filed"]), ("verified", 1))
        self.assertEqual(again["status"], "verified")
        listing = archive.archive_listing("read", self.config, "biology")
        student = listing["students"][0]
        self.assertEqual(student["status"], "identity_unverified")
        self.assertEqual(student["identity_basis"], "unique_name_for_filing")
        self.assertEqual(student["records"][0]["identity_status"], "identity_unverified")
        self.assertIn("Rivera, Ana", student["records"][0]["name"])
        self.assertIn("filed by name", student["records"][0]["name"])
        self.assertEqual(self.drive.items[SOURCE], original)
        self.assertEqual(len([i for i in self.drive.created if i["mimeType"] == archive.SHORTCUT]), 1)
        # Filing permission does not change the strict native-feedback gate.
        value = saved(); value["archive_identity"].update(school_email="", matched_by="file name")
        self.assertEqual(archive.file_feedback("read", "write", self.config, value, b"%PDF unverified")["status"], "held")

    def test_name_then_verified_email_and_native_identity_reuse_one_student_folder(self):
        candidate = row(email="", matched_by="file name")
        with patch.object(archive, "_filing_roster", return_value=name_roster()):
            self.assertEqual(self.archive(found(candidate))["status"], "verified")
        folder = archive.archive_listing("read", self.config, "biology")["students"][0]["folder_url"]
        count = len(self.drive.created)
        self.assertEqual(self.archive()["status"], "verified")
        self.assertEqual(self.archive(found(row(ic_identity_verified=True, ic_student_id="123456")))["status"], "verified")
        with patch.object(archive, "_filing_roster", return_value=name_roster()):
            self.assertEqual(self.archive(found(candidate))["status"], "verified")
        listing = archive.archive_listing("read", self.config, "biology")
        self.assertEqual(len(self.drive.created), count)
        self.assertEqual(len(listing["students"]), 1)
        self.assertEqual(listing["students"][0]["folder_url"], folder)
        self.assertEqual(listing["students"][0]["status"], "unverified")
        self.assertEqual(listing["students"][0]["records"][0]["identity_status"], "verified")

    def test_named_earlier_folder_file_reuses_verified_email_folder_with_truthful_creation_date(self):
        self.drive.items[EARLIER]["name"] = "Earlier Energy Flow - Ana Rivera.pptx"
        self.drive.items[EARLIER]["createdTime"] = "2026-09-15T12:00:00Z"
        prior = {"source": "folder", "submitted_at": "2026-10-06T12:00:00Z", "email": "", "matched_by": "file name",
                 "files": [{"id": EARLIER, "name": self.drive.items[EARLIER]["name"], "mime": google.PPTX}]}
        with patch.object(archive, "_filing_roster", return_value=name_roster()):
            result = self.archive(found(row(earlier=[prior])))
        self.assertEqual((result["status"], result["filed"]), ("verified", 2))
        listing = archive.archive_listing("read", self.config, "biology")
        self.assertEqual(len(listing["students"]), 1)
        older = next(r for r in listing["students"][0]["records"] if r["source_file_id"] == EARLIER)
        self.assertEqual((older["date"], older["date_basis"]), ("2026-09-15", "file_created"))
        self.assertIn("file created", older["name"])
        self.assertEqual(older["identity_status"], "identity_unverified")
        self.assertEqual(self.drive.items[EARLIER]["parents"], ["OriginalFormUploadFolder"])

    def test_named_unknown_lesson_and_week_flag_can_file_without_weakening_source_scope(self):
        original = deepcopy(self.drive.items[SOURCE])
        candidate = row(email="", matched_by="file name", source="folder", matched=False,
                        files=[{"id": SOURCE, "other_week": 7, "mime": google.PPTX}])
        data = {"class_key": "biology", "week": "Wk 06", "lessons": [], "other_files": [candidate], "problems": []}
        with patch.object(archive, "_filing_roster", return_value=name_roster()):
            result = self.archive(data)
        self.assertEqual((result["status"], result["filed"]), ("verified", 1))
        record = archive.archive_listing("read", self.config, "biology")["students"][0]["records"][0]
        self.assertEqual(record["lesson_status"], "unverified")
        self.assertEqual(record["original_filename"], original["name"])
        self.assertEqual(record["source_context"]["week"], "Wk 06")
        self.assertIn("lesson not confirmed", record["name"])
        self.assertEqual(self.drive.items[SOURCE], original)
        self.assertEqual(data["other_files"][0]["files"][0]["other_week"], 7)

    def test_surname_only_duplicate_or_foreign_section_names_hold_with_resubmission_context(self):
        candidate = row(email="", matched_by="file name")
        original = deepcopy(self.drive.items[SOURCE])
        for filename, roster, reason in (
            ("Energy Flow - Rivera.pptx", name_roster(), "only a surname"),
            (original["name"], name_roster(*name_roster()), "more than one roster student"),
            (original["name"], [{**name_roster()[0], "class_key": "forensics", "section": "A2 Forensics"}], "another course or section")):
            self.drive.items[SOURCE]["name"] = filename
            with patch.object(archive, "_filing_roster", return_value=roster):
                result = self.archive(found(candidate))
            self.assertEqual((result["status"], result["filed"]), ("held", 0))
            self.assertIn(reason, result["held"][0]["problem"])
        self.assertFalse(any(i["mimeType"] == archive.SHORTCUT for i in self.drive.created))

    def test_earlier_email_for_another_student_cannot_inherit_current_display_name(self):
        other = {"class_key": "biology", "first": "Ben", "last": "Stone", "section": "A4 Biology", "order": "header"}
        prior = {"source": "form", "submitted_at": "2026-10-06T12:00:00Z", "email": "s.ben.stone@school.example",
                 "matched_by": "email", "files": [{"id": EARLIER, "mime": google.PPTX}]}
        with patch.object(archive, "_filing_roster", return_value=name_roster(other)):
            result = self.archive(found(row(earlier=[prior])))
        self.assertEqual((result["status"], result["filed"]), ("held", 1))
        self.assertIn("different student", result["held"][0]["problem"])

    def test_unnamed_source_hold_retains_exact_filename_for_a_named_resubmission(self):
        self.drive.items[SOURCE]["name"] = "Untitled presentation.pptx"
        original = deepcopy(self.drive.items[SOURCE])
        candidate = row(email="", matched_by="file name", first="", last="", section="")
        candidate["files"][0]["name"] = original["name"]
        with patch.object(archive, "_filing_roster", return_value=name_roster()):
            result = self.archive(found(candidate))
        self.assertEqual((result["status"], result["filed"]), ("held", 0))
        held = result["held"][0]
        self.assertIn("resubmission", held["problem"])
        self.assertEqual(held["source_file_names"], original["name"])
        receipt = archive._filing_receipt("biology", "Wk 03", result)
        self.assertEqual(receipt["held"][0]["source_file_names"], original["name"])
        self.assertEqual(receipt["held"][0]["source_files"], [{"id": SOURCE, "name": original["name"]}])
        index = archive.work_index_rows("read", self.config)
        review = next(r for r in index["rows"] if "Filing held:" in r[11])
        self.assertEqual(review[2], "Unconfirmed submission: " + original["name"])
        self.assertEqual(review[7], "")
        self.assertEqual(review[8], archive._link(archive._url(SOURCE), "Open original"))
        self.assertEqual(review[9:11], ["", ""])
        self.assertIn(original["name"], review[11])
        self.assertEqual(index["summary"]["biology"]["student_folders"], 0)
        self.assertEqual(self.drive.items[SOURCE], original)
        self.assertFalse(any(i["mimeType"] == archive.SHORTCUT for i in self.drive.created))

    def test_held_source_locators_reject_supplied_urls_and_preserve_older_row_key(self):
        held = {"assignment": "Lesson not confirmed", "source_file_names": "Untitled source.pptx",
                "problem": "Student and course are not confirmed."}
        scan = {"week": "Wk 03", "held": [held], "problems": []}
        with patch.object(archive, "_filing", return_value={"scans": [scan]}):
            prior = archive.work_index_rows("read", self.config)["rows"][0]
        held["source_files"] = [{"id": "https://untrusted.example/source", "name": "Unsafe"},
                                {"id": SOURCE, "name": "Untitled source.pptx", "url": "https://untrusted.example/source"},
                                {"id": SOURCE, "name": "Duplicate"}]
        receipt = archive._filing_receipt("biology", "Wk 03", {"status": "held", "held": [held]})
        self.assertEqual(receipt["held"][0]["source_files"], [{"id": SOURCE, "name": "Untitled source.pptx"}])
        with patch.object(archive, "_filing", return_value={"scans": [{**scan, "held": receipt["held"]}]}):
            current = archive.work_index_rows("read", self.config)["rows"][0]
        self.assertEqual(current[13], prior[13])
        self.assertEqual(current[8], archive._link(archive._url(SOURCE), "Open original"))
        self.assertNotIn("untrusted", str(current))
        self.assertFalse(self.drive.copied)

    def test_actual_roster_reader_retains_duplicate_rows_for_name_collision_checks(self):
        self.config["HUB_ROSTER_SPREADSHEET_ID"] = "FictionalRosterWorkbook001"
        cells = {(1, 1): {"formattedValue": "Last name"}, (1, 2): {"formattedValue": "First name"},
                 (2, 1): {"formattedValue": "Rivera"}, (2, 2): {"formattedValue": "Ana"},
                 (3, 1): {"formattedValue": "Rivera"}, (3, 2): {"formattedValue": "Ana"}}
        with patch.object(google, "sheet_tabs", return_value=("Roster", {"A4 Biology": 4})), \
                patch.object(google, "sheet_grid", return_value={"A4 Biology": cells}):
            result = self.archive(found(row(email="", matched_by="file name")))
        self.assertEqual((result["status"], result["filed"]), ("held", 0))
        self.assertIn("more than one roster student", result["held"][0]["problem"])

    def test_name_filing_holds_incomplete_roster_range_and_shared_section_tab(self):
        self.config["HUB_ROSTER_SPREADSHEET_ID"] = "FictionalRosterWorkbook001"
        cells = {(1, 1): {"formattedValue": "Last name"}, (1, 2): {"formattedValue": "First name"},
                 (2, 1): {"formattedValue": "Rivera"}, (2, 2): {"formattedValue": "Ana"},
                 (200, 1): {"formattedValue": "LastVisibleName"}}
        with patch.object(google, "sheet_tabs", return_value=("Roster", {"A4 Biology": 4})), \
                patch.object(google, "sheet_grid", return_value={"A4 Biology": cells}):
            result = self.archive(found(row(email="", matched_by="file name")))
        self.assertEqual((result["status"], result["filed"]), ("held", 0))
        self.assertIn("reading limit", result["held"][0]["problem"])
        self.config["HUB_ROSTER_TABS"] = json.dumps({"A2 Forensics": "OneTab", "A3 Forensics": "OneTab"})
        with patch.object(google, "sheet_tabs", return_value=("Roster", {"OneTab": 2})), self.assertRaises(archive.Held):
            archive._filing_roster("read", self.config, "forensics")
        self.assertFalse(any(i["mimeType"] == archive.SHORTCUT for i in self.drive.created))

    def test_two_existing_name_claims_are_never_merged_or_duplicated(self):
        self.assertEqual(self.archive()["status"], "verified")
        root = archive.key("archiveclass", [ROOT, "biology"])
        index = self.port.rows[root]
        original_key = next(iter(index.value["students"]))
        original = self.port.rows[original_key]
        second_key = archive.key("archivestudent", [ROOT, "biology", "email:second@school.example"])
        self.drive.add("SecondClaimStudentFolder", "Rivera, Ana", google.FOLDER, [original.value["parent"]])
        second = {**deepcopy(original.value), "drive_file_id": "SecondClaimStudentFolder", "student_key": second_key}
        self.port.commit({second_key: (second, None), root: ({**index.value, "students": {
            **index.value["students"], second_key: second["drive_file_id"]}}, index.version)})
        # Even a previously valid name alias cannot hide a competing history.
        created = len(self.drive.created)
        with patch.object(archive, "_filing_roster", return_value=name_roster()):
            result = self.archive(found(row(email="", matched_by="file name")))
        self.assertEqual((result["status"], result["filed"]), ("held", 0))
        self.assertIn("Two archive folders", result["held"][0]["problem"])
        self.assertEqual(len(self.drive.created), created)

    def test_legacy_full_first_name_and_surname_initial_reuses_verified_folder_without_native_identity(self):
        self.assertEqual(self.archive()["status"], "verified")
        original = archive.archive_listing("read", self.config, "biology")["students"][0]
        with patch.object(archive, "_filing_roster", return_value=name_roster()):
            person = archive.filing_person_for_legacy_label("read", self.config, "biology", "Student: Ana R.", section="A4 Biology")
        self.assertEqual(person["identity_status"], "identity_unverified")
        self.assertEqual((person["school_email"], person["student_id"]), ("", ""))
        run = archive._Run("read", "write", self.config, "biology")
        student = run.student(person)
        run.finish({"status": "verified"})
        self.assertEqual(student["student_key"], original["student_key"])
        self.assertEqual(student["url"], original["folder_url"])

    def test_legacy_label_needs_unique_explicit_first_name_and_source_section(self):
        duplicate_initial = {**name_roster()[0], "last": "Reed"}
        for label, roster, section in (("A. Rivera", name_roster(), "A4 Biology"),
                                       ("Rivera", name_roster(), "A4 Biology"),
                                       ("Ana R.", name_roster(duplicate_initial), "A4 Biology"),
                                       ("Ana Rivera", name_roster(), "A2 Forensics")):
            with patch.object(archive, "_filing_roster", return_value=roster), self.assertRaises(archive.Held):
                archive.filing_person_for_legacy_label("read", self.config, "biology", label, section=section)
        self.assertEqual(self.drive.created, [])

    def test_held_elsewhere_source_can_file_as_unknown_lesson_without_changing_its_hold(self):
        data = found()
        form = data["lessons"][0]["forms"][0]
        form["students"] = []
        form["elsewhere"] = [{"source": "form", "file": self.drive.items[SOURCE]["name"],
            "url": archive._url(SOURCE), "to": "Week 06 match not confirmed", "section": "A4 Biology",
            "submitted_at": "2026-10-07T13:00:00Z"}]
        original = deepcopy(data)
        with patch.object(archive, "_filing_roster", return_value=name_roster()):
            result = self.archive(data)
        self.assertEqual((result["status"], result["filed"]), ("verified", 1))
        record = archive.archive_listing("read", self.config, "biology")["students"][0]["records"][0]
        self.assertEqual(record["lesson_status"], "unverified")
        self.assertEqual(record["source_file_id"], SOURCE)
        self.assertEqual(data, original)

    def test_accepted_intake_keeps_original_form_event_separate_from_target_assignment(self):
        candidate = row(files=[{"id": SOURCE, "mime": google.PPTX,
            "source_form_id": "FictionalOriginalForm00001", "assignment_form_id": "FictionalTargetForm000001",
            "source_response_id": "FictionalResponse0000001", "source_submitted_at": "2026-10-06T13:00:00Z",
            "accepted_intake": True}])
        self.assertEqual(self.archive(found(candidate))["status"], "verified")
        item = self.port.rows[archive.key("archivework", [ROOT, "biology", SOURCE])].value
        self.assertEqual(item["form_id"], "FictionalOriginalForm00001")
        self.assertEqual(item["assignment_form_id"], "FictionalTargetForm000001")
        self.assertEqual(item["submitted_at"], "2026-10-06T13:00:00Z")
        self.assertEqual(item["date"], "2026-10-06")
        self.assertTrue(item["source_context"]["accepted_intake"])
        self.assertEqual(item["source_context"]["source_response_id"], "FictionalResponse0000001")

    def test_folder_assignment_form_is_not_claimed_as_an_original_form_receipt(self):
        self.assertEqual(self.archive()["status"], "verified")
        before = deepcopy(self.port.rows[archive.key("archivework", [ROOT, "biology", SOURCE])].value)
        candidate = row(source="folder", email="", matched_by="file name")
        with patch.object(archive, "_filing_roster", return_value=name_roster()):
            self.assertEqual(self.archive(found(candidate))["status"], "verified")
        after = self.port.rows[archive.key("archivework", [ROOT, "biology", SOURCE])].value
        self.assertEqual(after["form_id"], before["form_id"])
        self.assertEqual(after["source_context"], before["source_context"])
        observations = [d.value for k, d in self.port.rows.items() if k.startswith("archiveobservation_")]
        folder = next(o for o in observations if o["date_basis"] == "file_created")
        self.assertIsNone(folder["source_context"]["form_id"])
        self.assertEqual(folder["source_context"]["assignment_form_id"], "FictionalForm000000001")

    def test_selected_verified_withdrawal_retains_history_without_active_roster_claim(self):
        self.assertEqual(self.archive()["status"], "verified")
        ident = next(k for k in self.port.rows if k.startswith("archivestudent_"))
        old = self.port.rows[ident]
        status = {"verified": True, "basis": "verified_selected_ic_roster", "class_key": "biology",
            "section": "A4 Biology", "status": "withdrawn", "active": False, "end_date": "2026-10-01",
            "school_year": "2026-27", "checked_at": "2026-10-09T14:00:00Z"}
        self.port.commit({ident: ({**old.value, "roster_status": status}, old.version)})
        listing = archive.archive_listing("read", self.config, "biology")
        self.assertFalse(listing["roster_verified"])
        self.assertEqual(listing["students"][0]["status"], "withdrawn")
        self.assertEqual(listing["students"][0]["roster_status"]["end_date"], "2026-10-01")
        self.assertEqual(len(listing["students"][0]["records"]), 1)
        for updates in ({"verified": False}, {"class_key": "forensics"}, {"section": "A2 Forensics"}, {"active": True}):
            self.assertIsNone(archive._confirmed_roster_status({**old.value, "roster_status": {**status, **updates}}, "biology"))

    def test_shared_feedback_exposes_actual_checksum_and_exact_packet_coverage(self):
        value = saved(); raw = b"%PDF immutable shared student feedback"
        self.assertEqual(archive.file_feedback("read", "write", self.config, value, raw)["status"], "verified")
        ident = next(k for k in self.port.rows if k.startswith("archivefeedback_"))
        old = self.port.rows[ident]
        fields = {"shared_source_key": "sharedfeedbacksource_" + "c" * 64,
            "shared_source_document_id": "FictionalSharedOriginal01", "shared_pdf_sha256": hashlib.sha256(raw).hexdigest(),
            "covered_by_packet_manifest": "feedbackpacketmanifest_" + "d" * 64,
            "covered_by_packet_id": "FictionalWholeClassPDF01"}
        self.port.commit({ident: ({**old.value, **fields}, old.version)})
        meta = archive.list_feedback("read", self.config)[0]
        self.assertEqual(meta["pdf_sha256"], hashlib.sha256(raw).hexdigest())
        for field, expected in fields.items():
            self.assertEqual(meta[field], expected)

    def test_exact_configured_week_exclusion_suppresses_only_missing_form_warning(self):
        reason = "This first-week lesson was not assigned this school year."
        self.config["HUB_WEEK_EXCLUSIONS"] = json.dumps([{"school_year": "2026-27", "class_key": "biology",
            "weeks": ["Wk 01"], "reason": reason}])
        data = {"class_key": "biology", "week": "Wk 01", "week_policy": {"excluded": True, "school_year": "2026-27", "reason": reason},
            "lessons": [{"forms": [], "problem": "This lesson has no Form link in its 'Turn it in' column."}],
            "other_files": [], "problems": []}
        self.assertEqual(self.archive(data)["status"], "verified")
        data["week_policy"]["school_year"] = "2027-28"
        self.assertEqual(self.archive(data)["status"], "held")
        data["week_policy"]["school_year"] = "2026-27"
        data["lessons"][0]["problem"] = "The private submission folder is unavailable."
        self.assertEqual(self.archive(data)["status"], "held")

    def test_teacher_attributed_joint_native_copy_is_read_only_and_revision_bound(self):
        self.assertEqual(self.archive()["status"], "verified")
        student_key, student = next((k, doc) for k, doc in self.port.rows.items() if doc.value.get("student_key") and doc.value.get("items"))
        ordinary = next(k for k in student.value["items"] if k.startswith("archiveworkcopy_"))
        self.port.commit({student_key: ({**student.value, "items": {k: v for k, v in student.value["items"].items() if k != ordinary}}, student.version)})
        ident = archive.key("archivework", [ROOT, "biology", SOURCE]); old = self.port.rows[ident]
        value = {**old.value, "source_kind": "teacher_attributed_joint_copy", "mime": "application/vnd.google-apps.presentation",
            "drive_version": "1", "teacher_joint_attribution": True,
            "shared_source_key": "sharedfeedbacksource_" + "c" * 64, "copied_slide_content_sha256": "d" * 64}
        meta = self.drive.items[value["drive_file_id"]]
        meta.update(mimeType=value["mime"], properties={archive.TAG: ident, "hubCopiedSource": SOURCE,
            "hubSharedSource": value["shared_source_key"]})
        meta.pop("shortcutDetails")
        self.port.commit({ident: (value, old.version)})
        listing = archive.archive_listing("read", self.config, "biology")
        self.assertEqual(listing["counts"]["work"], 1)
        self.assertEqual(listing["students"][0]["records"][0]["source_file_id"], SOURCE)
        count = len(self.drive.created)
        meta["version"] = "2"
        self.assertEqual(archive.archive_listing("read", self.config, "biology")["status"], "held")
        self.assertEqual(len(self.drive.created), count)
        meta["version"] = "1"; meta["properties"]["hubCopiedSource"] = EARLIER
        self.assertEqual(archive.archive_listing("read", self.config, "biology")["status"], "held")


class OverlappingWritingIndexTests(unittest.TestCase):
    """Verified archive inputs; only the derived index projection is exercised."""

    def setUp(self):
        self.student = {"student_key": "FictionalStudentKey", "name": "Example, Learner",
                        "section": "Fictional section", "drive_file_id": "FictionalStudentFolder001"}
        self.works, self.prior = [], []
        for number in range(3):
            ident = "FictionalImportedOriginal%02d" % number
            original = {"kind": "work", "record_key": archive.key("archiveimportedwork", [ident]),
                "source_kind": archive.IMPORTED_ORIGINAL, "drive_file_id": ident, "mime": archive.PDF,
                "imported_file_revision": {"version": "2"}, "copied_content_proof": {"sha256": "a" * 64},
                "date": "2032-01-02", "import_provenance": {"fictional_receipt": ident},
                "assignment": "Fictional worksheet %d" % number, "url": archive._url(ident)}
            source = {"source_kind": archive.IMPORTED_ORIGINAL, "source_record_key": original["record_key"],
                "source_file_id": ident, "source_version": "2", "source_sha256": "a" * 64,
                "date": original["date"], "import_provenance": deepcopy(original["import_provenance"])}
            report = {"kind": "feedback", "report_type": "science_writing", "record_key": "FictionalPriorReport%d" % number,
                "created_at": "2032-01-10T12:00:00Z", "date": "2032-01-10", "source_records": [source],
                "url": archive._url("FictionalPriorFeedback%02d" % number)}
            self.works.append(original); self.prior.append(report)
        self.later = {"kind": "feedback", "report_type": "science_writing", "record_key": "FictionalCombinedReport",
            "created_at": "2032-01-19T12:00:00Z", "date": "2032-01-19",
            "source_records": [deepcopy(r["source_records"][0]) for r in self.prior],
            "url": archive._url("FictionalCombinedFeedback001")}

    def project(self, reports, works=None):
        artifacts = (self.works if works is None else works) + reports
        with patch.object(archive, "_indexed", side_effect=lambda token, config, cls, **kwargs:
                ({}, [(self.student, artifacts)] if cls == "biology" else [])), \
                patch.object(archive, "_filing", return_value={"scans": []}):
            result = archive.work_index_rows("read", {})
            result.pop("checked_at")
            return result

    def test_three_overlapping_originals_keep_old_anchor_report_and_new_independent_rows(self):
        prior = self.project(self.prior)
        current = self.project(self.prior + [self.later])
        old_by_key = {r[13]: r for r in prior["rows"]}
        new_by_key = {r[13]: r for r in current["rows"]}
        self.assertEqual(len(new_by_key), 6)
        self.assertEqual(len(current["rows"]), 6)
        self.assertEqual(current["summary"]["biology"]["feedback_files"], 4)
        for original, report in zip(self.works, self.prior):
            anchor = archive.key("workindexrow", [self.student["student_key"], original["record_key"]])
            self.assertEqual(old_by_key[anchor], new_by_key[anchor])
            self.assertEqual(current["imported_anchor_feedback"][anchor], archive._link(report["url"], "Open feedback"))
            extra = archive.key("workindexrow", [self.student["student_key"], self.later["record_key"], original["drive_file_id"]])
            self.assertEqual(new_by_key[extra][9], archive._link(self.later["url"], "Open feedback"))
            self.assertEqual(new_by_key[extra][11], "Science writing historical reference; assessment coverage not verified")
        cells = [{"values": [archive._index_cell(v) for v in archive.INDEX_HEADERS]}]
        for row in prior["rows"]:
            cells.append({"values": [archive._index_cell(v, link=c in {7, 8, 9, 10} and bool(v))
                                      for c, v in enumerate(row)]})
            cells[-1]["values"][12] = archive._index_cell("Existing teacher note for " + row[13])
        prepared, _ = archive._index_layout(cells, current["rows"], imported_anchor_feedback=current["imported_anchor_feedback"])
        for row in prepared[1:]:
            key = archive._index_entered(row)[13]["stringValue"]
            if key in old_by_key:
                self.assertEqual(archive._index_entered(row)[12], {"stringValue": "Existing teacher note for " + key})
                self.assertEqual(archive._index_entered(row)[9], archive._index_cell(old_by_key[key][9], link=True)["userEnteredValue"])

    def test_overlap_keys_and_report_pairing_are_stable_across_order_and_replay(self):
        expected = self.project(self.prior + [self.later])
        for reports in permutations(self.prior + [self.later]):
            self.assertEqual(self.project(list(reports), list(reversed(self.works))), expected)
        # Even equal timestamps have a stable immutable-record tie breaker.
        for report in self.prior:
            report["created_at"] = self.later["created_at"]
        self.assertEqual(self.project(self.prior + [self.later]), self.project([self.later] + list(reversed(self.prior))))

    def test_superseded_report_remains_history_and_does_not_compete_for_anchor(self):
        earlier = deepcopy(self.prior[0]); earlier["supersession_verified"] = True
        current = self.project([earlier, self.later])
        anchor = archive.key("workindexrow", [self.student["student_key"], self.works[0]["record_key"]])
        self.assertEqual(next(r for r in current["rows"] if r[13] == anchor)[9], archive._link(self.later["url"], "Open feedback"))
        historical = next(r for r in current["rows"] if r[9] == archive._link(earlier["url"], "Open feedback"))
        self.assertIn("Superseded feedback; previous version retained", historical[11])
        self.assertNotIn(anchor, current["imported_anchor_feedback"])
        self.assertEqual(current["summary"]["biology"]["historical_feedback_files"], 1)

    def test_foreign_imported_source_proof_never_borrows_the_old_work_anchor(self):
        foreign = deepcopy(self.later)
        foreign["source_records"][0]["source_version"] = "9"
        current = self.project(self.prior + [foreign])
        row = next(r for r in current["rows"] if r[13] == archive.key("workindexrow", [
            self.student["student_key"], foreign["record_key"], self.works[0]["drive_file_id"]]))
        self.assertEqual(row[11], "Science writing historical reference; assessment coverage not verified; filed work not located")
        self.assertEqual(row[7:9], ["", ""])

    def test_whole_class_read_failure_is_distinct_from_recorded_source_holds(self):
        scan = {"week": "Wk 04", "held": [{"problem": "One original needs its existing source check"}], "problems": []}
        for error in (google.GoogleError("Google didn't answer in time. Try again."),
                      archive.Held("Class folder unavailable"), archive.Conflict("Class history changed"),
                      archive.StoreUnavailable("state unavailable")):
            with self.subTest(error=type(error).__name__):
                def indexed(token, config, cls, **kwargs):
                    if cls == "anatomy":
                        raise error
                    return {}, []
                with patch.object(archive, "_indexed", side_effect=indexed), patch.object(archive, "_filing",
                        side_effect=lambda config, cls: {"scans": [scan] if cls == "forensics" else []}):
                    partial = archive.work_index_rows("read", {})
                self.assertEqual([f["class_key"] for f in partial["failed_class_histories"]], ["anatomy"])
                self.assertEqual(len(partial["holds"]), 2)
                self.assertTrue(any("Listing held:" in row[11] for row in partial["rows"]))
        with patch.object(archive, "_indexed", return_value=({}, [])), patch.object(archive, "_filing",
                side_effect=lambda config, cls: {"scans": [scan] if cls == "forensics" else []}):
            readable = archive.work_index_rows("read", {})
        self.assertEqual(readable["failed_class_histories"], [])
        self.assertEqual(len(readable["holds"]), 1)
        self.assertIn("Filing held:", readable["rows"][0][11])


if __name__ == "__main__":
    unittest.main()
