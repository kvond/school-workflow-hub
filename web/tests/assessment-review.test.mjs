// Synthetic transport only: no model, Drive or school writes.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mountAssessmentReview} from '../assessment-review.mjs';

class Element extends EventTarget{
 constructor(doc,tag){super();this.ownerDocument=doc;this.tagName=tag;this.children=[];this.attributes={};this.textContent='';this.value='';}
 append(...children){for(const child of children)if(child&&typeof child==='object')child.parent=this;this.children.push(...children);}
 replaceChildren(...children){this.children=[...children];}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);}
 setAttribute(key,value){this.attributes[key]=value;}
 get all(){return [this,...this.children.flatMap(child=>typeof child==='string'?[]:child.all)];}
 get allText(){return this.textContent+' '+this.children.map(child=>typeof child==='string'?child:child.allText).join(' ');}
}
const document={createElement(tag){return new Element(document,tag);}};
const records={status:'loaded',data:{assignments:[{class_key:'forensics',assignment_key:'F1',assignment_title:'Fictional observation'}],
 sections:[{class_key:'forensics',section_id:'a2',section_name:'A2 Forensics'},{class_key:'forensics',section_id:'a3',section_name:'A3 Forensics'}]}};
const scope={classKey:'forensics',assignmentKey:'F1',sectionId:'a2',sectionName:'A2 Forensics',ready:true};
const files=[{file_id:'fictional-a2',name:'Alex Fictional',status:'present',stage:'awaiting_assessment',class_key:'forensics',assignment_key:'F1',section_id:'a2'},
 {file_id:'fictional-a3',name:'Blair Fictional',status:'present',stage:'awaiting_assessment',class_key:'forensics',assignment_key:'F1',section_id:'a3'},
 {file_id:'fictional-held',name:'Held work',status:'present',stage:'detected',class_key:'forensics',assignment_key:'F1',section_id:''}];
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
function setup(draftResponse,postResponse){
 const root=new Element(document,'div'),calls=[];
 const fetcher=async(url,options)=>{
  calls.push({url,options});
  if(options?.method==='POST')return {ok:true,json:async()=>typeof postResponse==='function'?postResponse():postResponse||{status:'already_assessed',checked_at:'2026-09-23T12:00:00Z'}};
  if(url.includes('draft_for='))return {ok:true,json:async()=>draftResponse||{status:'none'}};
  return {ok:true,json:async()=>({configured:true,model:'synthetic-no-model'})};
 };
 return {root,calls,view:mountAssessmentReview(root,{fetcher,records:()=>records})};
}
const button=(root,label)=>root.all.find(el=>el.tagName==='button'&&el.textContent===label);

