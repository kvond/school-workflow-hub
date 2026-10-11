const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const E=require('../entry.js'),P=require('../protocol.js');
const readScript=name=>fs.readFileSync(path.join(__dirname,'..',name),'utf8');

// Exercise the content script's native score setter, Save and frame reload,
// together with the real morning planner/executor. All data is fictional.
function contentPanel({kind='zero',value=0,flags=['Missing'],forcePlan=false,changedFlags=null,manual=false,
 replaceFrame=false,reloadSavedPolls=0,reloadRows=null,reloadContext=null,reloadFlags=null,reloadPanelPolls=0,
 dirtyMarkerPolls=0,saveCompletionPolls=0,loseDirtyBeforeSave=false,saveContext=null,dirtyTitle='(Alt + Enter)',changeDirtyBeforeSave=null,recognizedEvents=false,keyupOnlyValidation=false,disableKeyupHandler=false}={}){
 const origin='https://school.example.test',label='F MP: Marking Period Score input for Avery, Ann';
 const target={section:'A4 Fictional',column:'notes',section_id:'42',term_id:'5',year:'2027',school_id:'7',calendar_id:'8',structure_id:'9',assignment_id:'123',score_group:'3',task_id:'1',label_code:'F',url:origin+'/campus/sis/control-center/classroom/curriculum/resource/123/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/42/objectSectionID/123/calendarID/8/structureID/9)?classroomSectionID=42&classroomTermID=5&year=2027&schoolID=7&calendarID=8&structureID=9'};
 const list={schema:E.SCHEMA,list_id:crypto.randomUUID(),class_key:'biology',class_name:'Biology',week:'Wk 05',form_id:'FICTIONAL_FORM_1234',assignment:'Matter',form_label:'Matter',status:'open',status_text:'Open',sent_at:new Date().toISOString(),rows:[{last:'Avery',first:'Ann',section:target.section,kind,notes:value,revision:kind==='zero'?0:null,reason:''}]};
 const state={calls:[],flags:[...flags],saved:true,authorizations:0,results:[],afterReload:false,reloadPolls:0,
  dirtyPending:false,dirtyPolls:0,savePending:false,savePolls:0,committed:'',dirtyTitle,modelScore:'',modelPending:'',draft:'',keyupPending:false,keyupPolls:0};
 const row={};
 class NativeInput {
  constructor(){this.raw='';}
  get value(){return this.raw;}
  set value(next){state.calls.push('set:'+next);this.raw=next;}
  getAttribute(name){return name==='aria-label'?label:null;}
  closest(){return row;}
  focus(){state.calls.push('focus');}
  dispatchEvent(event){
   state.calls.push(event.type);
   if(recognizedEvents){
    if(event.type==='input')state.modelPending=this.raw;
    if(event.type==='keyup'&&!disableKeyupHandler)state.keyupPending=true;
   }else{state.dirtyPending=Boolean(dirtyMarkerPolls);state.saved=state.dirtyPending;}
  }
  blur(){state.calls.push('blur');if(recognizedEvents&&!keyupOnlyValidation&&state.modelScore!==state.draft){state.draft=state.modelScore;state.saved=false;}}

 }
 const input=new NativeInput(),view={HTMLInputElement:NativeInput,Event:class {constructor(type){this.type=type;}},KeyboardEvent:class {constructor(type,options){this.type=type;Object.assign(this,options);}}};
 let doc={defaultView:view,readyState:'complete'};
 let frame;
 const reload=()=>{
  state.calls.push('reload');input.raw=state.committed;const previous=doc;doc={defaultView:view,readyState:'complete'};state.afterReload=true;
  if(replaceFrame){Object.defineProperty(frame,'contentDocument',{get:()=>previous});frame={contentDocument:doc,contentWindow:{location:{reload}}};}
  if(reloadSavedPolls)state.saved=false;
  if(reloadFlags)state.flags=[...reloadFlags];
 };
 frame={get contentDocument(){return doc;},contentWindow:{location:{reload}}};
 const context={section_id:'42',term_id:'5',year:'2027',school_id:'7',calendar_id:'8',assignment_id:'123',title:'Matter (F)',points:10};
 const save={getAttribute:()=>state.saved?'All scores have been saved.':state.dirtyTitle,click(){state.calls.push('save');state.savePending=Boolean(saveCompletionPolls);state.saved=!state.savePending;if(state.saved)state.committed=recognizedEvents?state.draft:input.raw;}};
 const surface=()=>{
  if(state.afterReload&&state.reloadPolls<reloadPanelPolls)throw Error('school_sign_in_required');
  if(state.afterReload&&reloadRows==='missing')throw Error('unsupported_gradebook');
  return {inputs:state.afterReload&&reloadRows==='duplicate'?[input,input]:[input],context:{...context,...(state.savePending?saveContext:null),...(state.afterReload?reloadContext:null)},save,frame,doc,group:'3',path:target.url,score:{querySelectorAll:()=>[{getAttribute:()=> 'progressScore_5_1_42'}]}};
 };
 const engine=forcePlan?{...E,buildPlan(...args){
  // Defense in depth: a malformed planner decision must still be rejected by
  // the native port, without touching a flagged nonzero or other flag.
  const plan=JSON.parse(JSON.stringify(E.buildPlan(...args))),item=plan.items[0];
  Object.assign(item,{action:'fill',value});
  plan.changes=[{label:item.label,name:item.name,before:item.current,value,action:'fill'}];
  plan.counts.fill=1;plan.counts.leave=0;return plan;
 }}:E;
 let listener;
 class DomNode {
  constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.listeners={};this.style={};this.text='';}
  setAttribute(name,value){this.attrs[name]=value;this[name]=value;}
  append(...children){for(const child of children){if(child instanceof DomNode)child.parent=this;this.children.push(child);}}
  replaceChildren(...children){this.children=[];this.text='';this.append(...children);}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);}
  addEventListener(type,fn){this.listeners[type]=fn;}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text+this.children.map(child=>child instanceof DomNode?child.textContent:String(child)).join('');}
  click(){return this.listeners.click?.({isTrusted:true,target:this});}
 }
 const body=new DomNode('body'),walk=node=>[node,...node.children.filter(child=>child instanceof DomNode).flatMap(walk)];
 const window={};window.top=window;
 const sandbox={HubIcEntry:engine,HubPostingProtocol:P,HubPostingConfig:{HUB_ORIGIN:'https://hub.example.test',IC_ORIGIN:origin},
  HubIcAdapter:{panel:{surface,waitSurface:async()=>surface(),renderedFlags:()=>[...state.flags],norm:value=>String(value||'').replace(/\s+/g,' ').trim(),until:async check=>{
   for(let i=0;i<5;i++){
    const result=check();if(result)return result;
    if(state.keyupPending){state.keyupPolls++;if(state.keyupPolls>=2){state.draft=state.modelScore;state.saved=false;state.keyupPending=false;}}
    if(state.dirtyPending){state.dirtyPolls++;if(state.dirtyPolls>=dirtyMarkerPolls){state.saved=false;state.dirtyPending=false;}}
    if(state.savePending){state.savePolls++;if(state.savePolls>=saveCompletionPolls){state.saved=true;state.savePending=false;state.committed=recognizedEvents?state.draft:input.raw;}}
    if(state.afterReload){state.reloadPolls++;if(state.reloadPolls>=reloadSavedPolls)state.saved=true;}
   }
   throw Error('not_ready');
  }}},
  chrome:{runtime:{id:'fictional-extension',onMessage:{addListener:fn=>{listener=fn;}},sendMessage:async message=>{
   if(message.type==='entry_ic'){
    if(message.op==='result'){state.results.push(message.result);return {ok:true};}
    return {lists:manual?[{list}]:[]};
   }
   if(message.type==='morning_authorize'){
    state.authorizations++;
    if(changedFlags&&state.authorizations===3)state.flags=[...changedFlags];
    if(loseDirtyBeforeSave&&state.authorizations===4)state.saved=true;
    if(changeDirtyBeforeSave!==null&&state.authorizations===4)state.dirtyTitle=changeDirtyBeforeSave;
    return {ok:true};
   }
   throw Error('unexpected_message');
  }}},window,document:{body,hidden:false,querySelectorAll:()=>[],createElement:tag=>new DomNode(tag)},Node:DomNode,location:{origin,href:target.url},URL,Date,console,setTimeout:fn=>{state.calls.push('model-turn');state.modelScore=state.modelPending;fn();},setInterval:()=>1,clearInterval(){}};
 vm.runInNewContext(readScript('morning.js'),sandbox);
 vm.runInNewContext(readScript('ic-entry.js'),sandbox);
 const run=()=>new Promise(resolve=>{assert.equal(listener({type:'morning_execute',run_id:crypto.randomUUID(),nonce:crypto.randomUUID(),list,target},{id:'fictional-extension'},resolve),true);});
 const review=async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  const launcher=body.children.find(node=>node.tag==='button');assert.ok(launcher);launcher.click();
  const dialog=body.children.find(node=>node.attrs.role==='dialog');assert.ok(dialog);
  const check=walk(dialog).find(node=>node.tag==='input'&&node.attrs.type==='checkbox');
  const submit=walk(dialog).find(node=>node.tag==='button'&&/^(Enter \d|Nothing to enter)/.test(node.textContent));
  const enter=async()=>{check.checked=true;check.listeners.change();await submit.click();};
  return {dialog,enter,nodes:()=>walk(dialog)};
 };
 return {state,input,run,review};
}

