// Assessment drafting & review: the runtime provider only judges and writes
// feedback; the hub reads the live Agenda instructions, derives the manifest
// from the deck itself, verifies every quoted excerpt against a fresh capture,
// and recomputes all scores in Python at save time. Drafts are persisted
// server-side so paid output survives refreshes and identical content never
// bills twice; only the reviewed Save writes records.
import {deadlineLine} from './submission-monitor.mjs';
const JUDGMENTS=['meaningful_attempt','blank','printed_copy','review'];
const CONCEPTUAL=['Developing','Almost there','Proficient','Evidence unavailable'];
const EXPLANATION=['Names an idea','Connects ideas','Explains how or why','Evidence unavailable'];
const RATES={input:3/1e6,output:15/1e6};// claude-sonnet-5 list prices
const BUDGET=5;
const FAILURES={
 provider_not_configured:'The Anthropic runtime key is not configured; drafting is unavailable until the key dependency is resolved.',
 runtime_key_missing:'The runtime key secret is not reaching the service.',
 runtime_key_malformed:'The stored runtime key is not a valid API key; store a freshly copied key.',
 provider_auth_failed:'The provider rejected the runtime key. Store a freshly copied key.',
 provider_rate_limited:'The provider rate-limited this request. Try again in a minute.',
 provider_request_refused:'The provider refused the request. Check the workspace spend limit and credit balance.',
 provider_unavailable:'The assessment provider could not be reached. Nothing was drafted or saved.',
 provider_draft_invalid:'The drafted output failed validation and was discarded. Generate again.',
 provider_prompts_unavailable:'The live Agenda instructions could not be read; drafting refuses to run on stale prompts.',
 agenda_prompts_layout_changed:'The Agenda’s Workflow Prompts tab changed layout; the live instructions could not be verified.',
 assessment_manifest_missing:'This deck has no marker-inventory manifest header, so its requirements cannot be verified. It is held for manual reconciliation.',
 assessment_manifest_unparsed:'The deck’s manifest header disagrees with its own item lists. Review the deck before assessing.',
 assessment_identity_unresolved:'No verified identity yet: confirm the student name or enter the IC number.',
 assessment_evidence_not_in_source:'A quoted excerpt no longer matches the document. Generate a fresh draft.',
 assessment_not_ready:'At least one item is marked "review"; resolve every review judgment before saving.',
 submission_changed_reassess:'The document changed after this draft was generated. Generate a fresh draft from the current version.',
 assessment_record_conflict:'An identical assessment of this exact version already exists in the workbook.',
 assessment_assignment_unknown:'That assignment is not in the workbook’s Assignments tab.',
 assessment_readback_failed:'The saved rows did not read back as expected. Check the workbook before retrying.',
 assessment_save_busy:'Another request for this student and assignment is still running. Wait, then retry.',
 grading_records_not_connected:'The private grading workbook is not connected.',
 // Server-side Drive access is the Cloud Run service identity, never a local
 // AI account: these are server permission problems, not your sign-in.
 assessment_source_access_required:'The hub’s service account cannot read this document (server-side Drive access, not your sign-in). Share the file or its folder with the runtime reader as Viewer, then retry.',
 assessment_source_unavailable:'The document could not be read from Drive right now (a server-side source problem, not your sign-in). Retry shortly.',
 assessment_source_unsupported:'This file type cannot be exported as text for assessment.',
 sign_in_required:'Your dashboard sign-in expired. Reload the page and sign in again.'};
const explain=code=>FAILURES[code]||('The request could not be completed ('+code+'). Nothing was saved.');

function node(root,tag,text,cls){const n=root.ownerDocument.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.setAttribute('class',cls);return n;}
function labeled(root,text,control){const wrap=node(root,'label',undefined,'field');wrap.append(node(root,'span',text,'hint'),control);return wrap;}
function select(root,options,value){const s=node(root,'select');for(const [v,label,disabled]of options){const o=node(root,'option',label);o.value=v;if(disabled)o.disabled=true;s.append(o);}if(value!==undefined)s.value=value;return s;}
function input(root,value,placeholder){const i=node(root,'input');i.type='text';i.value=value||'';if(placeholder)i.placeholder=placeholder;return i;}
const score=s=>s&&s.exact_value!==null&&s.exact_value!==undefined?(Math.round(s.value*100)/100)+' / '+s.out_of+' (exact '+s.exact_value+')':'not due at this checkpoint';
const cost=usage=>usage&&Number.isFinite(usage.input_tokens)?usage.input_tokens*RATES.input+usage.output_tokens*RATES.output:0;
const money=v=>'$'+v.toFixed(2);
// "Copy of ▶ LIVE — … (VT deck) - First Last" → suggested short display name.
export function suggestName(fileName){
 const tail=String(fileName||'').split(' - ').pop().replace(/\.[a-z]+$/i,'').trim();
 const words=tail.split(/\s+/).filter(Boolean);
 if(words.length<2||tail.length>60||/[—▶]/.test(tail))return '';
 return words[0]+' '+words[words.length-1][0].toUpperCase()+'.';
}

