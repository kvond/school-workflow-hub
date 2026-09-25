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
export function verifiedSectionOptions(data,classKey){
 const named=new Map();
 for(const section of data.sections||[])if(section.class_key===classKey&&section.section_id&&section.section_name)named.set(section.section_id,section.section_name);
 return [...named].sort((a,b)=>a[1].localeCompare(b[1]));
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
// Review candidates stay visible without being assigned to the selected period.
// Posting, printing and exports continue to use the strict filter above.
export function filterReviewRecords(data,classKey,sectionId,assignmentKey='all',stage='all'){
 return data.submissions.filter(row=>inClass(row,classKey)&&(!row.section_id||row.section_id===sectionId)&&
  (assignmentKey==='all'||!row.assignment_key||row.assignment_key===assignmentKey)&&
  (stage==='all'||row.assessment_stage===stage||!row.assessment_stage)).sort((a,b)=>(a.section_name||'').localeCompare(b.section_name||'')||(a.display_name||'').localeCompare(b.display_name||''));
}
function sourceFileId(row){
 if(row.source_file_id)return row.source_file_id;
 try{const u=new URL(row.source_url);if(u.protocol!=='https:'||!['docs.google.com','drive.google.com'].includes(u.hostname))return '';return u.pathname.match(/\/d\/([A-Za-z0-9_-]+)(?:\/|$)/)?.[1]||u.searchParams.get('id')||'';}catch{return '';}
}
// This projection is for the table and its selectors only. Posting, downloads
// and printing keep consuming the untouched saved recordState.
export function recordsWithDiscovery(data,discovery){
 if(!['loaded','loading','error'].includes(discovery?.status)||!discovery.data)return data;
 const stale=discovery.status!=='loaded';
 const files=discovery.data.files.map(file=>stale?{...file,discovery_status:'needs_review',review_reasons:[...file.review_reasons,discovery.status==='loading'?'discovery_refresh_pending':'discovery_refresh_failed']}:file),attached=new Set();
 const submissions=data.submissions.map(row=>{
  const file=files.find(f=>f.file_id===sourceFileId(row)&&f.class_key===row.class_key&&f.assignment_key===row.assignment_key&&f.section_id===(row.section_id||'')&&(!f.student_id||!row.student_id||f.student_id===row.student_id));
  if(!file)return row;
  attached.add(file.file_id);return {...row,discovery:file};
 });
 for(const file of files)if(!attached.has(file.file_id))submissions.push({record_id:'discovery:'+file.file_id,class_key:file.class_key,discovery_scope_class:discovery.data.class_key,assignment_key:file.assignment_key,assignment_title:file.assignment_title||'Lesson needs matching',section_id:file.section_id,section_name:file.section_name,student_id:file.student_id,display_name:file.display_name||'Identity needs matching',source_file_id:file.file_id,source_url:file.source_url,assessment_stage:file.assessment_stage||'',status:file.discovery_status,content_score:null,revision_score:null,answers:[],included_in_counts:false,discovery_only:true,discovery:file});
 const assignments=[...(data.assignments||[])],keys=new Set(assignments.map(a=>a.assignment_key));
 for(const assignment of discovery.data.assignments||[])if(assignment.class_key===discovery.data.class_key&&!keys.has(assignment.assignment_key)){
  assignments.push({...assignment,discovery_only:true});keys.add(assignment.assignment_key);
 }
 return {...data,assignments,submissions};
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
 return {status:'error',authExpired:category==='sign_in',code,category,title:recovery.title,message:recovery.message,
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
const DISCOVERY_REVIEW={prior_submission_superseded:'Previously superseded work · history only; do not treat as new work.',competing_submission_versions:'Multiple submission versions need review; no current version has been selected.',submission_assignment_source_unverified:'Listed only · no verified source link to the selected lesson; content was not read.',discovery_refresh_pending:'Earlier preview · checking the current source.',discovery_refresh_failed:'Earlier preview · the current source check failed.'};
export function renderRecords(root,state,{classKey,sectionId='matched',assignmentKey='all',stage='all',discovery={status:'empty'},includeUnresolved=false}){
 root.replaceChildren();
 if(state.status!=='loaded'){renderReadState(root,state,state.status==='loading'?'Loading private grading records…':'Refresh to load the private grading workbook.');return;}
 const projected=recordsWithDiscovery(state.data,discovery);
 const rows=(includeUnresolved?filterReviewRecords:filterRecords)(projected,classKey,sectionId,assignmentKey,stage);
 const unscoped=row=>assignmentKey!=='all'&&row.discovery_only&&row.discovery?.review_reasons.includes('submission_assignment_source_unverified');
 const primaryRows=rows.filter(row=>!unscoped(row)),unscopedRows=rows.filter(unscoped);
 const identity=row=>row.discovery?.learner_key||row.learner_key||row.student_id||row.count_key;
 const matched=primaryRows.filter(r=>r.section_id&&identity(r)&&(r.discovery_only||r.included_in_counts));
 const sources=[...new Map(primaryRows.filter(r=>r.discovery).map(r=>[r.discovery.file_id,r.discovery])).values()],saved=primaryRows.filter(r=>!r.discovery_only).length;
 const readable=sources.filter(f=>f.source_availability==='active'&&f.content_read===true).length;
 const historical=sources.filter(f=>['trashed','unavailable'].includes(f.source_availability)&&f.saved_record_ids?.length).length;
 const other=sources.length-readable-historical;
 const sourceCount=`${readable} ${discovery.status==='loaded'?'readable':'previously readable'} source files`+(historical?` · ${historical} ${discovery.status==='loaded'?'unavailable':'previously unavailable'} historical references`:'')+(other?` · ${other} other source references needing verification`:'');
 line(root,discovery.status==='loading'&&!discovery.data?`${saved} saved records · current submissions are being checked · workbook read ${time(state.data.checked_at)}`:`${new Set(matched.map(r=>r.section_id+'\0'+identity(r))).size} matched students in this selection · ${sourceCount} · ${saved} saved records · workbook read ${time(state.data.checked_at)}`,'hint');
 if(discovery.status==='loading')line(root,'Checking current submissions for new or changed work… '+(discovery.data?'The earlier preview stays visible until this check finishes. ':'')+'Saved assessments are unchanged.','hint');
 else if(discovery.status==='error')line(root,'Discovery failed: '+discovery.message+(discovery.data?' The earlier preview stays visible; it has not been reverified.':'')+' Saved assessments are unchanged.','error');
 else if(discovery.status==='loaded'){
  const d=discovery.data;
  line(root,`Submissions discovered ${time(d.discovered_at)} · ${d.files.length} files listed for review. ${d.coverage.complete?'Discovery source read complete.':'Discovery coverage is incomplete; review the gaps.'} No work was graded.`,'hint');
  const unassigned=d.files.filter(f=>!f.assignment_key).length;
  if(unassigned)line(root,`${unassigned} ${unassigned===1?'file needs':'files need'} a lesson match. ${includeUnresolved?'Unresolved work remains available for review without assigning it to this lesson.':'Choose All lessons and Unmatched or All records to review them.'}`,'hint');
  if(assignmentKey==='all'&&d.assignment_key!=='all')line(root,'Current-file preview covers one previously selected lesson; other lessons show saved records only. Choose a lesson name to check its current files.','hint');
  const unknownClass=d.files.filter(f=>!f.class_key).length;
  if(unknownClass)line(root,`${unknownClass} ${unknownClass===1?'file has':'files have'} no verified class match. ${includeUnresolved?'They remain visible as unresolved review items.':'They appear under Unmatched for review, not as confirmed work for this class.'}`,'hint');
  const forms=Array.isArray(d.coverage.form_results)?d.coverage.form_results:[];
  const contentReadKnown=Number.isSafeInteger(d.coverage.deck_contents_checked)&&d.coverage.deck_contents_checked>=0;
  if(!d.coverage.complete||forms.length||contentReadKnown){
   const details=node(root,'details');details.append(node(root,'summary','Discovery coverage details'));
   if(Number.isSafeInteger(d.coverage.files_checked)&&d.coverage.files_checked>=0)line(details,`Drive metadata: ${d.coverage.files_checked} files listed.`);
   if(contentReadKnown)line(details,`Deck content: ${d.coverage.deck_contents_checked} files read.`);
   if(Number.isSafeInteger(d.coverage.unscoped_deck_content_skipped)&&d.coverage.unscoped_deck_content_skipped>0)line(details,`${d.coverage.unscoped_deck_content_skipped} decks without an established source link to this lesson were listed only; their content was not read.`);
   if(d.coverage.content_scope==='selected_submission_sources')line(details,'Content reading was limited to this lesson’s Form responses, upload folders and saved sources.');
   for(const [key,name]of [['form_complete','Form responses'],['roster_complete','Roster'],['catalog_complete','Lesson catalog']])if(d.coverage[key]===false)line(details,name+' could not be read completely.');
   if(forms.length){
    const list=node(root,'ul');
    for(const form of forms){
     const assignment=(projected.assignments||[]).find(a=>a.class_key===d.class_key&&a.assignment_key===form.assignment_key);
     const item=node(root,'li');item.append(node(root,'strong',assignment?.assignment_title||'Assignment needs matching'));
     if(form.complete){
      const count=value=>Number.isInteger(value)&&value>=0?String(value):'unavailable';
      line(item,`Form read complete · ${count(form.responses_checked)} responses checked · ${count(form.files_referenced)} files referenced · ${count(form.pages)} response pages.`);
     }else line(item,'Form read incomplete · '+label(form.error||'Form unavailable')+'.');
     const published=form.is_published===true?'Published':form.is_published===false?'Unpublished':'Publication unknown';
     const accepting=form.is_accepting_responses===true?'accepting responses':form.is_accepting_responses===false?'responses closed':'response acceptance unknown';
     line(item,published+' · '+accepting+'.');
     if(form.is_published!==true||form.is_accepting_responses!==true)line(item,'Check this Form’s publication and response settings before sharing its link. Historical responses remain separate.','hint');
     if(form.next_action)line(item,form.next_action);
     link(item,'Open assignment Form ↗',form.form_url);list.append(item);
    }
   details.append(list);
   }
   for(const source of [...(d.coverage.source_results||[]),...(d.coverage.catalog_results||[]),...(d.coverage.catalog_issues||[])].filter(source=>source.complete===false||source.error)){
    const assignment=(projected.assignments||[]).find(a=>a.assignment_key===source.assignment_key);
    line(details,(assignment?assignment.assignment_title+': ':'')+(source.next_action||label(source.error)));
    if(source.source_url)link(details,'Open source needing review ↗',source.source_url);
   }
   for(const reason of d.coverage.errors||[])line(details,label(reason));
   if(!d.coverage.complete)line(details,'Incomplete coverage does not establish missing work.');
   root.append(details);
  }
 }else line(root,assignmentKey==='all'?'Choose a lesson name to check its current submissions. All lessons shows saved work and previews already read.':'Submission discovery has not run in this view. Refresh submissions checks for new and changed work.','hint');
 if(sectionId==='matched'){
  const unmatched=projected.submissions.filter(r=>inClass(r,classKey)&&!r.section_id).length;
  if(unmatched)line(root,`${unmatched} ${unmatched===1?'record needs':'records need'} a section match. Choose Unmatched in Section to review them.`,'hint');
 }
 if(!rows.length){line(root,discovery.status==='loading'?'Waiting for the current submission check. This does not establish missing work.':'No submission records for this selection. This does not establish missing work.');return;}
 for(const [group,isUnscoped]of [[primaryRows,false],[unscopedRows,true]]){
 if(!group.length)continue;
 const destination=isUnscoped?node(root,'details'):root;
 if(isUnscoped){destination.append(node(root,'summary',`Other files needing a lesson match (${group.length})`));line(destination,'These files have no verified source link to this lesson. Their content was not read; they are not counted as this lesson’s submissions.');root.append(destination);}
 const table=node(root,'table');table.className='work-table';table.setAttribute('aria-label',isUnscoped?'Files without a verified lesson source':'Submissions by class and section');const head=node(root,'thead'),hr=node(root,'tr');
 for(const text of ['Student / section','Assignment','Content / notes','Revision','Status / work'])hr.append(node(root,'th',text));head.append(hr);table.append(head);const body=node(root,'tbody');
 for(const row of group){
  const tr=node(root,'tr'),student=node(root,'td');
  const displayName=row.discovery_only&&!row.discovery?.display_name?'Identity needs matching':surnameFirst(row.display_name)||'Identity needs matching';
  student.append(node(root,'strong',displayName));
  if(row.discovery?.name)line(student,'File: '+row.discovery.name,'hint');
  line(student,(row.section_name||'Unmatched')+(row.student_id?(row.discovery_only?' · matched identity ':' · IC ')+row.student_id:(row.discovery_only?' · identity unverified':' · no IC number')),'hint');tr.append(student);
  if(row.discovery_only&&!row.class_key)line(student,'Class needs matching','hint');
  if(row.discovery?.source_availability==='trashed')line(student,'Source was in Trash at its last check; retained here for history and review.','hint');
  else if(row.discovery?.source_availability==='unavailable')line(student,'Source could not be read at its last check; its reference is retained for review.','hint');
  for(const text of [row.assignment_title,score(row,'content_score'),score(row,'revision_score')])tr.append(node(root,'td',text));
  const cell=node(root,'td');line(cell,row.discovery_only?'Not assessed':label(row.status));if(CHECKPOINT[row.assessment_stage])line(cell,CHECKPOINT[row.assessment_stage],'hint');
  if(row.discovery){line(cell,DISCOVERY_LABEL[row.discovery.discovery_status]);if(row.discovery.source_changed_since_assessment)line(cell,'Source version changed since assessment; this does not establish revised student writing.','hint');for(const reason of row.discovery.review_reasons)if(DISCOVERY_REVIEW[reason])line(cell,DISCOVERY_REVIEW[reason]);if(row.discovery_only&&!row.discovery.assessment_stage)line(cell,'Discovered work checkpoint needs review.','hint');if(!row.discovery_only&&row.discovery.discovery_status!=='unchanged')line(cell,'Scores shown are the saved assessment; they have not been updated for this discovery.','hint');}
  const details=node(root,'details');details.append(node(root,'summary','View work & feedback'));
  if(row.discovery){const f=row.discovery;for(const reason of f.review_reasons)line(details,label(reason));if(f.modified_at)line(details,'Source modified: '+time(f.modified_at),'hint');}
  if(row.assessment_stage_review_reason)line(cell,'Assessment checkpoint needs review.','hint');
  if(row.hold_reason)line(details,row.hold_reason);if(!row.included_in_counts)line(details,row.discovery_only?'Not included in saved assessment counts.':'Excluded from class counts until identity and current submission are resolved.','hint');
  if(row.feedback){line(details,row.feedback,'student-feedback-text');
   const print=node(details,'button','Print feedback');print.type='button';print.className='print-feedback-button';
   print.addEventListener('click',()=>printFeedback(row,state.data?.submissions||[]));details.append(print);}
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
 table.append(body);const wrap=node(root,'div');wrap.className='work-table-wrap';wrap.append(table);destination.append(wrap);
 }
}
// Printed student report (teacher decision, September 23, 2026). Page one: year
// graphs for this lesson's critical aspects, the lesson title, current
// understanding per aspect on one four-level scale, and first/revised answers
// side by side. Page two: the written sections from the saved report, one
// question, exact evidence, the hand-back reflection and the rubric key.
// Scores and teacher-only evidence notes never print. Each sheet is a
// top-level sibling so the print CSS shows every sheet on its own pages.
export const SCALE=['Beginning','Developing','Proficient','Extending'];
export const SCALE_VERSION='critical-aspect-4/v1';
export const RUBRIC_KEY=[['Beginning','Names an idea; the relationship is missing or inaccurate.'],['Developing','Connects some ideas; the mechanism is partly explained.'],['Proficient','Explains the relationship using a scientifically sound mechanism.'],['Extending','Uses the mechanism to explain a new case and its limits.']];
// Headings the saved report text uses. The report speaks to the student as
// "you" and quotes the student's own words; earlier first-person headings are
// still recognized so older reports print.
const WRITTEN=['Current understanding','How your thinking changed','One question to think about','Evidence of your understanding'];
const ALIASES={'how my thinking changed':'How your thinking changed','evidence for my conceptual understanding':'Evidence of your understanding'};
const QUESTION_NOTE='Think this through for yourself; reworking concept notes is for your understanding, not for submission or another grade.';
const HAND_BACK='Write this yourself, then hand this page back to me. This is the part I read.';
const EDGE=/^[\s.,:;\u00b7\u2014\u2013-]+|[\s.,:;\u00b7\u2014\u2013-]+$/g;
// Mirrors understanding_scale.aspect_key: exact-name joins only.
export function aspectKey(name){
 if(typeof name!=='string')return '';
 const t=name.trim().replace(/^\s*(?:your answer\s*[\u2014\u2013-]\s*)?(?:critical aspect\s*\d*\s*[\u00b7:.\u2014\u2013-]?\s*)?/i,'');
 return t.replace(/\s+/g,' ').replace(EDGE,'').toLowerCase();
}
function levelClass(level){return 'fb-level-'+String(level||'unavailable').toLowerCase().replace(/[^a-z]+/g,'-');}
// Splits the saved report text at the teacher's headings; text before the
// first heading is kept, and an old-style reflection block is dropped because
// the sheet prints its own.
export function reportSections(text){
 const sections={},preamble=[];let current=null;
 for(const raw of String(text||'').split(/\r?\n/)){
  const bare=raw.replace(/^[\s#*_]+|[\s*_:]+$/g,'');
  const hit=WRITTEN.find(h=>h.toLowerCase()===bare.toLowerCase())||ALIASES[bare.toLowerCase()];
  if(hit){current=hit;sections[hit]=[];continue;}
  if(/^(what i understand now|my reflection$|rubric key$)/i.test(bare)){current='';continue;}
  if(current)sections[current].push(raw);else if(current===null)preamble.push(raw);
 }
 const clean=list=>list.join('\n').trim();
 const out={};for(const [k,v] of Object.entries(sections))if(clean(v))out[k]=clean(v);
 return {found:Object.keys(sections).length>0,sections:out,preamble:clean(preamble)};
}
function answerLabel(a){return a.critical_aspect||String(a.prompt||'').split('\n')[0].slice(0,90)||('Question '+a.question_id);}
// One item per critical aspect (or per question when no aspect is named),
// preferring the revised answer.
export function understandingItems(row){
 const items=new Map();
 for(const a of row.answers||[]){
  if(a.stage==='BANK'||!a.conceptual_level)continue;
  const key=aspectKey(a.critical_aspect)||('q:'+a.question_id),prev=items.get(key);
  if(!prev||(a.stage==='DRAFT'&&prev.stage!=='DRAFT'))items.set(key,a);
 }
 return [...items.values()].map(a=>({label:answerLabel(a),level:a.conceptual_level}));
}
export function answerPairs(row){
 const first=new Map(),revised=new Map();
 for(const a of row.answers||[]){if(a.stage==='NOTES')first.set(a.question_id,a);else if(a.stage==='DRAFT')revised.set(a.question_id,a);}
 const pairs=[];
 for(const [id,d] of revised){const n=first.get(id);if(!n||!(String(d.response||'').trim()||String(n.response||'').trim()))continue;
  pairs.push({label:answerLabel(d.critical_aspect?d:n),first:n.response||'',firstLevel:n.conceptual_level,revised:d.response||'',revisedLevel:d.conceptual_level,slide:d.slide_number||n.slide_number||''});}
 return pairs;
}
function sameLearner(a,b){
 if(!a||!b||a.class_key!==b.class_key)return false;
 if(a.student_id&&b.student_id)return a.student_id===b.student_id;
 if(a.count_key&&b.count_key)return a.count_key===b.count_key;
 return !!a.learner_key&&a.learner_key===b.learner_key&&!!a.section_id&&a.section_id===b.section_id;
}
// Graph lines for this lesson's named aspects, including earlier aspects the
// student used again under the same name. Only four-level ratings plot;
// older three-level ratings are never placed on these lines.
export function aspectHistory(row,history){
 const printed=new Map();
 for(const a of row.answers||[]){const k=aspectKey(a.critical_aspect);if(a.stage!=='BANK'&&k&&!printed.has(k))printed.set(k,a.critical_aspect.replace(EDGE,''));}
 const points=new Map([...printed.keys()].map(k=>[k,[]])),seen=new Set();
 const reportDate=validDate(row.checked_at),year=reportDate?schoolYear(reportDate):null;
 for(const r of [row,...(history||[])]){
  if(!r||!r.record_id||seen.has(r.record_id)||['superseded','identity_unverified'].includes(r.status)||r.hold_reason||r.discovery_only||!sameLearner(r,row))continue;seen.add(r.record_id);
  const date=validDate(r.checked_at);if(!date||!year||Date.parse(date)<year[0]||date>reportDate)continue;
  for(const a of r.answers||[]){const k=aspectKey(a.critical_aspect);
   // "What if" is a task label, not a shared concept across unrelated lessons.
   if(k==='what if'&&r!==row&&(!r.assignment_key||r.assignment_key!==row.assignment_key))continue;
   if(points.has(k)&&['NOTES','DRAFT'].includes(a.stage)&&a.rubric_version===SCALE_VERSION&&(SCALE.includes(a.conceptual_level)||a.conceptual_level==='Evidence unavailable'))points.get(k).push({date,level:a.conceptual_level,revised:a.stage==='DRAFT'});}
 }
 return [...printed].map(([key,label])=>({key,label,points:points.get(key).sort((x,y)=>x.date.localeCompare(y.date))}));
}
function validDate(value){const date=String(value||'').slice(0,10),time=Date.parse(date);return /^\d{4}-\d{2}-\d{2}$/.test(date)&&Number.isFinite(time)&&new Date(time).toISOString().slice(0,10)===date?date:'';}
function schoolYear(date){const [y,m]=date.split('-').map(Number);const start=m>=8?y:y-1;return [Date.parse(start+'-08-01'),Date.parse((start+1)+'-07-31')];}
function drawGraph(doc,host,item,today){
 const card=node(host,'div');card.className='fb-graph';card.append(node(host,'h4',item.label));host.append(card);
 if(!item.points.some(p=>SCALE.includes(p.level))){const empty=node(host,'div');empty.className='fb-graph-empty';empty.append(node(host,'strong','No dated evidence'),node(host,'span','No points plotted'));card.append(empty);line(card,'School year · dated evidence','fb-graph-caption');return;}
 if(typeof doc.createElementNS!=='function'){for(const p of item.points)line(card,p.date+' · '+p.level+(p.revised?' (revised answer)':' (first answer)'));return;}
 const NS='http://www.w3.org/2000/svg',el=(tag,attrs,text)=>{const n=doc.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));if(text!==undefined)n.textContent=text;return n;};
 const svg=el('svg',{viewBox:'0 0 330 112',role:'img','aria-label':item.label+': understanding by date across the school year'});
 const left=74,right=320,y=level=>88-SCALE.indexOf(level)*22;
 [...SCALE].reverse().forEach(level=>{svg.append(el('line',{x1:left,x2:right,y1:y(level),y2:y(level),stroke:'#D5DEE7','stroke-width':1}),el('text',{x:left-6,y:y(level)+3,'text-anchor':'end','font-size':8.5,fill:'#23384A'},level));});
 const [lo,hi]=schoolYear(item.points[item.points.length-1].date||today),x=d=>left+Math.min(1,Math.max(0,(Date.parse(d)-lo)/(hi-lo)))*(right-left);
 const revised=item.points.filter(p=>p.revised);
 for(let i=1;i<revised.length;i++)if(SCALE.includes(revised[i-1].level)&&SCALE.includes(revised[i].level))svg.append(el('line',{x1:x(revised[i-1].date),y1:y(revised[i-1].level),x2:x(revised[i].date),y2:y(revised[i].level),stroke:'#23384A','stroke-width':1.5}));
 for(const p of item.points){if(!SCALE.includes(p.level))continue;const dot=el('circle',{cx:x(p.date),cy:y(p.level),r:4,fill:p.revised?'#23384A':'#FFFFFF',stroke:'#23384A','stroke-width':1.5});dot.append(el('title',{},p.date+' · '+p.level+(p.revised?' · revised answer':' · first answer')));svg.append(dot);}
 svg.append(el('text',{x:(left+right)/2,y:108,'text-anchor':'middle','font-size':8,fill:'#5B6B7A'},'School year · dated evidence'));
 card.append(svg);
}
function block(doc,parent,heading,cls){const s=node(doc.body,'section');s.className='fb-section'+(cls?' '+cls:'');s.append(node(doc.body,'h2',heading));parent.append(s);return s;}
function text(doc,parent,value,cls){const p=node(doc.body,'p',value);p.className='fb-text'+(cls?' '+cls:'');parent.append(p);return p;}
export function appendSheet(doc,row,history=[]){
 const sheet=node(doc.body,'div');sheet.className='print-sheet';doc.body.append(sheet);
 const written=reportSections(row.feedback),items=understandingItems(row),aspects=aspectHistory(row,history),pairs=answerPairs(row);
 const fourLevel=(row.answers||[]).some(a=>a.rubric_version===SCALE_VERSION);
 const one=node(doc.body,'div');one.className='fb-page';sheet.append(one);
 if(aspects.length){
  const g=node(doc.body,'section');g.className='fb-section fb-year';g.append(node(doc.body,'h3','Your understanding through the year'));
  const grid=node(doc.body,'div');grid.className='fb-graphs';for(const item of aspects)drawGraph(doc,grid,item,String(row.checked_at||'').slice(0,10));g.append(grid);
  text(doc,g,aspects.some(a=>a.points.length>1)?'Each graph is one critical aspect. Filled dots are revised answers; open dots are first answers.':'Each graph is one critical aspect. One dated answer is a starting point, not a trend.','fb-note');
  one.append(g);
 }
 one.append(node(doc.body,'h1',row.assignment_title||'Lesson'));
 const prepared=new Date(row.checked_at);text(doc,one,[row.display_name||'Student',row.section_name,Number.isNaN(prepared.valueOf())?'':'Feedback prepared '+prepared.toLocaleDateString(undefined,{dateStyle:'long'})].filter(Boolean).join(' · '),'fb-subtitle');
 const current=block(doc,one,'Current understanding');
 if(items.length){const cards=node(doc.body,'div');cards.className='fb-cards';
  for(const item of items){const card=node(doc.body,'div');card.className='fb-card '+levelClass(item.level);card.append(node(doc.body,'h4',item.label),node(doc.body,'strong',item.level||'Evidence unavailable'));cards.append(card);}
  current.append(cards);}
 if(written.sections['Current understanding'])text(doc,current,written.sections['Current understanding']);
 if(!written.found&&row.feedback)text(doc,current,row.feedback,'student-feedback-text');
 const compare=block(doc,one,'Your earlier and current explanations');
 if(pairs.length){const grid=node(doc.body,'div');grid.className='fb-compare';
  const firstLabel=node(doc.body,'div','Your first answer'),revisedLabel=node(doc.body,'div','Your revised answer');firstLabel.className='fb-col-label fb-first';revisedLabel.className='fb-col-label fb-revised';grid.append(firstLabel,revisedLabel);
  for(const pair of pairs){
   const a=node(doc.body,'div');a.className='fb-card '+levelClass(pair.firstLevel);a.append(node(doc.body,'h4',pair.label));text(doc,a,pair.first.trim()?'“'+pair.first.trim()+'”':'No first answer was recorded.');
   const b=node(doc.body,'div');b.className='fb-card '+levelClass(pair.revisedLevel);b.append(node(doc.body,'h4',pair.label+(pair.slide?' · slide '+pair.slide:'')));text(doc,b,pair.revised.trim()?'“'+pair.revised.trim()+'”':'No revised answer was recorded.');
   grid.append(a,b);}
  compare.append(grid);
 }else text(doc,compare,'Your first and revised answers are not both available for this lesson.','fb-note');
 const two=node(doc.body,'div');two.className='fb-page fb-page-two';sheet.append(two);
 if(written.sections['How your thinking changed'])text(doc,block(doc,two,'How your thinking changed'),written.sections['How your thinking changed']);
 if(written.sections['One question to think about']){const q=block(doc,two,'One question to think about','fb-question');
  text(doc,q,written.sections['One question to think about'].split('\n').filter(l=>!/not for submission/i.test(l)).join('\n').trim());text(doc,q,QUESTION_NOTE,'fb-note');}
 if(written.sections['Evidence of your understanding'])text(doc,block(doc,two,'Evidence of your understanding'),written.sections['Evidence of your understanding']);
 const reflect=block(doc,two,"What I understand now that I didn't before");text(doc,reflect,HAND_BACK,'fb-handback');
 for(let i=0;i<4;i++){const ruled=node(doc.body,'div');ruled.className='reflection-line';reflect.append(ruled);}
 if(fourLevel){const key=block(doc,two,'Rubric key','fb-key');
  for(const [level,description] of RUBRIC_KEY){const r=node(doc.body,'div');r.className='fb-key-row';const badge=node(doc.body,'span',level);badge.className='fb-badge '+levelClass(level);r.append(badge,node(doc.body,'span',description));key.append(r);}}
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
// history: every loaded record, so each student's year graphs can use their
// earlier lessons. It is read only for the same learner and class.
export function printFeedback(row,history=[]){
 document.querySelectorAll('.print-sheet').forEach(s=>s.remove());
 appendSheet(document,row,history);
 printSheets();
}
// A multi-student packet: one report per student, each starting on a new page.
export function printFeedbackPacket(rows,history=[]){
 const withReport=(rows||[]).filter(r=>r.feedback);
 if(!withReport.length)return 0;
 document.querySelectorAll('.print-sheet').forEach(s=>s.remove());
 for(const row of withReport)appendSheet(document,row,history);
 printSheets();
 return withReport.length;
}

// Page 3: one link per lesson and class section with saved, matched work. Each
// opens that lesson's class growth summary on its own page (September 23,
// 2026). Older section-only summaries remain listed below the links.
export function summaryLinks(data){
 const groups=new Map();
 for(const r of data?.submissions||[]){
  if(!r.included_in_counts||r.discovery_only||!r.section_id||!r.assignment_key)continue;
  const key=[r.class_key,r.section_id,r.assignment_key].join('\u0000');
  const g=groups.get(key)||{class_key:r.class_key,section_id:r.section_id,section_name:r.section_name,assignment_key:r.assignment_key,assignment_title:r.assignment_title,students:new Set()};
  g.students.add(r.count_key||r.record_id);groups.set(key,g);
 }
 return [...groups.values()].map(g=>({...g,students:g.students.size,
  saved:(data.summaries||[]).find(s=>s.class_key===g.class_key&&s.section_id===g.section_id&&s.assignment_key===g.assignment_key)||null,
  href:'class-summary.html?'+new URLSearchParams({class:g.class_key,section:g.section_id,assignment:g.assignment_key})}))
  .sort((a,b)=>a.class_key.localeCompare(b.class_key)||String(a.section_name).localeCompare(String(b.section_name))||String(a.assignment_title).localeCompare(String(b.assignment_title)));
}
export function renderSummaries(root,state){
 root.replaceChildren();if(state.status!=='loaded'){renderReadState(root,state,state.status==='loading'?'Loading class summaries…':'Refresh to load class summaries.');return;}
 const links=summaryLinks(state.data);
 const list=node(root,'section');list.className='surface';list.append(node(root,'h2','Class growth summaries'));
 if(!links.length)line(list,'No lesson has saved, matched work yet.');
 for(const item of links){const entry=node(root,'div');entry.className='summary-link';
  entry.append(node(root,'strong',`${courses[item.class_key]||item.class_key} · ${item.section_name||item.section_id} · ${item.assignment_title||item.assignment_key}`));
  line(entry,`${item.students} ${item.students===1?'student':'students'} with saved work · `+(item.saved?'written sections saved '+time(item.saved.checked_at):'written sections not yet written'),'hint');
  const a=node(root,'a','Open class summary ↗');a.href=item.href;a.target='_blank';a.rel='noopener';a.className='source-link';entry.append(a);list.append(entry);}
 root.append(list);
 // Section-only summaries saved before lesson summaries existed.
 for(const summary of [...(state.data.summaries||[])].filter(s=>!s.assignment_key).sort((a,b)=>a.class_key.localeCompare(b.class_key)||a.section_name.localeCompare(b.section_name))){
  const card=node(root,'section');card.className='surface';card.append(node(root,'h2',`${courses[summary.class_key]} · ${summary.section_name} (earlier section summary)`));line(card,summary.summary,'class-summary-text');line(card,summary.coverage||'Coverage has not been specified.','hint');line(card,`${summary.submitted_students} matched students · checked ${time(summary.checked_at)}`,'hint');root.append(card);
 }
}
