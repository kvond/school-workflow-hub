import {filterRecords,validateRecords} from './grading-records.mjs';

const courses={biology:'Biology',forensics:'Forensics',anatomy:'Anatomy'};
const scoreFields=['content_score','revision_score'];
const reasons={
 section_missing:'Section is not matched.',
 student_id_missing:'Infinite Campus Student Number is missing. A roster match alone cannot identify a grade destination.',
 assignment_key_missing:'Assignment identity is missing.',
 identity_unverified:'Student identity needs review.',
 superseded:'This record has been superseded.',
 held:'The recorded grade is on hold.',
 duplicate_target:'Multiple records share this student and assignment. Choose the current record before posting.',
 score_blank:'Unscored; no zero will be substituted.',
 score_invalid:'The recorded score needs review.'
};
const text=value=>typeof value==='string'?value:'';
const hasId=value=>typeof value==='string'&&Boolean(value.trim());
const isHeld=row=>Boolean(row.hold_reason)||/^(?:held|hold|on_hold)(?:_|$)/.test(text(row.status));
function freeze(value){if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;}
function validateSource(data){
 validateRecords(data);
 if(typeof data.source_revision!=='string'||!/^[a-f0-9]{64}$/.test(data.source_revision)||typeof data.checked_at!=='string'||!Number.isFinite(Date.parse(data.checked_at)))throw Error('Refresh the private grading records before reviewing grades.');
 const seen=new Set();
 for(const row of data.submissions){
  if(typeof row.record_id!=='string'||seen.has(row.record_id)||typeof row.assignment_title!=='string')throw Error('The submission identities need review.');
  seen.add(row.record_id);
 }
 return data;
}

// A source review only: every IC destination still requires its own verification.
export function buildPostingReview(data,{classKey,sectionId='matched'}={}){
 validateSource(data);
 if(!courses[classKey]||typeof sectionId!=='string'||(!['matched','all',''].includes(sectionId)&&!data.submissions.some(row=>row.class_key===classKey&&row.section_id===sectionId)))throw Error('Choose a valid class and section before reviewing grades.');
 const selected=filterRecords(data,classKey,sectionId),targets=new Map();
 for(const row of selected){
  if(isHeld(row)||['identity_unverified','superseded'].includes(row.status)||!hasId(row.student_id)||!hasId(row.assignment_key)||!hasId(row.section_id))continue;
  const key=JSON.stringify([row.class_key,row.section_id,row.student_id,row.assignment_key]);
  targets.set(key,(targets.get(key)||0)+1);
 }
 const rows=selected.map(row=>{
  const excluded=[];
  if(!hasId(row.section_id))excluded.push('section_missing');
  if(!hasId(row.student_id))excluded.push('student_id_missing');
  if(!hasId(row.assignment_key))excluded.push('assignment_key_missing');
  if(['identity_unverified','superseded'].includes(row.status))excluded.push(row.status);
  if(isHeld(row))excluded.push('held');
  const key=JSON.stringify([row.class_key,row.section_id,row.student_id,row.assignment_key]);
  if(!excluded.length&&targets.get(key)>1)excluded.push('duplicate_target');
  const scores=Object.fromEntries(scoreFields.map(field=>{
   const value=row[field],issues=[...excluded];
   if(value===null||value===undefined||value==='')issues.push('score_blank');
   else if(typeof value!=='number'||!Number.isFinite(value)||value<0||value>10)issues.push('score_invalid');
   return [field,{value:issues.length?null:value,exact:issues.length?'':text(row[field+'_exact']),exclusion_reasons:issues}];
  }));
  return {record_id:row.record_id,class_key:classKey,section_id:text(row.section_id),section_name:text(row.section_name),student_id:text(row.student_id),display_name:text(row.display_name),assignment_key:text(row.assignment_key),assignment_title:row.assignment_title,ic_assignment_id:text(row.ic_assignment_id),content_points_possible:text(row.content_points_possible),revision_points_possible:text(row.revision_points_possible),source_revision:text(row.source_revision),status:text(row.status),hold_reason:text(row.hold_reason),ic_posting_status:text(row.ic_posting_status),exclusion_reasons:excluded,scores,destination_verified:false};
 });
 return freeze({schema_version:'hub-grade-posting-review/v1',source_revision:data.source_revision,checked_at:data.checked_at,scope:{classKey,sectionId},record_ids:rows.map(row=>row.record_id),rows,external_writes:0,posting_enabled:false});
}

