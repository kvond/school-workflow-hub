"""Synthetic PowerPoint capture tests; no live submission data."""
import io
import unittest
import zipfile

from school_workflow_hub.adapters.assessment_save import _pptx_text
from school_workflow_hub.adapters.hosted_agenda import ConnectionError


def pptx(*slides):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for number, texts in enumerate(slides, 1):
            runs = "".join("<a:r><a:t>" + text + "</a:t></a:r>" for text in texts)
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                '<p:cSld><p:spTree><p:sp><p:txBody><a:p>' + runs +
                '</a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
            archive.writestr("ppt/slides/slide%d.xml" % number, xml)
    return stream.getvalue()


class PowerPointTextTests(unittest.TestCase):
    def test_extracts_slide_text_in_slide_order(self):
        text = _pptx_text(pptx(("First", "idea"), ("Second",)))
        self.assertEqual(text, "First\nidea\n\nSecond")

    def test_broken_powerpoint_fails_closed(self):
        with self.assertRaisesRegex(ConnectionError, "unsupported"):
            _pptx_text(b"not-a-zip")

    def test_image_only_powerpoint_is_not_claimed_as_text(self):
        with self.assertRaisesRegex(ConnectionError, "unsupported"):
            _pptx_text(pptx((),))


if __name__ == "__main__":
    unittest.main()
