"""Student PDF rendering only; no model, storage, delivery-state or grade writes."""
from io import BytesIO
from datetime import datetime
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from .native_feedback import Held, LEVELS, TEACHER_DIMENSIONS

HAND_BACK = 'Write this yourself, then hand this page back to me. This is the part I read.'
SCIENCE_WRITING_RENDER_VERSION = 'science-writing-pdf/v3'
MAX_WRITING_SAMPLES = 125
WRITING_KEYS = ('vocabulary', 'conceptual', 'evidence', 'reasoning')
WRITING_CHART_LABELS = ('Vocabulary', 'Conceptual answers', 'Specific evidence', 'Reasoning')


def _science_writing_history(saved):
    """Source-bound observations, with unavailable samples kept as gaps.

    Dates describe submitted work, not an earlier assessment or a growth score.
    An optional original submission timestamp can order same-day samples only
    when it agrees with the source date. Without that evidence, no line joins
    two same-day lessons. Older payloads supply only their selected examples.
    """
    if (not isinstance(saved.get('lessons'), list)
            or not 1 <= len(saved['lessons']) <= MAX_WRITING_SAMPLES):
        raise Held('The writing charts need a bounded list of every verified submitted source.')
    prepared = saved.get('prepared_at', '')
    try:
        if datetime.strptime(prepared, '%Y-%m-%d').date().isoformat() != prepared:
            raise ValueError()
    except (ValueError, TypeError):
        raise Held('The science-writing report needs its actual preparation date.') from None
    context = saved.get('presentation_context') or {}
    if (not isinstance(context, dict) or set(context) - {'original_submission_times', 'writing_display'}):
        raise Held('The writing charts need only their verified original submission-time context.')
    events = context.get('original_submission_times', []) if isinstance(context, dict) else None
    if not isinstance(events, list) or len(events) > MAX_WRITING_SAMPLES:
        raise Held('The writing charts need bounded original submission-time evidence.')
    source_times = {}
    for event in events:
        if not isinstance(event, dict) or set(event) != {'source_file_id', 'source_revision', 'submitted_at'}:
            raise Held('A writing-chart submission time needs its exact original source and revision.')
        source = (event['source_file_id'], str(event['source_revision']))
        if not source[0] or not source[1] or source in source_times:
            raise Held('A writing-chart submission time has a duplicated or missing source identity.')
        source_times[source] = event['submitted_at']
    slots, by_source, by_title = [], {}, {}
    for lesson in saved['lessons']:
        day = lesson.get('source_date', '')
        try:
            date = datetime.strptime(day, '%Y-%m-%d')
            if date.date().isoformat() != day or day > prepared or not str(lesson.get('title', '')).strip():
                raise ValueError()
            source = (lesson.get('source_file_id'), str(lesson.get('source_revision', '')))
            timestamp = source_times.get(source, lesson.get('submitted_at'))
            if lesson.get('submitted_at') and timestamp != lesson['submitted_at']:
                raise ValueError()
            moment = datetime.fromisoformat(timestamp.replace('Z', '+00:00')) if timestamp else None
            if moment is not None and (moment.tzinfo is None or
                    moment.astimezone(ZoneInfo('America/New_York')).date().isoformat() != day):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise Held('A writing-chart sample needs its verified original work date and submission time.') from None
        title_key = (lesson['title'], day)
        if source[0] and source in by_source:
            raise Held('Two writing-chart samples describe the same source revision.')
        slot = {'lesson': lesson['title'], 'date': day, 'moment': moment,
                'source_file_id': source[0], 'source_revision': source[1],
                'date_sort': date, 'observations': {}}
        slots.append(slot); by_title.setdefault(title_key, []).append(slot)
        if source[0]:
            by_source[source] = slot
    if any(source not in by_source for source in source_times):
        raise Held('A writing-chart submission time does not belong to a listed original source revision.')
    rows = (saved.get('provenance') or {}).get('source_observations')
    if rows is not None:
        if not isinstance(rows, list) or len(rows) > MAX_WRITING_SAMPLES * 4 or not rows:
            raise Held('The writing charts need a bounded set of source-bound observations.')
        canonical = dict(zip(TEACHER_DIMENSIONS, WRITING_KEYS))
        for row in rows:
            if not isinstance(row, dict):
                raise Held('A writing-chart observation needs a source-bound record.')
            source = (row.get('source_file_id'), str(row.get('source_revision', '')))
            slot, key = by_source.get(source), canonical.get(row.get('dimension'))
            student_key = saved['student'].get('student_key')
            if (slot is None or key is None or row.get('date') != slot['date']
                    or row.get('lesson') != slot['lesson']
                    or row.get('section', saved['student'].get('section')) != saved['student'].get('section')
                    or (student_key and row.get('student_key') != student_key)):
                raise Held('A writing-chart observation does not match this student and original source revision.')
            level = row.get('level')
            if level not in LEVELS and level != 'Not assessed':
                raise Held('A writing-chart observation has an unsupported understanding level.')
            if level in LEVELS[:4] and not str(row.get('quote', '')).strip():
                raise Held('A rated writing-chart observation needs its actual source example.')
            if key in slot['observations']:
                raise Held('Conflicting writing-chart observations need review; levels are never averaged.')
            slot['observations'][key] = level
    else:
        for dimension in saved['dimensions']:
            matches = by_title.get((dimension.get('lesson'), dimension.get('source_date')), [])
            if len(matches) != 1:
                raise Held('A selected writing example does not match a listed source lesson and date.')
            slot = matches[0]
            slot['observations'][dimension['key']] = dimension['level']
    # Precise original timestamps order same-day work. A date-only tie stays
    # explicit and unconnected, rather than turning a filename sort into growth.
    slots.sort(key=lambda s: (s['date'], s['moment'].timestamp() if s['moment'] else float('-inf'), s['lesson']))
    charts = []
    for key, label in zip(WRITING_KEYS, WRITING_CHART_LABELS):
        points = [LEVELS.index(s['observations'][key]) + 1 if s['observations'].get(key) in LEVELS[:4]
                  else None for s in slots]
        segments = []
        for index in range(1, len(slots)):
            left, right = slots[index - 1], slots[index]
            ordered = left['date'] < right['date'] or (left['moment'] and right['moment']
                      and left['moment'] < right['moment'])
            if points[index - 1] is not None and points[index] is not None and ordered:
                segments.append((index - 1, index))
        count = sum(p is not None for p in points)
        charts.append({'key': key, 'label': label, 'points': points, 'segments': segments,
                       'caption': 'No rated observations. No trend.' if not count else
                                  'One rated observation. No trend.' if count == 1 else
                                  '%d rated work samples.' % count})
    return slots, charts


