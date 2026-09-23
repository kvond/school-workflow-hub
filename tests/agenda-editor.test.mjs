// Synthetic DOM and transport. Imports the production app rather than copying it.
import assert from 'node:assert/strict';
import {test} from 'node:test';

class Element {
 constructor(tag,doc){this.tagName=tag;this.doc=doc;this.children=[];this.listeners=new Map();this.textContent='';this.disabled=false;this.hidden=false;this.style={setProperty(){}};this.classList={add(){}};}
 set id(value){this._id=value;this.doc.ids.set(value,this);}
 get id(){return this._id;}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 before(){}
 setAttribute(){}
 addEventListener(name,fn){const handlers=this.listeners.get(name)||[];handlers.push(fn);this.listeners.set(name,handlers);}
 dispatchEvent(event){for(const fn of this.listeners.get(event.type)||[])fn(event);}
 click(){if(!this.disabled)this.dispatchEvent({type:'click',target:this});}
}
class Document extends Element {
 constructor(){super('document',null);this.doc=this;this.ids=new Map();this.selectors=new Map();}
 createElement(tag){return new Element(tag,this);}
 getElementById(id){if(!this.ids.has(id)){const element=this.createElement('div');element.id=id;}return this.ids.get(id);}
 querySelector(selector){if(!this.selectors.has(selector))this.selectors.set(selector,this.createElement('div'));return this.selectors.get(selector);}
}
class TestEvent {constructor(type,{detail}={}){this.type=type;this.detail=detail;}}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const dayData=date=>({schema_version:'hub-agenda-day/v1',status:'source_read',date,
 save_enabled:false,run_enabled:false,external_writes:0,checked_at:'2032-01-07T13:00:00Z',closures:'',
 lessons:['biology','forensics','anatomy'].map(class_key=>({class_key,title:'Fictional lesson',
 plan:'Fictional plan',notes:'',origin:'Weekly Agenda',weekly_plan:'Fictional plan',todo:'',prep:'',
 source_reference:'Fictional weekly row',teacher_slides:null,links:[]}))});
