"""Synthetic inputs only; these tests do not establish live school access."""
import copy
import unittest
from unittest.mock import Mock, patch
from school_workflow_hub.adapters import dated_agenda as day
from school_workflow_hub.adapters.google_sheets import validate_ranges
from tests.test_hosted_agenda import CONFIG
from tests.test_web_hosting import request


def sheet(title, entries):
    blocks = []
    for r, values in entries.items():
        blocks.append({'startRow':r-1,'rowData':[{'values':[v if isinstance(v,dict) else {'formattedValue':v} for v in values]}]})
    return {'properties':{'title':title},'data':blocks}


def fixtures():
    weeks = sheet('🗂 Weeks', {2:['','course row','week','Monday'],
        3:['','4','Wk example','01/05/2032','Example closure'],
        5:['','','','','','','01/05/2032']})
    dashboard = sheet('📅 Dashboard',{6:['Course','Mon 01/05','Tue 01/06','Wed 01/07','Thu 01/08','Fri 01/09'],
        7:['🌿 Biology',{'formattedValue':'Plan\n📝 Note','userEnteredValue':{'formulaValue':'=INDEX(...)'}},''],
        8:['🫀 A&P','—','Example school closure'],9:['🔬 Forensics','Example override','Extra meeting']})
    courses=[]
    for key,title in day.COURSES:
        first,second=('Tue','Thu') if key=='anatomy' else ('Mon','Wed')
        headers=['Wk','Dates & closures','Topic',first+' — plan','note',second+' — plan','note','Fri — plan','note','To-do','Prep','Student','Teacher','Folder','Other','_studentURL','_teacherURL','_folderURL','_extraURL','📤 Turn it in','_turnInURL']
        row=['Wk example','Example closure','Fictional topic','Plan','Note','Second plan','Second note','Does not meet','Friday note','To-do','Prep','','','','',
            'https://docs.google.com/presentation/d/FICTIONALDECK1234567890/copy','https://docs.google.com/presentation/d/FICTIONALDECK1234567890/edit','https://example.com/folder','','Turn it in',
            'https://docs.google.com/forms/d/e/FICTIONALFORM123456789012345/viewform?usp=pp_url&entry.123=Fictional+assignment']
        courses.append(sheet(title,{3:headers,4:row}))
    mapping={'sheets':[weeks,dashboard]}
    data={'sheets':courses}
    metadata={'sheets':[{'properties':s['properties']} for s in [weeks,dashboard]+courses]}
    return metadata,mapping,data


