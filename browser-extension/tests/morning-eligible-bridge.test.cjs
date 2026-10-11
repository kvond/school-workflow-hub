const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const E=require('../entry.js');global.HubIcEntry=E;const M=require('../morning.js');
const C={HUB_ORIGIN:'https://hub.fictional.example',IC_ORIGIN:'https://ic.fictional.example'};
function fixture(){
 const target={section:'A2 Fictional',column:'notes',section_id:'101',term_id:'501',year:'2027',school_id:'55',calendar_id:'854',structure_id:'854',assignment_id:'301',score_group:'401',task_id:'13',label_code:'W03F',url:C.IC_ORIGIN+'/campus/sis/control-center/classroom/curriculum/resource/301/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/101/objectSectionID/301/calendarID/854/structureID/854)?classroomSectionID=101&classroomTermID=501&year=2027&schoolID=55&calendarID=854&structureID=854'};
 const list={schema:E.SCHEMA,list_id:'22222222-2222-4222-8222-222222222222',class_key:'forensics',class_name:'Forensics',week:'Wk 03',form_id:'FICTIONAL_OBSERVATION_FORM',assignment:'Observation',form_label:'Observation',status:'closed',status_text:'Morning scores',sent_at:new Date(Date.now()-60000).toISOString(),rows:[{last:'Avery',first:'Alex',section:'A2 Fictional',kind:'score',notes:10,revision:null,reason:''},...Array.from({length:10},(_,i)=>({last:'Held'+i,first:'Alex',section:'A2 Fictional',kind:'check',notes:null,revision:null,reason:'Exact original history needs review'}))]};
 const result={list_id:list.list_id,form_id:list.form_id,class_key:list.class_key,week:list.week,column:'notes',section:target.section,ic_title:'Observation (F)',outcome:'entered',certain:true,counts:{filled:0,raised:1,kept:0,left:10,not_saved:0},at:new Date().toISOString()};
 return {run_id:'11111111-1111-4111-8111-111111111111',scope:{class:list.class_key,week:list.week,form:list.form_id},revision:'a'.repeat(64),list,targets:[target],results:[result]};
}
function bridge(change=()=>{}){
 let listener;const calls=[];const window={};window.top=window;
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../morning-hub.js'),'utf8'),{HubIcEntry:E,HubMorning:M,HubPostingConfig:C,window,location:{origin:C.HUB_ORIGIN},URLSearchParams,AbortController,setTimeout,clearTimeout,setInterval,clearInterval,chrome:{runtime:{id:'extension',sendMessage:async()=>({ok:true}),onMessage:{addListener:f=>listener=f}}},fetch:async(url,options)=>{
  const p=JSON.parse(options.body);calls.push({url,p});
  const value={recorded:true,receipt:{schema:'hub-morning-entry-ack/v1',run_id:p.run_id,scope:p.scope,list_id:p.list.list_id,source_revision:p.revision,target_count:p.targets.length,certain:true,counts:p.results[0].counts,source_check_rows:10,whole_assignment_recorded:false,receipt_sha256:'b'.repeat(64)}};
  change(value);
  return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>value};
 }});
 return {calls,invoke:(payload,sender={id:'extension'})=>new Promise(resolve=>{if(listener({type:'morning_hub',action:'record',payload},sender,resolve)!==true)resolve(undefined);})};
}
test('ten check rows stay in exact private payload and partial comparison acknowledgement is accepted',async()=>{
 const b=bridge(),p=fixture(),reply=await b.invoke(p);assert.equal(reply.ok,true);assert.equal(reply.data.receipt.whole_assignment_recorded,false);
 assert.equal(b.calls[0].url,'/v2/api/morning/entry');assert.deepEqual(b.calls[0].p,p);
 assert.equal(reply.data.receipt.source_check_rows,10);assert.ok(!JSON.stringify(reply.data).includes('Avery'));
});
test('wrong run/list/source/target/certainty/count acknowledgement stays unconfirmed',async()=>{
 const changes=[a=>a.recorded=false,a=>a.receipt.run_id='33333333-3333-4333-8333-333333333333',a=>a.receipt.list_id=a.receipt.run_id,a=>a.receipt.scope.form='OTHER_FICTIONAL_FORM',a=>a.receipt.source_revision='c'.repeat(64),a=>a.receipt.target_count=2,a=>a.receipt.certain=false,a=>a.receipt.counts.raised=0,a=>a.receipt.source_check_rows=0,a=>a.receipt.whole_assignment_recorded='yes',a=>a.receipt.whole_assignment_recorded=true,a=>a.receipt.receipt_sha256='unknown'];
 for(const change of changes){const b=bridge(change);assert.equal((await b.invoke(fixture())).ok,false);}
});
test('incomplete or uncertain destination results never reach the endpoint',async()=>{
 for(const change of [p=>p.results=[],p=>p.results[0].certain=false,p=>p.results[0].outcome='partial',p=>p.results[0].counts.not_saved=1,p=>p.revision='unknown',p=>p.run_id='unknown']){
  const b=bridge(),p=fixture();change(p);assert.equal((await b.invoke(p)).ok,false);assert.equal(b.calls.length,0);
 }
});
test('a page or foreign extension cannot create a comparison receipt',async()=>{
 const b=bridge();assert.equal(await b.invoke(fixture(),{id:'extension',tab:{id:1}}),undefined);assert.equal(await b.invoke(fixture(),{id:'foreign'}),undefined);assert.equal(b.calls.length,0);
});
