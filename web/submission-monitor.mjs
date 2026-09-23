// Submission monitoring panel: durable cloud observations, never a hidden scan.
// A status refresh only reads; only the explicit Scan button persists a scan.

export function validateStatus(data){
 if(data?.schema_version!=='hub-submission-monitor-status/v1'||data.external_writes!==0||data.run_enabled!==false)throw Error('Monitoring status could not be verified.');
 if(data.status==='no_complete_scan_recorded')return data;
 if(!data.counts||!Array.isArray(data.files)||!Array.isArray(data.needs_review)||!Array.isArray(data.events_last_scan))throw Error('Monitoring status could not be verified.');
 return data;
}

// Fresh discovery is a preview. It never registers work, changes the saved
// assessment, or advances the durable monitoring baseline.
const DISCOVERY_STATES=new Set(['new_ungraded','changed_since_assessment','needs_review','unchanged']);
export function validateCatalog(data,classKey){
 if(data?.schema_version!=='hub-submission-catalog/v1'||data.provenance!=='source_read'||data.external_writes!==0||data.live_model_calls!==0||data.class_key!==classKey||!Number.isFinite(Date.parse(data.checked_at))||typeof data.coverage?.complete!=='boolean'||!Array.isArray(data.assignments))throw Error('The current lesson list could not be verified.');
 const seen=new Set();
 for(const assignment of data.assignments){
  if(!assignment||assignment.class_key!==classKey||typeof assignment.assignment_key!=='string'||!/^[A-Za-z0-9:_-]{1,128}$/.test(assignment.assignment_key)||assignment.assignment_key==='all'||seen.has(assignment.assignment_key)||typeof assignment.assignment_title!=='string'||!assignment.assignment_title)throw Error('The current lesson list could not be verified.');
  seen.add(assignment.assignment_key);
 }
 return data;
}
export function createCatalogLoader({fetcher=fetch,onState}){
 let generation=0,pending,controller;
 return {load(classKey){
  if(pending?.classKey===classKey)return pending.promise;
  const mine=++generation;controller?.abort();controller=new AbortController();
  onState({status:'loading',classKey});
  let authExpired=false;
  const promise=(async()=>{try{
   const response=await fetcher('/api/submission-catalog?'+new URLSearchParams({class_key:classKey}),{cache:'no-store',credentials:'same-origin',redirect:'error',signal:controller.signal});
   authExpired=[401,403].includes(response.status);
   const data=await response.json();if(mine!==generation)return null;
   if(!response.ok){authExpired=[401,403].includes(response.status);throw Error(authExpired?'Sign in again to read current lessons.':'The current lesson list is unavailable. Saved lessons remain selectable.');}
   const verified=validateCatalog(data,classKey);onState({status:'loaded',classKey,data:verified});return verified;
  }catch(error){if(mine===generation&&error.name!=='AbortError')onState({status:'error',classKey,message:authExpired?'Sign in again to read current lessons.':error.message,authExpired});return null;}
  finally{if(mine===generation)pending=null;}
  })();pending={classKey,promise};return promise;
 }};
}
export function validateDiscovery(data,scope){
 if(data?.schema_version!=='hub-submission-refresh/v1'||data.provenance!=='source_read'||data.external_writes!==0||data.live_model_calls!==0||data.class_key!==scope.classKey||data.assignment_key!==scope.assignmentKey||!Number.isFinite(Date.parse(data.discovered_at))||typeof data.coverage?.complete!=='boolean'||!Array.isArray(data.files))throw Error('The submission discovery scope could not be verified.');
 const seen=new Set();
 for(const file of data.files){
  if(!file||typeof file.file_id!=='string'||!file.file_id||seen.has(file.file_id)||![scope.classKey,''].includes(file.class_key)||typeof file.assignment_key!=='string'||!DISCOVERY_STATES.has(file.discovery_status)||!Array.isArray(file.review_reasons)||file.review_reasons.some(reason=>typeof reason!=='string')||!['','initial','final',undefined,null].includes(file.assessment_stage))throw Error('The discovered submissions need review.');
  if(!file.class_key&&file.discovery_status!=='needs_review')throw Error('The discovered class needs review.');
  if(file.source_changed_since_assessment!==undefined&&typeof file.source_changed_since_assessment!=='boolean')throw Error('The discovered source version needs review.');
  if(file.content_read!==undefined&&typeof file.content_read!=='boolean')throw Error('The discovered source read needs review.');
  if(file.source_availability!==undefined&&!['active','trashed','unavailable','unverified'].includes(file.source_availability))throw Error('The discovered source availability needs review.');
  if(file.content_read===true&&file.source_availability!=='active')throw Error('The discovered source read needs review.');
  if(scope.assignmentKey!=='all'&&file.assignment_key&&file.assignment_key!==scope.assignmentKey)throw Error('The submission discovery scope could not be verified.');
  for(const field of ['source_url','name','assignment_title','section_id','section_name','student_id','display_name'])if(typeof file[field]!=='string')throw Error('The discovered submissions need review.');
  seen.add(file.file_id);
 }
 return data;
}
export function createDiscoveryLoader({fetcher=fetch,onState}){
 let generation=0,controller,pending;
 // Preview data stays in memory only, bounded to a few recently viewed lessons.
 const cache=new Map(),keyFor=scope=>JSON.stringify([scope.classKey,scope.assignmentKey]);
 return {
  clear({purge=false}={}){generation++;controller?.abort();pending=null;if(purge)cache.clear();onState({status:'empty'});},
  load(requested){
   const scope={classKey:requested.classKey,assignmentKey:requested.assignmentKey},key=keyFor(scope);
   if(pending?.key===key)return pending.promise;
   const mine=++generation;controller?.abort();controller=new AbortController();
   const data=cache.get(key),signal=controller.signal;let authExpired=false;
   onState({status:'loading',scope,...(data?{data}:{})});
   const promise=(async()=>{
   try{
    const response=await fetcher('/api/submission-monitor/refresh',{method:'POST',cache:'no-store',credentials:'same-origin',redirect:'error',signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({class_key:scope.classKey,assignment_key:scope.assignmentKey})});
    authExpired=[401,403].includes(response.status);if(authExpired)cache.clear();
    const data=await response.json();if(mine!==generation)return null;
    if(!response.ok){authExpired=[401,403].includes(response.status);if(authExpired)cache.clear();throw Error(authExpired?describeFailure('sign_in_required'):describeFailure(data.error));}
    const verified=validateDiscovery(data,scope);cache.delete(key);cache.set(key,verified);
    if(cache.size>8)cache.delete(cache.keys().next().value);
    onState({status:'loaded',data:verified});return verified;
   }catch(error){if(mine===generation&&error.name!=='AbortError')onState({status:'error',scope,...(data&&!authExpired?{data}:{}),authExpired,message:authExpired?describeFailure('sign_in_required'):error.message||'Submission discovery failed. Saved assessments are unchanged.'});return null;}
   finally{if(mine===generation)pending=null;}
   })();pending={key,promise};return promise;
  }
 };
}

