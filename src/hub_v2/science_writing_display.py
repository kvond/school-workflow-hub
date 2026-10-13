"""Source-bound presentation of saved writing observations and teacher CER practice.

No assessment, source, grade, delivery, provider or storage writes. The practice
is supplied teaching material, never evidence of a student's completed work.
"""
from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape

from .native_feedback import Held, LEVELS, TEACHER_DIMENSIONS

SCHEMA = 'science-writing-display/v1'
KEYS = ('vocabulary', 'conceptual', 'evidence', 'reasoning')
LABELS = ('Science vocabulary', 'Conceptual explanation', 'Specific evidence', 'Scientific reasoning')


def _reviewed(slots):
    reviewed = [slot for slot in slots if slot['observations']]
    if not reviewed:
        raise Held('The writing report needs at least one actually reviewed source; submitted work alone is not an assessment.')
    return reviewed


def _before(left, right):
    return left['date'] < right['date'] or (left['moment'] and right['moment']
           and left['moment'] < right['moment'])


def _lesson_reference(saved, latest):
    refs = {r.get('reference_url') for r in (saved.get('provenance') or {}).get('source_observations', [])
            if r.get('source_file_id') == latest['source_file_id']
            and str(r.get('source_revision')) == latest['source_revision'] and r.get('reference_url')}
    for lesson in saved['lessons']:
        if (lesson.get('source_file_id') == latest['source_file_id']
                and str(lesson.get('source_revision')) == latest['source_revision'] and lesson.get('reference_url')):
            refs.add(lesson['reference_url'])
    imported = (saved.get('provenance') or {}).get('imported_source_evidence') or {}
    if (imported.get('source_file_id') == latest['source_file_id']
            and str(imported.get('source_revision')) == latest['source_revision']
            and (imported.get('teacher') or {}).get('id')):
        refs.add('https://drive.google.com/open?id=' + imported['teacher']['id'])
    if len(refs) != 1:
        raise Held('The CER needs the latest exact lesson reference from its verified source evidence.')
    return refs.pop()


def context(saved, cer, *, comparison=False):
    """Build the new presentation overlay; default to latest-work narration.

    The trusted author supplies reviewed lesson practice facts. This function
    never guesses a lesson from its title, constructs an assessment, or changes
    the old report. Its existing verified submission-time context is retained.
    """
    from copy import deepcopy
    from .feedback_pdf import _science_writing_history
    if type(comparison) is not bool:
        raise Held('Choose the initial comparison or subsequent latest-work presentation explicitly.')
    slots = _science_writing_history(saved)[0]
    latest = _reviewed(slots)[-1]
    if not isinstance(cer, dict) or set(cer) - {'question', 'evidence', 'reference_url', 'lesson_reference'}:
        raise Held('Supply the teacher-reviewed CER practice as a bounded evidence record.')
    result = deepcopy(saved.get('presentation_context') or {'original_submission_times': []})
    practice = deepcopy(cer)
    practice['lesson_reference'] = _lesson_reference(saved, latest)
    result['writing_display'] = {'schema': SCHEMA, 'mode': 'comparison' if comparison else 'latest',
        'latest_source': {'source_file_id': latest['source_file_id'], 'source_revision': latest['source_revision']},
        'cer': practice}
    validate({**saved, 'presentation_context': result}, slots)
    return result


