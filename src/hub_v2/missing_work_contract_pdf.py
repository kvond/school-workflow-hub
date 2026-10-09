"""A student's current source checks and supportive writing plan, without scoring."""
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from .missing_work_contracts import SCHEMA
from .native_feedback import Held


def render(contracts):
    """Combine one student's verified absent lesson checks on one printable page."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Paragraph

    if not isinstance(contracts, list) or not 1 <= len(contracts) <= 8:
        raise Held('Choose one student and a bounded set of current work checks.')
    first = contracts[0]
    person, day, cls = first.get('student') or {}, first.get('prepared_at'), first.get('class_key')
    identity = (person.get('student_key'), person.get('ic_student_id'), person.get('name'), person.get('section'))
    if not identity[2] or not identity[3] or not (identity[0] or identity[1]):
        raise Held('The work contract needs one verified current student and class.')
    try:
        if datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day:
            raise ValueError()
    except (TypeError, ValueError):
        raise Held('The work contract needs its actual check date.') from None
    keys = set()
    for item in contracts:
        student = item.get('student') or {}
        actual = (student.get('student_key'), student.get('ic_student_id'), student.get('name'), student.get('section'))
        if (item.get('schema') != SCHEMA or item.get('report_type') != 'missing_work_contract'
                or item.get('status') != 'open' or item.get('grade_change') is not False
                or item.get('delivery_changed') is not False or actual != identity
                or item.get('prepared_at') != day or item.get('class_key') != cls
                or not item.get('assignment_title') or not item.get('original_due_date')
                or not item.get('source_proof') or not item.get('contract_key')
                or item['contract_key'] in keys):
            raise Held('Each work-contract line needs the same current student and verified lesson evidence.')
        keys.add(item['contract_key'])
    stream = BytesIO()
    c = canvas.Canvas(stream, pagesize=(612, 792), invariant=1)
    c.setTitle('Current work check and plan - ' + person['name'])
    c.setAuthor('School Workflow Hub')
    ink, muted, blue = map(HexColor, ('#17384B', '#526575', '#087EA4'))
    styles = {
        'name': ParagraphStyle('contract-name', fontName='Helvetica-Bold', fontSize=21, leading=25, textColor=ink),
        'heading': ParagraphStyle('contract-heading', fontName='Helvetica-Bold', fontSize=12, leading=16, textColor=blue),
        'body': ParagraphStyle('contract-body', fontName='Helvetica', fontSize=10.5, leading=14, textColor=ink),
        'small': ParagraphStyle('contract-small', fontName='Helvetica', fontSize=8.5, leading=11, textColor=muted),
    }

    def para(text, top, style='body'):
        text = str(text).replace('—', '-').replace('–', '-').replace('’', "'")
        p = Paragraph(escape(text), styles[style]); _, height = p.wrap(524, 720)
        if top - height < 48:
            raise Held('This contract needs fewer lesson lines to stay readable on one page.')
        p.drawOn(c, 44, top - height)
        return top - height

    y = para(person['section'] + ' / CURRENT WORK CHECK / ' + day, 758, 'small')
    y = para(person['name'], y - 13, 'name')
    y = para('Work not located as of ' + day, y - 14, 'heading')
    y = para('I want to give you useful feedback on your thinking. I could not locate submitted work '
             'for the lessons below in the sources checked today. If you submitted another way, '
             'please show me where to find it. Your ideas matter.', y - 8)
    for item in sorted(contracts, key=lambda x: (x['original_due_date'], x['assignment_title'])):
        y = para(item['assignment_title'] + ' | recorded due date: ' + item['original_due_date'], y - 9)
    y = para(first['note'], y - 12, 'small')
    y = para('My plan to show my thinking', y - 24, 'heading')
    prompts = [('My next step', 2), ('When I plan to do it', 1),
               ('Help I need, or where I already submitted it', 2)]
    c.setStrokeColor(HexColor('#B8C9D3')); c.setLineWidth(.6)
    for label, lines in prompts:
        y = para(label, y - 14)
        for _ in range(lines):
            y -= 25
            if y < 96:
                raise Held('This contract needs fewer lesson lines to preserve writing space.')
            c.line(44, y, 568, y)
    y = para('Student signature: ________________________  Date: _________________', y - 29)
    para('Bring this page back so we can choose a useful next step together.', y - 15, 'small')
    c.setFillColor(muted); c.setFont('Helvetica', 7.5)
    c.drawString(44, 24, 'Check date: ' + day + '. Existing extensions, deadlines and grades are unchanged.')
    c.showPage(); c.save()
    return stream.getvalue()