test('named lesson and exact section govern generation; a changed date cannot retarget it',async()=>{
 const {root,calls,view}=setup();view.update({...scope,date:'2026-09-16'});view.setFiles(files,null);await settle();
 const recoveries=calls.filter(call=>call.url.includes('draft_for='));assert.ok(recoveries.length);
 for(const call of recoveries)assert.deepEqual(Object.fromEntries(new URL(call.url,'https://fictional.invalid').searchParams),{draft_for:'fictional-a2',class_key:'forensics',assignment_key:'F1',section_id:'a2',stage:'initial'});
 assert.match(root.allText,/source version, lesson, section and checkpoint match/);assert.match(root.allText,/Notes to Drafts may require a separate assessment/);assert.doesNotMatch(root.allText,/unchanged documents are never billed twice/);
 assert.match(root.allText,/Fictional observation · A2 Forensics/);
 assert.doesNotMatch(root.allText,/Section id|Section name|Blair Fictional|Held work/);
 assert.equal(root.all.filter(el=>el.tagName==='select'&&el.children.some(option=>option.value==='F1')).length,0,'no competing assignment selector');
 view.update({...scope,date:'2026-10-30'});
 button(root,'Generate feedback for this submission').dispatchEvent(new Event('click'));await settle();
 const request=JSON.parse(calls.find(call=>call.options?.method==='POST').options.body);
 assert.equal(request.file_id,'fictional-a2');assert.equal(request.assignment_key,'F1');assert.equal(request.section_id,'a2');assert.equal(request.section_name,'A2 Forensics');assert.equal('date' in request,false);
});
test('changing A2 to A3 clears the old draft controls and targets only A3 files',async()=>{
 const {root,calls,view}=setup();view.update(scope);view.setFiles(files,null);await settle();
 view.update({...scope,sectionId:'a3',sectionName:'A3 Forensics'});
 assert.equal(button(root,'Generate feedback for this submission'),undefined);
 view.setFiles(files,null);await settle();
 assert.match(root.allText,/Fictional observation · A3 Forensics/);assert.doesNotMatch(root.allText,/Alex Fictional|Held work/);
 button(root,'Generate feedback for this submission').dispatchEvent(new Event('click'));await settle();
 const request=JSON.parse(calls.find(call=>call.options?.method==='POST').options.body);
 assert.equal(request.file_id,'fictional-a3');assert.equal(request.section_id,'a3');
});
test('unresolved section files stay held and cannot produce a generation request',async()=>{
 const {root,calls,view}=setup();view.update(scope);view.setFiles([files[2]],null);await settle();
 assert.match(root.allText,/unresolved identities or sections/);assert.equal(button(root,'Generate feedback for this submission'),undefined);
 assert.equal(calls.some(call=>call.options?.method==='POST'),false);
});
test('a cached draft for another lesson or section is not adopted',async()=>{
 const {root,view}=setup({status:'draft_ready',packet:{class_key:'forensics',assignment_key:'OTHER',section_id:'a3'}});
 view.update(scope);view.setFiles(files,null);await settle();
 assert.doesNotMatch(root.allText,/Draft assessment|stored draft.*recovered/);
 assert.match(root.allText,/No stored draft for the current version/);
});

function draft(overrides={}){return {schema_version:'hub-assessment-provider/v1',external_writes:0,status:'draft_ready',cached:true,scores:{},packet:{
 class_key:'forensics',assignment_key:'F1',section_id:'a2',stage:'initial',
 source:{file_id:'fictional-a2',version:'1',modified_at:'2026-09-23T12:00:00Z',sha256:'synthetic-content-hash'},
 identity:{display_name:'Alex Fictional',student_id:'fictional-student',identity_basis:'synthetic'},responses:[],feedback:'Synthetic feedback',comparison_status:'No comparison',...overrides}};}

test('a late generation response cannot populate the newly selected section',async()=>{
 let finish;const response=new Promise(resolve=>{finish=resolve;});
 const {root,view}=setup(null,()=>response);view.update(scope);view.setFiles(files,null);await settle();
 button(root,'Generate feedback for this submission').dispatchEvent(new Event('click'));await settle();
 view.update({...scope,sectionId:'a3',sectionName:'A3 Forensics'});view.setFiles(files,null);await settle();
 finish(draft());await settle();
 assert.match(root.allText,/Fictional observation · A3 Forensics/);assert.doesNotMatch(root.allText,/Draft assessment · Alex/);
});
test('a saved draft control cannot write after the selected section changes',async()=>{
 const {root,calls,view}=setup(draft());view.update(scope);view.setFiles(files,null);await settle();
 const oldSave=button(root,'Save reviewed assessment to the workbook');assert.ok(oldSave);
 view.update({...scope,sectionId:'a3',sectionName:'A3 Forensics'});view.setFiles(files,null);await settle();
 oldSave.dispatchEvent(new Event('click'));await settle();
 assert.equal(calls.some(call=>call.url==='/api/assessment-save'&&call.options?.method==='POST'),false);
});
test('new source holds invalidate an already displayed draft and remove generation controls',async()=>{
 const {root,calls,view}=setup(draft());view.update(scope);view.setFiles([files[0]],null);await settle();
 const oldSave=button(root,'Save reviewed assessment to the workbook');assert.ok(oldSave);
 view.setFiles([{...files[0],review_reasons:['form_deck_assignment_disagreement']}],null);await settle();
 assert.equal(button(root,'Generate feedback for this submission'),undefined);assert.equal(button(root,'Save reviewed assessment to the workbook'),undefined);
 assert.match(root.allText,/unresolved identities or sections/);
 oldSave.dispatchEvent(new Event('click'));await settle();assert.equal(calls.some(call=>call.options?.method==='POST'),false);
});
test('a generated response for a different file or checkpoint is held',async()=>{
 const {root,view}=setup(null,draft({stage:'final'}));view.update(scope);view.setFiles(files,null);await settle();
 button(root,'Generate feedback for this submission').dispatchEvent(new Event('click'));await settle();
 assert.match(root.allText,/does not match the requested lesson, section, checkpoint and submission/);
 assert.equal(button(root,'Save reviewed assessment to the workbook'),undefined);
});

