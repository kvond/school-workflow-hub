'use strict';
chrome.runtime.sendMessage({type:'companion_popup_status'}).then(state=>{
 document.getElementById('source').textContent=state.source_origin||'Not configured';
 document.getElementById('destination').textContent=state.destination_origin||'Not configured';
 document.getElementById('status').textContent=state.configured?(state.pending_count?'A grade review is pending in Infinite Campus. Keep both tabs open.':state.entry_lists?`${state.entry_lists} Hub list${state.entry_lists===1?'':'s'} waiting. Open that assignment's score panel in Infinite Campus and click “Hub helper”.`:'Ready. On the Hub, open an assignment’s IC scores and click “Send to the Chrome helper”. Nothing runs on a schedule.'):'Package this helper with the exact dashboard and Infinite Campus origins before installation.';
}).catch(()=>{document.getElementById('status').textContent='Companion unavailable. Reload the extension and review again.';});
