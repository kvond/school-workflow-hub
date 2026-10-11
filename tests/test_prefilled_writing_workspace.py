"""An authentic heading workspace is one activity and never borrowed writing."""
from copy import deepcopy
import io
import json
import unittest
import zipfile
import xml.etree.ElementTree as E

from hub_v2 import completion as c, response_binding as rb, workspace_binding as w
P,A=c.P,c.A
SIZE=(1000000,750000)
FIELD=[100000,200000,800000,400000]
LINK=[100000,620000,800000,30000]
TOKEN='[[NOTES:F09:TRACE]]'


def shape(name,text,box,marker=False,description=None):
 node=E.Element(P+'sp');nv=E.SubElement(node,P+'nvSpPr');E.SubElement(nv,P+'cNvPr',{'id':str(sum(map(ord,name))),'name':name,**({'descr':rb.PREFIX+json.dumps(description)}if description else{})});E.SubElement(nv,P+'cNvSpPr',{'txBox':'1'});E.SubElement(nv,P+'nvPr')
 props=E.SubElement(node,P+'spPr');xfrm=E.SubElement(props,A+'xfrm');E.SubElement(xfrm,A+'off',dict(x=str(box[0]),y=str(box[1])));E.SubElement(xfrm,A+'ext',dict(cx=str(box[2]),cy=str(box[3])));E.SubElement(props,A+'noFill');E.SubElement(E.SubElement(props,A+'ln'),A+'noFill')
 body=E.SubElement(node,P+'txBody');E.SubElement(body,A+'bodyPr');E.SubElement(body,A+'lstStyle')
 for line in text.split('\n'):
  p=E.SubElement(body,A+'p');r=E.SubElement(p,A+'r');st=E.SubElement(r,A+'rPr',{'sz':'100'if marker else'1800'});clr=E.SubElement(E.SubElement(st,A+'solidFill'),A+'srgbClr',{'val':'000000'})
  if marker:E.SubElement(clr,A+'alpha',{'val':'0'})
  E.SubElement(r,A+'t').text=line
 return node


def package(trees,extras=None):
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w')as z:
  pres=E.Element(P+'presentation');E.SubElement(pres,P+'sldSz',dict(cx=str(SIZE[0]),cy=str(SIZE[1])));z.writestr('ppt/presentation.xml',E.tostring(pres))
  for i,tree in enumerate(trees,1):
   root=E.Element(P+'sld');E.SubElement(root,P+'cSld').append(deepcopy(tree));z.writestr(f'ppt/slides/slide{i}.xml',E.tostring(root))
  for n,b in(extras or{}).items():z.writestr(n,b)
 return out.getvalue()


def fixture():
 prompt=E.Element(P+'spTree');page=E.Element(P+'spTree')
 texts=['List the small traces you can find.','Compare your list with a partner.','Discuss their origin and transfer.']
 for i,t in enumerate(texts):prompt.append(shape(f'prompt-{i}',t,[100000,30000+i*50000,800000,40000]))
 page.append(shape('workspace','TRACE EVIDENCE',FIELD));page.append(shape('link','Trace Transfer Lab Sheet',LINK))
 teacher0=package([prompt,page]);parsed0=c.read_deck(teacher0);ps=w.read_shapes(prompt,box=c._box,affine=c._affine,name=c._name);fields=w.read_shapes(page,box=c._box,affine=c._affine,name=c._name);f,l=fields
 root=E.fromstring(zipfile.ZipFile(io.BytesIO(teacher0)).read('ppt/slides/slide2.xml'));pr=E.fromstring(zipfile.ZipFile(io.BytesIO(teacher0)).read('ppt/slides/slide1.xml'))
 empty=w._sha([]);data={'kind':w.KIND,'page_size':list(SIZE),'prompt_page':1,'marker_box':[400000,300000,1000,1000],
  'workspace':{'heading':f['text'],'box':list(f['box']),'received_boxes':[list(f['box']),[100000,212669,800000,400000]],'paint_sha256':f['paint_sha256'],'body_style_sha256':f['body_style_sha256'],'order':f['order']},
  'prompts':[{'text':s['text'],'box':list(s['box']),'sha256':s['sha256'],'received_sha256':[s['sha256']]}for s in ps],
  'other_original_shapes':[{'text':l['text'],'box':list(l['box']),'sha256':l['sha256'],'received_sha256':[l['sha256']],'order':l['order']}],
  'theme_signatures':[empty],'resource_signatures':[empty],'slide_style_signatures':[w.slide_style(page,root)],'prompt_style_signatures':[w.slide_style(prompt,pr)],
  'visible_run_styles':sorted({v for v in f['run_style_sha256']}),'visible_paragraph_styles':sorted({v for v in f['paragraph_style_sha256']}),'visible_break_styles':[],
  'prompt_scene_signatures':[w.scene(ps,{})],'workspace_media_signatures':[empty]}
 extra=shape('extra','Fibers',[120000,240000,700000,300000]);e=w.read_shapes(E.fromstring('<root/>'),box=c._box,affine=c._affine,name=c._name)
 tree=E.Element(P+'spTree');tree.append(extra);e=w.read_shapes(tree,box=c._box,affine=c._affine,name=c._name)[0]
 data['extra_response']={k:list(e[k])if k=='box'else e[k]for k in('box','paint_sha256','body_style_sha256')}
 page.append(shape('code',TOKEN,data['marker_box'],True,data));return[prompt,page]


