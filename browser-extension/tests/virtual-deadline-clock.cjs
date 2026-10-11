'use strict';
// Drive real async control flow with an explicit clock; no wall-clock waits.
const tick=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
function virtualClock(){
 let now=0,next=0;const jobs=new Map(),scheduled=[];
 const setTimeout=(fn,ms)=>{const id=++next;jobs.set(id,{at:now+ms,fn});scheduled.push(ms);return id;};
 const clearTimeout=id=>jobs.delete(id);
 async function advance(ms){
  await tick();const end=now+ms;
  for(;;){
   const due=[...jobs].filter(([,j])=>j.at<=end).sort((a,b)=>a[1].at-b[1].at||a[0]-b[0])[0];
   if(!due)break;
   now=due[1].at;jobs.delete(due[0]);due[1].fn();await tick();
  }
  now=end;await tick();
 }
 return {setTimeout,clearTimeout,setInterval:()=>0,clearInterval:()=>{},advance,tick,scheduled,pending:()=>jobs.size};
}
module.exports={virtualClock};
