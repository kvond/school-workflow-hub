"""Exactly one visible original option highlight is completion-only evidence."""
from copy import deepcopy
import unittest
import xml.etree.ElementTree as ET
from hub_v2 import completion,graphical_choice
from test_eyewitness_choice_binding import fixture,raw,raw_xml,shape,circle
P,A=completion.P,completion.A

def visible_fixture():
 tree=ET.fromstring(raw_xml(fixture())).find(P+'cSld/'+P+'spTree')
 for node in tree:
  if completion._text(node.find(P+'txBody')) in ('YES','NO'):
   run=node.find(P+'txBody/'+A+'p/'+A+'r');style=ET.Element(A+'rPr',{'sz':'1100','b':'1'});fill=ET.SubElement(style,A+'solidFill');ET.SubElement(fill,A+'srgbClr',{'val':'475569'});run.insert(0,style)
 return trusted(''.join(ET.tostring(n,encoding='unicode')for n in tree))

def trusted(content):
 # Rebuild this authenticated fixture's baseline descriptor after teacher styling.
 tree=ET.fromstring(raw_xml(content)).find(P+'cSld/'+P+'spTree')
 parsed=completion.read_deck(raw(content));s=parsed['slides'][0];code=next(u for u in s['units']if u.get('binding'));data=code['binding'];data['original_shapes']=[{'sha256':n['sha256'],'box':list(n['box'])}for n in s['choice_shapes']if graphical_choice._overlaps(n.get('box'),data['region'])]
 import json
 for node in tree:
  props=node.find(P+'nvSpPr/'+P+'cNvPr')
  if props is not None and props.get('descr','').startswith('HUB_COMPLETION_BINDING_V1\n'):props.set('descr','HUB_COMPLETION_BINDING_V1\n'+json.dumps(data))
 return ''.join(ET.tostring(n,encoding='unicode')for n in tree)

def highlight(content,label='YES',color='FDE047',alpha=None):
 tree=ET.fromstring(raw_xml(content)).find(P+'cSld/'+P+'spTree')
 node=next(n for n in tree if completion._text(n.find(P+'txBody'))==label);style=node.find(P+'txBody/'+A+'p/'+A+'r/'+A+'rPr')
 mark=ET.SubElement(style,A+'highlight');c=ET.SubElement(mark,A+'srgbClr',{'val':color})
 if alpha is not None:ET.SubElement(c,A+'alpha',{'val':alpha})
 return ''.join(ET.tostring(n,encoding='unicode')for n in tree)

def assess(student,teacher=None):return completion.assess(completion.read_deck(raw(student)),completion.read_deck(raw(teacher or visible_fixture())),found_codes_only=True)

