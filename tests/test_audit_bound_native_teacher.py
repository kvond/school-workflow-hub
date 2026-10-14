"""Exact authoritative teacher reads fix audit export limits without grade operations."""
from copy import deepcopy
from datetime import date
from unittest import TestCase
from unittest.mock import patch
from hub_v2 import audit, completion, google, work
from test_hub_v2 import fixture, handout
from test_native_teacher_source import native, metadata, TEMPLATE

class BoundNativeAuditTests(TestCase):
 def setUp(self):
  completion._templates.clear();completion._native_teacher_templates.clear()
  self.meta=metadata(parents=['FICTIONALFOLDER123'])
 def read(self,export=None,native_error=None,meta=None):
  with patch.object(google,'drive_file',return_value=self.meta if meta is None else meta), \
       patch.object(google,'deck_bytes',side_effect=export if isinstance(export,Exception) else None,return_value=export if isinstance(export,bytes) else handout()) as exported, \
       patch.object(google,'native_presentation',side_effect=native_error,return_value=native()) as native_get, \
       patch.object(google,'append_rows') as writes:
   result=audit._release_deck('read',TEMPLATE,native_teacher=True)
  writes.assert_not_called()
  return result,exported,native_get
 def test_oversized_export_uses_existing_exact_native_parser(self):
  result,exported,native_get=self.read(google.GoogleError('Too large.',403,'exportSizeLimitExceeded'))
  self.assertEqual(result['status'],'READY');self.assertFalse(result['student_copies_checked'])
  self.assertEqual(result['deck_id'],TEMPLATE);self.assertEqual(result['_teacher_source_metadata'],self.meta)
  exported.assert_called_once();native_get.assert_called_once_with('read',TEMPLATE)
 def test_native_failure_remains_availability(self):
  result,_,_=self.read(google.GoogleError('Too large.',403,'exportSizeLimitExceeded'),google.GoogleError('Native unavailable.',503))
  self.assertEqual(result['status'],'NOT_READY');self.assertEqual(result['readability']['http_status'],503)
  self.assertEqual(result['issues'],['deck_release_unreadable']);self.assertIn('Native unavailable',result['problem'])
 def test_successful_ordinary_export_uses_no_native_read(self):
  result,exported,native_get=self.read(handout())
  self.assertEqual(result['status'],'READY');exported.assert_called_once();native_get.assert_not_called()
 def test_unbound_403_keeps_existing_readability_contract(self):
  with patch.object(google,'drive_file',return_value=self.meta),patch.object(google,'deck_bytes',side_effect=google.GoogleError('Too large.',403)),patch.object(google,'native_presentation') as native_get:
   result=audit._release_deck('read',TEMPLATE)
  self.assertEqual(result['status'],'NOT_READY');native_get.assert_not_called()
 def test_trash_never_reads_native_or_export(self):
  result,exported,native_get=self.read(meta={**self.meta,'trashed':True})
  self.assertEqual(result['status'],'NOT_READY');exported.assert_not_called();native_get.assert_not_called()
 def test_native_content_metadata_drift_does_not_become_ready(self):
  calls=iter([self.meta,self.meta,self.meta,{**self.meta,'version':'2'}])
  with patch.object(google,'drive_file',side_effect=lambda *_a,**_k:deepcopy(next(calls))),patch.object(google,'deck_bytes',side_effect=google.GoogleError('Too large.',403)),patch.object(google,'native_presentation',return_value=native()):
   result=audit._release_deck('read',TEMPLATE,native_teacher=True)
  self.assertEqual(result['status'],'NOT_READY');self.assertIn('changed during',result['problem'])
 def overview(self,*,foreign=False,folder_changed=False):
  ag=fixture();binding={'teacher_ids':[TEMPLATE]}
  source={'decks':[{'id':TEMPLATE,'title':'Fictional source','metadata':deepcopy(self.meta)}],'stale_links':[],'folders':[{'id':'FICTIONALFOLDER123','name':'Verified folder','deck_ids':[TEMPLATE]}],'problem':None}
  bound=deepcopy(source);bound['binding']=binding
  changed=deepcopy(bound)
  if folder_changed:changed['folders'][0]['name']='Changed folder'
  def resolution(_token,_lesson,**kw):
   if kw.get('binding'):
    value=deepcopy(bound)
    if foreign:value['problem']='The registered teacher sources do not match their current declared parent.'
    return value
   return deepcopy(source)
  with patch.object(audit,'_titles',return_value={}),patch.object(audit,'_published',return_value={}),patch.object(audit,'school_today') as clock, \
       patch.object(work,'form_teacher_sources',side_effect=lambda _c,_a,_k,week,_f:binding if week=='Wk 04' else None), \
       patch.object(completion,'lesson_sources',side_effect=resolution),patch.object(completion,'_registered_teacher_sources',return_value=changed), \
       patch.object(google,'drive_file',return_value=self.meta),patch.object(google,'deck_bytes',side_effect=google.GoogleError('Too large.',403)),patch.object(google,'native_presentation',return_value=native()) as native_get,patch.object(google,'append_rows') as writes:
   clock.return_value=date(2026,9,23)
   result=audit.check('read',ag,'forensics',{})
  writes.assert_not_called()
  return next(row for row in result['rows'] if row['week']=='Wk 04'),native_get
 def test_exact_current_binding_is_required_before_native_opt_in(self):
  row,native_get=self.overview(foreign=True)
  self.assertEqual(row['release']['status'],'NOT_READY');self.assertIn('do not match',row['release']['problem'])
  native_get.assert_not_called()
 def test_bound_folder_bookend_after_consumed_native_read_holds_drift(self):
  row,native_get=self.overview()
  self.assertEqual(row['release']['status'],'READY');self.assertNotIn('_teacher_source_metadata',row['release'])
  self.assertTrue(native_get.called)
  completion._templates.clear();completion._native_teacher_templates.clear()
  row,native_get=self.overview(folder_changed=True)
  self.assertEqual(row['release']['status'],'NOT_READY');self.assertIn('changed while',row['release']['problem'])
  self.assertTrue(native_get.called)