def render_science_writing(saved):
    """Two pages: colored evidence/charts, then the student's own writing challenge."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    if (saved.get('schema') != 'science-writing/v1' or saved.get('report_type') != 'science_writing'
            or not isinstance(saved.get('lessons'), list) or not 1 <= len(saved['lessons']) <= MAX_WRITING_SAMPLES
            or len(saved.get('dimensions', [])) != 4
            or {item.get('key') for item in saved['dimensions']} != set(WRITING_KEYS)):
        raise Held('The science-writing report needs four distinct supported writing observations and its source lessons.')
    for dimension in saved['dimensions']:
        if (dimension.get('level') not in LEVELS or not isinstance(dimension.get('example'), str)
                or len(dimension['example'].split()) > 25
                or (not dimension['example'].strip() and dimension['level'] != 'Evidence unavailable')):
            raise Held('The writing report needs an observed level and a short exact example for each dimension.')
    slots, charts = _science_writing_history(saved)
    if (saved.get('presentation_context') or {}).get('writing_display') is not None:
        from .science_writing_display import render
        return render(saved, slots, charts)
    prepared = saved['prepared_at']
    stream = BytesIO(); c = canvas.Canvas(stream, pagesize=(612, 792), invariant=1)
    c.setTitle('Science writing feedback - ' + saved['student']['name'])
    c.setAuthor('School Workflow Hub')
    ink, muted = HexColor('#17384B'), HexColor('#526575')
    styles = {
        'body': ParagraphStyle('writing-body', fontName='Helvetica', fontSize=9.5, leading=12, textColor=ink),
        'small': ParagraphStyle('writing-small', fontName='Helvetica', fontSize=8.5, leading=11, textColor=muted),
        'heading': ParagraphStyle('writing-heading', fontName='Helvetica-Bold', fontSize=10.5, leading=13, textColor=ink),
        'name': ParagraphStyle('writing-name', fontName='Helvetica-Bold', fontSize=21, leading=25, textColor=ink),
    }
    def text(value):
        value = str(value)
        for old, new in [('—', '-'), ('–', '-'), ('‑', '-'), ('’', "'"), ('“', '"'), ('”', '"')]:
            value = value.replace(old, new)
        return escape(value)
    def paragraph(value, y, style='body', x=44, width=524, floor=48):
        p = Paragraph(text(value), styles[style]); _, height = p.wrap(width, 740)
        if y - height < floor:
            raise Held('The science-writing report needs shorter observations to fit two pages without dropping evidence.')
        p.drawOn(c, x, y - height); return y - height
    levels = {
        'Beginning': ('#9D2235', '#FCE5EA'), 'Developing': ('#795200', '#FFF1C9'),
        'Proficient': ('#146C43', '#DDF3E7'), 'Extending': ('#1D54A8', '#E3ECFC'),
        'Evidence unavailable': ('#526575', '#EBEFF2'),
    }
    area_colors = [HexColor(color) for color in ('#087EA4', '#8050A2', '#16836D', '#D36528')]
    def chart(slot_values, model, x, top, color):
        width, height = 211, 119
        c.setFillColor(color); c.setFont('Helvetica-Bold', 9); c.drawString(x, top, model['label'])
        c.setFillColor(muted); c.setFont('Helvetica', 7.5); c.drawString(x, top - 12, model['caption'])
        plot_left, plot_right = x + 43, x + width - 6
        plot_bottom, plot_top = top - height + 32, top - 30
        positions = [plot_left + (plot_right - plot_left) * i / max(1, len(slot_values) - 1)
                     for i in range(len(slot_values))]
        if len(slot_values) == 1:
            positions[0] = (plot_left + plot_right) / 2
        if any(point is not None for point in model['points']):
            for level_index, level in enumerate(LEVELS[:4], 1):
                yy = plot_bottom + (plot_top - plot_bottom) * (level_index - 1) / 3
                c.setStrokeColor(HexColor('#DFE6EA')); c.setLineWidth(.4); c.line(plot_left, yy, plot_right, yy)
                c.setFillColor(HexColor(levels[level][0])); c.setFont('Helvetica', 7.3)
                c.drawRightString(plot_left - 4, yy - 2, level)
            def point(index):
                return positions[index], plot_bottom + (plot_top - plot_bottom) * (model['points'][index] - 1) / 3
            c.setStrokeColor(color); c.setLineWidth(1.7)
            for left, right in model['segments']:
                c.line(*point(left), *point(right))
            for index, value in enumerate(model['points']):
                if value is not None:
                    c.setFillColor(color); c.circle(*point(index), 3.2, stroke=0, fill=1)
        for index, slot in enumerate(slot_values):
            c.setFillColor(muted); c.setFont('Helvetica', 7.3)
            c.drawCentredString(positions[index], plot_bottom - 13, 'L%d %s' % (index + 1, slot['date'][5:]))
            if model['points'][index] is None:
                c.drawCentredString(positions[index], plot_bottom + 7, 'unassessed')
        c.setFont('Helvetica', 7.3); c.setFillColor(muted)
        c.drawString(x, top - height + 4, 'Gaps stay unassessed; no level is invented.')
    y = paragraph(saved['student']['section'] + ' / SCIENCE WRITING / ' + prepared, 758, 'small')
    y = paragraph(saved['student']['name'], y - 12, 'name')
    y = paragraph('Writing observations across your submitted work. Your own challenge is on page 2.', y - 8, 'small')
    y = paragraph('Work reviewed', y - 14, 'heading')
    for index, slot in enumerate(slots):
        y = paragraph('L%d  %s | source %s' % (index + 1, slot['lesson'], slot['date']), y - 2, 'small')
    y = paragraph('Different lessons are different tasks. These observations do not establish a growth score.', y - 5, 'small')
    first_top = y - 24
    by_key = {dimension['key']: dimension for dimension in saved['dimensions']}
    for index, model in enumerate(charts):
        dimension = by_key[model['key']]; top = first_top - index * 130; floor = top - 124
        heading = Paragraph(text(dimension['label']) + ' - <font color="' + levels[dimension['level']][0]
                            + '">' + text(dimension['level']) + '</font>', styles['heading'])
        _, heading_height = heading.wrap(296, 740)
        y = top - heading_height
        if y < floor:
            raise Held('The science-writing report needs shorter observations to fit two pages without dropping evidence.')
        heading.drawOn(c, 44, y)
        context = dimension['lesson'] + ' | ' + dimension['source_date']
        if dimension.get('source_location'):
            context += ' | ' + dimension['source_location']
        elif dimension.get('question_id'):
            context += ' | ' + dimension['question_id']
        y = paragraph(context, y - 3, 'small', width=296, floor=floor)
        y = paragraph('From your submitted work: "' + dimension['example'] + '"' if dimension['example'].strip()
                      else 'No attributable science explanation in the located work.', y - 4, width=296, floor=floor)
        paragraph(dimension['explanation'], y - 3, width=296, floor=floor)
        chart(slots, model, 357, top - 3, area_colors[index])
    if first_top - 3 * 130 - 124 < 38:
        raise Held('The science-writing report needs shorter observations to fit two pages without dropping evidence.')
    c.setFont('Helvetica', 7.5); c.setFillColor(muted)
    c.drawString(44, 24, 'Source dates identify the work. Prepared ' + prepared + '.'); c.drawRightString(568, 24, '1 of 2')
    c.showPage()
    y = paragraph(saved['student']['section'] + ' / SCIENCE WRITING / ' + prepared, 758, 'small')
    y = paragraph(saved['student']['name'], y - 12, 'name')
    y = paragraph('Your writing challenge', y - 21, 'heading')
    y = paragraph(saved['target']['title'], y - 10, 'heading')
    y = paragraph(saved['target']['text'], y - 7)
    y = paragraph('Try your challenge in your own words. Use a specific example or observation, then explain why it supports your answer. Bring this page back so we can discuss your thinking.', y - 15)
    y = paragraph('My explanation', y - 20, 'heading')
    c.setStrokeColor(HexColor('#B8C9D3')); c.setLineWidth(.6)
    rendered_note = 'Page 1 contains your writing examples. ' + saved['note']
    note = Paragraph(text(rendered_note), styles['small']); _, note_height = note.wrap(524, 740)
    line_count = max(0, min(17, int((y - 48 - note_height - 100) / 24)))
    if line_count < 10:
        raise Held('The writing challenge needs shorter text to fit two pages without dropping evidence or writing lines.')
    for _ in range(line_count):
        y -= 24; c.line(44, y, 568, y)
    y = paragraph('One question I want to discuss', y - 23, 'heading')
    for _ in range(2):
        y -= 23; c.line(44, y, 568, y)
    paragraph(rendered_note, y - 18, 'small')
    c.setFont('Helvetica', 7.5); c.setFillColor(muted)
    c.drawRightString(568, 24, '2 of 2'); c.showPage(); c.save()
    return stream.getvalue()


def render(saved):
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph

    stream = BytesIO()
    c = canvas.Canvas(stream, pagesize=(612, 792), invariant=1)
    c.setTitle('Feedback draft - ' + saved['person']['name'])
    c.setAuthor('School Workflow Hub')
    ink, grey, light = [HexColor(x) for x in ('#17384B', '#526575', '#DCE5EB')]
    left, right = 44, 568
    styles = {
        'body': ParagraphStyle('body', fontName='Helvetica', fontSize=10.5, leading=14, textColor=ink),
        'small': ParagraphStyle('small', fontName='Helvetica', fontSize=8.5, leading=11, textColor=grey),
        'aspect': ParagraphStyle('aspect', fontName='Helvetica', fontSize=9, leading=11, textColor=ink),
    }

    def plain(value):
        for old, new in [('—', '-'), ('–', '-'), ('‑', '-'), ('’', "'"), ('“', '"'), ('”', '"')]:
            value = value.replace(old, new)
        return value

    def para(value, y, style='body', x=left, width=524):
        p = Paragraph(escape(plain(value)), styles[style])
        _, height = p.wrap(width, 760)
        p.drawOn(c, x, y - height)
        return y - height

    def heading(value, y):
        c.setFillColor(ink)
        c.setFont('Helvetica-Bold', 11)
        c.drawString(left, y, value)
        return y - 10

    def lines(y, count=4):
        c.setStrokeColor(light)
        c.setLineWidth(.6)
        for _ in range(count):
            y -= 22
            c.line(left, y, right, y)
        return y

    c.setFillColor(grey)
    c.setFont('Helvetica-Bold', 8)
    c.drawString(left, 759, plain(saved['person']['section'].upper()) + ' / FEEDBACK DRAFT - TEACHER REVIEW')
    c.setFillColor(ink)
    c.setFont('Helvetica-Bold', 22)
    c.drawString(left, 727, plain(saved['person']['name']))
    created = datetime.fromisoformat(saved['created_at'].replace('Z', '+00:00'))
    prepared = (created.astimezone(ZoneInfo('America/New_York')).date().isoformat()
                if created.tzinfo else created.date().isoformat())
    y = para(saved['assignment']['assignment_title'] + ' | ' + prepared, 715, 'small')
    y = heading('1  YOUR GROWTH', y - 24)
    y = para('Your current understanding in this lesson. A single report does not establish a growth trend.', y - 2, 'small')
    xs = [355, 413, 471, 529]
    for label, x in zip(LEVELS[:4], xs):
        c.setFont('Helvetica', 7)
        c.setFillColor(grey)
        c.drawCentredString(x, y - 17, label)
    y -= 32
    for aspect in saved['report']['aspects']:
        bottom = para(aspect['name'], y + 7, 'aspect', width=286)
        c.setStrokeColor(light)
        c.line(xs[0], y, xs[-1], y)
        for x in xs:
            c.setFillColor(light)
            c.circle(x, y, 2, fill=1, stroke=0)
        if aspect['current_level'] in LEVELS[:4]:
            c.setFillColor(ink)
            c.circle(xs[LEVELS.index(aspect['current_level'])], y, 4.5, fill=1, stroke=0)
        else:
            para('Evidence unavailable', y + 6, 'small', x=350, width=174)
        y = min(y - 27, bottom - 12)
    y = heading('2  HOW YOU DID ON THIS ASSIGNMENT', y - 6)
    y = para(saved['report']['student_summary'], y - 2)
    y = heading('3  ONE QUESTION TO THINK ABOUT', y - 24)
    y = para(saved['report']['question'], y - 2)
    y = lines(y - 3)
    y = heading("4  WHAT I UNDERSTAND NOW THAT I DIDN'T BEFORE", y - 26)
    y = para(HAND_BACK, y - 2, 'small')
    y = lines(y - 2)
    y = heading('5  HOW I CAN IMPROVE MY RESPONSES NEXT TIME', y - 26)
    y = lines(y)
    if y < 45:
        raise Held('The draft needs shorter text to fit one page while preserving the writing space.')
    c.setFont('Helvetica', 7.5)
    c.setFillColor(grey)
    c.drawString(left, 24, 'For teacher review. Conceptual feedback is not a grade.')
    c.drawRightString(right, 24, '1')
    c.showPage()
    c.save()
    return stream.getvalue()
