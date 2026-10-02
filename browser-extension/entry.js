/* Hub v2 "enter the IC list": the teacher sends one assignment's IC list from
 * the Teaching Hub, then opens that assignment's score panel in Infinite
 * Campus. This file holds the rules, with no browser access: validating the
 * list, matching IC rows to it by name, the keep-higher plan, and the
 * type → save → reload → read back sequence run through ports. */
(function(root){
 'use strict';
 const CHANNEL='hub-ic-entry', RESULT_CHANNEL='hub-ic-entry-result', VERSION=1, SCHEMA='hub-ic-list/v1';
 const MAX_ROWS=300, MAX_LISTS=12, LIST_TTL_MS=3*3600*1000;
 const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
 const CLASSES=['biology','forensics','anatomy'], KINDS=['score','zero','excused','check','none'];
 const COLUMNS=['notes','revision'], STATUSES=['before','open','closed'];
 const OUTCOMES=['entered','checked','partial','stopped','uncertain'];
 const COLUMN_NAME={notes:'Notes',revision:'Revision'};
 const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
 const exact=(v,keys)=>object(v)&&Object.keys(v).sort().join('|')===[...keys].sort().join('|');
 const text=(v,max=200)=>typeof v==='string'&&v.length<=max&&!/[\u0000-\u001f\u007f]/.test(v);
 const clone=v=>JSON.parse(JSON.stringify(v));
 const freeze=v=>{if(v&&typeof v==='object'){Object.values(v).forEach(freeze);Object.freeze(v);}return v;};
 const fail=code=>{throw Error(code);};
 // Hub scores are out of 10 with one decimal place.
 const tenth=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<=10&&Math.abs(Math.round(v*10)-v*10)<1e-9;
 const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);

 function validateList(value,now=Date.now()){
  if(!exact(value,['schema','list_id','class_key','class_name','week','form_id','assignment','form_label','status','status_text','sent_at','rows'])||value.schema!==SCHEMA||!UUID.test(value.list_id)||!CLASSES.includes(value.class_key)||!STATUSES.includes(value.status))fail('invalid_list');
  for(const key of ['class_name','week','assignment','form_label'])if(!text(value[key]))fail('invalid_list');
  if(!text(value.status_text,400)||!/^[A-Za-z0-9_-]{10,100}$/.test(value.form_id))fail('invalid_list');
  const sent=Date.parse(value.sent_at);
  if(!text(value.sent_at,40)||!Number.isFinite(sent)||sent>now+60000)fail('invalid_list');
  if(now-sent>LIST_TTL_MS)fail('list_expired');
  if(!Array.isArray(value.rows)||!value.rows.length||value.rows.length>MAX_ROWS)fail('invalid_list');
  const seen=new Set();
  for(const row of value.rows){
   if(!exact(row,['last','first','section','kind','notes','revision','reason'])||!KINDS.includes(row.kind))fail('invalid_list');
   if(!text(row.last,100)||!row.last.trim()||!text(row.first,100)||!text(row.section,100)||!row.section.trim()||!text(row.reason))fail('invalid_list');
   for(const key of COLUMNS)if(row[key]!==null&&!tenth(row[key]))fail('invalid_list');
   if(row.kind==='zero'&&(row.notes!==0||row.revision!==0))fail('invalid_list');
   if(row.kind==='score'&&row.notes===null&&row.revision===null)fail('invalid_list');
   if(!['score','zero'].includes(row.kind)&&(row.notes!==null||row.revision!==null))fail('invalid_list');
   const key=JSON.stringify([plain(row.last),plain(row.first),row.section]);
   if(seen.has(key))fail('invalid_list');seen.add(key);
  }
  return freeze(clone(value));
 }

 // ---- Names: the same folding the Hub's roster uses ----
 function plain(value){return String(value||'').normalize('NFKD').replace(/[̀-ͯ]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();}
 const compact=value=>plain(value).replace(/ /g,'');
 function firstNames(first){
  const legal=plain(String(first||'').replace(/\([^)]*\)/g,' '));
  const names=new Set([legal,legal.split(' ')[0]]);
  for(const nick of String(first||'').matchAll(/\(([^)]+)\)/g))names.add(plain(nick[1]));
  names.delete('');return names;
 }
 const hubKeys=row=>new Set([...firstNames(row.first)].map(f=>compact(f+' '+row.last)));
 // IC shows "Last, First Middle"; anything without a comma is compared whole.
 function icKeys(display){
  const at=display.indexOf(',');
  if(at<0)return new Set([compact(display)].filter(Boolean));
  const last=display.slice(0,at);
  return new Set([...firstNames(display.slice(at+1))].map(f=>compact(f+' '+last)));
 }
 // A pair counts only when each side has exactly one candidate on the other.
 function matchNames(rows,icNames){
  const keys=rows.map(hubKeys),byIC=icNames.map(name=>{const mine=icKeys(name);return rows.map((_,i)=>i).filter(i=>[...keys[i]].some(k=>mine.has(k)));});
  const claims=new Map();
  byIC.forEach(list=>list.forEach(i=>claims.set(i,(claims.get(i)||0)+1)));
  return byIC.map(list=>list.length===1&&claims.get(list[0])===1?{row:list[0]}:list.length?{ambiguous:true}:{none:true});
 }

 function columnFromTitle(title){
  if(/\(\s*F\s*\)\s*$/i.test(title||''))return 'notes';
  if(/\(\s*S\s*\)\s*$/i.test(title||''))return 'revision';
  return null;
 }
 const STOP=new Set(['the','and','for','with','turn','cycle','week','lesson','notes','revision','from','into','your','what']);
 const words=value=>new Set(plain(value).split(' ').filter(w=>w.length>2&&!/^\d+$/.test(w)&&!STOP.has(w)));
 function titleOverlap(list,title){const mine=words(title);return [...words(list.assignment+' '+list.form_label)].filter(w=>mine.has(w)).length;}
 // The list whose lesson shares the most words with the IC title; newest on a tie.
 function pickList(lists,title){
  let best=null,score=-1;
  for(const list of lists){const s=titleOverlap(list,title);if(s>score||(s===score&&Date.parse(list.sent_at)>Date.parse(best.sent_at))){best=list;score=s;}}
  return best?{list:best,overlap:score}:null;
 }

 function validateSnapshot(value){
  if(!exact(value,['context','rows','saved'])||!object(value.context)||typeof value.saved!=='boolean'||!Array.isArray(value.rows)||value.rows.length>MAX_ROWS)fail('invalid_snapshot');
  const labels=new Set();
  for(const row of value.rows){
   if(!exact(row,['label','name','score','other','flags'])||!text(row.label,1000)||!text(row.name,300)||!row.name||labels.has(row.label))fail('invalid_snapshot');
   if(row.score!==null&&(typeof row.score!=='number'||!Number.isFinite(row.score)||row.score<0))fail('invalid_snapshot');
   if(row.other!==null&&(!text(row.other,40)||row.score!==null))fail('invalid_snapshot');
   if(!Array.isArray(row.flags)||row.flags.some(f=>!text(f,100)))fail('invalid_snapshot');
   labels.add(row.label);
  }
  return value;
 }

 // What the helper will do in this IC score panel with this list's column.
 // Keep the higher score: fill blanks, raise lower scores, never lower one.
 function buildPlan(listValue,snapshotValue,column,{now=Date.now()}={}){
  const list=validateList(listValue,now),snapshot=validateSnapshot(clone(snapshotValue));
  if(!COLUMNS.includes(column))fail('column_required');
  if(!snapshot.saved)fail('ic_not_saved');
  if(!snapshot.rows.length)fail('ic_no_students');
  const matches=matchNames(list.rows,snapshot.rows.map(r=>r.name));
  const matched=matches.filter(m=>m.row!==undefined);
  if(matched.length<Math.max(1,Math.ceil(0.6*snapshot.rows.length)))fail('students_dont_match');
  const sections=new Set(matched.map(m=>list.rows[m.row].section));
  if(sections.size!==1)fail('several_sections');
  const section=[...sections][0];
  const items=snapshot.rows.map((ic,i)=>{
   const m=matches[i],hub=m.row===undefined?null:list.rows[m.row];
   const item={label:ic.label,name:ic.name,current:ic.score,other:ic.other,flags:[...ic.flags],hub:hub?{last:hub.last,first:hub.first,kind:hub.kind,reason:hub.reason}:null,value:null,action:'leave',why:''};
   const leave=why=>Object.assign(item,{action:'leave',why});
   if(!hub)return leave(m.ambiguous?'name matches more than one student on the Hub list':'not on the Hub list');
   if(hub.kind==='none')return Object.assign(item,{action:'skip',why:hub.reason||'nothing to enter yet'});
   if(hub.kind==='excused')return ic.flags.includes('Exempt')?Object.assign(item,{action:'keep',why:'Exempt in IC'}):leave('new student: mark Exempt in IC yourself');
   if(hub.kind==='check')return leave(hub.reason?`check by hand (${hub.reason})`:'check by hand');
   const value=hub[column];
   if(value===null)return Object.assign(item,{action:'skip',why:'N/A on the Hub'});
   item.value=value;
   if(ic.other!==null)return leave(`IC shows “${ic.other}”: check by hand`);
   if(ic.score!==null&&ic.score>=value-1e-9)return Object.assign(item,{action:'keep',why:ic.score>value?'IC is higher':'same'});
   if(ic.flags.length)return leave(`IC flag (${ic.flags.join(', ')}): check by hand`);
   return Object.assign(item,{action:ic.score===null?'fill':'raise',why:''});
  });
  const found=new Set(matched.map(m=>m.row));
  const missing=list.rows.map((row,i)=>({row,i})).filter(({row,i})=>row.section===section&&!found.has(i)&&row.kind!=='none').map(({row})=>`${row.last}, ${row.first}`);
  const count=action=>items.filter(item=>item.action===action).length;
  return freeze({list_id:list.list_id,form_id:list.form_id,class_key:list.class_key,week:list.week,column,section,context:clone(snapshot.context),snapshot:clone(snapshot),items,missing,
   changes:items.filter(item=>['fill','raise'].includes(item.action)).map(item=>({label:item.label,name:item.name,before:item.current,value:item.value,action:item.action})),
   counts:{fill:count('fill'),raise:count('raise'),keep:count('keep'),leave:count('leave'),skip:count('skip')}});
 }

 const rowView=row=>({label:row.label,name:row.name,score:row.score,other:row.other,flags:row.flags});
 function expected(plan){
  const rows=plan.snapshot.rows.map(rowView);
  for(const change of plan.changes){const row=rows.find(r=>r.label===change.label);row.score=change.value;}
  return rows;
 }
 const sameScore=(a,b)=>a===null||b===null?a===b:Math.abs(a-b)<1e-9;

 // Type, save, reload and read back. Ports touch the page; this decides.
 // A result is certain when the reloaded panel was read completely.
 async function execute(plan,ports){
  const counts=(extra={})=>({filled:0,raised:0,kept:plan.counts.keep,left:plan.counts.leave,not_saved:0,...extra});
  const done=(outcome,code,certain,extra={})=>freeze({outcome,code,certain,counts:counts(extra.counts),failed:extra.failed||[],unexpected:extra.unexpected||[]});
  let typed=0,saving=false;
  try{
   const before=validateSnapshot(await ports.snapshot(true));
   if(!same(before.context,plan.context)||!same(before.rows.map(rowView),plan.snapshot.rows.map(rowView)))return done('stopped','ic_changed',true);
   if(!plan.changes.length)return done('checked','nothing_to_enter',true);
   for(const change of plan.changes){typed++;await ports.fill(change);}
   const want=expected(plan),typedView=validateSnapshot(await ports.snapshot(false));
   if(!same(typedView.context,plan.context)||!same(typedView.rows.map(rowView),want))return done('stopped','typed_values_differ',true,{counts:{not_saved:plan.changes.length}});
   saving=true;await ports.save();
   const after=validateSnapshot(await ports.reload());
   const afterByLabel=new Map(after.rows.map(r=>[r.label,r]));
   const unread=want.filter(r=>{const got=afterByLabel.get(r.label);return !got||got.name!==r.name||!same(got.flags,r.flags);}).map(r=>r.name);
   if(!after.saved||!same(after.context,plan.context)||unread.length||after.rows.length!==want.length)return done('uncertain','readback_incomplete',false,{failed:unread.length?unread:plan.changes.map(r=>r.name)});
   const failed=[],unexpected=[];let filled=0,raised=0;
   after.rows.forEach((row,i)=>{
    const goal=want.find(r=>r.label===row.label),change=plan.changes.find(c=>c.label===row.label);
    if(change){if(sameScore(row.score,goal.score)&&row.other===null){if(change.action==='fill')filled++;else raised++;}else failed.push(row.name);}
    else if(!sameScore(row.score,goal.score)||row.other!==goal.other)unexpected.push(row.name);
   });
   const outcome=failed.length||unexpected.length?'partial':'entered';
   return done(outcome,outcome==='entered'?'read_back':'readback_mismatch',true,{counts:{filled,raised,not_saved:failed.length},failed,unexpected});
  }catch(error){
   // Before Save, anything typed is unsaved; after it, IC may hold some of it.
   if(!saving)return done('stopped',typed?'typing_stopped':'ic_unreadable',true,{counts:{not_saved:plan.changes.length}});
   return done('uncertain','save_unconfirmed',false,{failed:plan.changes.map(r=>r.name)});
  }
 }

 // The summary that goes back to the Hub page: counts only, no names.
 function hubResult(plan,run,title,at=new Date().toISOString()){
  return validateResult({list_id:plan.list_id,form_id:plan.form_id,class_key:plan.class_key,week:plan.week,column:plan.column,section:plan.section,ic_title:String(title||'').slice(0,200),outcome:run.outcome,certain:run.certain,counts:{...run.counts},at});
 }
 function validateResult(value){
  if(!exact(value,['list_id','form_id','class_key','week','column','section','ic_title','outcome','certain','counts','at'])||!UUID.test(value.list_id)||!/^[A-Za-z0-9_-]{10,100}$/.test(value.form_id)||!CLASSES.includes(value.class_key)||!text(value.week)||!COLUMNS.includes(value.column)||!text(value.section,100)||!text(value.ic_title)||!OUTCOMES.includes(value.outcome)||typeof value.certain!=='boolean'||!text(value.at,40)||!Number.isFinite(Date.parse(value.at)))fail('invalid_result');
  if(!exact(value.counts,['filled','raised','kept','left','not_saved'])||Object.values(value.counts).some(n=>!Number.isSafeInteger(n)||n<0||n>MAX_ROWS))fail('invalid_result');
  return freeze(clone(value));
 }

 const api=Object.freeze({CHANNEL,RESULT_CHANNEL,VERSION,SCHEMA,MAX_LISTS,LIST_TTL_MS,UUID,COLUMN_NAME,validateList,validateSnapshot,validateResult,plain,matchNames,columnFromTitle,titleOverlap,pickList,buildPlan,expected,execute,hubResult});
 root.HubIcEntry=api;
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(globalThis);
