"""Fictional nearby source information; no provider, grade or accepted intake."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from hub_v2 import work,review_context as rc
from test_review_context import found,row,scope,FORM,SOURCE

TARGET='FICTIONAL_NEAR_FORM123'
TITLE='Eyewitness Accounts'
class CrossweekSourceContext(unittest.TestCase):
    def entry(self,**extra):
        return {'current':False,'class_key':'forensics','school_year':'2026-27','week':'Wk 05',
                'form_id':TARGET,'label':TITLE,'teacher_signature':{work.deck_stem(TITLE)},**extra}
    def test_existing_move_context_requires_unique_same_class_year_full_match(self):
        a=SimpleNamespace(weeks=[{'label':'Wk 04','monday':'2026-09-21'},{'label':'Wk 05','monday':'2026-09-28'}],lessons=lambda c,w:[{'forms':[{'form_id':TARGET,'label':TITLE}]}])
        s={'files':[{'id':SOURCE,'name':'Copy of '+TITLE+' - Fictional Student'}]}
        e=self.entry();result=work._nearby_match_context(a,'forensics','Wk 04',[e],0,s)
        self.assertEqual(result['form_id'],TARGET);self.assertEqual(result['source_file_ids'],[SOURCE])
        self.assertEqual(result['matching_kind'],'canonical_nearby_full_title')
        for altered in [self.entry(current=True),self.entry(class_key='biology'),self.entry(school_year='2025-26'),self.entry(problem='Source inaccessible')]:
            self.assertIsNone(work._nearby_match_context(a,'forensics','Wk 04',[altered],0,s))
        self.assertIsNone(work._nearby_match_context(a,'forensics','Wk 04',[e,deepcopy(e)],0,s))
    def test_weak_mixed_duplicate_and_unknown_originals_do_not_gain_context(self):
        a=SimpleNamespace(weeks=[{'label':'Wk 04','monday':'2026-09-21'},{'label':'Wk 05','monday':'2026-09-28'}],lessons=lambda c,w:[{'forms':[{'form_id':TARGET,'label':TITLE}]}]);e=self.entry()
        for files in [[{'id':SOURCE,'name':'Eyewitness'}],
                      [{'id':SOURCE,'name':TITLE},{'id':'FICTIONAL_OTHER123','name':'Foreign Deck'}],
                      [{'id':SOURCE,'name':TITLE},{'id':SOURCE,'name':TITLE}],
                      [{'id':'','name':TITLE}]]:
            self.assertIsNone(work._nearby_match_context(a,'forensics','Wk 04',[e],0,{'files':files}))
    def fixture(self):
        current=scope();data=found(rows=[]);person=row(files=[])
        data['lessons'][0]['forms'][0]['missing']=[person]
        files=[{'id':SOURCE,'name':'Original '+TITLE,'url':'https://drive.google.com/open?id='+SOURCE}]
        item={'who':'Rivera, Alex','section':'A2 Fictional','to':'Wk 05 · Eyewitness Accounts',
              'source_form_id':FORM,'assignment_form_id':TARGET,'accepted_intake':False,
              'file':files[0]['name'],'url':files[0]['url'],'matched_source_files':files,
              'matched_other_lesson':{'schema':'hub-nearby-lesson-match/v1','matching_kind':'canonical_nearby_full_title',
               'class_key':'forensics','school_year':current['school_year'],'week':'Wk 05','form_id':TARGET,
               'lesson':TITLE,'source_file_ids':[SOURCE]}}
        data['lessons'][0]['forms'][0]['elsewhere']=[item]
        return current,data,item
    def test_verified_adjacent_source_is_information_not_intake_or_grade_acceptance(self):
        current,data,item=self.fixture();before=deepcopy(data)
        result=rc.build(current,data)
        self.assertEqual(result['notices'],[])
        info=result['matched_other_lessons'][0]
        self.assertEqual(info['target']['week'],'Wk 05');self.assertEqual(info['target']['school_year'],current['school_year'])
        self.assertEqual(info['target']['url'],'https://docs.google.com/forms/d/'+TARGET+'/edit')
        self.assertEqual(info['sources'][0]['file_id'],SOURCE)
        self.assertNotIn('next_action',info);self.assertNotIn('verified_at',info)
        self.assertIs(result['source_selection']['assessment_performed'],False)
        self.assertEqual(data,before);self.assertIs(item['accepted_intake'],False)
    def test_foreign_unknown_incomplete_context_remains_actionable(self):
        for key,value in [('class_key','biology'),('school_year','2025-26'),('source_file_ids',['FOREIGN_SOURCE123']),('schema','unknown'),('matching_kind','weak_title')]:
            current,data,item=self.fixture();item['matched_other_lesson'][key]=value
            result=rc.build(current,data)
            self.assertEqual(result['matched_other_lessons'],[]);self.assertEqual(len(result['notices']),1)
        current,data,item=self.fixture();item['source_form_id']=None
        self.assertEqual(rc.build(current,data)['matched_other_lessons'],[])
        current,data,item=self.fixture();item['who']='Unknown Person'
        self.assertEqual(rc.build(current,data)['matched_other_lessons'],[])
    def test_real_unknown_notice_survives_adjacent_information(self):
        current,data,item=self.fixture()
        data['lessons'][0]['forms'][0]['students']=[row(first='Pat',last='Unknown',section='A3 Fictional',matched=False)]
        result=rc.build(current,data)
        self.assertEqual(len(result['matched_other_lessons']),1)
        self.assertEqual(len(result['notices']),1)
        self.assertIs(result['notices'][0]['student']['verified'],False)
if __name__=='__main__':unittest.main()
