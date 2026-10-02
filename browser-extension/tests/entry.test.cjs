const {test}=require('node:test');const assert=require('node:assert/strict');
const E=require('../entry.js');
// Fictional students and scores only.
const now=Date.parse('2026-09-28T12:00:00Z');
const row=(last,first,kind,notes=null,revision=null,section='A4 Fictional',reason='')=>({last,first,section,kind,notes,revision,reason});
function list(rows,extra={}){return {schema:'hub-ic-list/v1',list_id:'5f0c2c1e-8b1a-4c33-9d2e-2a9d7a3b1c01',class_key:'biology',class_name:'Biology',week:'Wk 03',form_id:'1FictionalFormId_abcdef',assignment:'Energy Flow in Fictional Ecosystems',form_label:'📤 Energy Flow — Turn in',status:'open',status_text:'IC: open until Oct 9.',sent_at:new Date(now-60000).toISOString(),rows,...extra};}
const ic=(name,score=null,flags=[],other=null)=>({label:`EX1 MP: Marking Period Score input for ${name}`,name,score,other,flags});
const snap=(rows,saved=true)=>({context:{assignment_id:'301',section_id:'101',term_id:'501',title:'Cycle 03 · Energy Flow (F)'},rows,saved});
const base=()=>[row('Avery','Ann','score',7.4,5),row('Brook','Ben','score',6,10),row('Cole','Cy','zero',0,0),row('Dale','Di','check',null,null,'A4 Fictional','the Hub isn’t sure of this score'),row('Eve','Ed','excused',null,null,'A4 Fictional','new student'),row('Fox','Fay','score',9,null),row('Gray','Gus','score',8.8,3)];
const plan=(rows,icRows,column='notes')=>E.buildPlan(list(rows),snap(icRows),column,{now});

test('a list is validated strictly and expires',()=>{
 assert.equal(E.validateList(list(base()),now).rows.length,7);
 for(const bad of [l=>l.schema='other',l=>l.class_key='chemistry',l=>l.rows[0].notes=11,l=>l.rows[0].notes=7.45,l=>l.rows[2].notes=3,l=>l.rows[3].notes=5,l=>l.rows.push({...l.rows[0]}),l=>l.rows[0].extra=1,l=>l.form_id='bad id',l=>l.rows=[]])
 {const value=structuredClone(list(base()));bad(value);assert.throws(()=>E.validateList(value,now),/invalid_list/);}
 assert.throws(()=>E.validateList(list(base(),{sent_at:new Date(now-E.LIST_TTL_MS-1).toISOString()}),now),/list_expired/);
});

test('names match by last name and any first name, nickname or accent-free spelling, one to one',()=>{
 const rows=[row('Fuentes-Aguilar','José (Pepe)','score',5),row('Moore',"Shemaih'Yah",'score',5),row('Lee','Sam','score',1),row('Lee','Samuel (Sam)','score',2)];
 const found=E.matchNames(rows,['Fuentes Aguilar, Pepe','Moore, Shemaih Yah L','Lee, Sam','Nobody, Here']);
 assert.deepEqual(found,[{row:0},{row:1},{ambiguous:true},{none:true}]);
});

test('keep the higher score: fill blanks, raise lower scores, keep IC when it is the same or higher',()=>{
 const p=plan(base(),[ic('Avery, Ann'),ic('Brook, Ben',4),ic('Cole, Cy'),ic('Dale, Di'),ic('Eve, Ed'),ic('Fox, Fay',10),ic('Gray, Gus',8.8),ic('Hill, Hal',3)]);
 const by=Object.fromEntries(p.items.map(i=>[i.name,[i.action,i.value]]));
 assert.deepEqual(by['Avery, Ann'],['fill',7.4]);assert.deepEqual(by['Brook, Ben'],['raise',6]);assert.deepEqual(by['Cole, Cy'],['fill',0]);
 assert.equal(by['Dale, Di'][0],'leave');assert.equal(by['Eve, Ed'][0],'leave');assert.deepEqual(by['Fox, Fay'],['keep',9]);assert.deepEqual(by['Gray, Gus'],['keep',8.8]);assert.equal(by['Hill, Hal'][0],'leave');
 assert.deepEqual(p.counts,{fill:2,raise:1,keep:2,leave:3,skip:0});
 assert.equal(p.section,'A4 Fictional');
 assert.deepEqual(p.changes.map(c=>[c.name,c.before,c.value]),[['Avery, Ann',null,7.4],['Brook, Ben',4,6],['Cole, Cy',null,0]]);
});