test('native IC executor enters a planned missing-work zero, saves and reloads without clearing Missing',async()=>{
 const h=contentPanel(),reply=await h.run();
 assert.equal(reply.ok,true);assert.equal(reply.data.code,'read_back');
 assert.equal(reply.data.result.counts.filled,1);assert.equal(h.input.value,'0');
 assert.deepEqual(h.state.flags,['Missing']);
 assert.deepEqual(h.state.calls,['focus','set:0','input','change','keyup','model-turn','blur','save','reload']);
 assert.equal(reply.data.reasons.flag,0);
});

test('the real port event sequence lets a deferred ngModel score reach blur validation before Save',async()=>{
 const h=contentPanel({kind:'score',value:10,flags:[],recognizedEvents:true}),reply=await h.run();
 assert.equal(reply.data.code,'read_back');assert.equal(reply.data.result.counts.filled,1);
 assert.equal(h.state.modelScore,'10');assert.equal(h.state.draft,'10');assert.equal(h.state.committed,'10');
 assert.deepEqual(h.state.calls,['focus','set:10','input','change','keyup','model-turn','blur','save','reload']);
});

test('the actual own keyup validation path can recognize the model when blur alone does not',async()=>{
 const h=contentPanel({kind:'score',value:10,flags:[],recognizedEvents:true,keyupOnlyValidation:true}),reply=await h.run();
 assert.equal(reply.data.code,'read_back');assert.equal(h.state.keyupPolls,2);assert.equal(h.state.committed,'10');
 assert.equal(h.state.calls.filter(c=>c==='save').length,1);assert.equal(h.state.calls.filter(c=>c==='reload').length,1);
});

