"""Synthetic evidence only. No school identifiers, files or API writes."""
import unittest
from unittest.mock import patch
from school_workflow_hub.adapters import shared_form_intake as intake
from school_workflow_hub.adapters.roster_matching import parse_roster, match_respondent
from school_workflow_hub.adapters.forms_submissions import response_file_refs
from school_workflow_hub.adapters.intake_records import plan
from school_workflow_hub.adapters.hosted_agenda import ConnectionError


def sheet(title, rows):
    return {"properties": {"title": title}, "data": [{"rowData": [
        {"values": [{"formattedValue": c} for c in row]} for row in rows]}]}


def roster():
    return parse_roster({"sheets": [sheet("B3 A&P", [["Example, Alex Middle (Lex)"]])]})


def lesson(key="anatomy:W02"):
    text = '\n'.join(f'What distinguishes synthetic example number {n} from another example?\n[[NOTES:TEST:Q{n}]]' for n in range(3))
    return {"assignment_key":key,"class_key":"anatomy", "assignment_title":"Synthetic anatomy",
            "source_url":"https://example.invalid/deck", "source_text":text}


class IntakeTests(unittest.TestCase):
    def test_all_roster_layouts_and_first_student_survives(self):
        data={"sheets":[sheet("A2 Forensics", [["A2 Forensics"],["Last Name","First Name"],["Sample","Morgan"]]),
                        sheet("A4 Biology", [["Fiction","Taylor"]]),
                        sheet("B3 A&P", [["Example, Alex Middle (Lex)"]]),
                        sheet("SHALL", [["Ignored","Student"]])]}
        rows=parse_roster(data)
        self.assertEqual(len(rows),3)
        self.assertEqual({r['last'] for r in rows},{'Sample','Fiction','Example'})
        self.assertEqual(rows[-1]['first'],'Alex Middle (Lex)')

    def test_collected_email_nickname_and_middle_name(self):
        for name in ('alex.example','alexmiddle.example','lex.example'):
            person, reason=match_respondent('s.'+name+'@school.example',roster(),['school.example'])
            self.assertIsNone(reason)
            self.assertEqual(person['last'],'Example')

    def test_ambiguous_or_wrong_domain_is_held(self):
        rows=roster()*2
        self.assertEqual(match_respondent('s.lex.example@school.example',rows,['school.example'])[1], 'roster_identity_ambiguous')
        self.assertIsNone(match_respondent('s.lex.example@other.example',roster(),['school.example'])[0])

    def test_title_row_not_guessed_student(self):
        with self.assertRaisesRegex(ConnectionError,'layout'):
            parse_roster({'sheets':[sheet('B3 A&P',[['Unexpected title']])]})

    def test_deck_no_match_and_multiple_matches(self):
        source=lesson()
        self.assertEqual(intake.match_deck('unmarked title',[source])[1],'deck_unmatched_or_revision_changed')
        self.assertEqual(intake.match_deck(source['source_text'],[source,lesson('anatomy:W03')])[1],'deck_ambiguous')
        self.assertEqual(intake.match_deck(source['source_text'],[source])[0]['assignment_key'],'anatomy:W02')

    def test_markers_alone_do_not_prove_lesson(self):
        source=lesson()
        markers='\n'.join(intake.signature(source['source_text'])[0])
        self.assertIsNone(intake.match_deck(markers,[source])[0])

    def test_wrong_typed_period_and_free_text_or_link_do_not_select_lesson(self):
        source=lesson(); person=roster()[0]
        for assignment in ('what we did today','https://example.invalid/unrelated'):
            ref={'typed_answers':{'period':['A9 Wrong'], 'name':['Alex Example'], 'work':[assignment]}}
            holds=intake.cross_checks(ref,person,source,{'name':'name','section':'period','assignment':'work'})
            self.assertIn('typed_section_disagreement',holds)
            self.assertIn('typed_assignment_disagreement',holds)
            self.assertEqual(person['class_key'],'anatomy')

    def test_form_email_not_typed_email(self):
        class Reader:
            def responses(self,*args):
                return {'responses':[{'responseId':'synthetic-response','createTime':'2026-09-22T10:00:00Z',
                 'respondentEmail':'s.lex.example@school.example',
                 'answers':{'email':{'textAnswers':{'answers':[{'value':'wrong@school.example'}]}},
                            'upload':{'fileUploadAnswers':{'answers':[{'fileId':'f'*24,'fileName':'anything','mimeType':intake.PPTX}]}}}}]}
        refs,_=response_file_refs(Reader(),'form'+'x'*24,collected_identity=True)
        self.assertEqual(refs['f'*24]['respondent_email'],'s.lex.example@school.example')
        self.assertEqual(refs['f'*24]['typed_answers']['email'],['wrong@school.example'])

    def test_workbook_plan_supersedes_only_matching_unscored_stopgap(self):
        headers=['record_id','assignment_key','section_id','section_name','student_id','learner_key',
                 'display_name','source_url','source_file_id','source_revision','status','content_score',
                 'revision_score','hold_reason','feedback','checked_at','ic_posting_status','superseded_by']
        old={'record_id':'intake-synthetic','source_file_id':'f'*24,'assignment_key':'anatomy:W02',
             'status':'identity_unverified','ic_posting_status':'not_posted'}
        book={'Assignments':{'sheet_id':1,'headers':['assignment_key','class_key','assignment_title'],'rows':[]},
              'Submissions':{'sheet_id':2,'headers':headers,'rows':[(2,old)]}}
        item={'id':'f'*24,'roster_match':roster()[0],'section_id':'synthetic-section',
              'assignment_key':'anatomy:W02','version':'1','intake_holds':['typed_section_disagreement']}
        requests,expected,superseded=plan(book,[lesson()],[item],'2026-09-22T10:00:00Z')
        self.assertEqual(superseded,['intake-synthetic'])
        self.assertEqual(expected[0]['content_score'],'')
        self.assertEqual(expected[0]['student_id'],'')
        book['Assignments']['rows']=[(1,lesson())]
        book['Submissions']['rows']=[(2,{**old,'status':'superseded'}),(3,expected[0])]
        again,_,_=plan(book,[lesson()],[item],'2026-09-22T11:00:00Z')
        self.assertEqual(again,[])

    def test_reviewed_work_is_not_overwritten(self):
        # A reviewed grade protects the whole record, not only its score cell.
        from school_workflow_hub.adapters.intake_records import reconcile
        with patch('school_workflow_hub.adapters.intake_records._batch') as batch:
            self.assertEqual(reconcile(None,{},[],[],'now',lambda:None)['status'],'dry_run')
            batch.assert_not_called()


