// Fictional rows and injected ports only: no browser, network, model or grade writes.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const E=require('../entry.js');
global.HubIcEntry=E;
const M=require('../morning.js');

const hub=(last,first,kind='score',notes=7,revision=null,reason='')=>({last,first,kind,notes,revision,section:'A4 Fictional',reason});
const ic=(name,score=null,flags=[],other=null)=>({label:'F MP: Marking Period Score input for '+name,name,score,flags,other});
const context=()=>({section_id:'42',term_id:'5',year:'2027',school_id:'7',calendar_id:'8',assignment_id:'123',title:'Fictional lesson (F)',points:10});
const source=rows=>({schema:E.SCHEMA,list_id:'11111111-1111-4111-8111-111111111111',class_key:'biology',class_name:'Biology',week:'Wk 05',form_id:'FICTIONAL_FORM_1234',assignment:'Fictional lesson',form_label:'Fictional lesson',status:'open',status_text:'Open',sent_at:new Date().toISOString(),rows});
const snapshot=rows=>({context:context(),rows,saved:true});
const target=()=>({section:'A4 Fictional',column:'notes',section_id:'42',term_id:'5',year:'2027',school_id:'7',calendar_id:'8',structure_id:'9',assignment_id:'123',score_group:'3',task_id:'1',label_code:'F'});
function basicPlan(){
 return E.buildPlan(source([hub('Avery','Ann','score',7.4),hub('Brook','Ben','score',6),hub('Cole','Cy','score',5)]),
  snapshot([ic('Avery, Ann'),ic('Brook, Ben',2),ic('Cole, Cy',15)]),'notes');
}
function transport(plan,options={}){
 const state={rows:structuredClone(plan.snapshot.rows),saved:true,calls:[],snapshots:0,fills:0};
 const ports={
  snapshot:async requireSaved=>{
   state.calls.push(requireSaved?'snapshot:saved':'snapshot:typed');state.snapshots++;
   if(options.snapshotThrows===state.snapshots)throw Error('Synthetic unreadable snapshot');
   const value={context:context(),rows:structuredClone(state.rows),saved:state.saved};
   return options.snapshot?options.snapshot(value,state):value;
  },
  fill:async change=>{
   state.calls.push('fill:'+change.name);state.fills++;
   state.rows.find(row=>row.label===change.label).score=change.value;state.saved=false;
   if(options.fillThrows===state.fills)throw Error('Synthetic interrupted fill');
   options.fill?.(state);
  },
  save:async()=>{state.calls.push('save');state.saved=true;if(options.saveThrows)throw Error('Synthetic uncertain Save');},
  reload:async()=>{
   state.calls.push('reload');if(options.reloadThrows)throw Error('Synthetic unreadable reload');
   const value={context:context(),rows:structuredClone(state.rows),saved:state.saved};
   return options.reload?options.reload(value):value;
  },
  binding:async()=>({group:'3',task:true,structure_id:'9'}),authorized:async()=>{},
 };
 return {state,ports};
}
const originalResult=run=>Object.fromEntries(['outcome','code','certain','counts','failed','unexpected'].map(key=>[key,run[key]]));
const afterUnknown=rows=>rows.every(row=>row.after===null&&row.after_flags===null&&row.observed_at===null);

