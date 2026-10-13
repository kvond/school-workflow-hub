/* Navigation only. No worker message, sign-in probe, settings, storage or grades. */
(() => {
 'use strict';
 const T=globalThis.HubMorningTabs;
 async function inspect(chrome,config){
  if(!T?.origin(config?.HUB_ORIGIN)||!T.origin(config?.IC_ORIGIN)||config.HUB_ORIGIN===config.IC_ORIGIN)throw Error('not_configured');
  const [hubs,ics]=await Promise.all([
   chrome.tabs.query({url:config.HUB_ORIGIN+'/*'}),
   chrome.tabs.query({url:config.IC_ORIGIN+'/*'})
  ]);
  return {hubs:T.hubTabs(hubs,config.HUB_ORIGIN),ics:T.siteTabs(ics,config.IC_ORIGIN)};
 }
 async function openMissing(chrome,config){
  // When two launcher clicks race, only the oldest setup tab opens sites.
  // An extension can inspect its own page URLs without the tabs permission.
  const ownURL=chrome.runtime.getURL('morning-setup.html');
  const [current,all]=await Promise.all([chrome.tabs.getCurrent(),chrome.tabs.query({})]);
  const setups=all.filter(tab=>tab.url===ownURL||tab.pendingUrl===ownURL).sort((a,b)=>a.id-b.id);
  if(current&&setups.length&&current.id!==setups[0].id){
   await chrome.tabs.update(setups[0].id,{active:true});
   return {...await inspect(chrome,config),otherSetup:true};
  }
  const before=await inspect(chrome,config);
  if(!before.hubs.length)await chrome.tabs.create({url:config.HUB_ORIGIN+'/v2/',active:false});
  if(!before.ics.length)await chrome.tabs.create({url:config.IC_ORIGIN+'/',active:false});
  return inspect(chrome,config);
 }
 function describe(state){
  const multiple=state.hubs.length>1||state.ics.length>1;
  const ready=state.hubs.length===1&&state.ics.length===1;
  return {
   status:state.otherSetup?'Morning setup is already open in another tab.':multiple?'Your school tabs are open. Choose one of each.':ready?'Your school tabs are open.':'School tabs still need attention.',
   hub:`Hub: ${state.hubs.length} dashboard tab${state.hubs.length===1?'':'s'}`,
   ic:`Infinite Campus: ${state.ics.length} tab${state.ics.length===1?'':'s'}`,
   next:multiple?'Check for unsaved work, then close extra Hub dashboards or IC tabs until one of each remains.':state.otherSetup?'Use the existing School morning tab.':ready?'Check the Hub and Infinite Campus tabs for any sign-in request.':'Check the browser for a blocked or still-loading school tab, then check again.'
  };
 }
 const api=Object.freeze({inspect,openMissing,describe});
 if(typeof module==='object'&&module.exports){module.exports=api;return;}
 const status=document.getElementById('status'),next=document.getElementById('next'),list=document.getElementById('tabs'),button=document.getElementById('check');
 let busy=false;
 async function show(open){
  if(busy)return;busy=true;button.disabled=true;
  try{
   const result=describe(await (open?openMissing:inspect)(chrome,globalThis.HubPostingConfig));
   status.textContent=result.status;next.textContent=result.next;list.replaceChildren();
   for(const label of [result.hub,result.ic]){const li=document.createElement('li');li.textContent=label;list.append(li);}
  }catch{
   status.textContent='School tabs could not be checked.';
   next.textContent='Use the installed school helper with its configured Hub and Infinite Campus sites.';
  }finally{busy=false;button.disabled=false;}
 }
 button.addEventListener('click',()=>show(true));
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)show(true);});
 show(true);
})();