class DatedTests(unittest.TestCase):
    def run_day(self, selected='2032-01-05', data=None, config=None):
        reader=Mock();reader.read.side_effect=data or fixtures()
        with patch.object(day,'authenticated_reader',return_value=(reader,'fictional')):
            result=day.read_day('signed',selected,config)
        for call in reader.read.call_args_list[1:]:
            validate_ranges(call.args[1])
        return result

    def test_dashboard_override_and_weekly_notes_remain_separate(self):
        result=self.run_day()
        bio,forensics,anatomy=result['lessons']
        self.assertEqual(bio['plan'],'Plan')
        self.assertEqual(bio['notes'],'Note')
        self.assertEqual(forensics['plan'],'Example override')
        self.assertEqual(forensics['weekly_plan'],'Plan')
        self.assertEqual(anatomy['plan'],'')
        self.assertTrue(bio['teacher_slides'].endswith('/edit'))
        self.assertTrue(bio['links'][0]['url'].endswith('/copy'))
        self.assertTrue(bio['turnin_form'].startswith('https://docs.google.com/forms/'))
        self.assertEqual(bio['assignment_label'],'Fictional assignment')
        self.assertFalse(result['save_enabled'])
        self.assertFalse(result['run_enabled'])

    def test_missing_dashboard_tab_reads_weekly_tabs(self):
        meta,mapping,data=fixtures()
        meta['sheets']=[s for s in meta['sheets'] if s['properties']['title']!='📅 Dashboard']
        mapping['sheets']=[s for s in mapping['sheets'] if s['properties']['title']!='📅 Dashboard']
        reader=Mock();reader.read.side_effect=(meta,mapping,data)
        with patch.object(day,'authenticated_reader',return_value=(reader,'fictional')):
            result=day.read_day('signed','2032-01-05')
        self.assertEqual(reader.read.call_args_list[1].args[1],("'🗂 Weeks'!A2:G43",))
        self.assertEqual([l['plan'] for l in result['lessons']],['Plan','Plan',''])
        self.assertEqual(result['lessons'][1]['origin'],'Weekly Agenda')

    def test_stale_dashboard_tab_is_ignored(self):
        meta,mapping,data=fixtures()
        mapping['sheets'][1]['data'][0]['rowData'][0]['values'][1]={'formattedValue':'Mon 12/29'}
        result=self.run_day(data=(meta,mapping,data))
        self.assertEqual(result['lessons'][1]['plan'],'Plan')
        self.assertEqual(result['lessons'][1]['origin'],'Weekly Agenda')

    def test_extra_day_and_closure_preserved(self):
        result=self.run_day('2032-01-06')
        self.assertEqual(result['lessons'][1]['plan'],'Extra meeting')
        self.assertEqual(result['lessons'][2]['plan'],'Example school closure')

    def test_friday_text_preserved_no_inferred_attendance(self):
        result=self.run_day('2032-01-09')
        self.assertEqual(result['lessons'][0]['plan'],'Does not meet')

    def test_out_of_week_does_not_reuse_current_dashboard(self):
        with self.assertRaisesRegex(day.ConnectionError,'date_not_mapped'):
            self.run_day('2032-01-12')

    def test_invalid_date_does_not_authenticate(self):
        with patch.object(day,'authenticated_reader') as auth:
            for selected in ('2032-02-30','20320105',None,'2032-01-05&range=A1'):
                with self.assertRaises(day.ConnectionError):day.read_day('signed',selected)
            auth.assert_not_called()

    def test_moved_header_and_wrong_week_fail_closed(self):
        for column,new in ((0,'Wrong week'),(16,'https://example.com/deck')):
            meta,mapping,data=fixtures()
            data['sheets'][0]['data'][1]['rowData'][0]['values'][column]['formattedValue']=new
            if column==0:
                with self.assertRaises(day.ConnectionError):self.run_day(data=(meta,mapping,data))
            else:
                self.assertIsNone(self.run_day(data=(meta,mapping,data))['lessons'][0]['teacher_slides'])
        meta,mapping,data=fixtures()
        data['sheets'][0]['data'][0]['rowData'][0]['values'][16]['formattedValue']='Moved'
        with self.assertRaises(day.ConnectionError):self.run_day(data=(meta,mapping,data))

    def test_unauthorized_head_post_and_query_gates(self):
        with patch.dict('os.environ',CONFIG,clear=True),patch('school_workflow_hub.adapters.hosted_agenda.service_token') as token:
            result=request('/api/agenda-day',QUERY_STRING='date=2032-01-05',HTTP_X_GOOG_AUTHENTICATED_USER_EMAIL='teacher@example.com')
            self.assertEqual(result['status'],'403 Forbidden');token.assert_not_called()
        with patch.object(day,'read_day') as read:
            request('/api/agenda-day','HEAD');request('/api/agenda-day','POST')
            for query in ('','date=2032-01-05&date=2032-01-06','date=2032-01-05&source=other'):
                self.assertEqual(request('/api/agenda-day',QUERY_STRING=query)['status'],'400 Bad Request')
            read.assert_not_called()

    def test_backend_exception_is_redacted(self):
        with patch.object(day,'read_day',side_effect=ValueError('PRIVATE CONTENT')):
            result=request('/api/agenda-day',QUERY_STRING='date=2032-01-05')
            self.assertNotIn(b'PRIVATE',result['body'])
            self.assertEqual(result['headers']['Cache-Control'],'no-store')

