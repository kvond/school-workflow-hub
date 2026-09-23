"""The boundary to Google: which scope is requested, and how a 403 is reported.

Both of the 2026-09-20 production failures lived here while 704 tests passed:
the runtime token carried no Drive scope, and every Drive 403 was reported to
the teacher as a sharing problem she could not fix by sharing.
"""
from unittest import TestCase
from unittest.mock import patch

from school_workflow_hub.adapters import hosted_agenda, submission_inventory as inv


class Response:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


SCOPE_DENIED = {"error": {"code": 403, "status": "PERMISSION_DENIED",
                          "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}
SHARING_DENIED = {"error": {"code": 403, "errors": [{"reason": "forbidden"}]}}


class ScopedTokenTests(TestCase):
    def test_token_is_minted_for_the_requested_scope_not_the_runtime_default(self):
        """Cloud Run's metadata server ignores a requested scope, so the scope
        has to be obtained by impersonation or Drive rejects every call."""
        with patch.object(hosted_agenda, "_request"), \
             patch("google.auth.compute_engine.Credentials") as runtime, \
             patch("google.auth.impersonated_credentials.Credentials") as scoped:
            runtime.return_value.service_account_email = "reader@fictional.iam.gserviceaccount.com"
            scoped.return_value.token = "fictional-scoped-token"
            token = hosted_agenda.service_token("reader@fictional.iam.gserviceaccount.com",
                                                inv.DRIVE_SCOPE)
        self.assertEqual(token, "fictional-scoped-token")
        self.assertEqual(scoped.call_args.kwargs["target_scopes"], [inv.DRIVE_SCOPE])
        self.assertEqual(runtime.call_args.kwargs, {}, "runtime credential must not carry scopes")

    def test_a_different_runtime_identity_is_refused(self):
        with patch.object(hosted_agenda, "_request"), \
             patch("google.auth.compute_engine.Credentials") as runtime, \
             patch("google.auth.impersonated_credentials.Credentials") as scoped:
            runtime.return_value.service_account_email = "someone-else@fictional.iam.gserviceaccount.com"
            with self.assertRaisesRegex(hosted_agenda.ConnectionError, "runtime_identity_unavailable"):
                hosted_agenda.service_token("reader@fictional.iam.gserviceaccount.com")
            scoped.assert_not_called()

    def test_submissions_are_read_with_a_scope_that_returns_parents(self):
        """drive.metadata.readonly returns a name but withholds `parents`,
        which empties folder resolution."""
        self.assertEqual(inv.DRIVE_SCOPE, "https://www.googleapis.com/auth/drive.readonly")


class ForbiddenReasonTests(TestCase):
    def test_scope_failure_is_not_reported_as_a_sharing_failure(self):
        self.assertEqual(inv._forbidden_reason(Response(403, SCOPE_DENIED)),
                         "submission_reader_scope_missing")

    def test_genuine_sharing_failure_still_asks_for_sharing(self):
        self.assertEqual(inv._forbidden_reason(Response(403, SHARING_DENIED)),
                         "submission_folder_access_required")

    def test_unreadable_body_falls_back_to_the_actionable_case(self):
        class Broken(Response):
            def json(self):
                raise ValueError("not json")
        self.assertEqual(inv._forbidden_reason(Broken(403)), "submission_folder_access_required")


class FolderResolutionTests(TestCase):
    records = {"submissions": [{"class_key": "forensics", "assignment_key": "forensics:F03",
                                "source_file_id": "1" + "a" * 32}]}

    def _resolve(self, reader):
        return inv.resolve_lesson_folders(reader, self.records, "forensics", "forensics:F03")

    def test_a_scope_failure_survives_folder_resolution(self):
        """The reason must not be collapsed into the sharing case on its way out."""
        class Reader:
            def metadata(self, file_id):
                raise inv.ConnectionError("submission_reader_scope_missing")
        with self.assertRaisesRegex(inv.ConnectionError, "submission_reader_scope_missing"):
            self._resolve(Reader())

    def test_readable_files_reporting_no_parent_are_not_a_sharing_problem(self):
        """With a root to search, a file that turns up nowhere under it is
        still not a sharing fault — and must not be reported as one."""
        class Reader:
            def metadata(self, file_id):
                return {"id": file_id, "name": "Fictional submission"}

            def children_of(self, folder_ids, cursor):
                return {"files": [], "incompleteSearch": False}
        with self.assertRaisesRegex(inv.ConnectionError, "submission_parents_unavailable"):
            inv.resolve_lesson_folders(Reader(), self.records, "forensics", "forensics:F03",
                                       "1" + "r" * 32)

    def test_resolved_parents_are_returned(self):
        class Reader:
            def metadata(self, file_id):
                return {"id": file_id, "parents": ["1" + "b" * 32]}
        folders, unreadable = self._resolve(Reader())
        self.assertEqual(folders, ["1" + "b" * 32])
        self.assertEqual(unreadable, 0)


class DriveReaderWiringTests(TestCase):
    """Through DriveReader.get, not just the helper: the 2026-09-20 defect was
    in how the caller branched on the status code, so the test has to cross it."""

    def _reader(self, response):
        reader = inv.DriveReader("fictional-token")

        class Session:
            headers = {}

            def get(self, *args, **kwargs):
                return response

        reader.session = Session()
        return reader

    def test_a_scope_403_reaches_the_caller_as_a_scope_error(self):
        with self.assertRaisesRegex(inv.ConnectionError, "submission_reader_scope_missing"):
            self._reader(Response(403, SCOPE_DENIED)).metadata("1" + "a" * 32)

    def test_a_sharing_403_reaches_the_caller_as_a_sharing_error(self):
        with self.assertRaisesRegex(inv.ConnectionError, "submission_folder_access_required"):
            self._reader(Response(403, SHARING_DENIED)).metadata("1" + "a" * 32)

    def test_a_404_reaches_the_caller_as_a_sharing_error(self):
        with self.assertRaisesRegex(inv.ConnectionError, "submission_folder_access_required"):
            self._reader(Response(404)).metadata("1" + "a" * 32)


class LocateLessonFoldersTests(TestCase):
    """Drive reports `parents` from files.list but not from files.get for
    content the reader can see and does not own, so folders are found by
    descending from the shared root and matching the recorded files."""

    ROOT = "1" + "r" * 32
    COURSE = "1" + "c" * 32
    LESSON = "1" + "l" * 32
    WANTED = "1" + "w" * 32

    def _reader(self, tree, calls=None):
        class Reader:
            def children_of(self, folder_ids, cursor):
                if calls is not None:
                    calls.append(sorted(folder_ids))
                files = [f for parent in folder_ids for f in tree.get(parent, [])]
                return {"files": files, "incompleteSearch": False}
        return Reader()

    def _tree(self):
        folder = inv.FOLDER
        return {
            self.ROOT: [{"id": self.COURSE, "name": "Forensics", "mimeType": folder,
                         "parents": [self.ROOT], "trashed": False}],
            self.COURSE: [{"id": self.LESSON, "name": "Wk 03 — Turn In", "mimeType": folder,
                           "parents": [self.COURSE], "trashed": False}],
            self.LESSON: [{"id": self.WANTED, "name": "s.fictional.student — work",
                           "mimeType": "application/vnd.google-apps.presentation",
                           "parents": [self.LESSON], "trashed": False}],
        }

    def test_the_folder_holding_a_recorded_file_is_found(self):
        folders = inv.locate_lesson_folders(self._reader(self._tree()), [self.WANTED], self.ROOT)
        self.assertEqual(folders, [self.LESSON])

    def test_a_level_is_one_query_for_every_folder_on_it(self):
        """A course holds dozens of lesson folders. Asking for them one at a
        time is dozens of round trips per Process work, so a level must cost
        one query however many folders are on it."""
        folder, sibling = inv.FOLDER, "1" + "s" * 32
        tree = self._tree()
        tree[self.COURSE] = tree[self.COURSE] + [
            {"id": sibling, "name": "Wk 04 — Turn In", "mimeType": folder,
             "parents": [self.COURSE], "trashed": False}]
        calls = []
        inv.locate_lesson_folders(self._reader(tree, calls), [self.WANTED], self.ROOT)
        self.assertEqual(calls[0], [self.ROOT])
        self.assertEqual(calls[1], [self.COURSE])
        self.assertEqual(calls[2], sorted([self.LESSON, sibling]),
                         "both lesson folders must be asked for in one query, not one each")
        self.assertEqual(len(calls), 3, "one request per level")

    def test_a_file_that_is_nowhere_under_the_root_yields_no_folder(self):
        folders = inv.locate_lesson_folders(self._reader(self._tree()), ["1" + "z" * 32], self.ROOT)
        self.assertEqual(folders, [])

    def test_resolution_falls_back_to_the_root_when_drive_withholds_parents(self):
        records = {"submissions": [{"class_key": "forensics", "assignment_key": "forensics:F03",
                                    "source_file_id": self.WANTED}]}
        tree = self._tree()

        class Reader:
            def metadata(self, file_id):
                return {"id": file_id, "name": "Fictional submission"}   # no parents

            def children_of(self, folder_ids, cursor):
                return {"files": [f for p in folder_ids for f in tree.get(p, [])],
                        "incompleteSearch": False}

        folders, unreadable = inv.resolve_lesson_folders(
            Reader(), records, "forensics", "forensics:F03", self.ROOT)
        self.assertEqual(folders, [self.LESSON])
        self.assertEqual(unreadable, 0)

    def test_without_a_configured_root_the_message_names_the_setting(self):
        records = {"submissions": [{"class_key": "forensics", "assignment_key": "forensics:F03",
                                    "source_file_id": self.WANTED}]}

        class Reader:
            def metadata(self, file_id):
                return {"id": file_id}

        with self.assertRaisesRegex(inv.ConnectionError, "submission_root_not_configured"):
            inv.resolve_lesson_folders(Reader(), records, "forensics", "forensics:F03")
