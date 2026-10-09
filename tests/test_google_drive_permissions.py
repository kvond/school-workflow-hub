"""Shared Drive ACL readback must be complete before private filing proceeds."""
import unittest
from unittest.mock import patch

from hub_v2 import google, student_archive


class DrivePermissionReadbackTests(unittest.TestCase):
    fields = "id,name,mimeType,parents,trashed,permissions,capabilities,properties,driveId"

    def metadata(self):
        prefix = "Ana Rivera.Fictional lesson - "
        return {"id": "file", "name": prefix + "Original submission", "parents": ["parent"],
                "mimeType": google.FOLDER, "trashed": False, "driveId": "shared-drive",
                "capabilities": {"canAddChildren": True},
                "properties": {google.PREFIX_TAG: google._prefix_tag(prefix)}}

    def user(self, ident="teacher", role="organizer"):
        return {"id": ident, "type": "user", "emailAddress": ident + "@example.test", "role": role}

    def test_shared_drive_pages_preserve_every_acl_and_source_field(self):
        meta = self.metadata()
        acl = [self.user(), self.user("reader", "writer")]
        with patch.object(google, "get_json", side_effect=[meta,
                {"permissions": acl[:1], "nextPageToken": "next"}, {"permissions": acl[1:]}]) as api:
            result = google.drive_file("read-token", "file", self.fields)
        self.assertEqual(result["permissions"], acl)
        self.assertEqual(result["name"], "Original submission")
        self.assertEqual(result["shown_name"], "Ana Rivera.Fictional lesson - Original submission")
        self.assertEqual((result["parents"], result["driveId"], result["properties"]),
                         (["parent"], "shared-drive", meta["properties"]))
        self.assertIs(student_archive._private(result, parent="parent", folder=True, write=True), result)
        for call in api.call_args_list[1:]:
            self.assertEqual(call.args[:2], ("https://www.googleapis.com/drive/v3/files/file/permissions", "read-token"))
            self.assertEqual(call.args[2]["supportsAllDrives"], "true")
            self.assertEqual(call.args[2]["pageSize"], 100)
            self.assertEqual(call.kwargs["timeout"], 10)
            self.assertNotIn("method", call.kwargs)
        self.assertNotIn("pageToken", api.call_args_list[1].args[2])
        self.assertEqual(api.call_args_list[2].args[2]["pageToken"], "next")

    def test_existing_acl_and_queries_without_acl_do_not_make_extra_calls(self):
        for fields, meta in [(self.fields, {**self.metadata(), "permissions": [self.user()]}),
                             ("id,modifiedTime", {"id": "file", "modifiedTime": "timestamp"})]:
            with self.subTest(fields=fields), patch.object(google, "get_json", return_value=meta) as api:
                result = google.drive_file("read", "file", fields)
            self.assertEqual(api.call_count, 1)
            if "permissions" not in meta:
                self.assertEqual(result, meta)

    def test_permissions_selector_also_requests_the_missing_acl(self):
        for fields in ("id,permissions(id,type)", "id,permissions/emailAddress", "*"):
            with self.subTest(fields=fields), patch.object(google, "get_json", side_effect=[
                    self.metadata(), {"permissions": [self.user()]}]) as api:
                self.assertEqual(google.drive_file("read", "file", fields)["permissions"], [self.user()])
            self.assertEqual(api.call_count, 2)

    def test_missing_file_metadata_is_not_fabricated_from_a_permission_list(self):
        for meta in ({}, None, []):
            with self.subTest(meta=meta), patch.object(google, "get_json", return_value=meta) as api:
                with self.assertRaisesRegex(google.GoogleError, "incomplete Drive file metadata"):
                    google.drive_file("read", "file", self.fields)
            self.assertEqual(api.call_count, 1)

    def test_public_group_and_domain_acl_evidence_is_preserved_and_held(self):
        for kind in ("anyone", "group", "domain"):
            extra = {"id": "other", "type": kind, "emailAddress": "group@example.test", "role": "reader"}
            with self.subTest(kind=kind), patch.object(google, "get_json", side_effect=[
                    self.metadata(), {"permissions": [self.user()], "nextPageToken": "next"},
                    {"permissions": [extra]}]):
                result = google.drive_file("read", "file", self.fields)
            self.assertEqual(result["permissions"][-1], extra)
            with self.assertRaises(student_archive.Held):
                student_archive._private(result, folder=True)

    def test_preexisting_empty_acl_remains_a_hold(self):
        with patch.object(google, "get_json", return_value={**self.metadata(), "permissions": []}) as api:
            result = google.drive_file("read", "file", self.fields)
        self.assertEqual(api.call_count, 1)
        with self.assertRaises(student_archive.Held):
            student_archive._private(result, folder=True)

    def test_empty_or_malformed_permission_listing_cannot_prove_private_access(self):
        for page in ({"permissions": []}, {}, {"permissions": None}, {"permissions": [None]}, [],
                     {"permissions": [self.user()], "nextPageToken": 0}):
            with self.subTest(page=page), patch.object(google, "get_json", side_effect=[self.metadata(), page]):
                with self.assertRaises(google.GoogleError):
                    google.drive_file("read", "file", self.fields)

    def test_permission_api_failures_propagate_without_a_partial_acl(self):
        error = google.GoogleError("Access unavailable", 403)
        with patch.object(google, "get_json", side_effect=[self.metadata(),
                {"permissions": [self.user()], "nextPageToken": "next"}, error]):
            with self.assertRaises(google.GoogleError) as raised:
                google.drive_file("read", "file", self.fields)
        self.assertIs(raised.exception, error)

    def test_repeated_page_token_is_held(self):
        page = {"permissions": [self.user()], "nextPageToken": "repeat"}
        with patch.object(google, "get_json", side_effect=[self.metadata(), page, page]) as api:
            with self.assertRaisesRegex(google.GoogleError, "fully verified"):
                google.drive_file("read", "file", self.fields)
        self.assertEqual(api.call_count, 3)

    def test_permission_listing_has_a_hard_page_limit(self):
        pages = [{"permissions": [self.user(str(i))], "nextPageToken": "page" + str(i)} for i in range(10)]
        with patch.object(google, "get_json", side_effect=[self.metadata()] + pages) as api:
            with self.assertRaisesRegex(google.GoogleError, "verification limit"):
                google.drive_file("read", "file", self.fields)
        self.assertEqual(api.call_count, 11)

    def test_oversized_page_is_held_before_using_partial_evidence(self):
        with patch.object(google, "get_json", side_effect=[self.metadata(),
                {"permissions": [self.user(str(i)) for i in range(101)]}]):
            with self.assertRaises(google.GoogleError):
                google.drive_file("read", "file", self.fields)


if __name__ == "__main__":
    unittest.main()