function savedReply({missing=false,...packetChanges}={}){
 const p=draft(packetChanges).packet;
 p.responses=[{question_id:'N1',category:'NOTES',judgment:'meaningful_attempt',response_excerpt:'Literal original evidence',evidence_note:'Original evidence note',conceptual_level:'Developing',explanation_level:'Names an idea',conceptual_feedback:'Original item feedback',optional:false}];
 return {schema_version:'hub-assessment-review/v1',status:missing?'saved_packet_required':'saved_review_ready',external_writes:0,live_model_calls:0,
  packet:missing?null:p,review_basis:{record_id:'asmt-saved',record_sha256:'synthetic-record-hash',source:{file_id:'fictional-a2',sha256:'synthetic-content-hash',version:'1'}},
  base:{submission:{record_id:'asmt-saved',feedback:'Synthetic feedback'},answers:[{question_id:'N1',stage:'NOTES',response:'Literal original evidence',evidence_note:'Original evidence note',conceptual_level:'Developing',explanation_level:'Names an idea',conceptual_feedback:'Original item feedback',meaningful_complete:'TRUE'}]}};
}
function savedSetup({loaded=savedReply(),saveResponse,saveFetch}={}){
 const root=new Element(document,'div'),calls=[];let savedCount=0;
 const data=structuredClone(records);
 data.data.submissions=[{record_id:'asmt-saved',class_key:'forensics',assignment_key:'F1',section_id:'a2',assessment_stage:'initial',status:'assessed',source_file_id:'fictional-a2'}];
 const fetcher=async(url,options)=>{
  calls.push({url,options});
  if(url.startsWith('/api/assessment-save?'))return {ok:true,json:async()=>typeof loaded==='function'?loaded():loaded};
  if(url==='/api/assessment-save'&&options?.method==='POST'){
   if(saveFetch)return saveFetch();
   return {ok:true,json:async()=>saveResponse||{status:'saved',record_id:'asmt-successor',readback_verified:true,answers:1,superseded:['asmt-saved'],content_score:10,content_score_exact:'10',revision_score:null}};
  }
  if(url.includes('draft_for='))return {ok:true,json:async()=>({status:'no_draft'})};
  return {ok:true,json:async()=>({configured:false,dependency:'provider_not_configured'})};
 };
 return {root,calls,data,get savedCount(){return savedCount;},view:mountAssessmentReview(root,{fetcher,records:()=>data,onSaved:()=>savedCount++})};
}
async function loadExisting(h){h.view.update(scope);h.view.setFiles(files,null);await settle();button(h.root,'Load saved assessment for review').dispatchEvent(new Event('click'));await settle();}
const saveCalls=h=>h.calls.filter(c=>c.url==='/api/assessment-save'&&c.options?.method==='POST');

