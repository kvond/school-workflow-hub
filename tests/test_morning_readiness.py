"""No assignment zero/entry bypass around copy readiness or a teacher hold."""
from copy import deepcopy
from datetime import date
import io,json
from unittest.mock import Mock,patch
import unittest
from hub_v2 import app,audit,completion,completion_holds as holds,google,morning
from school_workflow_hub.core.cloud_state import StoreUnavailable
from tests.test_cloud_state import MemoryCAS
from test_hub_v2 import fixture
from test_completion_readiness import source,FORM,TEMPLATE,STUDENT,work_result
import test_hub_submission_policy as intake_fixture

YEAR='2026-27';WEEK='Wk 04';CLASS='forensics'


def settings(cls=CLASS,year=YEAR,week=WEEK,fid=FORM):
    return {holds.CONFIG:json.dumps([{'school_year':year,'class_key':cls,'week':week,'form_id':fid}])}


def held_record(agenda):
    ident=holds.hold_key(agenda.spreadsheet_id,YEAR,CLASS,WEEK,FORM)
    return {'schema':holds.SCHEMA,'record_key':ident,'agenda_id':agenda.spreadsheet_id,'school_year':YEAR,
            'class_key':CLASS,'week':WEEK,'form_id':FORM,'status':'held','reason':'The distributed student copies are unmarked.',
            'teacher_action':'Embed codes in those existing copies, preserve answers, then rerun grading.',
            'updated_at':'2026-09-23T10:00:00Z'}


class TeacherHoldTests(unittest.TestCase):
    def setUp(self):
        self.agenda=fixture();self.port=MemoryCAS();self.value=held_record(self.agenda)
        self.ident=self.value['record_key'];self.port.commit({self.ident:(self.value,None)})

    def check(self,config=None,agenda=None):
        with patch.object(holds,'configured_store',return_value=self.port):
            return holds.check(config or settings(),agenda or self.agenda,CLASS,WEEK,FORM)

    def test_explicit_copy_hold_is_not_cleared_by_a_ready_linked_deck(self):
        with patch.object(holds,'configured_store',return_value=self.port),patch.object(completion,'_template') as template:
            result=completion.assignment_readiness('read',settings(),self.agenda,CLASS,WEEK,FORM)
        self.assertEqual(result['status'],'NOT_READY');self.assertEqual(result['code'],'teacher_readiness_hold')
        self.assertEqual(result['label'],'Needs embedded codes')
        self.assertIn('Forensics · Wk 04',result['problem']);self.assertIn('preserve answers',result['action'])
        template.assert_not_called()
        self.assertEqual(self.port.rows[self.ident].value,self.value)

    def test_other_year_course_week_and_form_do_not_consume_this_hold(self):
        for config in (settings(year='2025-26',week='Retired label'),settings(cls='biology'),
                       settings(week='Wk 03'),settings(fid='FICTIONAL_OTHER_FORM_1234')):
            with patch.object(holds,'configured_store') as store:
                result=holds.check(config,self.agenda,CLASS,WEEK,FORM)
            self.assertEqual(result['status'],'READY');store.assert_not_called()

    def test_unregistered_operation_never_contacts_cas(self):
        with patch.object(holds,'configured_store') as store:
            self.assertEqual(holds.check({},self.agenda,CLASS,WEEK,FORM)['status'],'READY')
        store.assert_not_called()

    def test_missing_unavailable_or_cross_assignment_decision_fails_closed(self):
        self.port.rows.clear();missing=self.check();self.assertEqual(missing['status'],'NOT_READY')
        self.assertEqual(missing['label'],'Needs check')
        self.assertNotIn('Needs embedded codes',missing['problem'])
        changed={**self.value,'form_id':'FICTIONAL_OTHER_FORM_1234'}
        self.port.commit({self.ident:(changed,None)});self.assertEqual(self.check()['status'],'NOT_READY')
        with patch.object(holds,'configured_store',side_effect=StoreUnavailable('Unavailable')):
            self.assertEqual(holds.check(settings(),self.agenda,CLASS,WEEK,FORM)['status'],'NOT_READY')

    def test_automatic_source_ready_or_empty_clearance_never_releases_hold(self):
        for value in ({**self.value,'status':'source_ready'}, {**self.value,'status':'cleared'},
                      {**self.value,'status':'cleared','clearance':{'basis':'teacher_confirmed_distributed_copies_coded',
                       'teacher_confirmed_at':'2026-09-23T10:00:00Z','verified_at':'2026-09-23T10:01:00Z','copies':[]}}):
            self.port.rows[self.ident].value.clear();self.port.rows[self.ident].value.update(value)
            self.assertEqual(self.check()['status'],'NOT_READY')

    def test_explicit_teacher_confirmation_and_nonempty_verified_copy_revision_can_clear(self):
        self.port.rows[self.ident].value.update(status='cleared',clearance={
            'basis':'teacher_confirmed_distributed_copies_coded','teacher_confirmed_at':'2026-09-23T10:00:00Z',
            'verified_at':'2026-09-23T10:01:00Z','copies':[{'file_id':'FICTIONAL_CODED_COPY_1234','version':'3','status':'READY'}]})
        result=self.check();self.assertEqual(result['status'],'READY');self.assertTrue(result['clearance_verified'])
        self.port.rows[self.ident].value['clearance']['copies'][0]['status']='NOT_READY'
        self.assertEqual(self.check()['status'],'NOT_READY')

    def test_explicit_count_found_codes_decision_can_clear_only_non_exception_scope(self):
        self.port.rows[self.ident].value.update(status='cleared',clearance={
            'basis':'teacher_confirmed_count_found_codes','teacher_confirmed_at':'2026-10-10T10:00:00Z',
            'verified_at':'2026-10-10T10:01:00Z','copies':[{'file_id':'FICTIONAL_READABLE_COPY_1234','version':'4','status':'READY'}]})
        result=self.check()
        self.assertEqual(result['status'],'READY');self.assertTrue(result['clearance_verified'])
        with patch.object(completion,'student_coding_required',return_value=True):
            self.assertEqual(self.check()['status'],'NOT_READY')
        self.port.rows[self.ident].value['clearance']['copies'][0]['version']=''
        self.assertEqual(self.check()['status'],'NOT_READY')

    def test_malformed_or_duplicate_active_registration_fails_closed(self):
        for config in ({holds.CONFIG:'{broken'}, {holds.CONFIG:json.dumps(json.loads(settings()[holds.CONFIG])*2)},
                       settings(week='Cycle 04')):
            self.assertEqual(self.check(config)['status'],'NOT_READY')