// Shared entry point for constructing a new review from a fresh endpoint read.
// The raw IC reference identifies only its recorded component; never derive a
// separate revision assignment or points denominator from another component.
export const sourceReviewFromData=buildPostingReview;

export function assertPostingReviewCurrent(review,freshData){
 if(review?.schema_version!=='hub-grade-posting-review/v1')throw Error('Open a new grade review.');
 const fresh=buildPostingReview(freshData,review.scope);
 if(fresh.source_revision!==review.source_revision||JSON.stringify(fresh.rows)!==JSON.stringify(review.rows)||JSON.stringify(fresh.record_ids)!==JSON.stringify(review.record_ids))throw Error('The grading records changed. Open a new review before posting.');
 return fresh;
}

function node(root,tag,value){const element=root.ownerDocument.createElement(tag);if(value!==undefined)element.textContent=String(value);return element;}
function line(root,value){const paragraph=node(root,'p',value);root.append(paragraph);return paragraph;}
export function renderPostingReview(root,review){
 root.replaceChildren();
 if(review?.schema_version!=='hub-grade-posting-review/v1')throw Error('Open a new grade review.');
 root.append(node(root,'h3',`Review ${courses[review.scope.classKey]} grades`));
 line(root,`${review.rows.length} submission records in the selected class and section. Scores below come from the grading workbook.`);
 line(root,'Infinite Campus Student Number, section, and assignment targets must be verified against the current gradebook before any grade can be posted. Existing grades and teacher changes also require a fresh check.');
 if(!review.rows.length){line(root,'No submission records in this selection. This does not establish missing work.');return;}
 const table=node(root,'table');table.className='work-table';table.setAttribute('aria-label','Grades to review before Infinite Campus posting');
 const head=node(root,'thead'),heading=node(root,'tr');
 for(const label of ['Student / section','Assignment','Content / notes','Revision','Review notes'])heading.append(node(root,'th',label));head.append(heading);table.append(head);
 const body=node(root,'tbody');
 for(const row of review.rows){
  const tr=node(root,'tr'),student=node(root,'td');student.append(node(root,'strong',row.display_name||'Identity needs matching'));line(student,row.section_name||'Unmatched');tr.append(student,node(root,'td',row.assignment_title));
  for(const field of scoreFields){const score=row.scores[field],cell=node(root,'td');line(cell,score.value===null?'—':`${score.value} / 10`);if(score.exact)line(cell,`Exact recorded value: ${score.exact}`);const points=row[field==='content_score'?'content_points_possible':'revision_points_possible'];if(points)line(cell,`Recorded points possible: ${points}. Verify against Infinite Campus.`);for(const reason of score.exclusion_reasons.filter(reason=>reason.startsWith('score_')))line(cell,reasons[reason]);tr.append(cell);}
  const notes=node(root,'td');line(notes,`Recorded assessment status: ${row.status||'not supplied'}.`);for(const reason of row.exclusion_reasons)line(notes,reasons[reason]);if(row.hold_reason)line(notes,row.hold_reason);
  line(notes,`Recorded IC status: ${row.ic_posting_status||'not supplied'}. This is not a verified gradebook readback.`);line(notes,'Infinite Campus assignment mapping has not been verified.');tr.append(notes);body.append(tr);
 }
 table.append(body);const wrap=node(root,'div');wrap.className='work-table-wrap';wrap.append(table);root.append(wrap);
 line(root,'This review has not posted grades or changed the workbook.');
}