def prepare_presentation(saved, cer, *, prepared_date, saved_at, comparison=False):
    """Author a new immutable presentation using the existing archive contract.

    This is the trusted author pipeline: choose the latest actual reviewed
    source, attach the teacher-reviewed lesson CER, render and hash its PDF.
    Existing saved assessment and older delivery versions remain unchanged.
    """
    from copy import deepcopy
    import hashlib
    import re
    from .feedback_pdf import render_science_writing, SCIENCE_WRITING_RENDER_VERSION
    from school_workflow_hub.core.runs import content_hash
    try:
        stamp = datetime.fromisoformat(saved_at.replace('Z', '+00:00'))
        valid = (stamp.tzinfo is not None and stamp.astimezone(ZoneInfo('America/New_York')).date().isoformat() == prepared_date
                 and datetime.strptime(prepared_date, '%Y-%m-%d').date().isoformat() == prepared_date)
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid or not re.fullmatch(r'feedbacksource_[a-f0-9]{64}', str(saved.get('id', ''))):
        raise Held('The writing presentation needs its actual preparation time and existing saved report identity.')
    version = saved.get('presentation_version', 1)
    if type(version) is not int or version < 1:
        raise Held('The existing writing presentation version is invalid.')
    value = deepcopy(saved)
    for name in ('pdf_path', 'pdf_bytes', 'pdf_sha256', 'id', 'report_source_sha256'):
        value.pop(name, None)
    value.update(prepared_at=prepared_date, saved_at=saved_at,
        presentation_version=version + 1,
        supersedes_report_id=saved['id'], supersession_previous_payload=deepcopy(saved))
    value['presentation_context'] = context(value, cer, comparison=comparison)
    basis = {k: v for k, v in value.items() if k != 'supersession_previous_payload'}
    value['report_source_sha256'] = content_hash([SCIENCE_WRITING_RENDER_VERSION, basis])
    value['id'] = 'feedbacksource_' + value['report_source_sha256']
    pdf = render_science_writing(value)
    value['pdf_sha256'] = hashlib.sha256(pdf).hexdigest()
    return value, pdf


def validate(saved, slots=None):
    from .feedback_pdf import _science_writing_history
    slots = slots if slots is not None else _science_writing_history(saved)[0]
    display = (saved.get('presentation_context') or {}).get('writing_display')
    if (not isinstance(display, dict) or set(display) != {'schema', 'mode', 'latest_source', 'cer'}
            or display['schema'] != SCHEMA or display['mode'] not in {'comparison', 'latest'}):
        raise Held('The writing presentation needs its supported narrative scope and teacher-provided CER.')
    reviewed = _reviewed(slots)
    latest = reviewed[-1]
    if any(not _before(previous, latest) for previous in reviewed[:-1]):
        raise Held('The latest writing source needs verified submission order; title or filename order is not enough.')
    if (display['mode'] == 'comparison' and len(reviewed) > 2
            and any(not _before(previous, reviewed[-2]) for previous in reviewed[:-2])):
        raise Held('The comparison needs a uniquely verified previous reviewed source.')
    if (not isinstance(display['latest_source'], dict)
            or display['latest_source'] != {'source_file_id': latest['source_file_id'],
                                            'source_revision': latest['source_revision']}
            or not latest['source_file_id']):
        raise Held('The writing practice must belong to the latest exact reviewed source revision.')
    cer = display['cer']
    if (not isinstance(cer, dict) or set(cer) != {'question', 'evidence', 'reference_url', 'lesson_reference'}
            or not isinstance(cer['question'], str) or not 1 <= len(cer['question']) <= 350
            or not isinstance(cer['evidence'], list) or len(cer['evidence']) != 3
            or any(not isinstance(item, str) or not 1 <= len(item) <= 300 for item in cer['evidence'])
            or len(set(cer['evidence'])) != 3
            or not isinstance(cer['reference_url'], str) or not cer['reference_url'].startswith('https://')
            or len(cer['reference_url']) > 1000
            or cer['lesson_reference'] != _lesson_reference(saved, latest)):
        raise Held('The CER needs one easy lesson question and exactly three distinct teacher-provided evidence statements.')
    for item in [cer['question'], cer['reference_url'], cer['lesson_reference'], *cer['evidence']]:
        if any(ord(char) < 32 for char in item):
            raise Held('The teacher-provided CER contains unsupported control characters.')
    rows = (saved.get('provenance') or {}).get('source_observations')
    if not rows:
        raise Held('The latest writing narrative needs the actual saved source-bound observations.')
    for row in rows:
        if (not isinstance(row.get('quote', ''), str) or len(row.get('quote', '').split()) > 25
                or not isinstance(row.get('explanation', ''), str) or len(row.get('explanation', '')) > 600
                or (row.get('level') in LEVELS[:4] and not row.get('explanation', '').strip())):
            raise Held('The writing narrative needs concise original quotations and dimension-specific explanations.')
    return display


