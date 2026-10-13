/* Read-only tab selection shared by morning setup and the weekday worker.
 * PDFs, API replies and other same-origin pages are not Hub dashboards. */
(() => {
 'use strict';
 const shells=new Set(['/','/index.html','/v2','/v2/','/v2/index.html']);
 function origin(value){
  try{
   const u=new URL(value);
   return typeof value==='string'&&value===u.origin&&u.protocol==='https:'
    &&!u.username&&!u.password&&!u.port&&u.hostname.includes('.')&&!u.hostname.includes('..')
    &&/^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/.test(u.hostname)?value:null;
  }catch{return null;}
 }
 function siteURL(value,site){
  try{
   if(!origin(site)||typeof value!=='string'||/[\s\\]/.test(value))return false;
   const u=new URL(value);
   return u.origin===site&&u.protocol==='https:'&&!u.username&&!u.password
    &&(value===site||value.startsWith(site+'/')||value.startsWith(site+'?')||value.startsWith(site+'#'));
  }catch{return false;}
 }
 function hubURL(value,site){
  if(!siteURL(value,site))return false;
  // Check the literal path too: URL() must not turn a dot-segment API path
  // or an encoded path into an approved app shell.
  const literal=value.slice(site.length).split(/[?#]/,1)[0]||'/';
  return shells.has(literal)&&shells.has(new URL(value).pathname);
 }
 const tabURL=tab=>tab?.pendingUrl||tab?.url;
 const hubTabs=(tabs,site)=>tabs.filter(tab=>hubURL(tabURL(tab),site));
 const siteTabs=(tabs,site)=>tabs.filter(tab=>siteURL(tabURL(tab),site));
 const api=Object.freeze({origin,siteURL,hubURL,hubTabs,siteTabs});
 globalThis.HubMorningTabs=api;
 if(typeof module==='object'&&module.exports)module.exports=api;
})();