test('load saved assessment works with no provider and performs no model or save request',async()=>{
 const h=savedSetup();await loadExisting(h);
 assert.match(h.root.allText,/Saved assessment review/);assert.match(h.root.allText,/no model call/);
 assert.match(h.root.allText,/Literal original evidence/);assert.match(h.root.allText,/Original evidence note/);
 const loaded=h.calls.filter(c=>c.url.startsWith('/api/assessment-save?'));
 assert.equal(loaded.length,1);assert.deepEqual(Object.fromEntries(new URL(loaded[0].url,'https://fictional.invalid').searchParams),{record_id:'asmt-saved'});
 assert.equal(h.calls.some(c=>c.options?.method==='POST'),false);
 assert.ok(button(h.root,'Save reviewed assessment to the workbook'));
});
test('review shows explicit feedback changes and Save uses existing endpoint and unchanged evidence',async()=>{
 const h=savedSetup();await loadExisting(h);
 const report=h.root.all.find(e=>e.tagName==='textarea');report.value='Approved clerical correction';report.dispatchEvent(new Event('input'));
 assert.match(h.root.allText,/Synthetic feedback → Approved clerical correction/);
 button(h.root,'Save reviewed assessment to the workbook').dispatchEvent(new Event('click'));await settle();
 const body=JSON.parse(saveCalls(h)[0].options.body);
 assert.equal(body.review_basis.record_id,'asmt-saved');assert.equal(body.packet.feedback,report.value);
 assert.equal(body.packet.responses[0].response_excerpt,'Literal original evidence');assert.equal(body.packet.responses[0].evidence_note,'Original evidence note');
 assert.match(h.root.allText,/Saved and read back/);assert.match(h.root.allText,/asmt-saved/);assert.equal(h.savedCount,1);
});
test('missing retained packet permits explicit prepared successor review without reconstructing judgments',async()=>{
 const h=savedSetup({loaded:savedReply({missing:true})});await loadExisting(h);
 assert.match(h.root.allText,/cannot recreate its original judgments/);assert.equal(button(h.root,'Save reviewed assessment to the workbook'),undefined);
 const prepared=h.root.all.find(e=>e.tagName==='textarea');const p=savedReply().packet;p.feedback='Prepared approved correction';prepared.value=JSON.stringify(p);
 button(h.root,'Review prepared successor').dispatchEvent(new Event('click'));await settle();
 assert.match(h.root.allText,/Synthetic feedback → Prepared approved correction/);
 assert.match(h.root.allText,/not retained.*meaningful_attempt/);
 assert.equal(saveCalls(h).length,0);
 button(h.root,'Save reviewed assessment to the workbook').dispatchEvent(new Event('click'));await settle();
 assert.equal(JSON.parse(saveCalls(h)[0].options.body).packet.feedback,p.feedback);
});
test('a prepared packet for another checkpoint or source cannot produce a Save control',async()=>{
 const h=savedSetup({loaded:savedReply({missing:true})});await loadExisting(h);
 const prepared=h.root.all.find(e=>e.tagName==='textarea');prepared.value=JSON.stringify(savedReply({stage:'final'}).packet);
 button(h.root,'Review prepared successor').dispatchEvent(new Event('click'));await settle();
 assert.match(h.root.allText,/does not match the selected lesson/);assert.equal(button(h.root,'Save reviewed assessment to the workbook'),undefined);assert.equal(saveCalls(h).length,0);
});
test('malformed prepared packet preserves editable JSON and cancel controls',async()=>{
 const h=savedSetup({loaded:savedReply({missing:true})});await loadExisting(h);
 const prepared=h.root.all.find(e=>e.tagName==='textarea');const malformed=savedReply().packet;delete malformed.identity;
 prepared.value=JSON.stringify(malformed);const original=prepared.value;
 button(h.root,'Review prepared successor').dispatchEvent(new Event('click'));await settle();
 assert.equal(h.root.all.find(e=>e.tagName==='textarea'),prepared);assert.equal(prepared.value,original);
 assert.ok(button(h.root,'Cancel saved review'));assert.equal(button(h.root,'Save reviewed assessment to the workbook'),undefined);
 assert.match(h.root.allText,/packet is incomplete/);assert.equal(saveCalls(h).length,0);
});
test('cancelled saved review invalidates detached prepared import and Save controls',async()=>{
 const h=savedSetup();await loadExisting(h);
 const save=button(h.root,'Save reviewed assessment to the workbook');button(h.root,'Discard this draft view').dispatchEvent(new Event('click'));
 save.dispatchEvent(new Event('click'));await settle();assert.equal(saveCalls(h).length,0);
 const missing=savedSetup({loaded:savedReply({missing:true})});await loadExisting(missing);
 const prepared=missing.root.all.find(e=>e.tagName==='textarea');prepared.value=JSON.stringify(savedReply().packet);
 const oldImport=button(missing.root,'Review prepared successor');button(missing.root,'Cancel saved review').dispatchEvent(new Event('click'));
 oldImport.dispatchEvent(new Event('click'));await settle();assert.equal(button(missing.root,'Save reviewed assessment to the workbook'),undefined);
});
test('late saved assessment load cannot populate a different section or changed source',async()=>{
 let finish;const wait=new Promise(resolve=>{finish=resolve;});
 const h=savedSetup({loaded:()=>wait});await loadExisting(h);
 h.view.setFiles([{...files[0],source_revision:'new-source',review_reasons:['source_changed']}],null);
 finish(savedReply());await settle();assert.equal(button(h.root,'Save reviewed assessment to the workbook'),undefined);assert.equal(saveCalls(h).length,0);
});
test('changing checkpoint invalidates detached saved review controls',async()=>{
 const h=savedSetup();await loadExisting(h);
 const save=button(h.root,'Save reviewed assessment to the workbook');
 const stage=h.root.all.find(e=>e.tagName==='select'&&e.children.some(c=>c.value==='final'));
 stage.value='final';stage.dispatchEvent(new Event('change'));
 save.dispatchEvent(new Event('click'));await settle();assert.equal(saveCalls(h).length,0);
});
test('duplicate clicks and an uncertain Save never send a new request or claim success',async()=>{
 let finish;const pending=new Promise(resolve=>{finish=resolve;});
 const h=savedSetup({saveFetch:()=>pending});await loadExisting(h);
 const save=button(h.root,'Save reviewed assessment to the workbook');save.dispatchEvent(new Event('click'));save.dispatchEvent(new Event('click'));await settle();
 assert.equal(saveCalls(h).length,1);
 finish({ok:false,json:async()=>({error:'assessment_write_uncertain'})});await settle();
 save.dispatchEvent(new Event('click'));await settle();assert.equal(saveCalls(h).length,1);assert.equal(save.disabled,true);
 assert.match(h.root.allText,/will not send another Save/);assert.doesNotMatch(h.root.allText,/Saved and read back/);assert.equal(h.savedCount,0);
 assert.doesNotMatch(h.root.allText,/Nothing was saved/);
});
test('a success-shaped response without verified readback stays unresolved',async()=>{
 const h=savedSetup({saveResponse:{status:'saved',readback_verified:false}});await loadExisting(h);
 button(h.root,'Save reviewed assessment to the workbook').dispatchEvent(new Event('click'));await settle();
 assert.doesNotMatch(h.root.allText,/Saved and read back/);assert.equal(h.savedCount,0);
});

