const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const crypto=require('node:crypto');
const P=require('../protocol.js');
const request_id='d2a76033-928d-4032-a447-33d1b1830691';
const C={HUB_ORIGIN:'https://hub.example.test',IC_ORIGIN:'https://school.example.test'};
const script=name=>fs.readFileSync(path.join(__dirname,'..',name),'utf8');
const assignmentPath='/campus/instruction/resource/50002/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/50001/objectSectionID/50002/calendarID/50003/structureID/50004)';
const assignmentURL=C.IC_ORIGIN+assignmentPath+'?classroomSectionID=50001&classroomTermID=50005&schoolID=50006&year=2026';
const summaryURL=person=>assignmentURL.replace(')',`//tertiaryRouteOutlet:studentInfoSidePanel/multiscore/50001/${person}/1/50002)`);
const claim={task_id:'task_'+'a'.repeat(64),run_id:'run_'+'b'.repeat(64),fence:1,input_hash:'c'.repeat(64),expires_at:new Date(Date.now()+900000).toISOString()};
function fixture(now){return {schema_version:'hub-grade-posting-review/v1',source_revision:'a'.repeat(64),checked_at:new Date(now).toISOString(),scope:{classKey:'forensics',sectionId:'matched'},record_ids:[],rows:[],external_writes:0,posting_enabled:false};}
const report=()=>({workflow:'grading',outcome:'checked',summary:'Verified current scores; no changes needed.',counts:{checked:2,changed:0,needs_review:0},evidence:[{kind:'grade_readback',reference:'plan_sha256:'+'d'.repeat(64),checked_at:new Date().toISOString()}]});
function harness(){
 const state={now:Date.now(),posted:[],fetches:[],ports:null,serverClaim:null,fetchHook:null,replyHook:null,tabRemoved:null,tabUpdated:null,privateSource:'PRIVATE_STUDENT_SECRET',timers:new Map(),timerId:0};
 const listeners={},siteListeners={};
 class Clock extends Date{static now(){return state.now;}}
 const setInterval=(fn,ms)=>{const id=++state.timerId;state.timers.set(id,{fn,ms});return id;};
 const clearInterval=id=>state.timers.delete(id);
 const protocol={...P,validateReview:value=>P.validateReview(value,state.now),validateRequest:value=>P.validateRequest(value,state.now)};
 const tabUrls={40:C.HUB_ORIGIN+'/#feedback',50:C.IC_ORIGIN+'/campus/'};
 const runtimeBase={id:'fictional-extension',getURL:name=>'chrome-extension://fictional-extension/'+name,getManifest:()=>({version:'0.2.0'})};
 const bgSender={id:runtimeBase.id};
 const sender=id=>({id:runtimeBase.id,frameId:0,tab:{id},url:tabUrls[id]});
 const invoke=(listener,message,who)=>new Promise((resolve,reject)=>{try{const keep=listener(message,who,resolve);if(keep!==true)queueMicrotask(()=>resolve(undefined));}catch(error){reject(error);}});
 const tabs={query:async()=>[{id:50,url:tabUrls[50]}],get:async id=>({id,url:tabUrls[id]}),update:async id=>({id,url:tabUrls[id]}),create:async()=>({id:50,url:tabUrls[50]}),sendMessage:async(id,message)=>{const value=await invoke(listeners[id],message,bgSender);return state.replyHook?state.replyHook(id,message,value):value;},onRemoved:{addListener:fn=>state.tabRemoved=fn},onUpdated:{addListener:fn=>state.tabUpdated=fn}};
 const status=()=>({schema_version:'hub-browser-runs/v1',workflow:'grading',busy:Boolean(state.serverClaim),owned_by_this_browser:Boolean(state.serverClaim),active_claim:state.serverClaim,recovery_required:false,run_enabled:false,server_verified:false,external_writes:0});
 async function fetch(url,options){
  state.fetches.push({url,options});
  if(state.fetchHook){const hooked=await state.fetchHook(url,options);if(hooked)return hooked;}
  let data;
  if(url==='/api/grading-records')data={schema_version:'hub-grading-records/v1',provenance:'source_read',source_revision:'a'.repeat(64),checked_at:new Date(state.now).toISOString(),assignments:[],submissions:[{display_name:state.privateSource}],summaries:[],external_writes:0};
  else if(options.method==='POST'){
   const body=JSON.parse(options.body);
   if(body.action==='claim'){state.serverClaim=claim;data={...status(),claim,completed:false};}
   else if(body.action==='renew')data={...status(),claim,completed:false};
   else if(body.action==='finish'){state.serverClaim=null;data={...status(),completed:true,receipt:{provenance:'browser_report',server_verified:false,result_hash:'e'.repeat(64)}};}
   else if(body.action==='release'){state.serverClaim=null;data=status();}
  }else data=status();
  return {ok:true,status:200,text:async()=>JSON.stringify(data)};
 }
 function startBackground(){
  vm.runInNewContext(script('background.js'),{importScripts(){},HubPostingProtocol:protocol,HubPostingConfig:C,chrome:{runtime:{...runtimeBase,onMessage:{addListener:fn=>listeners.background=fn}},tabs},crypto,URL,Date:Clock});
 }
 startBackground();
 for(const [id,file]of [[40,'hub-content.js'],[50,'ic-content.js']]){
  const window={addEventListener:(type,fn)=>siteListeners[id]=fn,postMessage:(value,origin)=>state.posted.push({value,origin})};window.top=window;
  state['window'+id]=window;
  const runtime={...runtimeBase,onMessage:{addListener:fn=>listeners[id]=fn},sendMessage:message=>invoke(listeners.background,message,sender(id))};
  vm.runInNewContext(script(file),{HubPostingProtocol:protocol,HubPostingConfig:C,HubIcAdapter:{prepare:async(review,ports)=>{state.ports=ports;return {code:'review_opened'};}},chrome:{runtime},window,location:{origin:id===40?C.HUB_ORIGIN:C.IC_ORIGIN},crypto,URLSearchParams,fetch,Date:Clock,setInterval,clearInterval});
 }
 const open=async()=>{
  await siteListeners[40]({source:state.window40,origin:C.HUB_ORIGIN,data:{channel:P.CHANNEL,version:1,request_id,type:'prepare',review:fixture(state.now)}});
  assert.equal(state.posted.at(-1).value.code,'review_opened');
  return state.ports;
 };
 return {state,open,sender,invoke,background:()=>listeners.background,hub:()=>listeners[40],page:event=>siteListeners[40](event),restart:startBackground,status,source:()=>fixture(state.now),advance:ms=>state.now+=ms,tabUrls};
}
const statusRequest={channel:P.CHANNEL,version:1,request_id,type:'status'};
test('Hub bridge ignores another window or origin',async()=>{const h=harness();await h.page({source:{},origin:C.HUB_ORIGIN,data:statusRequest});await h.page({source:h.state.window40,origin:'https://forged.example.test',data:statusRequest});assert.equal(h.state.posted.length,0);assert.equal(h.state.fetches.length,0);});
test('background rejects forged extension, origin and subframe senders',async()=>{const h=harness();for(const sender of [{...h.sender(40),id:'other-extension'},{...h.sender(40),url:'https://forged.example.test/'},{...h.sender(40),frameId:1}]){const result=await h.invoke(h.background(),{type:'hub_request',request:statusRequest},sender);assert.equal(result,undefined);}assert.equal(h.state.fetches.length,0);});
test('review opens in IC with expiry; private fresh source never reaches the webpage',async()=>{const h=harness(),ports=await h.open();assert.ok(h.state.posted[0].value.expires_at>h.state.now);const source=await ports.hubCall('source',{});assert.equal(source.submissions[0].display_name,h.state.privateSource);assert.ok(!JSON.stringify(h.state.posted).includes(h.state.privateSource));assert.equal(h.state.fetches[0].url,'/api/grading-records');assert.equal(h.state.fetches[0].options.credentials,'same-origin');assert.equal(h.state.fetches[0].options.redirect,'error');});
test('page requests cannot call private source, claim, arbitrary URL or finish operations',async()=>{const h=harness();await h.page({source:h.state.window40,origin:C.HUB_ORIGIN,data:{...statusRequest,type:'claim',url:'https://forged.example.test'}});assert.equal(h.state.posted.at(-1).value.code,'invalid_review_request');assert.equal(h.state.fetches.length,0);const result=await h.invoke(h.hub(),{type:'hub_operation',session_id:crypto.randomUUID(),request_id,call_id:crypto.randomUUID(),action:'source',payload:{}},h.sender(40));assert.equal(result,undefined);});
test('private operations reject unbound IC tab or wrong correlation before fetching',async()=>{const h=harness();await h.open();const message={type:'ic_hub_call',session_id:crypto.randomUUID(),request_id,call_id:crypto.randomUUID(),action:'source',payload:{}};const result=await h.invoke(h.background(),message,h.sender(50));assert.equal(result.ok,false);assert.equal(result.code,'review_expired');assert.equal(h.state.fetches.length,0);});
test('source revision changes and raw provider errors return safe codes',async()=>{const h=harness(),ports=await h.open();h.state.fetchHook=async()=>({ok:false,status:500,text:async()=>JSON.stringify({error:'PRIVATE_PROVIDER_CREDENTIALS'})});await assert.rejects(ports.hubCall('source',{}),/^Error: source_unavailable$/);assert.ok(!JSON.stringify(h.state.posted).includes('PRIVATE_PROVIDER'));h.state.fetchHook=async()=>({ok:true,status:200,text:async()=>JSON.stringify({schema_version:'hub-grading-records/v1',provenance:'source_read',source_revision:'b'.repeat(64),checked_at:new Date().toISOString(),assignments:[],submissions:[],summaries:[],external_writes:0})});await assert.rejects(ports.hubCall('source',{}),/^Error: source_changed$/);});
test('claim establishes cookie via status GET and uses fixed authenticated endpoint',async()=>{const h=harness(),ports=await h.open();const result=await ports.hubCall('claim',{request_id:'d'.repeat(64)});assert.equal(result.claim.run_id,claim.run_id);assert.equal(h.state.fetches.length,2);assert.ok(h.state.fetches[0].url.startsWith('/api/browser-runs?'));const body=JSON.parse(h.state.fetches[1].options.body);assert.equal(body.workflow,'grading');assert.equal(body.action,'claim');assert.ok(P.UUID.test(body.worker_id));assert.equal(h.state.fetches[1].options.credentials,'same-origin');await assert.rejects(ports.hubCall('source',{url:'https://forged.example.test'}),/^Error: invalid_browser_operation$/);});
test('uncertain report retains exact retry and blocks subsequent source or grading actions',async()=>{const h=harness(),ports=await h.open();const owned=(await ports.hubCall('claim',{request_id:'d'.repeat(64)})).claim,payload={claim:owned,report:report()};let failures=1;h.state.fetchHook=async(url,options)=>{if(options.method==='POST'&&JSON.parse(options.body).action==='finish'&&failures-->0)throw Error('PRIVATE_NETWORK_ERROR');};await assert.rejects(ports.hubCall('finish',payload),/^Error: report_unconfirmed$/);await assert.rejects(ports.hubCall('source',{}),/^Error: report_retry_required$/);await assert.rejects(ports.hubCall('finish',{...payload,report:{...payload.report,summary:'Different receipt'}}),/^Error: report_retry_required$/);h.advance(P.REVIEW_TTL_MS+1);const result=await ports.hubCall('finish',payload);assert.equal(result.completed,true);const finishBodies=h.state.fetches.filter(item=>item.options.method==='POST'&&JSON.parse(item.options.body).action==='finish').map(item=>JSON.parse(item.options.body));assert.equal(finishBodies.length,2);assert.deepEqual(finishBodies[0],finishBodies[1]);});
test('completed already-match result publishes safe counts without grade data',async()=>{const h=harness(),ports=await h.open();const owned=(await ports.hubCall('claim',{request_id:'d'.repeat(64)})).claim;await ports.hubCall('finish',{claim:owned,report:report()});await ports.onResult({code:'already_matches',counts:{checked:2,changed:0,needs_review:0}});const result=h.state.posted.at(-1).value;assert.equal(result.code,'already_matches');assert.equal(result.external_writes,0);assert.deepEqual(JSON.parse(JSON.stringify(result.counts)),{checked:2,changed:0,needs_review:0});assert.ok(!JSON.stringify(result).includes('source_revision'));});
test('uncertain save publishes unknown write count without manufacturing zero',async()=>{const h=harness(),ports=await h.open();await ports.onResult({code:'needs_review',counts:{checked:2,changed:0,needs_review:2},possible_changes:true,external_writes:null});const result=h.state.posted.at(-1).value;assert.equal(result.external_writes,null);assert.equal(result.possible_changes,true);});
test('expiry, browser-worker restart and navigation invalidate the private session',async()=>{for(const mutate of [h=>h.advance(P.REVIEW_TTL_MS+1),h=>h.restart(),h=>h.state.tabUpdated(40,{url:C.HUB_ORIGIN+'/#summary'})]){const h=harness(),ports=await h.open();mutate(h);await assert.rejects(ports.hubCall('source',{}),/^Error: review_expired$/);assert.equal(h.state.fetches.length,0);}});
test('forged runtime reply correlation never delivers private data',async()=>{const h=harness(),ports=await h.open();h.state.replyHook=(id,message,value)=>message.type==='hub_operation'?{...value,call_id:'forged',data:{secret:'PRIVATE_STUDENT_SECRET'}}:value;await assert.rejects(ports.hubCall('source',{}),/^Error: operation_unconfirmed$/);assert.ok(!JSON.stringify(h.state.posted).includes(h.state.privateSource));});