class TurnInMapTests(unittest.TestCase):
    run_day = DatedTests.run_day
    def test_matching_map_supplies_folder_without_claiming_submission_evidence(self):
        meta,mapping,data=fixtures()
        source=sheet('🗂 TurnIn Map',{1:['assignment key','target folder id','course','week'],2:['Fictional assignment','FICTIONAL_FOLDER_123456789','🌿 Biology','Wk example']})
        meta['sheets'].append({'properties':source['properties']});data['sheets'].append(source)
        result=self.run_day(data=(meta,mapping,data))
        self.assertEqual(result['lessons'][0]['assignment_label'],'Fictional assignment')
        self.assertEqual(result['lessons'][0]['turnin_folder'],'https://drive.google.com/drive/folders/FICTIONAL_FOLDER_123456789')
        self.assertIsNotNone(result['lessons'][0]['turnin_form'])
        self.assertIsNone(result['lessons'][1]['turnin_folder'])
        self.assertNotIn('submitted',result)

    def test_assignment_specific_form_needs_no_prefilled_assignment_field(self):
        meta,mapping,data=fixtures()
        source=sheet('🗂 TurnIn Map',{1:['assignment key','target folder id','course','week'],
            2:['Fictional assignment','FICTIONAL_FOLDER_123456789','🌿 Biology','Wk example']})
        meta['sheets'].append({'properties':source['properties']});data['sheets'].append(source)
        row=data['sheets'][0]['data'][1]['rowData'][0]['values']
        row[20]['formattedValue']='https://docs.google.com/forms/d/FICTIONALFORM123456789012345/viewform'
        result=self.run_day(data=(meta,mapping,data))
        self.assertEqual(result['lessons'][0]['turnin_form_id'],
                         'FICTIONALFORM123456789012345')
        self.assertEqual(result['lessons'][0]['assignment_label'],'Fictional assignment')
        self.assertIsNotNone(result['lessons'][0]['turnin_form'])

    def test_duplicate_or_unsafe_folder_is_not_offered(self):
        for rows in ({2:['Lesson','invalid/path','🌿 Biology','Wk example']},{2:['Lesson','FICTIONAL_FOLDER_123456789','🌿 Biology','Wk example'],3:['Other lesson','FICTIONAL_FOLDER_123456789','🌿 Biology','Wk example']}):
            meta,mapping,data=fixtures()
            source=sheet('🗂 TurnIn Map',{1:['assignment key','target folder id','course','week'],**rows})
            meta['sheets'].append({'properties':source['properties']});data['sheets'].append(source)
            self.assertIsNone(self.run_day(data=(meta,mapping,data))['lessons'][0]['turnin_folder'])