test('an unavailable IC model validation path keeps a visible typed value uncommitted and stops before Save',async()=>{
 const h=contentPanel({kind:'score',value:10,flags:[],recognizedEvents:true,keyupOnlyValidation:true,disableKeyupHandler:true}),reply=await h.run();
 assert.equal(reply.data.code,'typing_stopped');assert.equal(h.input.value,'10');assert.equal(h.state.committed,'');
 assert.equal(h.state.calls.filter(c=>c==='save').length,0);assert.equal(h.state.calls.filter(c=>c==='reload').length,0);
});

test('native port waits for delayed dirty recognition and this Save completion before reload',async()=>{
 const h=contentPanel({kind:'score',value:8,flags:[],dirtyMarkerPolls:2,saveCompletionPolls:2}),reply=await h.run();
 assert.equal(reply.data.code,'read_back');assert.equal(reply.data.result.counts.filled,1);
 assert.equal(h.state.dirtyPolls,2);assert.equal(h.state.savePolls,2);assert.equal(h.state.committed,'8');
 assert.equal(h.state.calls.filter(c=>c==='save').length,1);assert.equal(h.state.calls.filter(c=>c==='reload').length,1);
});

test('a visible typed value with only the stale Saved marker cannot reach Save or reload',async()=>{
 const h=contentPanel({kind:'score',value:8,flags:[],dirtyMarkerPolls:99}),reply=await h.run();
 assert.equal(h.input.value,'8');assert.equal(h.state.saved,true);assert.equal(h.state.committed,'');
 assert.equal(reply.data.code,'typing_stopped');assert.equal(reply.data.result.counts.not_saved,1);
 assert.equal(h.state.calls.filter(c=>c==='save').length,0);assert.equal(h.state.calls.filter(c=>c==='reload').length,0);
});

