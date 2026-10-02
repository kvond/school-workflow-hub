"""Permanent ledger identity survives folder changes; fictional files only."""
import unittest
from unittest.mock import patch

from hub_v2 import google, ledger


class LedgerLocationTests(unittest.TestCase):
    CONFIG = {"HUB_SCORES_ENTERED_SPREADSHEET_ID": "FICTIONAL_LEDGER",
              "HUB_SUBMISSIONS_ROOT_FOLDER": "FICTIONAL_OLD_ROOT"}
    SHEET = {"id": "FICTIONAL_LEDGER", "name": "Scores entered (Dashboard)",
             "mimeType": google.SHEET, "trashed": False,
             "parents": ["FICTIONAL_NEW_PARENT"]}

    def test_moved_explicit_sheet_wins_over_old_folder_copies(self):
        with patch.object(google, "drive_file", return_value=self.SHEET) as get, \
                patch.object(google, "files_named_in") as search:
            self.assertEqual(ledger.find("token", self.CONFIG), self.SHEET)
            get.assert_called_once_with("token", "FICTIONAL_LEDGER")
            search.assert_not_called()

    def test_wrong_or_trashed_explicit_file_never_falls_back(self):
        for changes in ({"id": "OTHER"}, {"mimeType": google.FOLDER},
                        {"trashed": True}, {"name": "Unrelated records"}):
            with self.subTest(changes=changes), \
                    patch.object(google, "drive_file", return_value={**self.SHEET, **changes}), \
                    patch.object(google, "files_named_in") as search:
                with self.assertRaises(google.GoogleError):
                    ledger.find("token", self.CONFIG)
                search.assert_not_called()

    def test_unreadable_explicit_file_never_falls_back(self):
        with patch.object(google, "drive_file", side_effect=google.GoogleError("Unavailable")), \
                patch.object(google, "files_named_in") as search:
            with self.assertRaises(google.GoogleError):
                ledger.find("token", self.CONFIG)
            search.assert_not_called()

    def test_unconfigured_installations_keep_existing_folder_lookup(self):
        with patch.object(google, "drive_file") as get, \
                patch.object(google, "files_named_in", return_value=[self.SHEET]):
            self.assertEqual(ledger.find("token", {"HUB_SUBMISSIONS_ROOT_FOLDER": "ROOT"}), self.SHEET)
            get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
