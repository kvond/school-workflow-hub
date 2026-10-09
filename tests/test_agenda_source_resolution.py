"""Current explicit links plus actual folders, never obsolete title matches."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch
import unittest
from hub_v2 import agenda, audit, completion, google
from test_hub_v2 import cell, grid, fixture, DIRECT
from test_completion_readiness import source

OLD='FICTIONALOLDDECK1234567890'
NEW='FICTIONALNEWDECK1234567890'
EXTRA='FICTIONALEXTRADECK1234567890'
FOLDER='FICTIONALLESSONFOLDER'
def deck_url(ident):return 'https://docs.google.com/presentation/d/'+ident+'/edit'


class HiddenSourceTests(unittest.TestCase):
    def cells(self, slot_header=''):
        return grid({1:['Wk','Topic','2nd deck','_studentURL','_teacherURL',slot_header,'_folderURL','_extraURL'],
                     2:['Wk 07','Mock trial','2nd: Mock trial',deck_url(OLD),deck_url(OLD),
                        cell('New source',hyperlink=deck_url(NEW)),
                        'https://drive.google.com/drive/folders/'+FOLDER,deck_url(EXTRA)]})

    def test_exact_reserved_hidden_slot_is_read_and_preserves_role(self):
        cells=self.cells();layout,problem=agenda.parse_class_tab(cells)
        self.assertIsNone(problem)
        found=agenda.build_lesson(cells,layout,2,'')
        self.assertEqual(layout['columns']['second_deck'],6)
        self.assertEqual(found['links']['deck2'],deck_url(NEW))
        self.assertIn({'label':'2nd deck','url':deck_url(NEW)},found['materials'])
        self.assertEqual(next(m['url'] for m in found['materials'] if m['label']=='Teacher deck'),deck_url(OLD))

    def test_explicit_deck2_url_header_and_rich_text_link_are_supported(self):
        cells=self.cells('_deck2URL')
        cells[2,6]=cell('Updated source',textFormatRuns=[{'startIndex':0,'format':{'link':{'uri':deck_url(NEW)}}}])
        layout,_=agenda.parse_class_tab(cells)
        self.assertEqual(agenda.build_lesson(cells,layout,2,'')['links']['deck2'],deck_url(NEW))

    def test_unrelated_unlabelled_or_named_slot_is_not_assigned_a_deck_role(self):
        cells=self.cells('Private note URL');layout,_=agenda.parse_class_tab(cells)
        self.assertNotIn(deck_url(NEW),[m['url'] for m in agenda.build_lesson(cells,layout,2,'')['materials']])
        cells=self.cells();cells[1,3]=cell('Some other field');layout,_=agenda.parse_class_tab(cells)
        self.assertNotIn(deck_url(NEW),[m['url'] for m in agenda.build_lesson(cells,layout,2,'')['materials']])


class ActualFolderTests(unittest.TestCase):
    def setUp(self):
        self.lesson={'topic':'Mock trial','materials':[
            {'label':'Teacher deck','url':deck_url(OLD)},
            {'label':'Student copy','url':deck_url(OLD)},
            {'label':'2nd deck','url':deck_url(NEW)},
            {'label':'Extra material','url':deck_url(EXTRA)},
            {'label':'Lesson folder','url':'https://drive.google.com/drive/folders/'+FOLDER}]}
        self.meta={FOLDER:{'id':FOLDER,'name':'Week 08b - Mock trial','mimeType':google.FOLDER,'trashed':False},
                   OLD:{'id':OLD,'name':'History of Forensics','mimeType':completion.SLIDES,'trashed':True},
                   EXTRA:{'id':EXTRA,'name':'Old Mock trial','mimeType':completion.SLIDES,'trashed':True},
                   NEW:{'id':NEW,'name':'Week 08b - Mock trial','mimeType':completion.SLIDES,'trashed':False,'version':'3'}}
        self.children=[self.meta[NEW],{'id':'RESOURCE','mimeType':'application/vnd.google-apps.document'}]

    def drive(self,token,ident,fields=None):return deepcopy(self.meta[ident])
    def resolve(self,children=None):
        with patch.object(google,'drive_file',side_effect=self.drive), \
             patch.object(google,'folder_children',return_value=self.children if children is None else children), \
             patch.object(google,'files_named') as search:
            result=completion.lesson_sources('read',self.lesson)
        search.assert_not_called();return result

    def test_only_one_actual_linked_deck_is_required_and_trash_is_informational(self):
        result=self.resolve()
        self.assertIsNone(result['problem'])
        self.assertEqual([d['id'] for d in result['decks']],[NEW])
        self.assertEqual({s['id'] for s in result['stale_links']},{OLD,EXTRA})
        self.assertEqual(result['folders'][0]['deck_ids'],[NEW])

    def test_unavailable_folder_is_not_mistaken_for_empty_or_replaced_by_title(self):
        with patch.object(google,'drive_file',side_effect=google.GoogleError('Not found or not shared.',404)), \
             patch.object(google,'folder_children') as listing,patch.object(google,'files_named') as search:
            result=completion.lesson_sources('read',self.lesson)
        self.assertIn('Cannot verify the actual Agenda lesson folder',result['problem'])
        listing.assert_not_called();search.assert_not_called()

    def test_empty_folder_or_incomplete_read_holds_without_export(self):
        self.assertIn('no current slide deck',self.resolve([])['problem'])
        with patch.object(google,'drive_file',side_effect=self.drive), \
             patch.object(google,'folder_children',side_effect=google.GoogleError('Incomplete folder read.')):
            self.assertIn('Incomplete folder read',completion.lesson_sources('read',self.lesson)['problem'])

    def test_wrong_folder_link_is_stale_but_arbitrary_unlinked_deck_is_not_selected(self):
        self.meta[OLD]['trashed']=False
        result=self.resolve([{'id':'UNLINKED','mimeType':completion.SLIDES}])
        self.assertEqual(result['decks'],[])
        self.assertIn('No current deck',result['problem'])
        self.assertTrue(all(s['reason']=='outside the current lesson folder' or s['reason']=='in trash' for s in result['stale_links']))

    def test_assignment_uses_current_second_source_without_title_week_override(self):
        a=fixture();a.lessons=lambda cls,week:[{**self.lesson,'forms':[{'form_id':'FORM'}]}]
        with patch.object(google,'drive_file',side_effect=self.drive), \
             patch.object(google,'folder_children',return_value=self.children), \
             patch.object(completion,'_template',return_value=(self.meta[NEW],source())) as template:
            result=completion.assignment_readiness('read',{},a,'forensics','Wk 04','FORM')
        self.assertEqual(result['status'],'READY')
        template.assert_called_once_with('read',NEW)
        self.assertEqual(result['source_resolution']['decks'][0]['title'],'Week 08b - Mock trial')

    def test_audit_shows_actual_current_source_and_does_not_flag_intentional_named_week(self):
        a=fixture();e=a.classes['forensics'];cells=e['cells']
        cells[5,16]=cell(deck_url(OLD));cells[5,17]=cell(deck_url(OLD))
        cells[5,18]=cell('https://drive.google.com/drive/folders/'+FOLDER)
        cells[5,19]=cell('')
        cells[5,22]=cell(deck_url(NEW));e['layout']['columns']['second_deck']=22
        with patch.object(google,'drive_file',side_effect=self.drive), \
             patch.object(google,'folder_children',return_value=self.children), \
             patch.object(audit,'_titles',return_value={OLD:{'trashed':True},NEW:'Week 08b - Mock trial'}), \
             patch.object(audit,'_published',return_value={}),patch.object(audit,'date') as clock, \
             patch.object(audit,'_release_deck',return_value={'status':'READY','counts':{'NOTES':1},'issues':[]}) as release:
            clock.today.return_value=date(2026,9,23)
            result=audit.check('read',a,'forensics',{})
        current=next(r for r in result['rows'] if r['week']=='Wk 04')
        self.assertEqual(current['release']['status'],'READY')
        release.assert_called_once_with('read',NEW)
        self.assertNotIn('deck-week',[f['kind'] for f in result['flags']])
        self.assertIn('stale-deck-link',[f['kind'] for f in result['flags']])

    def test_direct_trashed_template_cannot_export_or_reuse_cache(self):
        with patch.object(google,'drive_file',return_value=self.meta[OLD]),patch.object(completion,'_read') as read:
            with self.assertRaises(completion.Unreadable):completion._template('read',OLD)
        read.assert_not_called()

    def test_same_title_elsewhere_does_not_replace_current_lesson_source(self):
        from test_completion_readiness import work_result,FORM,STUDENT
        from test_hub_v2 import handout
        completion._templates.clear();completion._results.clear()
        with patch.object(completion,'_template',return_value=({'name':'Current linked deck'},completion.read_deck(handout()))), \
             patch.object(google,'drive_file',return_value={'mimeType':google.PPTX,'version':'1'}), \
             patch.object(completion,'_read',return_value=source('A supported answer.')), \
             patch.object(google,'files_named',return_value=[{'id':NEW}]) as search:
            result=completion.score_file('read',fixture(),'forensics','Wk 04',FORM,STUDENT,work_result())
        self.assertIn('No handed-out copy',result['problem'])
        search.assert_not_called()


if __name__=='__main__':unittest.main()