class ChoiceHighlightTests(unittest.TestCase):
 def held(self,content):
  r=assess(content);self.assertTrue(r['note']);self.assertIsNone(r['notes_score']);self.assertEqual(r['counts']['NOTES']['done'],0)
 def test_either_one_known_visible_option_is_completion_only_not_a_printed_quote(self):
  for label in ('YES','NO'):
   r=assess(highlight(visible_fixture(),label));item=next(i for i in r['items']if i['category']=='NOTES');self.assertEqual(item['status'],'done');self.assertEqual(item['answer'],'');self.assertEqual(item['recorded_choice'],label);self.assertEqual(item['completion_evidence'],'One original YES/NO option highlighted');self.assertEqual(r['counts']['DRAFT']['done'],0);self.assertIsNone(r['note'])
 def test_unchanged_supplied_highlight_and_printed_options_never_credit(self):
  base=trusted(highlight(visible_fixture()));r=assess(base,teacher=base);self.assertEqual(r['counts']['NOTES']['done'],0);self.assertIsNone(r['note'])
  self.assertEqual(assess(visible_fixture())['counts']['NOTES']['done'],0)
 def test_both_options_circle_plus_highlight_and_overlays_hold(self):
  base=visible_fixture()
  for content in (highlight(highlight(base),'NO'),highlight(base)+circle(),highlight(base)+circle('NO'),highlight(base)+shape('overlay','YES',(60000,230000,90000,40000))):self.held(content)
 def test_unknown_or_translucent_or_invisible_paint_is_not_a_supported_mark(self):
  for color,alpha in [('FFFFFF',None),('475569',None),('FDE047','0'),('FDE047','50000'),('00FF00',None)]:self.held(highlight(visible_fixture(),color=color,alpha=alpha))
  self.held(highlight(visible_fixture()).replace('<ns1:highlight>','<ns1:highlight unsupported="1">'))
 def test_changed_question_label_geometry_style_and_painter_order_hold(self):
  content=highlight(visible_fixture())
  for changed in (content.replace('Can a confident witness still be wrong? Circle one:','A changed prompt.'),content.replace('>YES<','>MAYBE<'),content.replace('x="60000"','x="65000"',1),content.replace('sz="1100"','sz="1200"',1),content.replace('val="475569"','val="000000"',1)):
   self.held(changed)
  tree=ET.fromstring(raw_xml(content)).find(P+'cSld/'+P+'spTree');paper=next(n for n in tree if n.find(P+'nvSpPr/'+P+'cNvPr').get('name')=='original-initial-paper');tree.remove(paper);tree.append(paper);self.held(''.join(ET.tostring(n,encoding='unicode')for n in tree))
 def test_hidden_or_effectful_original_label_or_parent_group_holds(self):
  content=highlight(visible_fixture())
  tree=ET.fromstring(raw_xml(content)).find(P+'cSld/'+P+'spTree');node=next(n for n in tree if completion._text(n.find(P+'txBody'))=='YES');node.find(P+'nvSpPr/'+P+'cNvPr').set('hidden','1');self.held(''.join(ET.tostring(n,encoding='unicode')for n in tree))
  for mode in ('hidden','effect'):
   tree=ET.fromstring(raw_xml(content)).find(P+'cSld/'+P+'spTree');node=next(n for n in tree if completion._text(n.find(P+'txBody'))=='YES');tree.remove(node)
   group=ET.SubElement(tree,P+'grpSp');nv=ET.SubElement(group,P+'nvGrpSpPr');ET.SubElement(nv,P+'cNvPr',{'id':'9','name':'group',**({'hidden':'1'}if mode=='hidden'else{})});properties=ET.SubElement(group,P+'grpSpPr')
   if mode=='effect':ET.SubElement(properties,A+'effectLst')
   group.append(node);self.held(''.join(ET.tostring(n,encoding='unicode')for n in tree))
 def test_lost_teacher_descriptor_or_changed_marker_cannot_supply_trust(self):
  student=completion.read_deck(raw(highlight(visible_fixture())));teacher=completion.read_deck(raw(visible_fixture()));teacher['slides'][0]['units']=[u for u in teacher['slides'][0]['units']if not u.get('binding')];self.assertTrue(completion.assess(student,teacher,found_codes_only=True)['note'])
  content=highlight(visible_fixture()).replace('>[[NOTES:F04:Q-MASTERY-4]]<','>[[NOTES:F04:Q-WRONG]]<');self.held(content)
 def test_real_revision_writing_stays_separate_from_option_completion(self):
  content=highlight(visible_fixture());tree=ET.fromstring(raw_xml(content)).find(P+'cSld/'+P+'spTree');node=next(n for n in tree if n.find(P+'nvSpPr/'+P+'cNvPr').get('name')=='revision-response');node.find(P+'txBody/'+A+'p/'+A+'r/'+A+'t').text='Good lighting and a clear view improve reliability.'
  r=assess(''.join(ET.tostring(n,encoding='unicode')for n in tree));self.assertEqual([r['counts'][c]['done']for c in('NOTES','DRAFT')],[1,1]);draft=next(i for i in r['items']if i['category']=='DRAFT');self.assertIn('Good lighting',draft['answer']);self.assertNotIn('completion_evidence',draft)

if __name__=='__main__':unittest.main()