test('blank and unknown Save titles cannot certify recognized edits or reach Save',async()=>{
 for(const title of ['', 'Unsaved scores', 'Saving scores...', ' (Alt + Enter) ', '(Alt + Return)']){
  const h=contentPanel({kind:'score',value:8,flags:[],dirtyTitle:title}),reply=await h.run();
  assert.equal(reply.data.code,'typing_stopped',title);assert.equal(h.state.committed,'',title);
  assert.equal(h.state.calls.filter(c=>c==='save').length,0,title);assert.equal(h.state.calls.filter(c=>c==='reload').length,0,title);
 }
});

test('an unknown marker replacing authenticated dirty state immediately before Save blocks the click',async()=>{
 for(const title of ['', 'Unexpected state']){
  const h=contentPanel({kind:'score',value:8,flags:[],changeDirtyBeforeSave:title}),reply=await h.run();
  assert.equal(reply.data.code,'save_unconfirmed');assert.equal(reply.data.result.certain,false);
  assert.equal(h.state.calls.filter(c=>c==='save').length,0);assert.equal(h.state.calls.filter(c=>c==='reload').length,0);
 }
});

test('loss of the dirty marker after typed verification cannot borrow a prior Saved acknowledgement',async()=>{
 const h=contentPanel({kind:'score',value:8,flags:[],loseDirtyBeforeSave:true}),reply=await h.run();
 assert.equal(reply.data.code,'save_unconfirmed');assert.equal(reply.data.result.certain,false);
 assert.equal(h.state.committed,'');assert.equal(h.state.calls.filter(c=>c==='save').length,0);
 assert.equal(h.state.calls.filter(c=>c==='reload').length,0);
});

test('a Save that never completes stays uncertain after one click without reload or retry',async()=>{
 const h=contentPanel({kind:'score',value:8,flags:[],saveCompletionPolls:99}),reply=await h.run();
 assert.equal(reply.data.code,'save_unconfirmed');assert.equal(reply.data.result.certain,false);
 assert.equal(h.state.calls.filter(c=>c==='save').length,1);assert.equal(h.state.calls.filter(c=>c==='reload').length,0);
 assert.equal(h.state.committed,'');
});

test('changed assignment context during Save is uncertain without a second click',async()=>{
 const h=contentPanel({kind:'score',value:8,flags:[],saveCompletionPolls:2,saveContext:{assignment_id:'999'}}),reply=await h.run();
 assert.equal(reply.data.code,'save_unconfirmed');assert.equal(reply.data.result.certain,false);
 assert.equal(h.state.calls.filter(c=>c==='save').length,1);assert.equal(h.state.calls.filter(c=>c==='reload').length,0);
});

test('readback finds a new workspace iframe instead of waiting on the detached frame',async()=>{
 const h=contentPanel({replaceFrame:true}),reply=await h.run();
 assert.equal(reply.data.code,'read_back');assert.equal(reply.data.result.outcome,'entered');
 assert.deepEqual(h.state.calls,['focus','set:0','input','change','keyup','model-turn','blur','save','reload']);
 assert.deepEqual(h.state.flags,['Missing']);
});

test('readback waits for the Saved indicator after the new document and rows are ready',async()=>{
 const h=contentPanel({replaceFrame:true,reloadSavedPolls:2}),reply=await h.run();
 assert.equal(reply.data.code,'read_back');assert.equal(reply.data.result.outcome,'entered');
 assert.equal(h.state.reloadPolls,2);assert.equal(h.state.calls.filter(call=>call==='save').length,1);
});

test('a temporarily unreadable navigating frame can finish, but a continuing sign-in panel stays uncertain',async()=>{
 for(const [reloadPanelPolls,outcome] of [[2,'entered'],[99,'uncertain']]){
  const h=contentPanel({replaceFrame:true,reloadPanelPolls}),reply=await h.run();
  assert.equal(reply.data.result.outcome,outcome);
  assert.equal(h.state.calls.filter(call=>call==='save').length,1);
  assert.equal(h.state.calls.filter(call=>call==='reload').length,1);
 }
});

test('incomplete or duplicate reloaded rows remain uncertain without another Save or reload',async()=>{
 for(const reloadRows of ['missing','duplicate']){
  const h=contentPanel({replaceFrame:true,reloadRows}),reply=await h.run();
  assert.equal(reply.data.result.outcome,'uncertain');assert.equal(reply.data.result.certain,false);
  assert.equal(h.state.calls.filter(call=>call==='save').length,1);
  assert.equal(h.state.calls.filter(call=>call==='reload').length,1);
 }
});

