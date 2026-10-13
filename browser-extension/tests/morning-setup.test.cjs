const {test}=require('node:test'),assert=require('node:assert/strict');
const T=require('../morning-tabs.js'),S=require('../morning-setup.js');
const C={HUB_ORIGIN:'https://hub.example.test',IC_ORIGIN:'https://school.example.test'};
test('only exact Hub shells count, preserving legacy and query/hash navigation',()=>{
 for(const path of ['/','/index.html','/v2','/v2/','/v2/index.html']){
  assert.equal(T.hubURL(C.HUB_ORIGIN+path,C.HUB_ORIGIN),true);
  assert.equal(T.hubURL(C.HUB_ORIGIN+path+'?page=routine#student-work',C.HUB_ORIGIN),true);
 }
 for(const path of ['/v2/api/feedback/print-today','/v2/api/feedback/daily?id=synthetic','/v2/feedback/delivery','/api/readiness','/v2/app.js','/v2/style.css','/healthz','/signin','/login','/v2/api/../','/v2/%69ndex.html','/v2//','/v2/./'])
  assert.equal(T.hubURL(C.HUB_ORIGIN+path,C.HUB_ORIGIN),false,path);
 for(const value of ['not a URL','http://hub.example.test/v2/','https://hub.example.test:443/v2/','https://user:password@hub.example.test/v2/','https://hub.example.test.foreign.test/v2/','https://hub.example.test\\v2/',' https://hub.example.test/v2/','https://hub.example.test/v2/\n'])
  assert.equal(T.hubURL(value,C.HUB_ORIGIN),false,value);
 for(const origin of ['','https://hub.example.test/','http://hub.example.test','https://hub.example.test:443','https://user@hub.example.test','https://*.example.test'])
  assert.equal(T.hubURL(C.HUB_ORIGIN+'/v2/',origin),false,origin);
});
function fixture(initial=[]){
 const own='chrome-extension://synthetic/morning-setup.html';
 const tabs=[{id:1,url:own},...initial],created=[],updated=[];
 const chrome={runtime:{getURL:()=>own,sendMessage:()=>{throw Error('No worker messages allowed');}},
  storage:{local:{set:()=>{throw Error('No storage changes allowed');}}},
  tabs:{getCurrent:async()=>tabs[0],query:async({url}={})=>url?tabs.filter(t=>(t.pendingUrl||t.url)?.startsWith(url.slice(0,-1))):tabs,
   create:async options=>{created.push(options);const t={id:tabs.length+100,pendingUrl:options.url};tabs.push(t);return t;},
   update:async(id,options)=>{assert.deepEqual(options,{active:true});updated.push(id);return tabs.find(t=>t.id===id);},
   remove:()=>{throw Error('No tabs may be closed');},sendMessage:()=>{throw Error('No site messages allowed');}}};
 return {chrome,tabs,created,updated};
}
test('setup opens missing Hub/IC only once and never invokes worker, login or storage',async()=>{
 const h=fixture();let state=await S.openMissing(h.chrome,C);
 assert.equal(state.hubs.length,1);assert.equal(state.ics.length,1);
 assert.deepEqual(h.created,[{url:C.HUB_ORIGIN+'/v2/',active:false},{url:C.IC_ORIGIN+'/',active:false}]);
 state=await S.openMissing(h.chrome,C);assert.equal(h.created.length,2);
 assert.equal(S.describe(state).status,'Your school tabs are open.');
 assert.match(S.describe(state).next,/sign-in request/);
});
test('same-origin PDF tabs remain open and do not cause a false multiple-dashboard hold',async()=>{
 const h=fixture([{id:2,url:C.HUB_ORIGIN+'/v2/'},{id:3,url:C.HUB_ORIGIN+'/v2/api/feedback/daily?id=synthetic'},{id:4,url:C.IC_ORIGIN+'/campus/'}]);
 const state=await S.openMissing(h.chrome,C);
 assert.equal(state.hubs.length,1);assert.equal(state.ics.length,1);assert.equal(h.created.length,0);
 assert.equal(h.tabs.length,4);
});
test('a PDF alone cannot stand in for a dashboard; setup adds only the missing Hub',async()=>{
 const h=fixture([{id:2,url:C.HUB_ORIGIN+'/v2/api/feedback/daily?id=synthetic'},{id:3,url:C.IC_ORIGIN+'/campus/'}]);
 const state=await S.openMissing(h.chrome,C);
 assert.equal(state.hubs.length,1);assert.equal(h.created.length,1);assert.equal(h.tabs[1].id,2);
});
test('actual duplicates stay open with their counts and a specific unsaved-work next step',async()=>{
 const h=fixture([{id:2,url:C.HUB_ORIGIN+'/v2/'},{id:3,url:C.HUB_ORIGIN+'/'},{id:4,url:C.IC_ORIGIN+'/campus/assignment'},{id:5,url:C.IC_ORIGIN+'/campus/other'}]);
 const state=await S.openMissing(h.chrome,C),view=S.describe(state);
 assert.equal(h.created.length,0);assert.equal(h.tabs.length,5);
 assert.equal(view.hub,'Hub: 2 dashboard tabs');assert.equal(view.ic,'Infinite Campus: 2 tabs');
 assert.match(view.next,/unsaved work/);assert.match(view.next,/one of each/);
});
test('a racing second setup page reuses the oldest setup without opening duplicate sites',async()=>{
 const h=fixture([{id:0,url:'chrome-extension://synthetic/morning-setup.html'}]);
 const state=await S.openMissing(h.chrome,C);
 assert.equal(state.otherSetup,true);assert.equal(h.created.length,0);assert.deepEqual(h.updated,[0]);
});
test('unconfigured or same-site origins fail closed without creating tabs',async()=>{
 for(const config of [{}, {...C,HUB_ORIGIN:''},{...C,IC_ORIGIN:C.HUB_ORIGIN},{...C,HUB_ORIGIN:C.HUB_ORIGIN+'/v2/'}]){
  const h=fixture();await assert.rejects(S.openMissing(h.chrome,config),/not_configured/);assert.equal(h.created.length,0);
 }
});
test('no settings, run, recovery, fetch, navigation of existing sites or credentials APIs exist in setup',()=>{
 const fs=require('node:fs'),path=require('node:path'),source=fs.readFileSync(path.join(__dirname,'../morning-setup.js'),'utf8');
 assert.doesNotMatch(source,/sendMessage|storage\.|fetch\(|cookies|identity\.|morning_run_now|morning_setting|morning_clear|morning_execute|tabs\.remove/);
});