const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const contentHash=value=>createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
function freshFixture(){
 const packet={schema_version:'hub-assessment-packet/v1',class_key:'forensics',section_id:'a2',section_name:'A2 Forensics',assignment_key:'F1',stage:'initial',stage_reference:'Teacher-reviewed current checkpoint',
  identity:{student_id:'12345',student_id_kind:'IC number',learner_key:'',display_name:'Alex Fictional',identity_basis:'Verified fictional IC identity'},
  source:{file_id:'fictional-a2',url:'https://docs.google.com/presentation/d/fictional-a2/edit',version:'34',modified_at:'2026-10-25T12:00:00Z',sha256:'c'.repeat(64)},
  manifest:{inventory_line:'[[MARKER-INVENTORY:C01]]',version_label:'Current source',notes_ids:Array.from({length:29},(_,i)=>'N'+(i+1)),bank_ids:[],draft_ids:[],optional_ids:[]},
  responses:Array.from({length:29},(_,i)=>({question_id:'N'+(i+1),category:'NOTES',judgment:i<8?'meaningful_attempt':'blank',response_excerpt:i<8?'Current literal evidence '+(i+1):'',evidence_note:'Reviewed current evidence note '+(i+1),
   conceptual_level:i<8?'Developing':'Evidence unavailable',explanation_level:'',conceptual_feedback:'Reviewed item feedback '+(i+1),prompt:'Current prompt '+(i+1),prompt_basis:'Current labeled source',slide_number:String(i+1),optional:false})),
  feedback:'Teacher-reviewed current report with explicit partial completion',comparison_status:'Fresh current assessment; no invented growth',ic_assignment_id:'Verified existing destination'};
 const base={submission:{record_id:'asmt-old',class_key:'forensics',assignment_key:'F1',section_id:'a2',assessment_stage:'initial',source_file_id:'fictional-a2',source_revision:'9',source_sha256:'a'.repeat(64),status:'assessed'},answers:[{record_id:'asmt-old',question_id:'old-item',stage:'NOTES'}]};
 const reply={schema_version:'hub-assessment-review/v1',status:'fresh_packet_required',packet:null,external_writes:0,live_model_calls:0,base,
  review_basis:{mode:'fresh_current_review',record_id:'asmt-old',record_sha256:'b'.repeat(64),source:{file_id:'fictional-a2',sha256:packet.source.sha256,version:'34',modified_at:packet.source.modified_at,raw_sha256:'d'.repeat(64),metadata_sha256:'e'.repeat(64)}}};
 return {packet,reply};
}
function freshSetup({loadReply,saveFetch}={}){
 const fixture=freshFixture(),root=new Element(document,'div'),calls=[];let savedCount=0;
 const data=structuredClone(records);data.data.submissions=[{...fixture.reply.base.submission}];
 const fetcher=async(url,options)=>{
  calls.push({url,options});
  if(url.includes('mode=fresh_current_review'))return typeof loadReply==='function'?loadReply():{ok:true,json:async()=>loadReply||fixture.reply};
  if(url.startsWith('/api/assessment-save?'))return {ok:false,json:async()=>({error:'submission_changed_reassess'})};
  if(url==='/api/assessment-save'&&options?.method==='POST')return saveFetch?saveFetch():{ok:true,json:async()=>({record_id:'asmt-fresh',readback_verified:true,superseded:['asmt-old']})};
  if(url.includes('draft_for='))return {ok:true,json:async()=>({status:'no_draft'})};
  return {ok:true,json:async()=>({configured:false,dependency:'provider_not_configured'})};
 };
 return {root,calls,data,fixture,get savedCount(){return savedCount;},view:mountAssessmentReview(root,{fetcher,records:()=>data,onSaved:()=>savedCount++})};
}
async function openFresh(h){
 h.view.update(scope);h.view.setFiles([{...files[0],source_revision:'34'}],null);await settle();
 button(h.root,'Review fresh current-source assessment').dispatchEvent(new Event('click'));await settle();
}
async function importFresh(h,input=h.fixture.packet){
 const textarea=h.root.all.find(e=>e.tagName==='textarea');textarea.value=JSON.stringify(input);
 button(h.root,'Review imported current assessment').dispatchEvent(new Event('click'));
 for(let n=0;n<20;n++)await new Promise(resolve=>setTimeout(resolve,0));
 return textarea;
}
const approveFresh=h=>{const checkbox=h.root.all.find(e=>e.tagName==='input'&&e.type==='checkbox');checkbox.checked=true;checkbox.dispatchEvent(new Event('change'));return checkbox;};

