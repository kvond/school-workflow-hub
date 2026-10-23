"""Fictional conditional authorization and operator verification; no live calls."""
from copy import deepcopy
import unittest
from hub_v2 import completion_holds as holds
from school_workflow_hub.core.runs import content_hash
import test_morning_readiness as baseline


class OperatorClearanceTests(unittest.TestCase):
    def setUp(self):
        self.f = baseline.TeacherHoldTests(); self.f.setUp()
        self.value = self.f.port.rows[self.f.ident].value
        self.value.update(status='cleared', clearance={
            'basis': holds.OPERATOR_VERIFIED,
            'teacher_confirmed_at': '2026-09-23T10:00:00Z',
            'verified_at': '2026-09-23T11:00:00Z',
            'copies': [{'file_id': 'FICTIONAL_ORIGINAL_%02d' % n, 'version': str(n+2), 'status': 'READY'}
                       for n in range(6)],
            'operator': 'fictional-coordinator',
            'authorization': {'schema': 'teacher-conditional-copy-repair/v1',
                'ref': 'fictional-original-teacher-message', 'sha256': 'a'*64,
                'authorized_at': '2026-09-23T10:00:00Z',
                'scope': {k: self.value[k] for k in ('agenda_id', 'school_year', 'class_key', 'week', 'form_id')}},
            'census': {'schema': 'complete-current-copy-census/v1', 'complete': True,
                'source_count': 6, 'copies_sha256': '', 'evidence_sha256': 'b'*64}})
        self.value['clearance']['census']['copies_sha256'] = content_hash(self.value['clearance']['copies'])

    def held(self):
        result = self.f.check()
        self.assertEqual(result['status'], 'NOT_READY')
        self.assertTrue(result['held'])

    def test_conditional_authorization_and_later_operator_verification_clear_exact_hold(self):
        old = deepcopy(self.value)
        result = self.f.check()
        self.assertEqual(result['status'], 'READY'); self.assertTrue(result['clearance_verified'])
        self.assertEqual(self.value, old, 'The reader never writes a clearance or history')

    def test_valid_proof_on_still_held_record_is_not_automatic_clearance(self):
        self.value['status'] = 'held'; self.held()

    def test_cross_assignment_or_missing_actual_authorization_holds(self):
        original = deepcopy(self.value)
        for field, changed in (('ref', ''), ('sha256', 'invented'), ('authorized_at', '2026-09-23T09:00:00Z'),
                               ('scope', {**original['clearance']['authorization']['scope'], 'form_id': 'OTHER_FORM'})):
            self.value.clear(); self.value.update(deepcopy(original))
            self.value['clearance']['authorization'][field] = changed; self.held()

    def test_incomplete_census_wrong_count_or_unbound_revisions_hold(self):
        original = deepcopy(self.value)
        for field, changed in (('complete', False), ('source_count', 5), ('source_count', True),
                               ('copies_sha256', 'c'*64), ('evidence_sha256', 'unverified')):
            self.value.clear(); self.value.update(deepcopy(original))
            self.value['clearance']['census'][field] = changed; self.held()

    def test_empty_duplicate_not_ready_or_changed_copy_revisions_hold(self):
        original = deepcopy(self.value)
        changes = (lambda p: p.update(copies=[]),
                   lambda p: p['copies'].append(deepcopy(p['copies'][0])),
                   lambda p: p['copies'][0].update(status='NOT_READY'),
                   lambda p: p['copies'][0].update(version='changed'))
        for change in changes:
            self.value.clear(); self.value.update(deepcopy(original))
            change(self.value['clearance']); self.held()

    def test_operator_missing_or_authorization_after_verification_holds(self):
        original = deepcopy(self.value)
        for change in (lambda p: p.update(operator=''),
                       lambda p: p.update(verified_at='2026-09-23T09:59:59Z'),
                       lambda p: p.update(teacher_confirmed_at='unknown')):
            self.value.clear(); self.value.update(deepcopy(original))
            change(self.value['clearance']); self.held()

    def test_new_basis_never_borrows_legacy_schema_or_other_scope(self):
        proof = self.value['clearance']
        for field in ('operator', 'authorization', 'census'):
            old = proof.pop(field); self.held(); proof[field] = old
        proof['unrequested'] = True; self.held(); proof.pop('unrequested')
        proof['basis'] = 'teacher_confirmed_distributed_copies_coded'
        self.held()  # Expanded operator fields cannot be relabeled as a teacher assertion.


if __name__ == '__main__':
    unittest.main()
