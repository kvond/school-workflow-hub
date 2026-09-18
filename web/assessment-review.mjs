// Assessment drafting & review: the runtime provider only judges and writes
// feedback; the hub derives the manifest from the deck itself, verifies every
// quoted excerpt against a fresh capture, and recomputes all scores in Python
// at save time. Drafts store nothing; only the reviewed Save writes.
const JUDGMENTS=['meaningful_attempt','blank','printed_copy','review'];
const CONCEPTUAL=['Developing','Almost there','Proficient','Evidence unavailable'];
const EXPLANATION=['Names an idea','Connects ideas','Explains how or why','Evidence unavailable'];
const KINDS=['roster_match','local_student_number','teacher_declared_roster'];
const FAILURES={
 provider_not_configured:'The Anthropic runtime key is not configured; drafting is unavailable until the key dependency is resolved.',
 runtime_key_missing:'The runtime key secret is not reaching the service.',
 runtime_key_malformed:'The stored runtime key is not a valid API key; store a freshly copied key.',
 provider_auth_failed:'The provider rejected the runtime key. Store a freshly copied key.',
 provider_rate_limited:'The provider rate-limited this request. Try again in a minute.',
 provider_request_refused:'The provider refused the request. Check the workspace spend limit and credit balance.',
 provider_unavailable:'The assessment provider could not be reached. Nothing was drafted or saved.',
 provider_draft_invalid:'The drafted output failed validation and was discarded. Generate again.',
 provider_prompts_unavailable:'The packaged teacher prompts could not be read.',
 assessment_manifest_missing:'This deck has no marker-inventory manifest header, so its requirements cannot be verified. It needs manual reconciliation and was not auto-assessed.',
 assessment_manifest_unparsed:'The deck’s manifest header disagrees with its own item lists. Review the deck before assessing.',
 assessment_identity_unresolved:'No verified identity yet: enter a student ID, or roster fields to derive a learner key.',
 assessment_evidence_not_in_source:'A quoted excerpt no longer matches the document. Generate a fresh draft.',
 assessment_not_ready:'At least one item is marked "review"; resolve every review judgment before saving.',
 submission_changed_reassess:'The document changed after this draft was generated. Generate a fresh draft from the current version.',
 assessment_record_conflict:'An identical assessment of this exact version already exists in the workbook.',
 assessment_assignment_unknown:'That assignment key is not in the workbook’s Assignments tab.',
 assessment_readback_failed:'The saved rows did not read back as expected. Check the workbook before retrying.',
 assessment_save_busy:'Another save for this student and assignment is still running. Wait, then retry.',
 grading_records_not_connected:'The private grading workbook is not connected.',
 sign_in_required:'Your dashboard sign-in expired. Reload the page and sign in again.'};
const explain=code=>FAILURES[code]||('The request could not be completed ('+code+'). Nothing was saved.');

function node(root,tag,text,cls){const n=root.ownerDocument.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.setAttribute('class',cls);return n;}
function labeled(root,text,control){const wrap=node(root,'label',undefined,'field');wrap.append(node(root,'span',text,'hint'),control);return wrap;}
function select(root,options,value){const s=node(root,'select');for(const [v,label]of options){const o=node(root,'option',label);o.value=v;s.append(o);}if(value!==undefined)s.value=value;return s;}
function input(root,value,placeholder){const i=node(root,'input');i.type='text';i.value=value||'';if(placeholder)i.placeholder=placeholder;return i;}
const score=s=>s&&s.exact_value!==null&&s.exact_value!==undefined?(Math.round(s.value*100)/100)+' / '+s.out_of+' (exact '+s.exact_value+')':'not due';

async function learnerKey(classKey,sectionId,seed){
 const bytes=new TextEncoder().encode('roster|'+classKey+'|'+sectionId+'|'+seed.trim());
 const digest=await crypto.subtle.digest('SHA-256',bytes);
 return 'roster:'+[...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,'0')).join('');
}

