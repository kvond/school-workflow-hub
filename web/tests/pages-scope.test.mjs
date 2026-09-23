// Fictional DOM integration test. No browser, upstream fetch, or private data.
import assert from 'node:assert/strict';

class Element extends EventTarget{
 constructor(doc,tag){super();this.ownerDocument=doc;this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.textContent='';this.hidden=false;this.disabled=false;this.value='';}
 append(...children){for(const c of children){c.parentNode=this;this.children.push(c);}}
 replaceChildren(...children){this.children=[];this.append(...children);}
 replaceWith(replacement){const siblings=this.parentNode?.children;if(siblings){siblings.splice(siblings.indexOf(this),1,replacement);replacement.parentNode=this.parentNode;}}
 get firstChild(){return this.children[0];}
 setAttribute(k,v){this.attributes[k]=v;}
 removeAttribute(k){delete this.attributes[k];}
 get all(){return [this,...this.children.flatMap(c=>c.all)];}
 get allText(){return this.textContent+' '+this.children.map(c=>c.allText).join(' ');}
}
class Document extends EventTarget{
 constructor(){super();this.nodes=new Map();for(const id of ['date','page-control','page-agenda','page-feedback','page-summary','page-research','detail','detail-body','detail-title']){const e=new Element(this,'div');e.id=id;this.nodes.set(id,e);}this.nodes.get('date').value='2026-09-16';this.links=['control','agenda','feedback','summary','research'].map(page=>{const e=new Element(this,'a');e.dataset.page=page;return e;});}
 createElement(tag){return new Element(this,tag);}
 getElementById(id){return this.nodes.get(id);}
 querySelectorAll(query){assert.equal(query,'[data-page]');return this.links;}
}
globalThis.document=new Document();globalThis.window=new EventTarget();globalThis.location={hash:'#feedback'};
const sessionValues=new Map();globalThis.sessionStorage={getItem:key=>sessionValues.get(key)||null,setItem:(key,value)=>sessionValues.set(key,value)};
globalThis.CustomEvent=class extends Event{constructor(type,options){super(type);this.detail=options?.detail;}};
const fixture={sections:[{class_key:'biology',section_id:'c',section_name:'A4 Biology'},{class_key:'forensics',section_id:'a',section_name:'A2 Forensics'},{class_key:'forensics',section_id:'b',section_name:'A3 Forensics'},{class_key:'anatomy',section_id:'d',section_name:'B3 Anatomy'}],schema_version:'hub-grading-records/v1',provenance:'source_read',external_writes:0,checked_at:'2026-09-16T12:00:00Z',source_revision:'fictional-unit-transport',assignments:[
 {assignment_key:'BIO-ENERGY',class_key:'biology',assignment_title:'Fictional energy'},
 {assignment_key:'FOR-REASON',class_key:'forensics',assignment_title:'Fictional reasoning'},
 {assignment_key:'FOR-SCENE',class_key:'forensics',assignment_title:'Fictional scene'}],submissions:[
 {record_id:'a',class_key:'biology',section_id:'c',section_name:'Period C',student_id:'f-a',display_name:'Alex F.',assignment_key:'BIO-ENERGY',assignment_title:'Fictional energy',assessment_stage:'initial',status:'assessed',content_score:60/13,content_score_exact:'60/13',revision_score:null,answers:[{question_id:'Q1',stage:'NOTES',response:'Fictional first line.\nFictional second line.',conceptual_level:'Developing',explanation_level:'Names idea'}],included_in_counts:true},
 {record_id:'b',class_key:'forensics',section_id:'a',section_name:'Period A',student_id:'f-b',display_name:'Blair F.',assignment_key:'FOR-REASON',assignment_title:'Fictional reasoning',assessment_stage:'final',status:'assessed',content_score:3.75,revision_score:null,answers:[],included_in_counts:true},
 {record_id:'d',class_key:'forensics',section_id:'b',section_name:'Period B',student_id:'f-d',display_name:'Devon F.',assignment_key:'FOR-SCENE',assignment_title:'Fictional scene',assessment_stage:'final',status:'assessed',content_score:10,revision_score:null,answers:[],included_in_counts:true}
 ],summaries:['biology:c','forensics:a','forensics:b','anatomy:d'].map(scope=>({class_key:scope.split(':')[0],section_id:scope.split(':')[1],section_name:'Fictional '+scope,summary:'One concise fictional teaching next step.',coverage:'Fictional partial sample.',submitted_students:1,checked_at:'2026-09-16T12:00:00Z'}))};