class HostedSharedTests(unittest.TestCase):
    def test_collector_routes_by_deck_and_email_despite_wrong_typed_values(self):
        from contextlib import ExitStack
        src = lesson()
        src.update(source_deck_id='t'*24, week='02')
        catalog = [{k:v for k,v in src.items() if k != 'source_text'}]
        uploaded = {'id':'f'*24,'name':'misleading filename', 'mimeType':intake.PPTX,
                    'trashed':False,'modifiedTime':'2026-09-22T10:00:00Z','version':'1'}
        teacher = {'id':'t'*24,'mimeType':intake.NATIVE,'trashed':False,'version':'1'}
        class Drive:
            def __init__(self,*a): pass
            def metadata(self,fid):
                return {'r'*24:{'id':'r'*24,'mimeType':intake.FOLDER,'trashed':False},
                        'f'*24:uploaded,'t'*24:teacher}[fid]
        refs={'f'*24:{'respondent_email':'s.lex.example@school.example',
                      'submitted_at':'2026-09-22T10:00:00Z',
                      'typed_answers':{'p':['A9 Wrong'],'n':['Alex Example'],'a':['today work']}}}
        config={'HUB_TURNIN_FORM_ID':'z'*24,'HUB_SUBMISSIONS_ROOT_FOLDER':'r'*24,
                'HUB_SCHOOL_EMAIL_DOMAINS':'school.example',
                'HUB_ROSTER_SECTION_IDS':{'B3 A&P':'section-synthetic'},
                'HUB_TURNIN_QUESTION_IDS':{'section':'p','name':'n','assignment':'a'}}
        with ExitStack() as stack:
            for name, replacement in {
                'service_token':lambda *a:'synthetic-token', 'FormsReader':lambda *a:object(),
                'response_file_refs':lambda *a,**kw:(refs,{'responses_checked':1}),
                'DriveReader':Drive,'locate_files_under_root':lambda *a:({'f'*24:uploaded},1),
                'agenda_catalog':lambda *a:[dict(x) for x in catalog],
                'read_deck':lambda *a:src['source_text'], 'read_roster':lambda *a:roster()
            }.items(): stack.enter_context(patch.object(intake,name,replacement))
            files,evidence=intake.collect_shared(None,config,{})
        self.assertEqual(files[0]['assignment_key'],'anatomy:W02')
        self.assertEqual(files[0]['section_id'],'section-synthetic')
        self.assertIn('typed_section_disagreement',files[0]['intake_holds'])
        self.assertIn('typed_assignment_disagreement',files[0]['intake_holds'])
        self.assertEqual(len(evidence['needs_review']),2)

    def test_shared_monitor_preserves_holds_and_student_mapping(self):
        from school_workflow_hub.adapters import submission_monitor as monitor
        person=roster()[0]
        item={'id':'f'*24,'roster_match':person,'section_id':'section-synthetic',
              'intake_holds':['typed_section_disagreement']}
        with patch.object(monitor,'read_records',return_value={'checked_at':'2026-09-22T10:00:00Z'}):
            result=monitor._mappings(None,'anatomy',{'HUB_INTAKE_ROUTE':'shared'},[item])
        self.assertEqual(result['by_file']['f'*24]['identity_basis'],'collected_email_deck_roster')
        self.assertEqual(result['by_file']['f'*24]['section_id'],'section-synthetic')

    def test_write_requires_fresh_read_and_verifies_supersession(self):
        from school_workflow_hub.adapters import intake_records as sink
        with patch.object(sink,'_workbook',side_effect=[{'old':1},{'changed':1}]), \
             patch.object(sink,'plan',return_value=([{'synthetic':'request'}],[],[])), \
             patch.object(sink,'SheetsReader'), patch.object(sink,'service_token'), \
             patch.object(sink,'_batch') as batch:
            with self.assertRaisesRegex(ConnectionError,'workbook_changed'):
                sink.reconcile(None,{'HUB_INTAKE_WORKBOOK_WRITES':'true'},[],[],'now',lambda:None)
            batch.assert_not_called()

if __name__ == '__main__':
    unittest.main()