test('all fixed planner reasons are branch metadata and all actual IC rows retain their source row index or explicit unknown',()=>{
 const rows=[hub('HubOnly','Hannah'),hub('Avery','Ann','score',7.4),hub('Brook','Ben','score',6),hub('Cole','Cy','score',5),hub('Dale','Di','score',6),
  hub('Eve','Ed','none',null,null,'Synthetic arbitrary wording'),hub('Fox','Fay','excused',null),hub('Gray','Gus','excused',null),
  hub('Hall','Hal','check',null,null,'Synthetic arbitrary check'),hub('Ivy','Ida','score',null,8),hub('Jones','Jay','score',5),hub('Kline','Kay','score',5),
  hub('Twin','Sam','score',1),hub('Twin','Samuel (Sam)','score',2)];
 const panel=[ic('Avery, Ann'),ic('Brook, Ben',2),ic('Cole, Cy',5),ic('Dale, Di',15),ic('Eve, Ed'),ic('Fox, Fay',null,['Exempt']),
  ic('Gray, Gus'),ic('Hall, Hal'),ic('Ivy, Ida'),ic('Jones, Jay',null,[],'M'),ic('Kline, Kay',null,['Late']),ic('Twin, Sam'),ic('Unmatched, Una')];
 const before=structuredClone({rows,panel}),plan=E.buildPlan(source(rows),snapshot(panel),'notes');
 assert.deepEqual([...new Set(plan.items.map(row=>row.reason_code))].sort(),[...E.REASON_CODES].sort());
 assert.deepEqual(plan.items.map(row=>[row.name,row.action,row.value]),[
  ['Avery, Ann','fill',7.4],['Brook, Ben','raise',6],['Cole, Cy','keep',5],['Dale, Di','keep',6],['Eve, Ed','skip',null],
  ['Fox, Fay','keep',null],['Gray, Gus','leave',null],['Hall, Hal','leave',null],['Ivy, Ida','skip',null],['Jones, Jay','leave',5],
  ['Kline, Kay','leave',5],['Twin, Sam','leave',null],['Unmatched, Una','leave',null]]);
 assert.deepEqual(plan.counts,{fill:1,raise:1,keep:3,leave:6,skip:2});
 assert.equal(plan.items[0].hub_row_index,1,'the index refers to the Hub list, not the IC row');
 for(const row of plan.items){
  if(row.hub_row_index===null)assert.equal(row.hub,null);
  else assert.equal(rows[row.hub_row_index].last+', '+rows[row.hub_row_index].first,row.name);
 }
 assert.equal(plan.items.at(-2).hub_row_index,null);assert.equal(plan.items.at(-1).hub_row_index,null);
 assert.equal(plan.items.some(row=>row.name==='HubOnly, Hannah'),false);
 assert.deepEqual({rows,panel},before);
});

test('complete observations use actual reloaded values with unchanged counts and exact existing IC call order',async()=>{
 const plan=basicPlan(),{state,ports}=transport(plan),run=await E.execute(plan,ports),observed=E.observation(plan,run);
 assert.deepEqual(originalResult(run),{outcome:'entered',code:'read_back',certain:true,counts:{filled:1,raised:1,kept:1,left:0,not_saved:0},failed:[],unexpected:[]});
 assert.deepEqual(state.calls,['snapshot:saved','fill:Avery, Ann','fill:Brook, Ben','snapshot:typed','save','reload']);
 assert.deepEqual([observed.save_attempted,observed.save_confirmed,observed.readback_status],[true,true,'complete']);
 assert.deepEqual(observed.rows.map(row=>[row.before,row.proposed,row.after,row.fill_attempted,row.fill_returned,row.readback]),
  [[null,7.4,7.4,true,true,'reloaded_match'],[2,6,6,true,true,'reloaded_match'],[15,5,15,false,false,'reloaded_match']]);
 assert.ok(observed.rows.every(row=>row.observed_at&&row.after_flags.length===0));
 assert.ok(Date.parse(observed.started_at)<=Date.parse(observed.finished_at));
 assert.notEqual(observed,run.observation);assert.notEqual(observed.rows[0].before_flags,plan.items[0].flags);
 assert.ok(Object.isFrozen(observed)&&Object.isFrozen(observed.rows)&&Object.isFrozen(observed.rows[0]));
 assert.throws(()=>observed.rows[0].before_flags.push('Private edit'),TypeError);
 assert.equal(Object.hasOwn(observed.rows[0],'student_id'),false);
});