test('the Revision column uses the Hub revision score, and N/A is skipped',()=>{
 const p=plan(base(),[ic('Avery, Ann',5),ic('Brook, Ben'),ic('Cole, Cy'),ic('Fox, Fay'),ic('Gray, Gus',2)],'revision');
 const by=Object.fromEntries(p.items.map(i=>[i.name,i.action]));
 assert.deepEqual(by,{'Avery, Ann':'keep','Brook, Ben':'fill','Cole, Cy':'fill','Fox, Fay':'skip','Gray, Gus':'raise'});
});

test('IC flags, odd IC values and Exempt are left alone',()=>{
 const p=plan(base(),[ic('Avery, Ann',null,['Missing']),ic('Brook, Ben',null,[],'M'),ic('Cole, Cy',0,['Missing']),ic('Eve, Ed',null,['Exempt']),ic('Fox, Fay')]);
 const by=Object.fromEntries(p.items.map(i=>[i.name,[i.action,i.why]]));
 assert.equal(by['Avery, Ann'][0],'leave');assert.match(by['Avery, Ann'][1],/Missing/);
 assert.equal(by['Brook, Ben'][0],'leave');assert.deepEqual(by['Cole, Cy'],['keep','same']);assert.deepEqual(by['Eve, Ed'],['keep','Exempt in IC']);
});

test('students not yet due are skipped, and Hub students missing from IC are listed',()=>{
 const rows=[...base(),row('Ivy','Ida','none',null,null,'A4 Fictional','its two weeks haven’t started'),row('Jay','Jo','score',4,4,'B3 Other')];
 const p=plan(rows,[ic('Avery, Ann'),ic('Brook, Ben'),ic('Cole, Cy'),ic('Ivy, Ida')]);
 assert.equal(p.items.find(i=>i.name==='Ivy, Ida').action,'skip');
 assert.deepEqual(p.missing,['Dale, Di','Eve, Ed','Fox, Fay','Gray, Gus']);
});

test('the wrong class, a mixed section or an unsaved panel stops before any plan',()=>{
 assert.throws(()=>plan(base(),[ic('Other, One'),ic('Other, Two'),ic('Avery, Ann')]),/students_dont_match/);
 const mixed=[row('Avery','Ann','score',5),row('Brook','Ben','score',5,5,'B3 Other')];
 assert.throws(()=>plan(mixed,[ic('Avery, Ann'),ic('Brook, Ben')]),/several_sections/);
 assert.throws(()=>E.buildPlan(list(base()),snap([ic('Avery, Ann')],false),'notes',{now}),/ic_not_saved/);
 assert.throws(()=>plan(base(),[ic('Avery, Ann')],'content'),/column_required/);
});

test('the column comes from the IC title, and the list from shared words',()=>{
 assert.equal(E.columnFromTitle('Cycle 03 · Energy Flow (F)'),'notes');assert.equal(E.columnFromTitle('Energy Flow (S)'),'revision');assert.equal(E.columnFromTitle('Energy Flow'),null);
 const a=list(base()),b=list(base(),{list_id:'5f0c2c1e-8b1a-4c33-9d2e-2a9d7a3b1c02',assignment:'Ecosystems and Feeding Relationships',form_label:'📤 Ecosystems — Turn in'});
 assert.equal(E.pickList([a,b],'Cycle 02 · Ecosystems & Feeding Relationships (F)').list.list_id,b.list_id);
 assert.equal(E.pickList([a,b],'Cycle 03 · Energy Flow (S)').list.list_id,a.list_id);
 assert.equal(E.pickList([a],'Something Else (F)').overlap,0);
});

function ports(p,{afterReload,typedHook,saveFails,before}={}){
 const state={rows:structuredClone(p.snapshot.rows),saved:true,calls:[]};
 return {state,ports:{
  snapshot:async requireSaved=>{state.calls.push('snapshot');if(requireSaved&&!state.saved)throw Error('ic_not_saved');const rows=before&&state.calls.length===1?before(structuredClone(state.rows)):structuredClone(state.rows);return {context:structuredClone(p.context),rows,saved:state.saved};},
  fill:async change=>{state.calls.push('fill');const r=state.rows.find(x=>x.label===change.label);r.score=change.value;state.saved=false;if(typedHook)typedHook(state);},
  save:async()=>{state.calls.push('save');if(saveFails)throw Error('save');state.saved=true;},
  reload:async()=>{state.calls.push('reload');const rows=afterReload?afterReload(structuredClone(state.rows)):structuredClone(state.rows);return {context:structuredClone(p.context),rows,saved:true};},
 }};
}
const icRows=()=>[ic('Avery, Ann'),ic('Brook, Ben',4),ic('Cole, Cy'),ic('Fox, Fay',10)];

