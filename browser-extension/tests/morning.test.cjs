const {test}=require('node:test');const assert=require('node:assert/strict');
const E=require('../entry.js');global.HubIcEntry=E;const M=require('../morning.js');
const origin='https://school.example.test';
const target=()=>({section:'A4 Fictional',column:'notes',section_id:'42',term_id:'5',year:'2027',school_id:'7',calendar_id:'8',structure_id:'9',assignment_id:'123',score_group:'3',task_id:'1',label_code:'F',url:origin+'/campus/sis/control-center/classroom/curriculum/resource/123/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/42/objectSectionID/123/calendarID/8/structureID/9)?classroomSectionID=42&classroomTermID=5&year=2027&schoolID=7&calendarID=8&structureID=9'});
const list=()=>({schema:E.SCHEMA,list_id:'11111111-1111-4111-8111-111111111111',class_key:'biology',class_name:'Biology',week:'Wk 05',form_id:'FICTIONAL_FORM_1234',assignment:'Matter',form_label:'Matter',status:'open',status_text:'Open',sent_at:new Date().toISOString(),rows:[
 ['Avery','Ann','score',7],['Brook','Ben','score',8],['Cole','Cy','score',5],['Dale','Di','score',9],['Eve','Ed','excused',null],['Fox','Fay','check',null]
].map(([last,first,kind,notes])=>({last,first,kind,notes,revision:null,section:'A4 Fictional',reason:''}))});
const context=()=>({section_id:'42',term_id:'5',year:'2027',school_id:'7',calendar_id:'8',assignment_id:'123',title:'Matter (F)',points:10});
function panel({change=false,saveFails=false,unsavedReadback=false,flagAfter=false}={}){
 const state={calls:[],reads:0,saved:true,rows:list().rows.map((r,i)=>({label:'F MP: Marking Period Score input for '+r.last+', '+r.first,name:r.last+', '+r.first,score:[null,2,10,null,null,null][i],other:null,flags:i===3?['Missing']:[]}))};
 return {state,ports:{snapshot:async()=>{state.calls.push('snapshot');state.reads++;if(change&&state.reads===2)state.rows[0].score=4;return {context:context(),rows:structuredClone(state.rows),saved:state.saved};},
 binding:async()=>({group:'3',task:true,structure_id:'9'}),authorized:async()=>{},
 fill:async c=>{state.calls.push('fill');state.rows.find(r=>r.label===c.label).score=c.value;state.saved=false;},
 save:async()=>{state.calls.push('save');if(saveFails)throw Error();state.saved=true;},
 reload:async()=>{state.calls.push('reload');if(flagAfter)state.rows[0].flags=['Late'];return {context:context(),rows:structuredClone(state.rows),saved:!unsavedReadback};}}};
}
test('Eastern weekday clock: 7am, wake before noon, noon skip, weekend and DST',()=>{
 const s={enabled:true,day:''};
 assert.equal(M.due(s,Date.parse('2026-10-01T10:59:00Z')),null);
 assert.deepEqual(M.due(s,Date.parse('2026-10-01T11:00:00Z')),{day:'2026-10-01',skip:false});
 assert.deepEqual(M.due(s,Date.parse('2026-10-01T15:59:00Z')),{day:'2026-10-01',skip:false});
 assert.deepEqual(M.due(s,Date.parse('2026-10-01T16:00:00Z')),{day:'2026-10-01',skip:true});
 assert.equal(M.due(s,Date.parse('2026-10-03T12:00:00Z')),null);
 assert.equal(M.due({...s,day:'2026-10-01'},Date.parse('2026-10-01T12:00:00Z')),null);
 assert.equal(M.due({...s,enabled:false},Date.parse('2026-10-01T12:00:00Z')),null);
 assert.equal(M.due(s,Date.parse('2026-11-02T11:59:00Z')),null);
 assert.equal(M.due(s,Date.parse('2026-11-02T12:00:00Z')).skip,false);
});
test('exact destination mapping, no title fallback or foreign routes',()=>{
 assert.equal(M.targets(list(),[target()],origin).length,1);
 for(const mutate of [t=>t.url='https://evil.test/',t=>t.url+='&classroomSectionID=42',t=>t.assignment_id='124',t=>t.url+='#changed',t=>t.url=t.url.replace('sectionID/42','sectionID/43'),t=>t.url=t.url.replace('classroomTermID=5','classroomTermID=6')]){
  const t=target();mutate(t);assert.throws(()=>M.targets(list(),[t],origin),/mapping_required/);
 }
 assert.throws(()=>M.targets(list(),[],origin),/mapping_required/);
 assert.throws(()=>M.targets(list(),[target(),target()],origin),/mapping_required/);
});
test('unattended mock panel: blank, raise, higher, flag, excused and check by hand',async()=>{
 const {state,ports}=panel(),r=await M.run(list(),target(),ports);
 assert.equal(r.result.outcome,'entered');assert.equal(r.result.counts.filled,1);assert.equal(r.result.counts.raised,1);assert.equal(r.result.counts.kept,1);assert.equal(r.result.counts.left,3);
 assert.deepEqual(r.reasons,{flag:1,excused:1,check:1,name:0,missing:0});
 assert.deepEqual(state.calls,['snapshot','snapshot','fill','fill','snapshot','save','reload']);
 assert.equal(state.rows[2].score,10);assert.equal(state.rows[3].score,null);
});
test('literal parentheses in IC abbreviations pass only an exact label match',async()=>{
 for(const code of ['Unit(F','Unit(S','Unit(F)']){
  const binding=M.target({...target(),label_code:code},origin),{state,ports}=panel();
  state.rows.forEach(r=>r.label=code+r.label.slice(1));
  const result=await M.run(list(),binding,ports);
  assert.equal(result.result.outcome,'entered');
 }
 const binding=M.target({...target(),label_code:'Unit(F'},origin),{state,ports}=panel();
 state.rows.forEach(r=>r.label='UnitF'+r.label.slice(1));
 await assert.rejects(M.run(list(),binding,ports),/destination_mapping_changed/);
 assert.ok(!state.calls.includes('fill'));
 assert.ok(!state.calls.includes('save'));
});
test('abbreviation validation rejects coercion, whitespace, controls and regex syntax',()=>{
 for(const label_code of [null,undefined,123,'','F'.repeat(81),' F','F ','F\n','F\u0000','F.*','F[','F|S','F/']){
  assert.throws(()=>M.target({...target(),label_code},origin),/mapping_required/);
 }
});
test('a change after review stops without any typing or save',async()=>{
 const {state,ports}=panel({change:true}),r=await M.run(list(),target(),ports);
 assert.equal(r.code,'ic_changed');assert.deepEqual(state.calls,['snapshot','snapshot']);
});
test('unconfirmed saves, unsaved reloads and changed flags never report success',async()=>{
 for(const options of [{saveFails:true},{unsavedReadback:true},{flagAfter:true}]){
  const {state,ports}=panel(options),r=await M.run(list(),target(),ports);
  assert.equal(r.result.outcome,'uncertain');assert.equal(state.calls.filter(c=>c==='save').length,1);
 }
});
test('different live section, score group, task, structure or label refuses before typing',async()=>{
 for(const edit of [p=>p.binding=async()=>({group:'4',task:true,structure_id:'9'}),p=>p.binding=async()=>({group:'3',task:false,structure_id:'9'}),p=>p.binding=async()=>({group:'3',task:true,structure_id:'10'}),p=>p.snapshot=async()=>({context:{...context(),section_id:'43'},saved:true,rows:panel().state.rows})]){
  const {state,ports}=panel();edit(ports);await assert.rejects(M.run(list(),target(),ports),/destination_mapping_changed/);assert.ok(!state.calls.includes('fill'));
 }
});
module.exports={target,list,panel,origin};
test('missed weekdays are reported on the next wake, but not weekends or days before opt-in',()=>{
 const now=Date.parse('2026-10-05T11:00:00Z');
 assert.deepEqual(M.missed({enabled:true,day:'2026-10-01'},now),['2026-10-02']);
 assert.deepEqual(M.missed({enabled:true,day:'',enabled_day:'2026-10-05'},now),[]);
 assert.deepEqual(M.missed({enabled:false,day:'2026-10-01'},now),[]);
});
