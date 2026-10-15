/* Hub v2 list entry in the signed-in Infinite Campus tab. When the teacher has
 * sent an assignment's IC list from the Teaching Hub and has one assignment's
 * score panel open, a "Hub helper" button offers to review it. The review
 * shows every row and what the helper will do; only a trusted click after the
 * teacher's checkbox types, saves, reloads and reads the scores back. The
 * rules live in entry.js; this file only reads and touches the page. */
(() => {
 'use strict';
 const E=globalThis.HubIcEntry,A=globalThis.HubIcAdapter,C=globalThis.HubPostingConfig,P=globalThis.HubPostingProtocol;
 if(!E||!A?.panel||!P.origin(C.IC_ORIGIN)||location.origin!==C.IC_ORIGIN||window.top!==window)return;
 const {surface,waitSurface,renderedFlags,until,norm}=A.panel;
 const SAVED='All scores have been saved.';
 const LABEL=/^(.+?) MP: Marking Period Score input for (.+)$/;
 const fail=code=>{throw Error(code);};
 const ask=(op,extra={})=>chrome.runtime.sendMessage({type:'entry_ic',op,...extra});

 // The open score panel as the plan sees it: each student's label, name,
 // current score (or an odd value) and IC flags.
 function read(requireSaved){
  const s=surface(),saved=norm(s.save.getAttribute('title'))===SAVED;
  if(requireSaved&&!saved)fail('ic_not_saved');
  const codes=new Set();
  const rows=s.inputs.map(input=>{
   const label=norm(input.getAttribute('aria-label')),m=LABEL.exec(label),row=input.closest('tr');
   if(!m||!row)fail('unsupported_gradebook');
   codes.add(m[1]);
   const raw=input.value.trim(),number=/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(raw)?Number(raw):null;
   return {label,name:norm(m[2]),score:raw===''?null:number,other:raw!==''&&number===null?raw.slice(0,40):null,flags:renderedFlags(row)};
  });
  if(codes.size!==1)fail('unsupported_gradebook');
  return {s,snapshot:{context:{...s.context},rows,saved}};
 }
 const sameContext=(s,plan)=>JSON.stringify(s.context)===JSON.stringify(plan.context);
 function ports(plan){
  return {
   snapshot:async requireSaved=>read(requireSaved).snapshot,
   fill:async change=>{
    const {s,snapshot}=read(false);if(!sameContext(s,plan))fail('ic_changed');
    const inputs=s.inputs.filter(i=>norm(i.getAttribute('aria-label'))===change.label),row=snapshot.rows.find(r=>r.label===change.label);
    if(inputs.length!==1||!row||row.score!==change.before||row.other!==null||row.flags.length)fail('ic_changed');
    const input=inputs[0],view=s.doc.defaultView,setter=Object.getOwnPropertyDescriptor(view.HTMLInputElement.prototype,'value').set;
    input.focus();setter.call(input,String(change.value));
    input.dispatchEvent(new view.Event('input',{bubbles:true}));input.dispatchEvent(new view.Event('change',{bubbles:true}));input.blur();
    if(input.value.trim()!==String(change.value))fail('typing_not_taken');
   },
   save:async()=>{
    const {s}=read(false);if(!sameContext(s,plan))fail('ic_changed');
    s.save.click();
    await until(()=>{try{return norm(surface().save.getAttribute('title'))===SAVED;}catch{return false;}},20000);
   },
   reload:async()=>{
    const current=surface(),old=current.doc;
    current.frame.contentWindow.location.reload();
    await until(()=>{try{return current.frame.contentDocument!==old&&current.frame.contentDocument.readyState==='complete';}catch{return false;}},20000);
    await until(()=>{try{return surface().inputs.length;}catch{return false;}},20000);
    return read(false).snapshot;
   },
  };
 }

 const MESSAGES={
  ic_not_saved:'This assignment has unsaved changes in Infinite Campus. Save or reload it first.',
  students_dont_match:'Most students in this IC assignment aren’t on this Hub list. Pick the right list, or open the right class in IC.',
  several_sections:'These students match more than one section on the Hub list. Check the list and the assignment.',
  column_required:'Choose Notes or Revision for this IC assignment.',
  list_expired:'This Hub list is more than 3 hours old. Send it again from the Hub.',
  unsupported_points_scale:'This assignment isn’t scored out of 10 points, so the helper won’t enter Hub scores in it.',
 };
 const say=error=>MESSAGES[error?.message]||'Open one assignment’s score panel in Infinite Campus (the one where scores are typed), then review again.';
 function node(tag,props={},...children){
  const n=document.createElement(tag);
  for(const [k,v]of Object.entries(props)){if(v==null||v===false)continue;if(k==='text')n.textContent=v;else if(k==='style')n.style.cssText=v;else n.setAttribute(k,v===true?'':v);}
  for(const c of children.flat())if(c!=null&&c!==false)n.append(c instanceof Node?c:String(c));
  return n;
 }
 const time=iso=>new Date(iso).toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'});
 const listName=list=>`${list.class_name} · ${list.week} · ${list.assignment}`;
 const shown=v=>v===null?'blank':String(v);
 function hubCell(item){
  if(!item.hub)return '—';
  if(item.hub.kind==='excused')return 'Excused';
  if(item.hub.kind==='check')return 'check by hand';
  if(item.hub.kind==='none')return '—';
  return item.value===null?'N/A':String(item.value);
 }
 function doing(item){
  if(item.action==='fill')return `enter ${item.value}`;
  if(item.action==='raise')return `raise to ${item.value}`;
  if(item.action==='keep')return `keep (${item.why})`;
  if(item.action==='skip')return `nothing (${item.why})`;
  return `leave: ${item.why}`;
 }
 const ORDER={fill:0,raise:1,leave:2,keep:3,skip:4};

 let launcher=null,host=null,working=false;

 // Worker-only unattended path; no window/page message can invoke it.
 const M=globalThis.HubMorning;
 function sessionProblem(){
  const docs=[document];
  for(const f of document.querySelectorAll('iframe#main-workspace')){
   try{if(f.contentWindow.location.origin!==location.origin)return 'panel_unusable';if(f.contentDocument)docs.push(f.contentDocument);else return 'panel_unusable';}catch{return 'panel_unusable';}
  }
  return docs.some(d=>[...d.querySelectorAll('input[type="password"],form[action*="login"],form[action*="signin"]')].some(e=>e.getClientRects().length))?'signed_out':null;
 }
 chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(!M||sender.id!==chrome.runtime.id||sender.tab||!['morning_session','morning_probe','morning_execute'].includes(message?.type))return;
  const problem=sessionProblem();if(problem){reply({ok:false,code:problem,not_started:true});return;}
  if(message.type==='morning_session'){reply({ok:true});return;}
  if(message.type==='morning_probe'){
   (async()=>{try{
    if(document.hidden||working||host)throw Error('panel_unusable');
    await waitSurface();read(true);reply({ok:true});
   }catch(error){
    const known=new Set(['ic_not_saved','unsupported_gradebook','unsupported_points_scale','signed_out']);
    reply({ok:false,code:known.has(error?.message)?error.message:'panel_unusable'});
   }})();return true;
  }
  if(working||host||document.hidden){reply({ok:false,code:'panel_unusable',not_started:true});return;}
  working=true;
  const ping=setInterval(()=>chrome.runtime.sendMessage({type:'morning_authorize',run_id:message.run_id,nonce:message.nonce}).catch(()=>{}),20000);
  (async()=>{
   const authorized=async()=>{
    const problem=sessionProblem();if(problem)throw Error(problem);
    if(document.hidden)throw Error('panel_unusable');
    const answer=await chrome.runtime.sendMessage({type:'morning_authorize',run_id:message.run_id,nonce:message.nonce});
    if(!answer?.ok)throw Error('disabled');
   };
   await authorized();
   const target=M.target(message.target,C.IC_ORIGIN);
   if(location.href!==target.url)throw Error('destination_mapping_changed');
   await waitSurface();
   const initial=read(true),io=ports({context:initial.snapshot.context});
   io.authorized=authorized;
   io.binding=async()=>{const s=surface();return {group:s.group,structure_id:/structureID\/(\d+)/.exec(new URL(s.path).pathname)?.[1],task:[...s.score.querySelectorAll('[aria-label]')].some(e=>e.getAttribute('aria-label')===`progressScore_${target.term_id}_${target.task_id}_${target.section_id}`)};};
   return M.run(message.list,target,io);
  })().then(data=>reply({ok:true,data}),error=>reply({ok:false,not_started:true,code:['signed_out','disabled','destination_mapping_changed','students_dont_match','several_sections','ic_not_saved','unsupported_points_scale','unsupported_gradebook','list_expired'].includes(error.message)?error.message:'panel_unusable'})).finally(()=>{clearInterval(ping);working=false;});
  return true;
 });
 function hideLauncher(){launcher?.remove();launcher=null;}
 async function tick(){
  if(host||working||document.hidden)return;
  let lists=[];
  try{lists=(await ask('lists'))?.lists||[];}catch{lists=[];}
  let ready=false;
  if(lists.length){try{surface();ready=true;}catch{ready=false;}}
  if(!ready){hideLauncher();return;}
  if(launcher)return;
  launcher=node('button',{type:'button',style:'position:fixed;right:16px;bottom:16px;z-index:2147483646;padding:10px 16px;border-radius:999px;border:2px solid #315a42;background:#315a42;color:white;font:600 14px system-ui;box-shadow:0 4px 16px #0004;cursor:pointer'},'Hub helper: enter the Hub’s scores here');
  launcher.addEventListener('click',event=>{if(event.isTrusted)openReview(lists);});
  document.body.append(launcher);
 }
 setInterval(tick,2500);tick();

 function openReview(lists){
  hideLauncher();
  let title='';try{title=surface().context.title;}catch{/* shown below */}
  const picked=E.pickList(lists.map(l=>l.list),title);
  let list=picked?.list||lists[0].list,column=E.columnFromTitle(title),plan=null;
  host=node('div',{role:'dialog','aria-label':'Hub helper',style:'box-sizing:border-box;position:fixed;inset:16px 16px auto auto;z-index:2147483647;width:min(680px,calc(100vw - 32px));max-height:90vh;overflow:auto;background:white;color:#15281c;border:2px solid #315a42;border-radius:12px;padding:20px 22px;box-shadow:0 8px 40px #0005;font:14px/1.45 system-ui'});
  const select=node('select',{'aria-label':'Hub list'},lists.map(({list:l})=>node('option',{value:l.list_id},`${listName(l)} (sent ${time(l.sent_at)})`)));
  select.value=list.list_id;
  const radios=['notes','revision'].map(c=>{const r=node('input',{type:'radio',name:'hub-helper-column',value:c});r.checked=c===column;return r;});
  const warn=node('p',{style:'color:#8a4b00;margin:6px 0'});
  const status=node('p',{style:'margin:10px 0;font-weight:600'});
  const body=node('div');
  const confirm=node('input',{type:'checkbox'});
  const confirmText=node('span');
  const go=node('button',{type:'button',style:'margin:10px 8px 0 0;padding:8px 14px;border-radius:8px;border:0;background:#315a42;color:white;font:600 14px system-ui;cursor:pointer'});
  const again=node('button',{type:'button',style:'margin:10px 8px 0 0;padding:8px 14px'},'Review again');
  const close=node('button',{type:'button',style:'margin:10px 0 0;padding:8px 14px'},'Close');
  host.append(node('h2',{style:'margin:0 0 8px;font-size:18px'},'Enter the Hub’s scores in this IC assignment'),
   node('p',{style:'margin:4px 0'},'IC assignment: ',node('strong',{text:title||'(not found)'})),
   node('p',{style:'margin:4px 0'},node('label',{},'Hub list: ',select)),
   node('p',{style:'margin:4px 0'},'Hub column for this assignment: ',...radios.flatMap(r=>[node('label',{style:'margin-right:14px'},r,' ',E.COLUMN_NAME[r.value])])),
   warn,status,body,
   node('p',{style:'margin:10px 0 0'},node('label',{},confirm,' ',confirmText)),go,again,close);
  document.body.append(host);
  const finish=()=>{host?.remove();host=null;};
  close.addEventListener('click',e=>{if(e.isTrusted&&!working)finish();});
  again.addEventListener('click',e=>{if(e.isTrusted&&!working)review();});
  select.addEventListener('change',()=>{list=lists.find(l=>l.list.list_id===select.value).list;review();});
  radios.forEach(r=>r.addEventListener('change',()=>{column=r.value;review();}));
  // A disabled Enter looks disabled; it enables only once the box is ticked.
  const setGo=off=>{go.disabled=off;go.style.opacity=off?'0.45':'1';go.style.cursor=off?'not-allowed':'pointer';};
  confirm.addEventListener('change',()=>setGo(!confirm.checked||!plan));

  function review(){
   plan=null;confirm.checked=false;confirm.disabled=false;setGo(true);go.hidden=false;body.replaceChildren();
   warn.textContent=E.titleOverlap(list,title)?'':'The IC title and this Hub lesson share no words. Check that it’s the same lesson before entering.';
   confirmText.textContent=`I checked: “${title}” gets the Hub’s ${column?E.COLUMN_NAME[column]:'…'} scores for ${listName(list)}.`;
   try{plan=E.buildPlan(list,read(true).snapshot,column);}
   catch(error){status.textContent=say(error);go.hidden=true;confirm.disabled=true;return;}
   const c=plan.counts;
   status.textContent=`${plan.section}: ${c.fill} blank → enter · ${c.raise} raise · ${c.keep} kept (IC same or higher) · ${c.leave} left for you.`;
   go.textContent=plan.changes.length?`Enter ${plan.changes.length} score${plan.changes.length===1?'':'s'} and Save`:'Nothing to enter: tell the Hub this is checked';
   const items=[...plan.items].sort((a,b)=>ORDER[a.action]-ORDER[b.action]||a.name.localeCompare(b.name));
   body.append(node('p',{style:'margin:4px 0;color:#445'},list.status_text),
    node('table',{style:'width:100%;border-collapse:collapse;margin:8px 0'},
     node('tr',{},['Student','IC now','Hub','Helper will'].map(h=>node('th',{style:'text-align:left;border-bottom:1px solid #ccd;padding:3px 6px'},h))),
     items.map(item=>node('tr',{style:['fill','raise'].includes(item.action)?'background:#eef7f0':item.action==='leave'?'background:#fff6e5':''},
      [item.name,item.other??shown(item.current),hubCell(item),doing(item)].map(t=>node('td',{style:'padding:3px 6px;border-bottom:1px solid #eef'},t))))),
    plan.missing.length?node('p',{style:'margin:6px 0'},node('strong',{},'On the Hub list but not in this IC assignment: '),plan.missing.join('; '),'.'):null,
    node('p',{style:'margin:6px 0;color:#445'},'The helper keeps the higher score: it fills blanks and raises lower scores, never lowers one, and leaves IC flags alone. After Save it reloads the assignment and checks every score.'));
  }

  go.addEventListener('click',async event=>{
   if(!event.isTrusted||!confirm.checked||!plan||working)return;
   working=true;setGo(true);confirm.disabled=true;again.disabled=true;close.disabled=true;select.disabled=true;radios.forEach(r=>{r.disabled=true;});
   status.textContent=plan.changes.length?'Typing, saving and reloading to check… keep this tab open.':'Checking…';
   const current=plan;
   let run;
   try{run=await E.execute(current,ports(current));}
   catch{run={outcome:'uncertain',code:'save_unconfirmed',certain:false,counts:{filled:0,raised:0,kept:current.counts.keep,left:current.counts.leave,not_saved:0},failed:[],unexpected:[]};}
   const n=run.counts;
   status.textContent={
    entered:`Saved and checked after reloading: ${n.filled} entered, ${n.raised} raised. ${n.left} left for you (listed below).`,
    checked:'Nothing needed entering: IC already has these scores or higher.',
    partial:`Saved, but after reloading ${run.failed.length?`these didn’t match: ${run.failed.join('; ')}. `:''}${run.unexpected.length?`These changed though the helper didn’t touch them: ${run.unexpected.join('; ')}. `:''}Check them in IC. Reviewing again enters only what’s still missing.`,
    uncertain:'Stopped after clicking Save; the helper couldn’t confirm what IC saved. Reload this assignment in IC and check it before trying again.',
   }[run.outcome]||(run.code==='ic_changed'?'IC changed since this review, so nothing was typed. Click Review again.'
    :run.code==='ic_unreadable'?'The assignment couldn’t be read, so nothing was typed. Click Review again.'
    :'Stopped before saving: nothing was saved. Reload this assignment in IC to clear what the helper typed, then review again.');
   if(['entered','checked','partial','uncertain'].includes(run.outcome)){
    try{await ask('result',{result:E.hubResult(current,run,title)});}
    catch{status.textContent+=' (The Hub couldn’t be told; click “I entered these in IC” there yourself when every section is done.)';}
   }
   working=false;again.disabled=false;close.disabled=false;select.disabled=false;radios.forEach(r=>{r.disabled=false;});go.hidden=true;
   host.scrollTop=0;  // the result is at the top of the panel
  });
  review();
 }
})();