test('fresh 29-item current assessment opens despite old revision with no provider or stored draft, and preserves 8 meaningful plus 21 incomplete judgments',async()=>{
 const h=freshSetup();await openFresh(h);
 const loads=h.calls.filter(call=>call.url.startsWith('/api/assessment-save?'));
 assert.equal(loads.length,1);assert.deepEqual(Object.fromEntries(new URL(loads[0].url,'https://fictional.invalid').searchParams),{record_id:'asmt-old',mode:'fresh_current_review'});
 const textarea=await importFresh(h);
 assert.match(h.root.allText,/8 meaningful; 21 not meaningful; 29 total/);assert.match(h.root.allText,/Original saved record: asmt-old/);assert.match(h.root.allText,/current source version 34/);
 assert.match(h.root.allText,/Current literal evidence 8/);assert.match(h.root.allText,/Reviewed item feedback 29/);assert.match(h.root.allText,/Teacher-reviewed current report/);
 assert.equal(h.root.all.filter(e=>e.tagName==='textarea').length,0);assert.equal(h.root.children.find(e=>e.attributes?.class==='assessment-form').inert,true);
 const save=button(h.root,'Save reviewed assessment to the workbook');assert.equal(save.disabled,true);save.dispatchEvent(new Event('click'));await settle();assert.equal(saveCalls(h).length,0);
 textarea.value='tampered detached editor';h.fixture.packet.responses[0].judgment='blank';
 approveFresh(h);save.dispatchEvent(new Event('click'));await settle();
 const body=JSON.parse(saveCalls(h)[0].options.body);
 assert.equal(body.packet.responses[0].judgment,'meaningful_attempt');assert.equal(body.packet.responses.filter(response=>response.judgment==='blank').length,21);
 assert.deepEqual(body.review_basis,{...h.fixture.reply.review_basis,packet_sha256:contentHash(body.packet)});
 assert.equal(body.packet.responses.length,29);assert.equal('original_packet' in body.review_basis,false);assert.equal('approved_changes' in body.review_basis,false);
 assert.match(h.root.allText,/Saved and read back/);assert.equal(h.savedCount,1);assert.equal(h.calls.some(call=>call.url==='/api/assessment-run'&&call.options?.method==='POST'),false);
});