test('changed assignment context or grade flags after reload are never accepted as verified',async()=>{
 for(const options of [{reloadContext:{assignment_id:'999'}},{reloadFlags:['Exempt']}]){
  const h=contentPanel({replaceFrame:true,...options}),reply=await h.run();
  assert.equal(reply.data.result.outcome,'uncertain');assert.equal(reply.data.result.certain,false);
  assert.equal(h.state.calls.filter(call=>call==='save').length,1);
  assert.equal(h.state.calls.filter(call=>call==='reload').length,1);
 }
});

test('manual uncertain result explains a missing Saved indicator without adding private receipt fields',async()=>{
 const h=contentPanel({manual:true,replaceFrame:true,reloadSavedPolls:99}),review=await h.review();
 await review.enter();
 assert.match(review.dialog.textContent,/score rows returned, but IC did not confirm their saved state in time/);
 assert.equal(h.state.results[0].outcome,'uncertain');assert.equal(h.state.results[0].certain,false);
 assert.deepEqual(Object.keys(h.state.results[0]).sort(),['list_id','form_id','class_key','week','column','section','ic_title','outcome','certain','counts','at'].sort());
 assert.equal(h.state.calls.filter(call=>call==='save').length,1);
 assert.equal(h.state.calls.filter(call=>call==='reload').length,1);
});

test('real IC planner leaves positive scores and other flags untouched',async()=>{
 for(const options of [{kind:'score',value:8,flags:['Missing']},{flags:['Exempt']},{flags:['Missing','Late']},{flags:['Late']}]){
  const h=contentPanel(options),reply=await h.run();
  assert.equal(reply.ok,true);assert.equal(reply.data.code,'nothing_to_enter');
  assert.equal(reply.data.result.counts.left,1);assert.equal(h.input.value,'');
  if(options.flags.length===1&&options.flags[0]==='Exempt')assert.equal(reply.data.reasons.exempt,1);
  assert.deepEqual(h.state.flags,options.flags);assert.deepEqual(h.state.calls,[]);
 }
});

test('native fill gate rejects nonzero Missing and zero on other flags even with an invalid proposed change',async()=>{
 for(const options of [{kind:'score',value:8,flags:['Missing']},{flags:['Exempt']},{flags:['Missing','Late']},{flags:['Late']}]){
  const h=contentPanel({...options,forcePlan:true}),reply=await h.run();
  assert.equal(reply.ok,true);assert.equal(reply.data.code,'typing_stopped');
  assert.equal(reply.data.result.outcome,'stopped');assert.equal(h.input.value,'');
  assert.deepEqual(h.state.flags,options.flags);assert.deepEqual(h.state.calls,[]);
 }
});

test('a Missing flag that appears after the planned read prevents typing or Save',async()=>{
 const h=contentPanel({flags:[],changedFlags:['Missing']}),reply=await h.run();
 assert.equal(reply.data.code,'typing_stopped');assert.equal(reply.data.result.outcome,'stopped');
 assert.equal(h.input.value,'');assert.deepEqual(h.state.flags,['Missing']);assert.deepEqual(h.state.calls,[]);
});

test('manual review preserves Exempt without a hand-check warning or follow-up receipt count',async()=>{
 const h=contentPanel({manual:true,kind:'score',value:8,flags:['Exempt']}),review=await h.review();
 assert.match(review.dialog.textContent,/1 Exempt preserved · 0 left for you/);
 assert.match(review.dialog.textContent,/preserve Exempt/);
 assert.doesNotMatch(review.dialog.textContent,/IC flag \(Exempt\): check by hand/);
 assert.ok(review.nodes().filter(node=>node.tag==='tr').every(node=>node.style.cssText!=='background:#fff6e5'));
 await review.enter();
 assert.equal(h.state.results.length,1);assert.equal(h.state.results[0].counts.left,0);
 assert.deepEqual(h.state.flags,['Exempt']);assert.deepEqual(h.state.calls,[]);
});

test('manual review still reports mixed Exempt and actionable flags as needing attention',async()=>{
 const h=contentPanel({manual:true,kind:'score',value:8,flags:['Exempt','Missing']}),review=await h.review();
 assert.match(review.dialog.textContent,/1 left for you/);
 assert.match(review.dialog.textContent,/leave: IC flag \(Exempt, Missing\): check by hand/);
 assert.doesNotMatch(review.dialog.textContent,/Exempt preserved/);
 await review.enter();
 assert.equal(h.state.results[0].counts.left,1);assert.deepEqual(h.state.calls,[]);
});
