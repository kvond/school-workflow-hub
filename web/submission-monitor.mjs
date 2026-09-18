// Submission monitoring panel: durable cloud observations, never a hidden scan.
// A status refresh only reads; only the explicit Scan button persists a scan.

export function validateStatus(data){
 if(data?.schema_version!=='hub-submission-monitor-status/v1'||data.external_writes!==0||data.run_enabled!==false)throw Error('Monitoring status could not be verified.');
 if(data.status==='no_complete_scan_recorded')return data;
 if(!data.counts||!Array.isArray(data.files)||!Array.isArray(data.needs_review)||!Array.isArray(data.events_last_scan))throw Error('Monitoring status could not be verified.');
 return data;
}

// One specific recovery action per failure; never a generic shrug.
const FAILURES={
 submission_folder_access_required:'The cloud service cannot read this turn-in folder. Share the submissions folder tree with the hub runtime reader as Viewer, then Scan again.',
 submission_lesson_mapping_required:'This lesson has no specific turn-in folder mapping. Add its TurnIn Map row in the Agenda, then Scan again.',
 submission_scan_incomplete:'The folder read hit a bound before completing. No baseline was replaced; Scan again once the folder is reachable.',
 submission_agenda_changed:'The Agenda changed during the scan. Load the lesson again, then Scan.',
 submission_folder_inactive:'The mapped folder is trashed or not a folder. Repair its Agenda TurnIn link, then Scan again.',
 submission_shortcut_needs_review:'The folder contains a shortcut, which the scan never follows. Replace it with the real file or folder, then Scan again.',
 monitor_scan_in_progress:'Another scan of this assignment is still running. Wait for it, then Refresh status.',
 monitor_scan_conflict:'Another worker updated this stream first. Refresh status, then Scan again.',
 monitor_state_unavailable:'The private state store is unreachable. Try again in a few minutes.',
 monitor_scan_invalid:'The scan result failed its own validation and was not saved. Refresh status and Scan again.',
 sign_in_required:'Your dashboard sign-in expired. Reload the page and sign in again.',
 invalid_monitor_request:'The request was not valid for this lesson. Reload the page and try again.'};
export function describeFailure(code){return FAILURES[code]||'The scan could not run. Nothing was recorded as missing. Refresh status and try again.';}

const STAGES={detected:'Detected · identity unmatched',awaiting_assessment:'Awaiting assessment',assessed:'Assessed'};
const TIMINGS={baseline_start:'first observed at baseline',not_due_yet:'before due',
 content_predates_due_turnin_unverified:'content predates due · turn-in time unverified',
 modified_after_due_needs_review:'modified after due · needs review',due_date_unverified:'due date unverified'};
const EVENTS={first_observed:'First observed',appeared:'New file appeared',modified:'Document changed',
 inaccessible:'No longer readable (access review, not withdrawal)',reappeared:'Readable again',
 matched:'Student match verified',due_date_changed:'Due date changed',possible_replacement:'Possible replacement'};

function node(root,tag,text,cls){const n=root.ownerDocument.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.setAttribute('class',cls);return n;}
const when=value=>value?new Date(value).toLocaleString():'never';

