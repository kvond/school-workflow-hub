import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from school_workflow_hub.adapters.agenda_prompts import read_prompts, ROWS
from school_workflow_hub.adapters.hosted_agenda import ConnectionError


def fixture():
    original=json.loads((Path(__file__).resolve().parents[1]/'web/teacher-prompts.json').read_text())
    rows=[{'values': []} for _ in range(10)]
    def put(row, a, b):
        rows[row-1]={'values':[{'formattedValue':a},{'formattedValue':b}]}
    put(1,'Workflow Prompts','Shared instructions')
    put(2,'Update together','Update Agenda pages, matching skills and prompts together.')
    put(3,'Version','fictional test version')
    for key,(row,label) in ROWS.items():put(row,label,original['prompts'][key])
    return {'sheets':[{'properties':{'title':'Workflow Prompts','sheetId':123},
                      'data':[{'startRow':0,'startColumn':0,'rowData':rows}]}]}


class Reader:
    def __init__(self,data): self.data=data;self.calls=[]
    def read(self,*args):self.calls.append(args);return self.data


class AgendaPromptsTests(unittest.TestCase):
    def read(self,data):
        reader=Reader(data)
        with patch('school_workflow_hub.adapters.agenda_prompts.authenticated_reader',return_value=(reader,'FICTIONAL_SOURCE')):
            result=read_prompts('assertion')
        self.assertEqual(reader.calls,[('FICTIONAL_SOURCE',("'Workflow Prompts'!A1:B10",))])
        return result
    def test_exact_prompts_and_direct_cell_links(self):
        r=self.read(fixture())
        self.assertTrue(all(r['bundled_matches'].values()))
        self.assertFalse(r['alignment_review_needed'])
        self.assertTrue(r['links']['feedback'].endswith('gid=123&range=B7'))
        self.assertTrue(r['links']['summary'].endswith('gid=123&range=B8'))
        self.assertEqual(r['external_writes'],0)
    def test_agenda_edit_is_returned_and_drift_marked(self):
        data=fixture();data['sheets'][0]['data'][0]['rowData'][6]['values'][1]['formattedValue']+='\nTeacher-approved edit.'
        r=self.read(data)
        self.assertTrue(r['alignment_review_needed'])
        self.assertTrue(r['prompts']['feedback'].endswith('Teacher-approved edit.'))
        self.assertFalse(r['bundled_matches']['feedback'])
    def test_missing_prompt_or_moved_label_fails_closed(self):
        for col in (0,1):
            data=fixture();data['sheets'][0]['data'][0]['rowData'][6]['values'][col]['formattedValue']=''
            with self.assertRaises(ConnectionError):self.read(data)
    def test_identity_failure_prevents_read(self):
        with patch('school_workflow_hub.adapters.agenda_prompts.authenticated_reader',side_effect=ConnectionError('sign_in_required')):
            with self.assertRaisesRegex(ConnectionError,'sign_in_required'):read_prompts(None)