test('fresh import accepts only a bare complete packet or exact packet wrapper',async()=>{
 const h=freshSetup();await openFresh(h);await importFresh(h,{packet:h.fixture.packet});assert.ok(button(h.root,'Save reviewed assessment to the workbook'));
 const bad=freshSetup();await openFresh(bad);const textarea=await importFresh(bad,{packet:bad.fixture.packet,packet_sha256:'f'.repeat(64)});
 assert.equal(bad.root.all.find(e=>e.tagName==='textarea'),textarea);assert.equal(button(bad.root,'Save reviewed assessment to the workbook'),undefined);assert.equal(saveCalls(bad).length,0);
});

test('fresh import rejects malformed, wrong scope, incomplete packets and every changed current source binding without losing editable JSON',async()=>{
 const mutations=[packet=>{packet.class_key='biology';},packet=>{packet.assignment_key='OTHER';},packet=>{packet.section_id='a3';},packet=>{packet.section_name='Unverified section';},packet=>{packet.stage='final';},
  packet=>{packet.source.file_id='other-file';},packet=>{packet.source.version='35';},packet=>{packet.source.sha256='f'.repeat(64);},packet=>{packet.source.modified_at='2026-10-25T13:00:00Z';},
  packet=>{delete packet.manifest;},packet=>{delete packet.identity;},packet=>{delete packet.responses[0].judgment;},packet=>{packet.responses[0].judgment='assumed_complete';},
  packet=>{packet.responses.push({...packet.responses[0]});}];
 for(const mutate of mutations){
  const h=freshSetup();await openFresh(h);const packet=structuredClone(h.fixture.packet);mutate(packet);const textarea=await importFresh(h,packet);
  assert.equal(h.root.all.find(e=>e.tagName==='textarea'),textarea);assert.equal(button(h.root,'Save reviewed assessment to the workbook'),undefined);assert.ok(button(h.root,'Cancel fresh current-source review'));assert.equal(saveCalls(h).length,0);
 }
 const malformed=freshSetup();await openFresh(malformed);const textarea=malformed.root.all.find(e=>e.tagName==='textarea');textarea.value='{';
 button(malformed.root,'Review imported current assessment').dispatchEvent(new Event('click'));await settle();assert.equal(textarea.value,'{');assert.equal(button(malformed.root,'Save reviewed assessment to the workbook'),undefined);
});

test('fresh review uses imported judgment counts rather than a hardcoded count rule',async()=>{
 const h=freshSetup();await openFresh(h);const packet=structuredClone(h.fixture.packet);packet.responses[8].judgment='meaningful_attempt';packet.responses[8].response_excerpt='Explicit additionally reviewed current evidence';
 await importFresh(h,packet);assert.match(h.root.allText,/9 meaningful; 20 not meaningful; 29 total/);assert.equal(saveCalls(h).length,0);
});