class MultipleFormDayTests(unittest.TestCase):
    run_day = DatedTests.run_day

    def inputs(self, ids=None):
        from tests.test_shared_intake_catalog import PRIMARY, ADDITIONAL, KEY_A, KEY_B, FORM_A, FORM_B, named_forms, form_url, url
        meta, mapping, data = fixtures()
        course = next(s for s in data['sheets'] if s['properties']['title'] == '🔬 Forensics')
        row = course['data'][1]['rowData'][0]['values']
        row[15] = {'formattedValue': url(PRIMARY)}
        row[14] = {'formattedValue': url(ADDITIONAL)}
        row[19] = named_forms(ids or (FORM_A, FORM_B))
        row[20] = {'formattedValue': form_url(FORM_A)}
        source = sheet('🗂 TurnIn Map', {1: ['assignment key', 'target folder id', 'course', 'week'],
            2: ['Combined weekly display', 'z' * 24, '🔬 Forensics', 'Wk example']})
        meta['sheets'].append({'properties': source['properties']})
        data['sheets'].append(source)
        config = {'HUB_INTAKE_DECK_ASSIGNMENTS': {PRIMARY: KEY_A, ADDITIONAL: KEY_B},
                  'HUB_ASSIGNMENT_FORM_IDS': {KEY_A: FORM_A, KEY_B: FORM_B}}
        return (meta, mapping, data), config, row

    def test_two_named_links_keep_primary_compatibility_and_assignment_keys(self):
        from tests.test_shared_intake_catalog import KEY_A, KEY_B, FORM_A, FORM_B, form_url
        for order in ((FORM_A, FORM_B), (FORM_B, FORM_A)):
            with self.subTest(order=order):
                data, config, _ = self.inputs(order)
                lesson = self.run_day(data=data, config=config)['lessons'][1]
                self.assertEqual(lesson['turnin_form'], form_url(FORM_A))
                self.assertEqual(lesson['turnin_form_id'], FORM_A)
                self.assertEqual([f['assignment_key'] for f in lesson['turnin_forms']], [KEY_A, KEY_B])
                self.assertEqual([f['form_id'] for f in lesson['turnin_forms']], [FORM_A, FORM_B])
                self.assertEqual([f['assignment_title'] for f in lesson['turnin_forms']],
                                 ['Observation — submit', 'Reasoning — submit'])
                self.assertEqual([f['primary'] for f in lesson['turnin_forms']], [True, False])
                self.assertEqual(lesson['turnin_forms_review_reason'], '')

    def test_three_named_forms_follow_exact_sources_and_keep_primary_scalar(self):
        from tests.test_shared_intake_catalog import (ADDITIONAL, THIRD, KEY_A, KEY_B, KEY_C,
            FORM_A, FORM_B, FORM_C, form_url, named_forms, named_decks)
        data, config, row = self.inputs()
        row[14] = named_decks((THIRD, ADDITIONAL))
        row[19] = named_forms((FORM_C, FORM_B, FORM_A))
        config['HUB_INTAKE_DECK_ASSIGNMENTS'][THIRD] = KEY_C
        config['HUB_ASSIGNMENT_FORM_IDS'][KEY_C] = FORM_C
        lesson = self.run_day(data=data, config=config)['lessons'][1]
        self.assertEqual(lesson['turnin_form'], form_url(FORM_A))
        self.assertEqual([f['assignment_key'] for f in lesson['turnin_forms']], [KEY_A, KEY_B, KEY_C])
        self.assertEqual([f['form_id'] for f in lesson['turnin_forms']], [FORM_A, FORM_B, FORM_C])
        self.assertEqual([f['primary'] for f in lesson['turnin_forms']], [True, False, False])
        self.assertEqual(len({f['assignment_title'] for f in lesson['turnin_forms']}), 3)
        self.assertEqual(lesson['turnin_forms_review_reason'], '')

    def test_computed_primary_and_additional_links_still_display_named_forms(self):
        from tests.test_shared_intake_catalog import PRIMARY, ADDITIONAL, KEY_A, KEY_B, url
        data, config, row = self.inputs()
        row[15] = {'formattedValue': url(PRIMARY), 'hyperlink': url(PRIMARY), 'userEnteredValue': {
            'formulaValue': '=IF($Q4="","",IFERROR(REGEXEXTRACT($Q4,"https://docs.google.com/presentation/d/[^/]+")&"/copy",""))'}}
        row[14] = {'formattedValue': 'Activity ↗', 'hyperlink': url(ADDITIONAL), 'userEnteredValue': {
            'formulaValue': '=IF($S4="","—",HYPERLINK($S4,"Activity ↗"))'}}
        lesson = self.run_day(data=data, config=config)['lessons'][1]
        self.assertEqual([form['assignment_key'] for form in lesson['turnin_forms']], [KEY_A, KEY_B])
        self.assertEqual(lesson['turnin_forms_review_reason'], '')

    def test_unbound_third_source_is_held_even_if_its_form_has_a_name(self):
        from tests.test_shared_intake_catalog import ADDITIONAL, THIRD, FORM_A, FORM_B, FORM_C, named_forms, named_decks
        data, config, row = self.inputs()
        row[14] = named_decks((ADDITIONAL, THIRD))
        row[19] = named_forms((FORM_A, FORM_B, FORM_C))
        lesson = self.run_day(data=data, config=config)['lessons'][1]
        self.assertEqual(lesson['turnin_forms'], [])
        self.assertEqual(lesson['turnin_forms_review_reason'], 'assignment_form_source_links_need_review')

    def test_missing_extra_wrong_primary_reused_or_unconfigured_links_are_held(self):
        from tests.test_shared_intake_catalog import KEY_A, KEY_B, FORM_A, FORM_B, form_url, named_forms
        for problem in ('missing', 'extra', 'wrong_primary', 'reused', 'unconfigured'):
            with self.subTest(problem=problem):
                data, config, row = self.inputs()
                if problem == 'missing':
                    row[19] = named_forms((FORM_A,))
                elif problem == 'extra':
                    row[19] = named_forms((FORM_A, FORM_B, 'h' * 24))
                elif problem == 'wrong_primary':
                    row[20] = {'formattedValue': form_url(FORM_B)}
                elif problem == 'reused':
                    config['HUB_ASSIGNMENT_FORM_IDS'][KEY_B] = FORM_A
                else:
                    config['HUB_INTAKE_DECK_ASSIGNMENTS'] = {}
                lesson = self.run_day(data=data, config=config)['lessons'][1]
                self.assertEqual(lesson['turnin_forms'], [])
                self.assertTrue(lesson['turnin_forms_review_reason'])

    def test_existing_single_form_is_unchanged_and_added_to_link_array(self):
        lesson = self.run_day()['lessons'][0]
        self.assertEqual(len(lesson['turnin_forms']), 1)
        self.assertEqual(lesson['turnin_forms'][0]['url'], lesson['turnin_form'])
        self.assertEqual(lesson['turnin_forms'][0]['assignment_title'], lesson['assignment_label'])

    def test_label_text_cannot_change_assignment_binding(self):
        from tests.test_shared_intake_catalog import KEY_A, KEY_B
        data, config, row = self.inputs()
        row[19]['formattedValue'] = row[19]['formattedValue'].replace('Observation', 'Anything at')
        lesson = self.run_day(data=data, config=config)['lessons'][1]
        self.assertEqual([f['assignment_key'] for f in lesson['turnin_forms']], [KEY_A, KEY_B])
