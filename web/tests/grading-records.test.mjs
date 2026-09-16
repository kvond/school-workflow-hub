import test from 'node:test';
import assert from 'node:assert/strict';
import {validateRecords,sectionOptions,filterRecords,createRecordsLoader,renderRecords,renderSummaries,score} from '../grading-records.mjs';

function fixture(){return {schema_version:'hub-grading-records/v1',provenance:'source_read',external_writes:0,source_revision:'fictional-read',checked_at:'2026-09-01T12:00:00Z',submissions:[
 {record_id:'one',class_key:'forensics',section_id:'a',section_name:'Period A',student_id:'one',display_name:'Alex F.',assignment_title:'Fictional One',status:'proposed',content_score:6.25,revision_score:null,answers:[],included_in_counts:true},
 {record_id:'two',class_key:'forensics',section_id:'b',section_name:'Period B',student_id:'two',display_name:'Blair F.',assignment_title:'Fictional Two',status:'identity_unverified',content_score:8,revision_score:null,answers:[],included_in_counts:false},
 {record_id:'three',class_key:'biology',section_id:'c',section_name:'Period C',student_id:'three',display_name:'Casey F.',assignment_title:'Fictional Three',status:'superseded',answers:[],included_in_counts:false}],summaries:[
 {class_key:'forensics',section_id:'a',section_name:'Period A',summary:'Connect the claim to the evidence.',coverage:'Partial supplied work.',submitted_students:1,checked_at:'2026-09-01T12:00:00Z'},
 {class_key:'forensics',section_id:'b',section_name:'Period B',summary:'Verify the student match before drawing a class conclusion.',coverage:'Identity review remains.',submitted_students:0,checked_at:'2026-09-01T12:00:00Z'}]};}

class Element{
 constructor(doc,tag,text=''){this.ownerDocument=doc;this.tagName=tag;this.children=[];this.textContent=text;this.attributes={};}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(key,value){this.attributes[key]=value;}
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
 assert.match(target.allText,/Blair F/);assert.match(target.allText,/<script>fictional<\/script>/);
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