test('entering types each change, saves once, reloads and reads every value back',async()=>{
 const p=plan(base(),icRows()),{state,ports:io}=ports(p);
 const run=await E.execute(p,io);
 assert.equal(run.outcome,'entered');assert.equal(run.certain,true);
 assert.deepEqual(run.counts,{filled:2,raised:1,kept:1,left:0,not_saved:0});
 assert.deepEqual(state.calls,['snapshot','fill','fill','fill','snapshot','save','reload']);
});

test('nothing to enter is a checked result with no typing',async()=>{
 const p=plan(base(),[ic('Avery, Ann',8),ic('Brook, Ben',6),ic('Cole, Cy',0)]),{state,ports:io}=ports(p);
 const run=await E.execute(p,io);
 assert.equal(run.outcome,'checked');assert.deepEqual(state.calls,['snapshot']);
});

test('IC changed since the review: stop before typing',async()=>{
 const p=plan(base(),icRows()),{state,ports:io}=ports(p,{before:rows=>{rows[0].score=5;return rows;}});
 const run=await E.execute(p,io);
 assert.equal(run.outcome,'stopped');assert.equal(run.code,'ic_changed');assert.deepEqual(state.calls,['snapshot']);
});

test('an unexpected typed value stops before Save',async()=>{
 const p=plan(base(),icRows()),{state,ports:io}=ports(p,{typedHook:s=>{s.rows[3].score=2;}});
 const run=await E.execute(p,io);
 assert.equal(run.outcome,'stopped');assert.equal(run.code,'typed_values_differ');assert.ok(!state.calls.includes('save'));
});

test('a score that did not save is reported by name; a Save failure is uncertain',async()=>{
 const p=plan(base(),icRows());
 const partial=await E.execute(p,ports(p,{afterReload:rows=>{rows[1].score=4;return rows;}}).ports);
 assert.equal(partial.outcome,'partial');assert.deepEqual(partial.failed,['Brook, Ben']);assert.equal(partial.counts.not_saved,1);assert.equal(partial.certain,true);
 const odd=await E.execute(p,ports(p,{afterReload:rows=>{rows[3].score=1;return rows;}}).ports);
 assert.equal(odd.outcome,'partial');assert.deepEqual(odd.unexpected,['Fox, Fay']);
 const lost=await E.execute(p,ports(p,{saveFails:true}).ports);
 assert.equal(lost.outcome,'uncertain');assert.equal(lost.certain,false);
});

test('the Hub gets counts only, never names',()=>{
 const p=plan(base(),icRows()),result=E.hubResult(p,{outcome:'entered',certain:true,counts:{filled:2,raised:1,kept:1,left:0,not_saved:0}},'Cycle 03 · Energy Flow (F)','2026-09-28T12:05:00Z');
 assert.deepEqual(Object.keys(result).sort(),['at','certain','class_key','column','counts','form_id','ic_title','list_id','outcome','section','week']);
 assert.ok(!JSON.stringify(result).includes('Avery'));
 assert.throws(()=>E.validateResult({...result,names:['Avery']}),/invalid_result/);
});

test('readback follows stable row labels when IC reorders students',async()=>{
 const p=plan(base(),icRows());
 const result=await E.execute(p,ports(p,{afterReload:rows=>rows.reverse()}).ports);
 assert.equal(result.outcome,'entered');assert.equal(result.certain,true);
 assert.deepEqual(result.counts,{filled:2,raised:1,kept:1,left:0,not_saved:0});
});
test('incomplete readback identifies missing rows; failed Save identifies attempted rows',async()=>{
 const p=plan(base(),icRows());
 const missing=await E.execute(p,ports(p,{afterReload:rows=>rows.filter(r=>r.name!=='Brook, Ben')}).ports);
 assert.equal(missing.code,'readback_incomplete');assert.deepEqual(missing.failed,['Brook, Ben']);
 const save=await E.execute(p,ports(p,{saveFails:true}).ports);
 assert.equal(save.code,'save_unconfirmed');assert.deepEqual(save.failed,p.changes.map(c=>c.name));
});
