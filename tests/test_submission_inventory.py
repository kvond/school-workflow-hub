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
    def test_teacher_folder_naming_is_accepted_without_losing_the_guard(self):
        from school_workflow_hub.adapters.submission_inventory import lesson_folder_name
        # The teacher's real names: a course alias and a cycle-labeled folder.
        self.assertTrue(lesson_folder_name('A&P — Wk 03 — Introduction to Anatomy (cont.) — Turn In','Anatomy','Wk 03'))
        self.assertTrue(lesson_folder_name('Biology — Cycle 02 — Ecosystems & Feeding Relationships — Turn In','Biology','Wk 03'))
        # The protections stay: wrong week, whole-course folder, wrong course,
        # and a cycle folder without any unit label are still rejected.
        self.assertFalse(lesson_folder_name('A&P — Wk 02 — Old Lesson — Turn In','Anatomy','Wk 03'))
        self.assertFalse(lesson_folder_name('Biology','Biology','Wk 03'))
        self.assertFalse(lesson_folder_name('Biology — Turn In','Biology','Wk 03'))
        self.assertFalse(lesson_folder_name('Forensics — Wk 03 — Observation — Turn In','Biology','Wk 03'))
        self.assertFalse(lesson_folder_name('Biology — Cycle 02 — Ecosystems','Biology','Wk 03'))
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

    def test_skip_name_guard_scans_a_ground_truth_folder(self):
        # A folder derived from an assignment's own submission files (e.g. a
        # Form's "(File responses)" folder) does not match the Agenda-week name
        # guard, but is a legitimate source when the guard is skipped.
        class Responses(Reader):
            def metadata(self,key):
                return {'id':key,'name':'(File responses)','mimeType':FOLDER,'trashed':False}
        with self.assertRaisesRegex(ConnectionError,'mapping'):
            collect(Responses(),ROOT,course='Forensics',week='Wk 03')
        _,files,_,_=collect(Responses(),ROOT,course='Forensics',week=None,skip_name_guard=True)
        self.assertEqual([f['id'] for f in files],['deck','doc'])


FA='sourceAAAAAAAAAAAAAA'; FB='sourceBBBBBBBBBBBBBB'; FC='sourceCCCCCCCCCCCCCC'; FX='newXXXXXXXXXXXXXXXXX'
FOLDER1='FOLDERoneFOLDERoneFO'; FOLDER2='FOLDERtwoFOLDERtwoFO'
class LessonReader:
    parents={FA:[FOLDER1],FB:[FOLDER1],FC:[FOLDER2]}
    kids={FOLDER1:[{'id':FA,'name':'A','mimeType':'application/pdf','parents':[FOLDER1],'trashed':False},
                   {'id':FB,'name':'B','mimeType':'application/pdf','parents':[FOLDER1],'trashed':False},
                   {'id':FX,'name':'X','mimeType':'application/pdf','parents':[FOLDER1],'trashed':False}],
          FOLDER2:[{'id':FC,'name':'C','mimeType':'application/pdf','parents':[FOLDER2],'trashed':False}]}
    def metadata(self,key):
        if key in self.kids:
            return {'id':key,'name':'(File responses)','mimeType':FOLDER,'trashed':False}
        if key in self.parents:
            return {'id':key,'parents':self.parents[key]}
        raise ConnectionError('submission_folder_access_required')
    def children(self,key,cursor):
        return {'files':self.kids.get(key,[])}

def records(subs):
    return {'submissions':subs,'checked_at':'2026-09-18T00:00:00Z'}

def sub(fid,key='biology:C02',status='assessed',cls='biology'):
    return {'class_key':cls,'assignment_key':key,'source_file_id':fid,'status':status}


class LessonInventoryTests(unittest.TestCase):
    def resolve(self,reader,subs):
        from school_workflow_hub.adapters.submission_inventory import resolve_lesson_folders
        return resolve_lesson_folders(reader,records(subs),'biology','biology:C02')

    def test_unions_distinct_source_parents(self):
        folders,unreadable=self.resolve(LessonReader(),[sub(FA),sub(FB,status='roster_matched'),sub(FC)])
        self.assertEqual(sorted(folders),sorted([FOLDER1,FOLDER2]))
        self.assertEqual(unreadable,0)

    def test_no_recorded_submissions_refuses(self):
        with self.assertRaisesRegex(ConnectionError,'no_recorded_submissions'):
            self.resolve(LessonReader(),[sub(FA,key='other')])

    def test_unreadable_source_is_skipped_not_fatal(self):
        missing='missingMISSINGmissing'
        folders,unreadable=self.resolve(LessonReader(),[sub(FA),sub(missing)])
        self.assertEqual(folders,[FOLDER1]); self.assertEqual(unreadable,1)

    def test_too_scattered_refuses(self):
        class Scatter(LessonReader):
            parents={('src%02d'%i+'x'*15):['par%02d'%i+'x'*15] for i in range(8)}
            def metadata(self,key):
                if key in self.parents: return {'id':key,'parents':self.parents[key]}
                raise ConnectionError('submission_folder_access_required')
        subs=[sub(fid) for fid in Scatter.parents]
        with self.assertRaisesRegex(ConnectionError,'too_scattered'):
            self.resolve(Scatter(),subs)

    def test_read_lesson_inventory_marks_assessed_vs_awaiting(self):
        from school_workflow_hub.adapters import submission_inventory as si
        with patch.object(si,'read_records',return_value=records([sub(FA),sub(FB,status='roster_matched'),sub(FC)])), \
             patch.object(si,'service_token',return_value='tok'), \
             patch.object(si,'DriveReader',return_value=LessonReader()):
            out=si.read_lesson_inventory('signed','biology','biology:C02',{})
        self.assertEqual(out['class_key'],'biology'); self.assertEqual(out['assignment_key'],'biology:C02')
        by_id={f['file_id']:f for f in out['files']}
        self.assertEqual(set(by_id),{FA,FB,FC,FX})
        self.assertEqual(by_id[FX]['stage'],'awaiting_assessment')
        self.assertEqual(by_id[FA]['stage'],'assessed')
        self.assertTrue(all(f['status']=='present' for f in out['files']))
        self.assertEqual(out['external_writes'],0)

    def test_route_accepts_assignment_and_rejects_unknown_query(self):
        from school_workflow_hub.web import application
        from school_workflow_hub.adapters import submission_inventory as si
        ok=[]
        with patch.object(si,'read_lesson_inventory',return_value={'schema_version':'hub-submission-inventory/v1','class_key':'biology','assignment_key':'biology:C02','files':[],'external_writes':0}):
            application({'PATH_INFO':'/api/submission-inventory','REQUEST_METHOD':'GET','QUERY_STRING':'class_key=biology&assignment=biology:C02'},lambda s,h:ok.append(s))
        self.assertEqual(ok,['200 OK'])
        bad=[]
        application({'PATH_INFO':'/api/submission-inventory','REQUEST_METHOD':'GET','QUERY_STRING':'foo=bar'},lambda s,h:bad.append(s))
        self.assertEqual(bad,['400 Bad Request'])