test('bounded private heartbeat stops on completion and makes no school request',async()=>{const h=harness(),ports=await h.open();assert.equal(h.state.timers.size,1);const timer=[...h.state.timers.values()][0];assert.equal(timer.ms,20000);await timer.fn();assert.equal(h.state.fetches.length,0);assert.equal(h.state.timers.size,1);await ports.onResult({code:'already_matches',counts:{checked:0,changed:0,needs_review:0}});assert.equal(h.state.timers.size,0);});
test('receipt-pending heartbeat remains bounded by original review expiry',async()=>{const h=harness(),ports=await h.open();await ports.onResult({code:'report_unconfirmed',counts:{checked:0,changed:0,needs_review:0}});assert.equal(h.state.timers.size,1);h.advance(P.REVIEW_TTL_MS+1);await [...h.state.timers.values()][0].fn();assert.equal(h.state.timers.size,0);assert.equal(h.state.fetches.length,0);});
test('worker restart causes heartbeat to stop and invalidates later operations',async()=>{const h=harness(),ports=await h.open();h.restart();await [...h.state.timers.values()][0].fn();assert.equal(h.state.timers.size,0);await assert.rejects(ports.hubCall('source',{}),/^Error: review_expired$/);assert.equal(h.state.fetches.length,0);});
test('observed Student Summary opening and closing preserve only the same assignment binding',async()=>{const h=harness();h.tabUrls[50]=assignmentURL;const ports=await h.open();for(const url of [summaryURL('9000001'),summaryURL('9000002'),assignmentURL]){h.tabUrls[50]=url;h.state.tabUpdated(50,{url});assert.equal((await ports.hubCall('source',{})).source_revision,'a'.repeat(64));}assert.equal(h.state.fetches.length,3);});
test('an initial observed Student Summary route normalizes to its exact parent assignment',async()=>{const h=harness();h.tabUrls[50]=summaryURL('9000001');const ports=await h.open();h.tabUrls[50]=assignmentURL;h.state.tabUpdated(50,{url:assignmentURL});await ports.hubCall('source',{});assert.equal(h.state.fetches.length,1);});
test('other assignment, section, route, query, hash and origin changes invalidate the binding',async()=>{const variants=[assignmentURL.replaceAll('50002','60002'),summaryURL('9000001').replace('/multiscore/50001/','/multiscore/60001/'),summaryURL('9000001').replace('/1/50002)','/1/60002)'),summaryURL('9000001').replace('studentInfoSidePanel','otherSidePanel'),summaryURL('9000001').replace('/1/50002)','/2/50002)'),summaryURL('9000001').replace('/9000001/1/','/not-a-person/1/'),assignmentURL.replace('year=2026','year=2027'),assignmentURL+'#changed',assignmentURL.replace(C.IC_ORIGIN,'https://forged.example.test'),assignmentURL.replace('/structureID/50004','/structureID/60004')];for(const url of variants){const h=harness();h.tabUrls[50]=assignmentURL;const ports=await h.open();h.tabUrls[50]=url;h.state.tabUpdated(50,{url});await assert.rejects(ports.hubCall('source',{}),/^Error: review_expired$/);assert.equal(h.state.fetches.length,0);}});
test('checkTabs catches changed Hub or IC URLs even without an onUpdated event',async()=>{for(const [tabId,url]of [[40,C.HUB_ORIGIN+'/#summary'],[50,assignmentURL.replaceAll('50002','60002')]]){const h=harness();h.tabUrls[50]=assignmentURL;const ports=await h.open();h.tabUrls[tabId]=url;await assert.rejects(ports.hubCall('source',{}),/^Error: review_expired$/);assert.equal(h.state.fetches.length,0);}});
