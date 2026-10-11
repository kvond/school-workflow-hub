'use strict';
chrome.runtime.sendMessage({type:'companion_popup_status'}).then(state=>{
 document.getElementById('source').textContent=state.source_origin||'Not configured';
 document.getElementById('destination').textContent=state.destination_origin||'Not configured';
 document.getElementById('status').textContent=state.configured?(state.pending_count?'A manual grade review is open in Infinite Campus. This is separate from scheduled automatic entry.':state.entry_lists?`${state.entry_lists} manually sent Hub list${state.entry_lists===1?' is':'s are'} waiting. Use these only if you intentionally started a manual entry; the weekday run handles its own assignment list.`:'Ready. Weekday automatic entry is separately controlled below.'):'Package this helper with the exact dashboard and Infinite Campus origins before installation.';
}).catch(()=>{document.getElementById('status').textContent='Companion unavailable. Reload the extension and review again.';});

const morningBox=document.getElementById('morning-enabled'),morningText=document.getElementById('morning-status');
let legacyRecoveryMode=false,recoveryContext='';
function morningShow(s){
 if(s?.error){morningText.textContent='Morning settings unavailable.';return;}
 morningBox.checked=s.enabled;
 morningText.textContent=(s.enabled?'Weekday mornings enabled. ':'Weekday mornings off. ')+(s.report?`Last attempt: ${s.report.day}, ${s.report.code.replaceAll('_',' ')}. See the Hub morning summary.`:'No morning attempt yet.');
 const next=document.getElementById('morning-next'),report=s.report||{},items=Array.isArray(report.items)?report.items:[];
 const uncertain=new Set(['save_unconfirmed','readback_incomplete','readback_mismatch','uncertain_blocked']);
 const affected=[...new Set([...(s.assignments||[]),...items.filter(item=>uncertain.has(item.code)).map(item=>item.assignment)].filter(Boolean))];
 const verifiedAttempt=!s.blocked&&s.verified?.run_id&&s.verified.run_id===report.run_id;
 const currentScoresVerified=verifiedAttempt&&s.verified.scope==='current_mapped_assignments';
 if(currentScoresVerified){
  morningText.textContent=`Current mapped IC scores were checked by ${s.verified.by} at ${s.verified.at}. The ${s.verified.prior_day} attempt remains unconfirmed.`;
  next.textContent=s.can_run_now?'This allows a new class run. Use Run all mapped assignments now to reread current Hub and IC values, save only blanks or higher scores, and verify each result.':'This verification allows a future class run; it does not confirm the old attempt.';
 }
 else if(verifiedAttempt){
  morningText.textContent=`Weekday mornings enabled. The interrupted ${report.day} attempt was marked verified by ${s.verified.by} at ${s.verified.at}.`;
  next.textContent=s.can_run_now?`This cleared the recovery hold for ${affected.join('; ')||'the interrupted item'}; it did not complete the full class run. Run all mapped assignments now to re-read the current Hub and IC values, save only blanks or higher scores, and verify readback.`:`This cleared the recovery hold for ${affected.join('; ')||'the interrupted item'}; it did not complete the full class run. The next 7:00 a.m. run will process mapped assignments automatically. Review the gradebook afterward.`;
 }
 else if(s.busy)next.textContent='The class run is active. Leave the Hub and IC tabs open; it is handling mapped assignments without manual review clicks.';
 else if(s.blocked)next.textContent=`Automatic entry is paused. Next: open ${affected.length?affected.join('; '):'the affected assignment named in the Hub morning summary'} in IC, inspect its scores, correct/save only if IC is wrong, then reload and verify. Use “Mark verified” only after checking; uncertain saves are not retried.`;
 else if(s.can_verify_legacy)next.textContent=`The ${report.day} attempt has no assignment results and its failure cause is unknown. Check every currently mapped assignment in IC and resolve any unsaved or uncertain scores before marking current scores verified. This allows a new run; it does not confirm the old attempt.`;
 else if(s.can_run_now&&report.code==='finished')next.textContent='The previous pass finished. Run all mapped assignments now to re-read Hub and IC, save only blanks or higher scores, and verify each result once more today.';
 else if(report.code==='finished')next.textContent='The run finished. Review the IC gradebook when convenient and adjust any score you want to change.';
 else if(s.can_run_now&&report.code==='missed_noon')next.textContent='The morning run did not start and no scores were attempted. You can run all mapped assignments once now; it will reread current Hub and IC values and verify each save.';
 else if(s.can_run_now)next.textContent='Run all mapped assignments now to reread current Hub and IC scores across classes, save only blanks or higher scores, and verify each result. Keep one signed-in Hub tab and one signed-in IC tab open.';
 else if(report.code&&report.code!=='missed_noon'){
  const stuck=items.filter(item=>!['read_back','nothing_to_enter','no_scores'].includes(item.code));
  next.textContent=stuck.length?`The run stopped at ${stuck.map(item=>`${item.assignment} (${item.code.replaceAll('_',' ')})`).join('; ')}. No completion is confirmed for those items. Open the named assignment in IC, check its scores and the Hub morning summary, then correct/save only if needed; do not retry automatically.`:globalThis.HubMorning.preflightText(report.preflight)||'The run did not finish and this receipt contains no assignment results. Its original failure cause was not retained. No IC score comparison or save is confirmed; a delivered receipt does not prove grades ran.';
 }else next.textContent=s.enabled?'At 7:00 a.m., the helper will handle eligible assignments across classes and verify each save. You can review the gradebook afterward.':'Turn on weekday mornings here to enable automatic score entry at 7:00 a.m.';
 legacyRecoveryMode=s.can_verify_legacy===true;
 const context=legacyRecoveryMode?`current:${report.day}:${report.run_id}`:s.blocked?`interrupted:${report.day}:${report.run_id}`:'';
 if(context!==recoveryContext){document.getElementById('morning-checked').checked=false;document.getElementById('morning-clear').disabled=true;recoveryContext=context;}
 document.getElementById('morning-recovery').hidden=!s.blocked&&!legacyRecoveryMode;
 document.getElementById('morning-recovery-label').textContent=legacyRecoveryMode?'I checked every current mapped IC assignment and resolved any unsaved or uncertain scores. This allows a new run; it does not confirm the old attempt.':'I checked the interrupted run in IC and resolved its unsaved or uncertain scores.';
 document.getElementById('morning-clear').textContent=legacyRecoveryMode?'Mark current scores verified — allow a new run':'Mark verified — allow future mornings';
 const runNow=document.getElementById('morning-run-now');runNow.hidden=!s.can_run_now;runNow.disabled=!!s.busy;
 const retry=document.getElementById('morning-retry');retry.hidden=!s.can_retry;retry.disabled=!!s.busy;
 if(s.verified&&!s.blocked&&!verifiedAttempt)morningText.textContent+=s.verified.scope==='current_mapped_assignments'?` Current mapped IC scores checked by ${s.verified.by}; the ${s.verified.prior_day} attempt remains unconfirmed.`:` Verified by ${s.verified.by} at ${s.verified.at}.`;
 if(s.recovery_error)morningText.textContent=s.recovery_error;
 if(s.busy)morningText.textContent='The class run is active. Keep the Hub and Infinite Campus tabs open.';
}
chrome.runtime.sendMessage({type:'morning_status'}).then(morningShow).catch(()=>morningShow({error:true}));
morningBox.addEventListener('change',async e=>{if(!e.isTrusted)return;morningBox.disabled=true;try{morningShow(await chrome.runtime.sendMessage({type:'morning_setting',enabled:morningBox.checked}));}finally{morningBox.disabled=false;}});
const checked=document.getElementById('morning-checked'),clear=document.getElementById('morning-clear');
checked.addEventListener('change',e=>{if(e.isTrusted)clear.disabled=!checked.checked||!document.getElementById('morning-note').value.trim();});
document.getElementById('morning-note').addEventListener('input',()=>{clear.disabled=!checked.checked||!document.getElementById('morning-note').value.trim();});
clear.addEventListener('click',async e=>{if(!e.isTrusted||!checked.checked)return;clear.disabled=true;morningShow(await chrome.runtime.sendMessage({type:'morning_clear',confirmed:true,current_mapped_confirmed:legacyRecoveryMode,note:document.getElementById('morning-note').value}));checked.checked=false;});

document.getElementById('morning-retry').addEventListener('click',async e=>{
 if(!e.isTrusted)return;e.target.disabled=true;
 try{morningShow(await chrome.runtime.sendMessage({type:'morning_retry'}));}catch{morningShow({error:true});}
});
document.getElementById('morning-run-now').addEventListener('click',async e=>{
 if(!e.isTrusted)return;e.target.disabled=true;
 try{morningShow(await chrome.runtime.sendMessage({type:'morning_run_now'}));}catch{morningShow({error:true});}
});
setInterval(()=>chrome.runtime.sendMessage({type:'morning_status'}).then(morningShow).catch(()=>{}),2000);
