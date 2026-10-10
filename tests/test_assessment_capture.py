"""PowerPoint capture support with synthetic ZIP content."""
import io
import unittest
import zipfile
from unittest.mock import patch

from school_workflow_hub.adapters import assessment_save as save


def pptx_bytes():
    out = io.BytesIO()
    slide = ('<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
             'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
             '<a:t>[[MARKER-INVENTORY:T01]]</a:t><a:t>student answer</a:t></p:sld>')
    notes = ('<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
             'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
             '<a:t>speaker note answer</a:t></p:notes>')
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide)
        archive.writestr("ppt/notesSlides/notesSlide1.xml", notes)
    return out.getvalue()


class PptxCaptureTests(unittest.TestCase):
    def test_extracts_slide_and_note_text(self):
        text = save._pptx_text(pptx_bytes())
        self.assertIn("[Slide 1]", text)
        self.assertIn("[[MARKER-INVENTORY:T01]]", text)
        self.assertIn("student answer", text)
        self.assertIn("[Notes 1]", text)
        self.assertIn("speaker note answer", text)

    def test_capture_downloads_pptx_as_media(self):
        file_id = "f" * 24
        meta = {"id": file_id, "name": "student.pptx", "mimeType": save.PPTX,
                "trashed": False, "modifiedTime": "2026-09-20T14:00:00Z",
                "version": "7"}
        with patch.object(save, "_identity_configuration", return_value=("aud", {"teacher@example.com"})), \
             patch.object(save, "authorize"), \
             patch.object(save, "service_token", return_value="token"), \
             patch.object(save, "_drive", side_effect=[meta, pptx_bytes(), meta]) as drive:
            result = save.capture("signed", file_id, {"HUB_READER_SERVICE_ACCOUNT": "reader"})
        self.assertEqual(result["file"]["mime_type"], save.PPTX)
        self.assertIn("student answer", result["text"])
        self.assertEqual(drive.call_args_list[1].args[1], "files/" + file_id)
        self.assertEqual(drive.call_args_list[1].args[2]["alt"], "media")
        import hashlib
        self.assertEqual(result['raw_sha256'], hashlib.sha256(pptx_bytes()).hexdigest())
        self.assertEqual(len(result['metadata_sha256']), 64)

    def test_changed_source_or_access_during_export_holds_the_capture(self):
        from copy import deepcopy
        file_id = 'f' * 24
        meta = {'id': file_id, 'name': 'student.pptx', 'mimeType': save.PPTX,
                'trashed': False, 'modifiedTime': '2026-09-20T14:00:00Z', 'version': '7',
                'parents': ['private'], 'permissions': [{'type': 'user', 'role': 'reader'}]}
        for field, value in [('version', '8'), ('parents', ['different']),
                             ('permissions', [{'type': 'anyone', 'role': 'reader'}])]:
            changed = deepcopy(meta); changed[field] = value
            with self.subTest(field=field), \
                 patch.object(save, '_identity_configuration', return_value=('aud', {'teacher@example.com'})), \
                 patch.object(save, 'authorize'), patch.object(save, 'service_token', return_value='token'), \
                 patch.object(save, '_drive', side_effect=[meta, pptx_bytes(), changed]), \
                 self.assertRaisesRegex(save.ConnectionError, 'changed_during_capture'):
                save.capture('signed', file_id, {'HUB_READER_SERVICE_ACCOUNT': 'reader'})

    def test_invalid_powerpoint_is_rejected(self):
        with self.assertRaisesRegex(save.ConnectionError, "unsupported"):
            save._pptx_text(b"not a zip")


if __name__ == "__main__":
    unittest.main()