test('a no-change result observes the fresh saved panel without claiming Save or reload',async()=>{
 const plan=E.buildPlan(source([hub('Avery','Ann','score',7),hub('Brook','Ben','none',null)]),snapshot([ic('Avery, Ann',9),ic('Brook, Ben')]),'notes');
 const {state,ports}=transport(plan),run=await E.execute(plan,ports),observed=E.observation(plan,run);
 assert.deepEqual(state.calls,['snapshot:saved']);assert.equal(run.code,'nothing_to_enter');
 assert.deepEqual([observed.save_attempted,observed.save_confirmed,observed.readback_status],[false,false,'not_attempted']);
 assert.deepEqual(observed.rows.map(row=>[row.readback,row.after,row.fill_attempted]),[['saved_panel_checked',9,false],['saved_panel_checked',null,false]]);
 assert.ok(observed.rows.every(row=>row.observed_at&&Array.isArray(row.after_flags)));
 const odd=transport(plan,{snapshot:value=>({...value,saved:false})}),unchanged=await E.execute(plan,odd.ports);
 assert.equal(unchanged.code,'nothing_to_enter','observations do not change existing execution decisions');
 assert.ok(afterUnknown(E.observation(plan,unchanged).rows));
});

test('a changed or unreadable initial panel never receives an unchanged-after assertion',async()=>{
 for(const options of [{snapshot:(value,state)=>state.snapshots===1?{...value,rows:value.rows.map((row,i)=>i?row:{...row,score:4})}:value},{snapshotThrows:1}]){
  const plan=basicPlan(),{state,ports}=transport(plan,options),run=await E.execute(plan,ports),observed=E.observation(plan,run);
  assert.ok(['ic_changed','ic_unreadable'].includes(run.code));assert.deepEqual(state.calls,['snapshot:saved']);
  assert.ok(afterUnknown(observed.rows));assert.ok(observed.rows.every(row=>!row.fill_attempted&&!row.fill_returned&&row.readback==='not_observed'));
  assert.equal(observed.save_attempted,false);assert.equal(observed.readback_status,'not_attempted');
 }
});

test('typing interruption records attempted versus returned fills but no persisted after value or Save',async()=>{
 const plan=basicPlan(),{state,ports}=transport(plan,{fillThrows:2}),run=await E.execute(plan,ports),observed=E.observation(plan,run);
 assert.equal(run.code,'typing_stopped');assert.equal(run.outcome,'stopped');assert.equal(run.certain,true);
 assert.deepEqual(state.calls,['snapshot:saved','fill:Avery, Ann','fill:Brook, Ben']);
 assert.deepEqual(observed.rows.map(row=>[row.fill_attempted,row.fill_returned]),[[true,true],[true,false],[false,false]]);
 assert.equal(run.counts.not_saved,2);assert.equal(observed.save_attempted,false);assert.ok(afterUnknown(observed.rows));
});

test('typed snapshot mismatch preserves all returned fills and stops before native Save',async()=>{
 const plan=basicPlan(),{state,ports}=transport(plan,{fill:state=>{state.rows[2].score=1;}}),run=await E.execute(plan,ports),observed=E.observation(plan,run);
 assert.equal(run.code,'typed_values_differ');assert.equal(run.counts.not_saved,2);
 assert.deepEqual(state.calls,['snapshot:saved','fill:Avery, Ann','fill:Brook, Ben','snapshot:typed']);
 assert.equal(observed.save_attempted,false);assert.ok(afterUnknown(observed.rows));
 assert.ok(observed.rows.slice(0,2).every(row=>row.fill_attempted&&row.fill_returned));
});

test('uncertain native Save and a confirmed Save followed by failed reload remain distinct',async()=>{
 for(const options of [{saveThrows:true},{reloadThrows:true}]){
  const plan=basicPlan(),{state,ports}=transport(plan,options),run=await E.execute(plan,ports),observed=E.observation(plan,run);
  assert.equal(run.code,'save_unconfirmed');assert.equal(run.outcome,'uncertain');assert.equal(run.certain,false);
  assert.equal(state.calls.filter(value=>value==='save').length,1);assert.equal(state.calls.filter(value=>value==='reload').length,options.reloadThrows?1:0);
  assert.equal(observed.save_attempted,true);assert.equal(observed.save_confirmed,!!options.reloadThrows);
  assert.equal(observed.readback_status,options.reloadThrows?'incomplete':'not_attempted');assert.ok(afterUnknown(observed.rows));
  assert.ok(observed.rows.every(row=>row.readback===(options.reloadThrows?'unavailable':'not_observed')));
 }
});