// One specific recovery action per failure; never a generic shrug.
const FAILURES={
 submission_folder_access_required:'The legacy folder source cannot be read by the cloud service. Verify Viewer access before retrying.',
 submission_reader_scope_missing:'The dashboard’s Drive read permission is misconfigured.',
 submission_form_not_configured:'The turn-in Form API id is not configured in the cloud service.',
 submission_form_access_required:'The cloud service cannot read responses from the turn-in Form. Share the editable Form with the runtime reader, then Scan again.',
 submission_form_not_found:'The configured turn-in Form could not be found.',
 submission_form_rate_limited:'Google Forms rate-limited the response read. Try again shortly.',
 submission_form_unavailable:'Google Forms could not be read right now.',
 submission_form_scan_incomplete:'The Form response read was incomplete. No baseline was replaced.',
 submission_root_not_configured:'The approved 00 ALL TURN IN root is not configured in the cloud service.',
 submission_root_coverage_incomplete:'A Form-linked file could not be verified inside 00 ALL TURN IN. No baseline was replaced and nothing was called missing.',
 submission_lesson_mapping_required:'This lesson does not expose an exact assignment-bound turn-in Form in the Agenda.',
 submission_scan_incomplete:'The approved-root read hit a bound before completing. No baseline was replaced.',
 submission_agenda_changed:'The Agenda changed during the scan. Load the lesson again, then Scan.',
 submission_folder_inactive:'The approved submission root is inactive or unavailable.',
 submission_shortcut_needs_review:'A submission reference resolves through a shortcut and is held for review.',
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
const onDay=value=>value?new Date(value+'T12:00:00').toLocaleDateString():'—';

export function deadlineLine(deadline){
 if(!deadline)return 'Deadline: not available.';
 if(deadline.status==='no_topic_start_recorded')return 'No topic start recorded for this assignment. Write "Started <topic>" in the day\u2019s Plan or Notes and save it: that dashboard date becomes meeting 1, and the final revised submission is due at the end of the third class meeting\u2019s day.';
 if(deadline.status==='deadline_unavailable')return 'The deadline could not be derived right now; the stored observations are unaffected.';
 const meetings=(deadline.meetings||[]).map(onDay).join(' · ');
 if(deadline.status==='start_conflict_needs_review')return 'Conflicting topic start dates were written ('+[deadline.start_date,...(deadline.conflicts||[])].map(onDay).join(' vs ')+'). Review which is right; the deadline is held until then.';
 if(deadline.basis==='teacher_exception')return 'Topic started '+onDay(deadline.start_date)+' \u00b7 teacher deadline exception: final revised work due end of '+onDay(deadline.due_date)+'. Calculated from your notes \u2014 not yet verified in Infinite Campus.';
 if(deadline.provisional||!deadline.due_date)return 'Topic started '+onDay(deadline.start_date)+' \u00b7 counted meetings so far: '+(meetings||'none')+' \u00b7 the calendar ahead is not filled in yet, so the third-meeting deadline is provisional.';
 return 'Topic started '+onDay(deadline.start_date)+' \u00b7 meetings '+meetings+' \u00b7 final revised work due end of '+onDay(deadline.due_date)+' (third meeting). Unfinished topics do not move it. Calculated from the Agenda calendar \u2014 not yet verified in Infinite Campus.';
}

export function renderStatus(root,data){
 root.replaceChildren();
 const auto=data.automation||{};
 root.append(node(root,'p',(auto.active?'Automatic monitoring is active ('+auto.cadence+').':'Automatic monitoring is not active; scans run when you press Scan.'),'hint'));
 const attempt=data.last_attempt;
 const attemptLine=attempt?('Last scan attempt '+when(attempt.last_attempt_at)+' · '+(attempt.outcome==='complete'?'complete':'failed')+' · last complete scan '+when(attempt.last_success_at)):'No scan attempts recorded for this assignment.';
 root.append(node(root,'p',attemptLine));
 if(attempt&&attempt.outcome==='failed')root.append(node(root,'p',describeFailure(attempt.reason),'error'));
 root.append(node(root,'p',deadlineLine(data.deadline),'hint'));
 if(data.status==='no_complete_scan_recorded'){root.append(node(root,'p','No complete scan is recorded yet. Scan submissions to establish the first-observed baseline; existing files will be labeled first observed, never late.','hint'));return;}
 root.append(node(root,'p','Assignment '+data.stream.assignment_label+' · scanned '+when(data.observed_at)+' · scan '+data.scan_count+' · baseline since '+when(data.baseline_established_at)+(data.unchanged_rerun?' · unchanged since the last scan':'')));
 const coverage=data.coverage||{};
 const legacyGap=coverage.legacy_outside_root_files??0;
 const offRoot=coverage.form_refs_outside_root??0;
 const folderFiles=coverage.lesson_folder_files??0;
 const legacyShared=coverage.form_source==='shared_collected_identity';
 const coverageText=legacyShared
  ?('Legacy shared-Form observation · '+(coverage.responses_checked??0)+' responses checked · '+(coverage.root_verified_files??0)+' files matched to this lesson inside the approved root. '+offRoot+' off-root references require review. Current intake requires each assignment’s own Form.')
  :coverage.source==='form_responses_root_verified'
  ?('Coverage: assignment Form + exact lesson folder inside 00 ALL TURN IN · '+(coverage.matching_responses??0)+' Form responses · '+folderFiles+' files in the exact lesson folder · '+(coverage.root_verified_files??0)+' verified files total · '+(coverage.folders_checked??0)+' folders checked.'+(offRoot?(' '+offRoot+' Form-linked file'+(offRoot===1?' is':'s are')+' still outside the approved root; '+(offRoot===1?'it is':'they are')+' held as a coverage gap, not called missing.'):'')+(legacyGap?(' '+legacyGap+' already-recorded historical file'+(legacyGap===1?' is':'s are')+' outside current intake; '+(legacyGap===1?'it remains':'they remain')+' a named coverage gap.'):''))
  :('Coverage: complete visible-file scan, '+(coverage.folders_checked??0)+' folders.');
 root.append(node(root,'p',coverageText+(legacyShared?' This stored observation used collected school email, uploaded-deck evidence and the roster. Typed disagreements remain held for review.':' Student matches use prior reviewed grading identities plus the current roster workbook; the live IC roster reader is not integrated, so ambiguous work stays listed for review.'),'hint'));
 if(data.intake_receipt)root.append(node(root,'p',data.intake_receipt.status==='verified'?'Intake workbook registration verified.':'Intake workbook registration is in dry-run mode; no intake rows were saved.','hint'));
 const c=data.counts;
 root.append(node(root,'p',`${c.matched_students??0} matched students · ${c.matched??0} matched files · ${c.present} files present · ${c.awaiting_assessment} awaiting assessment · ${c.unmatched} unmatched files · ${c.needs_review} need review · ${c.inaccessible} inaccessible`));
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
  const reasons={typed_section_disagreement:'Typed class period disagrees with the roster; review it.',
   typed_assignment_disagreement:'Typed assignment differs from the matched deck; review it.',
   typed_name_disagreement:'Typed name differs from the email/roster identity; review it.',
   deck_unmatched_or_revision_changed:'Uploaded deck could not be matched to one current lesson version.',
   deck_ambiguous:'Uploaded deck matches more than one lesson; choose after review.',
   form_reference_outside_approved_root:'Form upload is outside the approved root; verify recovery or router copy provenance.',
   unmatched_identity_needs_review:'No verified student match yet; review and record it in the grading workbook.',
   after_due_evidence_needs_review:'The document changed after the due date; inspect before assessing.',
   inaccessible_needs_review:'Previously observed work is no longer readable; check folder access before any conclusion.'};
  const list=node(root,'ul');
  for(const item of data.needs_review.slice(0,40))list.append(node(root,'li','File '+item.file_id.slice(0,8)+'… · '+(reasons[item.reason]||item.reason)));
  root.append(list);
 }
 root.append(node(root,'p','Observation only: file evidence is metadata for inspection, not proof of who submitted, an assessment, or grounds for a zero.','hint'));
}

export function mountSubmissionMonitor(container,{fetcher,onFiles}={}){
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
  if(!scope){state.textContent='Load a lesson with an assignment-bound turn-in Form first.';return;}
  const mine=++generation,{date,classKey}=scope;
  refresh.disabled=scan.disabled=true;
  state.textContent=kind==='scan'?'Reading assignment Form responses, verifying files in 00 ALL TURN IN, and saving observations…':'Reading the stored monitoring state…';
  try{
   const response=kind==='scan'
    ?await request('/api/submission-monitor',{method:'POST',cache:'no-store',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'scan',date,class_key:classKey})})
    :await request('/api/submission-monitor?'+new URLSearchParams({date,class_key:classKey}),{cache:'no-store',credentials:'same-origin',redirect:'error'});
   const data=await response.json();
   if(mine!==generation)return;
   if(!response.ok)throw Error(describeFailure(data.error));
   renderStatus(status,validateStatus(data));
   onFiles?.(Array.isArray(data.files)?data.files:[],data.deadline||null);
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
  if(!next.ready){generation++;state.textContent='Load a lesson to see its monitoring state.';status.replaceChildren();refresh.disabled=scan.disabled=true;onFiles?.([]);}
  else{refresh.disabled=scan.disabled=false;}
 }};
}
