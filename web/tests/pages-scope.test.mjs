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
globalThis.CustomEvent=class extends Event{constructor(type,options){super(type);this.detail=options?.detail;}};
const fixture={schema_version:'hub-grading-records/v1',provenance:'source_read',external_writes:0,checked_at:'2026-09-16T12:00:00Z',source_revision:'fictional-unit-transport',assignments:[
 {assignment_key:'BIO-ENERGY',class_key:'biology',assignment_title:'Fictional energy'},
 {assignment_key:'FOR-REASON',class_key:'forensics',assignment_title:'Fictional reasoning'},
 {assignment_key:'FOR-SCENE',class_key:'forensics',assignment_title:'Fictional scene'}],submissions:[
 {record_id:'a',class_key:'biology',section_id:'c',section_name:'Period C',student_id:'f-a',display_name:'Alex F.',assignment_key:'BIO-ENERGY',assignment_title:'Fictional energy',assessment_stage:'initial',status:'assessed',content_score:60/13,content_score_exact:'60/13',revision_score:null,answers:[{question_id:'Q1',stage:'NOTES',response:'Fictional first line.\nFictional second line.',conceptual_level:'Developing',explanation_level:'Names idea'}],included_in_counts:true},
 {record_id:'b',class_key:'forensics',section_id:'a',section_name:'Period A',student_id:'f-b',display_name:'Blair F.',assignment_key:'FOR-REASON',assignment_title:'Fictional reasoning',assessment_stage:'final',status:'assessed',content_score:3.75,revision_score:null,answers:[],included_in_counts:true},
 {record_id:'d',class_key:'forensics',section_id:'b',section_name:'Period B',student_id:'f-d',display_name:'Devon F.',assignment_key:'FOR-SCENE',assignment_title:'Fictional scene',assessment_stage:'final',status:'assessed',content_score:10,revision_score:null,answers:[],included_in_counts:true}
 ],summaries:['biology:c','forensics:a','forensics:b','anatomy:d'].map(scope=>({class_key:scope.split(':')[0],section_id:scope.split(':')[1],section_name:'Fictional '+scope,summary:'One concise fictional teaching next step.',coverage:'Fictional partial sample.',submitted_students:1,checked_at:'2026-09-16T12:00:00Z'}))};
