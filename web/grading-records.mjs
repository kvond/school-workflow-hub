import {safeLink} from './contract.mjs';
const courses={biology:'Biology',forensics:'Forensics',anatomy:'Anatomy'};
export function validateRecords(data){
 if(!data||data.schema_version!=='hub-grading-records/v1'||data.provenance!=='source_read'||data.external_writes!==0||!data.source_revision||!data.checked_at||!Array.isArray(data.submissions)||!Array.isArray(data.summaries))throw Error('The private grading records could not be verified.');
 const seen=new Set();for(const summary of data.summaries){const key=summary.class_key+'\0'+summary.section_id;if(seen.has(key)||!courses[summary.class_key]||!summary.section_id||typeof summary.summary!=='string')throw Error('The class summaries need review.');seen.add(key);}
 for(const row of data.submissions)if(!courses[row.class_key]||!row.record_id||!Array.isArray(row.answers))throw Error('The submission records need review.');
 return data;
}
export function sectionOptions(data,classKey){
 const sections=new Map();let unmatched=0;
 for(const row of data.submissions)if(row.class_key===classKey){if(row.section_id)sections.set(row.section_id,row.section_name||row.section_id);else unmatched++;}
 const options=[...sections].sort((a,b)=>a[1].localeCompare(b[1]));if(unmatched)options.push(['',`Unmatched (${unmatched})`]);return options;
}
export function filterRecords(data,classKey,sectionId='matched'){
 return data.submissions.filter(row=>row.class_key===classKey&&(sectionId==='all'||(sectionId==='matched'?Boolean(row.section_id):row.section_id===sectionId))).sort((a,b)=>(a.section_name||'').localeCompare(b.section_name||'')||(a.display_name||'').localeCompare(b.display_name||'')||a.assignment_title.localeCompare(b.assignment_title));
}
export function createRecordsLoader({fetcher=fetch,onState}){
 let generation=0,controller;
 return async()=>{
  const current=++generation;controller?.abort();controller=new AbortController();onState({status:'loading'});
  try{
   const response=await fetcher('/api/grading-records',{cache:'no-store',credentials:'same-origin',redirect:'error',signal:controller.signal});
   if(current!==generation)return;
   if(!response.ok)throw Error(response.status===403?'Sign in to read your private grading records.':'The private grading workbook is unavailable. Refresh after its connection is restored.');
   const data=validateRecords(await response.json());if(current===generation)onState({status:'loaded',data});
  }catch(error){if(current===generation&&error.name!=='AbortError')onState({status:'error',message:error.message});}
 };
}
function node(root,tag,text){const n=root.ownerDocument.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;}
function line(root,text,cls){const p=node(root,'p',text);if(cls)p.className=cls;root.append(p);return p;}
function link(root,label,url){if(!safeLink(url)||!String(url).startsWith('https://'))return;const a=node(root,'a',label);a.href=url;a.target='_blank';a.rel='noopener noreferrer';a.className='source-link';root.append(a);}
function time(value){const date=new Date(value);return Number.isNaN(date.valueOf())?'time unavailable':date.toLocaleString();}
function label(value){return String(value||'Needs review').replaceAll('_',' ');}
export function score(row,key){
 if(row.hold_reason||['identity_unverified','superseded'].includes(row.status)||!Number.isFinite(row[key]))return '—';
 return `${Number(row[key].toFixed(2))} / 10`;
}
export function renderRecords(root,state,{classKey,sectionId='matched'}){
 root.replaceChildren();
 if(state.status!=='loaded'){line(root,state.message||(state.status==='loading'?'Loading private grading records…':'Refresh to load the private grading workbook.'));return;}
 const rows=filterRecords(state.data,classKey,sectionId),included=rows.filter(r=>r.included_in_counts);
 line(root,`${new Set(included.map(r=>r.section_id+'\0'+(r.count_key||r.student_id))).size} students with matched submissions · ${included.length} records · checked ${time(state.data.checked_at)}`,'hint');
 if(sectionId==='matched'){
  const unmatched=state.data.submissions.filter(r=>r.class_key===classKey&&!r.section_id).length;
  if(unmatched)line(root,`${unmatched} ${unmatched===1?'record needs':'records need'} a section match. Choose Unmatched in Section to review them.`,'hint');
 }
 if(!rows.length){line(root,'No submission records for this selection. This does not establish missing work.');return;}
 const table=node(root,'table');table.className='work-table';table.setAttribute('aria-label','Submissions by class and section');const head=node(root,'thead'),hr=node(root,'tr');
 for(const text of ['Student / section','Assignment','Content / notes','Revision','Status / work'])hr.append(node(root,'th',text));head.append(hr);table.append(head);const body=node(root,'tbody');
 for(const row of rows){
  const tr=node(root,'tr'),student=node(root,'td');student.append(node(root,'strong',row.display_name||'Identity needs matching'));line(student,row.section_name||'Unmatched','hint');tr.append(student);
  for(const text of [row.assignment_title,score(row,'content_score'),score(row,'revision_score')])tr.append(node(root,'td',text));
  const cell=node(root,'td');line(cell,label(row.status));const details=node(root,'details');details.append(node(root,'summary','View work & feedback'));
  if(row.hold_reason)line(details,row.hold_reason);if(!row.included_in_counts)line(details,'Excluded from class counts until identity and current submission are resolved.','hint');
  if(row.feedback){line(details,row.feedback,'student-feedback-text');
   const print=node(details,'button','Print feedback');print.type='button';print.className='print-feedback-button';
   print.addEventListener('click',()=>printFeedback(row));details.append(print);}
  link(details,'Open submitted work ↗',row.source_url);
  if(!row.hold_reason&&!['identity_unverified','superseded'].includes(row.status)){
   if(row.content_score_exact&&Number.isFinite(row.content_score))line(details,`Content calculation: ${row.content_score_exact} points. Score display rounded to two decimals.`,'hint');
   if(row.revision_score_exact&&Number.isFinite(row.revision_score))line(details,`Revision calculation: ${row.revision_score_exact} points. Score display rounded to two decimals.`,'hint');
  }
  if(row.notes_required)line(details,`NOTES attempts: ${row.notes_completed} / ${row.notes_required}`);
  if(row.drafts_required)line(details,`DRAFT attempts: ${row.drafts_completed} / ${row.drafts_required}`);
  for(const answer of row.answers){const item=node(root,'div');item.className='answer-evidence';item.append(node(root,'h4',`${answer.question_id} · ${answer.stage}`));if(answer.prompt)line(item,answer.prompt);const quote=node(root,'blockquote',answer.response||'No written response recorded.');item.append(quote);if(answer.conceptual_level)line(item,'Conceptual understanding: '+answer.conceptual_level);if(answer.explanation_level)line(item,'Explaining concepts: '+answer.explanation_level);if(answer.meaningful_complete)line(item,'Completion: '+answer.meaningful_complete);if(answer.evidence_note)line(item,answer.evidence_note,'hint');link(item,'Answer source ↗',answer.source_url||answer['Source URL']);details.append(item);}
  line(details,'Infinite Campus: '+label(row.ic_posting_status||'posting not verified'));line(details,'Assessment checked: '+time(row.checked_at),'hint');cell.append(details);tr.append(cell);body.append(tr);
 }
 table.append(body);const wrap=node(root,'div');wrap.className='work-table-wrap';wrap.append(table);root.append(wrap);
}
// A print-only sheet of the student-facing report: scores, the report text and
// a written-reflection space. Teacher-only evidence notes never print.
export function printFeedback(row){
 const doc=document;
 doc.querySelector('.print-sheet')?.remove();
 const sheet=node(doc.body,'div');sheet.className='print-sheet';
 sheet.append(node(doc.body,'h1',(row.display_name||'Student')+' · '+row.assignment_title));
 const meta=node(doc.body,'p',(row.section_name||'')+' · Feedback prepared '+time(row.checked_at));meta.className='hint';sheet.append(meta);
 sheet.append(node(doc.body,'p','Content / notes: '+score(row,'content_score')+'    Revision: '+score(row,'revision_score')));
 const report=node(doc.body,'div');report.className='student-feedback-text';report.textContent=row.feedback||'';sheet.append(report);
 sheet.append(node(doc.body,'h2','My reflection'));
 sheet.append(node(doc.body,'p','What is one thing this feedback tells you to do next, in your own words?'));
 for(let i=0;i<6;i++){const ruled=node(doc.body,'div');ruled.className='reflection-line';sheet.append(ruled);}
 doc.body.classList.add('print-feedback-active');
 const done=()=>{doc.body.classList.remove('print-feedback-active');sheet.remove();window.removeEventListener('afterprint',done);};
 window.addEventListener('afterprint',done);
 window.print();
 setTimeout(done,60000);
}

export function renderSummaries(root,state){
 root.replaceChildren();if(state.status!=='loaded'){line(root,state.message||(state.status==='loading'?'Loading class summaries…':'Refresh to load class summaries.'));return;}
 const summaries=state.data.summaries;
 if(!summaries.length){line(root,'No reviewed class summaries have been recorded.');return;}
 // Exactly one card per explicit class/section. No score averages or repeated panels.
 for(const summary of [...summaries].sort((a,b)=>a.class_key.localeCompare(b.class_key)||a.section_name.localeCompare(b.section_name))){
  const card=node(root,'section');card.className='surface';card.append(node(root,'h2',`${courses[summary.class_key]} · ${summary.section_name}`));line(card,summary.summary,'class-summary-text');line(card,summary.coverage||'Coverage has not been specified.','hint');line(card,`${summary.submitted_students} matched students · checked ${time(summary.checked_at)}`,'hint');root.append(card);
 }
}
