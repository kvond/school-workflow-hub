"""One-page student report. Pure rendering; no model, storage or grade writes."""
from io import BytesIO
from datetime import datetime
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from .native_feedback import Held, LEVELS

HAND_BACK = 'Write this yourself, then hand this page back to me. This is the part I read.'


def render_science_writing(saved):
    """A separate student writing report from already authored, source-bound observations."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    if (saved.get('schema') != 'science-writing/v1' or saved.get('report_type') != 'science_writing'
            or not isinstance(saved.get('lessons'), list) or not 1 <= len(saved['lessons']) <= 3
            or len(saved.get('dimensions', [])) != 4
            or {item.get('key') for item in saved['dimensions']} != {'vocabulary', 'conceptual', 'evidence', 'reasoning'}):
        raise Held('The science-writing report needs four distinct supported writing observations and its source lessons.')
    prepared = saved.get('prepared_at', '')
    try:
        datetime.strptime(prepared, '%Y-%m-%d')
    except (ValueError, TypeError):
        raise Held('The science-writing report needs its actual preparation date.') from None
    stream = BytesIO(); c = canvas.Canvas(stream, pagesize=(612, 792), invariant=1)
    c.setTitle('Science writing feedback - ' + saved['student']['name'])
    c.setAuthor('School Workflow Hub')
    ink, muted = HexColor('#17384B'), HexColor('#526575')
    styles = {
        'body': ParagraphStyle('writing-body', fontName='Helvetica', fontSize=10.5, leading=14, textColor=ink),
        'small': ParagraphStyle('writing-small', fontName='Helvetica', fontSize=8.5, leading=11, textColor=muted),
        'heading': ParagraphStyle('writing-heading', fontName='Helvetica-Bold', fontSize=11, leading=14, textColor=ink),
        'name': ParagraphStyle('writing-name', fontName='Helvetica-Bold', fontSize=21, leading=25, textColor=ink),
    }
    def text(value):
        value = str(value)
        for old, new in [('—', '-'), ('–', '-'), ('‑', '-'), ('’', "'"), ('“', '"'), ('”', '"')]:
            value = value.replace(old, new)
        return escape(value)
    def paragraph(value, y, style='body'):
        p = Paragraph(text(value), styles[style]); _, height = p.wrap(524, 740)
        if y - height < 48:
            raise Held('The science-writing report needs shorter observations to fit one page without dropping evidence.')
        p.drawOn(c, 44, y - height); return y - height
    y = paragraph(saved['student']['section'] + ' / SCIENCE WRITING / ' + prepared, 758, 'small')
    y = paragraph(saved['student']['name'], y - 12, 'name')
    y = paragraph('Science writing observed in your submitted work, and one next step.', y - 8, 'small')
    y = paragraph('Work reviewed', y - 18, 'heading')
    for lesson in saved['lessons']:
        y = paragraph(lesson['title'] + ' | source ' + lesson['source_date'], y - 3, 'small')
    for dimension in saved['dimensions']:
        if (dimension.get('level') not in LEVELS or not isinstance(dimension.get('example'), str)
                or len(dimension['example'].split()) > 25
                or (not dimension['example'].strip() and dimension['level'] != 'Evidence unavailable')):
            raise Held('The writing report needs an observed level and a short exact example for each dimension.')
        y = paragraph(dimension['label'] + ' - ' + dimension['level'], y - 20, 'heading')
        context = dimension['lesson'] + ' | ' + dimension['source_date']
        if dimension.get('question_id'):
            context += ' | ' + dimension['question_id']
        y = paragraph(context, y - 3, 'small')
        y = paragraph('From your submitted work: "' + dimension['example'] + '"' if dimension['example'].strip()
                      else 'No attributable science explanation in the located work.', y - 5)
        y = paragraph(dimension['explanation'], y - 4)
    y = paragraph(saved['target']['title'], y - 23, 'heading')
    y = paragraph(saved['target']['text'], y - 5)
    paragraph(saved['note'], y - 18, 'small')
    c.setFont('Helvetica', 7.5); c.setFillColor(muted)
    c.drawRightString(568, 24, '1'); c.showPage(); c.save()
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
