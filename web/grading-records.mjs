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
// Fixed, non-sensitive messages only: never render provider bodies or exception text.
const recoveryKinds={
 sign_in:{title:'Dashboard sign-in required',message:'The dashboard needs your signed-in school session.',kind:'connect',next:'Sign in to this dashboard, then refresh.',retryable:false},
 connection:{title:'Grading workbook connection missing',message:'The dashboard has no valid private grading-workbook connection.',kind:'connect',next:'Restore the existing workbook connection before refreshing.',retryable:false},
 access:{title:'Grading workbook access denied',message:'The dashboard reader was denied access to the configured workbook.',kind:'connect',next:'Restore the existing reader’s approved workbook access, then refresh.',retryable:false},
 not_found:{title:'Grading workbook not found',message:'The configured workbook could not be found or is not visible to the dashboard reader.',kind:'connect',next:'Verify the existing workbook connection and reader access before refreshing.',retryable:false},
 identity:{title:'Dashboard reader authentication failed',message:'The server could not authenticate its workbook reader. This is separate from your dashboard sign-in.',kind:'connect',next:'Restore the existing server reader identity before refreshing.',retryable:false},
 layout:{title:'Grading workbook layout needs review',message:'The existing workbook does not match the required tabs, headers, or bounded grid layout.',kind:'review',next:'Review the existing workbook layout; preserve its records, then refresh.',retryable:false},
 records:{title:'Grading records need review',message:'The workbook contains unresolved or invalid grading records. The dashboard has not treated them as missing work.',kind:'review',next:'Resolve the reported record issue in the existing private workbook, then refresh.',retryable:false},
 changed:{title:'Grading workbook changed during the read',message:'The workbook structure changed while the dashboard was reading it.',kind:'recheck',next:'Finish the current workbook edit, then refresh for a new read.',retryable:true},
 limit:{title:'Grading workbook read limit reached',message:'The workbook exceeded a bounded read limit; this is not an empty-submission result.',kind:'review',next:'Review the existing workbook’s read bounds without deleting student history, then refresh.',retryable:false},
 temporary:{title:'Grading workbook temporarily unavailable',message:'The workbook service is busy or could not complete this read.',kind:'recheck',next:'Retry Refresh after the temporary service issue clears.',retryable:true},
 invalid:{title:'Grading response could not be verified',message:'The dashboard received an unreadable or invalid grading response.',kind:'review',next:'Check the workbook reader response before relying on these records; then refresh.',retryable:false},
 request:{title:'Dashboard request could not be completed',message:'The request did not return a verified grading response. The cause has not been confirmed.',kind:'recheck',next:'Check the dashboard connection and signed-in session, then refresh.',retryable:true},
 unknown:{title:'Grading workbook unavailable',message:'The dashboard could not read verified grading records. The cause has not been confirmed.',kind:'recheck',next:'Retry Refresh; a repeated failure needs the existing service connection checked.',retryable:true},
};
const recoveryCodes={
 sign_in_required:'sign_in',
 grading_records_not_connected:'connection',
 grading_records_access_denied:'access',
 grading_records_source_not_found:'not_found',
 grading_records_authentication_required:'identity',
 grading_records_invalid_credential:'identity',
 grading_workbook_layout_required:'layout',
 grading_workbook_headers_required:'layout',
 grading_workbook_changed:'changed',
 grading_workbook_limit:'limit',
 grading_records_response_limit:'limit',
 grading_records_range_limit:'limit',
 grading_records_source_rate_limited:'temporary',
 grading_records_source_transient_failure:'temporary',
 grading_records_source_unavailable:'temporary',
 grading_records_invalid_response:'invalid',
 grading_record_conflict:'records',
 grading_identity_conflict:'records',
 grading_assignment_unresolved:'records',
 grading_answer_unresolved:'records',
 grading_score_invalid:'records',
 grading_class_invalid:'records',
 grading_summary_invalid:'records',
 grading_records_unavailable:'unknown',
};
export function recordsFailure(status,payload){
 const reported=payload&&!Array.isArray(payload)&&typeof payload.error==='string'?payload.error:'';
 const code=status===401||status===403?'sign_in_required':Object.hasOwn(recoveryCodes,reported)?reported:'grading_records_unavailable';
 const category=status===0?'request':recoveryCodes[code];
 const recovery=recoveryKinds[category];
 return {status:'error',code,category,title:recovery.title,message:recovery.message,
  next_action:{kind:recovery.kind,label:recovery.next},retryable:recovery.retryable};
}
export function createRecordsLoader({fetcher=fetch,onState}){
 let generation=0,controller;
 return async()=>{
  const current=++generation;controller?.abort();controller=new AbortController();onState({status:'loading'});
  try{
   const response=await fetcher('/api/grading-records',{cache:'no-store',credentials:'same-origin',redirect:'error',signal:controller.signal});
   if(current!==generation)return;
   if(!response.ok){
    let payload;try{payload=await response.json();}catch{} // IAP/proxies may return non-JSON.
    if(current===generation)onState(recordsFailure(response.status,payload));
    return;
   }
   let data;
   try{data=validateRecords(await response.json());}
   catch{if(current===generation)onState(recordsFailure(503,{error:'grading_records_invalid_response'}));return;}
   if(current===generation)onState({status:'loaded',data});
  }catch(error){if(current===generation&&error?.name!=='AbortError')onState(recordsFailure(0));}
 };
}
function renderReadState(root,state,waiting){
 if(state.status!=='error'){line(root,state.message||waiting);return;}
 const panel=node(root,'section');panel.className='surface';panel.setAttribute('role','alert');
 line(panel,state.title||'Grading records unavailable');line(panel,state.message||'No verified read is available.');
 if(state.next_action)line(panel,'Next action: '+state.next_action.label);
 if(state.code)line(panel,'Diagnostic: '+state.code,'hint');
 line(panel,'A failed read does not establish missing work. Refresh does not change grades.','hint');
 root.append(panel);
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
 if(state.status!=='loaded'){renderReadState(root,state,state.status==='loading'?'Loading private grading records…':'Refresh to load the private grading workbook.');return;}
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
  if(row.feedback)line(details,row.feedback,'student-feedback-text');link(details,'Open submitted work ↗',row.source_url);
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
export function renderSummaries(root,state){
 root.replaceChildren();if(state.status!=='loaded'){renderReadState(root,state,state.status==='loading'?'Loading class summaries…':'Refresh to load class summaries.');return;}
 const summaries=state.data.summaries;
 if(!summaries.length){line(root,'No reviewed class summaries have been recorded.');return;}
 // Exactly one card per explicit class/section. No score averages or repeated panels.
 for(const summary of [...summaries].sort((a,b)=>a.class_key.localeCompare(b.class_key)||a.section_name.localeCompare(b.section_name))){
  const card=node(root,'section');card.className='surface';card.append(node(root,'h2',`${courses[summary.class_key]} · ${summary.section_name}`));line(card,summary.summary,'class-summary-text');line(card,summary.coverage||'Coverage has not been specified.','hint');line(card,`${summary.submitted_students} matched students · checked ${time(summary.checked_at)}`,'hint');root.append(card);
 }
}
