"""Explicit fictional cross-week intake; no providers, grades or chronology changes."""
import copy
import json
import unittest
from unittest.mock import patch
from hub_v2 import work, google, completion
from test_native_teacher_source import source as coded_source

SOURCE = 'FICTIONAL_REASONING_FORM123'
TARGET = 'FICTIONAL_EYEWITNESS_FORM123'
FILE = 'FICTIONAL_ORIGINAL_FILE123'
STAMP = '2026-09-27T05:49:39Z'
PERSON = {'first':'Ann','last':'Avery','section':'A2 Forensics'}
TITLE = 'Eyewitness Accounts'

class Agenda:
    def __init__(self):
        self.classes = {c:{'name':c.title(),'layout':{'rows':[(1,'Wk 03'),(2,'Wk 04')]}}
                        for c in ('forensics','biology')}
        self.weeks = [{'label':'Wk 03','monday':'2026-09-14'},
                      {'label':'Wk 04','monday':'2026-09-21'}]
        self.rows = {'Wk 03':[self.lesson(SOURCE,'Inductive and Deductive Reasoning')],
                     'Wk 04':[self.lesson(TARGET,TITLE)]}
    @staticmethod
    def lesson(fid,label):
        return {'row':1,'topic':label,'dates':'','plans':[],'materials':[],
                'edit_url':'https://fictional.example/agenda',
                'forms':[{'form_id':fid,'label':label+' — Turn in','url':'https://docs.google.com/forms/d/'+fid+'/edit','problem':None}]}
    def lessons(self,cls,week):
        return copy.deepcopy(self.rows.get(week,[])) if cls=='forensics' else []
    def week_options(self,cls):
        return self.weeks

def declaration(**changes):
    return {'school_year':'2026-27','class_key':'forensics','source_week':'Wk 03',
            'week':'Wk 04','source_form_id':SOURCE,'assignment_form_id':TARGET,**changes}

def config(*values):
    return {'HUB_ACCEPTED_FORM_ROUTES':json.dumps(values or [declaration()]),
            'HUB_SCHOOL_EMAIL_DOMAINS':'school.example'}

def submission(*names):
    return {'source':'form','event':True,'key':'FICTIONAL_RESPONSE123','email':'s.ann.avery@school.example',
            'typed':[],'submitted_at':STAMP,'files':[{'id':FILE+str(i),'name':n,'mime':completion.SLIDES,
            'trashed':False,'problem':None,'source_form_id':SOURCE,'source_response_id':'FICTIONAL_RESPONSE123',
            'source_submitted_at':STAMP} for i,n in enumerate(names or [TITLE+' - Ann Avery'])]}

def entries(s):
    return [{'current':True,'week':'Wk 03','label':'Inductive and Deductive Reasoning — Turn in',
             'form':{'form_id':SOURCE,'problem':None},'base':'inductive and deductive reasoning',
             'words':{'inductive','deductive','reasoning'},'found':[s]},
            {'current':False,'week':'Wk 04','label':TITLE+' — Turn in','form_id':TARGET,
             'class_key':'forensics','school_year':'2026-27','problem':None,'base':'eyewitness accounts',
             'words':{'eyewitness','accounts'},'teacher_signature':{work.deck_stem(TITLE)},'found':[]}]

