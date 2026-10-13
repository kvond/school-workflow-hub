"""Paired evidence, identity, chronology and changing curriculum denominators."""
import copy
import unittest
from unittest.mock import patch
from hub_v2 import longitudinal_report as growth, teaching_report


ROSTER = [{'student_key': 'one', 'name': 'Rivera, Ana', 'section': 'A4 Biology'},
          {'student_key': 'two', 'name': 'Bennett, Ben', 'section': 'A4 Biology'},
          {'student_key': 'three', 'name': 'Carter, Cam', 'section': 'A4 Biology'}]


def observation(student='one', lesson='earlier', **changes):
    return {'class_key': 'biology', 'student_key': student, 'section': 'A4 Biology',
            'dimension': next(iter(growth.WRITING)), 'lesson_key': lesson, 'lesson': lesson.title(),
            'rubric_version': 'science-writing-four-dimensions/v1', 'stage': 'DRAFT',
            'date': '2026-09-25' if lesson == 'earlier' else '2026-10-06',
            'assessed_at': '2026-10-09', 'source_file_id': student+'-'+lesson,
            'source_revision': '3', 'source_url': 'https://example.test/'+student+'/'+lesson,
            'level': 'Developing' if lesson == 'earlier' else 'Proficient',
            'quote': 'A fictional scientific explanation.', 'source_date_basis': 'form_receipt', **changes}


