"""Read-only exact-assignment teacher holds in the existing private CAS store.

A marked linked source never clears a teacher's known unmarked-copy hold.
Only a trusted host operator may persist a separately verified clearance; no
browser endpoint or grading request writes these records.
"""
import re
from . import work
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import StoreUnavailable, instant, key

CONFIG = 'HUB_COMPLETION_READINESS_HOLDS'
SCHEMA = 'hub-completion-teacher-hold/v1'


def hold_key(agenda_id, school_year, class_key, week, form_id):
    return key('completionreadinesshold', [agenda_id, school_year, class_key, week, form_id])


def _text(value, limit):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and all(ord(c) >= 32 for c in value)


def _cleared(value, *, allow_found_codes=False):
    proof = value.get('clearance')
    bases = {'teacher_confirmed_distributed_copies_coded'}
    if allow_found_codes:
        bases.add('teacher_confirmed_count_found_codes')
    if (not isinstance(proof, dict) or set(proof) != {'basis', 'teacher_confirmed_at', 'verified_at', 'copies'}
            or proof['basis'] not in bases
            or not isinstance(proof['copies'], list) or not 1 <= len(proof['copies']) <= 500):
        return False
    instant(proof['teacher_confirmed_at']); instant(proof['verified_at'])
    seen = set()
    for item in proof['copies']:
        if (not isinstance(item, dict) or set(item) != {'file_id', 'version', 'status'}
                or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', str(item.get('file_id') or ''))
                or not _text(item.get('version'), 100) or item.get('status') != 'READY'
                or item['file_id'] in seen):
            return False
        seen.add(item['file_id'])
    return True


def check(config, agenda, class_key, week, form_id):
    """No state/source writes. A configured missing or invalid decision holds."""
    out = {'status': 'READY', 'label': 'Ready', 'configured': False, 'held': False, 'problem': None, 'action': None}
    try:
        entries = work._scoped_config(config, CONFIG, {'school_year', 'class_key', 'week', 'form_id'})
        if not entries:
            return out
        year = work._school_year(agenda, week)
        scopes = set()
        for entry in entries:
            scope = (entry['school_year'], entry['class_key'], entry['week'], entry['form_id'])
            if (entry['class_key'] not in agenda.classes or not _text(entry.get('week'), 100)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', str(entry.get('form_id') or '')) or scope in scopes):
                raise ValueError()
            scopes.add(scope)
            labels = {label for _, label in (agenda.classes[entry['class_key']].get('layout') or {'rows': []})['rows']}
            if entry['school_year'] == year and entry['week'] not in labels:
                raise ValueError()
        hits = [e for e in entries if (e['school_year'], e['class_key'], e['week'], e['form_id']) ==
                (year, class_key, week, form_id)]
        if not hits:
            if year is None and any(e['class_key'] == class_key and e['week'] == week and e['form_id'] == form_id for e in entries):
                raise ValueError()
            return out
        out['configured'] = True
        ident = hold_key(agenda.spreadsheet_id, year, class_key, week, form_id)
        out['record_key'] = ident
        doc = configured_store(config).read([ident]).documents.get(ident)
        value = doc.value if doc else None
        expected = {'schema': SCHEMA, 'record_key': ident, 'agenda_id': agenda.spreadsheet_id,
                    'school_year': year, 'class_key': class_key, 'week': week, 'form_id': form_id}
        if (not value or any(value.get(k) != v for k, v in expected.items())
                or value.get('status') not in ('held', 'cleared') or not _text(value.get('reason'), 500)
                or not _text(value.get('teacher_action'), 1000)):
            raise ValueError()
        instant(value['updated_at'])
        if value['status'] == 'cleared':
            from . import completion
            if not _cleared(value, allow_found_codes=not completion.student_coding_required(
                    agenda, class_key, week, form_id)):
                raise ValueError()
            return {**out, 'clearance_verified': True}
        reason = value['reason'] if value['reason'].startswith('Needs embedded codes') else 'Needs embedded codes. ' + value['reason']
        return {**out, 'status': 'NOT_READY', 'label': 'Needs embedded codes', 'held': True, 'problem': reason,
                'action': value['teacher_action'], 'code': 'teacher_readiness_hold'}
    except (ValueError, TypeError, KeyError, AttributeError, StoreUnavailable):
        return {**out, 'status': 'NOT_READY', 'label': 'Needs check', 'held': True, 'code': 'teacher_readiness_hold',
                'problem': "The teacher's exact assignment readiness decision could not be verified. No grades may be prepared.",
                'action': 'Verify this assignment’s private teacher readiness hold before rerunning grading; a marked teacher deck does not clear it.'}
