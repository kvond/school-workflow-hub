/* Uses only the rendered score panel in the signed-in, same-origin school tab. */
(function(root){
 'use strict';
 const norm=s=>String(s||'').replace(/\s+/g,' ').trim();
 const freeze=v=>{if(v&&typeof v==='object'){Object.values(v).forEach(freeze);Object.freeze(v);}return v;};
 const copy=v=>JSON.parse(JSON.stringify(v));
 const fail=code=>{throw Error(code);};
 function parseReference(value){
  const m=/^IC_RESOURCE:(\d+);SCORE_GROUP:(\d+);TERM:(\d+);TASK:(\d+);CODE:([A-Za-z0-9_-]+)$/.exec(value||'');
  return m?{resource:m[1],group:m[2],term:m[3],task:m[4],code:m[5]}:null;
 }
 function visible(e){return Boolean(e&&e.getClientRects().length&&!e.closest('[hidden],[aria-hidden="true"]'));}
 function one(values,code='destination_ambiguous'){if(values.length!==1)fail(code);return values[0];}
 function label(e){return norm(e.getAttribute('aria-label')||e.textContent);}
 function buttons(scope,text){return [...scope.querySelectorAll('button')].filter(e=>visible(e)&&label(e)===text);}
 async function until(fn,ms=10000){const end=Date.now()+ms;while(Date.now()<end){const value=fn();if(value)return value;await new Promise(r=>setTimeout(r,100));}fail('destination_not_ready');}
 function surface(){
  const f=one([...document.querySelectorAll('iframe#main-workspace')].filter(visible),'assignment_frame_required');
  let d,u;try{d=f.contentDocument;u=new URL(f.contentWindow.location.href);}catch{fail('school_sign_in_required');}
  if(!d||u.origin!==location.origin)fail('school_sign_in_required');
  const score=one([...d.querySelectorAll('ic-multiscore')].filter(visible),'score_panel_required');
  const outer=new URL(location.href),p=u.searchParams,op=outer.searchParams;
  const section=p.get('classroomSectionID'),term=p.get('classroomTermID');
  const path=/\/resource\/(\d+)\/view\(secondaryRouteOutlet:multiscore\/assignmentEditor\/sectionID\/(\d+)\/objectSectionID\/(\d+)\/calendarID\/(\d+)\/structureID\/(\d+)\)/.exec(u.pathname);
  if(!path||path[1]!==path[3]||path[2]!==section||op.get('classroomSectionID')!==section||op.get('classroomTermID')!==term)fail('destination_context_changed');
  // Student Summary overlays render inside ic-multiscore and can linger closed
  // with layout boxes, so the score-panel reads must not count their content.
  const summaryDialogs=[...score.querySelectorAll('[role="dialog"]')].filter(g=>[...g.querySelectorAll('h2')].some(h=>norm(h.textContent).startsWith('Student Summary')));
  const inPanel=e=>!summaryDialogs.some(g=>g.contains(e));
  const unsupported=reason=>{console.warn('School Workflow Hub: unsupported_gradebook ('+reason+')');fail('unsupported_gradebook');};
  // IC can change score-field styling across releases. The generated per-row
  // ID, accessible label, and later row/student checks identify destinations;
  // a CSS class is presentation and must not gate discovery. The bulk-fill
  // control remains excluded because it does not use the per-row ID grammar.
  const inputs=[...score.querySelectorAll('input[aria-label][id^="as_alignmentMultiScore_"]')].filter(e=>visible(e)&&inPanel(e));
  if(!inputs.length||inputs.length>100)unsupported('score_input_count');
  const groups=new Set(inputs.map(e=>/^as_alignmentMultiScore_(\d+)-\d+$/.exec(e.id)?.[1]));
  if(groups.size!==1||groups.has(undefined))unsupported('score_group_identifiers');
  const titles=[...score.querySelectorAll('h2')].filter(e=>visible(e)&&inPanel(e));
  if(titles.length!==1)unsupported('assignment_heading_count');
  const titleNode=titles[0];
  const heading=norm(titleNode.textContent),title=norm(titleNode.firstElementChild?.textContent||heading.split(/\s+\d{4}-\d+\s/)[0]);
  const points=[...score.querySelectorAll('th,[role="columnheader"]')].filter(e=>visible(e)&&inPanel(e)).map(e=>norm(e.textContent)).find(t=>/^MP \(10 pts\)/.test(t));
  if(!points)fail('unsupported_points_scale');
  const saves=buttons(score,'Save').filter(inPanel);
  if(saves.length!==1)unsupported('save_button_count');
  const save=saves[0];
  const context={section_id:section,term_id:term,year:op.get('year'),school_id:op.get('schoolID'),calendar_id:path[4],assignment_id:path[1],title,points:10};
  if(Object.entries(context).some(([k,v])=>k!=='points'&&(!v||k!=='title'&&!/^\d+$/.test(v))))fail('destination_context_changed');
  return {frame:f,doc:d,score,inputs,save,context,group:[...groups][0],path:u.href};
 }
 // IC's SPA can report the destination URL before Angular has rendered its
 // per-student score rows. Retry only that transient structural state; other
 // safety failures (wrong frame, sign-in, changed context) remain immediate.
 async function waitSurface(ms=60000){
  const end=Date.now()+ms;let last;
  while(Date.now()<end){
   try{return surface();}catch(error){
    if(error?.message!=='unsupported_gradebook')throw error;
    last=error;await new Promise(resolve=>setTimeout(resolve,100));
   }
  }
  throw last||Error('unsupported_gradebook');
 }
 const identityCache=new Map();
 function currentScore(input){const v=input.value.trim();if(v==='')return null;if(!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(v)||!Number.isFinite(Number(v))||Number(v)<0||Number(v)>10)fail('destination_grade_requires_review');return Number(v);}
 function renderedFlags(row){
  const risky=['Missing','Late','Incomplete','Cheated','Exempt','Drop Score'];
  const labels=[...row.querySelectorAll('[aria-label],[title]')].filter(visible).flatMap(e=>[norm(e.getAttribute('aria-label')),norm(e.getAttribute('title'))]);
  return risky.filter(flag=>labels.includes(flag));
 }
 async function identify(input,s){
  const name=norm(input.getAttribute('aria-label').split(' Score input for ')[1]);if(!name)fail('unsupported_gradebook');
  const key=JSON.stringify([s.context.school_id,s.context.year,s.context.calendar_id,s.context.section_id,name]);
  // Cache only within this live document; a reload requires a new visible identity read.
  const cached=identityCache.get(key);if(cached?.doc===s.doc)return cached.identity;
  const row=input.closest('tr');if(!row)fail('unsupported_gradebook');
  const index=row.getAttribute('data-kendo-grid-item-index');
  if(!/^\d+$/.test(index||''))fail('unsupported_gradebook');
  // Kendo keeps the student column in a separate locked table with the same row index.
  const rowParts=[...s.score.querySelectorAll('tr[data-kendo-grid-item-index]')].filter(r=>r.getAttribute('data-kendo-grid-item-index')===index);
  const link=one(rowParts.flatMap(r=>[...r.querySelectorAll('a')]).filter(a=>visible(a)&&norm(a.textContent)===name),'student_identity_unverified');
  link.click();
  // Closed Student Summary panels can linger in the document with layout boxes,
  // so several such dialogs may exist at once and render their bodies late.
  // Accept only one consistent reading: every panel naming this student must
  // agree, and the frame route must name the same person for this exact section
  // and assignment.
  const summaryPanels=()=>[...s.doc.querySelectorAll('h2')].filter(e=>visible(e)&&norm(e.textContent).startsWith('Student Summary')).map(h=>h.closest('[role="dialog"]')).filter(Boolean);
  const read=await until(()=>{
   let route;try{route=/\/\/tertiaryRouteOutlet:studentInfoSidePanel\/multiscore\/(\d+)\/(\d+)\/1\/(\d+)\)/.exec(new URL(s.frame.contentWindow.location.href).pathname);}catch{return null;}
   if(!route||route[1]!==s.context.section_id||route[3]!==s.context.assignment_id)return null;
   const readings=new Set();
   for(const panel of summaryPanels()){
    const links=[...panel.querySelectorAll('a[href]')].filter(a=>{const u=new URL(a.href);return visible(a)&&u.origin===location.origin&&u.pathname==='/campus/sis/navigation/parent/student.ToolSet'&&u.searchParams.has('selectedPersonID');});
    const numberHeadings=[...panel.querySelectorAll('h4')].filter(h=>visible(h)&&norm(h.textContent)==='Number');
    const nameHeadings=[...panel.querySelectorAll('h3')].filter(h=>visible(h)&&norm(h.textContent).replace(/\s*\(/g,'(')===name.replace(/\s*\(/g,'('));
    if(links.length>1||numberHeadings.length>1||nameHeadings.length>1)fail('student_identity_unverified');
    if(!links.length||!numberHeadings.length||!nameHeadings.length)continue;
    const person_id=new URL(links[0].href).searchParams.get('selectedPersonID');
    const number=/^Number\s*#(\d+)$/.exec(norm(numberHeadings[0].parentElement.textContent))?.[1];
    if(!number||!/^\d+$/.test(person_id||'')||route[2]!==person_id)continue;
    readings.add(JSON.stringify([number,person_id]));
   }
   if(readings.size>1)fail('student_identity_unverified');
   return readings.size?JSON.parse([...readings][0]):null;
  });
  const number=read[0],person_id=read[1];
  // Close every Student Summary dialog; lingering closed copies ignore the
  // click. Done means the student route left the frame URL and the same
  // assignment score panel is current again.
  await until(()=>{
   try{if(JSON.stringify(surface().context)===JSON.stringify(s.context))return true;}catch{}
   for(const panel of summaryPanels())for(const button of buttons(panel,'Close'))button.click();
   return false;
  });
  const identity={student_id:number,person_id,display_name:name};identityCache.set(key,{doc:s.doc,identity});return identity;
 }
 let relevantIds=null;
 async function snapshot(requireSaved=true){
  let s=surface();const saved=norm(s.save.getAttribute('title'))==='All scores have been saved.';if(requireSaved&&!saved)fail('destination_not_saved');
  const start=JSON.stringify(s.context),students=[];
  for(const original of s.inputs){
   const inputLabel=norm(original.getAttribute('aria-label')),identity=await identify(original,s);
   s=surface();if(JSON.stringify(s.context)!==start)fail('destination_context_changed');
   const input=one(s.inputs.filter(e=>norm(e.getAttribute('aria-label'))===inputLabel),'student_identity_unverified');
   students.push({...identity,input_label:inputLabel,current_score:currentScore(input),flags:renderedFlags(input.closest('tr'))});
  }
  return [{context:s.context,students,saved}];
 }
 async function inspect(){try{surface();return {code:'destination_unverified'};}catch(e){return {code:['gradebook_required','school_sign_in_required'].includes(e.message)?e.message:'unsupported_gradebook'};}}
 function sameSource(review,data){
  if(data?.schema_version!=='hub-grading-records/v1'||data.provenance!=='source_read'||data.external_writes!==0||data.source_revision!==review.source_revision||!Array.isArray(data.submissions))fail('source_changed');
  for(const row of review.rows){
   const raw=data.submissions.find(v=>v.record_id===row.record_id);if(!raw)fail('source_changed');
   for(const k of ['class_key','section_id','student_id','assignment_key','ic_assignment_id','content_points_possible','revision_points_possible'])if(String(raw[k]??'')!==String(row[k]??''))fail('source_changed');
   if(String(raw.hold_reason||'')!==row.hold_reason||raw.status!==row.status)fail('source_changed');
   for(const k of ['content_score','revision_score'])if(row.scores[k].value!==null&&(raw[k]!==row.scores[k].value||String(raw[k+'_exact']||'')!==row.scores[k].exact))fail('source_changed');
  }
  return root.HubPostingProtocol.validateReview({...copy(review),checked_at:data.checked_at});
 }
 function mappings(review,dest){
  const s=surface(),ctx=dest[0].context,result=[];
  for(const row of review.rows){
   const ref=parseReference(row.ic_assignment_id);
   if(!ref||ref.resource!==ctx.assignment_id||ref.group!==s.group||ref.term!==ctx.term_id||row.section_id!==ctx.section_id||String(row.content_points_possible)!=='10')continue;
   // Match the complete rendered score label, including the grading task's label/code.
   const student=dest[0].students.find(v=>v.student_id===row.student_id);
   if(!student||!student.input_label.startsWith(ref.code+' MP: Marking Period Score input for '))continue;
   // The supported MP task is checked from its rendered progress-score header.
   const markers=[...s.score.querySelectorAll('[aria-label]')].map(e=>e.getAttribute('aria-label'));
   if(!markers.includes(`progressScore_${ref.term}_${ref.task}_${ctx.section_id}`))continue;
   result.push({record_id:row.record_id,component:'content_score',student_id:row.student_id,context:copy(ctx)});
  }
  return result;
 }
 function node(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
 function verifyBindings(plan,current){
  const markers=[...current.score.querySelectorAll('[aria-label]')].map(e=>e.getAttribute('aria-label'));
  for(const item of plan.rows.filter(row=>row.target)){
   const source=plan.source.rows.find(row=>row.record_id===item.record_id),ref=parseReference(source?.ic_assignment_id);
   if(!ref||ref.resource!==current.context.assignment_id||ref.group!==current.group||ref.term!==current.context.term_id||!markers.includes(`progressScore_${ref.term}_${ref.task}_${current.context.section_id}`)||!item.target.input_label.startsWith(ref.code+' MP: Marking Period Score input for '))fail('destination_context_changed');
  }
 }
 function selectAssignmentReview(review,s){
  const rows=review.rows.filter(row=>{const ref=parseReference(row.ic_assignment_id);return ref&&ref.resource===s.context.assignment_id&&ref.group===s.group&&ref.term===s.context.term_id&&row.section_id===s.context.section_id;});
  if(!rows.length)fail('destination_mapping_required');
  return root.HubPostingProtocol.validateReview({...copy(review),rows:copy(rows),record_ids:rows.map(row=>row.record_id)});
 }
 let active=null;
 async function prepare(review,{hubCall,onResult:sendResult}){
  // Result delivery crosses the extension bridge and can reject after this
  // review expired or the worker restarted; the review is over either way, so
  // report the delivery failure without an unhandled rejection.
  const onResult=result=>Promise.resolve().then(()=>sendResult(result)).catch(error=>{console.warn('School Workflow Hub: result_delivery_failed ('+(['review_expired','companion_unavailable'].includes(error?.message)?error.message:'unknown')+')');});
  if(active)fail('companion_busy');
  root.HubPostingProtocol.validateReview(review);
  let s;
  try{s=surface();review=selectAssignmentReview(review,s);}catch(error){
   const codes=['assignment_frame_required','score_panel_required','school_sign_in_required','destination_context_changed','unsupported_gradebook','unsupported_points_scale','destination_mapping_required'];
   console.warn('School Workflow Hub: '+(codes.includes(error.message)?error.message:'assignment_check_failed'));
   throw error;
  }
  relevantIds=new Set(review.rows.map(r=>r.student_id).filter(Boolean));
  const host=node('div');host.style.cssText='position:fixed;inset:16px 16px auto auto;z-index:2147483647;width:min(620px,calc(100vw - 32px));max-height:90vh;overflow:auto;background:white;color:#15281c;border:2px solid #315a42;border-radius:12px;padding:22px;box-shadow:0 8px 40px #0005;font:15px/1.5 system-ui';
  host.setAttribute('role','dialog');host.setAttribute('aria-label','School Workflow Hub grade posting review');
  const heading=node('h2','Review grades for this Infinite Campus assignment'),status=node('p','Reading the current assignment and matching student numbers…');host.append(heading,status);document.body.append(host);
  active={host};
  const close=()=>{host.remove();active=null;relevantIds=null;};
  const cancel=node('button','Close review');cancel.type='button';cancel.addEventListener('click',()=>{if(!active?.working){const finished=active?.started;close();if(!finished)onResult({code:'needs_review',counts:{checked:0,changed:0,needs_review:0},external_writes:0});}});host.append(cancel);
  (async()=>{
   try{
    const fresh=sameSource(review,await hubCall('source',{})),dest=await snapshot();
    const plan=root.HubPostingPlan.buildPlan(fresh,mappings(fresh,dest),dest);
    status.textContent=`${dest[0].context.title} · ${plan.counts.ready} blank grades to post · ${plan.counts.already_matches} already match · ${plan.counts.needs_review} need review.`;
    const table=node('table');table.style.width='100%';const header=node('tr');for(const t of ['Student','Recorded','Current','Action'])header.append(node('th',t));table.append(header);
    for(const row of plan.rows.filter(r=>r.target)){const tr=node('tr');for(const text of [row.target.display_name,String(row.desired_score),row.previous_score===null?'Blank':String(row.previous_score),row.disposition.replaceAll('_',' ')])tr.append(node('td',text));table.append(tr);}host.insertBefore(table,cancel);
    const note=node('p','Only the current assignment is in this review. Matching grades are skipped; existing different grades, held work and unmapped components are left for review.');host.insertBefore(note,cancel);
    if(!plan.counts.ready&&!plan.counts.already_matches){status.textContent+=' No verified posting targets were found.';return;}
    const confirmLabel=node('label'),confirm=node('input');confirm.type='checkbox';confirmLabel.append(confirm,document.createTextNode(' I checked the student, assignment, points scale and grade flags shown in Infinite Campus.'));host.insertBefore(confirmLabel,cancel);
    const go=node('button',plan.counts.ready?'Post reviewed blank grades':'Verify matching grades');go.type='button';go.disabled=true;confirm.addEventListener('change',()=>{go.disabled=!confirm.checked;});host.insertBefore(go,cancel);
    go.addEventListener('click',async event=>{
     if(!event.isTrusted||!confirm.checked||active?.working)return;active.working=true;active.started=true;go.disabled=true;cancel.disabled=true;confirm.disabled=true;
     status.textContent='Checking the workbook and shared run ownership…';
     const context=JSON.stringify(dest[0].context);
     let executionStarted=false,lastResult=null;
     const publish=result=>{const code=result.code==='checked_verified'?'already_matches':result.code;onResult({code,counts:result.counts||result.report?.counts,external_writes:result.external_writes,...(result.possible_changes?{possible_changes:true}:{})});};
     try{
      executionStarted=true;
      const result=await root.HubPostingExecutor.executeReviewedPlan(plan,{
       refreshSource:async()=>sameSource(review,await hubCall('source',{})),snapshot:async()=>{verifyBindings(plan,surface());return snapshot();},hubCall,
       fill:async (row,lease)=>{
        const current=surface();if(JSON.stringify(current.context)!==context)fail('destination_context_changed');verifyBindings(plan,current);
        const input=one(current.inputs.filter(i=>norm(i.getAttribute('aria-label'))===row.target.input_label));
        const id=await identify(input,current);if(id.student_id!==row.target.student_id||id.person_id!==row.target.person_id||currentScore(input)!==row.previous_score||renderedFlags(input.closest('tr')).length)fail('destination_changed');
        if(!lease||Date.now()>=Date.parse(lease.expires_at))fail('run_ownership_expired');
        const setter=Object.getOwnPropertyDescriptor(current.doc.defaultView.HTMLInputElement.prototype,'value').set;
        input.focus();setter.call(input,String(row.desired_score));input.dispatchEvent(new current.doc.defaultView.Event('input',{bubbles:true}));input.dispatchEvent(new current.doc.defaultView.Event('change',{bubbles:true}));input.blur();
        if(currentScore(input)!==row.desired_score)fail('grade_fill_unconfirmed');
       },
       save:async lease=>{
        const current=surface();if(JSON.stringify(current.context)!==context)fail('destination_context_changed');verifyBindings(plan,current);
        const beforeSave=await snapshot(false),expected=copy(dest);
        for(const row of plan.rows.filter(r=>r.disposition==='ready')){const student=expected[0].students.find(v=>v.student_id===row.target.student_id&&v.person_id===row.target.person_id);if(!student)fail('destination_changed');student.current_score=row.desired_score;}
        if(JSON.stringify(beforeSave.map(v=>({...v,saved:true})))!==JSON.stringify(expected))fail('unreviewed_grade_edits');
        const fresh=surface();verifyBindings(plan,fresh);if(!lease||Date.now()>=Date.parse(lease.expires_at))fail('run_ownership_expired');fresh.save.click();await until(()=>norm(surface().save.getAttribute('title'))==='All scores have been saved.');
       },
       reloadAndSnapshot:async()=>{const current=surface(),old=current.doc;verifyBindings(plan,current);current.frame.contentWindow.location.reload();await until(()=>{try{return current.frame.contentDocument!==old&&current.frame.contentDocument.readyState==='complete';}catch{return false;}},20000);await until(()=>{try{return surface().inputs.length;}catch{return false;}},20000);identityCache.clear();verifyBindings(plan,surface());return snapshot();}
      });
      lastResult=result;
      const count=result.counts||result.report?.counts;
      if(!count)fail('result_unconfirmed');
      const code=result.code==='checked_verified'?'already_matches':result.code;
      status.textContent=code==='posted_verified'?`${count.changed} grades saved and verified after reloading Infinite Campus.`:code==='already_matches'?`${count.checked} grades already match. No grade changes were needed.`:code==='report_unconfirmed'?'The result report is not confirmed. Do not repeat the grade changes.':'Stopped for review. Check Infinite Campus before another attempt.';
      if(result.retry_payload){const retry=node('button','Retry saving the result report');retry.type='button';retry.onclick=async e=>{if(!e.isTrusted)return;retry.disabled=true;try{const retried=await root.HubPostingExecutor.retryReport(result.retry_payload,{hubCall});status.textContent=retried.code==='report_unconfirmed'?'The report is still unconfirmed. Do not repeat grade changes.':'Result report saved.';publish(retried);}catch{status.textContent='The report is still unconfirmed. Do not repeat grade changes.';}retry.disabled=false;};host.insertBefore(retry,cancel);}
      publish(result);
     }catch{status.textContent='Stopped for review. Verify any changes in Infinite Campus before trying again.';try{if(lastResult)publish(lastResult);else onResult({code:'needs_review',counts:{checked:0,changed:0,needs_review:1},external_writes:executionStarted?null:0,...(executionStarted?{possible_changes:true}:{})});}catch{}}
     finally{active.working=false;cancel.disabled=false;}
    });
   }catch(e){
    const codes=['source_changed','source_unavailable','review_expired','destination_not_ready','destination_not_saved','student_identity_unverified','unsupported_gradebook','destination_context_changed','invalid_destination','invalid_review_request'];
    console.warn('School Workflow Hub: '+(codes.includes(e.message)?e.message:'review_check_failed'));
    status.textContent=e.message==='source_changed'?'The workbook changed. Refresh Page 2 and open a new review.':'The current score panel could not be verified. No grades were changed.';onResult({code:e.message==='source_changed'?'source_changed':'needs_review',counts:{checked:0,changed:0,needs_review:1},external_writes:0});}
  })();
  return {code:'review_opened'};
 }
 // The score-panel readers, shared with the Hub v2 list entry (ic-entry.js).
 const panel=Object.freeze({surface,waitSurface,renderedFlags,until,norm});
 const api={parseReference,sameSource,selectAssignmentReview,inspect,prepare,panel};root.HubIcAdapter=Object.freeze(api);
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(globalThis);
