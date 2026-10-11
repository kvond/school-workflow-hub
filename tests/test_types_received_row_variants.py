"""Only original, finite received rows can differ from a newer teacher copy."""
from copy import deepcopy
import unittest
from hub_v2 import completion as c,received_rows as rr,response_binding as rb


def unit(key,text,box,**extra):return {'key':key,'text':text,'box':box,'title':False,**extra}
def fixture(number=1,mode='original-ransom-note'):
 y=1504950 if number==1 else 2895600 if number==14 else rr.SOURCE_Y[number]
 item={'text':'Signed Ransom Note'if number==1 else'A hit and run accident caught on film'if number==14 else rr.SHIFTED[number],
       'box':[619125,y,3238500,285900 if number==14 else 426300]}
 headers=[{'text':'Direct or Circumstantial?','box':[4143375,1123950,2047800,342900 if number==14 else 381000]},
          {'text':'Physical or Biological? (Circumstantial)','box':[6477000,1123950,2047800,342900 if number==14 else 381000]}]
 fields=([{'text':'Type here','box':[4000500,y,1762200,285900]},{'text':'Type here','box':[6334125,y,2000100,285900]}]if number==14 else
         [{'text':'Type here','box':[4143375,y,2047800,426300]},{'text':'Type here','box':[6477000,y,2047800,426300]}])
 data={'kind':'conditional-response-row','page_size':rr.SIZE,'item':item,'headers':headers,'fields':fields}
 token='[[NOTES:F05b:EVIDENCE-ROW-%02d]]'%number;teacher={'number':1,'units':[unit('item',item['text'],item['box']),*[unit('header%d'%i,h['text'],h['box'])for i,h in enumerate(headers)],*[unit('field%d'%i,f['text'],f['box'])for i,f in enumerate(fields)],unit('code',token,[fields[0]['box'][0]+100,fields[0]['box'][1]+100,1,1],binding=data)]}
 student=deepcopy(teacher);student['units'][-1]['binding']['received_variant']=mode
 marker=c.markers({'slides':[student]})[0];received=rr.regions(marker,(item,headers,fields))
 student['units'][0].update(text=received[0]['text'],box=received[0]['box'])
 for i,f in enumerate(received[2]):student['units'][3+i].update(text='Direct'if i==0 else'Type here',box=f['box'])
 if mode=='original-primary-only-row14':student['units'].pop(4)
 return teacher,student
def assess(teacher,student):return c.assess({'slides':[student],'size':tuple(rr.SIZE)},{'slides':[teacher],'size':tuple(rr.SIZE)},found_codes_only=True)


class ReceivedRowVariants(unittest.TestCase):
 def held(self,t,s):
  r=assess(t,s);self.assertTrue(r['note']);self.assertIsNone(r['notes_score']);self.assertEqual(r['counts']['NOTES']['done'],0)
 def test_older_ransom_prompt_is_a_real_one_row_attempt_without_rewriting_it(self):
  t,s=fixture();r=assess(t,s);self.assertEqual(r['counts']['NOTES'],{'done':1,'required':1});self.assertEqual(r['items'][0]['answer'],'Direct');self.assertEqual(s['units'][0]['text'],'Ransom Note')
 def test_exact_original_eight_translations_and_primary_only_row14_count_once(self):
  for number in range(1,9):
   t,s=fixture(number,'original-ransom-note-shifted'if number==1 else'original-shifted-row');r=assess(t,s);self.assertFalse(r['note']);self.assertEqual(r['counts']['NOTES'],{'done':1,'required':1})
  t,s=fixture(14,'original-primary-only-row14');r=assess(t,s);self.assertEqual(r['items'][0]['answer'],'Direct');self.assertFalse(r['note']);self.assertEqual(len([u for u in s['units']if u['key'].startswith('field')]),1)
 def test_wrong_tag_code_question_or_changed_teacher_descriptor_remains_held(self):
  for mode in('tag','code','label','teacher','manifest'):
   t,s=fixture()
   if mode=='tag':s['units'][-1]['binding']['received_variant']='arbitrary-old-source'
   if mode=='code':s['units'][-1]['text']='[[NOTES:F05a:EVIDENCE-ROW-01]]'
   if mode=='label':s['units'][0]['text']='An unsigned demand'
   if mode=='teacher':t['units'][-1].pop('binding')
   if mode=='manifest':s['units'][-1]['binding']['fields'][0]['box'][0]+=1
   self.held(t,s)
 def test_both_ransom_modes_reject_unknown_teacher_and_received_field_geometry(self):
  for mode in ('original-ransom-note','original-ransom-note-shifted'):
   for field in range(2):
    for coordinate in range(4):
     with self.subTest(mode=mode,field=field,coordinate=coordinate):
      t,s=fixture(1,mode)
      box=list(t['units'][-1]['binding']['fields'][field]['box']);box[coordinate]+=1
      t['units'][-1]['binding']['fields'][field]['box']=box.copy()
      t['units'][3+field]['box']=box.copy()
      s['units'][-1]['binding']['fields'][field]['box']=box.copy()
      actual=list(s['units'][3+field]['box']);actual[coordinate]+=1
      s['units'][3+field]['box']=actual
      stripped=deepcopy(c.markers({'slides':[s]})[0]);stripped['binding'].pop('received_variant')
      self.assertTrue(rb._trusted(stripped,t),'The finite guard, not ordinary teacher mismatch, must reject this control')
      self.held(t,s)
 def test_one_EMU_move_duplicates_missing_primary_and_unknown_secondary_hold(self):
  for mode in('move','repeat','missing'):
   t,s=fixture(2,'original-shifted-row')
   if mode=='move':s['units'][3]['box'][0]+=1
   if mode=='repeat':s['units'].append(deepcopy(s['units'][3]))
   if mode=='missing':s['units'].pop(3)
   self.held(t,s)
  t,s=fixture(14,'original-primary-only-row14');s['units'].append(unit('unknown','Physical',[6334125,2895600,2000100,285900]));self.held(t,s)
 def test_heading_choices_prompt_and_empty_cell_do_not_become_student_answers(self):
  for value in('','Type here','Ransom Note','Direct or Circumstantial?','Physical or Biological'):
   t,s=fixture();s['units'][3]['text']=value;r=assess(t,s);self.assertFalse(r['note']);self.assertEqual(r['counts']['NOTES']['done'],0)
 def test_wrong_row_and_page_size_and_malformed_fields_cannot_get_variant_trust(self):
  for mode in('row','size','fields'):
   t,s=fixture()
   if mode=='row':s['units'][-1]['text']='[[NOTES:F05b:EVIDENCE-ROW-10]]'
   if mode=='size':s['units'][-1]['binding']['page_size']=[1000,750]
   if mode=='fields':s['units'][-1]['binding']['fields']=['unknown']
   self.held(t,s)


if __name__=='__main__':unittest.main()
