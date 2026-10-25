// Assessment drafting & review: the runtime provider only judges and writes
// feedback; the hub reads the live Agenda instructions, derives the manifest
// from the deck itself, verifies every quoted excerpt against a fresh capture,
// and recomputes all scores in Python at save time. Drafts are persisted
// server-side so paid output survives refreshes and identical content never
// bills twice; only the reviewed Save writes records.
import {deadlineLine} from './submission-monitor.mjs';
import {verifiedSectionOptions} from './grading-records.mjs';
const JUDGMENTS=['meaningful_attempt','blank','printed_copy','review'];
// Four-level critical-aspect scale (September 23, 2026). An older draft's saved
// value stays selectable so reviewing it never silently changes it.
const CONCEPTUAL=['Beginning','Developing','Proficient','Extending','Evidence unavailable'];
const EXPLANATION=['Names an idea','Connects ideas','Explains how or why','Evidence unavailable'];
const withCurrent=(list,value)=>list.includes(value)||value===undefined?list:[...list,value];
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
 assessment_column_missing:'The grading workbook needs its report columns updated before this assessment can be saved. Keep this draft; it remains stored for review.',
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
 assessment_write_uncertain:'The Save outcome is unknown; the workbook may have changed. Check its saved readback and preserve the pending attempt.',
 assessment_state_unavailable:'The workbook may have saved, but private save history could not be confirmed. Preserve the pending attempt and check readback.',
 assessment_save_unavailable:'The Save outcome could not be confirmed. Check the saved record before another attempt.',
 assessment_save_busy:'Another request for this student and assignment is still running. Wait, then retry.',
 assessment_review_changed:'The saved assessment changed during review. Reload its current record before preparing a successor.',
 assessment_review_save_pending:'An earlier Save for this exact student and lesson is unresolved. Preserve it and check its saved readback before any new Save.',
 assessment_review_record_unavailable:'This saved assessment is held, superseded, ambiguous or incomplete; it cannot be loaded for correction.',
 assessment_review_protected_field_changed:'The prepared packet changes source evidence, identity, checkpoint or requirements. Preserve those fields and review the correction separately.',
 assessment_fresh_packet_changed:'The complete reviewed packet changed. Reopen the fresh review and confirm the exact current packet before Save.',
 grading_records_not_connected:'The private grading workbook is not connected.',
 // Server-side Drive access is the Cloud Run service identity, never a local
 // AI account: these are server permission problems, not your sign-in.
 assessment_source_access_required:'The hub’s service account cannot read this document (server-side Drive access, not your sign-in). Share the file or its folder with the runtime reader as Viewer, then retry.',
 assessment_source_unavailable:'The document could not be read from Drive right now (a server-side source problem, not your sign-in). Retry shortly.',
 assessment_source_unsupported:'This file type cannot be exported as text for assessment.',
 sign_in_required:'Your dashboard sign-in expired. Reload the page and sign in again.'};
const explain=code=>FAILURES[code]||('The request could not be completed ('+code+'). Check the saved record before another Save.');

function node(root,tag,text,cls){const n=root.ownerDocument.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.setAttribute('class',cls);return n;}
function labeled(root,text,control){const wrap=node(root,'label',undefined,'field');wrap.append(node(root,'span',text,'hint'),control);return wrap;}
function select(root,options,value){const s=node(root,'select');for(const [v,label,disabled]of options){const o=node(root,'option',label);o.value=v;if(disabled)o.disabled=true;s.append(o);}if(value!==undefined)s.value=value;return s;}
function input(root,value,placeholder){const i=node(root,'input');i.type='text';i.value=value||'';if(placeholder)i.placeholder=placeholder;return i;}
const score=s=>s&&s.exact_value!==null&&s.exact_value!==undefined?(Math.round(s.value*100)/100)+' / '+s.out_of+' (exact '+s.exact_value+')':'not due at this checkpoint';
const cost=usage=>usage&&Number.isFinite(usage.input_tokens)?usage.input_tokens*RATES.input+usage.output_tokens*RATES.output:0;
const money=v=>'$'+v.toFixed(2);
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exactKeys=(value,required,optional=[])=>object(value)&&required.every(key=>Object.hasOwn(value,key))&&Object.keys(value).every(key=>[...required,...optional].includes(key));
const canonical=value=>Array.isArray(value)?value.map(canonical):object(value)?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const encoded=value=>JSON.stringify(canonical(value));
const copy=value=>JSON.parse(JSON.stringify(value));
const hash=async value=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(encoded(value)))),byte=>byte.toString(16).padStart(2,'0')).join('');
const freshFailure=code=>['submission_changed_reassess','assessment_source_changed_during_capture'].includes(code)
 ?'The current source changed. Refresh the fresh current-source review and review a packet bound to that source before Save.'
 :explain(code);
const firstFailure=code=>code==='assessment_initial_identity_reconciliation_required'
 ?'Existing records use more than one student identity key. Reconcile that identity linkage before a first import; the approved packet was preserved and nothing was saved.'
 :code==='assessment_initial_predecessor_exists'
 ?'An assessment already exists for this student and lesson. Use its saved-record review; the first import will not replace it.'
 :code==='assessment_evidence_not_in_source'
 ?'A quoted excerpt does not match the current source. Preserve the approved packet and have the teacher review its current-source evidence before importing or saving again.'
 :['assessment_manifest_missing','assessment_manifest_unparsed','assessment_manifest_not_in_source','invalid_assessment_manifest'].includes(code)
 ?'The packet’s requirements could not be verified against the current source. Preserve the approved packet and manually review its manifest and item lists before importing or saving again.'
 :code==='assessment_not_ready'
 ?'At least one item still needs review. Have the teacher resolve every judgment in the complete approved packet before importing or saving again.'
 :freshFailure(code);
