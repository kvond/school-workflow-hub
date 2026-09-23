import test from 'node:test';
import assert from 'node:assert/strict';
import {validateRecords,sectionOptions,verifiedSectionOptions,filterRecords,filterReviewRecords,lessonOptions,createRecordsLoader,renderRecords,renderSummaries,score,appendSheet,recordsWithDiscovery,reportSections,aspectHistory,aspectKey,understandingItems,answerPairs} from '../grading-records.mjs';

function fixture(){return {schema_version:'hub-grading-records/v1',provenance:'source_read',external_writes:0,source_revision:'fictional-read',checked_at:'2026-09-01T12:00:00Z',assignments:[
 {assignment_key:'F1',class_key:'forensics',assignment_title:'Fictional One'},
 {assignment_key:'F2',class_key:'forensics',assignment_title:'Fictional Two'},
 {assignment_key:'B1',class_key:'biology',assignment_title:'Fictional Three'}],submissions:[
 {record_id:'one',class_key:'forensics',section_id:'a',section_name:'Period A',student_id:'one',display_name:'Alex F.',assignment_key:'F1',assignment_title:'Fictional One',assessment_stage:'final',status:'proposed',content_score:6.25,revision_score:null,answers:[],included_in_counts:true},
 {record_id:'two',class_key:'forensics',section_id:'b',section_name:'Period B',student_id:'two',display_name:'Blair F.',assignment_key:'F2',assignment_title:'Fictional Two',assessment_stage:'initial',status:'identity_unverified',content_score:8,revision_score:null,answers:[],included_in_counts:false},
 {record_id:'three',class_key:'biology',section_id:'c',section_name:'Period C',student_id:'three',display_name:'Casey F.',assignment_key:'B1',assignment_title:'Fictional Three',assessment_stage:'final',status:'superseded',answers:[],included_in_counts:false}],summaries:[
 {class_key:'forensics',section_id:'a',section_name:'Period A',summary:'Connect the claim to the evidence.',coverage:'Partial supplied work.',submitted_students:1,checked_at:'2026-09-01T12:00:00Z'},
 {class_key:'forensics',section_id:'b',section_name:'Period B',summary:'Verify the student match before drawing a class conclusion.',coverage:'Identity review remains.',submitted_students:0,checked_at:'2026-09-01T12:00:00Z'}]};}

class Element{
 constructor(doc,tag,text=''){this.ownerDocument=doc;this.tagName=tag;this.children=[];this.textContent=text;this.attributes={};}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(key,value){this.attributes[key]=value;}
 addEventListener(){}
 get allText(){return this.textContent+this.children.map(child=>child.allText).join(' ');}
}
const document={createElement(tag){return new Element(document,tag);}};
const root=()=>new Element(document,'div');

