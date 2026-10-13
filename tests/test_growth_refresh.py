"""Future evidence binds existing graphs; unsafe and uncertain updates hold."""
from copy import deepcopy
from struct import pack, unpack
import unittest
from unittest.mock import patch
from hub_v2 import growth_refresh as refresh, teaching_report as report, google
from school_workflow_hub.core.cloud_state import Conflict
from tests.test_cloud_state import MemoryCAS


ROSTER = [{'student_key':'one','name':'Rivera, Ana','section':'A4 Biology'},
          {'student_key':'two','name':'Bennett, Ben','section':'A4 Biology'}]
CONFIG = {'HUB_BIOLOGY_ANALYSIS_URL':'https://docs.google.com/spreadsheets/d/FictionalAnalysis00001/edit',
          'HUB_GROWTH_REPORT_URL':'https://docs.google.com/spreadsheets/d/FictionalDTGSS000001/edit'}
A, D = 'FictionalAnalysis00001', 'FictionalDTGSS000001'


def observations(lessons=('earlier','later')):
    rows = []
    for student in ROSTER:
        for lesson in lessons:
            for i, dimension in enumerate(report.DIMENSIONS):
                ident = student['student_key']+'-'+lesson
                rated = student['student_key']=='two' or i==0 or lesson=='future'
                rows.append({'student_key':student['student_key'],'section':student['section'],
                    'class_key':'biology','lesson_key':lesson,'lesson':lesson.title()+' full source title',
                    'date':{'earlier':'2026-09-25','later':'2026-10-01','future':'2026-10-07'}[lesson],
                    'assessed_at':'2026-10-09','source_date_basis':'form_receipt',
                    'source_file_id':ident,'source_revision':'3','source_url':'https://drive.google.com/file/d/'+ident+'/view',
                    'rubric_version':'science-writing-four-dimensions/v1','stage':'DRAFT','dimension':dimension,
                    'level': ('Developing' if lesson=='earlier' else 'Proficient' if lesson=='later' else 'Extending') if rated else 'Evidence unavailable',
                    'quote':'A specific fictional source answer.' if rated else ''})
    return rows


def cell(value):
    return {'userEnteredValue': {'numberValue':value} if isinstance(value,(int,float)) else {'stringValue':str(value or '')}}


def books(old):
    value = report.growth_tables(old,ROSTER); result={}
    for ident, tables, chart_tables, figures in (
            (A, {k:value['tables'][k] for k in ('Student growth','Student figure data')},[],value['student_figures']),
            (D, {k:value['tables'][k] for k in ('Across lessons','Growth figure data','Growth evidence limits')},['Growth figure data'],[])):
        for rows in tables.values():
            for row in rows:
                for i,x in enumerate(row):
                    if isinstance(x,str) and ' | ' in x: row[i]=x.replace(' full source title','')
        book={'spreadsheetId':ident,'sheets':[{'properties':{'title':'Teaching plan','sheetId':9,'gridProperties':{'rowCount':100}},
            'data':[{'rowData':[{'values':[cell('TRUE'),cell('Preserve my manual note')]}]}]}]}
        planned=report.additive_growth_plan(book,tables,chart_tables=chart_tables,student_figures=figures)
        for request in planned['requests']:
            if 'addSheet' in request: book['sheets'].append({'properties':deepcopy(request['addSheet']['properties'])})
            if 'updateCells' in request:
                update=request['updateCells']; sheet=next(s for s in book['sheets'] if s['properties']['sheetId']==update['range']['sheetId'])
                sheet['data']=[{'rowData':deepcopy(update['rows'])}]
            if 'addChart' in request:
                chart=deepcopy(request['addChart']['chart']); chart['chartId']=100+sum(len(s.get('charts',[])) for s in book['sheets'])
                sheet=next(s for s in book['sheets'] if s['properties']['sheetId']==chart['position']['overlayPosition']['anchorCell']['sheetId'])
                sheet.setdefault('charts',[]).append(chart)
        result[ident]=book
    for book in result.values():
        for sheet in book['sheets']:
            if sheet.get('data'): sheet['data'][0]['rowData'][0]['values'][0].update(note='Teacher cell note',userEnteredFormat={'textFormat':{'bold':True}})
            for chart in sheet.get('charts',[]):
                for item in chart['spec']['basicChart']['series']:
                    rgb=item.get('colorStyle',{}).get('rgbColor',{})
                    for k,v in rgb.items(): rgb[k]=unpack('f',pack('f',v))[0]
    return result


