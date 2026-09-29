const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');const crypto=require('node:crypto');
const P=require('../protocol.js');const E=require('../entry.js');
// Fictional origins, students and scores only.
const C={HUB_ORIGIN:'https://hub.example.test',IC_ORIGIN:'https://school.example.test'};
const script=name=>fs.readFileSync(path.join(__dirname,'..',name),'utf8');
const row=(last,first,kind,notes=null,revision=null)=>({last,first,section:'A4 Fictional',kind,notes,revision,reason:''});
const list=(extra={})=>({schema:'hub-ic-list/v1',list_id:crypto.randomUUID(),class_key:'biology',class_name:'Biology',week:'Wk 03',form_id:'1FictionalFormId_abcdef',assignment:'Energy Flow',form_label:'📤 Energy Flow — Turn in',status:'open',status_text:'IC: open until Oct 9.',sent_at:new Date().toISOString(),rows:[row('Avery','Ann','score',7.4,5),row('Brook','Ben','zero',0,0)],...extra});
function harness(){
 const state={posted:[],stored:{},sent:[]};
 const listeners={};
 const runtimeBase={id:'fictional-extension',getURL:name=>'chrome-extension://fictional-extension/'+name,getManifest:()=>({version:'0.3.0'})};
 const tabUrls={40:C.HUB_ORIGIN+'/v2/#work',50:C.IC_ORIGIN+'/campus/'};
 const sender=id=>({id:runtimeBase.id,frameId:0,tab:{id},url:tabUrls[id]});
 const invoke=(listener,message,who)=>new Promise((resolve,reject)=>{try{const keep=listener(message,who,resolve);if(keep!==true)queueMicrotask(()=>resolve(undefined));}catch(error){reject(error);}});
 const storage={session:{get:async key=>({[key]:structuredClone(state.stored[key])}),set:async value=>{Object.assign(state.stored,structuredClone(value));}}};
 const tabs={sendMessage:async(id,message)=>{state.sent.push({id,message});return listeners[id]?invoke(listeners[id],message,{id:runtimeBase.id}):undefined;},onRemoved:{addListener(){}},onUpdated:{addListener(){}}};
 vm.runInNewContext(script('background.js'),{importScripts(){},HubPostingProtocol:P,HubPostingConfig:C,HubIcEntry:E,chrome:{runtime:{...runtimeBase,onMessage:{addListener:fn=>listeners.background=fn}},tabs,storage},crypto,URL,Date,structuredClone,console});
 const page={};
 const window={addEventListener:(type,fn)=>{page.listener=fn;},postMessage:(value,origin)=>state.posted.push({value,origin})};window.top=window;
 const runtime={...runtimeBase,onMessage:{addListener:fn=>listeners[40]=fn},sendMessage:message=>invoke(listeners.background,message,sender(40))};
 vm.runInNewContext(script('hub-entry.js'),{HubPostingProtocol:P,HubPostingConfig:C,HubIcEntry:E,chrome:{runtime},window,location:{origin:C.HUB_ORIGIN},console});
 const fromPage=data=>page.listener({source:window,origin:C.HUB_ORIGIN,data:{channel:E.CHANNEL,version:E.VERSION,request_id:crypto.randomUUID(),...data}});
 const plain=value=>JSON.parse(JSON.stringify(value??null));
 const fromIC=async(message,id=50)=>plain(await invoke(listeners.background,{type:'entry_ic',...message},sender(id)));
 const last=type=>plain(state.posted.filter(p=>p.value.type===type).at(-1)?.value);
 const settle=()=>new Promise(r=>setTimeout(r,10));
 return {state,fromPage,fromIC,settle,last,plain,invoke:async(...a)=>plain(await invoke(...a)),background:()=>listeners.background,sender,window};
}

test('the page learns the helper is installed without asking',async()=>{
 const h=harness();await h.settle();
 assert.equal(h.state.posted[0].value.code,'helper_ready');assert.equal(h.state.posted[0].value.helper_version,'0.3.0');assert.equal(h.state.posted[0].origin,C.HUB_ORIGIN);
});

test('a list sent from the Hub is available to the IC tab only',async()=>{
 const h=harness(),sent=list();
 await h.fromPage({type:'send',list:sent});
 assert.equal(h.last('sent').code,'list_ready');
 const got=await h.fromIC({op:'lists'});
 assert.equal(got.lists[0].list.list_id,sent.list_id);
 const fromHubTab=await h.invoke(h.background(),{type:'entry_ic',op:'lists'},h.sender(40));
 assert.deepEqual(fromHubTab,{ok:false,code:'invalid_request'});
 const forged=await h.invoke(h.background(),{type:'entry_hub',op:'send',list:sent},h.sender(50));
 assert.deepEqual(forged,{ok:false,code:'invalid_request'});
});

test('a malformed or stale list is refused',async()=>{
 const h=harness();
 await h.fromPage({type:'send',list:{...list(),rows:[{...row('Avery','Ann','score',11)}]}});
 assert.equal(h.last('sent').code,'invalid_list');
 await h.fromPage({type:'send',list:list({sent_at:new Date(Date.now()-E.LIST_TTL_MS-1000).toISOString()})});
 assert.equal(h.last('sent').code,'list_expired');
 assert.deepEqual((await h.fromIC({op:'lists'})).lists,[]);
});

test('an IC result reaches the Hub page as counts only, and is remembered for its next load',async()=>{
 const h=harness(),sent=list();
 await h.fromPage({type:'send',list:sent});
 const result={list_id:sent.list_id,form_id:sent.form_id,class_key:'biology',week:'Wk 03',column:'notes',section:'A4 Fictional',ic_title:'Cycle 03 · Energy Flow (F)',outcome:'entered',certain:true,counts:{filled:1,raised:0,kept:0,left:0,not_saved:0},at:new Date().toISOString()};
 assert.deepEqual(await h.fromIC({op:'result',result}),{ok:true});
 await h.settle();
 const entered=h.last('entered');
 assert.deepEqual(entered.result,result);
 assert.ok(!JSON.stringify(h.state.posted).includes('Avery'));
 assert.deepEqual(await h.fromIC({op:'result',result:{...result,names:['Avery, Ann']}}),{ok:false,code:'invalid_result'});
 // Sent again for the same assignment: the new list replaces it, and what was entered stays noted.
 await h.fromPage({type:'send',list:list()});
 await h.fromPage({type:'status'});
 assert.deepEqual(h.last('status').results,[result]);
 assert.equal((await h.fromIC({op:'lists'})).lists.length,1);
});

test('the page bridge ignores other windows, origins and channels',async()=>{
 const h=harness();await h.settle();const before=h.state.posted.length;
 await h.fromPage({channel:'hub-grade-posting',type:'send',list:list()});
 assert.equal(h.state.posted.length,before);
 assert.deepEqual((await h.fromIC({op:'lists'})).lists,[]);
});