const timestamp=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)&&Number.isFinite(Date.parse(value));
function preserveSaveEvidence(root,body,response){
 const evidence={schema:'hub-assessment-save-evidence/v1',request:JSON.parse(body),...(response?{response:copy(response)}:{})};
 const text=JSON.stringify(evidence,null,2),details=node(root,'details');
 details.append(node(root,'summary','Preserve this Save attempt'));
 details.append(node(root,'p','This is the exact request sent by this view. Keep it in your private records for readback or recovery; it does not prove that the workbook changed.','hint'));
 const area=node(root,'textarea');area.readOnly=true;area.rows=8;area.value=text;
 details.append(labeled(root,'Save attempt evidence JSON',area));
 const status=node(root,'p','','hint'),copyButton=node(root,'button','Copy Save attempt evidence'),download=node(root,'button','Download Save attempt evidence');
 copyButton.type=download.type='button';
 copyButton.addEventListener('click',async()=>{try{if(!globalThis.navigator?.clipboard?.writeText)throw Error();await globalThis.navigator.clipboard.writeText(text);status.textContent='Save attempt evidence copied.';}catch{area.focus?.();area.select?.();status.textContent='Copy the evidence JSON into your private records.';}});
 download.addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([text],{type:'application/json'})),link=node(root,'a');link.href=url;link.download='assessment-save-attempt.json';link.click();URL.revokeObjectURL(url);});
 details.append(copyButton,download,status);root.append(details);
}
function completeFreshPacket(packet){
 const fields=['schema_version','class_key','section_id','section_name','assignment_key','stage','stage_reference','identity','source','manifest','responses','feedback','comparison_status','ic_assignment_id'];
 const responseFields=['question_id','category','judgment','response_excerpt','evidence_note','conceptual_level','explanation_level','conceptual_feedback','prompt','prompt_basis','slide_number','optional'];
 if(!exactKeys(packet,fields)||packet.schema_version!=='hub-assessment-packet/v1'||
  fields.filter(key=>!['identity','source','manifest','responses'].includes(key)).some(key=>typeof packet[key]!=='string')||!['initial','final'].includes(packet.stage)||
  !exactKeys(packet.identity,['student_id','student_id_kind','learner_key','display_name','identity_basis'])||Object.values(packet.identity).some(value=>typeof value!=='string')||
  !exactKeys(packet.source,['file_id','url','modified_at','version','sha256'])||Object.values(packet.source).some(value=>typeof value!=='string')||!/^[a-f0-9]{64}$/.test(packet.source.sha256)||
  !exactKeys(packet.manifest,['inventory_line','version_label','notes_ids','bank_ids','draft_ids','optional_ids'],['inventory_basis','reconciliation_note'])||
  ['inventory_line','version_label','inventory_basis','reconciliation_note'].some(key=>Object.hasOwn(packet.manifest,key)&&typeof packet.manifest[key]!=='string')||
  ['notes_ids','bank_ids','draft_ids','optional_ids'].some(key=>!Array.isArray(packet.manifest[key])||packet.manifest[key].some(value=>typeof value!=='string')||new Set(packet.manifest[key]).size!==packet.manifest[key].length)||
  !Array.isArray(packet.responses)||!packet.responses.length||packet.responses.length>400||packet.responses.some(response=>
   !exactKeys(response,responseFields,['critical_aspect','rubric_version'])||responseFields.filter(key=>key!=='optional').some(key=>typeof response[key]!=='string')||typeof response.optional!=='boolean'||
   !['NOTES','BANK','DRAFT'].includes(response.category)||!JUDGMENTS.includes(response.judgment)||
   ['critical_aspect','rubric_version'].some(key=>Object.hasOwn(response,key)&&typeof response[key]!=='string'))||
  new Set(packet.responses.map(response=>JSON.stringify([response.category,response.question_id]))).size!==packet.responses.length)
  throw Error('A complete current assessment packet is required. The JSON remains editable; nothing was saved.');
}
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
 container.append(node(container,'p','Review fresh current-source assessment imports a complete teacher-reviewed packet without a model call, including a first assessment. Generate reads the student’s current document and drafts judgments with quoted evidence using the live Agenda instructions (about $0.11 per new assessment). A saved draft is reused when the source version, lesson, section and checkpoint match; moving from Notes to Drafts may require a separate assessment. You review and can change any judgment, level or feedback; Save re-verifies the document and recomputes every score from your reviewed judgments. Saved feedback reopens from each student’s row above.','hint'));
 const state=node(container,'p','','hint');state.setAttribute('role','status');
 const form=node(container,'div',undefined,'assessment-form');
 const review=node(container,'div');review.setAttribute('aria-live','polite');
 container.append(state,form,review);
 let scope=null,files=[],deadline=null,generation=0,providerReady=null,busy=false,spent=0,unresolvedCount=0;
 const recoveryAttempts=new Set();
 const signature=list=>JSON.stringify(list.map(f=>[f.file_id,f.stage,f.section_id,f.class_key,f.assignment_key,f.version||f.source_revision||f.modified_at||'',f.review_reasons||[],f.hold_reason||'']));
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
  return (data?.assignments||[]).filter(a=>a.class_key===classKey).map(a=>[a.assignment_key,a.assignment_title]);
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
  form.inert=false;
  form.replaceChildren();
  if(!scope?.ready){state.textContent='Choose a lesson name and check its submissions to draft assessments.';return;}
  if(!files.length){state.textContent=unresolvedCount?'Submissions with unresolved identities or sections remain in Student work for review. No verified submission is ready for this section.':'No verified submissions are ready for this lesson and section. Refresh submissions to check its current Form and folder.';return;}
  state.textContent=providerReady?.blocked?explain(providerReady.blocked):'Provider ready ('+(providerReady?.model||'model pending')+'). '+budgetLine();
  const fileSelect=select(form,files.map(f=>[f.file_id,f.name+' · '+(f.stage||'')]),files[0].file_id);
  const knownForClass=knownAssignments(scope.classKey);
  const chosenLesson=knownForClass.find(([key])=>key===scope.assignmentKey);
  const chosenSection=verifiedSectionOptions(records?.()?.data||{},scope.classKey).find(([id])=>id===scope.sectionId);
  if(!chosenLesson||!chosenSection){state.textContent='The selected lesson and section must be verified before assessment.';return;}
  const target={classKey:scope.classKey,assignmentKey:scope.assignmentKey,sectionId:chosenSection[0],sectionName:chosenSection[1]};
  const assignment={value:target.assignmentKey};
  const suggestion=suggestedStage();
  const stage=select(form,[['final','Drafts — notes, bank and revisions due'],['initial','Notes — initial checkpoint']],['initial','final'].includes(scope.stage)?scope.stage:suggestion.stage);
  const stageWhy=node(form,'p',suggestion.why+' You can change the checkpoint.','hint');
  const displayName=input(form,suggestName(files[0].name),'confirm the student name');
  const studentId=input(form,'','IC student number, if verified');
  const identityHint=node(form,'p','Identity on file for this document is reused automatically. Otherwise the confirmed name above is recorded as a teacher-confirmed roster match — never an invented IC number.','hint');
  let referenceEdited=false;
  const stageRef=input(form,'','');
  const autoReference=()=>{if(referenceEdited)return;stageRef.value=(deadlineLine(deadline)+' Checkpoint: '+stage.value+'.').slice(0,380);};
  autoReference();
  stageRef.addEventListener('input',()=>{referenceEdited=true;});
  stage.addEventListener('change',()=>{generation++;busy=false;form.inert=false;generate.disabled=batch.disabled=false;autoReference();review.replaceChildren();});
  fileSelect.addEventListener('change',()=>{generation++;busy=false;form.inert=false;generate.disabled=batch.disabled=false;displayName.value=suggestName(files.find(f=>f.file_id===fileSelect.value)?.name);studentId.value='';review.replaceChildren();recoverDraft(fileSelect.value);});
  const generate=node(form,'button','Generate feedback for this submission');generate.type='button';
  const batch=node(form,'button','Generate pending feedback for this lesson');batch.type='button';
  const loadSaved=node(form,'button','Load saved assessment for review');loadSaved.type='button';
  const loadFresh=node(form,'button','Review fresh current-source assessment');loadFresh.type='button';
  const actions=node(form,'div',undefined,'action-row');actions.append(loadSaved,loadFresh,generate,batch);
  form.append(
   node(form,'p',chosenLesson[1]+' · '+chosenSection[1]),labeled(form,'Submission',fileSelect),
   labeled(form,'Checkpoint',stage),stageWhy,
   labeled(form,'Checkpoint basis (from your saved deadline; editable)',stageRef),
   labeled(form,'Student name (confirm the match)',displayName),
   labeled(form,'Student number (optional)',studentId),identityHint,actions);

  function buildRequest(fileId,name){
   return {file_id:fileId,class_key:target.classKey,stage:stage.value,
    stage_reference:stageRef.value.trim(),assignment_key:assignment.value.trim(),
    section_id:target.sectionId,section_name:target.sectionName,
    identity:{student_id:studentId.value.trim(),student_id_kind:'',learner_key:'',
     display_name:name,identity_basis:''}};
  }
  function checkReady(loading=false){
   if(!scope?.ready||scope.classKey!==target.classKey||scope.assignmentKey!==target.assignmentKey||scope.sectionId!==target.sectionId){state.textContent='The lesson or section changed. Check submissions again.';return false;}
   if(records?.()?.status!=='loaded'||!verifiedSectionOptions(records().data,target.classKey).some(([id])=>id===target.sectionId)||!knownAssignments(target.classKey).some(([key])=>key===target.assignmentKey)){state.textContent='Refresh the verified lesson and section before generating feedback.';return false;}
   if(!loading&&!stageRef.value.trim()){state.textContent='The checkpoint basis is empty; load the lesson’s deadline or describe the checkpoint.';return false;}
   return true;
  }
  async function postDraft(body){
   const r=await request('/api/assessment-run',{method:'POST',cache:'no-store',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   const d=await r.json();
   if(!r.ok)throw Error(explain(d.error));
   if(d.status==='draft_ready'&&(d.packet?.class_key!==body.class_key||d.packet?.assignment_key!==body.assignment_key||d.packet?.section_id!==body.section_id||d.packet?.stage!==body.stage||d.packet?.source?.file_id!==body.file_id))throw Error('The draft does not match the requested lesson, section, checkpoint and submission.');
   return d;
  }
  async function recoverDraft(fileId){
   const requestedGeneration=generation;
   try{
    state.textContent='Checking for a stored draft of this document…';
    const r=await request('/api/assessment-run?'+new URLSearchParams({draft_for:fileId,class_key:target.classKey,assignment_key:target.assignmentKey,section_id:target.sectionId,stage:stage.value}),{cache:'no-store',credentials:'same-origin',redirect:'error'});
    const d=await r.json();
    if(requestedGeneration!==generation||fileSelect.value!==fileId||scope?.assignmentKey!==target.assignmentKey||scope?.sectionId!==target.sectionId)return;
    if(r.ok&&d.status==='draft_ready'&&d.packet?.assignment_key===target.assignmentKey&&d.packet?.section_id===target.sectionId&&d.packet?.class_key===target.classKey&&d.packet?.stage===stage.value&&d.packet?.source?.file_id===fileId){state.textContent='A stored draft for this exact document version was recovered — no new model call.';renderDraft(d,files.find(f=>f.file_id===fileId));}
    else state.textContent='No stored draft for the current version. '+(providerReady?.blocked?explain(providerReady.blocked):budgetLine());
   }catch{if(requestedGeneration===generation)state.textContent='Stored-draft check unavailable.';}
  }
  loadFresh.addEventListener('click',async()=>{
   if(!checkReady(true))return;
   const file=files.find(file=>file.file_id===fileSelect.value);
   const saved=(records().data.submissions||[]).filter(row=>{
    let sourceId=row.source_file_id;
    if(!sourceId){try{sourceId=new URL(row.source_url).pathname.match(/\/d\/([^/]+)/)?.[1];}catch{}}
    return sourceId===file?.file_id&&row.class_key===target.classKey&&row.assignment_key===target.assignmentKey&&row.section_id===target.sectionId&&row.assessment_stage===stage.value&&row.status==='assessed'&&!row.hold_reason&&!row.superseded_by;
   });
   if(saved.length>1){state.textContent='One active saved assessment for this exact submission and checkpoint is required. Refresh the saved records; nothing was generated.';return;}
   const mine=++generation,selectedStage=stage.value;busy=true;review.replaceChildren();loadFresh.disabled=true;
   if(!saved.length){
    renderFirstImport(file,target,selectedStage,mine,()=>fileSelect.value===file.file_id&&stage.value===selectedStage);
    loadFresh.disabled=false;return;
   }
   state.textContent='Verifying the saved record and current source for a fresh reviewed packet — no model call…';
   try{
    const r=await request('/api/assessment-save?'+new URLSearchParams({record_id:saved[0].record_id,mode:'fresh_current_review'}),{cache:'no-store',credentials:'same-origin',redirect:'error'});
    const d=await r.json();if(mine!==generation)return;
    if(!r.ok){
     if(d.error==='assessment_review_save_pending'){
      await renderLegacyRecovery(saved[0],file,target,selectedStage,mine,()=>fileSelect.value===file.file_id&&stage.value===selectedStage);
      return;
     }
     throw Error(freshFailure(d.error));
    }
    const basis=d.review_basis,fresh=basis?.source;
    if(d.schema_version!=='hub-assessment-review/v1'||d.status!=='fresh_packet_required'||d.packet!==null||d.external_writes!==0||d.live_model_calls!==0||
     !exactKeys(basis,['mode','record_id','record_sha256','source'])||basis.mode!=='fresh_current_review'||basis.record_id!==saved[0].record_id||!/^[a-f0-9]{64}$/.test(basis.record_sha256)||
     d.base?.submission?.record_id!==basis.record_id||!Array.isArray(d.base?.answers)||!object(fresh)||fresh.file_id!==file.file_id||
     ['version','modified_at'].some(key=>typeof fresh[key]!=='string'||!fresh[key])||!/^[a-f0-9]{64}$/.test(fresh.sha256))throw Error('The fresh current-source review could not be verified.');
    review.append(node(review,'p','Paste the complete current assessment packet already reviewed by the teacher. This path imports explicit judgments and evidence; it does not generate or infer them.','hint'));
    const prepared=node(review,'textarea');prepared.rows=10;review.append(labeled(review,'Teacher-reviewed current assessment JSON',prepared));
    const open=node(review,'button','Review imported current assessment');open.type='button';
    const cancel=node(review,'button','Cancel fresh current-source review');cancel.type='button';review.append(open,cancel);
    cancel.addEventListener('click',()=>{generation++;busy=false;review.replaceChildren();state.textContent='Fresh review cancelled. Nothing was saved or generated.';});
    let opening=false;
    open.addEventListener('click',async()=>{
     if(opening||mine!==generation)return;opening=true;
     try{
      if(prepared.value.length>400000)throw Error('The prepared packet exceeds the bounded review size. Nothing was saved.');
      const imported=JSON.parse(prepared.value);
      const packet=exactKeys(imported,['packet'])?imported.packet:imported;
      completeFreshPacket(packet);
      if(packet.class_key!==target.classKey||packet.assignment_key!==target.assignmentKey||packet.section_id!==target.sectionId||packet.section_name!==target.sectionName||packet.stage!==selectedStage||packet.source.file_id!==file.file_id)
       throw Error('The current packet does not match the selected lesson, section, checkpoint and submission.');
      if(['file_id','sha256','version','modified_at'].some(key=>packet.source[key]!==fresh[key]))throw Error('The packet does not match the verified current source. Refresh the fresh current-source review before importing it.');
      const immutable=copy(packet),packetHash=await hash(immutable);
      if(mine!==generation)return;
      renderFreshReview(d,immutable,packetHash,file,mine);
     }catch(error){if(mine===generation)state.textContent=error.message||'The prepared packet is not valid JSON.';}
     finally{opening=false;}
    });
   }catch(error){if(mine===generation){busy=false;state.textContent=error.message||'The fresh current-source review could not be loaded.';}}
   finally{loadFresh.disabled=false;}
  });
  loadSaved.addEventListener('click',async()=>{
   if(!checkReady(true))return;
   const file=files.find(f=>f.file_id===fileSelect.value);
   const saved=(records().data.submissions||[]).filter(row=>{
    let sourceId=row.source_file_id;
    if(!sourceId){try{sourceId=new URL(row.source_url).pathname.match(/\/d\/([^/]+)/)?.[1];}catch{}}
    return sourceId===file.file_id&&row.class_key===target.classKey&&row.assignment_key===target.assignmentKey&&row.section_id===target.sectionId&&row.assessment_stage===stage.value&&row.status==='assessed'&&!row.hold_reason&&!row.superseded_by;
   });
   if(saved.length!==1){state.textContent='One unheld saved assessment for this exact submission and checkpoint is required. Refresh the saved records; nothing was generated.';return;}
   const mine=++generation;busy=true;review.replaceChildren();loadSaved.disabled=true;
   state.textContent='Loading the saved assessment and verifying its current source — no model call…';
   try{
    const r=await request('/api/assessment-save?'+new URLSearchParams({record_id:saved[0].record_id}),{cache:'no-store',credentials:'same-origin',redirect:'error'});
    const d=await r.json();
    if(mine!==generation)return;
    if(!r.ok)throw Error(explain(d.error));
    if(d.schema_version!=='hub-assessment-review/v1'||d.external_writes!==0||d.live_model_calls!==0||d.review_basis?.record_id!==saved[0].record_id||d.base?.submission?.record_id!==saved[0].record_id||!Array.isArray(d.base?.answers)||!['saved_review_ready','saved_packet_required'].includes(d.status))throw Error('The saved review could not be verified.');
    const matches=p=>p?.class_key===target.classKey&&p?.assignment_key===target.assignmentKey&&p?.section_id===target.sectionId&&p?.stage===stage.value&&p?.source?.file_id===file.file_id;
    const open=p=>{
     if(mine!==generation)return;
     if(!matches(p))throw Error('The prepared assessment does not match the selected lesson, section, checkpoint and submission.');
     if(!p.identity||typeof p.identity.display_name!=='string'||typeof p.identity.student_id!=='string'||typeof p.identity.identity_basis!=='string'||typeof p.source.sha256!=='string'||typeof p.source.version!=='string'||typeof p.source.modified_at!=='string'||typeof p.feedback!=='string'||typeof p.comparison_status!=='string'||!Array.isArray(p.responses)||p.responses.some(a=>!a||!JUDGMENTS.includes(a.judgment)||typeof a.question_id!=='string'||typeof a.category!=='string'||typeof a.response_excerpt!=='string'||typeof a.evidence_note!=='string'||typeof a.conceptual_level!=='string'||typeof a.explanation_level!=='string'||typeof a.conceptual_feedback!=='string'))throw Error('The prepared assessment packet is incomplete. Its JSON remains here for correction; nothing was saved.');
     state.textContent='Saved assessment loaded for review — no model call. Review every listed change before Save. Prior records and feedback remain in history.';
     renderDraft({...d,packet:p,original_packet:d.packet},file);
    };
    if(d.status==='saved_review_ready')open(d.packet);
    else{
     review.append(node(review,'p','The complete reviewed packet was not retained. Workbook completion flags cannot recreate its original judgments. Paste the prepared assessment packet to review it without generating new model judgments.','hint'));
     const prepared=node(review,'textarea');prepared.rows=8;
     review.append(labeled(review,'Prepared assessment packet JSON',prepared));
     const importPacket=node(review,'button','Review prepared successor');importPacket.type='button';review.append(importPacket);
     importPacket.addEventListener('click',()=>{
      if(mine!==generation)return;
      try{const parsed=JSON.parse(prepared.value);open(parsed.packet||parsed);}
      catch(error){state.textContent=error.message||'The prepared packet is not valid JSON.';}
     });
     const cancel=node(review,'button','Cancel saved review');cancel.type='button';review.append(cancel);
     cancel.addEventListener('click',()=>{generation++;busy=false;review.replaceChildren();state.textContent='Saved review cancelled. Nothing was saved or generated.';});
    }
   }catch(error){if(mine===generation){busy=false;state.textContent=error.message||'The saved assessment could not be loaded.';}}
   finally{loadSaved.disabled=false;}
  });
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
   const eligible=files.filter(f=>f.stage!=='assessed');
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
    if(!checkReady())return;
    const mine=++generation;busy=true;generate.disabled=batch.disabled=go.disabled=true;
    const list=node(review,'div');review.replaceChildren(list);
    const ready=[];
    try{
     for(const file of eligible){
      if(mine!==generation)return;
      const line=node(list,'p',file.name+' — drafting…');list.append(line);
      try{
       const d=await postDraft(buildRequest(file.file_id,suggestName(file.name)));
       if(mine!==generation)return;
       if(d.status==='already_assessed'){line.textContent=file.name+' — already assessed at this exact version; skipped (no model call).';continue;}
       if(!d.cached)spent+=cost(d.usage);
       line.textContent=file.name+' — draft ready'+(d.cached?' (stored, no new call)':'')+'.';
       const open=node(list,'button','Review this draft');open.type='button';
       open.addEventListener('click',()=>{if(mine===generation)renderDraft(d,file);});
       line.append(' ');line.append(open);
       ready.push(file.name);
      }catch(error){line.textContent=file.name+' — held: '+(error.message||'draft failed');line.className='error';}
     }
     state.textContent='Batch complete: '+ready.length+' drafts ready for review. '+budgetLine();
    }finally{if(mine===generation){busy=false;generate.disabled=batch.disabled=false;}}
   });
  });
  recoverDraft(fileSelect.value);
 }

 async function renderLegacyRecovery(saved,file,target,selectedStage,mine,selected){
  let closed=false,attempted=false,inFlight=false,challenge=null;
  const current=()=>!closed&&mine===generation&&selected()&&scope?.ready&&scope.classKey===target.classKey&&scope.assignmentKey===target.assignmentKey&&scope.sectionId===target.sectionId&&records?.()?.status==='loaded'&&files.some(item=>item.file_id===file.file_id);
  const result=node(review,'p','','hint');result.setAttribute('role','status');
  review.replaceChildren(node(review,'h4','Resolve this pending assessment Save'),result);
  const cancel=node(review,'button','Cancel pending Save review');cancel.type='button';review.append(cancel);
  cancel.addEventListener('click',()=>{if(inFlight)return;closed=true;generation++;busy=false;form.inert=false;review.replaceChildren();state.textContent='Pending Save review closed. Its history remains preserved.';});
  const verified=d=>['reconciled','already_reconciled'].includes(d?.status)&&d.readback_verified===true&&d.grade_writes===0&&typeof d.receipt?.record_id==='string'&&/^[A-Za-z0-9_-]{1,160}$/.test(d.receipt.record_id)&&/^run_[a-f0-9]{64}$/.test(d.receipt.run_id)&&(!challenge||d.receipt.run_id===challenge.run_id);
  const complete=d=>{result.textContent='Recovery read back ✓ · Receipt '+d.receipt.record_id+'. No grades were written. Load a fresh current-source review, review its packet, and make a separate new Save. Nothing is generated or saved automatically.';busy=false;form.inert=false;};
  const read=async()=>{const r=await request('/api/assessment-recovery?'+new URLSearchParams({record_id:saved.record_id,...(challenge?{run_id:challenge.run_id}:{})}),{cache:'no-store',credentials:'same-origin',redirect:'error'});const d=await r.json();if(!r.ok)throw Error(explain(d.error));return d;};
  result.textContent='Checking this selected submission’s pending operation — no model call or grade write…';
  try{
   const d=await read();if(!current())return;
   if(verified(d)){complete(d);return;}
   const c=d.challenge;
   if(d.schema!=='hub-assessment-legacy-recovery/v1'||d.status!=='legacy_pending_review'||!object(c)||c.schema!=='hub-assessment-legacy-challenge/v1'||c.record_id!==saved.record_id||
    ['task_id','run_id'].some(key=>typeof c[key]!=='string'||!c[key]||c[key].length>200)||!Number.isInteger(c.fence)||c.fence<1||
    ['input_hash','task_sha256','run_sha256','current_baseline_sha256','workbook_sha256','headers_sha256'].some(key=>!/^[a-f0-9]{64}$/.test(c[key]))||
    !Object.hasOwn(c,'task_version')||!Object.hasOwn(c,'run_version')||!object(c.current_source)||c.current_source.file_id!==file.file_id||
    typeof c.current_source.version!=='string'||!/^[a-f0-9]{64}$/.test(c.current_source.sha256)||!timestamp(d.run?.started_at)||!timestamp(d.run?.expires_at))throw Error('This pending operation could not be verified. Its history remains held.');
   challenge=copy(c);busy=false;form.inert=false;
   result.textContent='This one assessment remains held until its historical evidence and current server checks agree. Other submissions can still be processed.';
   review.append(node(review,'p',file.name+' · '+target.sectionName+' · '+selectedStage+' checkpoint'),
    node(review,'p','Pending run '+c.run_id+' · fence '+c.fence+' · expires '+d.run.expires_at,'hint'),
    node(review,'p','Current server proof: source version '+c.current_source.version+' · hash '+c.current_source.sha256+' · current baseline '+c.current_baseline_sha256+'. This is a fresh check, not proof of the original baseline.','hint'),
    node(review,'p','Provide the surviving reviewed packet and the private evidence of the missing-report-columns message. Historical observations below are your attestation; the server independently rechecks both workbook tables and the current baseline.','hint'));
   const prepared=node(review,'textarea');prepared.rows=10;
   if(object(d.retained_attempt)&&object(d.retained_attempt.packet))prepared.value=JSON.stringify(d.retained_attempt.packet,null,2);
   const evidence=input(review,'','private evidence reference'),absence=input(review,'','2026-10-25T12:00:00Z'),started=input(review,d.run.started_at);started.readOnly=true;
   review.append(labeled(review,'Surviving reviewed assessment packet JSON',prepared),labeled(review,'Private failure evidence reference',evidence),
    labeled(review,'Attempt started at (server record)',started),labeled(review,'First both-table absence observation at (timestamp with timezone)',absence));
   const approval=node(review,'input');approval.type='checkbox';approval.checked=false;
   review.append(labeled(review,'I observed the missing-report-columns message, checked that the attempted record was absent from both Submissions and Answers at the time above, verified the prior baseline was unchanged, and attest that this attempt made no native workbook write.',approval));
   const submit=node(review,'button','Reconcile this verified pre-write failure');submit.type='button';
   const check=node(review,'button','Check recovery readback');check.type='button';check.hidden=true;review.append(submit,check);
   const disable=()=>{submit.disabled=true;prepared.readOnly=evidence.readOnly=absence.readOnly=true;approval.disabled=true;};
   if(recoveryAttempts.has(challenge.run_id)){attempted=true;disable();check.hidden=false;result.textContent='This view already sent a recovery request for this exact operation. Check its readback; this view will not send another recovery request.';}
   check.addEventListener('click',async()=>{if(!current()||inFlight||!attempted)return;inFlight=true;check.disabled=true;try{const reply=await read();if(!current())return;if(verified(reply)){complete(reply);check.hidden=true;}else result.textContent='Recovery is still unresolved for this exact operation. Preserve the attempt; this view will not send another recovery request.';}catch(error){if(current())result.textContent=(error.message||'Recovery readback is unavailable.')+' Preserve this attempt; this view will not send another recovery request.';}finally{inFlight=false;check.disabled=false;}});
   submit.addEventListener('click',async()=>{
    if(!current()||attempted||inFlight)return;inFlight=true;submit.disabled=true;
    try{
     if(!approval.checked)throw Error('Confirm every historical observation before requesting recovery.');
     if(!evidence.value.trim()||evidence.value.length>400||/[\x00-\x1f\x7f]/.test(evidence.value))throw Error('Provide the private failure evidence reference.');
     if(!timestamp(absence.value)||Date.parse(absence.value)<Date.parse(d.run.started_at))throw Error('Provide the first both-table absence timestamp, including its timezone, at or after this attempt started.');
     if(prepared.value.length>400000)throw Error('The surviving packet exceeds the bounded review size.');
     const importedText=prepared.value,evidenceRef=evidence.value.trim(),firstAbsence=absence.value;
     const imported=JSON.parse(importedText),packet=exactKeys(imported,['packet'])?imported.packet:imported;completeFreshPacket(packet);
     if(packet.class_key!==target.classKey||packet.assignment_key!==target.assignmentKey||packet.section_id!==target.sectionId||packet.section_name!==target.sectionName||packet.stage!==selectedStage||packet.source.file_id!==file.file_id)throw Error('The surviving packet does not match the selected lesson, section, checkpoint and submission.');
     const immutable=copy(packet);if(await hash(immutable)!==challenge.input_hash)throw Error('The surviving packet does not match this exact pending operation. Preserve the original packet; nothing was reconciled.');
     if(!current())return;
     if(!approval.checked||prepared.value!==importedText||evidence.value.trim()!==evidenceRef||absence.value!==firstAbsence)throw Error('The packet or historical observations changed. Review and confirm them before requesting recovery.');
     const payload={mode:'legacy_ui_prewrite',record_id:saved.record_id,packet:immutable,challenge:copy(challenge),historical_evidence:{schema:'hub-assessment-legacy-ui-evidence/v1',observation:'missing_report_columns_ui',evidence_ref:evidenceRef,attempt_started_at:d.run.started_at,first_absence_at:firstAbsence,baseline_unchanged_attested:true,no_native_write_attested:true}};
     attempted=true;recoveryAttempts.add(challenge.run_id);disable();cancel.disabled=true;result.textContent='Rechecking this exact failure and recording its recovery — no grade write…';
     const r=await request('/api/assessment-recovery',{method:'POST',cache:'no-store',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),reply=await r.json();
     if(!current())return;if(!r.ok)throw Error(explain(reply.error));if(!verified(reply))throw Error('Recovery did not return verified readback for this exact operation.');complete(reply);
    }catch(error){if(current()){result.textContent=error.message||'The recovery reply was unavailable.';if(attempted){result.textContent+=' Preserve this attempt and check recovery readback; this view will not send another recovery request.';check.hidden=false;}}}
    finally{inFlight=false;cancel.disabled=false;if(!attempted)submit.disabled=false;}
   });
  }catch(error){if(current()){busy=false;form.inert=false;result.textContent=error.message||'Pending-operation readback is unavailable. Preserve its history.';}}
 }

 function renderFirstImport(file,target,selectedStage,mine,selected){
  state.textContent='Import a complete first assessment already reviewed by the teacher — no model call.';
  review.append(node(review,'h4','Review a first assessment'),node(review,'p','Paste the whole teacher-reviewed current assessment packet. The server will verify its source, identity, lesson, section and checkpoint, and check that no earlier assessment would be replaced. Nothing is generated or saved by this check.','hint'));
  const prepared=node(review,'textarea');prepared.rows=10;review.append(labeled(review,'Teacher-reviewed current assessment JSON',prepared));
  const open=node(review,'button','Review imported current assessment');open.type='button';
  const cancel=node(review,'button','Cancel first assessment review');cancel.type='button';review.append(open,cancel);
  let opening=false,closed=false;
  const current=()=>!closed&&mine===generation&&selected()&&scope?.ready&&scope.classKey===target.classKey&&scope.assignmentKey===target.assignmentKey&&scope.sectionId===target.sectionId&&records?.()?.status==='loaded';
  cancel.addEventListener('click',()=>{closed=true;generation++;busy=false;form.inert=false;review.replaceChildren();state.textContent='First assessment review cancelled. Nothing was saved or generated.';});
  open.addEventListener('click',async()=>{
   if(opening||!current())return;opening=true;open.disabled=true;
   try{
    const importedText=prepared.value;
    if(importedText.length>400000)throw Error('The prepared packet exceeds the bounded review size. Nothing was saved.');
    const imported=JSON.parse(importedText),packet=exactKeys(imported,['packet'])?imported.packet:imported;
    completeFreshPacket(packet);
    if(packet.class_key!==target.classKey||packet.assignment_key!==target.assignmentKey||packet.section_id!==target.sectionId||packet.section_name!==target.sectionName||packet.stage!==selectedStage||packet.source.file_id!==file.file_id)
     throw Error('The current packet does not match the selected lesson, section, checkpoint and submission.');
    if(!packet.stage_reference.trim()||!packet.identity.display_name.trim()||!packet.identity.student_id_kind.trim()||!packet.identity.identity_basis.trim()||!(packet.identity.student_id.trim()||packet.identity.learner_key.trim()))
     throw Error('The first packet needs its explicit confirmed identity and checkpoint basis. Nothing was inferred.');
    const immutable=copy(packet),packetHash=await hash(immutable);
    if(!current())return;
    if(prepared.value!==importedText)throw Error('The packet changed during verification. Review its current JSON again.');
    state.textContent='Verifying the first reviewed packet and current source — no model call…';
    const r=await request('/api/assessment-review',{method:'POST',cache:'no-store',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify({packet:immutable})});
    const d=await r.json();if(!current())return;
    if(!r.ok)throw Error(firstFailure(d.error));
    const basis=d.review_basis,fresh=basis?.source;
    const validScore=value=>object(value)&&Number.isFinite(value.out_of)&&value.out_of>0&&((value.value===null&&value.exact_value===null)||(Number.isFinite(value.value)&&typeof value.exact_value==='string'&&value.exact_value.length>0));
    if(d.schema_version!=='hub-assessment-review/v1'||d.status!=='first_packet_ready'||typeof d.record_id!=='string'||!d.record_id||d.external_writes!==0||d.live_model_calls!==0||
     !exactKeys(basis,['mode','source','packet_sha256','target_sha256','absence_sha256'])||basis.mode!=='first_current_review'||
     ['packet_sha256','target_sha256','absence_sha256'].some(key=>!/^[a-f0-9]{64}$/.test(basis[key]))||basis.packet_sha256!==packetHash||
     !object(fresh)||['file_id','sha256','version','modified_at'].some(key=>fresh[key]!==immutable.source[key])||
     !object(d.scores)||!validScore(d.scores.content_notes)||!validScore(d.scores.revision)||await hash(d.packet)!==packetHash)
     throw Error('The first assessment review could not be verified. Nothing was saved.');
    if(!current())return;
    if(prepared.value!==importedText)throw Error('The packet changed during verification. Review its current JSON again.');
    renderFreshReview(d,immutable,packetHash,file,mine);
   }catch(error){if(current())state.textContent=error.message||'The prepared packet is not valid JSON.';}
   finally{opening=false;open.disabled=false;}
  });
 }

 function renderFreshReview(loaded,packet,packetHash,file,mine){
  if(mine!==generation||!scope?.ready||packet.class_key!==scope.classKey||packet.assignment_key!==scope.assignmentKey||packet.section_id!==scope.sectionId||!files.some(current=>current.file_id===file.file_id))return;
  let closed=false,attempted=false,inFlight=false,attemptBody=null,responseEvidence=null;
  const first=loaded.status==='first_packet_ready'&&loaded.review_basis.mode==='first_current_review';
  const basis={...copy(loaded.review_basis),packet_sha256:packetHash};
  const firstSavedProof=d=>d.readback_verified===true&&['saved','already_saved'].includes(d.status)&&d.record_id===loaded.record_id&&
   object(d.source)&&['file_id','sha256','version','modified_at'].every(key=>d.source[key]===packet.source[key])&&
   Array.isArray(d.superseded)&&d.superseded.length===0&&d.answers===packet.responses.length;
  review.replaceChildren();busy=true;form.inert=true;
  const meaningful=packet.responses.filter(response=>response.judgment==='meaningful_attempt').length;
  state.textContent=(first?'First assessment':'Fresh current-source assessment')+' ready for confirmation — no model call. The complete packet stays unchanged through Save.';
  review.append(node(review,'h4',(first?'First assessment':'Fresh current-source assessment')+' · '+packet.identity.display_name+' · '+packet.stage+' checkpoint'));
  if(!first)review.append(node(review,'p','Original saved record: '+basis.record_id+'. Earlier assessments and uncertain Save history remain preserved.','hint'));
  review.append(
   node(review,'p','Verified current source version '+packet.source.version+' · hash '+packet.source.sha256),
   node(review,'p','Explicit judgments: '+meaningful+' meaningful; '+(packet.responses.length-meaningful)+' not meaningful; '+packet.responses.length+' total. These counts report the imported judgments, not a new assessment.'),
   node(review,'p','Reviewed packet hash: '+packetHash,'hint'));
  if(first){
   review.append(node(review,'p','Identity: '+(packet.identity.student_id||'(no native student number)')+' · kind '+packet.identity.student_id_kind+' · learner key '+(packet.identity.learner_key||'(none)')+' · '+packet.identity.identity_basis),
    node(review,'p','Checkpoint basis: '+packet.stage_reference),node(review,'p','Manifest: '+packet.manifest.inventory_line+' · '+packet.manifest.version_label));
   const counts=[];
   for(const [category,field]of [['NOTES','notes_ids'],['BANK','bank_ids'],['DRAFT','draft_ids']]){
    const ids=packet.manifest[field],completed=packet.responses.filter(response=>response.category===category&&!response.optional&&ids.includes(response.question_id)&&response.judgment==='meaningful_attempt').length;
    counts.push(completed+'/'+ids.length+' '+category);
    review.append(node(review,'p',category+' required IDs: '+(ids.join(', ')||'(none)')));
   }
   review.append(node(review,'p','Optional IDs: '+(packet.manifest.optional_ids.join(', ')||'(none)')),
    node(review,'p','Imported required judgments: '+counts.join(' · ')),
    node(review,'p','Server-computed scores — Content/notes: '+score(loaded.scores.content_notes)+' · Revision: '+score(loaded.scores.revision)+'. The existing scoring policy is unchanged.'));
   const literal=node(review,'details');literal.append(node(review,'summary','Complete teacher-reviewed packet JSON'));
   const area=node(review,'textarea');area.readOnly=true;area.rows=10;area.value=JSON.stringify(packet,null,2);literal.append(area);review.append(literal);
  }
  const table=node(review,'table'),heading=node(review,'tr');
  for(const title of ['Item','Explicit judgment','Concept','Explanation','Quoted current evidence & note','Feedback to student'])heading.append(node(review,'th',title));table.append(heading);
  for(const response of packet.responses){
   const row=node(review,'tr');
   for(const value of [response.category+' '+response.question_id,response.judgment,response.conceptual_level,response.explanation_level,response.response_excerpt+'\n'+response.evidence_note,response.conceptual_feedback])row.append(node(review,'td',value));
   table.append(row);
  }
  review.append(table,node(review,'h4','Teacher-reviewed student report'),node(review,'p',packet.feedback),node(review,'p',packet.comparison_status,'hint'));
  const approval=node(review,'input');approval.type='checkbox';approval.checked=false;
  review.append(labeled(review,'I confirm that the teacher has already reviewed this whole current-source packet, including every judgment and quoted excerpt.',approval));
  const save=node(review,'button','Save reviewed assessment to the workbook');save.type='button';save.disabled=true;
  const cancel=node(review,'button',first?'Cancel first assessment review':'Cancel fresh current-source review');cancel.type='button';
  const result=node(review,'p','','hint');result.setAttribute('role','status');review.append(save,cancel,result);
  const check=first?node(review,'button','Check saved assessment readback'):null;
  if(check){
   check.type='button';check.hidden=true;review.append(check);
   check.addEventListener('click',async()=>{
    if(closed||inFlight||!attempted||mine!==generation||!scope?.ready||scope.classKey!==packet.class_key||scope.assignmentKey!==packet.assignment_key||scope.sectionId!==packet.section_id||records?.()?.status!=='loaded')return;
    inFlight=true;check.disabled=cancel.disabled=true;result.textContent='Checking the exact saved assessment — readback only…';
    try{
     const r=await request('/api/assessment-review',{method:'POST',cache:'no-store',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'first_readback',packet:copy(packet),review_basis:copy(basis)})});
     const d=await r.json();if(closed||mine!==generation)return;
     if(!r.ok)throw Error(firstFailure(d.error));
     if(!firstSavedProof(d)||d.schema_version!=='hub-assessment-save/v1'||d.status!=='already_saved'||
      Object.entries(basis.source).some(([key,value])=>d.source[key]!==value)||d.external_writes!==0||d.live_model_calls!==0)throw Error('The exact saved assessment could not be verified. Preserve this attempt and check readback again.');
     result.textContent='Saved and read back ✓ · Record '+d.record_id+'. First assessment verified by readback only; no new Save was sent. Nothing was posted to Infinite Campus.';
     save.remove();cancel.remove();check.remove();approval.disabled=true;busy=false;form.inert=false;closed=true;onSaved?.();
    }catch(error){if(!closed&&mine===generation)result.textContent=(error.message||'The saved readback reply was unavailable.')+' This view will not send another Save.';}
    finally{inFlight=false;check.disabled=false;cancel.disabled=first&&attempted;}
   });
  }
  approval.addEventListener('change',()=>{save.disabled=!approval.checked||attempted||closed;});
  cancel.addEventListener('click',()=>{if(inFlight||(first&&attempted))return;closed=true;generation++;busy=false;form.inert=false;review.replaceChildren();state.textContent='Fresh review cancelled. Saved records remain unchanged.';});
  save.addEventListener('click',async()=>{
   if(closed||attempted||inFlight||!approval.checked)return;
   if(mine!==generation||!scope?.ready||scope.classKey!==packet.class_key||scope.assignmentKey!==packet.assignment_key||scope.sectionId!==packet.section_id||records?.()?.status!=='loaded'){
    result.textContent='The selected lesson, section or submission changed. Refresh the fresh current-source review before Save.';return;
   }
   attempted=true;inFlight=true;save.disabled=cancel.disabled=approval.disabled=true;result.textContent='Re-verifying the complete reviewed packet and saving…';
   try{
    const requestId=crypto.randomUUID().replace(/-/g,'').slice(0,32),envelope={request_id:requestId,packet:copy(packet),review_basis:copy(basis)};
    attemptBody=JSON.stringify(envelope);
    const r=await request('/api/assessment-save',{method:'POST',cache:'no-store',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:attemptBody});
    responseEvidence=Number.isInteger(r.status)?{http_status:r.status}:null;
    const d=await r.json();if(typeof d.error==='string')responseEvidence={...responseEvidence,error_code:d.error};if(!r.ok)throw Error(first?firstFailure(d.error):freshFailure(d.error));
    if(d.readback_verified!==true||(first&&!firstSavedProof(d)))throw Error('The save did not read back as expected.');
    if(mine!==generation){onSaved?.();return;}
    result.textContent='Saved and read back ✓ · Record '+d.record_id+'. '+(first?'First assessment saved; no earlier assessment replaced. ':d.superseded?.includes(basis.record_id)?'Prior record retained as superseded history. ':'Saved record verified; prior history preserved. ')+'Nothing was posted to Infinite Campus.';
    save.remove();cancel.remove();check?.remove();approval.disabled=true;busy=false;form.inert=false;if(first)closed=true;onSaved?.();
   }catch(error){if(mine===generation){result.textContent=(error.message||'The Save reply was unavailable.')+' Check the saved record before reopening review; this view will not send another Save.'+(first?' Cancel stays unavailable while this Save is unresolved; keep its evidence and check readback.':'');if(attemptBody)preserveSaveEvidence(review,attemptBody,responseEvidence);if(check)check.hidden=false;}}
   finally{inFlight=false;cancel.disabled=first&&attempted;}
  });
 }

 function renderDraft(draft,file){
  const packet=draft.packet;
  if(!scope?.ready||packet?.class_key!==scope.classKey||packet?.assignment_key!==scope.assignmentKey||packet?.section_id!==scope.sectionId||packet?.source?.file_id!==file?.file_id||!files.some(current=>current.file_id===file.file_id)){
   busy=false;state.textContent='This draft does not match the current lesson, section and submission. Check submissions again.';return;
  }
  const draftGeneration=generation;
  const savedReview=draft.schema_version==='hub-assessment-review/v1';
  let saveInFlight=false,saveAttempted=false,closed=false,attemptBody=null,responseEvidence=null;
  review.replaceChildren();busy=true;
  const lessonTitle=knownAssignments(packet.class_key).find(([key])=>key===packet.assignment_key)?.[1]||'Lesson needs verification';
  review.append(node(review,'h4',(savedReview?'Saved assessment review · ':'Draft assessment · ')+(packet.identity.display_name||'unnamed')+' · '+lessonTitle+' · '+packet.stage+' checkpoint'));
  review.append(node(review,'p','Source: '+(file?.name||packet.source.file_id)+' · version '+packet.source.version+' · last modified '+new Date(packet.source.modified_at).toLocaleString()+' · content hash '+packet.source.sha256.slice(0,12)+'…','hint'));
  if(draft.prompts?.version_label)review.append(node(review,'p','Instructions: live Agenda prompts, '+draft.prompts.version_label,'hint'));
  review.append(node(review,'p',savedReview?'Original saved record: '+draft.review_basis.record_id+'. No model was called. Save uses the existing arithmetic and retains the prior record as superseded history.':'Draft scores — Content/notes: '+score(draft.scores.content_notes)+' · Revision: '+score(draft.scores.revision)+'. Scores are recomputed by the hub from your reviewed judgments when you save.'));
  if(draft.usage?.input_tokens)review.append(node(review,'p','Model '+(draft.usage.model||'')+' · '+draft.usage.input_tokens+' input + '+draft.usage.output_tokens+' output tokens · about '+money(cost(draft.usage))+(draft.cached?' (already paid; recovered from the stored draft)':''),'hint'));
  const table=node(review,'table');const head=node(review,'tr');
  for(const t of ['Item','Judgment','Concept','Explanation','Evidence & note','Feedback to student'])head.append(node(review,'th',t));
  table.append(head);
  const rows=[];
  for(const response of packet.responses){
   const tr=node(review,'tr');
   tr.append(node(review,'td',response.question_id+' · '+response.category+(response.optional?' · optional':'')));
   const judgment=select(review,JUDGMENTS.map(j=>[j,j.replace(/_/g,' ')]),response.judgment);
   const conceptual=select(review,withCurrent(CONCEPTUAL,response.conceptual_level).map(c=>[c,c]),response.conceptual_level);
   // '' means explanation is not rated separately under the four-level scale.
   const explanation=select(review,[['','Not rated separately'],...EXPLANATION.map(e=>[e,e])],response.explanation_level??'');
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
  if(savedReview){
   const changes=node(review,'div',undefined,'assessment-changes');review.append(changes);
   const updateChanges=()=>{
    changes.replaceChildren(node(review,'h4','Changes to review before Save'));
    let count=0;
    const change=(label,old,value)=>{if(old!==value){count++;changes.append(node(review,'p',label+': '+String(old??'(not retained)')+' → '+String(value)));}};
    change('Student report',draft.base.submission.feedback,feedbackBox.value);
    for(const row of rows){
     const old=draft.base.answers.find(a=>a.question_id===row.response.question_id&&a.stage===row.response.category)||{};
     const literal=draft.original_packet?.responses?.find(a=>a.question_id===row.response.question_id&&a.category===row.response.category);
     const label=row.response.category+' '+row.response.question_id;
     change(label+' judgment',literal?.judgment,row.judgment.value);
     change(label+' conceptual level',old.conceptual_level,row.conceptual.value);
     change(label+' explanation level',old.explanation_level,row.explanation.value);
     change(label+' feedback',old.conceptual_feedback,row.feedback.value);
    }
    if(!count)changes.append(node(review,'p','No reviewed judgments or feedback changed. Save will verify the existing record.'));
   };
   for(const row of rows)for(const control of [row.judgment,row.conceptual,row.explanation,row.feedback]){control.addEventListener('change',updateChanges);control.addEventListener('input',updateChanges);}
   feedbackBox.addEventListener('input',updateChanges);updateChanges();
  }
  review.append(node(review,'p','Identity: '+(packet.identity.student_id?('student number '+packet.identity.student_id):'teacher-confirmed roster match')+' · '+packet.identity.identity_basis,'hint'));
  review.append(node(review,'p',packet.comparison_status,'hint'));
  const save=node(review,'button','Save reviewed assessment to the workbook');save.type='button';
  const discard=node(review,'button','Discard this draft view');discard.type='button';
  const saveState=node(review,'p','','hint');saveState.setAttribute('role','status');
  const actionRow=node(review,'div',undefined,'action-row');actionRow.append(save,discard);
  review.append(actionRow,saveState);
  discard.addEventListener('click',()=>{if(saveInFlight)return;closed=true;generation++;busy=false;review.replaceChildren();state.textContent='Review view closed. Saved records and stored drafts remain unchanged.';});
  save.addEventListener('click',async()=>{
   if(saveInFlight||saveAttempted||closed)return;
   if(draftGeneration!==generation||!scope?.ready||scope.classKey!==packet.class_key||scope.assignmentKey!==packet.assignment_key||scope.sectionId!==packet.section_id||records?.()?.status!=='loaded'){
    saveState.textContent='The selected lesson, section or submission changed. Reopen its current draft before saving.';return;
   }
   saveInFlight=true;saveAttempted=true;save.disabled=discard.disabled=true;saveState.textContent='Re-verifying the document and saving…';
   try{
    const responses=rows.map(({response,judgment,conceptual,explanation,feedback})=>({...response,
     judgment:judgment.value,
     response_excerpt:judgment.value==='blank'?'':response.response_excerpt,
     conceptual_level:judgment.value==='blank'?'Evidence unavailable':conceptual.value,
     explanation_level:response.rubric_version==='critical-aspect-4/v1'?'':judgment.value==='blank'?'Evidence unavailable':explanation.value,
     conceptual_feedback:feedback.value}));
    const reviewed={...packet,responses,feedback:feedbackBox.value};
    const requestId=crypto.randomUUID().replace(/-/g,'').slice(0,32);
    const envelope={request_id:requestId,packet:copy(reviewed),...(savedReview?{review_basis:copy(draft.review_basis)}:{})};attemptBody=JSON.stringify(envelope);
    const r=await request('/api/assessment-save',{method:'POST',cache:'no-store',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:attemptBody});
    responseEvidence=Number.isInteger(r.status)?{http_status:r.status}:null;
    const d=await r.json();
    if(typeof d.error==='string')responseEvidence={...responseEvidence,error_code:d.error};
    if(!r.ok)throw Error(explain(d.error));
    if(d.readback_verified!==true)throw Error('The save did not read back as expected.');
    if(draftGeneration!==generation){onSaved?.();return;}
    saveState.textContent='';
    const receipt=node(review,'div',undefined,'assessment-receipt');
    receipt.append(node(review,'h4','Saved and read back ✓'),
     node(review,'p','Record '+d.record_id+' · Content/notes '+(d.content_score??'—')+' (exact '+(d.content_score_exact||'—')+') · Revision '+(d.revision_score??'not due')+(d.revision_score_exact?' (exact '+d.revision_score_exact+')':'')+' · '+d.answers+' answer rows.'),
     node(review,'p',(d.superseded?.length?('Superseded earlier record(s): '+d.superseded.join(', ')+'.'):'No earlier record superseded.')+' Readback verified against the live workbook. Reopen this feedback anytime from the student’s row above; nothing was posted to Infinite Campus.','hint'));
    review.append(receipt);save.remove();discard.remove();
    busy=false;onSaved?.();
   }catch(error){if(draftGeneration===generation){saveState.textContent=(error.message||'The save reply was unavailable.')+' Check the saved record before reopening review; this view will not send another Save.';if(attemptBody)preserveSaveEvidence(review,attemptBody,responseEvidence);}}
   finally{saveInFlight=false;discard.disabled=false;}
  });
 }

 providerStatus().then(()=>{if(scope?.ready)renderForm();});
 return {
  update(next){
   const changed=!scope||scope.assignmentKey!==next.assignmentKey||scope.sectionId!==next.sectionId||scope.classKey!==next.classKey||scope.stage!==next.stage||scope.ready!==next.ready;
   scope=next;
   if(changed){files=[];unresolvedCount=0;deadline=null;}
   if(changed){
    // A different lesson or class invalidates any pending draft view.
    generation++;busy=false;review.replaceChildren();renderForm();
   }
  },
  setFiles(list,nextDeadline){
   const present=(Array.isArray(list)?list:[]).filter(f=>f.status==='present'&&(!f.class_key||f.class_key===scope?.classKey)&&(!f.assignment_key||f.assignment_key===scope?.assignmentKey));
   unresolvedCount=present.filter(f=>!f.section_id||f.section_id===scope?.sectionId&&(f.hold_reason||f.review_reasons?.length||f.discovery_status==='needs_review'||f.stage==='detected')).length;
   const next=present.filter(f=>f.section_id&&f.section_id===scope?.sectionId&&!f.hold_reason&&!f.review_reasons?.length&&f.discovery_status!=='needs_review'&&f.stage!=='detected');
   const changed=signature(next)!==signature(files);
   if(changed){generation++;busy=false;review.replaceChildren();}
   files=next;if(nextDeadline!==undefined)deadline=nextDeadline;
   if(changed||!files.length)renderForm();
  }
 };
}
