"""One explicitly mapped Saved component is historical evidence, never a maximum."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch
from hub_v2 import completion, google, source_history_dispositions as dispositions
from school_workflow_hub.core.cloud_state import Document, Snapshot
from test_source_history_dispositions import receipt, captures, configured, NOW, HASH, STUDENT, OLDER
from test_form_source_bindings_and_case_policy import Agenda, SECOND, metadata

def single_receipt():
 value,_,detail=receipt()
 value['native_saved']['components']=value['native_saved']['components'][:1]
 value['native_saved']['components'][0]['value']=5
 value['native_saved']['targets']=value['native_saved']['targets'][:1]
 targets=deepcopy(value['native_saved']['targets'])
 mapping={'class_key':'forensics','form_id':SECOND,'targets':targets}
 config={'HUB_IC_ASSIGNMENT_MAP':json.dumps([mapping])}
 row,found,scope,roster,result=captures()
 basis=dispositions.current_basis(Agenda(),config,scope,found,row,roster)
 value['scope']=basis['scope'];value['record_key']=dispositions.receipt_key(value)
 basis['expected_targets']=targets;basis['native_instruction_sha256']=HASH
 config.update(configured(value))
 return value,basis,detail,config

class SingleSavedComponentTests(unittest.TestCase):
 def test_one_current_mapped_Notes_value_has_as_of_history_only(self):
  value,basis,detail,config=single_receipt()
  md={fid:metadata(fid) for fid in (STUDENT,OLDER)}
  self.assertEqual(dispositions.verify_current(value,basis,detail,md,now=NOW),value)
  row,found,scope,roster,result=captures();before=deepcopy(found)
  store=Mock();store.read.return_value=Snapshot({value['record_key']:Document(value,'1')},NOW.isoformat())
  with patch.object(dispositions,'configured_store',return_value=store),patch.object(google,'drive_file',side_effect=lambda _t,fid,_f:metadata(fid)),patch.object(completion,'assess',side_effect=AssertionError('no assessment')),patch.object(google,'append_rows',side_effect=AssertionError('no school writes')):
   out=dispositions.apply('read',config,Agenda(),scope,found,result,roster)
  self.assertEqual(out['status'],'history_retained');self.assertEqual(out['notices'],[])
  retained=out['retained_history'][0]
  self.assertEqual(retained['basis'],'manual_saved_history');self.assertNotIn('next_action',retained)
  self.assertIn('Saved entry verified',retained['reason']);self.assertNotIn('Both',retained['reason'])
  self.assertFalse(out['source_selection']['assessment_performed']);self.assertEqual(found,before)
  store.commit.assert_not_called()
 def test_absent_full_duplicate_foreign_or_changed_current_map_rejects(self):
  value,basis,detail,_=single_receipt();md={fid:metadata(fid) for fid in (STUDENT,OLDER)}
  full=receipt()[0]['native_saved']['targets']
  variants=[[],full,basis['mapped_targets']*2,[{**basis['mapped_targets'][0],'section':'Different section'}],[{**basis['mapped_targets'][0],'assignment_id':'999'}]]
  for mapped in variants:
   changed={**basis,'mapped_targets':mapped}
   with self.subTest(mapped=mapped),self.assertRaises(ValueError):dispositions.verify_current(value,changed,detail,md,now=NOW)
 def test_one_component_can_never_be_a_maximum_receipt(self):
  value,_,_,_=single_receipt();value['basis']='maximum_saved_as_of';value['native_saved']['components'][0]['value']=10
  value['record_key']=dispositions.receipt_key(value)
  with self.assertRaises(ValueError):dispositions.validate(value)
 def test_wrong_column_value_unsaved_or_extra_component_rejects(self):
  value,_,_,_=single_receipt()
  for field,bad in [('column','revision'),('value',11),('saved',False),('points',20)]:
   changed=deepcopy(value);changed['native_saved']['components'][0][field]=bad;changed['record_key']=dispositions.receipt_key(changed)
   with self.subTest(field=field),self.assertRaises(ValueError):dispositions.validate(changed)
  changed=deepcopy(value);changed['native_saved']['components']*=2;changed['record_key']=dispositions.receipt_key(changed)
  with self.assertRaises(ValueError):dispositions.validate(changed)
 def test_two_component_manual_and_existing_maximum_are_unchanged(self):
  md={fid:metadata(fid) for fid in (STUDENT,OLDER)}
  for basis_name in ('manual_saved_history','maximum_saved_as_of'):
   value,basis,detail=receipt(basis=basis_name)
   self.assertEqual(dispositions.verify_current(value,basis,detail,md,now=NOW),value)
