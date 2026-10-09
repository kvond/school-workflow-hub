"""Link-audit exclusions preserve real source failures on fictional Agenda rows."""
import copy
import json
import unittest
from unittest.mock import patch

from hub_v2 import audit, google
from test_hub_v2 import DIRECT, cell, fixture


def config(cls='biology', year='2026-27', weeks=None):
    return {'HUB_WEEK_EXCLUSIONS': json.dumps([{'school_year': year, 'class_key': cls,
            'weeks': weeks or ['Wk 03'], 'reason': 'This week is future-use only this school year.'}])}


class AuditPolicyTests(unittest.TestCase):
    def check(self, source, settings, cls='biology', missing=()):
        def drive(token, ident, fields=None):
            if ident in missing:
                raise google.GoogleError('Not shared with runtime', 403)
            return {'name': 'Fictional Week 04 deck' if 'DECK' in ident else 'Fictional Week 04 Form'}
        with patch.object(google, 'drive_file', side_effect=drive), \
                patch.object(google, 'form_info', return_value={}), \
                patch.object(audit.feedback_module, 'load_embed_prompt', return_value=('', '')), \
                patch.object(audit.feedback_module, 'load_prompt', return_value=('', '')):
            return audit.check('read-only', source, cls, settings)

    def test_only_exact_valid_excluded_no_form_warning_becomes_information(self):
        source = fixture()
        original = copy.deepcopy(source.classes['biology']['cells'])
        found = self.check(source, config())
        self.assertFalse(any(f['kind'] == 'no-form' for f in found['flags']))
        self.assertEqual(found['exclusions'], [{'week': 'Wk 03', 'row': 4, 'topic': 'Earlier topic',
            'school_year': '2026-27', 'reason': 'This week is future-use only this school year.'}])
        self.assertTrue(found['rows'][0]['week_policy']['excluded'])
        self.assertIsNone(found['problem'])
        self.assertEqual(source.classes['biology']['cells'], original)

    def test_other_class_and_other_year_keep_genuine_no_form(self):
        for settings in ({}, config('forensics'), config(year='2025-26')):
            found = self.check(fixture(), settings)
            self.assertIn(('Wk 03', 'no-form'), [(f['week'], f['kind']) for f in found['flags']])
            self.assertEqual(found['exclusions'], [])

    def test_excluded_week_still_reports_unreadable_form_deck_and_conflicting_hidden_link(self):
        source = fixture()
        source.classes['biology']['cells'][5, 21] = cell('https://docs.google.com/forms/d/FICTIONAL_DIFFERENT_FORM_123456/viewform')
        found = self.check(source, config(weeks=['Wk 04']), missing=('FICTIONALDECK1234567890', 'FICTIONALFORMID1234567890abc'))
        kinds = {f['kind'] for f in found['flags'] if f['week'] == 'Wk 04'}
        self.assertTrue({'unreadable', 'deck-unreadable', 'hidden-differs'} <= kinds)
        self.assertEqual(found['exclusions'][0]['week'], 'Wk 04')

    def test_invalid_exclusion_is_explicit_and_never_silences_missing_form(self):
        for settings in ({'HUB_WEEK_EXCLUSIONS': '{invalid'}, config(weeks=['Cycle 02'])):
            found = self.check(fixture(), settings)
            self.assertTrue(found['problem'])
            self.assertTrue(any(f['kind'] == 'no-form' for f in found['flags']))
            self.assertEqual(found['exclusions'], [])


if __name__ == '__main__':
    unittest.main()
