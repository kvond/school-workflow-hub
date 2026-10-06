/* Deterministic rules for the explicitly opted-in weekday morning path. */
(function(root){
 'use strict';
 const E=root.HubIcEntry;
 const fail=code=>{throw Error(code);};
 const safe=v=>typeof v==='string'&&v.length<=200&&!/[\x00-\x1f\x7f]/.test(v);
 const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
 function clock(now=Date.now()){
  const p=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit',weekday:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(now)).map(p=>[p.type,p.value]));
  return {day:`${p.year}-${p.month}-${p.day}`,weekday:!['Sat','Sun'].includes(p.weekday),minutes:Number(p.hour)*60+Number(p.minute)};
 }
 function due(state,now=Date.now()){
  const t=clock(now);
  if(!state.enabled||!t.weekday||t.minutes<420||state.day>=t.day)return null;
  return {day:t.day,skip:t.minutes>=720};
 }
 function missed(state,now=Date.now()){
  const today=clock(now).day,start=[state.day||'',state.enabled_day||''].sort().at(-1);
  if(!state.enabled||!start||start>=today)return [];
  const days=[];
  for(let date=new Date(start+'T12:00:00Z'),i=0;i<370;i++){
   date.setUTCDate(date.getUTCDate()+1);const day=date.toISOString().slice(0,10);
   if(day>=today)break;
   if(![0,6].includes(date.getUTCDay()))days.push(day);
  }
  return days.slice(-10);
 }
 function need(list){return [...new Set(list.rows.map(r=>r.section))].sort().flatMap(section=>['notes','revision'].filter(column=>list.rows.some(r=>r.section===section&&['score','zero'].includes(r.kind)&&r[column]!==null)).map(column=>[section,column]));}
 function target(value,origin){
  const fields=['section','column','url','section_id','term_id','year','school_id','calendar_id','structure_id','assignment_id','score_group','task_id','label_code'];
  if(!value||Object.keys(value).sort().join('|')!==fields.sort().join('|')||!safe(value.section)||!['notes','revision'].includes(value.column))fail('mapping_required');
  for(const key of ['section_id','term_id','year','school_id','calendar_id','structure_id','assignment_id','score_group','task_id'])if(!/^\d{1,30}$/.test(value[key]))fail('mapping_required');
  // IC abbreviations may contain literal parentheses, including a truncated one.
  // They are compared as text below, never interpreted as a regular expression.
  if(typeof value.label_code!=='string'||!/^[A-Za-z0-9_()-]{1,80}$/.test(value.label_code))fail('mapping_required');
  let u;try{u=new URL(value.url);}catch{fail('mapping_required');}
  const path=`/campus/sis/control-center/classroom/curriculum/resource/${value.assignment_id}/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/${value.section_id}/objectSectionID/${value.assignment_id}/calendarID/${value.calendar_id}/structureID/${value.structure_id})`;
  if(u.origin!==origin||u.username||u.password||u.hash||u.pathname!==path)fail('mapping_required');
  const q={classroomSectionID:value.section_id,classroomTermID:value.term_id,year:value.year,schoolID:value.school_id,calendarID:value.calendar_id,structureID:value.structure_id};
  if([...u.searchParams.keys()].some(k=>!Object.hasOwn(q,k)&&k!=='appName'))fail('mapping_required');
  for(const [k,v]of Object.entries(q))if(u.searchParams.getAll(k).length!==1||u.searchParams.get(k)!==v)fail('mapping_required');
  return Object.freeze({...value});
 }
 function targets(list,values,origin){
  if(!Array.isArray(values)||values.length>12)fail('mapping_required');
  const ts=values.map(v=>target(v,origin)),required=need(list);
  if(ts.length!==required.length||required.some(([s,c])=>ts.filter(t=>t.section===s&&t.column===c).length!==1)||new Set(ts.map(t=>t.url)).size!==ts.length)fail('mapping_required');
  return ts;
 }
 function verify(plan,binding,surface){
  for(const key of ['section_id','term_id','year','school_id','calendar_id','assignment_id'])if(plan.context[key]!==binding[key])fail('destination_mapping_changed');
  if(plan.context.points!==10||plan.section!==binding.section||plan.column!==binding.column||surface.group!==binding.score_group||surface.structure_id!==binding.structure_id||!surface.task)fail('destination_mapping_changed');
  if(plan.snapshot.rows.some(r=>!r.label.startsWith(binding.label_code+' MP: Marking Period Score input for ')))fail('destination_mapping_changed');
 }
 function reasons(plan){
  const counts={flag:0,excused:0,check:0,name:0,missing:plan.missing.length};
  for(const i of plan.items){if(i.flags.length)counts.flag++;else if(i.hub?.kind==='excused')counts.excused++;else if(i.hub?.kind==='check')counts.check++;else if(!i.hub)counts.name++;}
  return counts;
 }
 async function run(list,binding,ports){
  E.validateList(list);
  const snapshot=await ports.snapshot(true),plan=E.buildPlan(list,snapshot,binding.column);
  verify(plan,binding,await ports.binding());
  const checked={...ports,reload:async()=>{const next=await ports.reload();verify({...plan,context:next.context,snapshot:next},binding,await ports.binding());return next;},snapshot:async saved=>{
   const next=await ports.snapshot(saved);
   verify({...plan,context:next.context,snapshot:next},binding,await ports.binding());
   return next;
  },save:async()=>{await ports.authorized();verify(plan,binding,await ports.binding());return ports.save();},fill:async change=>{await ports.authorized();return ports.fill(change);}};
  await ports.authorized();
  const result=await E.execute(plan,checked);
  return {result:E.hubResult(plan,result,plan.context.title),code:result.code,reasons:reasons(plan),diagnostics:[...new Set([...result.failed,...result.unexpected])]};
 }
 const api={clock,due,missed,need,target,targets,verify,reasons,run};root.HubMorning=Object.freeze(api);
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(globalThis);
