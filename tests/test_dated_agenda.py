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
            'https://docs.google.com/presentation/d/FICTIONAL/copy','https://docs.google.com/presentation/d/FICTIONAL/edit','https://example.com/folder','','',
            'https://docs.google.com/forms/d/e/FICTIONALPUBLICFORM12345/viewform?usp=pp_url&entry.123=Fictional+assignment']
        courses.append(sheet(title,{3:headers,4:row}))
    mapping={'sheets':[weeks,dashboard]}
    data={'sheets':courses}
    metadata={'sheets':[{'properties':s['properties']} for s in [weeks,dashboard]+courses]}
    return metadata,mapping,data


class DatedTests(unittest.TestCase):
    def run_day(self, selected='2032-01-05', data=None):
        reader=Mock();reader.read.side_effect=data or fixtures()
        with patch.object(day,'authenticated_reader',return_value=(reader,'fictional')):
            result=day.read_day('signed',selected)
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
        self.assertTrue(bio['turnin_form'].startswith('https://docs.google.com/forms/'))
        self.assertTrue(bio['links'][0]['url'].endswith('/copy'))
        self.assertFalse(result['save_enabled'])
        self.assertFalse(result['run_enabled'])

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
        self.assertIsNone(result['lessons'][1]['turnin_folder'])
        self.assertNotIn('submitted',result)

    def test_duplicate_or_unsafe_folder_is_not_offered(self):
        for rows in ({2:['Lesson','invalid/path','🌿 Biology','Wk example']},{2:['Lesson','FICTIONAL_FOLDER_123456789','🌿 Biology','Wk example'],3:['Other lesson','FICTIONAL_FOLDER_123456789','🌿 Biology','Wk example']}):
            meta,mapping,data=fixtures()
            source=sheet('🗂 TurnIn Map',{1:['assignment key','target folder id','course','week'],**rows})
            meta['sheets'].append({'properties':source['properties']});data['sheets'].append(source)
            self.assertIsNone(self.run_day(data=(meta,mapping,data))['lessons'][0]['turnin_folder'])
