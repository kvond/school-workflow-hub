// Synthetic transport only: no model, Drive or school writes.
import test from 'node:test';
import assert from 'node:assert/strict';
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