class LongitudinalTests(unittest.TestCase):
    def test_same_students_drive_both_percentages_and_unrated_is_not_beginning(self):
        rows = [observation(), observation(lesson='later'), observation('two', level='Proficient'),
                observation('two', 'later', level='Evidence unavailable', quote=''), observation('three', 'later')]
        before = copy.deepcopy(rows)
        result = growth.build(rows, ROSTER); row = result['tables']['Across lessons'][1]
        self.assertEqual(row[7:14], [3, 1, 0, 1, 0, 1, 100])
        self.assertEqual(row[14:18], [1, 0, 0, 2])
        self.assertEqual(sum(r[12] == 'N/A' for r in result['tables']['Student growth'][1:]), 2)
        self.assertEqual(rows, before)

    def test_actual_submission_order_can_differ_from_curriculum_or_filename_order(self):
        result = growth.build([observation(date='2026-10-07'), observation(lesson='later')], ROSTER)
        row = result['tables']['Across lessons'][1]
        self.assertEqual(row[5:7], ['Later', 'Earlier'])
        self.assertEqual(row[14:17], [0, 0, 1])

    def test_equal_day_requires_exact_source_receipts_and_file_created_is_not_growth(self):
        a = observation(date='2026-10-06'); b = observation(lesson='later')
        result = growth.build([a,b], ROSTER)
        self.assertEqual(result['paired_comparisons'], 0)
        self.assertEqual(result['tables']['Growth figure data'], [growth.FIGURE_HEADER])
        a['submitted_at'] = '2026-10-06T16:00:00Z'; b['submitted_at'] = '2026-10-06T17:00:00Z'
        self.assertEqual(growth.build([a,b], ROSTER)['paired_comparisons'], 1)
        a['source_date_basis'] = 'filename_submission'
        self.assertEqual(growth.build([a,b], ROSTER)['paired_comparisons'], 0)

    def test_stage_rubric_class_section_and_student_identity_cannot_be_borrowed(self):
        for change in ({'stage':'NOTES'}, {'rubric_version':'older-version'}, {'identity_verified':False}):
            result = growth.build([observation(), observation(lesson='later', **change)], ROSTER)
            self.assertEqual(result['paired_comparisons'], 0)
        for change in ({'section':'A3 Biology'}, {'class_key':'forensics'}):
            with self.assertRaises(growth.Held):
                growth.build([observation(**change)], ROSTER)
        result = growth.build([observation('departed'), observation(lesson='later')], ROSTER)
        self.assertEqual(result['paired_comparisons'], 0)

    def test_missing_quote_bad_timestamp_duplicate_source_and_unversioned_concept(self):
        for change in ({'quote':''}, {'submitted_at':'2026-09-25T01:00:00Z'}, {'date':'file created yesterday'}):
            with self.assertRaises(growth.Held): growth.build([observation(**change)], ROSTER)
        a = observation(); b = observation(lesson='later', source_file_id=a['source_file_id'])
        self.assertEqual(growth.build([a,b], ROSTER)['paired_comparisons'], 0)
        a.pop('dimension'); a.update(critical_aspect='Energy transfer', rubric_version='')
        self.assertEqual(growth.build([a], ROSTER)['paired_comparisons'], 0)

    def test_same_critical_aspect_key_joins_only_explicit_current_scale(self):
        rows = [observation(), observation(lesson='later')]
        for row, label in zip(rows, ('Critical aspect: Energy Transfer.', 'Your answer — Critical Aspect 2 · energy transfer')):
            row.pop('dimension'); row.update(critical_aspect=label, rubric_version='critical-aspect-4/v1')
        self.assertEqual(growth.build(rows, ROSTER)['paired_comparisons'], 1)
        rows[1]['critical_aspect'] = 'Where transferred energy goes'
        self.assertEqual(growth.build(rows, ROSTER)['paired_comparisons'], 0)

    def test_revision_of_same_original_updates_one_point_never_invents_another_lesson(self):
        a = observation(); b = {**a, 'source_revision':'4', 'level':'Proficient'}
        result = growth.build([a,b], ROSTER)
        self.assertEqual(result['tables']['Across lessons'], [growth.CLASS_HEADER])
        with self.assertRaises(growth.Held):
            growth.build([a, {**b, 'source_file_id':'other-source'}], ROSTER)

    def test_individual_lines_preserve_missing_samples_and_skip_unordered_same_day(self):
        rows = [observation(), observation(lesson='middle', date='2026-10-01', level='Evidence unavailable', quote=''),
                observation(lesson='later')]
        table, blocks = growth.student_series(rows, ROSTER)
        self.assertEqual([r[1] for r in table[blocks[0]['start_row']+1:blocks[0]['end_row']]], [2, '', 3])
        self.assertEqual(len(blocks), 1)
        rows[0]['date'] = '2026-10-01'
        table, blocks = growth.student_series(rows, ROSTER)
        self.assertEqual(blocks, [])

    def test_another_field_keeps_a_gap_in_an_individual_line(self):
        rows = [observation(), observation(lesson='middle', date='2026-10-01', stage='NOTES'),
                observation(lesson='later')]
        table, blocks = growth.student_series(rows, ROSTER)
        draft = next(b for b in blocks if b['title'].endswith('DRAFT'))
        self.assertEqual([r[1] for r in table[draft['start_row']+1:draft['end_row']]], [2, '', 3])

    def critical(self):
        rows=[]
        for lesson, aspects, day in [('earlier', ['Concept A','Concept B','Concept C'], '2026-09-25'),
                                     ('later', ['New concept D','New concept E'], '2026-10-06')]:
            for student in ('one','two'):
                for i, aspect in enumerate(aspects):
                    r=observation(student,lesson,critical_aspect=aspect,assessed_at=day)
                    r.pop('dimension'); r['level']='Proficient' if i==0 or lesson=='later' else 'Developing'
                    rows.append(r)
        return rows

    def test_critical_common_categories_keep_changed_aspect_denominators_and_assessment_dates(self):
        rows=self.critical(); attainment=growth.critical_attainment(rows,ROSTER)
        self.assertEqual(attainment[1][3:6], [3,3,9])
        paired, individual=growth.critical_pairs(rows,ROSTER)
        self.assertEqual(paired[1][3:8], [2,6,4,2,4])
        self.assertAlmostEqual(paired[1][10], 200/3)
        self.assertIn('not repeated mastery', paired[1][-1])
        self.assertEqual(len(individual),3)
        self.assertEqual(growth.build(rows,ROSTER)['paired_comparisons'],0)

    def test_additive_plan_cannot_overwrite_old_comparisons_notes_or_existing_growth_tab(self):
        book={'spreadsheetId':'FictionalSheet','sheets':[{'properties':{'title':'Comparisons','sheetId':5}},
                                                        {'properties':{'title':'Teaching plan','sheetId':6}}]}
        result=teaching_report.growth_tables([observation(),observation(lesson='later')],ROSTER)
        with patch.object(teaching_report.google,'get_json',side_effect=AssertionError('unexpected write')):
            plan=teaching_report.additive_growth_plan(book,result['tables'],
                 chart_tables=['Growth figure data'],student_figures=result['student_figures'])
        updates=[r['updateCells'] for r in plan['requests'] if 'updateCells' in r]
        self.assertTrue(all(r['range']['sheetId'] not in (5,6) for r in updates))
        for r in updates:
            self.assertEqual(len(r['rows']),r['range']['endRowIndex'])
            self.assertTrue(all(len(row['values'])==r['range']['endColumnIndex'] for row in r['rows']))
        chart=next(r['addChart']['chart'] for r in plan['requests'] if 'addChart' in r and r['addChart']['chart']['spec']['basicChart']['chartType']=='LINE')
        self.assertFalse(chart['spec']['basicChart']['interpolateNulls'])
        self.assertEqual(len(chart['spec']['basicChart']['series']),1)
        for data in chart['spec']['basicChart']['domains'] + chart['spec']['basicChart']['series']:
            sources=(data.get('domain')or data.get('series'))['sourceRange']['sources']
            self.assertEqual(len(sources),1)
            self.assertEqual(sources[0]['startRowIndex'],result['student_figures'][0]['start_row'])
            self.assertEqual(sources[0]['endRowIndex'],result['student_figures'][0]['end_row'])
        book['sheets'].append({'properties':{'title':'Across lessons','sheetId':10}})
        with self.assertRaises(teaching_report.Held): teaching_report.additive_growth_plan(book,result['tables'])


if __name__ == '__main__': unittest.main()