class GrowthRefreshTests(unittest.TestCase):
    def setUp(self):
        self.port=MemoryCAS(); self.store_patch=patch.object(report,'configured_store',return_value=self.port)
        self.store_patch.start(); self.addCleanup(self.store_patch.stop)
    def run_refresh(self, mutate=None, *, lost=False, failed=False, provider=False, evidence_changed=False, book_changed=False, lease=False):
        old=observations(); new=observations(('earlier','later','future')); self.books=books(old); self.before=deepcopy(self.books); self.calls=[]
        if mutate: mutate(self.books,new)
        reads={A:0,D:0}
        def meta(token,ident,fields):
            if provider: raise google.GoogleError('Workbook unavailable',503)
            return {'id':ident,'trashed':False,'mimeType':'application/vnd.google-apps.spreadsheet','capabilities':{'canEdit':True},'permissions':[{'type':'user','emailAddress':'teacher@example.test'}]}
        def api(url,token,params=None,method='GET',body=None):
            ident=url.split('/spreadsheets/')[1].split(':')[0]; self.calls.append((method,ident))
            if method=='GET':
                reads[ident]+=1
                if book_changed and reads[ident]==2: self.books[ident]['sheets'][0]['data'][0]['rowData'][0]['values'][1]=cell('Concurrent teacher edit')
                return deepcopy(self.books[ident])
            if failed is True or failed=='second' and ident==D: raise google.GoogleError('Lost write response',503)
            for request in body['requests']:
                if 'appendDimension' in request:
                    value=request['appendDimension']; s=next(s for s in self.books[ident]['sheets'] if s['properties']['sheetId']==value['sheetId']); s['properties']['gridProperties']['rowCount']+=value['length']
                if 'updateCells' in request:
                    value=request['updateCells']; s=next(s for s in self.books[ident]['sheets'] if s['properties']['sheetId']==value['range']['sheetId'])
                    rows=s.setdefault('data',[{}])[0].setdefault('rowData',[])
                    for i in range(value['range']['endRowIndex']):
                        if i>=len(rows): rows.append({'values':[]})
                        values=rows[i].setdefault('values',[])
                        for j in range(value['range']['endColumnIndex']):
                            if j>=len(values): values.append({})
                            values[j].pop('userEnteredValue',None)
                            if i<len(value['rows']) and j<len(value['rows'][i]['values']): values[j].update(deepcopy(value['rows'][i]['values'][j]))
                if 'updateChartSpec' in request:
                    value=request['updateChartSpec']; c=next(c for s in self.books[ident]['sheets'] for c in s.get('charts',[]) if c['chartId']==value['chartId']); c['spec']=deepcopy(value['spec'])
                    for item in c['spec']['basicChart']['series']:
                        rgb=item.get('colorStyle',{}).get('rgbColor',{})
                        for k,v in rgb.items(): rgb[k]=unpack('f',pack('f',v))[0]
            if lost: raise google.GoogleError('Lost write response',503)
            return {}
        evidence=[new,new+observations(('future',))] if evidence_changed else [new]*4
        renewals=0
        def renew():
            nonlocal renewals
            renewals+=1
            if lease is True or lease=='after_writes' and renewals==4: raise Conflict('Ownership lost')
        with patch.object(report,'read_observations',side_effect=evidence),patch.object(refresh.google,'drive_file',side_effect=meta),patch.object(refresh.google,'get_json',side_effect=api):
            return refresh.refresh('read','write',CONFIG,ROSTER,renew)

    def test_future_lesson_changes_paired_metrics_and_owned_graph_sources_preserving_style_and_notes(self):
        result=self.run_refresh(lost=True); self.assertEqual(result['status'],'verified',result)
        self.assertEqual([c for c in self.calls if c[0]=='POST'], [('POST',A),('POST',D)])
        for index,call in enumerate(self.calls):
            if call[0]=='POST': self.assertEqual(self.calls[index+1],('GET',call[1]))
        mapping={s['properties']['title']:s for s in self.books[D]['sheets']}
        rows=report._sheet_rows(mapping['Across lessons'])
        future=next(r for r in rows[1:] if r[2]==report.DIMENSIONS[0] and r[5].startswith('Earlier') and r[6].startswith('Future'))
        self.assertEqual(future[8:17],[2,0,2,0,1,100,2,0,0])
        self.assertEqual(len(rows)-1,12)
        for ident,book in self.books.items():
            for before,sheet in zip(self.before[ident]['sheets'],book['sheets']):
                if sheet['properties']['title']=='Teaching plan': self.assertEqual(before,sheet)
                if sheet.get('data'):
                    self.assertEqual(sheet['data'][0]['rowData'][0]['values'][0]['note'],'Teacher cell note')
                    self.assertEqual(sheet['data'][0]['rowData'][0]['values'][0]['userEnteredFormat'],before['data'][0]['rowData'][0]['values'][0]['userEnteredFormat'])
                for old,chart in zip(before.get('charts',[]),sheet.get('charts',[])):
                    self.assertEqual(chart['position'],old['position'])
                    self.assertEqual(chart['spec']['basicChart']['axis'],old['spec']['basicChart']['axis'])
                    for original in old['spec']['basicChart']['series']:
                        match=next(s for s in chart['spec']['basicChart']['series'] if s['series']['sourceRange']['sources'][0]['startColumnIndex']==original['series']['sourceRange']['sources'][0]['startColumnIndex'])
                        self.assertEqual({k:v for k,v in original.items() if k!='series'},{k:v for k,v in match.items() if k!='series'})
                    self.assertTrue(all(r['endRowIndex']>old['spec']['basicChart']['domains'][0]['domain']['sourceRange']['sources'][0]['endRowIndex'] for r in refresh._sources(chart['spec'])))
        charts=[c for s in self.books[A]['sheets'] for c in s.get('charts',[])]
        self.assertEqual([len(c['spec']['basicChart']['series']) for c in charts],[4,4])
        repeated=refresh.plan(self.books,A,D,observations(('earlier','later','future')),ROSTER)
        self.assertTrue(all(not p['requests'] for p in repeated.values()))

    def test_ownership_or_source_evidence_ambiguity_never_writes(self):
        def bad_chart(book,new):
            chart=next(c for s in book[A]['sheets'] for c in s.get('charts',[])); refresh._sources(chart['spec'])[0]['startRowIndex']+=1
        def bad_source(book,new):
            sheet=next(s for s in book[A]['sheets'] if s['properties']['title']=='Student growth'); sheet['data'][0]['rowData'][1]['values'][13]=cell('https://drive.google.com/file/d/wrong-original/view')
        for mutate in (bad_chart,bad_source):
            with self.subTest(mutate=mutate):
                self.assertEqual(self.run_refresh(mutate)['status'],'held'); self.assertFalse(any(c[0]=='POST' for c in self.calls))

    def test_two_dates_on_new_rubric_need_a_separate_chart_without_combining_versions(self):
        def add_rubric(book,new):
            other=observations(); new.extend({**r,'rubric_version':'new-rubric/v2'} for r in other)
        result=self.run_refresh(add_rubric)
        self.assertEqual(result['status'],'held'); self.assertIn('rubric/field',result['problem']); self.assertFalse(any(c[0]=='POST' for c in self.calls))

    def test_unrelated_discontiguous_pie_chart_is_preserved(self):
        def add_chart(book,new):
            book[A]['sheets'][0]['charts']=[{'chartId':999,'spec':{'pieChart':{'domain':{'sourceRange':{'sources':[{'sheetId':9,'endRowIndex':4},{'sheetId':9,'startRowIndex':8,'endRowIndex':12}]}}}},'position':{'newSheet':True}}]
        result=self.run_refresh(add_chart)
        self.assertEqual(result['status'],'verified',result)
        self.assertEqual(self.books[A]['sheets'][0]['charts'][0]['chartId'],999)

    def test_owned_chart_copies_on_other_tabs_bind_new_ranges_without_moving(self):
        def copy_chart(book,new):
            for ident in (A,D):
                original=next(c for s in book[ident]['sheets'] for c in s.get('charts',[])); copied=deepcopy(original)
                copied['chartId']+=1000; copied['position']['overlayPosition']['anchorCell']={'sheetId':77,'rowIndex':1,'columnIndex':0}
                book[ident]['sheets'].append({'properties':{'title':'Growth figures','sheetId':77,'gridProperties':{'rowCount':100}},'charts':[copied]})
        result=self.run_refresh(copy_chart); self.assertEqual(result['status'],'verified',result)
        for ident in (A,D):
            copied=self.books[ident]['sheets'][-1]['charts'][0]
            self.assertEqual(copied['position']['overlayPosition']['anchorCell'],{'sheetId':77,'rowIndex':1,'columnIndex':0})
            original=next(c for s in self.books[ident]['sheets'][:-1] for c in s.get('charts',[]))
            self.assertEqual(refresh._sources(copied['spec']),refresh._sources(original['spec']))

    def test_new_field_requires_separate_owner_even_when_names_and_dates_match(self):
        def add_field(book,new): new.extend({**r,'stage':'NOTES'} for r in observations())
        result=self.run_refresh(add_field)
        self.assertEqual(result['status'],'held'); self.assertFalse(any(c[0]=='POST' for c in self.calls))

    def test_provider_failure_or_changed_evidence_or_workbook_bookend_or_lease_loss_never_writes(self):
        for flag in ('provider','evidence_changed','book_changed','lease'):
            with self.subTest(flag=flag):
                self.assertEqual(self.run_refresh(**{flag:True})['status'],'held'); self.assertFalse(any(c[0]=='POST' for c in self.calls))

    def test_unconfirmed_post_stops_after_one_readback_without_second_workbook_or_retry(self):
        result=self.run_refresh(failed=True)
        self.assertEqual(result['status'],'held'); self.assertEqual(self.calls[-2:],[('POST',A),('GET',A)])
        self.assertEqual(sum(c[0]=='POST' for c in self.calls),1)

    def test_pending_partial_second_workbook_never_replays_on_a_later_run(self):
        result=self.run_refresh(failed='second'); self.assertEqual(result['status'],'held')
        self.assertEqual([c for c in self.calls if c[0]=='POST'], [('POST',A),('POST',D)])
        _,_,pending=refresh._reservation(CONFIG); self.assertEqual(pending.value['status'],'pending')
        self.assertNotEqual(self.books[A],self.before[A]); self.assertEqual(self.books[D],self.before[D])
        with patch.object(refresh,'_read',side_effect=lambda token,ident:deepcopy(self.books[ident])),patch.object(refresh.google,'get_json',side_effect=AssertionError('No automatic replay')):
            again=refresh.refresh('read','write',CONFIG,ROSTER,lambda:None)
        self.assertEqual(again['status'],'held'); self.assertIn('previous',again['problem'])
        self.assertEqual(refresh._reservation(CONFIG)[2].value,pending.value)

    def test_completed_books_reconcile_after_crash_without_any_replayed_post(self):
        result=self.run_refresh(lease='after_writes'); self.assertEqual(result['status'],'held')
        self.assertEqual(refresh._reservation(CONFIG)[2].value['status'],'pending')
        with patch.object(refresh,'_read',side_effect=lambda token,ident:deepcopy(self.books[ident])),patch.object(refresh.google,'get_json',side_effect=AssertionError('No automatic replay')):
            self.assertIsNone(refresh.recover('read',CONFIG,lambda:None))
        self.assertEqual(refresh._reservation(CONFIG)[2].value['status'],'verified')

    def test_growth_hold_preserves_saved_feedback_and_blocks_all_report_mutations(self):
        held={'status':'held','problem':'A previous growth write needs review.'}
        with patch.object(refresh,'recover',return_value=held),patch.object(refresh,'refresh',side_effect=AssertionError('No replay')),patch.object(report,'_refresh_writing_tables',return_value={'status':'verified'}) as base:
            result=report.refresh_writing_tables('read','write',CONFIG,ROSTER)
        self.assertEqual(result['status'],'held'); self.assertEqual(result['growth'],held)
        self.assertIn('Saved student feedback remains available',result['problem']); base.assert_not_called()

    def test_corrupt_verified_reservation_or_foreign_book_never_reads_any_workbook(self):
        self.run_refresh(); port,ident,doc=refresh._reservation(CONFIG)
        for changed in ({**doc.value,'schema':'foreign/v1'}, {**doc.value,'books':{'UnconfiguredForeignBook':{}}}):
            prior=port.read([ident]).documents[ident]; port.commit({ident:(changed,prior.version)})
            with patch.object(refresh,'_read',side_effect=AssertionError('No unbound workbook read')):
                result=refresh.recover('read',CONFIG,lambda:None)
            self.assertEqual(result['status'],'held')


if __name__=='__main__': unittest.main()