export function mountAssessmentReview(container,{fetcher,records,onSaved}={}){
 const request=fetcher||((url,options)=>fetch(url,options));
 container.append(node(container,'h3','Assessment drafting & review'));
 container.append(node(container,'p','Generate reads the student’s current document and drafts judgments with quoted evidence (one billable Claude call, about $0.11). You review and can change any judgment, level or feedback; Save re-verifies the document and recomputes every score in the hub from your reviewed judgments.','hint'));
 const state=node(container,'p','','hint');state.setAttribute('role','status');
 const form=node(container,'div',undefined,'assessment-form');
 const review=node(container,'div');review.setAttribute('aria-live','polite');
 container.append(state,form,review);
 let scope=null,files=[],generation=0,providerReady=null;

 async function providerStatus(){
  try{
   const r=await request('/api/assessment-run',{cache:'no-store',credentials:'same-origin',redirect:'error'});
   const d=await r.json();
   providerReady=r.ok&&d.configured===true?{model:d.model}:{blocked:d.dependency||d.error||'provider_not_configured'};
  }catch{providerReady={blocked:'provider_unavailable'};}
 }

 function knownSections(classKey){
  const data=records?.()?.data;const seen=new Map();
  for(const row of data?.submissions||[])if(row.class_key===classKey&&row.section_id)seen.set(row.section_id,row.section_name||row.section_id);
  return [...seen.entries()];
 }
 function knownAssignments(classKey){
  const data=records?.()?.data;
  return (data?.assignments||[]).filter(a=>a.class_key===classKey).map(a=>[a.assignment_key,a.assignment_key+' · '+a.assignment_title]);
 }

 function renderForm(){
  form.replaceChildren();
  if(!scope?.ready){state.textContent='Load a lesson to draft assessments for its submissions.';return;}
  if(!files.length){state.textContent='No monitored submissions yet. Refresh or scan the submission monitoring panel above.';return;}
  state.textContent=providerReady?.blocked?explain(providerReady.blocked):'Provider ready ('+(providerReady?.model||'model pending')+').';
  const fileSelect=select(form,files.map(f=>[f.file_id,f.name+' · '+(f.stage||'')]),files[0].file_id);
  const assignments=knownAssignments(scope.classKey);
  const assignment=assignments.length?select(form,assignments):input(form,'','class:CODE assignment key');
  const stage=select(form,[['final','final (NOTES + BANK due, revisions due)'],['initial','initial (NOTES only due)']],'final');
  const stageRef=input(form,'','e.g. Teacher plan 2026-09-16: ‘Write Revisions and turn in.’');
  const sections=knownSections(scope.classKey);
  const sectionId=input(form,sections[0]?.[0]||'','section id');
  const sectionName=input(form,sections[0]?.[1]||'','section name');
  const displayName=input(form,'','e.g. Jordan P.');
  const studentId=input(form,'','IC student number, if verified');
  const kind=select(form,KINDS.map(k=>[k,k]),'roster_match');
  const seed=input(form,'','First|Last|school email (used only without a student number)');
  const basis=input(form,'','how this identity was verified');
  const generate=node(form,'button','Generate draft (1 billable call)');generate.type='button';
  form.append(
   labeled(form,'Submission',fileSelect),labeled(form,'Assignment',assignment),
   labeled(form,'Checkpoint stage',stage),labeled(form,'Stage reference (teacher declaration this checkpoint rests on)',stageRef),
   labeled(form,'Section id',sectionId),labeled(form,'Section name',sectionName),
   labeled(form,'Student display name',displayName),labeled(form,'Student number (optional)',studentId),
   labeled(form,'Identity kind',kind),labeled(form,'Roster fields for learner key (optional)',seed),
   labeled(form,'Identity basis',basis),
   node(form,'p','Blank identity fields are filled automatically from the file’s newest workbook row when one exists.','hint'),
   generate);
  generate.addEventListener('click',async()=>{
   const mine=++generation;review.replaceChildren();
   const chosen=files.find(f=>f.file_id===fileSelect.value);
   generate.disabled=true;state.textContent='Capturing the document and drafting judgments… this can take a minute.';
   try{
    const identity={student_id:studentId.value.trim(),student_id_kind:kind.value,
     learner_key:'',display_name:displayName.value.trim(),identity_basis:basis.value.trim()};
    if(!identity.student_id&&seed.value.trim()){
     identity.learner_key=await learnerKey(scope.classKey,sectionId.value.trim(),seed.value);
     identity.identity_basis=(identity.identity_basis?identity.identity_basis+' ':'')+'learner_key = sha256(roster|'+scope.classKey+'|'+sectionId.value.trim()+'|'+seed.value.trim()+').';
    }
    const body={file_id:fileSelect.value,class_key:scope.classKey,stage:stage.value,
     stage_reference:stageRef.value.trim(),assignment_key:assignment.value.trim(),
     section_id:sectionId.value.trim(),section_name:sectionName.value.trim(),identity};
    const r=await request('/api/assessment-run',{method:'POST',cache:'no-store',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();
    if(mine!==generation)return;
    if(!r.ok)throw Error(explain(d.error));
    if(d.schema_version!=='hub-assessment-provider/v1'||d.status!=='draft_ready'||d.external_writes!==0)throw Error('The draft could not be verified.');
    state.textContent='Draft ready for your review. Nothing has been saved.';
    renderDraft(d,chosen);
   }catch(error){if(mine===generation)state.textContent=error.message||'Drafting failed; nothing was saved.';}
   finally{if(mine===generation)generate.disabled=false;}
  });
 }

 function renderDraft(draft,file){
  review.replaceChildren();
  const packet=draft.packet;
  review.append(node(review,'h4','Draft assessment · '+(packet.identity.display_name||'unnamed')+' · '+packet.assignment_key+' · stage '+packet.stage));
  review.append(node(review,'p','Source: '+(file?.name||packet.source.file_id)+' · version '+packet.source.version+' · last modified '+new Date(packet.source.modified_at).toLocaleString()+' · content hash '+packet.source.sha256.slice(0,12)+'…','hint'));
  review.append(node(review,'p','Draft scores — Content/notes: '+score(draft.scores.content_notes)+' · Revision: '+score(draft.scores.revision)+'. Scores are recomputed by the hub from your reviewed judgments when you save.'));
  if(draft.usage?.input_tokens)review.append(node(review,'p','Model '+(draft.usage.model||'')+' · '+draft.usage.input_tokens+' input + '+draft.usage.output_tokens+' output tokens.','hint'));
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
  review.append(node(review,'p','Identity: '+(packet.identity.student_id?('student number '+packet.identity.student_id):'roster learner key')+' · '+packet.identity.identity_basis,'hint'));
  review.append(node(review,'p',packet.comparison_status,'hint'));
  const save=node(review,'button','Save reviewed assessment to the workbook');save.type='button';
  const saveState=node(review,'p','','hint');saveState.setAttribute('role','status');
  review.append(save,saveState);
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
     node(review,'p',(d.superseded?.length?('Superseded earlier record(s): '+d.superseded.join(', ')+'.'):'No earlier record superseded.')+' Readback verified against the live workbook.','hint'));
    review.append(receipt);save.remove();
    onSaved?.();
   }catch(error){saveState.textContent=error.message||'The save failed; check the workbook state before retrying.';save.disabled=false;}
  });
 }

 providerStatus().then(()=>{if(scope?.ready)renderForm();});
 return {
  update(next){scope=next;if(!next.ready){generation++;files=[];review.replaceChildren();}renderForm();},
  setFiles(list){files=Array.isArray(list)?list.filter(f=>f.status==='present'):[];renderForm();}
 };
}
