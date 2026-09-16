// Coordinates signed-in browser workers. These controls never write school data.
const names={roster:'Roster check',grading:'Completion grading'};
const messages={
 sign_in_required:'Sign in to the dashboard again.',
 browser_worker_busy:'Another run owns this workflow. Wait for its result.',
 browser_claim_expired_or_conflict:'Run ownership expired or changed. Inspect any prior school changes, then refresh status before reserving another run.',
 browser_run_conflict:'The run changed or its ownership expired. Refresh its status before continuing.',
 browser_state_unavailable:'Shared run records are unavailable. Pause school changes.',
 invalid_browser_request:'The run record is invalid. Check its format before retrying.',
 browser_worker_origin_required:'Open these controls on the signed-in hosted dashboard.',
 browser_worker_cookie_required:'Refresh run status in the signed-in browser to establish ownership.',
};
function node(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
function button(text,fn){const b=node('button',text);b.type='button';b.addEventListener('click',fn);return b;}
function validReply(d,workflow){
 return d?.schema_version==='hub-browser-runs/v1'&&d.workflow===workflow&&d.run_enabled===false&&d.server_verified===false&&d.external_writes===0&&typeof d.busy==='boolean'&&typeof d.owned_by_this_browser==='boolean'&&typeof d.recovery_required==='boolean';
}
function workerId(){let id=sessionStorage.getItem('hub-browser-worker');if(!id){id=crypto.randomUUID();sessionStorage.setItem('hub-browser-worker',id);}return id;}
async function request(workflow,payload){
 const worker_id=workerId();
 const response=await fetch('./api/browser-runs'+(payload?'':'?'+new URLSearchParams({workflow,worker_id})),{
  method:payload?'POST':'GET',cache:'no-store',credentials:'same-origin',redirect:'error',
  ...(payload?{headers:{'Content-Type':'application/json'},body:JSON.stringify({workflow,worker_id,...payload})}:{})
 });
 const d=await response.json();
 if(!response.ok){const error=new Error(messages[d.error]||'The request was not confirmed. Refresh status before continuing.');error.status=response.status;error.code=d.error;throw error;}
 if(!validReply(d,workflow))throw Error('The run status could not be verified. Pause school changes.');
 return d;
}
export function showRunInstructions(workflow){
 const title=document.querySelector('#detail-title'),body=document.querySelector('#detail-body');
 const prompt=workflow==='roster'?'Run roster check now':'Run grading now';
 title.textContent=names[workflow];
 const text=node('textarea');text.readOnly=true;text.value=prompt;text.setAttribute('aria-label','Manual run request');
 const status=node('p');status.setAttribute('role','status');
 body.replaceChildren(node('p','Send this request in your connected Codex school-workflow task. Manual requests and scheduled runs use the same signed-in browser workflow.'),text,
  button('Copy manual request',async()=>{try{await navigator.clipboard.writeText(prompt);status.textContent='Copied. Paste it into your Codex school-workflow task to start.';}catch{text.focus();text.select();status.textContent='Copy the selected request and send it in your Codex task.';}}),status,
  node('p','The Mac, Codex, and the signed-in school browser must be available. The dashboard does not run Infinite Campus in the cloud.'));
 document.querySelector('#detail').showModal();
}

export function mountBrowserRuns(container){
 const section=node('section');section.className='browser-runs';
 section.append(node('h3','Manual & scheduled checks'),node('p','Run roster checks and completion grading in your connected Codex task. Shared records keep browser workers from running the same workflow at once.'));
 const actions=node('div');actions.className='action-row';
 actions.append(button('Manual roster check',()=>showRunInstructions('roster')),button('Manual completion grading',()=>showRunInstructions('grading')));section.append(actions);
 const details=node('details');details.className='workflow-details';details.append(node('summary','Automation run records'));
 details.append(node('p','Used by the connected browser worker. Reserving a run does not execute it. A stored browser report confirms record persistence, not independent verification of a school change.'));
 const label=node('label','Workflow '),select=node('select');select.setAttribute('aria-label','Run record workflow');
 for(const [value,name]of Object.entries(names)){const option=node('option',name);option.value=value;select.append(option);}label.append(select);details.append(label);
 const status=node('p');status.setAttribute('role','status');status.className='hint';
 const handle=node('pre');handle.className='review-text';handle.setAttribute('aria-label','Current run record');
 const controls=node('div');controls.className='action-row';
 const refresh=button('Refresh run status',()=>load());
 const start=button('Reserve browser run',()=>mutate('claim'));
 const renew=button('Renew run ownership',()=>mutate('renew'));
 const release=button('Stop for review',()=>mutate('release'));
 controls.append(refresh,start,renew,release);details.append(controls,status,handle);
 const reportLabel=node('label','Private run report'),report=node('textarea');report.className='run-report';report.setAttribute('aria-label','Private run report JSON');report.spellcheck=false;
 report.placeholder='The browser worker supplies its checked source evidence and result here.';reportLabel.append(report);
 const finish=button('Save browser report',()=>mutate('finish'));
 const recover=button('Review report after conflict',()=>recoverReport());
 details.append(reportLabel,finish,recover);section.append(details);container.append(section);
 let workflow='roster',current=null,busy=false,pendingFinish=null,finishConflict=false,recoveryDraft=false,notice='';
 const idKey=()=>`hub-browser-request-${workflow}`;
 const finishKey=()=>`hub-browser-finish-${workflow}`;
 // This tab-lifetime cache preserves retries across refresh, not completed results.
 function cacheFinish(value){sessionStorage.setItem(finishKey(),JSON.stringify(value));sessionStorage.setItem('hub-browser-workflow',workflow);}
 function restoreFinish(){
  pendingFinish=null;finishConflict=false;recoveryDraft=false;report.value='';
  const cached=JSON.parse(sessionStorage.getItem(finishKey())||'null');
  if(cached?.phase==='pending'&&cached.payload?.action==='finish'&&cached.payload.report?.workflow===workflow){
   pendingFinish=cached.payload;finishConflict=cached.conflict===true;report.value=JSON.stringify(pendingFinish.report,null,2);
  }else if(cached?.phase==='draft'&&typeof cached.text==='string'){report.value=cached.text;recoveryDraft=true;}
 }
 function reportNotice(){
  if(finishConflict)return 'The report was rejected for this claim. Use Review report after conflict, inspect the exact school destinations, and refresh ownership before continuing. Do not repeat school changes.';
  if(pendingFinish)return 'Report persistence is not confirmed. This tab retains the exact retry after refresh. Retry Save browser report; do not repeat school changes or close this tab.';
  if(recoveryDraft)return 'This report is an unsaved draft. Inspect the exact school destinations before reserving again. Record what already happened without repeating school changes.';
  return '';
 }
 function render(){
  select.disabled=busy||Boolean(current?.active_claim)||Boolean(pendingFinish);
  refresh.disabled=busy;start.disabled=busy||!current||current.busy||Boolean(pendingFinish);
  renew.disabled=busy||!current?.active_claim||Boolean(pendingFinish);
  release.disabled=busy||!current?.active_claim||Boolean(pendingFinish);
  finish.disabled=busy||finishConflict||(!current?.active_claim&&!pendingFinish);report.disabled=busy||(!current?.active_claim&&!pendingFinish&&!recoveryDraft);report.readOnly=Boolean(pendingFinish);
  recover.hidden=!finishConflict;recover.disabled=busy;
  const own=current?.active_claim;
  const state=own?`Reserved in this tab until ${new Date(own.expires_at).toLocaleString()}. Renew ownership before each school write.`:current?.busy?'Another browser worker is running this workflow.':current?'No active browser worker.':'Shared run status unavailable.';
  const last=current?.last_report?` Last browser report stored ${new Date(current.last_report.stored_at).toLocaleString()}.`:'';
  const recovery=current?.recovery_required?'A prior run stopped or expired. Inspect its school destinations before continuing. ':'';
  status.textContent=notice||reportNotice()||recovery+state+last;
  handle.hidden=!own&&!current?.last_report;
  handle.textContent=JSON.stringify(own?{workflow,claim:own}:{workflow,last_report:current?.last_report},null,2);
 }
 async function load(){
  busy=true;notice='Checking shared run status…';render();
  try{current=await request(workflow);if(current.active_claim)sessionStorage.removeItem(idKey());notice='';}catch(error){current=null;notice=error.message;}
  finally{busy=false;render();}
 }
 async function recoverReport(){
  const text=JSON.stringify(pendingFinish.report,null,2);
  try{cacheFinish({phase:'draft',text});}catch{notice='The retry cache is unavailable. Keep this tab open and preserve the report before continuing.';render();return;}
  report.value=text;pendingFinish=null;finishConflict=false;recoveryDraft=true;current=null;
  await load();
 }
 async function mutate(action){
  let payload;
  try{
   if(action==='claim'){
    let id=sessionStorage.getItem(idKey());if(!id){id=crypto.randomUUID();sessionStorage.setItem(idKey(),id);}payload={action,request_id:id};
   }else if(action==='finish'){
    payload=pendingFinish||{action,claim:current.active_claim,report:JSON.parse(report.value)};
   }else payload={action,claim:current.active_claim,...(action==='release'?{reason:'needs_review'}:{})};
  }catch{notice='Enter a valid JSON run report before saving.';render();return;}
  if(action==='finish'){
   try{cacheFinish({phase:'pending',payload,conflict:false});}catch{notice='This tab could not preserve the exact retry. The report was not submitted. Keep its contents before refreshing.';render();return;}
   pendingFinish=payload;finishConflict=false;recoveryDraft=false;
  }
  busy=true;notice='Saving shared run state…';render();
  try{
   const result=await request(workflow,payload);current=result;
   if(action==='claim')sessionStorage.removeItem(idKey());
   if(action==='finish'||result.completed){sessionStorage.removeItem(idKey());sessionStorage.removeItem(finishKey());pendingFinish=null;finishConflict=false;recoveryDraft=false;report.value='';}
   if(action==='release')sessionStorage.removeItem(idKey());
   notice=action==='finish'?'Browser report saved and read back from private storage. School actions are reported by the browser worker.':result.completed?'This request already has a stored report. Do not repeat its school actions.':action==='release'?'Run stopped for review. Prior results and baselines were preserved.':'';
   document.dispatchEvent(new CustomEvent('browser-run-updated',{detail:{workflow}}));
  }catch(error){
   notice=error.message;
   if(action==='claim'&&error.code==='browser_claim_expired_or_conflict')sessionStorage.removeItem(idKey());
   // Unknown responses retain the exact payload; definitive rejection allows review.
   if(action==='finish'&&error.status===400){
    cacheFinish({phase:'draft',text:report.value});pendingFinish=null;recoveryDraft=true;
    notice+=' The report is not stored; edit it and save again.';
   }else if(action==='finish'&&error.status===409){
    finishConflict=true;cacheFinish({phase:'pending',payload,conflict:true});notice=reportNotice();
   }else if(action==='finish')notice+=' '+reportNotice();
  }finally{busy=false;render();}
 }
 report.addEventListener('input',()=>{if(recoveryDraft){try{cacheFinish({phase:'draft',text:report.value});}catch{notice='Draft edits are not retained across refresh. Keep this tab open.';render();}}});
 select.addEventListener('change',()=>{workflow=select.value;current=null;notice='';try{sessionStorage.setItem('hub-browser-workflow',workflow);restoreFinish();}catch{notice='The saved retry could not be restored. Pause school changes and preserve this tab.';}load();});
 try{const selected=sessionStorage.getItem('hub-browser-workflow');if(Object.hasOwn(names,selected)){workflow=selected;select.value=selected;}restoreFinish();}catch{notice='The retry cache is unavailable. Keep this tab open while a report is pending.';}
 render();load();
}