def write(trees,text='TRACE EVIDENCE\nBlood'):
 trees=deepcopy(trees);old=trees[1][0];new=shape('workspace',text,FIELD);oldbody=old.find(P+'txBody');old.remove(oldbody);old.append(new.find(P+'txBody'));return trees


def assess(trees,teacher=None,extras=None):
 return c.assess(c.read_deck(package(trees,extras)),c.read_deck(package(teacher or fixture())),found_codes_only=True)


class PrefilledWorkspaceTests(unittest.TestCase):
 def held(self,trees,teacher=None,extras=None):
  result=assess(trees,teacher,extras);self.assertTrue(result['note']);self.assertIsNone(result['notes_score']);self.assertEqual(result['counts']['NOTES']['done'],0)
 def test_heading_only_and_punctuation_are_blank_not_student_writing(self):
  for text in('TRACE EVIDENCE','TRACE EVIDENCE\n...'):
   r=assess(write(fixture(),text));self.assertEqual(r['counts']['NOTES'],{'done':0,'required':1});self.assertFalse(r['note'])
 def test_one_activity_preserves_real_answer_and_scoped_line_breaks(self):
  t=write(fixture());body=t[1][0].find(P+'txBody');p=body.find(A+'p');body.remove(body.findall(A+'p')[1]);E.SubElement(p,A+'br');E.SubElement(p,A+'br');r=deepcopy(p.find(A+'r'));r.find(A+'t').text='Blood';p.append(r)
  data=json.loads(t[1][-1].find(P+'nvSpPr/'+P+'cNvPr').get('descr').split('\n',1)[1]);data['visible_break_styles']=[w._sha(w._canonical(p.find(A+'br')))];teacher=fixture();teacher[1][-1].find(P+'nvSpPr/'+P+'cNvPr').set('descr',rb.PREFIX+json.dumps(data));t[1][-1].find(P+'nvSpPr/'+P+'cNvPr').set('descr',rb.PREFIX+json.dumps(data))
  r=assess(t,teacher);self.assertEqual(r['counts']['NOTES'],{'done':1,'required':1});self.assertEqual(r['items'][0]['answer'],'Blood');self.assertNotIn('completion_evidence',r['items'][0])
 def test_copied_heading_link_directions_and_combined_directions_do_not_credit(self):
  base=fixture();texts=[c._text(n.find(P+'txBody'))for n in base[0]]
  for value in['TRACE EVIDENCE','Trace Transfer Lab Sheet',*texts,'\n'.join(texts)]:
   r=assess(write(base,'TRACE EVIDENCE\n'+value));self.assertEqual(r['counts']['NOTES']['done'],0)
 def test_exact_shift_and_contained_authentic_new_box_are_supported(self):
  t=write(fixture());t[1][0].find(P+'spPr/'+A+'xfrm/'+A+'off').set('y','212669');self.assertEqual(assess(t)['counts']['NOTES']['done'],1)
  t=fixture();t[1].append(shape('extra','Fibers',[120000,240000,700000,300000]));self.assertEqual(assess(t)['items'][0]['answer'],'Fibers')
 def test_other_shift_outside_cross_question_duplicate_and_competing_writing_hold(self):
  t=write(fixture());t[1][0].find(P+'spPr/'+A+'xfrm/'+A+'off').set('y','212670');self.held(t)
  for mode in('outside','cross-question','two','competing'):
   t=write(fixture())if mode=='competing'else fixture();t[1].append(shape('extra','Fibers',[120000,240000,700000,300000]))
   if mode=='two':t[1].append(deepcopy(t[1][-1]))
   if mode in('outside','cross-question'):t[1][-1].find(P+'spPr/'+A+'xfrm/'+A+'off').set('y','50000')
   self.held(t)
 def test_prompt_label_geometry_other_source_marker_or_teacher_trust_changes_hold(self):
  for mode in('prompt','heading','geometry','link','code','trust','descriptor'):
   t=write(fixture());teacher=fixture()
   if mode=='prompt':t[0][0].find(P+'txBody/'+A+'p/'+A+'r/'+A+'t').text='Different task'
   if mode=='heading':t[1][0].find(P+'txBody/'+A+'p/'+A+'r/'+A+'t').text='Different heading'
   if mode=='geometry':t[0][0].find(P+'spPr/'+A+'xfrm/'+A+'off').set('x','100001')
   if mode=='link':t[1][1].find(P+'txBody/'+A+'p/'+A+'r/'+A+'t').text='An answer here'
   if mode=='code':t[1][-1].find(P+'txBody/'+A+'p/'+A+'r/'+A+'t').text='[[NOTES:F09:WRONG]]'
   if mode=='trust':teacher[1].remove(teacher[1][-1])
   if mode=='descriptor':t[1][-1].find(P+'nvSpPr/'+P+'cNvPr').set('descr',rb.PREFIX+'{}')
   self.held(t,teacher)
 def test_hidden_alpha_zero_unknown_styles_effects_or_source_resources_hold(self):
  for mode in('hidden','alpha','paragraph','body','group','paint','bg'):
   t=write(fixture());node=t[1][0]
   if mode=='hidden':node.find(P+'nvSpPr/'+P+'cNvPr').set('hidden','1')
   if mode=='alpha':E.SubElement(node.find(P+'txBody/'+A+'p/'+A+'r/'+A+'rPr/'+A+'solidFill/'+A+'srgbClr'),A+'alpha',{'val':'0'})
   if mode=='paragraph':E.SubElement(node.find(P+'txBody/'+A+'p'),A+'pPr',{'marL':'123'})
   if mode=='body':node.find(P+'txBody/'+A+'bodyPr').set('rot','5400000')
   if mode=='paint':E.SubElement(node.find(P+'spPr'),A+'effectLst')
   if mode=='group':
    t[1].remove(node);g=E.SubElement(t[1],P+'grpSp');nv=E.SubElement(g,P+'nvGrpSpPr');E.SubElement(nv,P+'cNvPr',{'id':'88','name':'hidden','hidden':'1'});E.SubElement(g,P+'grpSpPr');g.append(node)
   if mode=='bg':E.SubElement(t[1],P+'unknownPayload')
   self.held(t)
  self.held(write(fixture()),extras={'ppt/theme/theme1.xml':b'<changed-theme/>'})
 def test_no_workspace_response_cannot_borrow_other_question_or_add_point(self):
  t=fixture();r=assess(t);self.assertEqual(r['counts']['NOTES'],{'done':0,'required':1});self.assertEqual(r['counts']['DRAFT']['required'],0)
 def test_complete_prompt_scene_missing_duplicate_reordered_moved_and_opaque_cover_hold(self):
  for mode in('missing','duplicate','order','moved','cover'):
   t=write(fixture());scene=t[0]
   if mode=='missing':scene.remove(scene[0])
   if mode=='duplicate':scene.append(deepcopy(scene[0]))
   if mode=='order':first=scene[0];scene.remove(first);scene.append(first)
   if mode=='moved':scene[0].find(P+'spPr/'+A+'xfrm/'+A+'off').set('x','100001')
   if mode=='cover':
    overlay=shape('cover','',list((0,0,*SIZE)));props=overlay.find(P+'spPr');props.remove(props.find(A+'noFill'));E.SubElement(E.SubElement(props,A+'solidFill'),A+'srgbClr',{'val':'FFFFFF'});scene.append(overlay)
   self.held(t)


if __name__=='__main__':unittest.main()