test('filters course then exact section without conflating periods',()=>{
 const data=fixture();assert.equal(validateRecords(data),data);
 assert.deepEqual(sectionOptions(data,'forensics'),[['a','Period A'],['b','Period B']]);
 assert.deepEqual(filterRecords(data,'forensics','a').map(r=>r.record_id),['one']);
 assert.equal(filterRecords(data,'forensics').length,2);
 assert.equal(filterRecords(data,'anatomy').length,0);
});
test('lesson names come from the assignment registry and filter the table by lesson',()=>{
 const data=fixture();
 assert.deepEqual(lessonOptions(data,'forensics'),[['F1','Fictional One'],['F2','Fictional Two']]);
 assert.deepEqual(lessonOptions(data,'biology'),[['B1','Fictional Three']]);
 assert.deepEqual(filterRecords(data,'forensics','all','F1').map(r=>r.record_id),['one']);
 assert.deepEqual(filterRecords(data,'forensics','all','F2').map(r=>r.record_id),['two']);
 assert.equal(filterRecords(data,'forensics','all','B1').length,0);
});
test('Notes/Drafts checkpoint partitions rows by assessment stage',()=>{
 const data=fixture();
 assert.deepEqual(filterRecords(data,'forensics','all','all','final').map(r=>r.record_id),['one']);
 assert.deepEqual(filterRecords(data,'forensics','all','all','initial').map(r=>r.record_id),['two']);
 assert.equal(filterRecords(data,'forensics','all','all','all').length,2);
});
test('a print sheet carries the student report and no teacher-only evidence',()=>{
 const printDoc={createElement(tag){return new Element(printDoc,tag);}};printDoc.body=new Element(printDoc,'body');
 const row={display_name:'Alex F.',assignment_title:'Fictional One',section_name:'Period A',checked_at:'2026-09-01T12:00:00Z',content_score:6.25,status:'proposed',revision_score:null,feedback:'Do this next.',evidence_note:'TEACHER ONLY — never print'};
 const sheet=appendSheet(printDoc,row);
 assert.equal(printDoc.body.children.length,1);
 assert.match(sheet.allText,/Alex F\./);assert.match(sheet.allText,/Do this next\./);assert.match(sheet.allText,/What I understand now that I didn't before/);
  assert.match(sheet.allText,/hand this page back to me/);assert.doesNotMatch(sheet.allText,/6\.25|Content \/ notes/);
 assert.doesNotMatch(sheet.allText,/TEACHER ONLY/);
});
test('unmatched work has a counted option and stays outside default class view',()=>{
 const data=fixture();data.submissions.push({record_id:'unmatched',class_key:'biology',section_id:'',section_name:'',student_id:'',display_name:'Unmatched F.',assignment_title:'Fictional unmatched',status:'identity_unverified',answers:[],included_in_counts:false});
 assert.deepEqual(sectionOptions(data,'biology'),[['c','Period C'],['','Unmatched (1)']]);
 assert.deepEqual(filterRecords(data,'biology').map(r=>r.record_id),['three']);
 assert.deepEqual(filterRecords(data,'biology','').map(r=>r.record_id),['unmatched']);
 assert.equal(filterRecords(data,'biology','all').length,2);
 const target=root();renderRecords(target,{status:'loaded',data},{classKey:'biology'});
 assert.match(target.allText,/1 record needs a section match/);assert.doesNotMatch(target.allText,/F\., Unmatched/);
 renderRecords(target,{status:'loaded',data},{classKey:'biology',sectionId:''});assert.match(target.allText,/F\., Unmatched/);assert.match(target.allText,/Excluded from class counts/);
});
test('duplicate summary scope and unsupported provenance fail',()=>{
 const data=fixture();data.summaries.push({...data.summaries[0]});assert.throws(()=>validateRecords(data));
 const synthetic=fixture();synthetic.provenance='synthetic';assert.throws(()=>validateRecords(synthetic));
});
test('exactly one concise card per class with no grade composite',()=>{
 const target=root();renderSummaries(target,{status:'loaded',data:fixture()});
 assert.equal(target.children.length,2);assert.ok(target.children.every(n=>n.tagName==='section'));
 assert.equal(target.children[0].children.filter(n=>n.tagName==='h2').length,1);
 assert.match(target.allText,/Connect the claim/);assert.doesNotMatch(target.allText,/6.25|average|proficiency spread/i);
});
test('held or superseded scores never display zero; actual zero remains valid',()=>{
 assert.equal(score({status:'proposed',content_score:0},'content_score'),'0 / 10');
 assert.equal(score({status:'superseded',content_score:8},'content_score'),'—');
 assert.equal(score({status:'proposed',content_score:8,hold_reason:'Review identity'},'content_score'),'—');
 assert.equal(score({status:'assessed',content_score:60/13,content_score_exact:'60/13'},'content_score'),'4.62 / 10');
});
test('private answer text renders literally and identity-held records are visible outside counts',()=>{
 const data=fixture();data.submissions[1].answers=[{question_id:'q1',stage:'NOTES',response:'<script>fictional</script>',conceptual_level:'Developing',explanation_level:'Names idea'}];
 const target=root();renderRecords(target,{status:'loaded',data},{classKey:'forensics'});
 assert.match(target.allText,/1 students with matched submissions/);
 assert.match(target.allText,/F\., Blair/);assert.match(target.allText,/<script>fictional<\/script>/);
 assert.match(target.allText,/Excluded from class counts/);assert.doesNotMatch(target.allText,/8 \/ 10/);
});
test('failed refresh discards previous private data and does not report missing work',async()=>{
 const states=[];const load=createRecordsLoader({fetcher:async()=>({ok:false,status:503}),onState:s=>states.push(s)});
 await load();assert.deepEqual(states.map(s=>s.status),['loading','error']);assert.ok(states.every(s=>!s.data));
});
test('late request cannot overwrite more recent refresh',async()=>{
 const waiting=[],states=[];
 const load=createRecordsLoader({fetcher:(_url,options)=>new Promise(resolve=>waiting.push({resolve,options})),onState:s=>states.push(s)});
 const first=load(),second=load();assert.equal(waiting[0].options.signal.aborted,true);
 const latest=fixture();latest.source_revision='latest';waiting[1].resolve({ok:true,json:async()=>latest});await second;
 waiting[0].resolve({ok:true,json:async()=>fixture()});await first;
 assert.equal(states.filter(s=>s.status==='loaded').length,1);assert.equal(states.at(-1).data.source_revision,'latest');
 assert.equal(waiting[1].options.cache,'no-store');assert.equal(waiting[1].options.credentials,'same-origin');
});

function discovered(overrides={}){return {file_id:'fictional-file-a',source_url:'https://docs.google.com/presentation/d/fictional-file-a/edit',name:'Fictional deck',class_key:'forensics',assignment_key:'F1',assignment_title:'Fictional One',section_id:'a',section_name:'Period A',student_id:'one',display_name:'Alex F.',discovery_status:'changed_since_assessment',review_reasons:[],modified_at:'2026-09-03T12:00:00Z',version:'2',...overrides};}
function discovery(files){return {status:'loaded',data:{class_key:'forensics',assignment_key:'all',discovered_at:'2026-09-04T12:00:00Z',coverage:{complete:true},files}};}
test('discovery overlays exact source identity while preserving saved grades and feedback',()=>{
 const data=fixture();data.submissions[0].source_url='https://docs.google.com/presentation/d/fictional-file-a/edit';data.submissions[0].feedback='Saved report';
 const original=structuredClone(data),state=discovery([discovered()]);
 const shown=recordsWithDiscovery(data,state);assert.deepEqual(data,original);assert.equal(shown.submissions.length,3);assert.equal(shown.submissions[0].content_score,6.25);assert.equal(shown.submissions[0].feedback,'Saved report');assert.equal(shown.submissions[0].discovery.discovery_status,'changed_since_assessment');
 assert.equal(filterRecords(shown,'forensics','a','F1','initial').length,0,'discovery cannot relabel a saved final assessment as Notes');
 const target=root();renderRecords(target,{status:'loaded',data},{classKey:'forensics',sectionId:'all',discovery:state});
 assert.match(target.allText,/Work changed since assessment/);assert.match(target.allText,/Scores shown are the saved assessment/);assert.match(target.allText,/workbook read/);assert.match(target.allText,/Submissions discovered/);assert.match(target.allText,/6.25 \/ 10/);
});
test('new discovery stays ungraded and keeps exact lesson and section boundaries',()=>{
 const data=fixture(),original=structuredClone(data);const state=discovery([
  discovered({file_id:'fictional-new',student_id:'new',display_name:'Casey F.',discovery_status:'new_ungraded'}),
  discovered({file_id:'fictional-other',assignment_key:'F2',assignment_title:'Fictional Two',section_id:'b',section_name:'Period B',student_id:'other',display_name:'Devon F.',discovery_status:'new_ungraded'}),
  discovered({file_id:'fictional-unknown',assignment_key:'',assignment_title:'',section_id:'',section_name:'',student_id:'',display_name:'',discovery_status:'needs_review',review_reasons:['identity_unverified']})]);
 const shown=recordsWithDiscovery(data,state);assert.deepEqual(data,original);
 assert.deepEqual(filterRecords(shown,'forensics','a','F1','initial').map(r=>r.record_id),['discovery:fictional-new']);
 assert.equal(filterRecords(shown,'forensics','','all').length,1);
 const target=root();renderRecords(target,{status:'loaded',data},{classKey:'forensics',sectionId:'a',assignmentKey:'F1',discovery:state});
 assert.match(target.allText,/New work · not graded/);assert.match(target.allText,/Discovered work checkpoint needs review/);assert.doesNotMatch(target.allText,/F\., Devon/);assert.match(target.allText,/1 file needs a lesson match/);
 assert.equal(score(shown.submissions.find(r=>r.record_id==='discovery:fictional-new'),'content_score'),'—');
});
test('same file with conflicting section stays a review preview instead of changing historical identity',()=>{
 const data=fixture();data.submissions[0].source_file_id='fictional-file-a';
 const shown=recordsWithDiscovery(data,discovery([discovered({section_id:'b',section_name:'Period B',discovery_status:'needs_review'})]));
 assert.equal(shown.submissions[0].section_id,'a');assert.equal(shown.submissions[0].discovery,undefined);assert.equal(shown.submissions.at(-1).section_id,'b');assert.equal(shown.submissions.at(-1).content_score,null);
});
test('failed discovery keeps saved work visible without presenting previous discovery as fresh',()=>{
 const target=root();renderRecords(target,{status:'loaded',data:fixture()},{classKey:'forensics',discovery:{status:'error',message:'Source unavailable.'}});
 assert.match(target.allText,/Discovery failed/);assert.match(target.allText,/Saved assessments are unchanged/);assert.match(target.allText,/6.25 \/ 10/);assert.doesNotMatch(target.allText,/Submissions discovered/);
});
test('unknown classes and superseded previews stay visibly held without acquiring a class or score',()=>{
 const data=fixture();const state=discovery([discovered({file_id:'fictional-held',class_key:'',assignment_key:'',assignment_title:'',section_id:'',section_name:'',student_id:'',display_name:'',discovery_status:'needs_review',source_changed_since_assessment:true,review_reasons:['prior_submission_superseded','competing_submission_versions']})]);
 state.data.coverage={complete:false,form_complete:false,errors:['source_unavailable']};
 const shown=recordsWithDiscovery(data,state),held=shown.submissions.at(-1);assert.equal(held.class_key,'');assert.equal(held.assignment_key,'');assert.equal(held.content_score,null);assert.equal(filterRecords(shown,'biology','all').some(r=>r.record_id===held.record_id),false);assert.equal(filterRecords(shown,'forensics','','F1').length,0);
 const target=root();renderRecords(target,{status:'loaded',data},{classKey:'forensics',sectionId:'',discovery:state});
 assert.match(target.allText,/Class needs matching/);assert.match(target.allText,/Previously superseded work/);assert.match(target.allText,/no current version has been selected/);assert.match(target.allText,/does not establish revised student writing/);assert.match(target.allText,/Discovery coverage is incomplete/);assert.match(target.allText,/Form responses could not be read completely/);assert.doesNotMatch(target.allText,/New work · not graded/);
 assert.match(target.allText,/Identity needs matching/);assert.doesNotMatch(target.allText,/needs matching, Identity/);
});
test('per-assignment Form coverage preserves successful reads beside an unavailable Form',()=>{
 const data=fixture(),state=discovery([]);state.data.coverage={complete:false,form_complete:false,form_results:[
  {assignment_key:'F1',form_id:'fictional-internal-form-one',form_url:'https://docs.google.com/forms/d/fictional-one/viewform',form_source_reference:'fictional-private-reference',complete:true,error:null,pages:2,responses_checked:7,files_referenced:6},
  {assignment_key:'F2',form_id:'fictional-internal-form-two',form_url:'javascript:alert(1)',complete:false,error:'submission_form_access_required',pages:0,responses_checked:0,files_referenced:0}
 ]};
 const target=root();renderRecords(target,{status:'loaded',data},{classKey:'forensics',sectionId:'all',discovery:state});
 const details=target.children.find(n=>n.tagName==='details'),items=details.children.find(n=>n.tagName==='ul').children;
 assert.equal(items.length,2);assert.match(items[0].allText,/Fictional One/);assert.match(items[0].allText,/Form read complete · 7 responses checked · 6 files referenced · 2 response pages/);
 assert.match(items[1].allText,/Fictional Two/);assert.match(items[1].allText,/Form read incomplete · submission form access required/);assert.doesNotMatch(items[1].allText,/Form read complete|0 responses checked/);
 assert.equal(items[0].children.find(n=>n.tagName==='a').href,'https://docs.google.com/forms/d/fictional-one/viewform');assert.equal(items[1].children.some(n=>n.tagName==='a'),false);
 assert.doesNotMatch(details.allText,/fictional-internal-form|fictional-private-reference/);assert.match(details.allText,/Incomplete coverage does not establish missing work/);assert.match(target.allText,/6.25 \/ 10/);
});

test('verified section choices use current section metadata, including empty classes',()=>{
 const data=fixture();assert.deepEqual(verifiedSectionOptions(data,'forensics'),[],'saved grading rows are not current roster configuration');
 data.sections=[{class_key:'anatomy',section_id:'anatomy-b3',section_name:'B3 Anatomy'},
  {class_key:'forensics',section_id:'b',section_name:'A3 Forensics'},
  {class_key:'forensics',section_id:'a',section_name:'A2 Forensics'}];
 assert.deepEqual(verifiedSectionOptions(data,'forensics'),[['a','A2 Forensics'],['b','A3 Forensics']]);
 assert.deepEqual(verifiedSectionOptions(data,'anatomy'),[['anatomy-b3','B3 Anatomy']]);
});
test('review rows include unresolved candidates without mixing known sections or changing exports',()=>{
 const data=fixture();const state=discovery([
  discovered({file_id:'unresolved-identity',section_id:'',section_name:'',student_id:'',display_name:'',discovery_status:'needs_review'}),
  discovered({file_id:'unresolved-lesson',assignment_key:'',assignment_title:'',section_id:'',section_name:'',student_id:'',display_name:'',discovery_status:'needs_review'}),
  discovered({file_id:'known-other-section',section_id:'b',section_name:'A3 Forensics'})]);
 const shown=recordsWithDiscovery(data,state);
 const ids=filterReviewRecords(shown,'forensics','a','F1').map(row=>row.record_id);
 assert.ok(ids.includes('discovery:unresolved-identity'));assert.ok(ids.includes('discovery:unresolved-lesson'));
 assert.ok(!ids.includes('discovery:known-other-section'));assert.ok(!ids.includes('two'));
 assert.deepEqual(filterRecords(data,'forensics','a','F1').map(row=>row.record_id),['one']);
});
test('Agenda-only lessons appear by name with no saved upload or invented registry binding',()=>{
 const data=fixture(),original=structuredClone(data),state=discovery([]);
 state.data.assignments=[{assignment_key:'discovery:forensics:fictional',class_key:'forensics',assignment_title:'New fictional lesson',discovery_only:true,registry_assignment_key:''}];
 const shown=recordsWithDiscovery(data,state);
 assert.ok(lessonOptions(shown,'forensics').some(([key,title])=>key==='discovery:forensics:fictional'&&title==='New fictional lesson'));
 assert.equal(shown.assignments.at(-1).discovery_only,true);assert.equal(shown.assignments.at(-1).registry_assignment_key,'');assert.deepEqual(data,original);
});
test('source discovery fixes appear in existing coverage details without a new panel',()=>{
 const state=discovery([]);state.data.coverage={complete:false,form_results:[{assignment_key:'F1',complete:false,error:'assignment_form_turn_in_links_disagree',next_action:'Verify the visible Turn it in link at Synthetic course!T6 and raw link at U6; both must identify the same assignment Form.'}],source_results:[{assignment_key:'F1',complete:false,error:'source_unavailable',next_action:'Verify the assignment upload folder.',source_url:'https://example.invalid/synthetic-folder'}],catalog_results:[{assignment_key:'F2',complete:false,error:'master_unavailable',next_action:'Restore reader access to the lesson master.'}]};
 const target=root();renderRecords(target,{status:'loaded',data:fixture()},{classKey:'forensics',sectionId:'a',discovery:state});
 const details=target.children.filter(node=>node.tagName==='details');assert.equal(details.length,1);
 assert.match(details[0].allText,/Fictional One: Verify the assignment upload folder/);
 assert.match(details[0].allText,/Fictional Two: Restore reader access/);
 assert.match(details[0].allText,/Synthetic course!T6 and raw link at U6/);
});
test('Form response completeness is distinct from published and accepting status',()=>{
 const state=discovery([]);state.data.coverage={complete:true,form_results:[
  {assignment_key:'F1',complete:true,responses_checked:1,files_referenced:1,pages:1,is_published:true,is_accepting_responses:false},
  {assignment_key:'F2',complete:true,responses_checked:0,files_referenced:0,pages:1,is_published:null,is_accepting_responses:null}]};
 const target=root();renderRecords(target,{status:'loaded',data:fixture()},{classKey:'forensics',sectionId:'a',discovery:state});
 const text=target.children.find(node=>node.tagName==='details').allText;
 assert.match(text,/Form read complete · 1 responses checked/);
 assert.match(text,/Published · responses closed/);
 assert.match(text,/Publication unknown · response acceptance unknown/);
 assert.match(text,/Historical responses remain separate/);
 assert.doesNotMatch(text,/accepting responses\./);
});

// Fictional four-level report fixtures (September 23, 2026 layout).
function aspectRow(extra={}){return {record_id:'r2',class_key:'biology',section_id:'c',student_id:'s1',display_name:'Casey F.',assignment_title:'Fictional ecosystems',section_name:'Period C',checked_at:'2026-10-02T12:00:00Z',status:'proposed',content_score:9,
 feedback:'**Current understanding:**\nMy answers explain how energy enters the web.\n## How my thinking changed\nI replaced "used up" with heat.\nOne question to think about\nHow does a vent bacterium act like a door?\nThink this through for yourself; this is not for submission or another grade.\nEvidence for my conceptual understanding\n"It leaves as heat." — revised response, slide 13\nWhat I understand now that I didn\'t before\n____',
 answers:[{question_id:'Q1',stage:'NOTES',response:'Plants make food.',conceptual_level:'Beginning',critical_aspect:'Producers as the entry point',slide_number:'7',evidence_note:'TEACHER ONLY'},
  {question_id:'Q1',stage:'DRAFT',response:'Plants and vent bacteria bring outside energy in.',conceptual_level:'Proficient',critical_aspect:'Producers as the entry point',slide_number:'7'},
  {question_id:'Q2',stage:'DRAFT',response:'It leaves as heat.',conceptual_level:'Extending',critical_aspect:'Critical Aspect 2 · Where transferred energy goes',slide_number:'13'},
  {question_id:'B1',stage:'BANK',response:'producer',conceptual_level:'Proficient',critical_aspect:''}],...extra};}
test('report text splits at the teacher headings, tolerating markdown and colons',()=>{
 const parsed=reportSections(aspectRow().feedback);
 assert.equal(parsed.found,true);
 assert.equal(parsed.sections['Current understanding'],'My answers explain how energy enters the web.');
 assert.match(parsed.sections['One question to think about'],/vent bacterium/);
 assert.equal(Object.keys(parsed.sections).length,4);
});
test('aspect names join only on the same name after the deck prefix',()=>{
 assert.equal(aspectKey('Your answer — Critical Aspect 2 · Where transferred energy goes'),'where transferred energy goes');
 assert.equal(aspectKey('Critical Aspect 2 · Where transferred energy goes'),aspectKey('where transferred energy goes.'));
 assert.notEqual(aspectKey('Energy transfer'),aspectKey('Where transferred energy goes'));
});
test('current understanding keeps one revised item per aspect and skips Concept Bank terms',()=>{
 const items=understandingItems(aspectRow());
 assert.deepEqual(items.map(i=>[i.label,i.level]),[['Producers as the entry point','Proficient'],['Critical Aspect 2 · Where transferred energy goes','Extending']]);
 assert.deepEqual(answerPairs(aspectRow()).map(p=>[p.first,p.revised,p.slide]),[['Plants make food.','Plants and vent bacteria bring outside energy in.','7']]);
});
test('year graphs use this learner only, four-level ratings only, and never superseded work',()=>{
 const earlier={record_id:'r1',class_key:'biology',section_id:'c',student_id:'s1',display_name:'Casey F.',checked_at:'2026-09-10T12:00:00Z',status:'proposed',answers:[
  {question_id:'Q9',stage:'DRAFT',conceptual_level:'Developing',critical_aspect:'producers as the entry point'},
  {question_id:'Q8',stage:'DRAFT',conceptual_level:'Almost there',critical_aspect:'Producers as the entry point'}]};
 const other={...earlier,record_id:'x',student_id:'s2'};
 const replaced={...earlier,record_id:'old',status:'superseded',answers:[{question_id:'Q9',stage:'DRAFT',conceptual_level:'Beginning',critical_aspect:'Producers as the entry point'}]};
 const lines=aspectHistory(aspectRow(),[earlier,other,replaced]);
 const producers=lines.find(l=>l.key==='producers as the entry point');
 assert.deepEqual(producers.points.map(p=>[p.date,p.level,p.revised]),[['2026-09-10','Developing',true],['2026-10-02','Beginning',false],['2026-10-02','Proficient',true]]);
 assert.equal(lines.length,2);
});
test('the printed report follows the sample layout with no scores or teacher notes',()=>{
 const printDoc={createElement(tag){return new Element(printDoc,tag);}};printDoc.body=new Element(printDoc,'body');
 const text=appendSheet(printDoc,aspectRow(),[]).allText;
 for(const expected of ['Your understanding through the year','Fictional ecosystems','Current understanding','Producers as the entry point','Proficient','Extending','Your earlier and current explanations','Your first answer','Your revised answer','slide 7','How your thinking changed','One question to think about','reworking concept notes is for your understanding','Evidence of your understanding',"What I understand now that I didn't before",'hand this page back to me','Rubric key','Uses the mechanism to explain a new case and its limits.'])
  assert.ok(text.includes(expected),expected);
 assert.doesNotMatch(text,/TEACHER ONLY|Content \/ notes|____/);
 assert.equal((text.match(/not for submission/g)||[]).length,1);
});
test('an older three-level report still prints its full text and no rubric key',()=>{
 const printDoc={createElement(tag){return new Element(printDoc,tag);}};printDoc.body=new Element(printDoc,'body');
 const row={display_name:'Alex F.',assignment_title:'Fictional One',checked_at:'2026-09-01T12:00:00Z',feedback:'You explained osmosis well.',answers:[{question_id:'Q1',stage:'DRAFT',response:'water moves',conceptual_level:'Almost there'}]};
 const text=appendSheet(printDoc,row).allText;
 assert.match(text,/You explained osmosis well\./);assert.match(text,/Almost there/);assert.doesNotMatch(text,/Rubric key/);
});
test('second-person headings parse, and older first-person headings still map to them',()=>{
 const text='Current understanding\nYour answers explain how energy enters the web.\nHow your thinking changed\nYour first answer said, "Plants make food."\nEvidence of your understanding\n"It leaves as heat." — revised response, slide 13';
 const parsed=reportSections(text);
 assert.deepEqual(Object.keys(parsed.sections),['Current understanding','How your thinking changed','Evidence of your understanding']);
 assert.match(reportSections(aspectRow().feedback).sections['How your thinking changed'],/used up/);
});
test('printed headings and fixed lines never speak as the student except the student-written reflection',()=>{
 const printDoc={createElement(tag){return new Element(printDoc,tag);}};printDoc.body=new Element(printDoc,'body');
 const row=aspectRow({feedback:'Current understanding\nYour answers explain it.',answers:[]});
 // The hand-back line is the teacher speaking ("the part I read"), which is intended.
 const text=appendSheet(printDoc,row).allText.replace("What I understand now that I didn't before",'').replace('Write this yourself, then hand this page back to me. This is the part I read.','');
 assert.doesNotMatch(text,/\bMy\b|\bI\b/);
});
