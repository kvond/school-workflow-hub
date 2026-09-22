"""Bounded extraction of PPTX slide text, without conversion or external reads.

Read slides in presentation order. Preserve paragraph boundaries and text in
tables/groups, including hidden marker text. Speaker notes are not responses.
Images are not OCRed; this has the same text-only evidence limit as Slides.
"""
from io import BytesIO
import posixpath
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .hosted_agenda import ConnectionError

MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
MAX_BYTES = 32_000_000
MAX_EXPANDED = 128_000_000
MAX_XML = 8_000_000
MAX_TEXT = 2_000_000
NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def extract(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise ConnectionError('assessment_source_too_large')
    try:
        with ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if (len(entries) > 10000 or len({e.filename for e in entries}) != len(entries)
                    or sum(e.file_size for e in entries) > MAX_EXPANDED
                    or any(e.flag_bits & 1 for e in entries)):
                raise ConnectionError('assessment_source_too_large')

            def xml(path):
                if archive.getinfo(path).file_size > MAX_XML:
                    raise ConnectionError('assessment_source_too_large')
                data = archive.read(path)
                # Forbid DTD/entity definitions, including UTF-16 XML forms.
                checked = data.replace(b'\x00', b'').upper()
                if b'<!DOCTYPE' in checked or b'<!ENTITY' in checked:
                    raise ValueError('DTD forbidden')
                return ET.fromstring(data)

            presentation = xml('ppt/presentation.xml')
            rels = {}
            for rel in xml('ppt/_rels/presentation.xml.rels'):
                rid, target = rel.get('Id'), rel.get('Target', '')
                if rid in rels:
                    raise ValueError('duplicate relationship')
                rels[rid] = (target, rel.get('TargetMode'), rel.get('Type', ''))
            slides = presentation.findall('p:sldIdLst/p:sldId', NS)
            if not slides or len(slides) > 1000:
                raise ValueError('invalid slide count')
            output, size, seen = [], 0, set()
            for slide in slides:
                target, mode, kind = rels[slide.get('{' + NS['r'] + '}id')]
                path = posixpath.normpath(posixpath.join('ppt', target))
                if (mode == 'External' or not kind.endswith('/slide')
                        or not path.startswith('ppt/slides/') or not path.endswith('.xml')
                        or path in seen or '\\' in target or ':' in target):
                    raise ValueError('invalid slide target')
                seen.add(path)
                root = xml(path)
                paragraphs = []
                for paragraph in root.findall('.//a:p', NS):
                    parts = []
                    for item in paragraph.iter():
                        if item.tag == '{' + NS['a'] + '}t':
                            parts.append(item.text or '')
                        elif item.tag == '{' + NS['a'] + '}br':
                            parts.append('\n')
                    paragraphs.append(''.join(parts))
                text = '\n'.join(paragraphs)
                size += len(text.encode('utf-8')) + 2
                if size > MAX_TEXT:
                    raise ConnectionError('assessment_source_too_large')
                output.append(text)
            result = '\n\n'.join(output)
            if not result.strip():
                raise ConnectionError('assessment_source_no_text')
            return result
    except ConnectionError:
        raise
    except (BadZipFile, KeyError, ValueError, ET.ParseError, RuntimeError, OSError):
        raise ConnectionError('assessment_pptx_invalid') from None
