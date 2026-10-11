"""Fictional theme aliasing must preserve byte payloads and their actual roles."""
from copy import deepcopy
import io,json,unittest,zipfile
from xml.etree import ElementTree as E
from hub_v2 import completion as c,response_binding as rb,writing_fields as w
from test_original_writing_fields import fixture,package,write,P,A

THEME='application/vnd.openxmlformats-officedocument.theme+xml'
MASTER='application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml'

def extras(swapped=False):
    one=b'<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Alpha"/>'
    two=b'<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Beta"/>'
    types=E.Element(w.CT+'Types')
    for name,mime in [('theme/theme1.xml',THEME),('theme/theme2.xml',THEME),('slideMasters/slideMaster1.xml',MASTER)]:
        E.SubElement(types,w.CT+'Override',PartName='/ppt/'+name,ContentType=mime)
    def rel(target):
        node=E.Element(w.REL+'Relationships');E.SubElement(node,w.REL+'Relationship',Id='rId1',Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme',Target=target)
        return E.tostring(node)
    return {'[Content_Types].xml':E.tostring(types),'ppt/theme/theme1.xml':two if swapped else one,'ppt/theme/theme2.xml':one if swapped else two,
            'ppt/slideMasters/slideMaster1.xml':b'<p:sldMaster xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
            'ppt/_rels/presentation.xml.rels':rel('theme/theme2.xml'if swapped else 'theme/theme1.xml'),
            'ppt/slideMasters/_rels/slideMaster1.xml.rels':rel('../theme/theme1.xml'if swapped else '../theme/theme2.xml')}

def teacher(data=None):
    data=extras() if data is None else data;tree=fixture(extras=data);prop=tree[1].find(P+'nvSpPr/'+P+'cNvPr');bound=json.loads(prop.get('descr')[len(rb.PREFIX):])
    scene=c.read_deck(package(tree,data))['slides'][0]['writing_scene'];bound['resources_basis']=w.STYLE_ROLES;bound['resources']=scene['style_resource_roles'];prop.set('descr',rb.PREFIX+json.dumps(bound))
    return tree,data

def assess(tree,data,template,original):
    return c.assess(c.read_deck(package(tree,data)),c.read_deck(package(template,original)),found_codes_only=True)

class OriginalWritingStyleResourceRolesTests(unittest.TestCase):
    def test_theme_name_permutation_keeps_authentic_role_and_own_response(self):
        t,e=teacher();renamed=extras(True)
        with zipfile.ZipFile(io.BytesIO(package(t,e)))as a,zipfile.ZipFile(io.BytesIO(package(t,renamed)))as b:
            self.assertEqual(w.style_resource_roles(a),w.style_resource_roles(b))
        result=assess(write(t,'Blood'),renamed,t,e)
        self.assertIsNone(result['note']);self.assertEqual(result['counts']['NOTES'],{'done':1,'required':1})
        self.assertEqual(result['items'][0]['answer'],'Blood')
    def test_same_payload_multiset_with_wrong_active_theme_role_holds(self):
        t,e=teacher();bad=extras(True);bad['ppt/_rels/presentation.xml.rels']=e['ppt/_rels/presentation.xml.rels']
        result=assess(write(t,'Blood'),bad,t,e);self.assertTrue(result['note']);self.assertIsNone(result['notes_score'])
    def test_changed_theme_or_literal_master_bytes_holds(self):
        t,e=teacher()
        for name in ('ppt/theme/theme1.xml','ppt/slideMasters/slideMaster1.xml'):
            bad=deepcopy(e);bad[name]=bad[name].replace(b'/>',b' changed="yes"/>')
            with self.subTest(name=name):
                result=assess(write(t,'Blood'),bad,t,e);self.assertTrue(result['note']);self.assertIsNone(result['notes_score'])
    def test_missing_duplicate_mime_external_and_malformed_styles_hold(self):
        t,e=teacher()
        for mode in ('missing','duplicate','mime','external','malformed','oversized','role_duplicate'):
            bad=deepcopy(e)
            if mode in ('missing','duplicate','mime'):
                types=E.fromstring(bad['[Content_Types].xml'])
                if mode=='missing':types.remove(types[0])
                elif mode=='duplicate':types.append(deepcopy(types[0]))
                else:types[0].set('ContentType','application/octet-stream')
                bad['[Content_Types].xml']=E.tostring(types)
            elif mode in ('external','role_duplicate'):
                rel=E.fromstring(bad['ppt/_rels/presentation.xml.rels'])
                if mode=='external':rel[0].set('TargetMode','External')
                else:rel.append(deepcopy(rel[0]))
                bad['ppt/_rels/presentation.xml.rels']=E.tostring(rel)
            elif mode=='malformed':bad['ppt/theme/theme1.xml']=b'<incomplete'
            else:bad['ppt/theme/theme1.xml']=b'<root>'+b' '*2_000_000+b'</root>'
            with self.subTest(mode=mode):
                result=assess(write(t,'Blood'),bad,t,e);self.assertTrue(result['note']);self.assertIsNone(result['notes_score'])
    def test_unknown_basis_and_legacy_raw_signature_are_not_aliased(self):
        t,e=teacher();bad=deepcopy(t);p=bad[1].find(P+'nvSpPr/'+P+'cNvPr');d=json.loads(p.get('descr')[len(rb.PREFIX):]);d['resources_basis']='unknown';p.set('descr',rb.PREFIX+json.dumps(d))
        self.assertTrue(assess(write(bad,'Blood'),e,bad,e)['note'])
        legacy=fixture(extras=e);self.assertTrue(assess(write(legacy,'Blood'),extras(True),legacy,e)['note'])
    def test_question_artwork_geometry_and_paragraph_changes_still_hold(self):
        t,e=teacher()
        for mode in ('question','geometry','paragraph'):
            own=write(t,'Blood')
            if mode=='question':own[0].find(P+'txBody/'+A+'p/'+A+'r/'+A+'t').text='Another question'
            elif mode=='geometry':own[1].find(P+'spPr/'+A+'xfrm/'+A+'off').set('x','100001')
            else:E.SubElement(own[1].find(P+'txBody/'+A+'p'),A+'pPr',{'marL':'91440000'})
            with self.subTest(mode=mode):self.assertTrue(assess(own,extras(True),t,e)['note'])
    def test_fixed_box_autofit_remains_literal_not_general_normalization(self):
        t,e=teacher();old=w.xml_surface(t[1]);body=t[1].find(P+'txBody/'+A+'bodyPr');E.SubElement(body,A+'spAutoFit');fitted=w.xml_surface(t[1]);body[0].tag=A+'noAutofit'
        self.assertNotEqual(old,fitted);self.assertNotEqual(fitted,w.xml_surface(t[1]))
        self.assertTrue(assess(write(t,'Blood'),e,fixture(extras=e),e)['note'])
    def test_unknown_style_root_in_both_authenticated_teacher_and_student_holds(self):
        data=extras();data['ppt/theme/theme1.xml']=b'<unknown/>'
        t,e=teacher(data)
        result=assess(write(t,'Blood'),e,t,e)
        self.assertTrue(result['note']);self.assertIsNone(result['notes_score'])

if __name__=='__main__':unittest.main()