class CrossweekAcceptedIntakeTests(unittest.TestCase):
    def routes(self,a=None,values=None,week='Wk 03'):
        a=a or Agenda()
        forms=[f for l in a.lessons('forensics',week) for f in l['forms']]
        return work.accepted_form_routes(config(*(values or [declaration()])),a,'forensics',week,forms)
    def accepted(self,s=None,e=None):
        s=s or submission();e=e or entries(s)
        return work._accepted_route_move({(SOURCE,TARGET)},e,0,1,s,[PERSON],['school.example'],'s.',class_key='forensics',school_year='2026-27',config=config())
    def test_explicit_route_is_available_only_at_its_two_exact_weeks(self):
        for week in ('Wk 03','Wk 04'):
            self.assertEqual(self.routes(week=week),({(SOURCE,TARGET)},None))
        s=submission();before=copy.deepcopy(s)
        self.assertTrue(self.accepted(s))
        self.assertEqual(s,before)
        self.assertEqual(s['files'][0]['source_form_id'],SOURCE)
    def test_cross_year_class_unknown_week_and_unknown_schema_do_not_accept(self):
        for changes in ({'school_year':'2025-26'},{'class_key':'biology'}):
            self.assertEqual(self.routes(values=[declaration(**changes)])[0],set())
        for changes in ({'source_week':'Wk 99'},{'week':'Wk 99'},{'source_week':None},{'foreign':True}):
            self.assertTrue(self.routes(values=[declaration(**changes)])[1])
        a=Agenda();a.weeks[0]['monday']='2025-09-14'
        self.assertTrue(self.routes(a,week='Wk 04')[1])
    def test_duplicate_or_foreign_actual_form_scopes_and_conflicting_routes_hold(self):
        for change in ('same_week','other_class'):
            a=Agenda()
            if change=='same_week':a.rows['Wk 03'][0]['forms'].append(copy.deepcopy(a.rows['Wk 03'][0]['forms'][0]))
            else:
                original=a.lessons
                a.lessons=lambda cls,week:original('forensics',week) if cls=='biology' else original(cls,week)
            self.assertTrue(self.routes(a)[1])
        self.assertTrue(self.routes(values=[declaration(),declaration()])[1])
        a=Agenda();a.rows['Wk 04'][0]['forms'].append({'form_id':'FICTIONAL_SECOND_TARGET123','label':'Other'})
        self.assertTrue(self.routes(a,[declaration(),declaration(assignment_form_id='FICTIONAL_SECOND_TARGET123')])[1])
    def test_same_class_continuation_is_not_an_implicit_route_week_alias(self):
        a=Agenda();a.weeks.append({'label':'Wk 05','monday':'2026-09-28'})
        a.classes['forensics']['layout']['rows'].append((3,'Wk 05'))
        a.rows['Wk 05']=copy.deepcopy(a.rows['Wk 04'])
        self.assertEqual(self.routes(a)[0],{(SOURCE,TARGET)})
        self.assertEqual(self.routes(a,week='Wk 05'),(set(),None))
        s=submission();e=entries(s);e[1]['week']='Wk 05'
        self.assertFalse(self.accepted(s,e))
    def test_partial_weak_mixed_and_ambiguous_file_matches_do_not_accept(self):
        for names in [('Eyewitness',),(TITLE,'Foreign Cycle Deck'),('My work',)]:
            s=submission(*names);self.assertFalse(self.accepted(s))
        s=submission();e=entries(s);e.append({**copy.deepcopy(e[1]),'form_id':'FICTIONAL_CONFLICT_FORM123'})
        self.assertFalse(self.accepted(s,e))
        s=submission(TITLE,TITLE);s['files'][1]['id']=s['files'][0]['id'];self.assertFalse(self.accepted(s))
    def test_authentic_source_event_identity_and_readability_are_required(self):
        for key,value in [('source','folder'),('event',False),('key',''),('submitted_at','bad'),('email','foreign@other.example')]:
            s=submission();s[key]=value;self.assertFalse(self.accepted(s))
        for key,value in [('source_form_id',TARGET),('source_response_id','OTHER'),('source_submitted_at','2026-10-01T00:00:00Z'),
                          ('problem','Cannot read'),('trashed',True),('id','')]:
            s=submission();s['files'][0][key]=value;self.assertFalse(self.accepted(s))
        s=submission();e=entries(s);e[1]['teacher_source_binding']={'exact':'source'}
        self.assertFalse(self.accepted(s,e))
        e[1]['teacher_source_verified']=True
        self.assertTrue(self.accepted(s,e))
        e[1]['problem']='Teacher source failed';self.assertFalse(self.accepted(s,e))
        for key,value in [('class_key','biology'),('school_year','2025-26')]:
            e=entries(s);e[1][key]=value;self.assertFalse(self.accepted(s,e))
    def test_real_move_keeps_original_event_in_target_and_suppresses_source_only(self):
        a=Agenda();s=submission();raw={'responseId':s['key'],'respondentEmail':s['email'],'lastSubmittedTime':STAMP,
                                     'answers':{'u':{'fileUploadAnswers':{'answers':[{'fileId':s['files'][0]['id'],
                                     'fileName':s['files'][0]['name'],'mimeType':completion.SLIDES}]}}}}
        names={SOURCE:'Inductive and Deductive Reasoning Form',TARGET:TITLE+' Form',s['files'][0]['id']:s['files'][0]['name']}
        for current in ('Wk 03','Wk 04'):
            near=entries(s)[1] if current=='Wk 03' else {**entries(s)[0],'current':False,'form_id':SOURCE,
                  'class_key':'forensics','school_year':'2026-27','problem':None,'topic':''}
            if current=='Wk 03':near={**near,'topic':''}
            with patch.object(work.roster,'load',return_value=({'forensics':[PERSON]},[])), \
                 patch.object(google,'form_responses',side_effect=lambda t,f:[raw] if f==SOURCE else []), \
                 patch.object(google,'drive_file',side_effect=lambda t,f,*args:{'id':f,'name':names[f],'mimeType':completion.SLIDES,'trashed':False}), \
                 patch.object(work,'lesson_folders',return_value=({},None)), \
                 patch.object(work,'nearby_forms',return_value=[near]):
                found=work.lesson_work('fictional',config(),a,'forensics',current,review_only=True)
            form=found['lessons'][0]['forms'][0]
            if current=='Wk 03':
                self.assertTrue(form['elsewhere'][0]['accepted_intake'])
            else:
                f=form['students'][0]['files'][0]
                self.assertTrue(f['accepted_intake'])
                self.assertEqual((f['source_form_id'],f['source_response_id'],f['source_submitted_at'],f['assignment_form_id']),
                                 (SOURCE,s['key'],STAMP,TARGET))
    def test_acceptance_does_not_waive_unknown_history_or_mixed_code_cycles(self):
        s=submission();self.assertTrue(self.accepted(s))
        current={**PERSON,'matched':True,'matched_by':'email','email':s['email'],'source':'form',
                 'submitted_at':STAMP,'files':copy.deepcopy(s['files']),'late':False,'earlier':[]}
        current['files'][0].update(accepted_intake=True,assignment_form_id=TARGET,from_folder=False,from_shared=False,other_week=None)
        current['earlier']=[{**current,'source':'folder','matched_by':'file name','email':'',
                            'submitted_at':'2026-09-26T00:00:00Z','files':[{'id':'FICTIONAL_UNKNOWN_PRIOR123','name':'Earlier copy'}]}]
        result=completion.recheck_candidates({'form_id':TARGET,'students':[current]})[0]
        self.assertIn('without a verified original Form event',result['selection_problem'])
        mixed=coded_source();mixed['slides'][1]['units'][-1]['text']='[[DRAFT:OTHER:Q-MASTERY]]'
        self.assertEqual(completion.student_readiness(mixed,require_complete_codes=False)['status'],'NOT_READY')
    def test_same_week_schema_and_route_behavior_remain_unchanged(self):
        a=Agenda();a.rows['Wk 03'][0]['forms'].append(a.rows['Wk 04'][0]['forms'][0])
        old=declaration(week='Wk 03');old.pop('source_week')
        self.assertEqual(self.routes(a,[old]),({(SOURCE,TARGET)},None))
        s={'files':[]};e=entries(s);e[1]['week']='Wk 03'
        self.assertTrue(self.accepted(s,e))

if __name__=='__main__':unittest.main()
