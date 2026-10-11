"""Fictional readbacks only; no browser, provider or real ledger calls."""
from copy import deepcopy
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
import io
import json
import unittest

from hub_v2 import app, completion, google, ledger, morning, morning_entry
import test_morning_row_selection_holds as row_holds

NOW = datetime(2026, 10, 11, 14, tzinfo=timezone.utc)
SENT = (NOW - timedelta(minutes=20)).isoformat()
AT = (NOW - timedelta(minutes=1)).isoformat()
RUN = '11111111-1111-4111-8111-111111111111'
LIST = '22222222-2222-4222-8222-222222222222'
FORM = 'FICTIONAL_OBSERVATION_FORM'
REV = 'a' * 64
TARGET = {'section': 'A2 Fictional', 'column': 'notes', 'url': 'https://ic.fictional.example/campus/sis/control-center/classroom/curriculum/resource/301/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/101/objectSectionID/301/calendarID/854/structureID/854)?classroomSectionID=101&classroomTermID=501&year=2027&schoolID=55&calendarID=854&structureID=854',
          'section_id': '101', 'term_id': '501', 'year': '2027', 'school_id': '55', 'calendar_id': '854',
          'structure_id': '854', 'assignment_id': '301', 'score_group': '401', 'task_id': '13', 'label_code': 'W03F'}


def fixture(checks=10):
    rows = [{'last': 'Avery', 'first': 'Alex', 'section': 'A2 Fictional', 'kind': 'score',
             'notes': 10, 'revision': None, 'reason': ''}]
    rows += [{'last': 'Held%d' % i, 'first': 'Alex', 'section': 'A2 Fictional', 'kind': 'check',
              'notes': None, 'revision': None, 'reason': 'Exact original history needs review'} for i in range(checks)]
    listed = {'schema': 'hub-ic-list/v1', 'list_id': LIST, 'class_key': 'forensics', 'class_name': 'Forensics',
              'week': 'Wk 03', 'form_id': FORM, 'assignment': 'Observation Skills', 'form_label': 'Observation Skills',
              'status': 'closed', 'status_text': 'Morning scores', 'sent_at': SENT, 'rows': rows}
    result = {'list_id': LIST, 'form_id': FORM, 'class_key': 'forensics', 'week': 'Wk 03',
              'column': 'notes', 'section': 'A2 Fictional', 'ic_title': 'Observation (F)',
              'outcome': 'entered', 'certain': True,
              'counts': {'filled': 0, 'raised': 1, 'kept': 0, 'left': checks, 'not_saved': 0}, 'at': AT}
    payload = {'run_id': RUN, 'scope': {'class': 'forensics', 'week': 'Wk 03', 'form': FORM},
               'revision': REV, 'list': listed, 'targets': [deepcopy(TARGET)], 'results': [result]}
    current = {'code': 'ready', 'revision': REV, 'targets': [deepcopy(TARGET)],
               'list': {**deepcopy(listed), 'list_id': '33333333-3333-4333-8333-333333333333', 'sent_at': NOW.isoformat()}}
    return payload, current