export function mountAssessmentReview(container,{fetcher,records,onSaved}={}){
 const request=fetcher||((url,options)=>fetch(url,options));
 container.append(node(container,'h3','Assessment drafting & review'));
 container.append(node(container,'p','Generate reads the student’s current document and drafts judgments with quoted evidence using the live Agenda instructions (about $0.11 per new submission; unchanged documents are never billed twice). You review and can change any judgment, level or feedback; Save re-verifies the document and recomputes every score from your reviewed judgments. Saved feedback reopens from each student’s row above.','hint'));
 const state=node(container,'p','','hint');state.setAttribute('role','status');
 const form=node(container,'div',undefined,'assessment-form');
 const review=node(container,'div');review.setAttribute('aria-live','polite');
 container.append(state,form,review);
 let scope=null,files=[],deadline=null,generation=0,providerReady=null,busy=false,spent=0;
 const signature=list=>list.map(f=>f.file_id+':'+(f.stage||'')).join('|');
 const budgetLine=()=>money(spent)+' estimated this session of the '+money(BUDGET)+' total approved budget.';

 async function providerStatus(){
  try{
   const r=await request('/api/assessment-run',{cache:'no-store',credentials:'same-origin',redirect:'error'});
   const d=await r.json();
   providerReady=r.ok&&d.configured===true?{model:d.model}:{blocked:d.dependency||d.error||'provider_not_configured'};
  }catch{providerReady={blocked:'provider_unavailable'};}
 }
 function knownAssignments(classKey){
  const data=records?.()?.data;
  return (data?.assignments||[]).filter(a=>a.class_key===classKey).map(a=>[a.assignment_key,a.assignment_key+' · '+a.assignment_title]);
 }
 function knownSections(classKey){
  const data=records?.()?.data;const seen=new Map();
  for(const row of data?.submissions||[])if(row.class_key===classKey&&row.section_id)seen.set(row.section_id,row.section_name||row.section_id);
  return [...seen.entries()];
 }
 function suggestedStage(){
  if(!deadline?.due_date)return {stage:'initial',why:'No verified final deadline yet, so only the NOTES-only initial checkpoint applies; BANK and DRAFT are not due early.'};
  const now=new Date();
  const dueEnd=new Date(deadline.due_date+'T23:59:59');// America/New_York wall clock of the teacher's browser
  return now>dueEnd
   ?{stage:'final',why:'The third-meeting deadline ('+deadline.due_date+', '+deadline.basis+') has passed, so the final checkpoint applies.'}
   :{stage:'initial',why:'The final deadline ('+deadline.due_date+') has not passed; a future deadline never makes BANK/DRAFT due at a NOTES-only checkpoint.'};
 }

 function renderForm(){
  if(busy)return;
  form.replaceChildren();
  if(!scope?.ready){state.textContent='Load a lesson to draft assessments for its submissions.';return;}
  if(!files.length){state.textContent='No monitored submissions yet. Refresh or scan the submission monitoring panel above.';return;}
  state.textContent=providerReady?.blocked?explain(providerReady.blocked):'Provider ready ('+(providerReady?.model||'model pending')+'). '+budgetLine();
  const fileSelect=select(form,files.map(f=>[f.file_id,f.name+' · '+(f.stage||'')]),files[0].file_id);
  const knownForClass=knownAssignments(scope.classKey);
  const preselect=scope?.assignmentKey&&knownForClass.some(([k])=>k===scope.assignmentKey)?scope.assignmentKey:'';
  const assignment=select(form,[['','Select the assignment…',true],...knownForClass],preselect);
  const suggestion=suggestedStage();
  const stage=select(form,[['final','final — NOTES + BANK due, revisions due'],['initial','initial — NOTES only due']],suggestion.stage);
  const stageWhy=node(form,'p',suggestion.why+' You can change the checkpoint.','hint');
  const sections=knownSections(scope.classKey);
  const sectionId=input(form,sections[0]?.[0]||'','section id');
  const sectionName=input(form,sections[0]?.[1]||'','section name');
  const displayName=input(form,suggestName(files[0].name),'confirm the student name');
  const studentId=input(form,'','IC student number, if verified');
  const identityHint=node(form,'p','Identity on file for this document is reused automatically. Otherwise the confirmed name above is recorded as a teacher-confirmed roster match — never an invented IC number.','hint');
  let referenceEdited=false;
  const stageRef=input(form,'','');
  const autoReference=()=>{if(referenceEdited)return;stageRef.value=(deadlineLine(deadline)+' Checkpoint: '+stage.value+'.').slice(0,380);};
  autoReference();
  stageRef.addEventListener('input',()=>{referenceEdited=true;});
  stage.addEventListener('change',()=>{autoReference();review.replaceChildren();});
  fileSelect.addEventListener('change',()=>{const selected=files.find(f=>f.file_id===fileSelect.value);displayName.value=suggestName(selected?.name);studentId.value='';if(selected?.assignment_key&&knownForClass.some(([k])=>k===selected.assignment_key))assignment.value=selected.assignment_key;review.replaceChildren();recoverDraft(fileSelect.value);});
  assignment.addEventListener('change',()=>review.replaceChildren());
  const generate=node(form,'button','Generate feedback for this submission');generate.type='button';
  const batch=node(form,'button','Generate pending feedback for this lesson');batch.type='button';
  const actions=node(form,'div',undefined,'action-row');actions.append(generate,batch);
  form.append(
   labeled(form,'Submission',fileSelect),labeled(form,'Assignment',assignment),
   labeled(form,'Checkpoint',stage),stageWhy,
   labeled(form,'Checkpoint basis (from your saved deadline; editable)',stageRef),
   labeled(form,'Section id',sectionId),labeled(form,'Section name',sectionName),
   labeled(form,'Student name (confirm the match)',displayName),
   labeled(form,'Student number (optional)',studentId),identityHint,actions);

  function buildRequest(fileId,name,assignmentKey=assignment.value.trim()){
   return {file_id:fileId,class_key:scope.classKey,stage:stage.value,
    stage_reference:stageRef.value.trim(),assignment_key:assignmentKey,
    section_id:sectionId.value.trim(),section_name:sectionName.value.trim(),
    identity:{student_id:studentId.value.trim(),student_id_kind:'',learner_key:'',
     display_name:name,identity_basis:''}};
  }
  function checkReady(){
   if(!assignment.value){state.textContent='Choose the assignment first — nothing is assumed.';return false;}
   if(!stageRef.value.trim()){state.textContent='The checkpoint basis is empty; load the lesson’s deadline or describe the checkpoint.';return false;}
   return true;
  }
  async function postDraft(body){
   const r=await request('/api/assessment-run',{method:'POST',cache:'no-store',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   const d=await r.json();
   if(!r.ok)throw Error(explain(d.error));
   return d;
  }
  async function recoverDraft(fileId){
   try{
    state.textContent='Checking for a stored draft of this document…';
    const r=await request('/api/assessment-run?'+new URLSearchParams({draft_for:fileId}),{cache:'no-store',credentials:'same-origin',redirect:'error'});
    const d=await r.json();
    if(r.ok&&d.status==='draft_ready'){state.textContent='A stored draft for this exact document version was recovered — no new model call.';renderDraft(d,files.find(f=>f.file_id===fileId));}
    else state.textContent='No stored draft for the current version. '+(providerReady?.blocked?explain(providerReady.blocked):budgetLine());
   }catch{state.textContent='Stored-draft check unavailable.';}
  }
  generate.addEventListener('click',async()=>{
   if(!checkReady())return;
   const mine=++generation;review.replaceChildren();busy=true;
   const chosen=files.find(f=>f.file_id===fileSelect.value);
   generate.disabled=batch.disabled=true;state.textContent='Capturing the document and drafting judgments… this can take a minute.';
   try{
    const d=await postDraft(buildRequest(fileSelect.value,displayName.value.trim()));
    if(mine!==generation)return;
    if(d.status==='already_assessed'){state.textContent='This exact document version is already assessed and saved (checked '+new Date(d.checked_at).toLocaleString()+'). No model call was made; reopen the saved feedback from the student’s row above.';busy=false;return;}
    if(d.schema_version!=='hub-assessment-provider/v1'||d.status!=='draft_ready'||d.external_writes!==0)throw Error('The draft could not be verified.');
    if(!d.cached)spent+=cost(d.usage);
    state.textContent=(d.cached?'Stored draft recovered — no new model call. ':'Draft ready for your review. ')+budgetLine();
    renderDraft(d,chosen);
   }catch(error){busy=false;if(mine===generation)state.textContent=error.message||'Drafting failed; nothing was saved.';}
   finally{if(mine===generation){generate.disabled=batch.disabled=false;}}
  });
  batch.addEventListener('click',async()=>{
   if(!checkReady())return;
   const selectedAssignment=assignment.value.trim();
   const eligible=files.filter(f=>f.stage!=='assessed'&&f.assignment_key===selectedAssignment);
   const held=files.length-eligible.length;
   if(!eligible.length){state.textContent='Every monitored submission is already assessed at its current version; nothing to generate.';return;}
   review.replaceChildren();
   const plan=node(review,'div',undefined,'surface');
   plan.append(node(review,'h4','Generate pending feedback — review before running'),
    node(review,'p',eligible.length+' eligible submissions · '+held+' already assessed (skipped) · estimated '+money(eligible.length*0.11)+' · '+budgetLine()),
    node(review,'p','Held or failing documents (for example a deck without its manifest) are reported and skipped; the rest continue. Drafts persist on the server and are reviewed one at a time below — nothing saves without you.','hint'));
   const go=node(review,'button','Run — draft '+eligible.length+' submissions');go.type='button';
   const cancel=node(review,'button','Cancel');cancel.type='button';
   const row=node(review,'div',undefined,'action-row');row.append(go,cancel);plan.append(row);review.append(plan);
   cancel.addEventListener('click',()=>review.replaceChildren());
   go.addEventListener('click',async()=>{
    const mine=++generation;busy=true;generate.disabled=batch.disabled=go.disabled=true;
    const list=node(review,'div');review.replaceChildren(list);
    const ready=[];
    try{
     for(const file of eligible){
      if(mine!==generation)return;
      const line=node(list,'p',file.name+' — drafting…');list.append(line);
      try{
       const d=await postDraft(buildRequest(file.file_id,suggestName(file.name),file.assignment_key));
       if(d.status==='already_assessed'){line.textContent=file.name+' — already assessed at this exact version; skipped (no model call).';continue;}
       if(!d.cached)spent+=cost(d.usage);
       line.textContent=file.name+' — draft ready'+(d.cached?' (stored, no new call)':'')+'.';
       const open=node(list,'button','Review this draft');open.type='button';
       open.addEventListener('click',()=>renderDraft(d,file));
       line.append(' ');line.append(open);
       ready.push(file.name);
      }catch(error){line.textContent=file.name+' — held: '+(error.message||'draft failed');line.className='error';}
     }
     state.textContent='Batch complete: '+ready.length+' drafts ready for review. '+budgetLine();
    }finally{busy=false;if(mine===generation){generate.disabled=batch.disabled=false;}}
   });
  });
  recoverDraft(fileSelect.value);
 }

 function renderDraft(draft,file){
  review.replaceChildren();busy=true;
  const packet=draft.packet;
  review.append(node(review,'h4','Draft assessment · '+(packet.identity.display_name||'unnamed')+' · '+packet.assignment_key+' · '+packet.stage+' checkpoint'));
  review.append(node(review,'p','Source: '+(file?.name||packet.source.file_id)+' · version '+packet.source.version+' · last modified '+new Date(packet.source.modified_at).toLocaleString()+' · content hash '+packet.source.sha256.slice(0,12)+'…','hint'));
  if(draft.prompts?.version_label)review.append(node(review,'p','Instructions: live Agenda prompts, '+draft.prompts.version_label,'hint'));
  review.append(node(review,'p','Draft scores — Content/notes: '+score(draft.scores.content_notes)+' · Revision: '+score(draft.scores.revision)+'. Scores are recomputed by the hub from your reviewed judgments when you save.'));
  if(draft.usage?.input_tokens)review.append(node(review,'p','Model '+(draft.usage.model||'')+' · '+draft.usage.input_tokens+' input + '+draft.usage.output_tokens+' output tokens · about '+money(cost(draft.usage))+(draft.cached?' (already paid; recovered from the stored draft)':''),'hint'));
  const table=node(review,'table');const head=node(review,'tr');
  for(const t of ['Item','Judgment','Concept','Explanation','Evidence & note','Feedback to student'])head.append(node(review,'th',t));
  table.append(head);
  const rows=[];
  for(const response of packet.responses){
   const tr=node(review,'tr');
   tr.append(node(review,'td',response.question_id+' · '+response.category+(response.optional?' · optional':'')));
   const judgment=select(review,JUDGMENTS.map(j=>[j,j.replace(/_/g,' ')]),response.judgment);
   const conceptual=select(review,CONCEPTUAL.map(c=>[c,c]),response.conceptual_level);
   const explanation=select(review,EXPLANATION.map(e=>[e,e]),response.explanation_level);
   const evidence=node(review,'td');
   if(response.response_excerpt){const details=node(review,'details');details.append(node(review,'summary','Quoted excerpt'),node(review,'div',response.response_excerpt));evidence.append(details);}
   else evidence.append(node(review,'span','(no response)','hint'));
   if(response.evidence_note)evidence.append(node(review,'p',response.evidence_note,'hint'));
   const feedback=input(review,response.conceptual_feedback,'');
   for(const control of [judgment,conceptual,explanation]){const td=node(review,'td');td.append(control);tr.append(td);}
   tr.append(evidence);const fb=node(review,'td');fb.append(feedback);tr.append(fb);
   table.append(tr);
   rows.push({response,judgment,conceptual,explanation,feedback});
  }
  review.append(table);
  const feedbackBox=node(review,'textarea');feedbackBox.value=packet.feedback;feedbackBox.rows=10;
  review.append(labeled(review,'Student report (editable before saving)',feedbackBox));
  review.append(node(review,'p','Identity: '+(packet.identity.student_id?('student number '+packet.identity.student_id):'teacher-confirmed roster match')+' · '+packet.identity.identity_basis,'hint'));
  review.append(node(review,'p',packet.comparison_status,'hint'));
  const save=node(review,'button','Save reviewed assessment to the workbook');save.type='button';
  const discard=node(review,'button','Discard this draft view');discard.type='button';
  const saveState=node(review,'p','','hint');saveState.setAttribute('role','status');
  const actionRow=node(review,'div',undefined,'action-row');actionRow.append(save,discard);
  review.append(actionRow,saveState);
  discard.addEventListener('click',()=>{busy=false;review.replaceChildren();state.textContent='Draft view closed; the stored draft remains recoverable. '+budgetLine();});
  save.addEventListener('click',async()=>{
   save.disabled=true;saveState.textContent='Re-verifying the document and saving…';
   try{
    const responses=rows.map(({response,judgment,conceptual,explanation,feedback})=>({...response,
     judgment:judgment.value,
     response_excerpt:judgment.value==='blank'?'':response.response_excerpt,
     conceptual_level:judgment.value==='blank'?'Evidence unavailable':conceptual.value,
     explanation_level:judgment.value==='blank'?'Evidence unavailable':explanation.value,
     conceptual_feedback:feedback.value}));
    const reviewed={...packet,responses,feedback:feedbackBox.value};
    const requestId=crypto.randomUUID().replace(/-/g,'').slice(0,32);
    const r=await request('/api/assessment-save',{method:'POST',cache:'no-store',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:requestId,packet:reviewed})});
    const d=await r.json();
    if(!r.ok)throw Error(explain(d.error));
    if(d.readback_verified!==true)throw Error('The save did not read back as expected.');
    saveState.textContent='';
    const receipt=node(review,'div',undefined,'assessment-receipt');
    receipt.append(node(review,'h4','Saved and read back ✓'),
     node(review,'p','Record '+d.record_id+' · Content/notes '+(d.content_score??'—')+' (exact '+(d.content_score_exact||'—')+') · Revision '+(d.revision_score??'not due')+(d.revision_score_exact?' (exact '+d.revision_score_exact+')':'')+' · '+d.answers+' answer rows.'),
     node(review,'p',(d.superseded?.length?('Superseded earlier record(s): '+d.superseded.join(', ')+'.'):'No earlier record superseded.')+' Readback verified against the live workbook. Reopen this feedback anytime from the student’s row above; nothing was posted to Infinite Campus.','hint'));
    review.append(receipt);save.remove();discard.remove();
    busy=false;onSaved?.();
   }catch(error){saveState.textContent=error.message||'The save failed; check the workbook state before retrying.';save.disabled=false;}
  });
 }

 providerStatus().then(()=>{if(scope?.ready)renderForm();});
 return {
  update(next){
   const changed=!scope||scope.date!==next.date||scope.classKey!==next.classKey||scope.ready!==next.ready;
   scope=next;
   if(!next.ready){generation++;files=[];deadline=null;}
   if(changed){
    // A different lesson or class invalidates any pending draft view.
    generation++;busy=false;review.replaceChildren();renderForm();
   }
  },
  setFiles(list,nextDeadline){
   const next=(Array.isArray(list)?list:[]).filter(f=>f.status==='present');
   const changed=signature(next)!==signature(files);
   files=next;if(nextDeadline!==undefined)deadline=nextDeadline;
   if(changed)renderForm();
  }
 };
}