const response=(body,ok=true)=>({ok,status:ok?200:503,json:async()=>body});
const find=(element,predicate)=>predicate(element)?element:element.children.map(child=>find(child,predicate)).find(Boolean);
let importId=0;
async function boot(t){
 const names=['document','window','fetch','CustomEvent','crypto'];
 const original=new Map(names.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
 const doc=new Document(),requests=[],states=[];
 doc.addEventListener('agenda-saving-state',event=>states.push(event.detail));
 const globals={document:doc,window:{addEventListener(){},open(){}},CustomEvent:TestEvent,
 crypto:{randomUUID:()=> 'fictional-save-request-1234'},
 fetch:(url,options={})=>url==='./operations.preview.json'?Promise.resolve(response({},false)):
  new Promise(resolve=>requests.push({url,options,resolve}))};
 for(const [name,value] of Object.entries(globals))Object.defineProperty(globalThis,name,{value,writable:true,configurable:true});
 t.after(()=>{for(const name of names){const descriptor=original.get(name);if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete globalThis[name];}});
 await import('../web/app.mjs?test='+ ++importId);
 const select=date=>doc.dispatchEvent(new TestEvent('agenda-request-day',{detail:{date}}));
 const load=async(date='2032-01-07',data=dayData(date))=>{select(date);const req=requests.at(-1);assert.match(req.url,/agenda-day/);req.resolve(response(data));await settle();return requests.filter(r=>r.url.includes('/agenda-save?')).slice(-3);};
 const capability=async(req,revision='b'.repeat(64),ok=true)=>{const course=new URL(req.url,'https://example.invalid').searchParams.get('class_key');req.resolve(response(ok?{schema_version:'hub-agenda-save/v1',save_enabled:true,revision,values:dayData('2032-01-07').lessons.find(l=>l.class_key===course)}:{error:'agenda_day_not_writable'},ok));await settle();};
 const button=()=>find(doc.getElementById('classes').children[0],e=>e.tagName==='button'&&e.textContent==='Save to Agenda');
 const typeNote=text=>{const input=doc.getElementById('notes-0');input.value=text;input.dispatchEvent({type:'input',target:input});};
 const save=()=>{button().click();const req=requests.at(-1);assert.equal(req.options.method,'POST');return req;};
 const finishSave=async req=>{const payload=JSON.parse(req.options.body);req.resolve(response({status:'saved',readback_verified:true,values:payload.values,revision:'c'.repeat(64)}));await settle();};
 return {doc,requests,states,load,capability,button,typeNote,save,finishSave};
}

for(const staleFails of [false,true])test('late capability '+(staleFails?'failure':'success')+' cannot replace the current save revision',async t=>{
 const app=await boot(t);
 const old=await app.load();
 const current=await app.load();
 await app.capability(current[0],'b'.repeat(64));
 await app.capability(old[0],'a'.repeat(64),!staleFails);
 assert.equal(app.button().disabled,false);
 app.typeNote('Teacher-authored note');
 const request=app.save();
 assert.equal(JSON.parse(request.options.body).revision,'b'.repeat(64));
 await app.finishSave(request);
});

test('selecting a date resets readiness and ignores the earlier date response',async t=>{
 const app=await boot(t);
 const old=await app.load();
 await app.capability(old[0]);
 assert.equal(app.states.at(-1).ready,1);
 const next=await app.load('2032-01-10');
 for(const req of next)await app.capability(req,'',false);
 await app.capability(old[1]);
 assert.equal(app.states.at(-1).date,'2032-01-10');
 assert.equal(app.states.at(-1).ready,0);
 assert.equal(app.states.at(-1).pending,0);
 assert.equal(app.button().disabled,true);
});

test('typing during a save preserves the newer unsaved draft',async t=>{
 const app=await boot(t);
 const probes=await app.load();
 await app.capability(probes[0]);
 app.typeNote('First edit');
 const request=app.save();
 app.typeNote('Second edit while saving');
 await app.finishSave(request);
 assert.equal(JSON.parse(request.options.body).values.notes,'First edit');
 assert.equal(app.doc.getElementById('notes-0').value,'Second edit while saving');
 assert.equal(app.doc.getElementById('draft-status').hidden,false);
});

const formUrl=id=>'https://docs.google.com/forms/d/'+id.repeat(24)+'/viewform';
const formLinks=element=>[...(element.tagName==='a'&&element.textContent.startsWith('Turn in')?[element]:[]),...element.children.flatMap(formLinks)];
test('Agenda shows distinct named turn-in Forms for both assignments in the same week',async t=>{
 const app=await boot(t),data=dayData('2032-01-07'),lesson=data.lessons[1];
 lesson.turnin_form=formUrl('a');
 lesson.turnin_forms=[{assignment_key:'forensics:alpha',assignment_title:'Fictional observation',url:formUrl('a'),primary:true},
  {assignment_key:'forensics:beta',assignment_title:'Fictional reasoning',url:formUrl('b'),primary:false}];
 lesson.links=[{label:'Existing generic submit link',url:formUrl('a')}];
 await app.load(data.date,data);
 const card=app.doc.getElementById('classes').children[1],links=formLinks(card);
 assert.deepEqual(links.map(l=>[l.textContent,l.href]),[
  ['Turn in · Fictional observation ↗',formUrl('a')],['Turn in · Fictional reasoning ↗',formUrl('b')]]);
 assert.equal(find(card,e=>e.textContent==='Existing generic submit link ↗'),undefined);
 assert.ok(links.every(l=>l.target==='_blank'&&l.rel==='noopener noreferrer'));
});

test('fresh Form links replace earlier source links while a teaching-notes draft is preserved',async t=>{
 const app=await boot(t),data=dayData('2032-01-07');
 data.lessons[0].turnin_forms=[{assignment_title:'Fictional lesson',url:formUrl('a')}];
 await app.load(data.date,data);
 app.typeNote('Unsaved teacher note');
 data.lessons[0].turnin_forms[0].url=formUrl('b');
 await app.load(data.date,data);
 assert.deepEqual(formLinks(app.doc.getElementById('classes').children[0]).map(l=>l.href),[formUrl('b')]);
 assert.equal(app.doc.getElementById('notes-0').value,'Unsaved teacher note');
});

test('legacy single Form remains visible and an invalid replacement URL is rejected',async t=>{
 const app=await boot(t),data=dayData('2032-01-07');
 data.lessons[0].turnin_form=formUrl('a');
 await app.load(data.date,data);
 assert.deepEqual(formLinks(app.doc.getElementById('classes').children[0]).map(l=>l.href),[formUrl('a')]);
 const invalid=dayData(data.date);
 invalid.lessons[0].turnin_forms=[{assignment_title:'Fictional invalid source',url:'javascript:alert(1)'}];
 await app.load(data.date,invalid);
 assert.deepEqual(formLinks(app.doc.getElementById('classes').children[0]).map(l=>l.href),[formUrl('a')]);
});

test('a held empty Form list cannot fall back to the old single Form',async t=>{
 const app=await boot(t),data=dayData('2032-01-07');
 data.lessons[1].turnin_form=formUrl('a');
 data.lessons[1].turnin_forms=[];
 data.lessons[1].turnin_forms_review_reason='assignment_form_turn_in_links_disagree';
 await app.load(data.date,data);
 const card=app.doc.getElementById('classes').children[1];
 assert.deepEqual(formLinks(card),[]);
 assert.ok(find(card,e=>e.textContent==='Turn-in Form links need review in this week’s Agenda.'));
});