class EligibleReceiptTests(unittest.TestCase):
    def test_ready_list_with_real_history_check_reproduces_generic_record_refusal(self):
        original = row_holds.MorningRowSelectionHoldTests()
        original.setUp()
        try:
            with ExitStack() as stack:
                original.source_patches(stack)
                result = original.make()
                self.assertEqual(result['code'], 'ready')
                self.assertEqual([r['kind'] for r in result['list']['rows']], ['score', 'check'])
                held = completion.submitted_readiness('fictional', original.form,
                    include_late=True, require_complete_codes=False)
            self.assertEqual((held['status'], held['code']), ('NOT_READY', 'coverage_incomplete'))
        finally:
            original.tearDown()

    def test_current_ten_checks_are_bound_but_not_given_scores(self):
        payload, current = fixture()
        before = deepcopy((payload, current))
        receipt = morning_entry.validate(payload, current, now=NOW)
        self.assertEqual(receipt['source_check_rows'], 10)
        self.assertEqual(receipt['results'], payload['results'])
        self.assertNotIn('rows', receipt)
        self.assertEqual((payload, current), before)

    def test_source_identity_rows_teacher_policy_and_mapping_changes_reject(self):
        payload, current = fixture()
        changes = [lambda d: d.update(revision='b' * 64),
                   lambda d: d['list']['rows'][0].update(notes=9),
                   lambda d: d['list']['rows'][1].update(kind='score', notes=10),
                   lambda d: d['list']['rows'][1].update(reason='Changed identity hold'),
                   lambda d: d['list'].update(form_id='FICTIONAL_OTHER_FORM'),
                   lambda d: d['targets'][0].update(assignment_id='999'),
                   lambda d: d.update(code='coverage_incomplete', readiness={'status': 'NOT_READY', 'problem': 'Photosynthesis'})]
        for change in changes:
            changed = deepcopy(current); change(changed)
            with self.subTest(changed=changed), self.assertRaises(morning_entry.Refused):
                morning_entry.validate(payload, changed, now=NOW)

    def test_expired_future_and_pre_list_result_times_reject(self):
        payload, current = fixture()
        for sent, at in [(NOW - timedelta(hours=4), NOW), (NOW + timedelta(minutes=2), NOW),
                         (NOW - timedelta(minutes=20), NOW - timedelta(minutes=21)),
                         (NOW - timedelta(minutes=20), NOW + timedelta(seconds=1))]:
            changed = deepcopy(payload)
            changed['list']['sent_at'] = sent.isoformat(); changed['results'][0]['at'] = at.isoformat()
            with self.subTest(sent=sent, at=at), self.assertRaises(morning_entry.Refused):
                morning_entry.validate(changed, current, now=NOW)

    def test_unknown_uncertain_duplicate_incomplete_or_impossible_results_reject(self):
        payload, current = fixture()
        changes = [lambda d: d.update(results=[]),
                   lambda d: d['results'].append(deepcopy(d['results'][0])),
                   lambda d: d['results'][0].update(certain=False),
                   lambda d: d['results'][0].update(outcome='partial'),
                   lambda d: d['results'][0].update(outcome='unknown'),
                   lambda d: d['results'][0].update(list_id=RUN),
                   lambda d: d['results'][0].update(section='A3 Fictional'),
                   lambda d: d['results'][0]['counts'].update(not_saved=1),
                   lambda d: d['results'][0]['counts'].update(raised=2),
                   lambda d: d['results'][0]['counts'].update(raised=True),
                   lambda d: d['results'][0].update(outcome='checked'),
                   lambda d: d['results'][0].update(raw_students=['Private names'])]
        for change in changes:
            changed = deepcopy(payload); change(changed)
            with self.subTest(changed=changed), self.assertRaises(morning_entry.Refused):
                morning_entry.validate(changed, current, now=NOW)

    def test_every_needed_target_requires_one_unique_matching_result(self):
        payload, current = fixture()
        for row in payload['list']['rows'][:1] + current['list']['rows'][:1]:
            row['revision'] = 10
        revision = {**deepcopy(TARGET), 'column': 'revision',
                    'url': TARGET['url'].replace('resource/301/', 'resource/302/').replace('objectSectionID/301/', 'objectSectionID/302/'),
                    'assignment_id': '302', 'label_code': 'W03S'}
        payload['targets'].append(revision); current['targets'].append(deepcopy(revision))
        second = {**deepcopy(payload['results'][0]), 'column': 'revision', 'outcome': 'checked',
                  'counts': {'filled': 0, 'raised': 0, 'kept': 1, 'left': 10, 'not_saved': 0}}
        payload['results'].append(second)
        self.assertEqual(len(morning_entry.validate(payload, current, now=NOW)['results']), 2)
        payload['results'][1]['column'] = 'notes'
        with self.assertRaises(morning_entry.Refused):
            morning_entry.validate(payload, current, now=NOW)

    def test_even_matching_current_config_requires_supported_exact_target_resource_shape(self):
        for change in (lambda t: t.update(task_id='unknown'), lambda t: t.update(url='https://ic.fictional.example/other'),
                       lambda t: t.update(url=t['url'] + '&classroomSectionID=101'),
                       lambda t: t.update(label_code='arbitrary label'), lambda t: t.update(unknown=True)):
            payload, current = fixture()
            change(payload['targets'][0]); current['targets'] = deepcopy(payload['targets'])
            with self.subTest(target=payload['targets']), self.assertRaises(morning_entry.Refused):
                morning_entry.validate(payload, current, now=NOW)

    def save(self, whole=False, existing=None, readback=True):
        payload, current = fixture(0 if whole else 10)
        receipt = morning_entry.validate(payload, current, now=NOW)
        context = {'class': 'Forensics', 'week': 'Wk 03', 'assignment': 'Observation Skills', 'form_id': FORM}
        rows = deepcopy(existing or [])
        def append(_token, _sheet, _area, additions):
            if readback: rows.extend(deepcopy(additions))
        with patch.object(ledger, 'find', return_value={'id': 'FICTIONAL_LEDGER'}), \
                patch.object(ledger, '_first_tab', return_value="'Sheet1'"), \
                patch.object(google, 'sheet_values', side_effect=lambda *args: deepcopy(rows)), \
                patch.object(google, 'append_rows', side_effect=append) as writer:
            saved = morning_entry.record('fictional-read', 'fictional-write', {}, context, receipt,
                                         whole_assignment=whole)
        return saved, rows, writer

    def test_partial_receipt_preserves_whole_assignment_date_and_identical_retry(self):
        saved, rows, writer = self.save()
        writer.assert_called_once()
        self.assertNotIn(morning_entry.KIND, ledger.KINDS.values())
        self.assertFalse(saved['whole_assignment_recorded'])
        self.assertEqual([row[1] for row in rows[1:]], [morning_entry.KIND])
        again, after, writer = self.save(existing=rows)
        writer.assert_not_called(); self.assertEqual(again, saved); self.assertEqual(after, rows)
        ack = morning_entry.acknowledgement(saved)
        self.assertEqual((ack['run_id'], ack['list_id'], ack['target_count'], ack['counts']['left']),
                         (RUN, LIST, 1, 10))
        self.assertNotIn('Avery', json.dumps(ack)); self.assertNotIn('Held0', json.dumps(saved))

    def test_whole_ready_receipt_and_entry_stamp_append_together_and_read_back(self):
        saved, rows, writer = self.save(whole=True)
        writer.assert_called_once()
        self.assertTrue(saved['whole_assignment_recorded'])
        self.assertEqual([r[1] for r in rows[1:]], [morning_entry.KIND, ledger.KINDS['ic']])
        self.save(whole=True, existing=rows)[2].assert_not_called()
        with self.assertRaises(morning_entry.Refused): self.save(whole=True, existing=rows[:-1])

    def test_changed_or_unconfirmed_stored_receipt_never_appends_again(self):
        _, rows, _ = self.save()
        value = json.loads(rows[1][6]); value['results'][0]['counts']['raised'] = 0
        rows[1][6] = json.dumps(value)
        with self.assertRaises(morning_entry.Refused): self.save(existing=rows)
        with self.assertRaises(google.GoogleError): self.save(readback=False)


