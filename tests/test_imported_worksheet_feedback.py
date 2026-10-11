"""Fictional authenticated worksheet reviews; no model or real Drive/PDF work."""
from copy import deepcopy
from unittest.mock import patch
import hashlib,unittest
from hub_v2 import imported_original_feedback as feedback, student_archive as archive
from hub_v2.feedback_pdf import _science_writing_history
from school_workflow_hub.core.runs import content_hash
from tests import test_imported_original_archive as imported_fixture
FILE,RAW=imported_fixture.FILE,imported_fixture.RAW
PNG=b'\x89PNG\r\n\x1a\nFictional captured page PNG'

class ImportedWorksheetFeedback(unittest.TestCase):
 def setUp(self):
  self.fixture=imported_fixture.ImportedOriginalArchiveTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
  registered=self.fixture.register();self.record=deepcopy(self.fixture.port.rows[registered['record_key']].value)
  self.student=deepcopy(self.fixture.port.rows[self.fixture.student_key].value);self.meta=self.fixture.drive.metadata('read',FILE)
  self.teacher={'id':self.record['import_provenance']['worksheet_teacher_id'],'role':'Research Banner','revision':'8','source_sha256':'d'*64,
   'fields':[{'id':'case-evidence','page':1,'box':[0.1,0.2,0.5,0.4],'kind':'written','provided_text':['Evidence leading to exoneration','Type here']},
             {'id':'profile','page':1,'box':[0.6,0.2,0.9,0.5],'kind':'picture','provided_text':[]}]}
  self.review={'schema':feedback.SCHEMA,'status':'TEACHER_VERIFIED_AUTHORED_RESPONSE_REGIONS','record_key':self.record['record_key'],'student_key':self.student['student_key'],'source_file_id':FILE,'source_revision':self.meta['version'],'source_sha256':hashlib.sha256(RAW).hexdigest(),'teacher':self.teacher,'page_count':1,
   'pages':[{'page':1,'source_sha256':hashlib.sha256(RAW).hexdigest(),'sha256':hashlib.sha256(PNG).hexdigest(),'width':600,'height':800,'personally_reviewed':True}],
   'fields':[{'id':'case-evidence','page':1,'box':[0.1,0.2,0.5,0.4],'classification':'authored','answer':'DNA evidence excluded the suspect because the sample did not match.'},{'id':'profile','page':1,'box':[0.6,0.2,0.9,0.5],'classification':'excluded','answer':''}]}
 def capture(self,q=None,**updates):
  q=self.review if q is None else q
  return feedback.evidence(self.record,self.student,self.meta,self.meta,RAW,q,expected_review_sha256=content_hash(q),rendered_page_bytes={1:PNG},teacher_reference_sha256=content_hash(self.teacher),expected_page_count=1,**updates)
 def verify(self,value):return feedback.validate_saved_report(value,expected_review_sha256=content_hash(self.review),expected_teacher_reference_sha256=content_hash(self.teacher))
 def file(self,value,raw):return archive.file_science_writing('read','write',self.fixture.config,value,raw,imported_review_sha256=content_hash(self.review),imported_teacher_reference_sha256=content_hash(self.teacher))
 def observations(self):return [{'dimension':d,'level':'Proficient','question_id':'case-evidence','quote':'DNA evidence excluded the suspect','explanation':'Your answer connects a specific result to your claim.'}for d in feedback.TEACHER_DIMENSIONS]
 def make(self,e=None,rows=None):return feedback.report(e or self.capture(),rows or self.observations(),prepared_date='2026-10-11',saved_at='2026-10-11T12:00:00Z',target_title='Connect the result to the claim',target_text='How does the DNA result support your claim?')
 def saved(self):
  v=self.make();raw=b'%PDF-1.7\nFictional reviewed feedback';v['pdf_sha256']=hashlib.sha256(raw).hexdigest();return v,raw
 def test_reviewed_human_location_is_shown_and_private_field_id_retained(self):
  self.teacher['fields'][0]['student_facing_location']='Page 1 — Exoneration evidence'
  v=self.make();self.assertEqual(v['dimensions'][0]['source_location'],'Page 1 - Exoneration evidence')
  self.assertEqual(v['dimensions'][0]['question_id'],'case-evidence');self.assertEqual(v['provenance']['source_observations'][0]['question_id'],'case-evidence');self.verify(v)
 def test_wrong_page_control_or_private_object_location_reject(self):
  for label in ('Page 2 - Question 9','Page 11 - Question 9','Page 1\nQuestion 9','Page 1 - g24bcaf083c3_0_45'):
   self.teacher['fields'][0]['student_facing_location']=label
   with self.subTest(label=label),self.assertRaises(archive.Held):self.make()
 def test_trusted_deferred_index_files_existing_source_once_without_native_refresh(self):
  value,raw=self.saved()
  with patch.object(archive,'refresh_work_index',side_effect=AssertionError('native index must remain untouched')):
   out=archive.file_science_writing('read','write',self.fixture.config,value,raw,imported_review_sha256=content_hash(self.review),imported_teacher_reference_sha256=content_hash(self.teacher),refresh_index=False)
  self.assertEqual(out['status'],'verified',out);self.assertEqual(out['index_sync']['status'],'deferred');self.assertFalse(out['index_sync']['native_index_write'])
  self.assertEqual(self.fixture.port.rows[self.record['record_key']].value,self.record)
 def test_non_boolean_deferred_option_rejects_before_any_create(self):
  value,raw=self.saved();n=len(self.fixture.drive.created)
  out=archive.file_science_writing('read','write',self.fixture.config,value,raw,imported_review_sha256=content_hash(self.review),imported_teacher_reference_sha256=content_hash(self.teacher),refresh_index='false')
  self.assertEqual(out['status'],'held');self.assertEqual(len(self.fixture.drive.created),n)
 def test_actual_external_date_rubric_and_no_Form_grade_growth(self):
  e=self.capture();r=self.make();self.assertEqual(e['source_date'],'2026-09-16');self.assertEqual(r['prepared_at'],'2026-10-11');self.assertIsNone(e['form_event']);self.assertIsNone(e['submitted_at']);self.assertFalse(e['grade_or_completion_assessment']);self.assertEqual(self.verify(r),e);self.assertEqual(_science_writing_history(r)[1][0]['segments'],[])
 def test_bytes_fullmetadata_parent_or_student_mutation_hold(self):
  for kind in ('bytes','metadata','parent','student'):
   r,s,m,raw=deepcopy(self.record),deepcopy(self.student),deepcopy(self.meta),RAW
   if kind=='bytes':raw+=b'changed'
   if kind=='metadata':m['version']='999'
   if kind=='parent':m['parents']=['OtherStudentFolder']
   if kind=='student':s['student_key']='OtherStudent'
   with self.subTest(kind=kind),self.assertRaises(archive.Held):feedback.evidence(r,s,m,m,raw,self.review,expected_review_sha256=content_hash(self.review),rendered_page_bytes={1:PNG},teacher_reference_sha256=content_hash(self.teacher),expected_page_count=1)
 def test_before_after_literal_metadata_drift_rejects(self):
  after=deepcopy(self.meta);after['modifiedTime']='2026-10-12T00:00:00Z'
  with self.assertRaises(archive.Held):feedback.evidence(self.record,self.student,self.meta,after,RAW,self.review,expected_review_sha256=content_hash(self.review),rendered_page_bytes={1:PNG},teacher_reference_sha256=content_hash(self.teacher),expected_page_count=1)
 def test_teacher_role_and_paired_geometry_changed_both_reject(self):
  for kind in ('id','role','paired'):
   q=deepcopy(self.review)
   if kind=='id':q['teacher']['id']='WrongTeacher'
   if kind=='role':q['teacher']['role']='WrongActivity'
   if kind=='paired':q['teacher']['fields'][0]['box'][0]+=0.001;q['fields'][0]['box'][0]+=0.001
   with self.subTest(kind=kind),self.assertRaises(archive.Held):self.capture(q)
 def test_wrong_source_identity_revision_or_record_review_reject(self):
  for key in ('source_file_id','student_key','source_revision','record_key','source_sha256'):
   q=deepcopy(self.review);q[key]='wrong'
   with self.subTest(key=key),self.assertRaises(archive.Held):self.capture(q)
 def test_page_hash_source_link_unreviewed_or_missing_page_reject(self):
  for kind in ('pagehash','sourcehash','unreviewed','missing'):
   q=deepcopy(self.review)
   if kind=='pagehash':q['pages'][0]['sha256']='0'*64
   if kind=='sourcehash':q['pages'][0]['source_sha256']='0'*64
   if kind=='unreviewed':q['pages'][0]['personally_reviewed']=False
   if kind=='missing':q['pages'].append({**q['pages'][0],'page':2})
   with self.subTest(kind=kind),self.assertRaises(archive.Held):self.capture(q)
 def test_prompt_picture_or_duplicate_field_is_not_authored_writing(self):
  for kind in ('prompt','picture','duplicate'):
   q=deepcopy(self.review)
   if kind=='prompt':q['fields'][0]['answer']='Evidence leading to exoneration'
   if kind=='picture':q['fields'][1].update(answer='A face is visible',classification='authored')
   if kind=='duplicate':q['fields'][1]['id']=q['fields'][0]['id']
   with self.subTest(kind=kind),self.assertRaises(archive.Held):self.capture(q)
 def test_quote_exact_field_contiguous_printed_or_long_reject(self):
  for kind in ('invented','otherfield','printed','long'):
   rows=self.observations()
   if kind=='invented':rows[0]['quote']='The suspect is certainly innocent'
   if kind=='otherfield':rows[0]['question_id']='profile'
   if kind=='printed':rows[0]['quote']='Evidence leading to exoneration'
   if kind=='long':rows[0]['quote']='word '*26
   with self.subTest(kind=kind),self.assertRaises(archive.Held):self.make(rows=rows)
 def test_unavailable_remains_unrated_not_Beginning(self):
  q=deepcopy(self.review);q['fields'][0].update(classification='unavailable',answer='');e=self.capture(q)
  rows=[{'dimension':d,'level':'Evidence unavailable','question_id':'case-evidence','quote':'','explanation':'No attributable scientific explanation in this field.'}for d in feedback.TEACHER_DIMENSIONS]
  r=self.make(e,rows);self.assertTrue(all(c['points']==[None]for c in _science_writing_history(r)[1]));rows[0]['level']='Beginning'
  with self.assertRaises(archive.Held):self.make(e,rows)
 def test_saved_source_date_quote_observation_teacher_changed_reject(self):
  for kind in ('date','quote','record','teacher'):
   r=self.make()
   if kind=='date':r['lessons'][0]['source_date']='2026-10-11'
   if kind=='quote':r['dimensions'][0]['example']='Made up'
   if kind=='record':r['provenance']['source_observations'][1]['source_record_key']='WrongRecord'
   if kind=='teacher':r['provenance']['imported_source_evidence']['teacher']['fields'][0]['box'][0]+=0.001
   with self.subTest(kind=kind),self.assertRaises(archive.Held):self.verify(r)
 def test_existing_folder_feedback_preserves_original_index_key_date_and_note(self):
  before=[r for r in archive.work_index_rows('read',self.fixture.config)['rows']if r[3]==self.record['assignment']][0]
  value,raw=self.saved();out=self.file(value,raw);self.assertEqual(out['status'],'verified',out)
  rows=[r for r in archive.work_index_rows('read',self.fixture.config)['rows']if r[3]==self.record['assignment']];self.assertEqual(len(rows),1);r=rows[0]
  self.assertEqual((r[5],r[12],r[13]),(before[5],before[12],before[13]));self.assertIn('Open feedback',r[9]);self.assertIn(FILE,r[7]);self.assertIn('schoology.com',r[8]);self.assertEqual(r[6],archive._sheet_date('2026-10-11'));self.assertEqual(self.fixture.port.rows[self.record['record_key']].value,self.record)
  n=len(self.fixture.drive.created);self.assertEqual(self.file(value,raw)['status'],'verified');self.assertEqual(len(self.fixture.drive.created),n)
 def test_wrong_imported_record_cannot_create_feedback(self):
  value,raw=self.saved();value['provenance']['source_observations'][0]['source_record_key']='WrongRecord';n=len(self.fixture.drive.created)
  out=self.file(value,raw);self.assertEqual(out['status'],'held');self.assertEqual(len(self.fixture.drive.created),n)
 def test_changed_source_cannot_create_feedback(self):
  value,raw=self.saved();self.fixture.drive.items[FILE]['version']='999';n=len(self.fixture.drive.created)
  out=self.file(value,raw);self.assertEqual(out['status'],'held');self.assertEqual(len(self.fixture.drive.created),n)
 def test_imported_filing_needs_independently_authenticated_review_digests(self):
  value,raw=self.saved();n=len(self.fixture.drive.created)
  out=archive.file_science_writing('read','write',self.fixture.config,value,raw)
  self.assertEqual(out['status'],'held');self.assertEqual(len(self.fixture.drive.created),n)
 def test_changed_both_capture_and_report_cannot_borrow_original_review_digest(self):
  e=self.capture();e['answers'][0]['answer']='A different invented response';e['approved_response_review']['fields'][0]['answer']=e['answers'][0]['answer']
  rows=self.observations()
  for r in rows:r['quote']='A different invented response'
  changed=self.make(e,rows)
  with self.assertRaises(archive.Held):self.verify(changed)
 def test_printed_prefix_in_mixed_transcript_cannot_be_rubric_quote(self):
  q=deepcopy(self.review);q['fields'][0]['answer']='Evidence leading to exoneration: DNA evidence excluded the suspect.'
  rows=self.observations();rows[0]['quote']='Evidence leading to exoneration'
  with self.assertRaises(archive.Held):self.make(self.capture(q),rows)
 def test_partial_page_review_cannot_borrow_independent_original_page_count(self):
  with self.assertRaises(archive.Held):feedback.evidence(self.record,self.student,self.meta,self.meta,RAW,self.review,expected_review_sha256=content_hash(self.review),rendered_page_bytes={1:PNG},teacher_reference_sha256=content_hash(self.teacher),expected_page_count=11)
 def test_mutated_projected_answers_cannot_keep_unchanged_approved_raw_review(self):
  e=self.capture();e['answers'][0]['answer']='Another response';rows=self.observations()
  for r in rows:r['quote']='Another response'
  value=self.make(e,rows)
  with self.assertRaises(archive.Held):self.verify(value)
 def test_no_synthetic_Form_timestamp_or_first_revision_stage(self):
  r=self.make();self.assertNotIn('presentation_context',r);self.assertTrue(all(x['submitted_at']is None and 'stage'not in x for x in r['provenance']['source_observations']));self.assertEqual([x['source_date']for x in r['lessons']],['2026-09-16'])
if __name__=='__main__':unittest.main()