test('cancelled and stale fresh reviews cannot import or save through detached controls',async()=>{
 const cancelled=freshSetup();await openFresh(cancelled);const textarea=cancelled.root.all.find(e=>e.tagName==='textarea');textarea.value=JSON.stringify(cancelled.fixture.packet);
 const oldImport=button(cancelled.root,'Review imported current assessment');button(cancelled.root,'Cancel fresh current-source review').dispatchEvent(new Event('click'));
 oldImport.dispatchEvent(new Event('click'));await settle();assert.equal(button(cancelled.root,'Save reviewed assessment to the workbook'),undefined);
 for(const change of ['cancel','section','source','checkpoint']){
  const h=freshSetup();await openFresh(h);await importFresh(h);const save=button(h.root,'Save reviewed assessment to the workbook');approveFresh(h);
  if(change==='cancel')button(h.root,'Cancel fresh current-source review').dispatchEvent(new Event('click'));
  else if(change==='section'){h.view.update({...scope,sectionId:'a3'});h.view.setFiles(files,null);}
  else if(change==='source')h.view.setFiles([{...files[0],source_revision:'35'}],null);
  else{const stage=h.root.all.find(e=>e.tagName==='select'&&e.children.some(c=>c.value==='final'));stage.value='final';stage.dispatchEvent(new Event('change'));}
  save.dispatchEvent(new Event('click'));await settle();assert.equal(saveCalls(h).length,0,change);
 }
});

test('late fresh source reply cannot populate a changed section',async()=>{
 let finish;const pending=new Promise(resolve=>{finish=resolve;}),h=freshSetup({loadReply:()=>pending});await openFresh(h);
 h.view.update({...scope,sectionId:'a3'});h.view.setFiles(files,null);finish({ok:true,json:async()=>h.fixture.reply});await settle();
 assert.equal(button(h.root,'Review imported current assessment'),undefined);assert.equal(saveCalls(h).length,0);
});

test('fresh double Save and uncertain or unverified readback remain one attempt without claiming success',async()=>{
 let finish;const pending=new Promise(resolve=>{finish=resolve;}),h=freshSetup({saveFetch:()=>pending});await openFresh(h);await importFresh(h);approveFresh(h);
 const save=button(h.root,'Save reviewed assessment to the workbook');save.dispatchEvent(new Event('click'));save.dispatchEvent(new Event('click'));await settle();assert.equal(saveCalls(h).length,1);
 finish({ok:false,json:async()=>({error:'assessment_write_uncertain'})});await settle();save.dispatchEvent(new Event('click'));await settle();
 assert.equal(saveCalls(h).length,1);assert.equal(save.disabled,true);assert.equal(h.savedCount,0);assert.match(h.root.allText,/will not send another Save/);assert.doesNotMatch(h.root.allText,/Saved and read back/);
 const unverified=freshSetup({saveFetch:async()=>({ok:true,json:async()=>({record_id:'asmt-fresh',readback_verified:false})})});await openFresh(unverified);await importFresh(unverified);approveFresh(unverified);
 button(unverified.root,'Save reviewed assessment to the workbook').dispatchEvent(new Event('click'));await settle();assert.equal(unverified.savedCount,0);assert.doesNotMatch(unverified.root.allText,/Saved and read back/);
});

test('fresh source-change failure directs a refreshed fresh review without generating',async()=>{
 const h=freshSetup({saveFetch:async()=>({ok:false,json:async()=>({error:'submission_changed_reassess'})})});await openFresh(h);await importFresh(h);approveFresh(h);
 button(h.root,'Save reviewed assessment to the workbook').dispatchEvent(new Event('click'));await settle();
 const message=h.root.all.filter(e=>e.attributes?.role==='status').at(-1).textContent;
 assert.match(message,/Refresh the fresh current-source review/);assert.doesNotMatch(message,/Generate|draft/i);assert.equal(h.savedCount,0);
 assert.equal(h.calls.some(call=>call.url==='/api/assessment-run'&&call.options?.method==='POST'),false);
});
