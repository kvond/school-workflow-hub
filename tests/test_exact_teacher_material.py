"""Exact teacher artifacts on synthetic data; no network or school writes."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from hub_v2 import agenda, google, teacher_material as tm, work, review_context, student_archive
from school_workflow_hub.core.cloud_state import Conflict, Document, Snapshot, StoreUnavailable
from test_hub_v2 import fixture, response, ROSTER

TEACHER='teacher@school.example'
CONFIG={'HUB_STATE_BACKEND':'firestore','HUB_ALLOWED_EMAILS':TEACHER,'HUB_SCHOOL_EMAIL_DOMAINS':'school.example'}
FORM='FICTIONALFORMID1234567890abc'
FILE='FICTIONAL_TEACHER_MATERIAL_123'
OTHER='FICTIONAL_STUDENT_ORIGINAL_123'
RID='FICTIONAL_RESPONSE_123'
AT='2026-09-22T14:00:00Z'


class Port:
    def __init__(self): self.docs={}; self.writes=0
    def read(self, ids): return Snapshot({k:self.docs.get(k) for k in ids},AT)
    def commit(self, changes):
        for k,(_,version) in changes.items():
            old=self.docs.get(k)
            if (old.version if old else None)!=version: raise Conflict('changed')
        self.writes+=1
        for k,(value,_) in changes.items(): self.docs[k]=Document(deepcopy(value),str(self.writes))


class ExactMaterialTests(unittest.TestCase):
    def setUp(self):
        self.a=fixture();self.port=Port();self.content=b'complete fictional teacher PPTX bytes'
        self.md={'id':FILE,'name':'Teacher example','mimeType':google.PPTX,'trashed':False,
                 'version':'11','modifiedTime':AT,'parents':['FICTIONAL_UPLOAD_FOLDER']}
        self.event=response(TEACHER,AT,(FILE,'Teacher example'),(OTHER,'Student original'),typed=('New Student',))
        self.event['responseId']=RID
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(tm,'configured_store',return_value=self.port))
        self.stack.enter_context(patch.object(google,'get_json',side_effect=lambda *a,**k:deepcopy(self.event)))
        self.stack.enter_context(patch.object(google,'drive_file',side_effect=lambda *a,**k:deepcopy(self.md)))
        self.stack.enter_context(patch.object(google,'deck_bytes',side_effect=lambda *a,**k:self.content))
        self.stack.enter_context(patch.object(agenda,'load',side_effect=lambda *a,**k:self.a))

    def proof(self): return tm.capture('read',CONFIG,self.a,'forensics','Wk 04',FORM,RID,FILE)
    def receipt(self):
        proof=self.proof()
        return {'schema':tm.SCHEMA,'record_key':tm.record_key(proof),'proof':proof,
                'classification':'verified_teacher_material','evidence':'Host read the complete original: a teacher reference with no student attribution.',
                'instruction_sha256':'a'*64,'operator':TEACHER,'verified_at':datetime.now(timezone.utc).isoformat()}
    def register(self):
        value=self.receipt();tm.import_verified('read',CONFIG,self.a,value,port=self.port);return value
    def entries(self,event=None):
        rows=work.submissions([event or self.event],FORM)
        form={'form_id':FORM,'found':rows}
        return [{'current':True,'week':'Wk 04','form':form,'found':rows}]
    def classify(self,entries): return tm.classify_entries('read',CONFIG,self.a,'forensics',entries)

    def test_email_and_teacher_filename_without_registration_never_exclude(self):
        entries=self.entries();before=deepcopy(entries)
        self.assertIsNone(self.classify(entries));self.assertEqual(entries,before);self.assertEqual(self.port.writes,0)

    def test_mixed_response_keeps_other_upload_and_original_event(self):
        self.register();entries=self.entries();original=deepcopy(self.event)
        self.assertIsNone(self.classify(entries))
        self.assertEqual([f['id'] for f in entries[0]['found'][0]['files']],[OTHER])
        info=entries[0]['form']['teacher_uploads'][0]
        self.assertEqual(info['files'][0]['id'],FILE);self.assertEqual(info['submitted_at'],AT)
        self.assertEqual(self.event,original)

    def test_an_unchanged_file_in_a_different_response_is_not_excluded(self):
        self.register();other=deepcopy(self.event);other['responseId']='FICTIONAL_OTHER_RESPONSE'
        entries=self.entries(other);before=deepcopy(entries)
        self.assertIsNone(self.classify(entries));self.assertEqual(entries,before)

    def test_changed_event_revision_content_or_agenda_preserves_normal_work(self):
        for change in ('event','version','content','scope'):
            with self.subTest(change=change):
                self.setUp();self.register()
                if change=='event': self.event['answers']['new']={'textAnswers':{'answers':[{'value':'student work'}]}}
                if change=='version': self.md['version']='12'
                if change=='content': self.content=b'new revision not reviewed'
                if change=='scope': self.a.classes['forensics']['cells'][(5,3)]={'formattedValue':'Different lesson'}
                entries=self.entries();before=deepcopy(entries)
                self.assertIsNone(self.classify(entries));self.assertEqual(entries,before)

    def test_store_or_source_failure_preserves_every_mixed_response_and_is_availability(self):
        self.register()
        for failure in ('store','source'):
            entries=self.entries();before=deepcopy(entries)
            with patch.object(self.port if failure=='store' else google,
                              'read' if failure=='store' else 'deck_bytes',
                              side_effect=StoreUnavailable('offline') if failure=='store' else google.GoogleError('offline')):
                problem=self.classify(entries)
            self.assertIn('verification is unavailable',problem);self.assertEqual(entries,before)

    def test_later_capture_cannot_hide_change_to_an_earlier_planned_artifact(self):
        metadata={FILE:deepcopy(self.md),OTHER:{**self.md,'id':OTHER}}
        with patch.object(google,'drive_file',side_effect=lambda token,ident,*a:deepcopy(metadata[ident])):
            self.register()
            value=self.receipt();proof=tm.capture('read',CONFIG,self.a,'forensics','Wk 04',FORM,RID,OTHER)
            value.update(proof=proof,record_key=tm.record_key(proof))
            tm.import_verified('read',CONFIG,self.a,value,port=self.port)
            entries=self.entries();before=deepcopy(entries);capture=tm.capture
            def late_change(*args,**kwargs):
                result=capture(*args,**kwargs)
                if args[-1]==OTHER:metadata[FILE]['version']='12'
                return result
            with patch.object(tm,'capture',side_effect=late_change):
                self.assertIn('verification is unavailable',self.classify(entries))
        self.assertEqual(entries,before)

    def test_changed_source_during_read_cannot_register(self):
        value=self.receipt()
        with patch.object(google,'drive_file',side_effect=[self.md,{**self.md,'version':'12'}]):
            with self.assertRaises(tm.Unverified):tm.import_verified('read',CONFIG,self.a,value,port=self.port)
        self.assertEqual(self.port.writes,0)

    def test_unavailable_agenda_binding_is_an_availability_failure(self):
        entries=self.entries();before=deepcopy(entries)
        with patch.object(tm,'scope',side_effect=tm.Unverified('binding unavailable')):
            self.assertIn('verification is unavailable',self.classify(entries))
        self.assertEqual(entries,before)

    def test_full_history_rejects_new_revision_without_changing_state(self):
        value=self.receipt();proof=value['proof']
        ident=tm.index_key(proof['scope'],RID,FILE)
        self.port.docs[ident]=Document({'schema':tm.SCHEMA,'records':['teachermaterial_'+str(n) for n in range(50)]},'1')
        before=deepcopy(self.port.docs)
        with self.assertRaises(tm.Unverified):tm.import_verified('read',CONFIG,self.a,value,port=self.port)
        self.assertEqual(self.port.docs,before);self.assertEqual(self.port.writes,0)

    def test_changed_agenda_during_read_cannot_register(self):
        value=self.receipt();other=fixture();other.classes['forensics']['cells'][(5,3)]={'formattedValue':'Changed'}
        with patch.object(agenda,'load',return_value=other):
            with self.assertRaises(tm.Unverified):tm.import_verified('read',CONFIG,self.a,value,port=self.port)
        self.assertEqual(self.port.writes,0)

    def test_registration_is_immutable_verified_and_new_revisions_keep_history(self):
        first=self.register();writes=self.port.writes
        tm.import_verified('read',CONFIG,self.a,first,port=self.port);self.assertEqual(self.port.writes,writes)
        changed=deepcopy(first);changed['evidence']='Different attestation for the same immutable receipt.'
        with self.assertRaises(Conflict):tm.import_verified('read',CONFIG,self.a,changed,port=self.port)
        self.md['version']='12';self.content=b'new reviewed teacher revision';second=self.register()
        self.assertNotEqual(first['record_key'],second['record_key'])
        self.assertEqual(self.port.docs[first['record_key']].value,first)
        entries=self.entries();self.assertIsNone(self.classify(entries));self.assertEqual(len(entries[0]['found'][0]['files']),1)

    def test_new_or_removed_teacher_authority_cannot_register(self):
        value=self.receipt();value['operator']='unapproved@school.example'
        with self.assertRaises(tm.Unverified):tm.import_verified('read',CONFIG,self.a,value,port=self.port)
        self.assertEqual(self.port.writes,0)

    def test_complete_native_content_is_required(self):
        self.md['mimeType']=tm.SLIDES
        with patch.object(student_archive,'_native_presentation_proof',return_value={'sha256':'b'*64}):
            self.register();entries=self.entries();self.assertIsNone(self.classify(entries))
        with patch.object(student_archive,'_native_presentation_proof',side_effect=student_archive.Held('incomplete')):
            with self.assertRaises(tm.Unverified):self.proof()

    def test_native_temporary_image_urls_do_not_expire_exact_content_proof(self):
        self.md['mimeType']=tm.SLIDES
        urls=iter(['https://fictional.googleusercontent.com/temporary-one','https://fictional.googleusercontent.com/temporary-two'])
        def get(url,token,params=None,**kwargs):
            if 'slides.googleapis.com' not in url:return deepcopy(self.event)
            self.assertEqual(params['fields'],'pageSize,slides,masters,layouts,notesMaster')
            return {'slides':[{'objectId':'fictional-slide','pageElements':[{'image':{'contentUrl':next(urls)}}]}]}
        with patch.object(google,'get_json',side_effect=get),patch.object(student_archive,'_native_image_proof',return_value={'sha256':'b'*64}):
            first=self.proof();second=self.proof()
        self.assertEqual(first,second)

    def test_nearby_cache_does_not_lose_excluded_attachments(self):
        work._responses_cache.clear()
        with patch.object(google,'form_responses',return_value=[self.event]):
            first=work.recent_submissions('read',FORM);first[0]['files'].clear()
            second=work.recent_submissions('read',FORM)
        self.assertEqual(len(second[0]['files']),2)

    def test_informational_rendering_has_no_student_question_or_score(self):
        self.register();entries=self.entries();self.classify(entries)
        form={'form_id':FORM,'label':'Fictional lesson','students':[],'late':[],'missing':[],
              'teacher_uploads':entries[0]['form']['teacher_uploads']}
        found={'class_key':'forensics','week':'Wk 04','lessons':[{'forms':[form]}],'other_files':[],'problems':[]}
        result=review_context.build({'class_key':'forensics','week':'Wk 04','form_id':FORM,'lesson':'Fictional'},found)
        self.assertEqual(result['notices'],[]);self.assertEqual(len(result['teacher_material']),1)
        self.assertNotIn('next_action',result['teacher_material'][0])

    def discovery(self):
        students,_=work.roster.parse_tab(ROSTER,'forensics','A2 Forensics')
        def meta(token,ident,*args):
            if ident==FILE:return deepcopy(self.md)
            if ident==FORM:return {'id':FORM,'name':'Reasoning','mimeType':'application/vnd.google-apps.form','trashed':False}
            return {'id':ident,'name':'Student original','mimeType':google.PPTX,'trashed':False,'parents':['FICTIONAL_UPLOAD_FOLDER']}
        with ExitStack() as stack:
            stack.enter_context(patch.object(work.roster,'load',return_value=({'forensics':students},[])))
            stack.enter_context(patch.object(google,'drive_file',side_effect=meta))
            stack.enter_context(patch.object(google,'form_responses',side_effect=lambda t,f:[deepcopy(self.event)] if f==FORM else []))
            stack.enter_context(patch.object(work,'_bind_form_teachers'))
            stack.enter_context(patch.object(work,'lesson_folders',return_value=({'FICTIONAL_UPLOAD_FOLDER':'Fictional upload folder'},None)))
            stack.enter_context(patch.object(work,'list_tree',return_value=([deepcopy(self.md)],{})))
            stack.enter_context(patch.object(work,'shared_submissions',return_value=([],None)))
            stack.enter_context(patch.object(work,'nearby_forms',return_value=[]))
            words=stack.enter_context(patch.object(work,'lesson_words',wraps=work.lesson_words))
            result=work.lesson_work('read',CONFIG,self.a,'forensics','Wk 04',review_only=True)
        return result,words

    def test_discovery_filters_before_signatures_and_does_not_readd_folder_copy(self):
        self.register();result,words=self.discovery()
        originals=[f['id'] for l in result['lessons'] for form in l['forms'] for row in form['students']+form['late'] for f in row['files']]
        self.assertNotIn(FILE,originals);self.assertIn(OTHER,originals)
        self.assertFalse(result['other_files'])
        material=[f for l in result['lessons'] for form in l['forms'] for r in form.get('teacher_uploads',[]) for f in r['files']]
        self.assertEqual([f['id'] for f in material],[FILE])
        for call in words.call_args_list:
            self.assertNotIn(FILE,[f['id'] for row in call.args[3] for f in row['files']])

    def test_discovery_proof_failure_blocks_assessment_and_preserves_originals(self):
        self.register()
        with patch.object(self.port,'read',side_effect=StoreUnavailable('offline')):
            result,_=self.discovery()
        self.assertIn('verification is unavailable',result['assessment_problem'])
        originals=[f['id'] for l in result['lessons'] for form in l['forms'] for row in form['students']+form['late'] for f in row['files']]
        self.assertIn(FILE,originals);self.assertIn(OTHER,originals)


if __name__=='__main__': unittest.main()
