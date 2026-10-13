"""Declared Form upload roots use fictional data and no provider calls."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from hub_v2 import google, roster, work
from test_hub_v2 import fixture, ROSTER, DIRECT, SECOND, cell

FORM_MIME="application/vnd.google-apps.form"
FIRST="FICTIONALFORMID1234567890abc"
SECOND_ID="FICTIONALFORMID0987654321xyz"
ROOT_ONE="FICTIONALUPLOADROOT1234567890one"
ROOT_TWO="FICTIONALUPLOADROOT1234567890two"
TIME="2026-10-12T12:00:00Z"

def form(fid, root):
    return {"formId":fid,"revisionId":"revision1","items":[{"questionItem":{"question":{"fileUploadQuestion":{"folderId":root}}}}]}

def meta(fid, mime, name="Original upload folder"):
    return {"id":fid,"name":name,"mimeType":mime,"trashed":False,"version":"1","modifiedTime":TIME,"parents":["UNRELATED_PARENT"]}

def descriptor(fid=FIRST):
    return {"form_id":fid,"problem":None,"_upload_form_verified":True}

class DeclaredUploadRootsTests(unittest.TestCase):
    def setUp(self):
        self.info={FIRST:form(FIRST,ROOT_ONE), SECOND_ID:form(SECOND_ID,ROOT_TWO)}
        self.metadata={FIRST:meta(FIRST,FORM_MIME),SECOND_ID:meta(SECOND_ID,FORM_MIME),
                       ROOT_ONE:meta(ROOT_ONE,google.FOLDER),ROOT_TWO:meta(ROOT_TWO,google.FOLDER)}

    def roots(self, forms=None):
        with patch.object(google,"get_json",side_effect=lambda url,t,params:deepcopy(self.info[url.rsplit("/",1)[-1]])) as get, \
             patch.object(google,"drive_file",side_effect=lambda t,fid,*args:deepcopy(self.metadata[fid])) as md:
            result=work._declared_upload_folders("token",forms or [descriptor()])
        self.assertEqual(get.call_args.args[2],{"fields":"formId,revisionId,items(questionItem(question(fileUploadQuestion(folderId))))"})
        return result

    def test_exact_declaration_finds_only_own_root_without_traversing_parent(self):
        roots=self.roots()
        self.assertEqual(set(roots[FIRST]["folders"]),{ROOT_ONE})
        self.assertNotIn("UNRELATED_PARENT",roots[FIRST]["folders"])

    def test_foreign_form_missing_revision_malformed_or_description_only_ids_are_unavailable(self):
        original=deepcopy(self.info[FIRST])
        changes=[{"formId":SECOND_ID}, {"revisionId":""}, {"items":"bad"},
                 {"items":[{"description":ROOT_ONE}]},
                 {"items":[{"questionItem":{"question":{"fileUploadQuestion":{"folderId":"short"}}}}]},
                 {"items":[{"questionItem":{"question":{"fileUploadQuestion":None}}}]}]
        for change in changes:
            with self.subTest(change=change):
                self.info[FIRST]={**deepcopy(original),**change}
                with self.assertRaises(google.GoogleError): self.roots()

    def test_folder_identity_type_trash_and_current_version_must_be_verified(self):
        changes=[{"id":ROOT_TWO},{"mimeType":"application/vnd.google-apps.shortcut"},
                 {"trashed":True},{"trashed":None},{"version":""},{"modifiedTime":""}]
        for fid in (ROOT_ONE,FIRST):
            original=deepcopy(self.metadata[fid])
            for change in changes:
                with self.subTest(fid=fid,change=change):
                    self.metadata[fid]={**deepcopy(original),**change}
                    with self.assertRaises(google.GoogleError): self.roots()
            self.metadata[fid]=[]
            with self.assertRaises(google.GoogleError): self.roots()
            self.metadata[fid]=original

    def test_unavailable_form_or_folder_does_not_create_roots(self):
        for status in (403,404,500):
            with self.subTest(status=status), patch.object(google,"drive_file",side_effect=google.GoogleError("unavailable",status)):
                with self.assertRaises(google.GoogleError): work._declared_upload_folders("token",[descriptor()])
        for desc in ({"form_id":FIRST,"problem":"unreadable","_upload_form_verified":True},
                     {"form_id":FIRST,"problem":None}):
            with patch.object(google,"get_json") as get:
                with self.assertRaises(google.GoogleError): work._declared_upload_folders("token",[desc])
                get.assert_not_called()

    def test_two_forms_preserve_ownership_and_shared_roots_remain_ambiguous(self):
        roots=self.roots([descriptor(),descriptor(SECOND_ID)])
        self.assertEqual(set(roots[FIRST]["folders"]),{ROOT_ONE})
        self.assertEqual(set(roots[SECOND_ID]["folders"]),{ROOT_TWO})
        self.info[SECOND_ID]=form(SECOND_ID,ROOT_ONE)
        with self.assertRaisesRegex(google.GoogleError,"same upload folder"):
            self.roots([descriptor(),descriptor(SECOND_ID)])

    def integrated(self, *, drift=None, children=None, existing=False):
        students,_=roster.parse_tab(ROSTER,"forensics","A2 Forensics")
        config={"HUB_SUBMISSIONS_ROOT_FOLDER":"CLASSROOT","HUB_READER_SERVICE_ACCOUNT":"reader@fictional.example"}
        count={}
        self.metadata[FIRST]["name"]="Fictional Wk 04 Form"
        self.metadata[SECOND_ID]["name"]="Second Form"
        def get(url,token,params):
            fid=url.rsplit("/",1)[-1]
            count[fid]=count.get(fid,0)+1
            body=deepcopy(self.info[fid])
            if drift=="form" and count[fid]>1: body["revisionId"]="changed"
            return body
        def drive(token,fid,*args):
            value=deepcopy(self.metadata.get(fid,meta(fid,"application/vnd.google-apps.presentation")))
            if drift=="folder" and fid in (ROOT_ONE,ROOT_TWO) and count.get(FIRST,0)>1: value["version"]="changed"
            if drift=="form_metadata" and fid==SECOND_ID and count.get(FIRST,0)>1: value["version"]="changed"
            return value
        def child(token,fid):
            if fid=="CLASSROOT": return [meta("CLASS",google.FOLDER,"Forensics")]
            if fid=="CLASS": return [meta("EXISTING",google.FOLDER,"📥 Wk 04 — Fictional topic")] if existing else []
            return deepcopy((children or {}).get(fid,[]))
        with patch.object(google,"get_json",side_effect=get) as get_mock, \
             patch.object(google,"form_responses",return_value=[]), \
             patch.object(google,"drive_file",side_effect=drive), \
             patch.object(google,"folder_children",side_effect=child) as listing, \
             patch.object(google,"folders_named",return_value=[]):
            result=work.lesson_work("token",config,fixture(),"forensics","Wk 04",
                                    review_only=True,roster_snapshot=({"forensics":students},[]))
        return result,get_mock,listing

    def test_empty_declared_folders_outside_class_tree_are_waiting_without_false_missing_folder(self):
        result,get,listing=self.integrated()
        self.assertIsNone(result["folder_note"])
        self.assertEqual({f["url"].rsplit("/",1)[-1] for f in result["folders"]},{ROOT_ONE,ROOT_TWO})
        self.assertEqual(result["folder_files"],0)
        self.assertTrue(all(not f["students"] and f["responses"]==0 for f in result["lessons"][0]["forms"]))
        self.assertEqual(get.call_count,4) # two exact Forms before and after listing
        self.assertNotIn("UNRELATED_PARENT",[c.args[1] for c in listing.call_args_list])

    def test_folder_files_keep_exact_declared_form_ownership_without_invented_form_events(self):
        upload=meta("FICTIONALORIGINAL1234567890","application/vnd.google-apps.presentation","s.ana.rivera — 10-12 08.01 — Work")
        result,_,_=self.integrated(children={ROOT_TWO:[upload]})
        first,second=result["lessons"][0]["forms"]
        self.assertEqual(first["students"],[])
        self.assertEqual(len(second["students"]),1)
        self.assertEqual(second["students"][0]["source"],"folder")
        self.assertFalse(second["students"][0].get("source_form_id"))

    def test_declaration_or_folder_drift_discards_unverified_folder_contents(self):
        for drift in ("form","form_metadata","folder"):
            with self.subTest(drift=drift):
                result,_,_=self.integrated(drift=drift)
                self.assertIn("changed",result["folder_note"])
                self.assertEqual(result["folders"],[])
                self.assertEqual(result["folder_files"],0)
                self.assertTrue(all(not f["students"] for f in result["lessons"][0]["forms"]))

    def test_misleading_root_and_descendant_titles_never_override_declared_Form_ownership(self):
        upload=meta("FICTIONALORIGINAL1234567890","application/vnd.google-apps.presentation","s.ana.rivera — 10-12 08.01 — Work")
        misleading="Fictional Wk 04 Form (File responses)"
        self.metadata[ROOT_TWO]["name"]=misleading
        for children in ({ROOT_TWO:[upload]},
                         {ROOT_TWO:[meta("NESTED",google.FOLDER,misleading)],"NESTED":[upload]}):
            with self.subTest(children=children):
                result,_,_=self.integrated(children=children)
                first,second=result["lessons"][0]["forms"]
                self.assertEqual(first["students"],[])
                self.assertEqual(len(second["students"]),1)
                self.assertEqual(second["students"][0]["files"][0]["id"],upload["id"])
                self.assertEqual(result["other_files"],[])

    def test_existing_discovered_roots_make_no_additional_Form_structure_reads(self):
        result,get,_=self.integrated(existing=True)
        self.assertIsNone(result["folder_note"])
        get.assert_not_called()

    def test_overlapping_declared_trees_remain_unavailable_without_heuristic_assignment(self):
        self.metadata[ROOT_TWO]["parents"]=[ROOT_ONE]
        upload=meta("FICTIONALORIGINAL1234567890","application/vnd.google-apps.presentation","s.ana.rivera — 10-12 08.01 — Work")
        result,_,_=self.integrated(children={ROOT_ONE:[self.metadata[ROOT_TWO]],ROOT_TWO:[upload]})
        self.assertIn("overlap",result["folder_note"])
        self.assertEqual(result["folders"],[])
        self.assertEqual(result["folder_files"],0)
        self.assertTrue(all(not f["students"] for f in result["lessons"][0]["forms"]))

    def test_deep_declared_tree_keeps_exact_Form_owner_beyond_legacy_ancestor_cap(self):
        upload=meta("FICTIONALORIGINAL1234567890","application/vnd.google-apps.presentation","s.ana.rivera — 10-12 08.01 — Work")
        children={ROOT_TWO:[meta("DEPTH00",google.FOLDER,"Fictional Wk 04 Form (File responses)")]}
        for i in range(24):
            children["DEPTH%02d"%i]=[meta("DEPTH%02d"%(i+1),google.FOLDER,"Nested")]
        children["DEPTH24"]=[upload]
        result,_,_=self.integrated(children=children)
        first,second=result["lessons"][0]["forms"]
        self.assertIsNone(result["folder_note"])
        self.assertEqual(first["students"],[])
        self.assertEqual(second["students"][0]["files"][0]["id"],upload["id"])
        self.assertEqual(result["other_files"],[])

    def test_declared_tree_budget_exhaustion_is_unavailable_not_empty(self):
        def children(token,fid):
            if fid=="ROOT": return [meta("CHILD%03d"%i,google.FOLDER) for i in range(work.MAX_FOLDERS)]
            return []
        with patch.object(google,"folder_children",side_effect=children):
            with self.assertRaisesRegex(google.GoogleError,"complete-read limit"):
                work.list_tree("token",{"ROOT":"Root"},require_complete=True)

if __name__=="__main__": unittest.main()
