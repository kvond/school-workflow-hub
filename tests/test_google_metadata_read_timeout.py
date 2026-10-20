"""A timed-out Drive metadata read may repeat; nothing else may."""
import unittest
from unittest.mock import patch

from hub_v2 import google


def timeout():
    return google.GoogleError("Google didn't answer in time. Try again.", reason="read_timeout")


class MetadataReadTimeoutTests(unittest.TestCase):
    fields = "id,name,mimeType,parents,permissions"

    def setUp(self):
        sleep = patch.object(google.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def test_permissions_page_timeout_repeats_the_identical_read(self):
        acl = [{"id": "teacher", "type": "user", "role": "owner"}]
        with patch.object(google, "get_json", side_effect=[timeout(), {"permissions": acl}]) as api:
            self.assertEqual(google._file_permissions("read-token", "file"), acl)
        self.assertEqual(api.call_count, 2)
        self.assertEqual(api.call_args_list[0], api.call_args_list[1])
        self.assertEqual(api.call_args_list[0].kwargs, {"timeout": 10})
        self.sleep.assert_called_once_with(google.METADATA_READ_BACKOFF[0])

    def test_files_get_timeout_repeats_then_reads_shared_drive_acl(self):
        meta = {"id": "file", "name": "Fictional", "parents": ["parent"]}
        acl = [{"id": "teacher", "type": "user", "role": "organizer"}]
        with patch.object(google, "get_json",
                          side_effect=[timeout(), timeout(), meta, {"permissions": acl}]) as api:
            result = google.drive_file("read-token", "file", self.fields)
        self.assertEqual(result["permissions"], acl)
        self.assertEqual(api.call_count, 4)
        self.assertEqual(self.sleep.call_count, 2)

    def test_third_timeout_is_final(self):
        with patch.object(google, "get_json", side_effect=[timeout()] * 4) as api:
            with self.assertRaises(google.GoogleError) as caught:
                google._file_permissions("read-token", "file")
        self.assertTrue(google.read_timed_out(caught.exception))
        self.assertEqual(api.call_count, google.METADATA_READ_ATTEMPTS)

    def test_other_errors_are_never_repeated(self):
        for error in (google.GoogleError("denied", 403), google.GoogleError("missing", 404),
                      google.GoogleError("Google didn't answer in time. Try again.")):
            with patch.object(google, "get_json", side_effect=[error, {"permissions": []}]) as api:
                with self.assertRaises(google.GoogleError):
                    google._file_permissions("read-token", "file")
            api.assert_called_once()
        self.sleep.assert_not_called()

    def test_incomplete_acl_after_a_retry_is_still_refused(self):
        with patch.object(google, "get_json", side_effect=[timeout(), {"permissions": []}]):
            with self.assertRaises(google.GoogleError):
                google._file_permissions("read-token", "file")


if __name__ == "__main__":
    unittest.main()
