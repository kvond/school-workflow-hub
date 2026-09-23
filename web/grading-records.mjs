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
 for(const row of data.submissions)if(inClass(row,classKey)){if(row.section_id)sections.set(row.section_id,row.section_name||row.section_id);else unmatched++;}
 const options=[...sections].sort((a,b)=>a[1].localeCompare(b[1]));if(unmatched)options.push(['',`Unmatched (${unmatched})`]);return options;
}
// Lesson names for a class come from the workbook's own assignment registry
// (the same stable identity the table, posting and saving use — never a date).
// Any assignment_key seen on a submission but absent from the registry is still
// offered so recovered/older work is never hidden.
export function lessonOptions(data,classKey){
 const named=new Map();
 for(const a of data.assignments||[])if(a.class_key===classKey&&a.assignment_key)named.set(a.assignment_key,a.assignment_title||a.assignment_key);
 for(const row of data.submissions)if(row.class_key===classKey&&row.assignment_key&&!named.has(row.assignment_key))named.set(row.assignment_key,row.assignment_title||row.assignment_key);
 return [...named].sort((a,b)=>a[1].localeCompare(b[1]));
}
function inClass(row,classKey){return row.class_key===classKey||(row.discovery_only&&!row.class_key&&row.discovery_scope_class===classKey);}
export function filterRecords(data,classKey,sectionId='matched',assignmentKey='all',stage='all'){
 return data.submissions.filter(row=>inClass(row,classKey)&&(assignmentKey==='all'||row.assignment_key===assignmentKey)&&(stage==='all'||row.assessment_stage===stage||(row.discovery_only&&!row.assessment_stage))&&(sectionId==='all'||(sectionId==='matched'?Boolean(row.section_id):row.section_id===sectionId))).sort((a,b)=>(a.section_name||'').localeCompare(b.section_name||'')||(a.display_name||'').localeCompare(b.display_name||'')||(a.assignment_title||'').localeCompare(b.assignment_title||''));
}
function sourceFileId(row){
 if(row.source_file_id)return row.source_file_id;
 try{const u=new URL(row.source_url);if(u.protocol!=='https:'||!['docs.google.com','drive.google.com'].includes(u.hostname))return '';return u.pathname.match(/\/d\/([A-Za-z0-9_-]+)(?:\/|$)/)?.[1]||u.searchParams.get('id')||'';}catch{return '';}
}
// This projection is for the table and its selectors only. Posting, downloads
// and printing keep consuming the untouched saved recordState.
export function recordsWithDiscovery(data,discovery){
 if(discovery?.status!=='loaded')return data;
 const files=discovery.data.files,attached=new Set();
 const submissions=data.submissions.map(row=>{
  const file=files.find(f=>f.file_id===sourceFileId(row)&&f.class_key===row.class_key&&f.assignment_key===row.assignment_key&&f.section_id===(row.section_id||'')&&(!f.student_id||!row.student_id||f.student_id===row.student_id));
  if(!file)return row;
  attached.add(file.file_id);return {...row,discovery:file};
 });
 for(const file of files)if(!attached.has(file.file_id))submissions.push({record_id:'discovery:'+file.file_id,class_key:file.class_key,discovery_scope_class:discovery.data.class_key,assignment_key:file.assignment_key,assignment_title:file.assignment_title||'Lesson needs matching',section_id:file.section_id,section_name:file.section_name,student_id:file.student_id,display_name:file.display_name||'Identity needs matching',source_file_id:file.file_id,source_url:file.source_url,assessment_stage:file.assessment_stage||'',status:file.discovery_status,content_score:null,revision_score:null,answers:[],included_in_counts:false,discovery_only:true,discovery:file});
 return {...data,submissions};
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
// 'Ada Fictional' -> ['Fictional','Ada']; 'Bo van Fictional' -> ['van Fictional','Bo'].
// First token is the given name and the rest is the surname, so hyphenated and
// multi-word surnames survive. A single token is treated as a surname, which is
// how it sorts. Names are never invented or expanded — an aliased record like
// 'Daniel P.' keeps its initial rather than gaining a surname it does not have.
export function splitName(displayName){
 const parts=String(displayName||'').split(/\s+/).filter(Boolean);
 if(!parts.length)return ['',''];
 if(parts.length===1)return [parts[0],''];
 return [parts.slice(1).join(' '),parts[0]];
}
// Surname first, to read against an Infinite Campus gradebook list.
export function surnameFirst(displayName){
 const [last,first]=splitName(displayName);
 return last&&first?last+', '+first:last||first||'';
}
export function score(row,key){
 if(row.hold_reason||['identity_unverified','superseded'].includes(row.status)||!Number.isFinite(row[key]))return '—';
 return `${Number(row[key].toFixed(2))} / 10`;
}
const CHECKPOINT={initial:'Notes checkpoint',final:'Drafts checkpoint'};
const DISCOVERY_LABEL={new_ungraded:'New work · not graded',changed_since_assessment:'Work changed since assessment · review needed',needs_review:'Discovery needs review',unchanged:'Source unchanged'};
const DISCOVERY_REVIEW={prior_submission_superseded:'Previously superseded work · history only; do not treat as new work.',competing_submission_versions:'Multiple submission versions need review; no current version has been selected.'};
export function renderRecords(root,state,{classKey,sectionId='matched',assignmentKey='all',stage='all',discovery={status:'empty'}}){
 root.replaceChildren();
 if(state.status!=='loaded'){line(root,state.message||(state.status==='loading'?'Loading private grading records…':'Refresh to load the private grading workbook.'));return;}
 const projected=recordsWithDiscovery(state.data,discovery);
 const rows=filterRecords(projected,classKey,sectionId,assignmentKey,stage),included=rows.filter(r=>r.included_in_counts);
 line(root,`${new Set(included.map(r=>r.section_id+'\0'+(r.count_key||r.student_id))).size} students with matched submissions · ${included.length} saved records · workbook read ${time(state.data.checked_at)}`,'hint');
 if(discovery.status==='loading')line(root,'Checking current submissions for new or changed work… Saved assessments are unchanged.','hint');
 else if(discovery.status==='error')line(root,'Discovery failed: '+discovery.message+' Saved assessments are unchanged.','error');
 else if(discovery.status==='loaded'){
  const d=discovery.data;
  line(root,`Submissions discovered ${time(d.discovered_at)} · ${d.files.length} files checked for this ${d.assignment_key==='all'?'class':'lesson'}. ${d.coverage.complete?'Discovery source read complete.':'Discovery coverage is incomplete; review the gaps.'} No work was graded.`,'hint');
  const unassigned=d.files.filter(f=>!f.assignment_key).length;
  if(unassigned)line(root,`${unassigned} ${unassigned===1?'file needs':'files need'} a lesson match. Choose All lessons and Unmatched or All records to review them.`,'hint');
  const unknownClass=d.files.filter(f=>!f.class_key).length;
  if(unknownClass)line(root,`${unknownClass} ${unknownClass===1?'file has':'files have'} no verified class match. They appear under Unmatched for review, not as confirmed work for this class.`,'hint');
  if(!d.coverage.complete){const details=node(root,'details');details.append(node(root,'summary','Discovery coverage details'));for(const [key,name]of [['form_complete','Form responses'],['roster_complete','Roster'],['catalog_complete','Lesson catalog']])if(d.coverage[key]===false)line(details,name+' could not be read completely.');for(const reason of d.coverage.errors||[])line(details,label(reason));line(details,'Incomplete coverage does not establish missing work.');root.append(details);}
 }else line(root,'Submission discovery has not run in this view. Refresh submissions checks for new and changed work.','hint');
 if(sectionId==='matched'){
  const unmatched=projected.submissions.filter(r=>inClass(r,classKey)&&!r.section_id).length;
  if(unmatched)line(root,`${unmatched} ${unmatched===1?'record needs':'records need'} a section match. Choose Unmatched in Section to review them.`,'hint');
 }
 if(!rows.length){line(root,'No submission records for this selection. This does not establish missing work.');return;}
 const table=node(root,'table');table.className='work-table';table.setAttribute('aria-label','Submissions by class and section');const head=node(root,'thead'),hr=node(root,'tr');
 for(const text of ['Student / section','Assignment','Content / notes','Revision','Status / work'])hr.append(node(root,'th',text));head.append(hr);table.append(head);const body=node(root,'tbody');
 for(const row of rows){
  const tr=node(root,'tr'),student=node(root,'td');student.append(node(root,'strong',surnameFirst(row.display_name)||'Identity needs matching'));
  line(student,(row.section_name||'Unmatched')+(row.student_id?(row.discovery_only?' · matched identity ':' · IC ')+row.student_id:(row.discovery_only?' · identity unverified':' · no IC number')),'hint');tr.append(student);
  if(row.discovery_only&&!row.class_key)line(student,'Class needs matching','hint');
  for(const text of [row.assignment_title,score(row,'content_score'),score(row,'revision_score')])tr.append(node(root,'td',text));
  const cell=node(root,'td');line(cell,row.discovery_only?'Not assessed':label(row.status));if(CHECKPOINT[row.assessment_stage])line(cell,CHECKPOINT[row.assessment_stage],'hint');
  if(row.discovery){line(cell,DISCOVERY_LABEL[row.discovery.discovery_status]);if(row.discovery.source_changed_since_assessment)line(cell,'Source version changed since assessment; this does not establish revised student writing.','hint');for(const reason of row.discovery.review_reasons)if(DISCOVERY_REVIEW[reason])line(cell,DISCOVERY_REVIEW[reason]);if(row.discovery_only&&!row.discovery.assessment_stage)line(cell,'Discovered work checkpoint needs review.','hint');if(!row.discovery_only&&row.discovery.discovery_status!=='unchanged')line(cell,'Scores shown are the saved assessment; they have not been updated for this discovery.','hint');}
  const details=node(root,'details');details.append(node(root,'summary','View work & feedback'));
  if(row.discovery){const f=row.discovery;for(const reason of f.review_reasons)line(details,label(reason));if(f.modified_at)line(details,'Source modified: '+time(f.modified_at),'hint');if(f.name)line(details,'Discovered file: '+f.name,'hint');}
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
  if(!row.discovery_only){line(details,'Infinite Campus: '+label(row.ic_posting_status||'posting not verified'));line(details,'Assessment checked: '+time(row.checked_at),'hint');}else line(details,'Discovery preview only. No assessment or intake record was saved.','hint');cell.append(details);tr.append(cell);body.append(tr);
 }
 table.append(body);const wrap=node(root,'div');wrap.className='work-table-wrap';wrap.append(table);root.append(wrap);
}
// One print-only sheet of a student-facing report: scores, the report text and
// a written-reflection space. Teacher-only evidence notes never print. Each
// sheet is a top-level sibling so the print CSS shows every sheet and starts
// each subsequent student on a new page. Returns the created sheet.
export function appendSheet(doc,row){
 const sheet=node(doc.body,'div');sheet.className='print-sheet';doc.body.append(sheet);
 sheet.append(node(doc.body,'h1',(row.display_name||'Student')+' · '+row.assignment_title));
 const meta=node(doc.body,'p',(row.section_name||'')+' · Feedback prepared '+time(row.checked_at));meta.className='hint';sheet.append(meta);
 sheet.append(node(doc.body,'p','Content / notes: '+score(row,'content_score')+'    Revision: '+score(row,'revision_score')));
 const report=node(doc.body,'div');report.className='student-feedback-text';report.textContent=row.feedback||'';sheet.append(report);
 sheet.append(node(doc.body,'h2','My reflection'));
 sheet.append(node(doc.body,'p','What is one thing this feedback tells you to do next, in your own words?'));
 for(let i=0;i<6;i++){const ruled=node(doc.body,'div');ruled.className='reflection-line';sheet.append(ruled);}
 return sheet;
}
function printSheets(){
 const doc=document;
 doc.body.classList.add('print-feedback-active');
 const done=()=>{doc.body.classList.remove('print-feedback-active');doc.querySelectorAll('.print-sheet').forEach(s=>s.remove());window.removeEventListener('afterprint',done);};
 window.addEventListener('afterprint',done);
 window.print();
 setTimeout(done,60000);
}
export function printFeedback(row){
 document.querySelectorAll('.print-sheet').forEach(s=>s.remove());
 appendSheet(document,row);
 printSheets();
}
// A multi-student packet: one report per student, each starting on a new page.
export function printFeedbackPacket(rows){
 const withReport=(rows||[]).filter(r=>r.feedback);
 if(!withReport.length)return 0;
 document.querySelectorAll('.print-sheet').forEach(s=>s.remove());
 for(const row of withReport)appendSheet(document,row);
 printSheets();
 return withReport.length;
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
