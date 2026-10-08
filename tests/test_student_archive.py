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

    def test_name_only_and_foreign_email_never_authorize_filing(self):
        for candidate in (row(email="", matched_by="file name"), row(email="s.ana.rivera@other.example")):
            self.assertEqual(self.archive(found(candidate))["status"], "held")
        self.assertEqual(self.drive.created, [])

    def test_conflicting_email_identities_hold_all_candidates(self):
        result = self.archive(found(row(), row(first="Different", last="Person")))
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["filed"], 0)
        self.assertEqual(self.drive.created, [])

    def test_unconfirmed_lesson_and_folder_only_history_stay_out(self):
        data = found(row(files=[{"id": SOURCE, "other_week": 7}], earlier=[{
            "source": "folder", "submitted_at": "2026-10-06T12:00:00Z", "files": [{"id": EARLIER}]}]))
        data["other_files"] = [{"files": [{"id": "Unknown"}]}]
        result = self.archive(data)
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["filed"], 0)
        self.assertFalse(any(m["mimeType"] == archive.SHORTCUT for m in self.drive.created))

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

    def test_unsaved_or_nameless_feedback_remains_private_without_filing(self):
        for value in ({**saved(), "saved_at": None}, {**saved(), "archive_identity": {"name": "Ana R."}}):
            self.assertEqual(archive.file_feedback("read", "write", self.config, value, b"%PDF x")["status"], "held")
        self.assertEqual(self.drive.created, [])

    def test_tampered_feedback_pdf_cannot_be_printed_from_archive(self):
        result = archive.file_feedback("read", "write", self.config, saved(), b"%PDF original")
        self.drive.items[result["drive_file_id"]]["md5Checksum"] = "wrong"
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


if __name__ == "__main__":
    unittest.main()
