"""Producer-shaped private evidence references; fictional state, no live calls."""
from copy import deepcopy
import unittest

from school_workflow_hub.adapters import roster_rejection as rejection
from school_workflow_hub.core.cloud_state import Conflict, identifier
from tests import test_roster_rejection as fixtures


class EvidenceReferenceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.RejectionTests('runTest')
        self.f.setUp()
        self.references = {
            'trace_reference': 'roster-bootstrap-v2-native-write-failure-root.private.json',
            'attempt_reference': 'roster-bootstrap-attempt.private.json',
            'historical_capability_reference': 'roster-writer-capability-readback.private.json',
        }

    def test_three_producer_basenames_record_literal_original_evidence_once(self):
        self.f.failure.update(self.references)
        old_rows = deepcopy(self.f.port.rows)
        failure = deepcopy(self.f.failure)
        prepared = self.f.recorder.prepare()
        self.assertEqual(prepared['payload']['native_evidence'], failure)
        result = self.f.recorder.record_rejected_bootstrap(prepared)
        self.assertEqual(result['status'], 'verified_rejection_record')
        self.assertEqual(self.f.port.rows[result['record_id']].value['native_evidence'], failure)
        self.assertTrue(all(self.f.port.rows[k] == v for k, v in old_rows.items()))
        self.assertEqual(len(self.f.port.rows), len(old_rows) + 1)
        self.assertFalse(result['retry_allowed'])
        self.assertFalse(result['activation_allowed'])
        self.assertEqual(result['native_writes'], 0)
        self.f.f.mirror.apply.assert_called_once()  # Only the original synthetic 403.
        self.assertFalse(failure['historical_capability']['can_edit'])
        before = deepcopy(self.f.port.rows)
        self.assertEqual(self.f.recorder.record_rejected_bootstrap(prepared), result)
        self.assertEqual(self.f.port.rows, before)

    def test_existing_opaque_references_and_global_identifier_are_unchanged(self):
        prepared = self.f.recorder.prepare()
        self.assertEqual(prepared['payload']['native_evidence'], self.f.failure)
        for value in ('trace-1', 'evidence:opaque_id-1', 'a' * 160):
            self.assertEqual(rejection._evidence_reference(value), value)
        for value in self.references.values():
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'invalid_state_identifier'):
                identifier(value)

    def test_paths_traversal_controls_and_hostile_basenames_hold_before_sidecar(self):
        hostile = (
            '../trace.private.json', 'folder/trace.private.json', '/trace.private.json',
            r'folder\trace.private.json', r'..\trace.private.json',
            'trace..private.json', '.private.json', 'trace.private.json/child',
            'trace.private.json\n', 'trace\r.private.json', 'trace\x00.private.json',
            'trace private.json', 'trace;command.private.json', '$(command).private.json',
            '`command`.private.json', 'trace?.private.json', 'trace%2fitem.private.json',
            'trace.private.JSON', 'trace.json', 'https://example.invalid/trace.private.json',
            '', None, 123, ['trace.private.json'], 'é.private.json',
        )
        original = deepcopy(self.f.failure)
        for field in self.references:
            for value in hostile:
                self.f.failure = {**deepcopy(original), field: value}
                before = deepcopy(self.f.port.rows)
                with self.subTest(field=field, value=value), self.assertRaisesRegex(
                        Conflict, 'evidence_reference_invalid'):
                    self.f.recorder.prepare()
                self.assertEqual(self.f.port.rows, before)
        self.f.f.mirror.apply.assert_called_once()

    def test_private_basename_boundaries_are_explicit(self):
        accepted = 'a' * (160 - len('.private.json')) + '.private.json'
        self.assertEqual(len(accepted), 160)
        self.assertEqual(rejection._evidence_reference(accepted), accepted)
        for value in ('a' + accepted, 'a' * 161):
            with self.subTest(value=value), self.assertRaisesRegex(Conflict, 'evidence_reference_invalid'):
                rejection._evidence_reference(value)

    def test_observer_cannot_be_relabelled_as_a_filename(self):
        self.f.failure.update(self.references)
        self.f.failure['observer'] = 'coordinator.private.json'
        before = deepcopy(self.f.port.rows)
        with self.assertRaisesRegex(ValueError, 'invalid_state_identifier'):
            self.f.recorder.prepare()
        self.assertEqual(self.f.port.rows, before)

    def test_accepted_reference_does_not_weaken_hash_403_or_original_claim_binding(self):
        self.f.failure.update(self.references)
        original = deepcopy(self.f.failure)
        for field, value in (('status', 429), ('trace_sha256', 'not-a-hash'),
                             ('provider_reason', 'inferred'), ('proposal_sha256', 'a' * 64)):
            self.f.failure = {**deepcopy(original), field: value}
            before = deepcopy(self.f.port.rows)
            with self.subTest(field=field), self.assertRaises(Conflict):
                self.f.recorder.prepare()
            self.assertEqual(self.f.port.rows, before)


if __name__ == '__main__':
    unittest.main()
