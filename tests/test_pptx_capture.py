"""Synthetic PowerPoint archives; no student file conversion or Drive writes."""
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from unittest import TestCase
from unittest.mock import patch

from school_workflow_hub.adapters import pptx_text, assessment_save as capture
from school_workflow_hub.adapters.hosted_agenda import ConnectionError

P = pptx_text.NS['p']; A = pptx_text.NS['a']; R = pptx_text.NS['r']


def archive(slide1=None, target='slides/slide1.xml'):
    data = BytesIO()
    with ZipFile(data, 'w', ZIP_DEFLATED) as z:
        z.writestr('ppt/presentation.xml', f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst><p:sldId r:id="second"/><p:sldId r:id="first"/></p:sldIdLst></p:presentation>')
        z.writestr('ppt/_rels/presentation.xml.rels', f'<Relationships><Relationship Id="first" Target="{target}" Type="{R}/slide"/><Relationship Id="second" Target="slides/slide2.xml" Type="{R}/slide"/></Relationships>')
        z.writestr('ppt/slides/slide1.xml', slide1 or f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:spTree><p:grpSp><a:p><a:r><a:t>Initial: </a:t></a:r><a:r><a:t>Fictional answer</a:t></a:r><a:br/><a:r><a:t>[[Q1]]</a:t></a:r></a:p></p:grpSp><a:tbl><a:tr><a:tc><a:txBody><a:p><a:r><a:t>Revision: More evidence</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl></p:spTree></p:sld>')
        z.writestr('ppt/slides/slide2.xml', f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><a:p><a:r><a:t>VERSION=FICTIONAL-r1</a:t></a:r></a:p></p:sld>')
        z.writestr('ppt/notesSlides/notesSlide1.xml', '<teacher>PRIVATE NOTES NOT STUDENT WRITING</teacher>')
    return data.getvalue()


class PptxTextTests(TestCase):
    def test_presentation_order_groups_tables_and_markers_preserved(self):
        text = pptx_text.extract(archive())
        self.assertEqual(text, 'VERSION=FICTIONAL-r1\n\nInitial: Fictional answer\n[[Q1]]\nRevision: More evidence')
        self.assertNotIn('PRIVATE NOTES', text)

    def test_invalid_archives_external_targets_and_dtd_fail(self):
        for raw in [b'not a zip', archive(target='https://example.invalid/slide.xml'),
                    archive(target='../../outside.xml'), archive(slide1='<!DOCTYPE p [<!ENTITY x "bad">]><p>&x;</p>')]:
            with self.assertRaisesRegex(ConnectionError, 'pptx_invalid'):
                pptx_text.extract(raw)

    def test_expansion_and_text_limits_fail(self):
        with patch.object(pptx_text, 'MAX_EXPANDED', 10):
            with self.assertRaisesRegex(ConnectionError, 'too_large'):
                pptx_text.extract(archive())
        with patch.object(pptx_text, 'MAX_TEXT', 5):
            with self.assertRaisesRegex(ConnectionError, 'too_large'):
                pptx_text.extract(archive())


class CaptureTests(TestCase):
    def run_capture(self, meta, raw, after=None):
        with patch.object(capture, '_identity_configuration', return_value=('aud', {'fictional@example.invalid'})), \
             patch.object(capture, 'authorize'), patch.object(capture, 'service_token', return_value='read-token'), \
             patch.object(capture, '_drive', side_effect=[meta, raw, after or dict(meta)]) as drive:
            result = capture.capture('signed', 'a' * 24, {})
        return result, drive

    def metadata(self, mime):
        return {'id': 'a' * 24, 'name': 'Fictional deck', 'mimeType': mime,
                'trashed': False, 'version': '4', 'modifiedTime': '2026-09-15T12:00:00Z'}

    def test_pptx_uses_media_get_preserves_id_revision_and_zero_writes(self):
        result, drive = self.run_capture(self.metadata(pptx_text.MIME), archive())
        self.assertEqual(drive.call_args_list[1].args[2]['alt'], 'media')
        self.assertFalse(drive.call_args_list[1].args[1].endswith('/export'))
        self.assertEqual(result['file']['id'], 'a' * 24)
        self.assertEqual(result['file']['version'], '4')
        self.assertEqual(result['external_writes'], 0)
        self.assertIn('[[Q1]]', result['text'])

    def test_native_slides_keep_existing_export_and_text_hash(self):
        result, drive = self.run_capture(self.metadata('application/vnd.google-apps.presentation'), b'Fictional\r\ntext')
        self.assertTrue(drive.call_args_list[1].args[1].endswith('/export'))
        self.assertEqual(result['text'], 'Fictional\ntext')

    def test_changed_revision_during_download_cannot_be_assessed(self):
        meta = self.metadata(pptx_text.MIME)
        with self.assertRaisesRegex(ConnectionError, 'submission_changed_reassess'):
            self.run_capture(meta, archive(), dict(meta, version='5'))

    def test_corrupt_pptx_does_not_fall_through_as_blank_work(self):
        with self.assertRaisesRegex(ConnectionError, 'pptx_invalid'):
            self.run_capture(self.metadata(pptx_text.MIME), b'corrupt')
