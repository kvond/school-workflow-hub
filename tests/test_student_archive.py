"""Private archival acceptance using fictional Drive and disposable CAS state."""
from copy import deepcopy
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from threading import Barrier, Lock
import unittest
from unittest.mock import Mock, patch

from hub_v2 import google, student_archive as archive
from tests.test_cloud_state import MemoryCAS

ROOT = "FictionalArchiveRoot00001"
COURSES = {"biology": "FictionalBiologyFolder001", "forensics": "FictionalForensicsFolder01",
           "anatomy": "FictionalAnatomyFolder001"}
SOURCE = "FictionalStudentSource001"
EARLIER = "FictionalStudentEarlier01"
PERMISSIONS = [{"type": "user", "role": "owner", "emailAddress": "reader@fictional.example"},
               {"type": "user", "role": "writer", "emailAddress": "teacher@school.example"}]
REAL_ARCHIVE_CREATE = google.archive_create
REAL_ARCHIVE_FILE_ID = google.archive_file_id


class FakeDrive:
    def __init__(self):
        self.items, self.created, self.renamed, self.moved = {}, [], [], []
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
    def setUp(self):
        self.drive, self.port, self.config = FakeDrive(), MemoryCAS(), config()
        self.patches = [patch.object(archive, "configured_store", return_value=self.port),
                        patch.object(google, "drive_file", side_effect=self.drive.metadata),
                        patch.object(google, "folder_children", side_effect=self.drive.children),
                        patch.object(google, "archive_file_id", side_effect=self.drive.reserve),
                        patch.object(google, "archive_create", side_effect=self.drive.create),
                        patch.object(google, "rename_item", side_effect=self.drive.rename),
                        patch.object(google, "move_item", side_effect=self.drive.move)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def archive(self, data=None):
        return archive.archive_work("read", "write", self.config, "biology", data or found())

    def test_unconfigured_archive_makes_no_drive_or_state_write(self):
        result = archive.archive_work("read", "write", {}, "biology", found())
        self.assertFalse(result["configured"])
        self.assertEqual(self.drive.created, [])
        self.assertEqual(self.port.rows, {})

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

    def test_current_and_earlier_form_work_are_shortcuts_not_moves_or_copies(self):
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
        self.assertEqual(len(artifact_batches[0]), 3)

    def test_concurrent_metadata_still_holds_private_parent_and_shortcut_target_failures(self):
        self.assertEqual(self.archive()["status"], "verified")
        listing = archive.archive_listing("read", self.config, "biology")
        artifact_id = listing["students"][0]["records"][0]["drive_file_id"]
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


if __name__ == "__main__":
    unittest.main()