test('incomplete native readback cannot promote partial rows or changed context flags or names to verified after evidence',async()=>{
 const changes=[value=>({...value,saved:false}),value=>({...value,context:{...value.context,assignment_id:'999'}}),
  value=>({...value,rows:value.rows.slice(1)}),value=>({...value,rows:value.rows.map((row,i)=>i?row:{...row,flags:['Late']})}),
  value=>({...value,rows:value.rows.map((row,i)=>i?row:{...row,name:'Changed, Identity'})})];
 for(const reload of changes){
  const plan=basicPlan(),{state,ports}=transport(plan,{reload}),run=await E.execute(plan,ports),observed=E.observation(plan,run);
  assert.equal(run.code,'readback_incomplete');assert.equal(run.outcome,'uncertain');assert.equal(observed.save_confirmed,true);assert.equal(observed.readback_status,'incomplete');
  assert.ok(afterUnknown(observed.rows));assert.ok(observed.rows.every(row=>row.readback==='unavailable'));
  assert.equal(state.calls.filter(value=>value==='save').length,1);assert.equal(state.calls.filter(value=>value==='reload').length,1);
 }
});

test('complete mismatches retain the actual row values and classify both changed and unexpected rows without regrading',async()=>{
 const plan=basicPlan(),{state,ports}=transport(plan,{reload:value=>({...value,rows:value.rows.map((row,i)=>i===1?{...row,score:2}:i===2?{...row,score:12}:row).reverse()})});
 const run=await E.execute(plan,ports),observed=E.observation(plan,run);
 assert.deepEqual(originalResult(run),{outcome:'partial',code:'readback_mismatch',certain:true,counts:{filled:1,raised:0,kept:1,left:0,not_saved:1},failed:['Brook, Ben'],unexpected:['Cole, Cy']});
 assert.equal(observed.readback_status,'complete');assert.deepEqual(observed.rows.map(row=>[row.ic_name,row.after,row.readback]),[
  ['Avery, Ann',7.4,'reloaded_match'],['Brook, Ben',2,'reloaded_mismatch'],['Cole, Cy',12,'reloaded_mismatch']]);
 assert.deepEqual(state.calls,['snapshot:saved','fill:Avery, Ann','fill:Brook, Ben','snapshot:typed','save','reload']);
});

test('a nonnumeric unexpected value is a mismatch even when its numeric core is still null',async()=>{
 const plan=E.buildPlan(source([hub('Avery','Ann'),hub('Brook','Ben','none',null)]),snapshot([ic('Avery, Ann'),ic('Brook, Ben')]),'notes');
 const run=await E.execute(plan,transport(plan,{reload:value=>({...value,rows:value.rows.map((row,i)=>i?{...row,other:'M'}:row)})}).ports),observed=E.observation(plan,run);
 assert.equal(run.code,'readback_mismatch');assert.deepEqual(run.unexpected,['Brook, Ben']);assert.equal(run.counts.not_saved,0);
 assert.deepEqual([observed.rows[1].after,observed.rows[1].readback],[null,'reloaded_mismatch']);
});

test('Missing-zero flags stay intact and observation reason codes never parse editable English explanations',async()=>{
 const plan=E.buildPlan(source([hub('Avery','Ann','zero',0,0)]),snapshot([ic('Avery, Ann',null,['Missing'])]),'notes');
 const run=await E.execute(plan,transport(plan).ports),changed=structuredClone(plan);changed.items[0].why='Different human-facing prose: do not parse this.';
 const observed=E.observation(changed,run);assert.equal(observed.rows[0].reason_code,'blank_fill');
 assert.deepEqual([observed.rows[0].before_flags,observed.rows[0].after_flags],[['Missing'],['Missing']]);
 assert.deepEqual([observed.rows[0].proposed,observed.rows[0].after],[0,0]);
});

