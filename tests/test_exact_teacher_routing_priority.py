"""Fictional exact binding priority, preserving original routing and grade boundaries."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from hub_v2 import completion,work
TITLE='Inductive_Deductive_Forensics_Google_Slides'
STEM=work.deck_stem(TITLE)
class ExactTeacherRoutingPriority(unittest.TestCase):
    def entry(self,label,current=True,bound=False,verified=False,stem=STEM):
        return {'current':current,'week':'Wk 03' if current else 'Wk 02','label':label,
                'base':work.deck_words(label),'words':work.deck_words(label),'found':[],
                'teacher_signature':{stem},'teacher_source_binding':{'teacher_ids':['fictionalExactTeacherID']} if bound else None,
                'teacher_source_verified':verified,'form':{}}
    def original(self,name=TITLE):return {'files':[{'id':'fictionalOriginal','name':'Copy of '+name+' - Fictional Student'}]}
    def test_unique_verified_teacher_beats_nearby_heuristic_tie(self):
        source=self.entry('Observation Skills — Turn In',stem=work.deck_stem('Observation_Deductive_Reasoning_VT_CSI_1_markered'))
        source['found']=[self.original()]
        exact=self.entry('Inductive and Deductive Reasoning — Turn In',bound=True,verified=True)
        near=self.entry('Intro to Forensics — Turn In',current=False)
        self.assertEqual(work.wrong_form_moves([source,exact,near]),[(0,source['found'][0],1)])
        old=deepcopy(exact);old['teacher_source_verified']=False
        self.assertEqual(work.wrong_form_moves([source,old,near]),[])
    def test_current_heuristic_confirmation_cannot_hide_exact_other_teacher(self):
        source=self.entry('Intro to Forensics — Turn In');source['found']=[self.original()]
        exact=self.entry('Reasoning — Turn In',bound=True,verified=True)
        self.assertEqual(work.wrong_form_moves([source,exact]),[(0,source['found'][0],1)])
    def test_conflicting_exact_bindings_and_file_disjoint_claims_do_not_move(self):
        source=self.entry('Unresolved — Turn In',stem='');source['found']=[self.original()]
        a=self.entry('One teacher',bound=True,verified=True);b=self.entry('Other teacher',bound=True,verified=True)
        self.assertEqual(work.wrong_form_moves([source,a,b]),[])
        b['teacher_signature']={work.deck_stem('Distinct_Observation_Skills_Full_Template')}
        source['found'][0]['files'].append({'id':'fictionalOther','name':'Copy of Distinct_Observation_Skills_Full_Template - Fictional Student'})
        self.assertEqual(work.wrong_form_moves([source,a,b]),[])
        source['found'][0]['files'][1]['name']='Unknown Attachment'
        self.assertEqual(work.wrong_form_moves([source,a,b]),[])
    def test_short_unverified_or_missing_binding_cannot_confer_priority(self):
        source=self.entry('Source',stem='');source['found']=[self.original('Evidence')]
        a=self.entry('One',bound=True,verified=True,stem='evidence');b=self.entry('Other',current=False,stem='evidence')
        self.assertEqual(work.wrong_form_moves([source,a,b]),[])
        source['found']=[self.original()];a['teacher_signature']=b['teacher_signature']={STEM}
        for key,value in [('teacher_source_verified',False),('teacher_source_binding',None)]:
            bad=deepcopy(a);bad[key]=value
            self.assertEqual(work.wrong_form_moves([source,bad,b]),[])
    def test_multideck_combined_scopes_and_original_weak_path_unchanged(self):
        source=self.entry('Observation',stem='');source['found']=[self.original()]
        exact=self.entry('Reasoning',bound=True,verified=True);exact['form']['_combined_scope']={'fictional':True}
        self.assertEqual(work.wrong_form_moves([source,exact]),[])
        source['form']['_combined_scope']={'fictional':True};exact['form']={}
        self.assertEqual(work.wrong_form_moves([source,exact]),[])
        weak=self.entry('A source',current=False,stem='');weak['found']=[self.original('Reasoning')]
        target=self.entry('Inductive and Deductive Reasoning — Turn In',stem='')
        held=[];self.assertEqual(work.wrong_form_moves([weak,target],held),[])
        self.assertEqual(held,[(0,weak['found'][0],1)])
    def test_binding_success_only_marks_verified_and_recheck_failure_clears(self):
        binding={'source_folder_id':'fictionalFolder','teacher_ids':['fictionalExactTeacherID']}
        form={'form_id':'fictionalFormID'}
        agenda=SimpleNamespace(lessons=lambda *args:[{'forms':[form]}])
        with patch.object(work,'assignment_policy',return_value={}),patch.object(work,'form_teacher_sources',return_value=binding),patch.object(completion,'lesson_sources',return_value={'problem':None,'decks':[{'title':TITLE}]}):
            work._bind_form_teachers('fictional',{},agenda,'forensics','Wk 03',[form])
        self.assertIs(form['_teacher_source_verified'],True)
        self.assertEqual(form['teacher_signature'],{STEM})
        with patch.object(work,'assignment_policy',return_value={}),patch.object(work,'form_teacher_sources',return_value=binding),patch.object(completion,'lesson_sources',return_value={'problem':'Fresh source access failed','decks':[]}):
            work._bind_form_teachers('fictional',{},agenda,'forensics','Wk 03',[form])
        self.assertNotIn('_teacher_source_verified',form)
        self.assertEqual(form['problem'],'Fresh source access failed')
        form['problem']='Current Form access failed'
        with patch.object(work,'assignment_policy',return_value={}),patch.object(work,'form_teacher_sources',return_value=binding),patch.object(completion,'lesson_sources',return_value={'problem':None,'decks':[{'title':TITLE}]}):
            work._bind_form_teachers('fictional',{},agenda,'forensics','Wk 03',[form])
        self.assertNotIn('_teacher_source_verified',form)
    def test_nearby_verified_binding_provenance_survives_pairing(self):
        agenda=SimpleNamespace(classes={'forensics':{'layout':{'rows':[(1,'Wk 02'),(2,'Wk 03')]}}},lessons=lambda c,w:[{'topic':'Intro to Forensics','forms':[{'form_id':'fictionalNearForm','label':'Intro to Forensics — Turn In'}],'materials':[]}])
        def bind(token,config,agenda,class_key,week,forms):
            for f in forms:f.update(teacher_source_binding={'teacher_ids':['fictionalExactTeacherID']},_teacher_source_verified=True,teacher_signature={STEM},signature={STEM})
        with patch.object(work,'_metadata',return_value={}),patch.object(work,'_bind_form_teachers',side_effect=bind),patch.object(work,'recent_submissions',return_value=[]):
            out=work.nearby_forms('fictional',agenda,'forensics','Wk 03',set(),config={})
        self.assertEqual(len(out),1)
        self.assertIs(out[0]['teacher_source_verified'],True)
        self.assertEqual(out[0]['teacher_source_binding'],{'teacher_ids':['fictionalExactTeacherID']})
        self.assertEqual(out[0]['teacher_signature'],{STEM})
if __name__=='__main__':unittest.main()
