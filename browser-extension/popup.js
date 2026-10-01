'use strict';
chrome.runtime.sendMessage({type:'companion_popup_status'}).then(state=>{
 document.getElementById('source').textContent=state.source_origin||'Not configured';
 document.getElementById('destination').textContent=state.destination_origin||'Not configured';
 document.getElementById('status').textContent=state.configured?(state.pending_count?'A grade review is pending in Infinite Campus. Keep both tabs open.':state.entry_lists?`${state.entry_lists} Hub list${state.entry_lists===1?'':'s'} waiting. Open that assignment's score panel in Infinite Campus and click “Hub helper”.`:'Ready. On the Hub, open an assignment’s IC scores and click “Send to the Chrome helper”. Morning entry is separately controlled below.'):'Package this helper with the exact dashboard and Infinite Campus origins before installation.';
}).catch(()=>{document.getElementById('status').textContent='Companion unavailable. Reload the extension and review again.';});

const morningBox=document.getElementById('morning-enabled'),morningText=document.getElementById('morning-status');
function morningShow(s){
 if(s?.error){morningText.textContent='Morning settings unavailable.';return;}
 morningBox.checked=s.enabled;
 morningText.textContent=(s.enabled?'Weekday mornings enabled. ':'Weekday mornings off. ')+(s.report?`Last attempt: ${s.report.day}, ${s.report.code.replaceAll('_',' ')}. See the Hub morning summary.`:'No morning attempt yet.');
 document.getElementById('morning-recovery').hidden=!s.blocked;
}
chrome.runtime.sendMessage({type:'morning_status'}).then(morningShow).catch(()=>morningShow({error:true}));
morningBox.addEventListener('change',async e=>{if(!e.isTrusted)return;morningBox.disabled=true;try{morningShow(await chrome.runtime.sendMessage({type:'morning_setting',enabled:morningBox.checked}));}finally{morningBox.disabled=false;}});
const checked=document.getElementById('morning-checked'),clear=document.getElementById('morning-clear');
checked.addEventListener('change',e=>{if(e.isTrusted)clear.disabled=!checked.checked;});
clear.addEventListener('click',async e=>{if(!e.isTrusted||!checked.checked)return;clear.disabled=true;morningShow(await chrome.runtime.sendMessage({type:'morning_clear',confirmed:true}));checked.checked=false;});