export function renderStatus(root,data){
 root.replaceChildren();
 const auto=data.automation||{};
 root.append(node(root,'p',(auto.active?'Automatic monitoring is active ('+auto.cadence+').':'Automatic monitoring is not active; scans run when you press Scan.'),'hint'));
 const attempt=data.last_attempt;
 const attemptLine=attempt?('Last scan attempt '+when(attempt.last_attempt_at)+' · '+(attempt.outcome==='complete'?'complete':'failed')+' · last complete scan '+when(attempt.last_success_at)):'No scan attempts recorded for this assignment.';
 root.append(node(root,'p',attemptLine));
 if(attempt&&attempt.outcome==='failed')root.append(node(root,'p',describeFailure(attempt.reason),'error'));
 if(data.status==='no_complete_scan_recorded'){root.append(node(root,'p','No complete scan is recorded yet. Scan submissions to establish the first-observed baseline; existing files will be labeled first observed, never late.','hint'));return;}
 root.append(node(root,'p','Assignment '+data.stream.assignment_label+' · scanned '+when(data.observed_at)+' · scan '+data.scan_count+' · baseline since '+when(data.baseline_established_at)+(data.unchanged_rerun?' · unchanged since the last scan':'')));
 root.append(node(root,'p','Coverage: complete visible-file scan, '+data.coverage.folders_checked+' folders. Student matches come from the reviewed grading workbook ('+when(data.roster.checked_at)+'); the live IC roster reader is not integrated, so unmatched work stays listed for review rather than being called missing.','hint'));
 const c=data.counts;
 root.append(node(root,'p',`${c.present} present · ${c.awaiting_assessment} awaiting assessment · ${c.unmatched} unmatched · ${c.needs_review} need review · ${c.inaccessible} inaccessible`));
 if(data.events_last_scan.length){
  const h=node(root,'h4','Changes found by the last scan');root.append(h);
  const list=node(root,'ul');
  for(const event of data.events_last_scan.slice(0,40))list.append(node(root,'li',(EVENTS[event.kind]||event.kind)+(event.file_id&&event.file_id!=='-'?' · file '+event.file_id.slice(0,8)+'…':'')));
  root.append(list);
 }else root.append(node(root,'p','The last scan found no changes.','hint'));
 if(data.files.length){
  const table=node(root,'table');const head=node(root,'tr');
  for(const t of ['File','Student','Stage','Timing','Last modified'])head.append(node(root,'th',t));
  table.append(head);
  for(const f of data.files){
   const tr=node(root,'tr');
   tr.append(node(root,'td',f.name),node(root,'td',f.student_id?f.student_id+(f.ic_status_recorded?' · IC recorded: '+f.ic_status_recorded:''):'unmatched'),
    node(root,'td',(STAGES[f.stage]||f.stage)+(f.status!=='present'?' · inaccessible':'')),
    node(root,'td',TIMINGS[f.timing]||f.timing),node(root,'td',when(f.modified_at)));
   table.append(tr);
  }
  root.append(table);
 }
 if(data.needs_review.length){
  root.append(node(root,'h4','Needs your review'));
  const reasons={unmatched_identity_needs_review:'No verified student match yet; review and record it in the grading workbook.',
   after_due_evidence_needs_review:'The document changed after the due date; inspect before assessing.',
   inaccessible_needs_review:'Previously observed work is no longer readable; check folder access before any conclusion.'};
  const list=node(root,'ul');
  for(const item of data.needs_review.slice(0,40))list.append(node(root,'li','File '+item.file_id.slice(0,8)+'… · '+(reasons[item.reason]||item.reason)));
  root.append(list);
 }
 root.append(node(root,'p','Observation only: file evidence is metadata for inspection, not proof of who submitted, an assessment, or grounds for a zero.','hint'));
}

export function mountSubmissionMonitor(container,{fetcher}={}){
 const doc=container.ownerDocument;const request=fetcher||((url,options)=>fetch(url,options));
 const head=node(container,'h3','Submission monitoring');
 const status=node(container,'div');status.setAttribute('aria-live','polite');
 const state=node(container,'p','','hint');state.setAttribute('role','status');
 const refresh=node(container,'button','Refresh status');refresh.type='button';
 const scan=node(container,'button','Scan submissions now');scan.type='button';
 const row=node(container,'div',undefined,'action-row');row.append(refresh,scan);
 container.append(head,row,state,status);
 let scope=null,generation=0;
 async function load(kind){
  if(!scope){state.textContent='Load a lesson with a mapped turn-in folder first.';return;}
  const mine=++generation,{date,classKey}=scope;
  refresh.disabled=scan.disabled=true;
  state.textContent=kind==='scan'?'Scanning the turn-in folder and saving observations…':'Reading the stored monitoring state…';
  try{
   const response=kind==='scan'
    ?await request('/api/submission-monitor',{method:'POST',cache:'no-store',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'scan',date,class_key:classKey})})
    :await request('/api/submission-monitor?'+new URLSearchParams({date,class_key:classKey}),{cache:'no-store',credentials:'same-origin',redirect:'error'});
   const data=await response.json();
   if(mine!==generation)return;
   if(!response.ok)throw Error(describeFailure(data.error));
   renderStatus(status,validateStatus(data));
   state.textContent=kind==='scan'?(data.unchanged_rerun?'Scan complete: nothing changed since the last scan; no duplicate events were created.':'Scan complete and saved.'):'Stored monitoring state loaded. Refresh never starts a scan.';
  }catch(error){if(mine===generation)state.textContent=error.message||'Monitoring is unavailable.';}
  finally{if(mine===generation){refresh.disabled=scan.disabled=false;}}
 }
 refresh.addEventListener('click',()=>load('status'));
 scan.addEventListener('click',()=>load('scan'));
 return {update(next){
  // Becoming ready for the same lesson (agenda finished loading) also counts
  // as a change, so the teacher never has to press Refresh after Load lesson.
  const changed=!scope||scope.date!==next.date||scope.classKey!==next.classKey||(!scope.ready&&next.ready);
  scope=next;
  if(next.ready&&changed)load('status');
  if(!next.ready){generation++;state.textContent='Load a lesson to see its monitoring state.';status.replaceChildren();refresh.disabled=scan.disabled=true;}
  else{refresh.disabled=scan.disabled=false;}
 }};
}
