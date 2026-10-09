from datetime import datetime, timedelta, timezone
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hub_v2 import native_feedback as native, student_archive as archive
from tests import test_student_archive as archive_fixtures


class CurrentContractDaily(unittest.TestCase):
    def evidence(self):
        now = datetime.now(timezone.utc)
        plan = {'record_key': 'currentcontractplan_' + '1' * 64, 'date': '2026-10-09',
                'contract_keys': ['missingworkcontract_' + '2' * 64], 'plan_sha256': '3' * 64,
                'inputs_sha256': '4' * 64, 'evidence_checked_at': now.isoformat(),
                'expires_at': (now + timedelta(hours=1)).isoformat()}
        item = {'date': plan['date'], 'contract_status': 'open', 'contract_keys': plan['contract_keys'],
                'source_proof': {k: plan[k] for k in ('plan_sha256', 'inputs_sha256')},
                'report_type': 'missing_work_contract', 'course_name': 'Biology'}
        item['source_proof']['plan_id'] = plan['record_key']
        return item, plan

    def test_open_date_marker_cannot_replace_latest_exact_source_proof(self):
        item, plan = self.evidence()
        native._verify_current_contract(item, plan)
        for field in ('plan_sha256', 'inputs_sha256', 'plan_id'):
            changed = {**item, 'source_proof': {**item['source_proof'], field: 'other'}}
            with self.assertRaises(native.Held):
                native._verify_current_contract(changed, plan)
        with self.assertRaises(native.Held):
            native._verify_current_contract({**item, 'contract_keys': ['closed-lesson']}, plan)

    def test_stale_missing_evidence_holds_new_packet_but_not_other_feedback(self):
        item, _ = self.evidence()
        item['contract_evidence_expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        available = {'configured': True, 'files': []}
        with patch.object(native, '_feedback_files', return_value=available), \
                patch.object(native, '_select_daily_sources', return_value=[item]):
            with self.assertRaisesRegex(native.Held, 'recheck the current roster'):
                native.daily_sources('token', {}, datetime.now(timezone.utc).date().isoformat())
        lesson = {'report_type': 'science_writing'}
        with patch.object(native, '_feedback_files', return_value=available), \
                patch.object(native, '_select_daily_sources', return_value=[lesson]):
            self.assertEqual([lesson], native.daily_sources('token', {}, '2026-10-09'))

    def contract_catalog(self):
        config = archive_fixtures.config()
        _, plan = self.evidence()
        plan.update(schema='hub-current-contract-plan/v1', status='verified', class_key='biology',
            record_key=archive.key('currentcontractplan', [archive_fixtures.ROOT, 'biology']),
            feedback_ids=[], contract_keys=[])
        items = []
        for number in range(17):
            start = len(plan['contract_keys'])
            keys = ['missingworkcontract_' + format(start + i + 1, '064x') for i in range(3 if number < 6 else 2)]
            ident = 'feedbacksource_' + format(number + 1, '064x')
            plan['feedback_ids'].append(ident); plan['contract_keys'].extend(keys)
            items.append({'status': 'verified', 'mime': 'application/pdf', 'report_type': 'missing_work_contract',
                'name': '2026-10-09 - Student%02d, Fictional - Current work contract.pdf' % number,
                'feedback_id': ident, 'class_key': 'biology', 'drive_file_id': 'FictionalContractFile%02d' % number,
                'class_folder_id': 'FictionalBiologyCourse', 'student_folder_id': 'FictionalStudentFolder%02d' % number,
                'student_folder_name': 'Student%02d, Fictional' % number, 'created_at': '2026-10-09T15:00:00Z',
                'saved_at': '2026-10-09T15:00:00Z', 'date': plan['date'], 'contract_status': 'open',
                'contract_keys': keys, 'contract_checked_at': '2026-10-09T15:00:00Z',
                'source_proof': {'plan_sha256': plan['plan_sha256'], 'inputs_sha256': plan['inputs_sha256'],
                                 'plan_id': plan['record_key']}})
        return config, plan, items

    def catalog(self, config, plan, items):
        with patch.object(native, '_legacy_feedback_files', return_value={'configured': False, 'files': [], 'problem': None}), \
                patch.object(archive, 'list_feedback', return_value=items), \
                patch.object(native, 'configured_store', return_value=object()), \
                patch.object(native, '_read', return_value=SimpleNamespace(value=plan) if plan else None) as read:
            result = native._feedback_files('token', config)
        self.assertEqual(read.call_count, 1)
        return result

    def test_actual_current_contract_filenames_enter_catalog_only_with_exact_published_plan(self):
        config, plan, items = self.contract_catalog()
        self.assertEqual(len(plan['contract_keys']), 40)
        self.assertTrue(all(not native._student_print_name(i['name']) for i in items))
        result = self.catalog(config, plan, items)
        self.assertEqual(len(result['files']), 17)
        self.assertEqual({f['feedback_id'] for f in result['files']}, set(plan['feedback_ids']))
        self.assertTrue(all(f['report_type'] == 'missing_work_contract' and f['contract_status'] == 'open'
                            and f['contract_evidence_expires_at'] == plan['expires_at'] for f in result['files']))
        closed = deepcopy(plan); closed['feedback_ids'].pop()
        self.assertEqual(len(self.catalog(config, closed, items)['files']), 16)
        changed = deepcopy(items); changed[0]['source_proof']['plan_sha256'] = 'a' * 64
        with self.assertRaisesRegex(native.Held, 'latest verified class source check'):
            self.catalog(config, plan, changed)
        with self.assertRaisesRegex(native.Held, 'complete verified source check'):
            self.catalog(config, None, items)


if __name__ == '__main__':
    unittest.main()
