import test from 'node:test';
import assert from 'node:assert/strict';
import {renderClassSummary,summarySections,studentRows,completionState,levelTable,aspectColumns,teacherChecks} from '../class-summary.mjs';
import {summaryLinks} from '../grading-records.mjs';

class Element{
 constructor(doc,tag,text=''){this.ownerDocument=doc;this.tagName=tag;this.children=[];this.textContent=text;this.attributes={};}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(key,value){this.attributes[key]=value;}
 addEventListener(){}
 get allText(){return this.textContent+' '+this.children.map(child=>child.allText).join(' ');}
}
const document={createElement(tag){return new Element(document,tag);}};
const scope={classKey:'biology',sectionId:'c',assignmentKey:'B1'};
const aspect=(q,stage,level,name,response='words here',note='')=>({question_id:q,stage,conceptual_level:level,critical_aspect:name,response,evidence_note:note});
// Fictional students and writing only.
function data(){return {assignments:[{assignment_key:'B1',class_key:'biology',assignment_title:'Fictional ecosystems'}],summaries:[
 {class_key:'biology',section_id:'c',section_name:'Period C',assignment_key:'B1',summary:"An idea worth chasing\nAva K. called a producer a door.\nRecurring ideas in students' words\nTwo of three first answers say energy is \"used up\".\nA possible next instructional contrast\nWhy this contrast: Ben L. still says used up.\nCompare: carbon and energy.\nAsk: How is the fate of carbon different?\nCheck these yourself\nCal M.: check wording against language, not understanding.",coverage:'3 students',checked_at:'2026-10-03T12:00:00Z'}],
 submissions:[
 {record_id:'a',class_key:'biology',section_id:'c',section_name:'Period C',assignment_key:'B1',display_name:'Ava K.',count_key:'k1',included_in_counts:true,assessment_stage:'final',notes_required:2,notes_completed:2,drafts_required:2,drafts_completed:2,checked_at:'2026-10-02',answers:[aspect('Q1','DRAFT','Proficient','Producers as the entry point'),aspect('Q2','DRAFT','Extending','Where transferred energy goes')]},
 {record_id:'b',class_key:'biology',section_id:'c',section_name:'Period C',assignment_key:'B1',display_name:'Ben L.',count_key:'k2',included_in_counts:true,assessment_stage:'final',notes_required:2,notes_completed:2,drafts_required:2,drafts_completed:1,checked_at:'2026-10-02',answers:[aspect('Q1','DRAFT','Proficient','Producers as the entry point'),aspect('Q2','NOTES','Developing','Where transferred energy goes','the animals use it up'),aspect('Q2','DRAFT','Evidence unavailable','Where transferred energy goes','')]},
 {record_id:'c',class_key:'biology',section_id:'c',section_name:'Period C',assignment_key:'B1',display_name:'Cal M.',count_key:'k3',included_in_counts:true,assessment_stage:'final',notes_required:2,notes_completed:2,drafts_required:2,drafts_completed:2,checked_at:'2026-10-02',answers:[aspect('Q1','NOTES','Beginning','Producers as the entry point','copied line',"Matches the printed slide text"),aspect('Q1','DRAFT','Developing','Producers as the entry point'),aspect('Q2','DRAFT','Proficient','Where transferred energy goes')]},
 {record_id:'x',class_key:'biology',section_id:'d',section_name:'Period D',assignment_key:'B1',display_name:'Other P.',count_key:'k9',included_in_counts:true,answers:[]},
 {record_id:'u',class_key:'biology',section_id:'c',section_name:'Period C',assignment_key:'B1',display_name:'Unmatched U.',count_key:'',included_in_counts:false,answers:[]}]};}
test('only this lesson and section, matched work, one row per student',()=>{
 assert.deepEqual(studentRows(data(),scope).map(r=>r.display_name),['Ava K.','Ben L.','Cal M.']);
});
test('completion is counted from the saved counts for the checkpoint',()=>{
 const rows=studentRows(data(),scope);
 assert.deepEqual(rows.map(r=>completionState(r).state),['Complete','Incomplete','Complete']);
 assert.equal(completionState(rows[1]).detail,'Revisions 1/2');
 assert.equal(completionState({assessment_stage:'final'}).state,'Completion unknown');
});
test('the class table counts one level per student per critical aspect',()=>{
 const rows=studentRows(data(),scope),table=levelTable(rows,aspectColumns(rows));
 assert.deepEqual(table.levels,['Beginning','Developing','Proficient','Extending','Evidence unavailable']);
 assert.deepEqual(table.rows.map(r=>[r.label,r.counts.Developing,r.counts.Proficient,r.counts.Extending,r.counts['Evidence unavailable']]),
  [['Producers as the entry point',1,2,0,0],['Where transferred energy goes',0,1,1,1]]);
});
test('teacher checks come from saved notes, holds and blank revisions',()=>{
 const checks=teacherChecks(studentRows(data(),scope));
 assert.ok(checks.some(c=>c.startsWith('Cal M.: Producers as the entry point (first answer): Matches the printed slide text')));
 assert.ok(checks.some(c=>c.startsWith('Ben L.: the revised answer for Where transferred energy goes is blank')));
});
test('written sections come from the saved summary, and the page follows the sample order',()=>{
 assert.deepEqual(Object.keys(summarySections(data().summaries[0].summary)),['An idea worth chasing',"Recurring ideas in students' words",'A possible next instructional contrast','Check these yourself']);
 const root=new Element(document,'main');renderClassSummary(root,data(),scope,{saved:data().summaries[0]});
 const text=root.allText;let last=-1;
 for(const heading of ['Class growth summary','Fictional ecosystems','Teacher notes at a glance','An idea worth chasing','Current understanding across the class',"Recurring ideas in students' words",'From evidence to a teaching decision','A possible next instructional contrast','Check these yourself','Every student, one row each']){
  const at=text.indexOf(heading);assert.ok(at>last,heading);last=at;}
 assert.match(text,/Why this contrast: Ben L\./);assert.match(text,/Cal M\.: check wording/);assert.match(text,/This summary changes no grades/);
 assert.doesNotMatch(text,/Other P\.|Unmatched U\./);
});
test('without a saved summary the written sections say they are not written yet',()=>{
 const root=new Element(document,'main');renderClassSummary(root,data(),scope,{saved:null});
 assert.match(root.allText,/Not written yet/);assert.match(root.allText,/Every student, one row each/);
});
test('Page 3 links each lesson and section with matched work to its own summary page',()=>{
 const links=summaryLinks(data());
 assert.deepEqual(links.map(l=>[l.section_id,l.students,!!l.saved]),[['c',3,true],['d',1,false]]);
 assert.equal(links[0].href,'class-summary.html?class=biology&section=c&assignment=B1');
});