let readCount=0,failNext=false,waitForDiscovery=null,discoveryAssignments=[];const discoveryCalls=[],inventoryCalls=[],catalogCalls=[];
let discoveryFiles=[{file_id:'fictional-new-file',source_url:'https://docs.google.com/presentation/d/fictional-new-file/edit',name:'New fictional work',class_key:'forensics',assignment_key:'FOR-REASON',assignment_title:'Fictional reasoning',section_id:'a',section_name:'Period A',student_id:'f-new',display_name:'New F.',discovery_status:'new_ungraded',review_reasons:[]}];
for(let i=1;i<=5;i++)fixture.submissions.push({record_id:'unmatched-'+i,class_key:'biology',section_id:'',section_name:'',student_id:'',display_name:'Fictional unmatched '+i,assignment_title:'Fictional held work',status:'identity_unverified',content_score:null,revision_score:null,answers:[],included_in_counts:false});
globalThis.fetch=async(url,options)=>{
 if(url==='/api/grading-records'){readCount++;if(failNext)return{ok:false,status:503};return{ok:true,status:200,json:async()=>structuredClone(fixture)};}
 if(url.startsWith('/api/submission-catalog?')){
  const classKey=new URL(url,'https://fictional.invalid').searchParams.get('class_key');catalogCalls.push({url,options});
  return {ok:true,status:200,json:async()=>({schema_version:'hub-submission-catalog/v1',provenance:'source_read',external_writes:0,live_model_calls:0,class_key:classKey,checked_at:'2026-09-22T12:00:00Z',coverage:{complete:true,issues:[]},assignments:structuredClone(discoveryAssignments.filter(a=>a.class_key===classKey))})};
 }
 if(url.startsWith('/api/submission-inventory?')){
  inventoryCalls.push(url);const q=new URL(url,'https://fictional.invalid').searchParams;
  return {ok:true,json:async()=>({schema_version:'hub-submission-inventory/v1',class_key:q.get('class_key'),assignment_key:q.get('assignment'),section_id:q.get('section_id'),external_writes:0,files:[{file_id:'fictional-process-file',name:'Verified synthetic submission',status:'present',stage:'awaiting_assessment',class_key:q.get('class_key'),assignment_key:q.get('assignment'),section_id:q.get('section_id')}]})};
 }
 if(url==='/api/submission-monitor/refresh'){
  discoveryCalls.push({url,options});const requested=JSON.parse(options.body);
  if(waitForDiscovery)await waitForDiscovery;
  return {ok:true,status:200,json:async()=>({schema_version:'hub-submission-refresh/v1',provenance:'source_read',external_writes:0,live_model_calls:0,...requested,discovered_at:'2026-09-22T12:00:00Z',coverage:{complete:true},files:structuredClone(discoveryFiles.filter(f=>(!f.class_key||f.class_key===requested.class_key)&&(!f.assignment_key||requested.assignment_key==='all'||f.assignment_key===requested.assignment_key))),assignments:structuredClone(discoveryAssignments.filter(a=>a.class_key===requested.class_key&&a.assignment_key===requested.assignment_key))})};
 }
 return{ok:false,status:503,json:async()=>({error:'fictional-unavailable'})};
};
await import('../pages.mjs');
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};await settle();
const feedback=document.getElementById('page-feedback'),summary=document.getElementById('page-summary');
const select=label=>feedback.all.find(e=>e.tagName==='select'&&e.attributes['aria-label']===label);
const classSelect=select('Feedback class'),sectionSelect=select('Feedback section'),lessonSelect=select('Feedback lesson'),workSelect=select('Feedback checkpoint');
const table=()=>feedback.all.find(e=>e.tagName==='table');
const click=label=>feedback.all.find(e=>e.tagName==='button'&&e.textContent===label).dispatchEvent(new Event('click'));
const choose=(control,value)=>{control.value=value;control.dispatchEvent(new Event('change'));};
assert.equal(discoveryCalls.length,0,'initial All lessons never starts a broad source scan');
assert.equal(catalogCalls.length,1,'current lesson names are fetched through metadata-only GET');
assert.equal(catalogCalls[0].options.method,undefined);assert.equal(catalogCalls[0].url,'/api/submission-catalog?class_key=biology');
assert.match(feedback.allText,/Each assignment has its own turn-in Form/);
assert.equal(feedback.all.some(e=>e.tagName==='input'&&e.type==='date'),false,'Page 02 has no date-based assessment selector');
assert.equal(sectionSelect.parentNode.hidden,true,'single-section courses do not ask for a section');
assert.equal(sectionSelect.value,'c','Biology auto-selects its verified section');
assert.match(table().allText,/F\., Alex/);assert.match(table().allText,/unmatched 5, Fictional/,'unresolved work remains visible');
assert.doesNotMatch(table().allText,/F\., Blair/);
const collapsed=table().all.find(e=>e.tagName==='details'&&/Fictional first line/.test(e.allText));
assert.match(collapsed.allText,/Fictional first line.\nFictional second line./);assert.match(table().allText,/4.62 \/ 10/);
choose(classSelect,'forensics');
assert.equal(sectionSelect.parentNode.hidden,false);
assert.deepEqual(sectionSelect.children.map(e=>[e.value,e.textContent]),[['a','A2 Forensics'],['b','A3 Forensics']]);
assert.equal(sectionSelect.value,'a');assert.match(table().allText,/F\., Blair/);assert.doesNotMatch(table().allText,/F\., Devon/);
choose(sectionSelect,'b');assert.match(table().allText,/F\., Devon/);assert.doesNotMatch(table().allText,/F\., Blair/);
choose(sectionSelect,'a');choose(lessonSelect,'FOR-REASON');await settle();
assert.equal(discoveryCalls.length,1,'exact lesson selection starts a read-only discovery automatically');
click('Check selected lesson submissions');await settle();
const firstTarget=new URL(inventoryCalls.at(-1),'https://fictional.invalid').searchParams;
assert.deepEqual(Object.fromEntries(firstTarget),{class_key:'forensics',assignment:'FOR-REASON',section_id:'a'});
assert.match(feedback.allText,/Fictional reasoning/);assert.doesNotMatch(feedback.allText,/Section id|Section name|Assess new submissions date/);
// Agenda navigation/due-date changes cannot replace Page 02's selected lesson or assessment target.
document.dispatchEvent(new CustomEvent('agenda-day-loaded',{detail:{date:'2026-10-30',checked_at:'2026-09-23T12:00:00Z',lessons:[{class_key:'forensics',title:'Unrelated dated topic',due_date:'2026-10-30',links:[]}]}}));
assert.equal(lessonSelect.value,'FOR-REASON');assert.equal(sectionSelect.value,'a');
click('Process work');await settle();
assert.deepEqual(Object.fromEntries(new URL(inventoryCalls.at(-1),'https://fictional.invalid').searchParams),Object.fromEntries(firstTarget));
assert.doesNotMatch(feedback.allText,/Unrelated dated topic/);
// Refresh keeps explicit section/lesson filters; unresolved rows appear without becoming A2 work.
discoveryFiles.push({...discoveryFiles[0],file_id:'fictional-held-file',name:'Held original upload',section_id:'',section_name:'',student_id:'',display_name:'',discovery_status:'needs_review',review_reasons:['form_identity_unavailable']});
discoveryAssignments=[{class_key:'forensics',assignment_key:'FOR-REASON',assignment_title:'A newer title for the same stable lesson'}];
click('Refresh submissions');await settle();
assert.equal(sectionSelect.value,'a');assert.equal(lessonSelect.value,'FOR-REASON');
assert.equal(JSON.parse(discoveryCalls.at(-1).options.body).assignment_key,'FOR-REASON','different display titles cannot retarget a request');
assert.equal(lessonSelect.children.find(o=>o.value==='FOR-REASON').textContent,'Fictional reasoning');
assert.equal(feedback.all.some(el=>el.tagName==='button'&&el.textContent==='Generate feedback for this submission'),false,'fresh source checks invalidate the previous assessment form');
assert.match(table().allText,/F\., New/);assert.match(table().allText,/Held original upload/);assert.match(table().allText,/Unmatched/);
assert.doesNotMatch(table().allText,/F\., Devon/);assert.equal(fixture.submissions.length,8,'preview cannot mutate saved grades');
const beforeAll=discoveryCalls.length;choose(sectionSelect,'b');choose(lessonSelect,'all');click('Refresh submissions');await settle();assert.equal(discoveryCalls.length,beforeAll,'All lessons does not initiate a broad source read');assert.match(table().allText,/F\., Devon/);assert.match(table().allText,/Held original upload/);assert.doesNotMatch(table().allText,/F\., Blair|F\., New/);
// A section choice made during Refresh wins over its result.
let releaseDiscovery;waitForDiscovery=new Promise(resolve=>{releaseDiscovery=resolve;});
choose(lessonSelect,'FOR-REASON');const pendingCalls=discoveryCalls.length;click('Refresh submissions');
assert.equal(discoveryCalls.length,pendingCalls,'Refresh joins an already pending exact lesson read');
assert.match(table().allText,/Held original upload/,'cached exact lesson preview remains visible during a new read');
assert.match(feedback.allText,/Earlier preview/);
choose(sectionSelect,'a');assert.equal(discoveryCalls.length,pendingCalls,'section selection does not duplicate class/lesson reads');
releaseDiscovery();waitForDiscovery=null;await settle();
assert.equal(sectionSelect.value,'a');assert.match(table().allText,/F\., Blair/);assert.doesNotMatch(table().allText,/F\., Devon/);
choose(workSelect,'initial');assert.doesNotMatch(table().allText,/F\., Blair/);assert.match(table().allText,/Held original upload/);
choose(workSelect,'final');assert.match(table().allText,/F\., Blair/);
// New Agenda lessons are selectable before uploads exist, without inventing an assessment registry target.
discoveryAssignments=[{class_key:'forensics',assignment_key:'discovery:forensics:fictional',assignment_title:'New fictional lesson',discovery_only:true,registry_assignment_key:''}];
discoveryFiles=[];choose(lessonSelect,'all');const beforeNewCatalog=discoveryCalls.length;click('Refresh submissions');await settle();
assert.equal(discoveryCalls.length,beforeNewCatalog,'discovering new lesson names never reads student sources');
assert.ok(lessonSelect.children.some(option=>option.textContent==='New fictional lesson'));
choose(lessonSelect,'discovery:forensics:fictional');const requestsBefore=inventoryCalls.length;
click('Process work');await settle();assert.equal(inventoryCalls.length,requestsBefore);assert.match(feedback.allText,/registry binding must be verified/);
click('Refresh submissions');await settle();assert.equal(lessonSelect.value,'discovery:forensics:fictional','fresh discovery cannot reset the selected new lesson to All lessons');
assert.equal(JSON.parse(discoveryCalls.at(-1).options.body).assignment_key,'discovery:forensics:fictional');
await settle();
const emptyClassKey='discovery:anatomy:'+'c'.repeat(32);discoveryAssignments=[{class_key:'anatomy',assignment_key:emptyClassKey,assignment_title:'First lesson in an empty class',discovery_only:true,registry_assignment_key:''}];
const beforeEmptyClass=discoveryCalls.length;
choose(classSelect,'anatomy');assert.equal(sectionSelect.parentNode.hidden,true);assert.equal(sectionSelect.value,'d','empty Anatomy still gets its current roster section');assert.doesNotMatch(feedback.allText,/Held original upload|Submissions discovered/);
await settle();assert.ok(lessonSelect.children.some(o=>o.value===emptyClassKey),'a class without registry assignments can discover its lesson names');
assert.equal(discoveryCalls.length,beforeEmptyClass,'metadata discovery does not read submissions');
choose(lessonSelect,emptyClassKey);await settle();assert.equal(discoveryCalls.length,beforeEmptyClass+1);assert.equal(JSON.parse(discoveryCalls.at(-1).options.body).assignment_key,emptyClassKey);
choose(classSelect,'biology');assert.equal(sectionSelect.value,'c');assert.equal(lessonSelect.value,'all');assert.match(table().allText,/unmatched 5, Fictional/);
const cards=summary.all.filter(e=>e.tagName==='section'&&e.className==='surface');assert.equal(cards.length,4);
location.hash='#summary';window.dispatchEvent(new Event('hashchange'));assert.equal(summary.hidden,false);assert.equal(feedback.hidden,true);
failNext=true;summary.all.find(e=>e.tagName==='button'&&e.textContent==='Refresh summaries').dispatchEvent(new Event('click'));await settle();
assert.match(summary.allText,/workbook is unavailable/);assert.equal(table(),undefined);
// A fresh page starts a source read for its restored, server-known exact lesson.
// Only filter IDs were stored, never response data or student evidence.
failNext=false;discoveryFiles=[{file_id:'reload-source',source_url:'https://docs.google.com/presentation/d/reload-source/edit',name:'Reloaded source',class_key:'forensics',assignment_key:'FOR-SCENE',assignment_title:'Alias title',section_id:'b',section_name:'A3',student_id:'fictional-reload',display_name:'Reload F.',discovery_status:'new_ungraded',review_reasons:[]}];
sessionValues.set('hub-page2-selection/v1',JSON.stringify({classKey:'forensics',assignmentKey:'FOR-SCENE',sectionId:'b',stage:'initial'}));
const beforeReload=discoveryCalls.length;
globalThis.document=new Document();globalThis.window=new EventTarget();location.hash='#feedback';
await import('../pages.mjs?reload-exact-scope');await settle();
const reloaded=document.getElementById('page-feedback'),selectReload=label=>reloaded.all.find(e=>e.attributes['aria-label']===label);
assert.equal(discoveryCalls.length,beforeReload+1);assert.deepEqual(JSON.parse(discoveryCalls.at(-1).options.body),{class_key:'forensics',assignment_key:'FOR-SCENE'});
assert.equal(selectReload('Feedback lesson').value,'FOR-SCENE');assert.equal(selectReload('Feedback section').value,'b');assert.equal(selectReload('Feedback checkpoint').value,'initial');
assert.match(reloaded.allText,/Reloaded source/);assert.doesNotMatch(reloaded.allText,/F\., Blair/);
assert.deepEqual(Object.keys(JSON.parse(sessionValues.get('hub-page2-selection/v1'))).sort(),['assignmentKey','classKey','sectionId','stage']);
// A missing stored assignment is displayed as unavailable, never changed to All
// and never authorized by its remembered display label.
sessionValues.set('hub-page2-selection/v1',JSON.stringify({classKey:'forensics',assignmentKey:'fictional-unavailable',sectionId:'a',stage:'initial'}));
const beforeUnavailable=discoveryCalls.length;globalThis.document=new Document();globalThis.window=new EventTarget();
await import('../pages.mjs?reload-unavailable-scope');await settle();
const unavailable=document.getElementById('page-feedback');
assert.equal(discoveryCalls.length,beforeUnavailable);assert.match(unavailable.allText,/Selected lesson unavailable/);
const discoveryKey='discovery:forensics:'+'a'.repeat(32);
sessionValues.set('hub-page2-selection/v1',JSON.stringify({classKey:'forensics',assignmentKey:discoveryKey,sectionId:'a',stage:'initial'}));
discoveryAssignments=[{class_key:'forensics',assignment_key:discoveryKey,assignment_title:'Freshly verified discovered lesson',discovery_only:true,registry_assignment_key:''}];discoveryFiles=[];
globalThis.document=new Document();globalThis.window=new EventTarget();
await import('../pages.mjs?reload-discovery-scope');await settle();
assert.equal(discoveryCalls.length,beforeUnavailable+1,'a restored exact discovery key is revalidated by the read-only backend');
assert.equal(JSON.parse(discoveryCalls.at(-1).options.body).assignment_key,discoveryKey);
assert.match(document.getElementById('page-feedback').allText,/Freshly verified discovered lesson/);
assert.match(document.getElementById('page-feedback').allText,/registry binding needs verification before grading/);
console.log('Page 02 scope checks passed: automatic exact-lesson reads, reload startup, cache visibility, named stable targets, section isolation, and review holds.');