test('strict pure observations reject malformed contradictory or unbound detail with fixed safe errors',async()=>{
 const plan=basicPlan(),run=await E.execute(plan,transport(plan).ports);
 const changes=[value=>{value.observation.extra='private';},value=>{value.observation.rows[0].student_id='invented';},
  value=>{value.observation.rows[0].hub_row_index=299;},value=>{value.observation.rows[0].reason_code='human prose';},
  value=>{value.observation.rows[0].before=3;},value=>{value.observation.rows[0].proposed=3;},value=>{value.observation.rows[0].after=3;},
  value=>{value.observation.rows[0].after_flags=['Late'];},value=>{value.observation.rows[0].observed_at=null;},
  value=>{value.observation.rows[0].fill_attempted=false;},value=>{value.observation.rows[0].fill_returned=false;},
  value=>{value.observation.save_attempted=false;},value=>{value.observation.save_confirmed=false;},
  value=>{value.observation.started_at='tomorrow';},value=>{value.observation.finished_at='2000-01-01T00:00:00Z';},
  value=>{value.observation.readback_status='incomplete';},value=>{value.outcome='uncertain';},value=>{value.certain=false;},
  value=>{value.counts.filled=0;},value=>{value.code=value.observation.code='save_unconfirmed';}];
 for(const change of changes){const value=structuredClone(run);change(value);assert.throws(()=>E.observation(plan,value),/^Error: invalid_observation$/);}
 const plain=structuredClone(run);delete plain.observation;assert.throws(()=>E.observation(plan,plain),/^Error: invalid_observation$/);
});

test('existing counts-only v1 results reject student details and do not inherit the optional observation',async()=>{
 const plan=basicPlan(),run=await E.execute(plan,transport(plan).ports),result=E.hubResult(plan,run,plan.context.title);
 assert.deepEqual(Object.keys(result).sort(),['at','certain','class_key','column','counts','form_id','ic_title','list_id','outcome','section','week']);
 assert.equal(JSON.stringify(result).includes('Avery'),false);assert.equal(Object.hasOwn(result,'observation'),false);
 assert.throws(()=>E.validateResult({...result,observations:run.observation}),/invalid_result/);
});

test('morning returns private observations beside the unchanged counts result without extra IC requests',async()=>{
 const list=source([hub('Avery','Ann'),hub('Brook','Ben','score',6)]),plan=E.buildPlan(list,snapshot([ic('Avery, Ann'),ic('Brook, Ben',2)]),'notes');
 const {state,ports}=transport(plan),run=await M.run(list,target(),ports);
 assert.equal(run.code,'read_back');assert.equal(run.result.counts.filled,1);assert.equal(run.result.counts.raised,1);
 assert.equal(run.observations.schema,'hub-morning-panel-observation/v1');assert.equal(run.observations.rows.length,2);
 assert.deepEqual(state.calls,['snapshot:saved','snapshot:saved','fill:Avery, Ann','fill:Brook, Ben','snapshot:typed','save','reload']);
 assert.equal(Object.hasOwn(run.result,'observations'),false);
});

test('observation construction or validation failures cannot change grade execution or suppress its existing morning reply',async()=>{
 const plan=basicPlan(),nativeDate=global.Date;
 try{
  global.Date=class extends nativeDate{toISOString(){throw Error('Synthetic observer timestamp failure');}};
  const {state,ports}=transport(plan),run=await E.execute(plan,ports);
  assert.equal(run.code,'read_back');assert.equal(run.counts.filled,1);assert.equal(Object.hasOwn(run,'observation'),false);
  assert.deepEqual(state.calls,['snapshot:saved','fill:Avery, Ann','fill:Brook, Ben','snapshot:typed','save','reload']);
 }finally{global.Date=nativeDate;}
 const modulePath=require.resolve('../morning.js'),cached=require.cache[modulePath],prior=global.HubIcEntry;
 try{
  global.HubIcEntry={...E,observation:()=>{throw Error('Synthetic unavailable private observation');}};delete require.cache[modulePath];
  const isolated=require('../morning.js'),list=source([hub('Avery','Ann'),hub('Brook','Ben','score',6)]),p=E.buildPlan(list,snapshot([ic('Avery, Ann'),ic('Brook, Ben',2)]),'notes');
  const {state,ports}=transport(p),run=await isolated.run(list,target(),ports);
  assert.equal(run.code,'read_back');assert.equal(run.result.outcome,'entered');assert.equal(run.observations,null);
  assert.deepEqual(state.calls,['snapshot:saved','snapshot:saved','fill:Avery, Ann','fill:Brook, Ben','snapshot:typed','save','reload']);
 }finally{global.HubIcEntry=prior;require.cache[modulePath]=cached;}
});
