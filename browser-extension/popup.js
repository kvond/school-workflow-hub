'use strict';
chrome.runtime.sendMessage({type:'companion_popup_status'}).then(state=>{
 document.getElementById('source').textContent=state.source_origin||'Not configured';
 document.getElementById('destination').textContent=state.destination_origin||'Not configured';
 document.getElementById('status').textContent=state.configured?(state.pending_count?'A manual grade review is open in Infinite Campus. This is separate from scheduled automatic entry.':state.entry_lists?`${state.entry_lists} manually sent Hub list${state.entry_lists===1?' is':'s are'} waiting. Use these only if you intentionally started a manual entry; the weekday run handles its own assignment list.`:'Ready. Weekday automatic entry is separately controlled below.'):'Package this helper with the exact dashboard and Infinite Campus origins before installation.';
}).catch(()=>{document.getElementById('status').textContent='Companion unavailable. Reload the extension and review again.';});

const morningBox=document.getElementById('morning-enabled'),morningText=document.getElementById('morning-status');
let legacyRecoveryMode=false,receiptRecoveryMode=false,sourceRecoveryMode=false,readbackRecoveryMode=false,typingRecoveryMode=false,recoveryContext='';
function morningShow(s){
 if(s?.error){morningText.textContent='Morning settings unavailable.';return;}
 morningBox.checked=s.enabled;
 morningText.textContent=(s.enabled?'Weekday mornings enabled. ':'Weekday mornings off. ')+(s.report?`Last attempt: ${s.report.day}, ${s.report.code.replaceAll('_',' ')}. See the Hub morning summary.`:'No morning attempt yet.');
 const next=document.getElementById('morning-next'),report=s.report||{},items=Array.isArray(report.items)?report.items:[];
 const sourceReadUnavailable=report.code==='source_changed'&&items.at(-1)?.code==='source_changed'
  &&typeof items.at(-1)?.assignment==='string'&&items.at(-1).assignment.includes(' · Source check: registered teacher read timed out');
 const sourceStop=sourceReadUnavailable?'a teacher source read timeout':'a source change';
 if(sourceReadUnavailable)morningText.textContent=(s.enabled?'Weekday mornings enabled. ':'Weekday mornings off. ')+`Last attempt: ${report.day}, teacher source read timed out. See the Hub morning summary.`;
 const uncertain=new Set(['save_unconfirmed','readback_incomplete','readback_mismatch','uncertain_blocked']);
 const affected=[...new Set([...(s.assignments||[]),...items.filter(item=>uncertain.has(item.code)).map(item=>item.assignment)].filter(Boolean))];
 const verifiedAttempt=!s.blocked&&s.verified?.run_id&&s.verified.run_id===report.run_id;
 const currentScoresVerified=verifiedAttempt&&s.verified.scope==='current_mapped_assignments';
 if(currentScoresVerified){
  morningText.textContent=s.verified.recovery==='checked_typing_after_helper_update'
   ?`Current mapped IC grades were checked by ${s.verified.by} at ${s.verified.at}. The ${s.verified.prior_day} run remains incomplete after typing stopped before Save; this and earlier attempts keep their history.`
   :s.verified.recovery==='checked_readback_after_helper_update'
   ?`Current mapped IC grades were checked by ${s.verified.by} at ${s.verified.at}. The ${s.verified.prior_day} run remains incomplete after a saved-result mismatch; its Fill and Save history is unchanged.`
   :s.verified.recovery==='source_changed_before_entry'
   ?`Current mapped IC grades were checked by ${s.verified.by} at ${s.verified.at}. The ${s.verified.prior_day} run remains stopped at ${sourceStop} and incomplete.`
   :s.verified.recovery==='partial_receipt'
   ?`Current mapped IC scores were checked by ${s.verified.by} at ${s.verified.at}. The ${s.verified.prior_day} run remains incomplete and its entry receipt unconfirmed.`
   :`Current mapped IC scores were checked by ${s.verified.by} at ${s.verified.at}. The ${s.verified.prior_day} attempt remains unconfirmed.`;
  next.textContent=s.verified.recovery==='checked_typing_after_helper_update'?(s.can_run_now?'Use Run all mapped assignments now for one new pass with the next helper version. It rereads Hub and IC, keeps higher grades and verifies each saved result; it does not complete the stopped run.':'This checked-typing verification cannot be reused for this run and helper version. The stopped run remains incomplete.'):s.verified.recovery==='checked_readback_after_helper_update'?(s.can_run_now?'Use Run all mapped assignments now for one new pass with the updated helper. It rereads Hub and IC, keeps higher grades, and verifies each result; it does not complete the stopped run.':'This checked-readback verification cannot be reused for this run and helper version. The stopped run remains incomplete.'):s.can_run_now&&s.verified.recovery==='source_changed_before_entry'?'Use Run all mapped assignments now for one new full source pass after the repair. It rereads Hub and IC, keeps higher grades, and verifies each result; it does not complete the stopped run.':s.verified.recovery==='source_changed_before_entry'?'This source-change verification cannot be reused for this run and helper version. The stopped run remains incomplete.':s.can_run_now?'This allows a new class run. Use Run all mapped assignments now to reread current Hub and IC values, save only blanks or higher scores, and verify each result.':s.verified.recovery==='partial_receipt'?'A new pass is unavailable for this run and helper version. The interrupted run remains incomplete.':'This verification allows a future class run; it does not confirm the old attempt.';
 }
 else if(verifiedAttempt){
  morningText.textContent=`Weekday mornings enabled. The interrupted ${report.day} attempt was marked verified by ${s.verified.by} at ${s.verified.at}.`;
  next.textContent=s.can_run_now?`This cleared the recovery hold for ${affected.join('; ')||'the interrupted item'}; it did not complete the full class run. Run all mapped assignments now to re-read the current Hub and IC values, save only blanks or higher scores, and verify readback.`:`This cleared the recovery hold for ${affected.join('; ')||'the interrupted item'}; it did not complete the full class run. The next 7:00 a.m. run will process mapped assignments automatically. Review the gradebook afterward.`;
 }
 else if(s.busy)next.textContent='The class run is active. Leave the Hub and IC tabs open; it is handling mapped assignments without manual review clicks.';
 else if(s.can_verify_current_typing)next.textContent='The run stopped while entering a score, before Save. After the helper update, check the saved grades in every current mapped IC assignment and mark them checked below. This permits one new pass; the stopped run remains incomplete.';
 else if(s.can_verify_current_readback)next.textContent='The run stopped after its saved-result check did not match. After the helper update, check the saved grades in every current mapped IC assignment and mark them checked below. This permits one new pass; it does not erase the earlier Fill or Save history or complete the stopped run.';
 else if(s.blocked)next.textContent=`Automatic entry is paused. Next: open ${affected.length?affected.join('; '):'the affected assignment named in the Hub morning summary'} in IC, inspect its scores, correct/save only if IC is wrong, then reload and verify. Use “Mark verified” only after checking; uncertain saves are not retried.`;
 else if(s.can_verify_legacy)next.textContent=`The ${report.day} attempt has no assignment results and its failure cause is unknown. Check every currently mapped assignment in IC and resolve any unsaved or uncertain scores before marking current scores verified. This allows a new run; it does not confirm the old attempt.`;
 else if(s.can_verify_current_source_change)next.textContent=sourceReadUnavailable?'The registered teacher source read timed out before this item entered grades. The current source list could not be confirmed. After source access is ready, check the saved grades in every current mapped IC assignment and mark them checked below. This permits one new full source pass; the stopped run remains incomplete.':'The run stopped when its source comparison changed before this item entered grades. After the source repair, check the saved grades in every current mapped IC assignment and mark them checked below. This permits one new full source pass; the stopped run remains incomplete.';
 else if(s.can_verify_current_receipt)next.textContent='The run compared some assignments, but the Hub could not confirm its entry receipt. Check the saved scores in every current mapped IC assignment before marking them checked. This allows one new pass; it does not complete the interrupted run.';
 else if(s.can_run_now&&report.code==='finished')next.textContent='The previous pass finished. Run all mapped assignments now to re-read Hub and IC, save only blanks or higher scores, and verify each result once more today.';
 else if(report.code==='finished')next.textContent='The run finished. Review the IC gradebook when convenient and adjust any score you want to change.';
 else if(s.can_run_now&&report.code==='missed_noon')next.textContent='The morning run did not start and no scores were attempted. You can run all mapped assignments once now; it will reread current Hub and IC values and verify each save.';
 else if(s.can_run_now)next.textContent='Run all mapped assignments now to reread current Hub and IC scores across classes, save only blanks or higher scores, and verify each result. Keep one signed-in Hub tab and one signed-in IC tab open.';
 else if(sourceReadUnavailable)next.textContent='The registered teacher source read timed out. This assignment remains incomplete. Check Hub source access and the saved grades in IC before any new pass; uncertain saves are not retried.';
 else if(report.code&&report.code!=='missed_noon'){
  const stuck=items.filter(item=>!['read_back','nothing_to_enter','no_scores'].includes(item.code));
  next.textContent=stuck.length?`The run stopped at ${stuck.map(item=>`${item.assignment} (${item.code.replaceAll('_',' ')})`).join('; ')}. No completion is confirmed for those items. Open the named assignment in IC, check its scores and the Hub morning summary, then correct/save only if needed; do not retry automatically.`:globalThis.HubMorning.preflightText(report.preflight)||'The run did not finish and this receipt contains no assignment results. Its original failure cause was not retained. No IC score comparison or save is confirmed; a delivered receipt does not prove grades ran.';
 }else next.textContent=s.enabled?'At 7:00 a.m., the helper will handle eligible assignments across classes and verify each save. You can review the gradebook afterward.':'Turn on weekday mornings here to enable automatic score entry at 7:00 a.m.';
 legacyRecoveryMode=s.can_verify_legacy===true;
 receiptRecoveryMode=s.can_verify_current_receipt===true;
 sourceRecoveryMode=s.can_verify_current_source_change===true;
 readbackRecoveryMode=s.can_verify_current_readback===true;
 typingRecoveryMode=s.can_verify_current_typing===true;
 const context=typingRecoveryMode?`typing:${report.day}:${report.run_id}`:readbackRecoveryMode?`readback:${report.day}:${report.run_id}`:sourceRecoveryMode?`source:${report.day}:${report.run_id}`:receiptRecoveryMode?`receipt:${report.day}:${report.run_id}`:legacyRecoveryMode?`current:${report.day}:${report.run_id}`:s.blocked?`interrupted:${report.day}:${report.run_id}`:'';
 if(context!==recoveryContext){document.getElementById('morning-checked').checked=false;document.getElementById('morning-clear').disabled=true;recoveryContext=context;}
 document.getElementById('morning-recovery').hidden=!s.blocked&&!legacyRecoveryMode&&!receiptRecoveryMode&&!sourceRecoveryMode&&!readbackRecoveryMode&&!typingRecoveryMode;
 document.getElementById('morning-recovery-label').textContent=typingRecoveryMode?'The updated helper is ready, and I checked the saved grades in every current mapped IC assignment after the typing stop. This allows one new pass; the stopped run stays incomplete.':readbackRecoveryMode?'The updated helper is ready, and I checked the saved grades in every current mapped IC assignment. This allows one new pass; the stopped run and its Fill and Save history remain unchanged.':sourceRecoveryMode?`${sourceReadUnavailable?'Teacher source access':'The source repair'} is ready, and I checked the saved grades in every current mapped IC assignment. This allows one new source pass; the stopped run remains incomplete.`:receiptRecoveryMode?'I checked the saved scores in every current mapped IC assignment. This allows one new pass; it does not complete the interrupted run.':legacyRecoveryMode?'I checked every current mapped IC assignment and resolved any unsaved or uncertain scores. This allows a new run; it does not confirm the old attempt.':'I checked the interrupted run in IC and resolved its unsaved or uncertain scores.';
 document.getElementById('morning-clear').textContent=typingRecoveryMode?'Mark current mapped grades checked — allow one updated-helper pass':readbackRecoveryMode?'Mark current mapped grades checked — allow one updated-helper pass':sourceRecoveryMode?'Mark current mapped grades checked — allow one new source pass':receiptRecoveryMode?'Mark current scores checked — allow one new run':legacyRecoveryMode?'Mark current scores verified — allow a new run':'Mark verified — allow future mornings';
 const runNow=document.getElementById('morning-run-now');runNow.hidden=!s.can_run_now;runNow.disabled=!!s.busy;
 const retry=document.getElementById('morning-retry');retry.hidden=!s.can_retry;retry.disabled=!!s.busy;
 if(s.verified&&!s.blocked&&!verifiedAttempt)morningText.textContent+=s.verified.recovery==='source_changed_before_entry'?` Current mapped IC grades checked by ${s.verified.by}; the ${s.verified.prior_day} run remains stopped at ${sourceStop} and incomplete.`:s.verified.scope==='current_mapped_assignments'?` Current mapped IC scores checked by ${s.verified.by}; the ${s.verified.prior_day} attempt remains unconfirmed.`:` Verified by ${s.verified.by} at ${s.verified.at}.`;
 if(s.recovery_error)morningText.textContent=s.recovery_error;
 if(s.busy)morningText.textContent='The class run is active. Keep the Hub and Infinite Campus tabs open.';
}
chrome.runtime.sendMessage({type:'morning_status'}).then(morningShow).catch(()=>morningShow({error:true}));
morningBox.addEventListener('change',async e=>{if(!e.isTrusted)return;morningBox.disabled=true;try{morningShow(await chrome.runtime.sendMessage({type:'morning_setting',enabled:morningBox.checked}));}finally{morningBox.disabled=false;}});
const checked=document.getElementById('morning-checked'),clear=document.getElementById('morning-clear');
checked.addEventListener('change',e=>{if(e.isTrusted)clear.disabled=!checked.checked||!document.getElementById('morning-note').value.trim();});
document.getElementById('morning-note').addEventListener('input',()=>{clear.disabled=!checked.checked||!document.getElementById('morning-note').value.trim();});
clear.addEventListener('click',async e=>{if(!e.isTrusted||!checked.checked)return;clear.disabled=true;morningShow(await chrome.runtime.sendMessage({type:'morning_clear',confirmed:true,current_mapped_confirmed:legacyRecoveryMode||receiptRecoveryMode||sourceRecoveryMode||readbackRecoveryMode||typingRecoveryMode,receipt_recovery_confirmed:receiptRecoveryMode,source_recovery_confirmed:sourceRecoveryMode,readback_recovery_confirmed:readbackRecoveryMode,typing_recovery_confirmed:typingRecoveryMode,note:document.getElementById('morning-note').value}));checked.checked=false;});

document.getElementById('morning-retry').addEventListener('click',async e=>{
 if(!e.isTrusted)return;e.target.disabled=true;
 try{morningShow(await chrome.runtime.sendMessage({type:'morning_retry'}));}catch{morningShow({error:true});}
});
document.getElementById('morning-run-now').addEventListener('click',async e=>{
 if(!e.isTrusted)return;e.target.disabled=true;
 try{morningShow(await chrome.runtime.sendMessage({type:'morning_run_now'}));}catch{morningShow({error:true});}
});
setInterval(()=>chrome.runtime.sendMessage({type:'morning_status'}).then(morningShow).catch(()=>{}),2000);