def render(saved, slots, charts):
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    display = validate(saved, slots)
    raw = BytesIO(); c = canvas.Canvas(raw, pagesize=(612, 792), invariant=1)
    c.setTitle('Science writing and CER practice - ' + saved['student']['name'])
    c.setAuthor('School Workflow Hub')
    colors = {'Beginning': '#A52A42', 'Developing': '#936300', 'Proficient': '#127447',
              'Extending': '#2357B1', 'Evidence unavailable': '#526575', 'Not assessed': '#526575'}
    area_colors = ('#087EA4', '#8050A2', '#16836D', '#D36528')
    ink, muted = HexColor('#17384B'), HexColor('#526575')
    styles = {name: ParagraphStyle(name, fontName=font, fontSize=size, leading=leading, textColor=color)
              for name, font, size, leading, color in (
                  ('body', 'Helvetica', 9.5, 12, ink), ('small', 'Helvetica', 8, 10, muted),
                  ('heading', 'Helvetica-Bold', 11, 14, ink), ('name', 'Helvetica-Bold', 20, 24, ink))}
    def clean(value):
        for before, after in [('—', '-'), ('–', '-'), ('‑', '-'), ('’', "'"), ('“', '"'), ('”', '"')]:
            value = str(value).replace(before, after)
        return escape(str(value))
    def p(value, y, kind='body', x=44, width=524, floor=44, rich=False):
        para = Paragraph(value if rich else clean(value), styles[kind]); _, height = para.wrap(width, 760)
        if y - height < floor:
            raise Held('The writing presentation needs shorter supplied text; no observations or practice are dropped.')
        para.drawOn(c, x, y - height); return y - height
    graph_chunks = [list(range(max(0, start - 1), min(start + 8, len(slots))))
                    for start in range(0, len(slots), 8)]
    total_pages = 2 + len(graph_chunks)
    def header(subtitle):
        y = p(saved['student']['section'] + ' / SCIENCE WRITING / Prepared ' + saved['prepared_at'], 758, 'small')
        y = p(saved['student']['name'], y - 9, 'name')
        return p(subtitle, y - 9, 'heading')
    def footer(page):
        c.setFillColor(muted); c.setFont('Helvetica', 7.5)
        c.drawString(44, 24, 'Saved writing observations, not a grade or a completed CER assessment.')
        c.drawRightString(568, 24, '%d of %d' % (page, total_pages)); c.showPage()
    rows = {(r['source_file_id'], str(r['source_revision']), r['dimension']): r
            for r in saved['provenance']['source_observations']}
    reviewed = _reviewed(slots)
    narrative = reviewed[-2:] if display['mode'] == 'comparison' else reviewed[-1:]
    y = header('What your reviewed work shows')
    y = p('The colored levels describe each piece of writing. A change between tasks is not a diagnosis of your ability.', y - 8, 'small')
    for index, (key, label, dimension) in enumerate(zip(KEYS, LABELS, TEACHER_DIMENSIONS)):
        y = p(label, y - 12, 'heading')
        levels = []
        for slot in narrative:
            row = rows.get((slot['source_file_id'], slot['source_revision'], dimension))
            level = row['level'] if row else 'Not assessed'; levels.append(level)
            source_no = slots.index(slot) + 1
            line = '<b>S%d %s</b> - <font color="%s">%s</font>' % (
                source_no, clean(slot['lesson']), colors[level], clean(level))
            y = p(line, y - 3, 'small', rich=True)
            if not row or level in {'Not assessed', 'Evidence unavailable'}:
                y = p(row.get('explanation') or 'No attributable observation was saved for this area.' if row else
                      'No attributable observation was saved for this area.', y - 2)
            else:
                location = row.get('source_location') or ('slide %s' % row['slide'] if row.get('slide') else row.get('question_id', ''))
                excerpt = row['quote'] + ('' if row['quote'].rstrip().endswith(('.', '?', '!')) else ' ...')
                y = p('Your words' + (' (' + location + ')' if location else '') + ': "' + excerpt + '"', y - 2)
                y = p(row['explanation'], y - 2)
        if len(levels) == 2:
            first, last = levels
            if first in LEVELS[:4] and last in LEVELS[:4]:
                word = 'lower' if LEVELS.index(last) < LEVELS.index(first) else 'higher' if LEVELS.index(last) > LEVELS.index(first) else 'the same'
                y = p('The later sample is %s: %s to %s. The explanations above identify the difference in the work.' % (
                    word, first, last), y - 3, 'small')
    footer(1)
    for page_index, indices in enumerate(graph_chunks, 2):
        y = header('Every reviewed submission - four writing areas')
        y = p('S = one exact submitted source revision. First responses and revisions in one file are not separate time points.', y - 8, 'small')
        for sample_index in indices:
            slot = slots[sample_index]
            stamp = slot['moment'].astimezone(__import__('zoneinfo').ZoneInfo('America/New_York')).strftime('%H:%M:%S') if slot['moment'] else 'time unavailable'
            y = p('S%d | %s | %s %s' % (sample_index + 1, slot['lesson'], slot['date'], stamp), y - 2, 'small')
        if page_index > 2:
            y = p('The first point repeats the previous page endpoint to keep the line connected.', y - 3, 'small')
        chart_top = min(y - 24, 640)
        for index, chart in enumerate(charts):
            x, top = 44 + (index % 2) * 270, chart_top - (index // 2) * 245
            color = HexColor(area_colors[index]); left, right, bottom, plot_top = x + 47, x + 244, top - 179, top - 30
            c.setFillColor(color); c.setFont('Helvetica-Bold', 10); c.drawString(x, top, LABELS[index])
            for level_index, level in enumerate(LEVELS[:4], 1):
                yy = bottom + (plot_top - bottom) * (level_index - 1) / 3
                c.setStrokeColor(HexColor('#DCE5EA')); c.setLineWidth(.5); c.line(left, yy, right, yy)
                c.setFillColor(HexColor(colors[level])); c.setFont('Helvetica', 7.2); c.drawRightString(left - 4, yy - 2, level)
            positions = {value: left + (right - left) * i / max(1, len(indices) - 1) for i, value in enumerate(indices)}
            if len(indices) == 1:
                positions[indices[0]] = (left + right) / 2
            def point(i):
                return positions[i], bottom + (plot_top - bottom) * (chart['points'][i] - 1) / 3
            c.setStrokeColor(color); c.setLineWidth(1.7)
            for start, end in chart['segments']:
                if start in positions and end in positions:
                    c.line(*point(start), *point(end))
            for sample_index in indices:
                c.setFillColor(color)
                if chart['points'][sample_index] is not None:
                    c.circle(*point(sample_index), 3.3, stroke=0, fill=1)
                else:
                    c.setFont('Helvetica', 6.5); c.drawCentredString(positions[sample_index], bottom + 7, 'unassessed')
                c.setFillColor(muted); c.setFont('Helvetica', 7)
                c.drawCentredString(positions[sample_index], bottom - 14, 'S%d' % (sample_index + 1))
            p('Missing observations remain unassessed. Same-day samples connect only with verified times.', bottom - 26,
              'small', x=x, width=245, floor=40)
        footer(page_index)
    y = header('CER practice - claim, evidence, reasoning')
    latest = reviewed[-1]
    y = p('Based on your latest reviewed lesson: ' + latest['lesson'], y - 8, 'small')
    y = p(display['cer']['question'], y - 16, 'heading')
    y = p('Use these three given pieces of evidence. They are practice information, not words attributed to you.', y - 10)
    for index, statement in enumerate(display['cer']['evidence'], 1):
        y = p('%d. %s' % (index, statement), y - 8)
    c.linkURL(display['cer']['reference_url'], (44, y - 14, 230, y - 2), relative=0, thickness=0)
    y = p('Practice science reference', y - 6, 'small')
    y = p('Claim: Answer the question in one clear sentence.', y - 17, 'heading')
    c.setStrokeColor(HexColor('#B8C9D3')); c.setLineWidth(.6)
    def lines(y, count):
        for _ in range(count):
            y -= 22
            if y < 55:
                raise Held('The CER needs shorter supplied evidence to preserve writing space.')
            c.line(44, y, 568, y)
        return y
    y = lines(y, 2)
    y = p('Evidence: Use the three facts that support your claim.', y - 16, 'heading'); y = lines(y, 3)
    y = p('Reasoning: Explain how each fact connects to your claim. Use the science idea and because.', y - 16, 'heading'); y = lines(y, 5)
    p('Write this yourself and hand this page back. Your paper CER is not assessed in this report.', y - 17, 'small')
    footer(total_pages); c.save(); return raw.getvalue()
