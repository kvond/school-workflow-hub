// Synthetic transport only: no model, Drive or school writes.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mountAssessmentReview} from '../assessment-review.mjs';

class Element extends EventTarget{
 constructor(doc,tag){super();this.ownerDocument=doc;this.tagName=tag;this.children=[];this.attributes={};this.textContent='';this.value='';}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=[...children];}
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