class EligibleReceiptEndpointTests(unittest.TestCase):
    def request(self, payload, *, checks=10, ready=None, origin='https://fictional.example', signed_in=True,
                second_current=None):
        raw = json.dumps(payload).encode()
        env = {'REQUEST_METHOD': 'POST', 'PATH_INFO': '/v2/api/morning/entry', 'HTTP_ORIGIN': origin,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        current = fixture(checks)[1]
        form = {'form_id': FORM, 'label': 'Observation Skills', 'students': [], 'late': [], 'window': None}
        found = {'lessons': [{'forms': [form]}]}
        def make(_token, _config, _agenda, _cls, _week, _fid, loader, scorer):
            loader('fictional', {}, None, 'forensics', 'Wk 03', fresh=True)
            return deepcopy(current if make.count == 0 or second_current is None else second_current)
        make.count = 0
        def make_counted(*args):
            value = make(*args); make.count += 1; return value
        def save(*args, **kwargs): return {**args[-1], 'whole_assignment_recorded': kwargs['whole_assignment']}
        status = []
        with patch.object(google, 'verify_teacher', side_effect=None if signed_in else google.SignInRequired), \
                patch.object(google, 'service_token', return_value='fictional'), \
                patch.object(app, 'load_agenda', return_value=Mock()), \
                patch.object(app, 'lesson_work', return_value=found), \
                patch.object(morning, 'make_list', side_effect=make_counted), \
                patch.object(morning_entry, 'datetime', wraps=datetime) as clock, \
                patch.object(completion, 'student_coding_required', return_value=False), \
                patch.object(completion, 'submitted_readiness', return_value=ready or {'status': 'READY'}) as whole_guard, \
                patch.object(ledger, 'read', return_value={'configured': True}), \
                patch.object(morning_entry, 'record', side_effect=save) as writer:
            clock.now.return_value = NOW
            result = json.loads(b''.join(app.application(env, lambda code, headers: status.append(code),
                               {'HUB_STATE_PROBE_ORIGINS': 'https://fictional.example'})))
        return status[0], result, writer, whole_guard

    def test_ten_checks_get_eligible_ack_without_whole_assignment_guard_or_stamp(self):
        payload, _ = fixture()
        status, result, writer, guard = self.request(payload)
        self.assertEqual(status, '200 OK'); writer.assert_called_once(); guard.assert_not_called()
        self.assertEqual(result['receipt']['source_check_rows'], 10)
        self.assertFalse(result['receipt']['whole_assignment_recorded'])
        self.assertEqual(result['receipt']['source_revision'], REV)

    def test_whole_guard_still_rejects_photo_or_unreadable_and_source_bookend_change(self):
        payload, _ = fixture(0)
        status, result, writer, guard = self.request(payload, checks=0,
            ready={'status': 'NOT_READY', 'problem': 'Photosynthesis copy must be coded'})
        self.assertEqual(status, '409 Conflict'); writer.assert_not_called(); guard.assert_called_once()
        changed = fixture(0)[1]; changed['revision'] = 'b' * 64
        status, _, writer, _ = self.request(payload, checks=0, second_current=changed)
        self.assertEqual(status, '409 Conflict'); writer.assert_not_called()

    def test_auth_origin_invalid_or_uncertain_results_cannot_write(self):
        payload, _ = fixture()
        for kwargs, expected in [({'origin': 'https://evil.example'}, '403 Forbidden'),
                                 ({'signed_in': False}, '401 Unauthorized')]:
            status, _, writer, _ = self.request(payload, **kwargs)
            self.assertEqual(status, expected); writer.assert_not_called()
        payload['results'][0]['certain'] = False
        self.assertEqual(self.request(payload)[0], '409 Conflict')
        self.request(payload)[2].assert_not_called()


if __name__ == '__main__': unittest.main()