class WholeAssignmentTests(unittest.TestCase):
    def form(self):
        form=deepcopy(work_result()['lessons'][0]['forms'][0]);form['students'][0].update(
            first='Ana',last='Rivera',section='A2 Forensics',matched=True)
        form.update(missing=[{'first':'Ben','last':'Vale','section':'A2 Forensics','matched':True}],
                    late=[],excused=[],elsewhere=[],problem=None,
                    window={'from':'2026-09-21T00:00:00Z','at':'2026-12-30T00:00:00Z'})
        return form

    def make(self,form,ready,scored):
        found={'lessons':[{'topic':'Scene photography','forms':[form]}],'problems':[]}
        scorer=Mock(return_value=scored)
        config={'HUB_IC_ASSIGNMENT_MAP':json.dumps([{'class_key':CLASS,'form_id':FORM,'targets':[]}])}
        with patch.object(completion,'assignment_readiness',return_value=ready), \
             patch.object(morning.ledger,'read',return_value={'configured':True}),patch.object(morning,'rows',wraps=morning.rows) as rows:
            result=morning.make_list('read',config,fixture(),CLASS,WEEK,FORM,Mock(return_value=found),scorer)
        return result,scorer,rows

    def test_empty_form_cannot_make_missing_zeros_when_linked_codes_not_ready(self):
        form=self.form();form['students']=[]
        result,scorer,rows=self.make(form,{'status':'NOT_READY','problem':'Inventory is missing.','action':'Embed codes then rerun.'},[])
        self.assertEqual(result['code'],'coverage_incomplete');self.assertNotIn('list',result)
        scorer.assert_not_called();rows.assert_not_called()

    def test_one_unmarked_submission_blocks_every_other_score_and_missing_zero(self):
        form=self.form();blocked={'code':'deck_not_ready','problem':'This submitted copy is unmarked.','action':'Embed codes then rerun.'}
        result,scorer,rows=self.make(form,{'status':'READY'},[(form['students'][0],blocked)])
        self.assertEqual(result['code'],'coverage_incomplete');self.assertNotIn('list',result)
        rows.assert_not_called();scorer.assert_called_once()

    def test_valid_ready_assignment_retains_existing_missing_zero_rule(self):
        form=self.form()
        result,_,_=self.make(form,{'status':'READY'},[(form['students'][0],{'notes_score':8,'revision_score':None})])
        self.assertEqual(result['code'],'ready')
        self.assertEqual([r['kind'] for r in result['list']['rows']],['score','zero'])

    def test_work_serializes_guard_without_altering_original_form_provenance(self):
        hold={'status':'NOT_READY','problem':'Copies need embedded codes.','action':'Embed preserving answers then rerun.'}
        with patch.object(completion,'assignment_readiness',return_value=hold):
            found=intake_fixture.TeacherIntakeTests().scan(intake_fixture.route_config(),[intake_fixture.response('Types of Evidence - Ann Avery')])
        for form in found['lessons'][0]['forms']:
            self.assertEqual(form['grade_readiness'],hold)
        uploaded=found['lessons'][0]['forms'][0]['students'][0]['files'][0]
        self.assertEqual(uploaded['source_form_id'],'FICTIONAL_INVEST_FORM_123456')
        self.assertEqual(uploaded['source_submitted_at'],'2026-09-22T14:00:00Z')

    def test_read_only_copy_gate_does_not_calculate_or_cache_scores(self):
        form=self.form();history=deepcopy(completion._results)
        with patch.object(completion,'_template',return_value=({'version':'1'},source(header='Unmarked',codes=False))), \
             patch.object(completion,'assess') as assess:
            result=completion.submitted_readiness('read',form)
        self.assertEqual(result['status'],'NOT_READY');assess.assert_not_called()
        self.assertEqual(completion._results,history)

    def test_ic_receipt_backend_rejects_teacher_hold_and_unmarked_copy_even_with_forged_ready_ui(self):
        for linked,copy in (({'status':'NOT_READY','problem':'Explicit teacher hold.'},None),
                            ({'status':'READY'},{'status':'NOT_READY','problem':'Unmarked submitted copy.'})):
            data=json.dumps({'class':CLASS,'week':WEEK,'form':FORM,'kind':'ic'}).encode()
            env={'REQUEST_METHOD':'POST','PATH_INFO':'/v2/api/record','HTTP_ORIGIN':'https://hub.fictional.example',
                 'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(data)),'wsgi.input':io.BytesIO(data)}
            status=[]
            with patch.object(google,'verify_teacher',return_value='teacher@fictional.example'), \
                 patch.object(google,'service_token',return_value='read') as token,patch.object(app,'load_agenda',return_value=fixture()), \
                 patch.object(app,'_form_context',return_value=({},self.form(),{})), \
                 patch.object(completion,'assignment_readiness',return_value=linked), \
                 patch.object(completion,'submitted_readiness',return_value=copy) as copies,patch.object(morning.ledger,'record') as record:
                body=b''.join(app.application(env,lambda s,h:status.append(s),{'HUB_STATE_PROBE_ORIGINS':'https://hub.fictional.example'}))
            self.assertEqual(status,['409 Conflict']);self.assertEqual(json.loads(body)['code'],'coverage_incomplete')
            record.assert_not_called()
            self.assertFalse(any(c.kwargs.get('write') for c in token.call_args_list))
            if copy is None:copies.assert_not_called()

    def test_today_preserves_ready_source_counts_but_displays_explicit_copy_hold(self):
        teacher_hold={'status':'NOT_READY','held':True,'problem':'Distributed copies are unmarked.','action':'Embed copies preserving answers, then rerun.'}
        with patch.object(audit,'_titles',return_value={}),patch.object(audit,'_published',return_value={}), \
             patch.object(completion,'lesson_sources',return_value={'decks':[{'id':TEMPLATE}], 'stale_links':[], 'folders':[], 'problem':None}), \
             patch.object(audit,'date') as clock,patch.object(audit,'_release_deck',return_value={'status':'READY','counts':{'NOTES':3},'issues':[]}), \
             patch.object(holds,'check',return_value=teacher_hold):
            clock.today.return_value=date(2026,9,23)
            result=audit.check('read',fixture(),CLASS,{})
        release=next(r['release'] for r in result['rows'] if r['week']==WEEK)
        self.assertEqual(release['status'],'NOT_READY');self.assertEqual(release['linked_source_status'],'READY')
        self.assertEqual(release['counts'],{'NOTES':3});self.assertIn('distributed',release['problem'].lower())
        self.assertIn('rerun',release['action'])


if __name__=='__main__':unittest.main()