let readCount=0,failNext=false,waitForDiscovery=null;const discoveryCalls=[];
let discoveryFiles=[{file_id:'fictional-new-file',source_url:'https://docs.google.com/presentation/d/fictional-new-file/edit',name:'New fictional work',class_key:'forensics',assignment_key:'FOR-REASON',assignment_title:'Fictional reasoning',section_id:'a',section_name:'Period A',student_id:'f-new',display_name:'New F.',discovery_status:'new_ungraded',review_reasons:[]}];
for(let i=1;i<=5;i++)fixture.submissions.push({record_id:'unmatched-'+i,class_key:'biology',section_id:'',section_name:'',student_id:'',display_name:'Fictional unmatched '+i,assignment_title:'Fictional held work',status:'identity_unverified',content_score:null,revision_score:null,answers:[],included_in_counts:false});
globalThis.fetch=async(url,options)=>{
 if(url==='/api/grading-records'){readCount++;if(failNext)return{ok:false,status:503};return{ok:true,status:200,json:async()=>structuredClone(fixture)};}
 if(url==='/api/submission-monitor/refresh'){
  discoveryCalls.push({url,options});const requested=JSON.parse(options.body);
  if(waitForDiscovery)await waitForDiscovery;
  return {ok:true,status:200,json:async()=>({schema_version:'hub-submission-refresh/v1',provenance:'source_read',external_writes:0,live_model_calls:0,...requested,discovered_at:'2026-09-22T12:00:00Z',coverage:{complete:true},files:structuredClone(discoveryFiles)})};
 }
 return{ok:false,status:503,json:async()=>({error:'fictional-unavailable'})};
};
await import('../pages.mjs');
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};await settle();
const feedback=document.getElementById('page-feedback'),summary=document.getElementById('page-summary');
assert.match(feedback.allText,/Each assignment has its own turn-in Form/);assert.doesNotMatch(feedback.allText,/exact shared-Form assignment/);
const select=label=>feedback.all.find(e=>e.tagName==='select'&&e.attributes['aria-label']===label);
const classSelect=select('Feedback class'),sectionSelect=select('Feedback section');
const table=()=>feedback.all.find(e=>e.tagName==='table');
assert.match(table().allText,/F\., Alex/);assert.doesNotMatch(table().allText,/F\., Blair/);
const collapsed=table().all.find(e=>e.tagName==='details');assert.equal(collapsed.open,undefined);
assert.match(collapsed.allText,/Fictional first line.\nFictional second line./);assert.match(collapsed.allText,/Content calculation: 60\/13/);assert.match(table().allText,/4.62 \/ 10/);
assert.equal(sectionSelect.value,'matched');assert.ok(sectionSelect.children.some(e=>e.textContent==='Unmatched (5)'));assert.doesNotMatch(table().allText,/Fictional unmatched/);
sectionSelect.value='';sectionSelect.dispatchEvent(new Event('change'));assert.match(table().allText,/unmatched 5, Fictional/);assert.doesNotMatch(table().allText,/F\., Alex/);
classSelect.value='forensics';classSelect.dispatchEvent(new Event('change'));
assert.deepEqual(sectionSelect.children.map(e=>e.value),['matched','all','a','b']);assert.match(table().allText,/F\., Blair/);assert.match(table().allText,/F\., Devon/);assert.doesNotMatch(table().allText,/F\., Alex/);
sectionSelect.value='a';sectionSelect.dispatchEvent(new Event('change'));
assert.match(table().allText,/F\., Blair/);assert.doesNotMatch(table().allText,/F\., Devon/);
feedback.all.find(e=>e.tagName==='button'&&e.textContent==='Refresh submissions').dispatchEvent(new Event('click'));await settle();assert.equal(sectionSelect.value,'a');assert.match(table().allText,/F\., Blair/);assert.doesNotMatch(table().allText,/F\., Devon/);
assert.equal(discoveryCalls.length,1);assert.equal(discoveryCalls[0].options.method,'POST');assert.deepEqual(JSON.parse(discoveryCalls[0].options.body),{class_key:'forensics',assignment_key:'all'});assert.match(table().allText,/F\., New/);assert.match(table().allText,/New work · not graded/);assert.equal(fixture.submissions.length,8,'discovery never mutates saved assessment rows');assert.match(feedback.allText,/workbook read/);assert.match(feedback.allText,/Submissions discovered/);
// U01: Class/section, Lesson name and Notes/Drafts are the primary selectors; no date picker in the primary row.
const lessonSelect=select('Feedback lesson'),workSelect=select('Feedback checkpoint');
assert.ok(lessonSelect&&workSelect,'lesson name and Notes/Drafts selectors exist');
const primaryBar=feedback.all.find(e=>e.className==='workspace-controls');
assert.ok(primaryBar&&!primaryBar.all.some(e=>e.tagName==='input'&&e.type==='date'),'no lesson-date input in the primary selector row');
// U02: choosing a lesson narrows the table, and switching lesson fully replaces the rows.
sectionSelect.value='all';sectionSelect.dispatchEvent(new Event('change'));
assert.match(table().allText,/F\., Blair/);assert.match(table().allText,/F\., Devon/);
lessonSelect.value='FOR-SCENE';lessonSelect.dispatchEvent(new Event('change'));
assert.match(table().allText,/F\., Devon/);assert.doesNotMatch(table().allText,/F\., Blair/);
lessonSelect.value='FOR-REASON';lessonSelect.dispatchEvent(new Event('change'));
assert.match(table().allText,/F\., Blair/);assert.doesNotMatch(table().allText,/F\., Devon/);
// Notes/Drafts partitions by checkpoint (both fictional forensics rows are final=Drafts).
lessonSelect.value='all';lessonSelect.dispatchEvent(new Event('change'));
workSelect.value='initial';workSelect.dispatchEvent(new Event('change'));
assert.doesNotMatch(feedback.allText,/F\., Blair/);assert.doesNotMatch(feedback.allText,/F\., Devon/);assert.match(table().allText,/F\., New/);assert.match(table().allText,/checkpoint needs review/);
workSelect.value='final';workSelect.dispatchEvent(new Event('change'));
assert.match(table().allText,/F\., Blair/);assert.match(table().allText,/F\., Devon/);
workSelect.value='all';workSelect.dispatchEvent(new Event('change'));
// Changing class resets the lesson selector to All lessons (no stale lesson leaks across classes).
lessonSelect.value='FOR-REASON';lessonSelect.dispatchEvent(new Event('change'));
classSelect.value='biology';classSelect.dispatchEvent(new Event('change'));
assert.equal(lessonSelect.value,'all');assert.match(table().allText,/F\., Alex/);assert.doesNotMatch(table().allText,/F\., Blair/);
assert.doesNotMatch(feedback.allText,/F\., New|Submissions discovered/,'class changes clear the previous discovery preview');
classSelect.value='forensics';classSelect.dispatchEvent(new Event('change'));
// Fresh unverified work is visible after Refresh without changing an explicit filter.
const refreshSubmissions=feedback.all.find(e=>e.tagName==='button'&&e.textContent==='Refresh submissions');
discoveryFiles.push({...discoveryFiles[0],file_id:'fictional-held-file',name:'Held original upload',section_id:'',section_name:'',student_id:'',display_name:'',discovery_status:'needs_review',review_reasons:['form_identity_unavailable']});
lessonSelect.value='FOR-REASON';lessonSelect.dispatchEvent(new Event('change'));
assert.equal(sectionSelect.value,'matched');refreshSubmissions.dispatchEvent(new Event('click'));await settle();
assert.equal(sectionSelect.value,'all','untouched default reveals newly discovered unmatched work');
assert.equal(lessonSelect.value,'FOR-REASON','the selected lesson stays unchanged');
assert.match(table().allText,/Held original upload/);assert.match(table().allText,/form identity unavailable/);assert.match(table().allText,/Not assessed/);
assert.doesNotMatch(table().allText,/F\., Devon/,'the known other lesson stays excluded');
sectionSelect.value='matched';sectionSelect.dispatchEvent(new Event('change'));refreshSubmissions.dispatchEvent(new Event('click'));await settle();
assert.equal(sectionSelect.value,'matched','explicit Matched sections is preserved');assert.doesNotMatch(table().allText,/Held original upload/);
assert.match(feedback.allText,/record needs a section match/,'held work remains discoverable through the existing hint');
sectionSelect.value='a';sectionSelect.dispatchEvent(new Event('change'));refreshSubmissions.dispatchEvent(new Event('click'));await settle();
assert.equal(sectionSelect.value,'a');assert.match(table().allText,/F\., Blair/);assert.doesNotMatch(table().allText,/Held original upload|F\., Devon/);
sectionSelect.value='';sectionSelect.dispatchEvent(new Event('change'));refreshSubmissions.dispatchEvent(new Event('click'));await settle();
assert.equal(sectionSelect.value,'');assert.match(table().allText,/Held original upload/);assert.doesNotMatch(table().allText,/F\., Blair/);
// A choice made while the read is pending wins over automatic default reveal.
classSelect.value='biology';classSelect.dispatchEvent(new Event('change'));
classSelect.value='forensics';classSelect.dispatchEvent(new Event('change'));
let releaseDiscovery;waitForDiscovery=new Promise(resolve=>{releaseDiscovery=resolve;});
refreshSubmissions.dispatchEvent(new Event('click'));
sectionSelect.value='b';sectionSelect.dispatchEvent(new Event('change'));
releaseDiscovery();waitForDiscovery=null;await settle();
assert.equal(sectionSelect.value,'b');assert.match(table().allText,/F\., Devon/);assert.doesNotMatch(table().allText,/Held original upload|F\., Blair|F\., New/);
const cards=summary.all.filter(e=>e.tagName==='section'&&e.className==='surface');assert.equal(cards.length,4);assert.ok(cards.every(e=>e.children.filter(c=>c.tagName==='h2').length===1));
location.hash='#summary';window.dispatchEvent(new Event('hashchange'));assert.equal(summary.hidden,false);assert.equal(feedback.hidden,true);
failNext=true;summary.all.find(e=>e.tagName==='button'&&e.textContent==='Refresh summaries').dispatchEvent(new Event('click'));await settle();assert.equal(summary.all.filter(e=>e.tagName==='section'&&e.className==='surface').length,0);assert.match(summary.allText,/workbook is unavailable/);assert.equal(table(),undefined);
console.log('Page scope DOM checks passed: default matched view, unmatched access, class/section changes, detail evidence, refresh, summary cards, and errors.');
