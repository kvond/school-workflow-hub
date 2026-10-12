/* At most one automatic morning attempt per Eastern weekday. Persistent state is opt-in,
 * dates, counts and verification attribution; student lists remain in memory/session storage.
 * Reserve each destination before contacting IC. A restart never repeats it. */
(() => {
 'use strict';
 const M=globalThis.HubMorning,C=globalThis.HubPostingConfig,E=globalThis.HubIcEntry;
 if(!M||!chrome.alarms||!chrome.storage.local)return;
 const KEY='morning_state',ALARM='hub-weekday-morning';
 let busy=false,active=null,sourceTab=null,queue=Promise.resolve();
 const locked=fn=>{const next=queue.then(fn,fn);queue=next.catch(()=>{});return next;};
 // The new automatic-source grant requires actual durable guard fields;
 // read()'s historical blocked:false default must not manufacture that proof.
 const automaticSourceReadStates=new WeakSet();
 const read=async()=>{
  const data=await chrome.storage.local.get([KEY,'morning_enabled','morning_enabled_day']);
  const stored=data[KEY],s={day:'',blocked:false,...stored,enabled:data.morning_enabled===true,enabled_day:data.morning_enabled_day||''};
  if(stored&&typeof stored==='object'&&!Array.isArray(stored)&&typeof stored.blocked==='boolean'&&['blocked','inflight','execution_started','report_pending','outbox','run_helper_version'].every(k=>Object.hasOwn(stored,k)))automaticSourceReadStates.add(s);
  return s;
 };
 const write=s=>{const {enabled,enabled_day,...stored}=s;return chrome.storage.local.set({[KEY]:stored});};
 const reasons=()=>({flag:0,exempt:0,excused:0,check:0,name:0,missing:0});
 const classNames={biology:'Biology',forensics:'Forensics',anatomy:'Anatomy & Physiology'};
 const item=(entry,code)=>({assignment:String(entry.assignment).slice(0,200),class_name:String(entry.class_name||classNames[entry.class]||entry.class||'Class unavailable').slice(0,100),week:String(entry.week||'Week unavailable').slice(0,100),code,filled:0,raised:0,kept:0,left:0,reasons:reasons()});
 const safe=new Set(['signed_out','hub_unavailable','ic_unavailable','panel_unusable','disabled','source_changed','receipt_unconfirmed','mapping_required','missed_noon','ic_changed','destination_mapping_changed','students_dont_match','several_sections','ic_not_saved','unsupported_points_scale','unsupported_gradebook','list_expired','coverage_incomplete','no_scores','source_scores_held','excluded_week']);
 const completedBatchCodes=new Set(['read_back','nothing_to_enter','no_scores','excluded_week']);
 const batchCode=items=>{
  if(items.some(i=>i.code==='source_scores_held'))return 'source_scores_held';
  const held=items.find(i=>!completedBatchCodes.has(i.code));
  return held?(safe.has(held.code)?held.code:'coverage_incomplete'):'finished';
 };
 const code=e=>safe.has(e?.message)?e.message:'panel_unusable';
 const failure=(code,cause,status)=>Object.assign(Error(code),M.preflight('summary_publish',{cause,status}));
 const bridgeCause=e=>/Receiving end does not exist/i.test(e?.message||'')?'hub_bridge_missing':/message (?:port|channel) closed|back.forward cache/i.test(e?.message||'')?'hub_bridge_closed':'hub_bridge_unavailable';
 const deadline=(promise,ms=100000)=>{let timer;return Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('panel_unusable')),ms);})]).finally(()=>clearTimeout(timer));};
 async function hub(tab,action,payload){
  let current;try{current=await chrome.tabs.get(tab);}catch{throw failure('hub_unavailable','hub_tab_closed');}
  try{if(new URL(current.url).origin!==C.HUB_ORIGIN)throw Error();}catch{throw failure('signed_out','hub_origin_changed');}
  let r;try{r=await deadline(chrome.tabs.sendMessage(tab,{type:'morning_hub',action,payload},{frameId:0}).catch(e=>{throw failure('hub_unavailable',bridgeCause(e));}),action==='record'?210000:100000);}catch(e){throw e.cause?e:failure('hub_unavailable',e.message==='panel_unusable'?'hub_bridge_timeout':bridgeCause(e));}
  if(!r?.ok)throw failure(safe.has(r?.code)?r.code:'hub_unavailable',r?.cause||'hub_bridge_unavailable',r?.status);return r.data;
 }
 async function publish(state,tab){
  let failed=null;
  try{const reply=await hub(tab,'report',state.report);if(reply?.recorded!==true)throw failure('receipt_unconfirmed','hub_report_not_acknowledged');state.report_pending=false;}catch(e){state.report_pending=true;failed=M.preflight('summary_publish',e);}
  await write(state);
  return failed;
 }
 async function authorize(message,sender){
  const s=await read(),t=M.clock();
  if(!active||!s.enabled||s.day!==t.day||(!active.manualNow&&(!t.weekday||t.minutes<420||t.minutes>=720))||sender.id!==chrome.runtime.id||sender.frameId!==0||sender.tab?.id!==active.tab||sender.url!==active.url||message.run_id!==active.run_id||message.nonce!==active.nonce)return {ok:false,code:'disabled'};
  const tab=await chrome.tabs.get(active.tab);
  return {ok:tab.url===active.url};
 }
 async function usable(tab,url){
  await chrome.tabs.update(tab,{url,active:true});
  const actionable=new Set(['ic_not_saved','unsupported_gradebook','unsupported_points_scale','signed_out']);
  const end=Date.now()+30000;
  while(Date.now()<end){
   const current=await chrome.tabs.get(tab);
   if(current.status==='complete'&&current.url===url){
    try{const r=await chrome.tabs.sendMessage(tab,{type:'morning_probe'},{frameId:0});if(r?.ok)return;if(actionable.has(r?.code))throw Error(r.code);}catch(e){if(actionable.has(e.message))throw e;}
   }
   await new Promise(r=>setTimeout(r,250));
  }
  throw Error('panel_unusable');
 }
 function retryable(s){
  const t=M.clock();
  // Before 0.4.2, inflight was created before the first write-capable request
  // and never deleted. Its absence proves a legacy run never sent one.
  const neverExecuted=s.execution_started===false||(!Object.hasOwn(s,'execution_started')&&!Object.hasOwn(s,'inflight'));
  const untouched=Array.isArray(s.report?.items)&&s.report.items.every(i=>['signed_out','panel_unusable','mapping_required','coverage_incomplete'].includes(i.code)&&['filled','raised','kept','left'].every(k=>i[k]===0));
  return s.enabled&&t.weekday&&t.minutes>=420&&t.minutes<720&&s.day===t.day
   &&s.report?.day===t.day&&['interrupted','hub_unavailable','receipt_unconfirmed','signed_out','ic_unavailable','panel_unusable'].includes(s.report.code)
   &&Array.isArray(s.report.items)&&neverExecuted&&(s.report.items.length===0||untouched)&&!s.blocked&&!s.inflight
   &&!s.report_pending&&!(s.outbox?.length)&&!pending.size;
 }
 const preflightCodes=new Set(['hub_unavailable','ic_unavailable','panel_unusable','unsupported_gradebook']);
 const completedCodes=new Set(['read_back','nothing_to_enter']);
 const completedRecheckCodes=new Set(['read_back','nothing_to_enter','mapping_required','coverage_incomplete','no_scores']);
 const zeroCounts=i=>['filled','raised','kept','left'].every(k=>i[k]===0);
 function safePreflightRepeat(s,t){
  const items=s.report?.items;
  return s.manual_run_day===t.day&&s.manual_run_retry_day!==t.day&&s.report?.day===t.day
   &&preflightCodes.has(s.report?.code)&&Array.isArray(items)
   &&(items.length===0?s.execution_started===false:items.some(i=>i.code===s.report.code&&zeroCounts(i))
    &&items.every(i=>completedCodes.has(i.code)||(i.code===s.report.code&&zeroCounts(i))));
 }
 const validDay=day=>typeof day==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(day)
  &&Number.isFinite(Date.parse(day+'T12:00:00Z'))&&new Date(day+'T12:00:00Z').toISOString().slice(0,10)===day;
 const validRun=id=>typeof id==='string'&&/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id);
 function completedRecheckReport(s){
  const report=s.report,items=report?.items;
  // A persisted partial batch keeps its real source/mapping gaps. Only known
  // completed items authorize a new explicit pass; this never retries a Save.
  return validDay(s.day)&&report?.day===s.day&&validRun(report?.run_id)
   &&['finished','mapping_required','coverage_incomplete','no_scores','source_scores_held'].includes(report.code)
   &&s.report_pending===false&&typeof s.execution_started==='boolean'&&Array.isArray(items)
   &&(items.length>0||s.execution_started===false)
   &&(report.code==='source_scores_held'||!items.some(i=>i?.code==='source_scores_held'))
   &&(report.code==='finished'||(items.length>0&&items.some(i=>i?.code===report.code)))
   &&items.every(i=>i&&(completedRecheckCodes.has(i.code)||(i.code==='source_scores_held'
    &&i.source_holds&&!Array.isArray(i.source_holds)&&Object.hasOwn(i.source_holds,'checks')&&Object.keys(i.source_holds).length===1
    &&Number.isInteger(i.source_holds.checks)&&i.source_holds.checks>0&&i.source_holds.checks<=3600))
    &&['filled','raised','kept','left'].every(k=>Number.isInteger(i[k])&&i[k]>=0&&i[k]<=3600)
    &&(completedCodes.has(i.code)||zeroCounts(i)))
   &&(s.execution_started||items.every(zeroCounts));
 }
 const verifiedReport=s=>validRun(s.report?.run_id)&&s.verified?.run_id===s.report.run_id
  &&typeof s.verified.by==='string'&&!!s.verified.by&&Number.isFinite(Date.parse(s.verified.at));
 function freshManualDay(s,t){
  // A new install has no old attempt to resolve. An old receipt with missing
  // execution flags is unknown; absence of inflight alone cannot clear it.
  if(!s.day&&!s.report)return !Object.hasOwn(s,'execution_started')&&!Object.hasOwn(s,'inflight')&&!s.manual_run_day;
  const report=s.report,items=report?.items;
  if(!validDay(s.day)||s.day>=t.day||report?.day!==s.day||!validRun(report?.run_id)||!Array.isArray(items)
   ||(s.manual_run_day&&(!validDay(s.manual_run_day)||s.manual_run_day>s.day)))return false;
  if(verifiedReport(s))return !['partial_receipt','source_changed_before_entry','checked_readback_after_helper_update','checked_typing_after_helper_update'].includes(s.verified?.recovery);
  if(s.report_pending!==false)return false;
  if(report.code==='finished'&&(items.length?[true,false].includes(s.execution_started)&&items.every(i=>
   (completedRecheckCodes.has(i.code)||i.code==='excluded_week')
   &&['filled','raised','kept','left'].every(k=>Number.isInteger(i[k])&&i[k]>=0&&i[k]<=3600))
   &&(s.execution_started===true||items.every(zeroCounts)):s.execution_started===false))return true;
  return s.execution_started===false&&(safe.has(report.code)||report.code==='interrupted')
   &&items.every(i=>(safe.has(i.code)||i.code==='interrupted')&&zeroCounts(i));
 }
 function legacyRecoveryAllowed(s,t=M.clock()){
  // This offers an explicit current-gradebook verification, never evidence
  // about whether the old empty attempt wrote anything or why it failed.
  return s.enabled&&!s.blocked&&!s.inflight&&s.report_pending===false&&!(s.outbox?.length)&&!pending.size
   &&validDay(s.day)&&s.day<t.day&&s.report?.day===s.day&&validRun(s.report?.run_id)
   &&s.report.code==='receipt_unconfirmed'&&Array.isArray(s.report.items)&&s.report.items.length===0
   &&(!s.report.preflight||s.report.preflight.cause==='unrecorded')
   &&!Object.hasOwn(s,'execution_started')&&!verifiedReport(s)
   &&(!s.manual_run_day||(validDay(s.manual_run_day)&&s.manual_run_day<=s.day));
 }
 function currentReceiptReport(s,t=M.clock()){
  const report=s.report,items=report?.items,version=chrome.runtime.getManifest().version;
  const counts=i=>['filled','raised','kept','left'].every(k=>Number.isInteger(i[k])&&i[k]>=0&&i[k]<=3600);
  const reasons=i=>i.reasons&&!Array.isArray(i.reasons)
   &&Object.keys(i.reasons).sort().join('|')==='check|excused|exempt|flag|missing|name'
   &&Object.values(i.reasons).every(n=>Number.isInteger(n)&&n>=0&&n<=3600);
  const held=i=>i.code!=='source_scores_held'||(i.source_holds&&!Array.isArray(i.source_holds)
   &&Object.keys(i.source_holds).join('|')==='checks'&&Number.isInteger(i.source_holds.checks)
   &&i.source_holds.checks>0&&i.source_holds.checks<=3600);
  const shape=i=>Object.keys(i).sort().join('|')===(i.code==='source_scores_held'
   ?'assignment|class_name|code|filled|kept|left|raised|reasons|source_holds|week'
   :'assignment|class_name|code|filled|kept|left|raised|reasons|week');
  const unused=(!Object.hasOwn(s,'receipt_recheck_version')&&!Object.hasOwn(s,'receipt_recheck_run_id')&&!Object.hasOwn(s,'receipt_recheck_day'))
   ||(typeof s.receipt_recheck_version==='string'&&/^\d+\.\d+\.\d+$/.test(s.receipt_recheck_version)
    &&validRun(s.receipt_recheck_run_id)&&validDay(s.receipt_recheck_day)&&s.receipt_recheck_day<=s.day);
  return s.enabled&&s.blocked===false&&s.inflight===false&&s.report_pending===false
   &&s.execution_started===true&&Array.isArray(s.outbox)&&s.outbox.length===0&&!pending.size&&!active
   &&validDay(s.day)&&s.day===t.day&&report?.day===s.day&&validRun(report?.run_id)
   &&(!s.manual_run_day||(validDay(s.manual_run_day)&&s.manual_run_day<=s.day))
   &&Object.keys(report).sort().join('|')==='code|day|items|roster|run_id'&&unused
   &&report.roster==='unavailable'&&report.code==='receipt_unconfirmed'&&!report.preflight
   &&Array.isArray(items)&&items.length>0&&items.length<=300&&items.at(-1)?.code==='receipt_unconfirmed'
   &&items.every((i,n)=>i&&shape(i)&&typeof i.assignment==='string'&&i.assignment.trim()&&i.assignment.length<=200
    &&typeof i.class_name==='string'&&i.class_name.trim()&&i.class_name.length<=100
    &&typeof i.week==='string'&&i.week.trim()&&i.week.length<=100&&counts(i)&&reasons(i)
    &&(n===items.length-1||((completedRecheckCodes.has(i.code)||['excluded_week','source_scores_held'].includes(i.code))
     &&(completedCodes.has(i.code)||(zeroCounts(i)&&Object.values(i.reasons).every(v=>v===0)))&&held(i))))
   &&s.receipt_recheck_version!==version;
 }
 function currentReceiptRecoveryAllowed(s,t=M.clock()){
  return currentReceiptReport(s,t)&&!verifiedReport(s);
 }
 function currentReceiptRecoveryGrant(s,t=M.clock()){
  const v=s.verified;
  return currentReceiptReport(s,t)&&verifiedReport(s)&&v.scope==='current_mapped_assignments'
   &&v.recovery==='partial_receipt'&&v.prior_day===s.day
   &&v.for_helper_version===chrome.runtime.getManifest().version&&validRun(v.event_id)
   &&v.prior_report_signature===JSON.stringify(s.report);
 }
 function currentSourceChangedReport(s,t=M.clock()){
  const report=s.report,items=report?.items,version=chrome.runtime.getManifest().version;
  const counts=i=>['filled','raised','kept','left'].every(k=>Number.isInteger(i[k])&&i[k]>=0&&i[k]<=3600);
  const reasons=i=>i.reasons&&!Array.isArray(i.reasons)
   &&Object.keys(i.reasons).sort().join('|')==='check|excused|exempt|flag|missing|name'
   &&Object.values(i.reasons).every(n=>Number.isInteger(n)&&n>=0&&n<=3600);
  const held=i=>i.code!=='source_scores_held'||(i.source_holds&&!Array.isArray(i.source_holds)
   &&Object.keys(i.source_holds).join('|')==='checks'&&Number.isInteger(i.source_holds.checks)
   &&i.source_holds.checks>0&&i.source_holds.checks<=3600);
  const shape=i=>Object.keys(i).sort().join('|')===(i.code==='source_scores_held'
   ?'assignment|class_name|code|filled|kept|left|raised|reasons|source_holds|week'
   :'assignment|class_name|code|filled|kept|left|raised|reasons|week');
  const unused=(!Object.hasOwn(s,'source_recheck_version')&&!Object.hasOwn(s,'source_recheck_run_id')&&!Object.hasOwn(s,'source_recheck_day'))
   ||(typeof s.source_recheck_version==='string'&&/^\d+\.\d+\.\d+$/.test(s.source_recheck_version)
    &&validRun(s.source_recheck_run_id)&&validDay(s.source_recheck_day)&&s.source_recheck_day<=s.day);
  // Only a delivered, known source comparison stop BEFORE this item's entry
  // is recoverable here. Prior items retain their real comparisons and gaps.
  return s.enabled&&s.blocked===false&&s.inflight===false&&s.report_pending===false
   &&typeof s.execution_started==='boolean'&&Array.isArray(s.outbox)&&s.outbox.length===0&&!pending.size&&!active
   &&validDay(s.day)&&s.day===t.day&&report?.day===s.day&&validRun(report?.run_id)
   &&(!s.manual_run_day||(validDay(s.manual_run_day)&&s.manual_run_day<=s.day))
   &&Object.keys(report).sort().join('|')==='code|day|items|roster|run_id'&&unused
   &&report.roster==='unavailable'&&report.code==='source_changed'
   &&Array.isArray(items)&&items.length>0&&items.length<=300&&items.at(-1)?.code==='source_changed'
   &&items.every((i,n)=>i&&shape(i)&&typeof i.assignment==='string'&&i.assignment.trim()&&i.assignment.length<=200
    &&typeof i.class_name==='string'&&i.class_name.trim()&&i.class_name.length<=100
    &&typeof i.week==='string'&&i.week.trim()&&i.week.length<=100&&counts(i)&&reasons(i)
    &&(n===items.length-1?(zeroCounts(i)&&Object.values(i.reasons).every(v=>v===0)):
     ((completedRecheckCodes.has(i.code)||['excluded_week','source_scores_held'].includes(i.code))
      &&(completedCodes.has(i.code)||(zeroCounts(i)&&Object.values(i.reasons).every(v=>v===0)))&&held(i))))
   &&(s.execution_started||items.every(zeroCounts))&&s.source_recheck_version!==version;
 }
 function currentSourceChangedRecoveryAllowed(s,t=M.clock()){
  return currentSourceChangedReport(s,t)&&!verifiedReport(s);
 }
 function currentSourceChangedRecoveryGrant(s,t=M.clock()){
  const v=s.verified;
  return currentSourceChangedReport(s,t)&&verifiedReport(s)&&v.scope==='current_mapped_assignments'
   &&v.recovery==='source_changed_before_entry'&&v.prior_day===s.day
   &&v.for_helper_version===chrome.runtime.getManifest().version&&validRun(v.event_id)
   &&v.prior_report_signature===JSON.stringify(s.report);
 }
 function currentReadbackMismatchReport(s,t=M.clock()){
  const report=s.report,items=report?.items,version=chrome.runtime.getManifest().version;
  const counts=i=>['filled','raised','kept','left'].every(k=>Number.isInteger(i[k])&&i[k]>=0&&i[k]<=3600);
  const reasons=i=>i.reasons&&!Array.isArray(i.reasons)
   &&Object.keys(i.reasons).sort().join('|')==='check|excused|exempt|flag|missing|name'
   &&Object.values(i.reasons).every(n=>Number.isInteger(n)&&n>=0&&n<=3600);
  const held=i=>i.code!=='source_scores_held'||(i.source_holds&&!Array.isArray(i.source_holds)
   &&Object.keys(i.source_holds).join('|')==='checks'&&Number.isInteger(i.source_holds.checks)
   &&i.source_holds.checks>0&&i.source_holds.checks<=3600);
  const shape=i=>Object.keys(i).sort().join('|')===(i.code==='source_scores_held'
   ?'assignment|class_name|code|filled|kept|left|raised|reasons|source_holds|week'
   :'assignment|class_name|code|filled|kept|left|raised|reasons|week');
  // The 0.5.23 report predates the durable run-version stamp. Its missing
  // version is accepted only through the exact consumed source-recovery chain.
  const sourceVerified=s.verified?.recovery==='checked_readback_after_helper_update'?s.verified.prior_source_recovery:s.verified;
  let prior=null;try{prior=JSON.parse(sourceVerified?.prior_report_signature);}catch{}
  const legacyVersion=!Object.hasOwn(s,'run_helper_version')
   &&s.source_recheck_version==='0.5.23'&&s.source_recheck_day===s.day
   &&validRun(s.source_recheck_run_id)&&sourceVerified?.run_id===s.source_recheck_run_id
   &&sourceVerified.recovery==='source_changed_before_entry'&&sourceVerified.scope==='current_mapped_assignments'
   &&sourceVerified.for_helper_version==='0.5.23'&&sourceVerified.prior_day===s.day
   &&validRun(sourceVerified.event_id)&&typeof sourceVerified.by==='string'&&!!sourceVerified.by.trim()
   &&Number.isFinite(Date.parse(sourceVerified.at))&&prior?.run_id===s.source_recheck_run_id
   &&prior.day===s.day&&prior.code==='source_changed'&&report?.run_id!==prior.run_id
   &&currentSourceChangedReport({...s,blocked:false,report:prior,verified:sourceVerified},t);
  const priorVersion=s.run_helper_version==='0.5.23'||legacyVersion;
  const unused=(!Object.hasOwn(s,'readback_recheck_version')&&!Object.hasOwn(s,'readback_recheck_run_id')&&!Object.hasOwn(s,'readback_recheck_day'))
   ||(s.readback_recheck_version==='0.5.23'&&validRun(s.readback_recheck_run_id)
    &&validDay(s.readback_recheck_day)&&s.readback_recheck_day<=s.day);
  return version==='0.5.24'&&priorVersion&&unused&&s.enabled&&[true,false].includes(s.blocked)
   &&s.blocked_run_id===report?.run_id&&Array.isArray(s.blocked_assignments)&&s.blocked_assignments.length===1
   &&typeof s.blocked_assignments[0]==='string'&&!!s.blocked_assignments[0].trim()
   &&s.inflight===false&&s.report_pending===false&&s.execution_started===true
   &&Array.isArray(s.outbox)&&s.outbox.length===0&&!pending.size&&!active
   &&validDay(s.day)&&s.day===t.day&&report?.day===s.day&&validRun(report?.run_id)
   &&s.manual_run_day===s.day&&Object.keys(report).sort().join('|')==='code|day|items|roster|run_id'
   &&report.roster==='unavailable'&&report.code==='panel_unusable'
   &&Array.isArray(items)&&items.length>0&&items.length<=300&&items.at(-1)?.code==='readback_mismatch'
   &&typeof items.at(-1).assignment==='string'
   &&(items.at(-1).assignment===s.blocked_assignments[0]||items.at(-1).assignment.startsWith(s.blocked_assignments[0]+' · '))
   &&items.every((i,n)=>i&&shape(i)&&typeof i.assignment==='string'&&i.assignment.trim()&&i.assignment.length<=200
    &&typeof i.class_name==='string'&&i.class_name.trim()&&i.class_name.length<=100
    &&typeof i.week==='string'&&i.week.trim()&&i.week.length<=100&&counts(i)&&reasons(i)
    &&(n===items.length-1?(zeroCounts(i)&&Object.values(i.reasons).every(v=>v===0)):
     ((completedRecheckCodes.has(i.code)||['excluded_week','source_scores_held'].includes(i.code))
      &&(completedCodes.has(i.code)||(zeroCounts(i)&&Object.values(i.reasons).every(v=>v===0)))&&held(i))));
 }
 function currentReadbackMismatchRecoveryAllowed(s,t=M.clock()){
  return currentReadbackMismatchReport(s,t)&&s.blocked===true&&!verifiedReport(s);
 }
 function currentReadbackMismatchRecoveryGrant(s,t=M.clock()){
  const v=s.verified;
  return currentReadbackMismatchReport(s,t)&&s.blocked===false&&verifiedReport(s)
   &&v.scope==='current_mapped_assignments'&&v.recovery==='checked_readback_after_helper_update'
   &&v.prior_day===s.day&&v.prior_helper_version==='0.5.23'
   &&v.for_helper_version===chrome.runtime.getManifest().version&&validRun(v.event_id)
   &&v.prior_report_signature===JSON.stringify(s.report);
 }
 function currentTypingStoppedReport(s,t=M.clock()){
  const report=s.report,items=report?.items,version=chrome.runtime.getManifest().version;
  const counts=i=>['filled','raised','kept','left'].every(k=>Number.isInteger(i[k])&&i[k]>=0&&i[k]<=3600);
  const reasons=i=>i.reasons&&!Array.isArray(i.reasons)
   &&Object.keys(i.reasons).sort().join('|')==='check|excused|exempt|flag|missing|name'
   &&Object.values(i.reasons).every(n=>Number.isInteger(n)&&n>=0&&n<=3600);
  const held=i=>i.code!=='source_scores_held'||(i.source_holds&&!Array.isArray(i.source_holds)
   &&Object.keys(i.source_holds).join('|')==='checks'&&Number.isInteger(i.source_holds.checks)
   &&i.source_holds.checks>0&&i.source_holds.checks<=3600);
  const shape=i=>Object.keys(i).sort().join('|')===(i.code==='source_scores_held'
   ?'assignment|class_name|code|filled|kept|left|raised|reasons|source_holds|week'
   :'assignment|class_name|code|filled|kept|left|raised|reasons|week');
  // A consumed025 grant stays bound to its older report, even after the026
  // acknowledgement stores the new report. It cannot itself grant another run.
  const previous=s.verified?.recovery==='checked_typing_after_helper_update'
   &&s.verified.for_helper_version==='0.5.26'?s.verified.prior_typing_recovery:s.verified;
  let prior=null;try{prior=JSON.parse(previous?.prior_report_signature);}catch{}
  const priorMarker=s.typing_recheck_version==='0.5.25'&&validRun(s.typing_recheck_run_id)
   &&s.typing_recheck_run_id!==report?.run_id&&s.typing_recheck_day===s.day
   &&previous?.run_id===s.typing_recheck_run_id&&previous.recovery==='checked_typing_after_helper_update'
   &&previous.scope==='current_mapped_assignments'&&previous.prior_day===s.day
   &&previous.prior_helper_version==='0.5.24'&&previous.for_helper_version==='0.5.25'
   &&validRun(previous.event_id)&&typeof previous.by==='string'&&!!previous.by.trim()
   &&Number.isFinite(Date.parse(previous.at))&&prior?.run_id===s.typing_recheck_run_id
   &&prior.day===s.day&&Object.keys(prior).sort().join('|')==='code|day|items|roster|run_id'
   &&prior.code==='panel_unusable'&&prior.roster==='unavailable'&&Array.isArray(prior.items)
   &&prior.items.length>0&&prior.items.length<=300&&prior.items.at(-1)?.code==='typing_stopped'
   &&prior.items.every((i,n)=>i&&shape(i)&&typeof i.assignment==='string'&&i.assignment.trim()&&i.assignment.length<=200
    &&typeof i.class_name==='string'&&i.class_name.trim()&&i.class_name.length<=100
    &&typeof i.week==='string'&&i.week.trim()&&i.week.length<=100&&counts(i)&&reasons(i)
    &&(n===prior.items.length-1?(i.filled===0&&i.raised===0&&i.left===0&&Object.values(i.reasons).every(v=>v===0)):
     ((completedRecheckCodes.has(i.code)||['excluded_week','source_scores_held'].includes(i.code))
      &&(completedCodes.has(i.code)||(zeroCounts(i)&&Object.values(i.reasons).every(v=>v===0)))&&held(i))));
  const unused=(!Object.hasOwn(s,'typing_recheck_version')&&!Object.hasOwn(s,'typing_recheck_run_id')&&!Object.hasOwn(s,'typing_recheck_day'))||priorMarker;
  // This finite025-to026 path concerns a known executor stop before Save.
  // Any visible typing still requires fresh teacher verification of all mapped
  // saved grades. Missing prior version or unknown Save outcomes are not evidence.
  return version==='0.5.26'&&s.run_helper_version==='0.5.25'&&unused&&s.enabled&&[true,false].includes(s.blocked)
   &&s.blocked_run_id===report?.run_id&&Array.isArray(s.blocked_assignments)&&s.blocked_assignments.length===1
   &&typeof s.blocked_assignments[0]==='string'&&!!s.blocked_assignments[0].trim()
   &&s.inflight===false&&s.report_pending===false&&s.execution_started===true
   &&Array.isArray(s.outbox)&&s.outbox.length===0&&!pending.size&&!active
   &&validDay(s.day)&&s.day===t.day&&report?.day===s.day&&validRun(report?.run_id)
   &&s.manual_run_day===s.day&&Object.keys(report).sort().join('|')==='code|day|items|roster|run_id'
   &&report.roster==='unavailable'&&report.code==='panel_unusable'
   &&Array.isArray(items)&&items.length>0&&items.length<=300&&items.at(-1)?.code==='typing_stopped'
   &&typeof items.at(-1).assignment==='string'
   &&(items.at(-1).assignment===s.blocked_assignments[0]||items.at(-1).assignment.startsWith(s.blocked_assignments[0]+' · '))
   &&items.every((i,n)=>i&&shape(i)&&typeof i.assignment==='string'&&i.assignment.trim()&&i.assignment.length<=200
    &&typeof i.class_name==='string'&&i.class_name.trim()&&i.class_name.length<=100
    &&typeof i.week==='string'&&i.week.trim()&&i.week.length<=100&&counts(i)&&reasons(i)
    &&(n===items.length-1?(i.filled===0&&i.raised===0&&i.left===0&&Object.values(i.reasons).every(v=>v===0)):
     ((completedRecheckCodes.has(i.code)||['excluded_week','source_scores_held'].includes(i.code))
      &&(completedCodes.has(i.code)||(zeroCounts(i)&&Object.values(i.reasons).every(v=>v===0)))&&held(i))));
 }
 function currentTypingStoppedRecoveryAllowed(s,t=M.clock()){
  return currentTypingStoppedReport(s,t)&&s.blocked===true&&!verifiedReport(s);
 }
 function currentTypingStoppedRecoveryGrant(s,t=M.clock()){
  const v=s.verified;
  return currentTypingStoppedReport(s,t)&&s.blocked===false&&verifiedReport(s)
   &&v.scope==='current_mapped_assignments'&&v.recovery==='checked_typing_after_helper_update'
   &&v.prior_day===s.day&&v.prior_helper_version==='0.5.25'
   &&v.for_helper_version===chrome.runtime.getManifest().version&&validRun(v.event_id)
   &&v.prior_report_signature===JSON.stringify(s.report);
 }
 function automaticSourceReadRepeat(s,t=M.clock()){
  const report=s.report,items=report?.items;
  const datedBeforeToday=k=>!Object.hasOwn(s,k)||(validDay(s[k])&&s[k]<t.day);
  const keys=i=>Object.keys(i).sort().join('|')==='assignment|class_name|code|filled|kept|left|raised|reasons|week';
  const counts=i=>['filled','raised','kept','left'].every(k=>Number.isInteger(i[k])&&i[k]>=0&&i[k]<=3600);
  const reasons=i=>i.reasons&&!Array.isArray(i.reasons)
   &&Object.keys(i.reasons).sort().join('|')==='check|excused|exempt|flag|missing|name'
   &&Object.values(i.reasons).every(n=>Number.isInteger(n)&&n>=0&&n<=3600);
  const context=i=>typeof i.assignment==='string'&&i.assignment.trim()&&i.assignment.length<=200
   &&!/[\x00-\x1f\x7f]/.test(i.assignment)&&!/intentionally skipped/i.test(i.assignment)
   &&Object.values(classNames).includes(i.class_name)&&typeof i.week==='string'&&/^Wk (?:0[1-9]|[1-4]\d|5[0-3])$/.test(i.week);
  // Only a delivered automatic pass whose earlier assignment comparisons are
  // complete may offer one explicit new pass after an initial source-read stop.
  // A source/receipt/write uncertainty, skipped item or partial column is not a grant.
  return automaticSourceReadStates.has(s)&&chrome.runtime.getManifest().version==='0.5.29'
   &&['0.5.28','0.5.29'].includes(s.run_helper_version)
   &&s.enabled===true&&s.blocked===false&&s.inflight===false&&s.report_pending===false
   &&s.execution_started===true&&Array.isArray(s.outbox)&&s.outbox.length===0&&Object.keys(s.outbox).length===0&&!pending.size&&!active
   &&validDay(s.day)&&s.day===t.day&&report?.day===s.day&&validRun(report?.run_id)
   &&datedBeforeToday('manual_run_day')&&datedBeforeToday('manual_run_retry_day')&&!verifiedReport(s)
   &&typeof report==='object'&&!Array.isArray(report)&&Object.keys(report).sort().join('|')==='code|day|items|roster|run_id'
   &&report.code==='hub_unavailable'&&report.roster==='unavailable'
   &&Array.isArray(items)&&items.length>=2&&items.length<=300
   &&Object.keys(items).length===items.length&&Array.from(items).every(i=>i&&!Array.isArray(i)&&keys(i)&&context(i)&&counts(i)&&reasons(i))
   &&new Set(items.map(i=>JSON.stringify([i.class_name,i.week,i.assignment]))).size===items.length
   &&items.slice(0,-1).every(i=>completedCodes.has(i.code)&&i.filled+i.raised+i.kept>0)
   &&items.at(-1).code==='hub_unavailable'&&zeroCounts(items.at(-1))
   &&Object.values(items.at(-1).reasons).every(n=>n===0)
   &&!/ · .+ · (?:Notes|Revision)$/.test(items.at(-1).assignment);
 }
 function manualNowAllowed(s,t=M.clock()){
  const cleanMissedRun=s.report?.code==='missed_noon'&&s.execution_started===false
   &&Array.isArray(s.report.items)&&s.report.items.length===0;
  const verifiedRecovery=verifiedReport(s)&&!['partial_receipt','source_changed_before_entry','checked_readback_after_helper_update','checked_typing_after_helper_update'].includes(s.verified?.recovery);
  // A completed receipt can be explicitly rechecked once later that day.
  // The new pass rereads every source and destination; uncertain writes remain blocked.
  const completedRun=completedRecheckReport(s)&&s.finished_recheck_day!==t.day;
  // A shipped helper fix may need one fresh, full-source pass on the same day.
  // Keep it distinct from uncertain-write recovery; the pass still re-reads all
  // Hub and IC values and the keep-higher plan prevents duplicate/lower scores.
  const updatedCodeRun=completedRecheckReport(s)
   &&s.updated_recheck_version!==chrome.runtime.getManifest().version;
  const repeat=safePreflightRepeat(s,t)||automaticSourceReadRepeat(s,t);
  const firstRun=s.manual_run_day!==t.day&&(cleanMissedRun||verifiedRecovery);
  const sameDay=s.day===t.day&&s.report?.day===t.day&&(firstRun||completedRun||updatedCodeRun||repeat||currentReceiptRecoveryGrant(s,t)||currentSourceChangedRecoveryGrant(s,t)||currentReadbackMismatchRecoveryGrant(s,t)||currentTypingStoppedRecoveryGrant(s,t));
  return s.enabled&&(sameDay||freshManualDay(s,t))
   &&!s.blocked&&!s.inflight&&!s.report_pending&&!(s.outbox?.length)&&!pending.size;
 }
 async function run(manual=false,manualNow=false){
  if(busy)return;busy=true;
  try{
   let s=await read();
   if(manual&&!retryable(s))return;
   const requestedAt=M.clock();
   const preflightRepeat=manualNow&&safePreflightRepeat(s,requestedAt);
   if(manualNow&&!manualNowAllowed(s,requestedAt))return;
   if(manualNow&&automaticSourceReadRepeat(s,requestedAt)){
    // Consume before tabs/network; the exact stopped report remains in Hub history.
    // manual_run_day is set only when the new explicit pass actually starts.
    s.manual_run_retry_day=requestedAt.day;
    await write(s);
   }
   if(manualNow&&currentReceiptRecoveryGrant(s,requestedAt)){
    // Consume the explicit once-per-version grant before any tab/network action.
    // The earlier report remains unchanged and its private history is retained.
    s.receipt_recheck_version=chrome.runtime.getManifest().version;
    s.receipt_recheck_run_id=s.report.run_id;s.receipt_recheck_day=requestedAt.day;
    await write(s);
   }
   if(manualNow&&currentSourceChangedRecoveryGrant(s,requestedAt)){
    // The source-specific grant is consumed before any tab/network action.
    // Its exact old report stays in verified.prior_report_signature and Hub history.
    s.source_recheck_version=chrome.runtime.getManifest().version;
    s.source_recheck_run_id=s.report.run_id;s.source_recheck_day=requestedAt.day;
    await write(s);
   }
   if(manualNow&&currentReadbackMismatchRecoveryGrant(s,requestedAt)){
    // Consume this distinct checked-readback grant before any tab or network work.
    // Zero successful result counts do not imply that the old Save was unattempted.
    s.readback_recheck_version=chrome.runtime.getManifest().version;
    s.readback_recheck_run_id=s.report.run_id;s.readback_recheck_day=requestedAt.day;
    await write(s);
   }
   if(manualNow&&currentTypingStoppedRecoveryGrant(s,requestedAt)){
    // Consume before any tab/network action; preserve the stopped report signature.
    s.typing_recheck_version=chrome.runtime.getManifest().version;
    s.typing_recheck_run_id=s.report.run_id;s.typing_recheck_day=requestedAt.day;
    await write(s);
   }
   const completedRecheck=manualNow&&completedRecheckReport(s);
   const hubs=await chrome.tabs.query({url:C.HUB_ORIGIN+'/*'});
   s.outbox=s.outbox||[];
   for(const day of M.missed(s)){
    s.outbox.push({day,run_id:crypto.randomUUID(),code:'missed_noon',roster:'unavailable',items:[]});s.day=day;
   }
   if(s.outbox.length>10)s.outbox=s.outbox.slice(-10);
   await write(s);
   let backlogFailure=null;
   if(hubs.length===1){
    while(s.outbox.length){try{const reply=await hub(hubs[0].id,'report',s.outbox[0]);if(reply?.recorded!==true)throw failure('receipt_unconfirmed','hub_report_not_acknowledged');s.outbox.shift();await write(s);}catch(e){backlogFailure=M.preflight('summary_backlog',e);break;}}
   }
   // Summary delivery may retry; no IC operation is repeated.
   if(s.report_pending&&hubs.length===1)await publish(s,hubs[0].id);
   const due=manual?{day:M.clock().day,skip:false}:manualNow?{day:requestedAt.day,skip:false}:M.due(s);if(!due)return;
   if(s.report_pending&&s.report)s.outbox.push(s.report);
   if(s.outbox.length>10)s.outbox=s.outbox.slice(-10);
   if(s.blocked&&!s.blocked_run_id){s.blocked_run_id=s.report?.run_id;s.blocked_assignments=s.report?.items?.filter(i=>['readback_incomplete','save_unconfirmed','readback_mismatch'].includes(i.code)).map(i=>i.assignment)||[];}
   s.day=due.day;
   if(manualNow){
    s.manual_run_day=due.day;
    if(completedRecheck){
     s.finished_recheck_day=due.day;
     s.updated_recheck_version=chrome.runtime.getManifest().version;
    }
    if(preflightRepeat)s.manual_run_retry_day=due.day;
   }
   s.report={day:due.day,run_id:crypto.randomUUID(),code:due.skip?'missed_noon':s.blocked?'uncertain_blocked':'running',roster:'unavailable',items:[]};
   s.report_pending=true;s.execution_started=false;s.run_helper_version=chrome.runtime.getManifest().version;
   // A durable stamp BEFORE any network or tab action enforces once per day.
   await write(s);
   if(hubs.length!==1){s.report.code='hub_unavailable';s.report.preflight=M.preflight('hub_connection',{cause:hubs.length?'hub_tab_count':'hub_tab_missing'});await write(s);return;}
   const source=hubs[0].id;sourceTab=source;
   const startingFailure=await publish(s,source);
   if(due.skip||s.blocked)return;
   if(s.report_pending||s.outbox.length){s.report.code='receipt_unconfirmed';s.report.preflight=startingFailure||backlogFailure||M.preflight('summary_backlog',{cause:'previous_receipt_pending'});s.report_pending=true;await write(s);return;}
   let targetInProgress=null;
   try{
    if(pending.size)throw Error('panel_unusable');
    const catalog=await hub(source,'catalog');
    if(!Array.isArray(catalog.assignments)||catalog.assignments.length>300)throw Error('hub_unavailable');
    const tabs=await chrome.tabs.query({url:C.IC_ORIGIN+'/*'});
    if(tabs.length!==1)throw Error('ic_unavailable');
    const destination=tabs[0].id;
    // Read-only probe detects a visible sign-in screen before any navigation.
    const probe=await chrome.tabs.sendMessage(destination,{type:'morning_session'},{frameId:0}).catch(()=>null);
    if(!probe?.ok)throw Error(probe?.code||'ic_unavailable');
    for(const entry of catalog.assignments){
     const at=M.clock(),setting=await read();
     if(!setting.enabled)throw Error('disabled');
     if(at.day!==s.day||(!manualNow&&at.minutes>=720))throw Error('missed_noon');
     const skipped=M.skipByYear(entry,Date.parse(`${s.day}T12:00:00Z`));
     if(skipped){
      const assignment=skipped==='week_1_intentionally_skipped'
       ?'Week 1 (intentionally skipped)'
       :`${entry.assignment} (intentionally skipped this school year)`;
      const summary=item({...entry,assignment},'nothing_to_enter');
      s.report.items.push(summary);
      continue;
     }
     const scope={class:entry.class,week:entry.week,form:entry.form};
     const summary=item(entry,'running');
     let initial;
     try{initial=await hub(source,'list',scope);}catch(e){summary.code=code(e);s.report.items.push(summary);throw e;}
     if(initial.code!=='ready'){
      summary.code=safe.has(initial.code)?initial.code:'mapping_required';
      if(initial.code==='source_scores_held'){
       const checks=initial.source_holds?.checks;
       if(Number.isInteger(checks)&&checks>0&&checks<=3600)summary.source_holds={checks};
       else summary.code='coverage_incomplete';
      }
      s.report.items.push(summary);await publish(s,source);
      if(s.report_pending)throw Error('receipt_unconfirmed');
      continue;
     }
     s.report.items.push(summary);
     let list,targets;
     try{list=E.validateList(initial.list);targets=M.targets(list,initial.targets,C.IC_ORIGIN);}catch{summary.code='mapping_required';await publish(s,source);if(s.report_pending)throw Error('receipt_unconfirmed');continue;}
     const results=[];
     for(const binding of targets){
      targetInProgress={summary,binding};
      // A completed earlier column does not complete the assignment. Persist
      // the next column's in-progress state before its read-only preflight.
      summary.code='running';await write(s);
      // Re-read source and exact mapping before each section/column.
      const fresh=await hub(source,'list',scope);
      if(fresh.code!=='ready'||!initial.revision||fresh.revision!==initial.revision||JSON.stringify(fresh.list.rows)!==JSON.stringify(list.rows)||JSON.stringify(fresh.targets)!==JSON.stringify(initial.targets))throw Error('source_changed');
      await usable(destination,binding.url);
      active={run_id:s.report.run_id,nonce:crypto.randomUUID(),tab:destination,url:binding.url,manualNow};
      // Mark potentially uncertain BEFORE sending a write-capable request.
      s.blocked=true;s.blocked_run_id=s.report.run_id;s.blocked_assignments=[summary.assignment];s.inflight=true;s.execution_started=true;await write(s);
      let response;
      try{response=await deadline(chrome.tabs.sendMessage(destination,{type:'morning_execute',run_id:active.run_id,nonce:active.nonce,list,target:binding},{frameId:0}),120000);}catch{response=null;}
      active=null;s.inflight=false;
      if(!response?.ok){summary.code=response?.not_started&&safe.has(response.code)?response.code:'save_unconfirmed';if(response?.not_started)s.blocked=false;await write(s);throw Error(safe.has(summary.code)?summary.code:'panel_unusable');}
      const result=E.validateResult(response.data.result);
      if(result.list_id!==list.list_id||result.form_id!==list.form_id||result.section!==binding.section||result.column!==binding.column)throw Error('panel_unusable');
      summary.code=response.data.code;
      if(['readback_incomplete','readback_mismatch','save_unconfirmed'].includes(summary.code)){
       // Student names are sent only to the private ledger, never local storage.
       try{await hub(source,'recovery',{event_id:crypto.randomUUID(),run_id:s.report.run_id,type:'readback',assignment:summary.assignment,section:binding.section,column:binding.column,code:summary.code,names:response.data.diagnostics||[]});}catch{/* The uncertainty block stays in place even if diagnostics cannot be saved. */}
      }
      for(const k of ['filled','raised','kept','left'])summary[k]+=result.counts[k];
      for(const k of Object.keys(summary.reasons))summary.reasons[k]+=response.data.reasons[k]||0;
      results.push(result);
      const good=['entered','checked'].includes(result.outcome)&&result.certain&&!result.counts.not_saved;
      const untouched=result.outcome==='stopped'&&['ic_changed','ic_unreadable'].includes(response.data.code);
      s.blocked=!good&&!untouched;await write(s);
      if(!good)throw Error('panel_unusable');
     }
     targetInProgress=null;
     try{await hub(source,'record',{run_id:s.report.run_id,scope,revision:initial.revision,list,targets,results});}catch{summary.code='receipt_unconfirmed';throw Error('receipt_unconfirmed');}
     await publish(s,source);
     if(s.report_pending)throw Error('receipt_unconfirmed');
    }
    s.report.code=batchCode(s.report.items);
   }catch(e){
    s.report.code=code(e);const last=s.report.items.at(-1);
    if(last?.code==='running')last.code=s.report.code;
    if(targetInProgress&&last===targetInProgress.summary){
     const binding=targetInProgress.binding,context=` · ${String(binding.section).slice(0,100)} · ${binding.column==='notes'?'Notes':'Revision'}`;
     last.assignment=last.assignment.slice(0,200-context.length)+context;
    }
   }
   await publish(s,source);
  }finally{active=null;sourceTab=null;busy=false;}
 }
 chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(m?.type==='morning_keepalive'&&busy&&sender.id===chrome.runtime.id&&sender.frameId===0&&sender.tab?.id===sourceTab){reply({ok:true});return;}
  if(m?.type==='morning_authorize'){authorize(m,sender).then(reply,()=>reply({ok:false}));return true;}
  const popup=sender.id===chrome.runtime.id&&sender.url===chrome.runtime.getURL('popup.html')&&!sender.tab;
  if(!popup||!['morning_status','morning_setting','morning_clear','morning_retry','morning_run_now'].includes(m?.type))return;
  locked(async()=>{
   const s=await read();
   if(m.type==='morning_setting'&&typeof m.enabled==='boolean'){const enabling=m.enabled&&!s.enabled;s.enabled=m.enabled;await chrome.storage.local.set({morning_enabled:m.enabled,...(enabling?{morning_enabled_day:M.clock().day}:{})});await ensureAlarm();}
   let recovery_error='';
   const legacyVerification=m.current_mapped_confirmed===true&&legacyRecoveryAllowed(s);
   const receiptVerification=m.current_mapped_confirmed===true&&m.receipt_recovery_confirmed===true&&currentReceiptRecoveryAllowed(s);
   const sourceVerification=m.current_mapped_confirmed===true&&m.source_recovery_confirmed===true&&currentSourceChangedRecoveryAllowed(s);
   const readbackVerification=m.current_mapped_confirmed===true&&m.readback_recovery_confirmed===true&&currentReadbackMismatchRecoveryAllowed(s);
   const typingVerification=m.current_mapped_confirmed===true&&m.typing_recovery_confirmed===true&&currentTypingStoppedRecoveryAllowed(s);
   const receiptVersion=(receiptVerification||sourceVerification||readbackVerification||typingVerification)?chrome.runtime.getManifest().version:null;
   const currentVerification=legacyVerification||receiptVerification||sourceVerification||readbackVerification||typingVerification;
   if(m.type==='morning_clear'&&m.confirmed===true&&!busy&&((s.blocked&&m.current_mapped_confirmed!==true&&!currentReadbackMismatchReport(s)&&!currentTypingStoppedReport(s))||currentVerification)&&s.report){
    const note=typeof m.note==='string'?m.note.trim():'';
    const verificationNote=typingVerification?`Current mapped IC grades checked after helper update for one new pass. Prior ${s.report.day} run remains incomplete after typing stopped before Save; its history is unchanged. ${note}`:
     readbackVerification?`Current mapped IC grades checked after helper update for one new pass. Prior ${s.report.day} run remains incomplete after a saved-result mismatch; its Fill and Save history is unchanged. ${note}`:
     sourceVerification?`Current mapped IC grades checked after source repair for one new source pass. Prior ${s.report.day} run remains stopped at a source change and incomplete. ${note}`:
     receiptVerification?`Current mapped IC scores checked for one new run. Prior ${s.report.day} run remains incomplete and its entry receipt unconfirmed. ${note}`:
     legacyVerification?`Current mapped IC scores verified for a new run. Prior ${s.report.day} receipt remains unconfirmed. ${note}`:note;
    if(!note||verificationNote.length>500)recovery_error='Add a short verification note.';
    else try{
     const hubs=await chrome.tabs.query({url:C.HUB_ORIGIN+'/*'});
     if(hubs.length!==1)throw Error();
     let ic=null;
     if(currentVerification){
      const tabs=await chrome.tabs.query({url:C.IC_ORIGIN+'/*'});
      if(tabs.length!==1)throw Error();
      ic=await chrome.tabs.get(tabs[0].id);
      if(new URL(ic.url).origin!==C.IC_ORIGIN||(await chrome.tabs.sendMessage(ic.id,{type:'morning_session'},{frameId:0}))?.ok!==true)throw Error();
     }
     const hubUrl=hubs[0].url,icUrl=ic?.url;
     const run_id=currentVerification?s.report.run_id:s.blocked_run_id||s.report.run_id,event_id=crypto.randomUUID();
     const saved=await hub(hubs[0].id,'recovery',{event_id,run_id,type:'verified',note:verificationNote});
     if(saved?.recorded!==true||saved.record?.run_id!==run_id||saved.record?.event_id!==event_id||saved.record?.type!=='verified'||saved.record?.note!==verificationNote||typeof saved.record?.by!=='string'||!saved.record.by.trim()||typeof saved.record?.at!=='string'||!Number.isFinite(Date.parse(saved.record.at)))throw Error();
     const fresh=await read();
     if(busy||fresh.report?.run_id!==s.report.run_id||(legacyVerification&&(!legacyRecoveryAllowed(fresh)
      ||fresh.day!==s.day||JSON.stringify(fresh.report)!==JSON.stringify(s.report)))
      ||(receiptVerification&&(!currentReceiptRecoveryAllowed(fresh)||fresh.day!==s.day
       ||JSON.stringify(fresh.report)!==JSON.stringify(s.report)||chrome.runtime.getManifest().version!==receiptVersion))
      ||(sourceVerification&&(!currentSourceChangedRecoveryAllowed(fresh)||fresh.day!==s.day
       ||JSON.stringify(fresh.report)!==JSON.stringify(s.report)||chrome.runtime.getManifest().version!==receiptVersion))
      ||(readbackVerification&&(!currentReadbackMismatchRecoveryAllowed(fresh)||fresh.day!==s.day
       ||JSON.stringify(fresh.report)!==JSON.stringify(s.report)||chrome.runtime.getManifest().version!==receiptVersion))
      ||(typingVerification&&(!currentTypingStoppedRecoveryAllowed(fresh)||fresh.day!==s.day
       ||JSON.stringify(fresh.report)!==JSON.stringify(s.report)||chrome.runtime.getManifest().version!==receiptVersion)))throw Error();
     if(currentVerification&&((await chrome.tabs.get(hubs[0].id)).url!==hubUrl||(await chrome.tabs.get(ic.id)).url!==icUrl))throw Error();
     Object.assign(s,fresh,{blocked:false,inflight:false,verified:{run_id,at:saved.record.at,by:saved.record.by,
      ...(currentVerification?{scope:'current_mapped_assignments',prior_day:s.report.day}:{}),
      ...(typingVerification?{recovery:'checked_typing_after_helper_update',event_id,for_helper_version:receiptVersion,
       prior_helper_version:'0.5.25',prior_report_signature:JSON.stringify(s.report),
       ...(s.typing_recheck_version==='0.5.25'?{prior_typing_recovery:s.verified}:{})}:{}),
      ...(readbackVerification?{recovery:'checked_readback_after_helper_update',event_id,for_helper_version:receiptVersion,
       prior_helper_version:'0.5.23',prior_report_signature:JSON.stringify(s.report),
       ...(!Object.hasOwn(s,'run_helper_version')?{prior_source_recovery:s.verified}:{})}:{}),
      ...(sourceVerification?{recovery:'source_changed_before_entry',event_id,for_helper_version:receiptVersion,
       prior_report_signature:JSON.stringify(s.report)}:{}),
      ...(receiptVerification?{recovery:'partial_receipt',event_id,for_helper_version:receiptVersion,
       prior_report_signature:JSON.stringify(s.report)}:{})}});
     await write(s);
    }catch{recovery_error='Verification could not be saved. A new run is still unavailable. Keep one signed-in Hub tab open and try again.';}
   }
   if(m.type==='morning_retry'&&!busy&&retryable(s))run(true).catch(()=>{});
   if(m.type==='morning_run_now'&&!busy&&manualNowAllowed(s))run(false,true).catch(()=>{});
   return {enabled:s.enabled,blocked:s.blocked,report:s.report||null,busy,verified:s.verified||null,recovery_error,can_retry:!busy&&retryable(s),can_run_now:!busy&&manualNowAllowed(s),can_verify_legacy:!busy&&legacyRecoveryAllowed(s),can_verify_current_receipt:!busy&&currentReceiptRecoveryAllowed(s),can_verify_current_source_change:!busy&&currentSourceChangedRecoveryAllowed(s),can_verify_current_readback:!busy&&currentReadbackMismatchRecoveryAllowed(s),can_verify_current_typing:!busy&&currentTypingStoppedRecoveryAllowed(s)};
  }).then(reply,()=>reply({error:true}));return true;
 });
 globalThis.HubMorningStatus=async()=>{
  const s=await read(),tabs=await chrome.tabs.query({url:C.IC_ORIGIN+'/*'});
  let session='unavailable';
  if(tabs.length===1){const reply=await chrome.tabs.sendMessage(tabs[0].id,{type:'morning_session'},{frameId:0}).catch(()=>null);session=reply?.ok?'ready':reply?.code==='signed_out'?'signed_out':'unavailable';}
  return {enabled:s.enabled,blocked:s.blocked,busy,code:s.report?.code||'',run_id:(s.blocked?s.blocked_run_id:null)||s.report?.run_id||'',session,verified:s.verified||null,
   assignments:s.blocked_assignments||s.report?.items?.filter(i=>['readback_incomplete','save_unconfirmed','readback_mismatch'].includes(i.code)).map(i=>i.assignment)||[]};
 };
 async function ensureAlarm(){
  if(!(await chrome.alarms.get(ALARM)))await chrome.alarms.create(ALARM,{periodInMinutes:1});
 }
 async function boot(){
  await chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'});
  const s=await read();
  if(s.report?.code==='running'){
   s.report.code='interrupted';s.report.items.forEach(i=>{if(i.code==='running')i.code='interrupted';});
   s.report_pending=true;await write(s);
  }
  await ensureAlarm();
 }
 const ready=boot();
 chrome.alarms.onAlarm.addListener(a=>{if(a.name===ALARM)ready.then(run).catch(()=>{});});
 chrome.runtime.onStartup.addListener(()=>ready.then(run).catch(()=>{}));
})();
