import unittest
from unittest.mock import patch
from school_workflow_hub.adapters.submission_inventory import collect, FOLDER, ConnectionError

ROOT='fictional_folder_123456789'
def item(key, kind, parent=ROOT):
    return {'id':key,'name':'Work','mimeType':kind,'parents':[parent],'trashed':False}
class Reader:
    def metadata(self, key):
        return {'id':key,'name':'Forensics — Wk 03 — Observation — Turn In','mimeType':FOLDER,'trashed':False}
    def children(self, key, cursor):
        if key==ROOT and cursor is None:
            return {'files':[item('nested', FOLDER)], 'nextPageToken':'two'}
        if key==ROOT:
            return {'files':[item('doc','application/pdf')]}
        return {'files':[item('deck','application/vnd.google-apps.presentation','nested')]}
class InventoryTests(unittest.TestCase):
    def test_nested_and_paginated(self):
        _, files, folders, forms=collect(Reader(),ROOT,course='Forensics',week='Wk 03')
        self.assertEqual([f['id'] for f in files],['deck','doc'])
        self.assertEqual((folders,forms),(2,0))
    def test_wrong_lesson_rejected(self):
        with self.assertRaisesRegex(ConnectionError,'mapping'):
            collect(Reader(),ROOT,course='Forensics',week='Wk 04')
    def test_partial_rejected(self):
        reader=Reader()
        reader.children=lambda *_:{'files':[],'incompleteSearch':True}
        with self.assertRaisesRegex(ConnectionError,'incomplete'):
            collect(reader,ROOT,course='Forensics',week='Wk 03')
    def test_repeated_cursor_rejected(self):
        reader=Reader()
        reader.children=lambda *_:{'files':[],'nextPageToken':'same'}
        with self.assertRaisesRegex(ConnectionError,'incomplete'):
            collect(reader,ROOT,course='Forensics',week='Wk 03')
    def test_wrong_parent_rejected(self):
        reader=Reader()
        reader.children=lambda *_:{'files':[item('doc','application/pdf','other')]}
        with self.assertRaisesRegex(ConnectionError,'incomplete'):
            collect(reader,ROOT,course='Forensics',week='Wk 03')
    def test_signed_identity_required(self):
        from school_workflow_hub.web import application
        from school_workflow_hub.adapters.hosted_agenda import ConnectionError
        statuses=[]
        with patch('school_workflow_hub.adapters.submission_inventory.read_day',side_effect=ConnectionError('sign_in_required')):
            response=application({'PATH_INFO':'/api/submission-inventory','REQUEST_METHOD':'GET','QUERY_STRING':'date=2026-09-16&class_key=forensics'},lambda status,headers:statuses.append(status))
        self.assertEqual(statuses,['403 Forbidden'])
        self.assertNotIn(b'files',b''.join(response))
