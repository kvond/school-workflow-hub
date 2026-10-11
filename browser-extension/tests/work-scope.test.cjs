'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const source = fs.readFileSync(process.env.HUB_WORK_SCOPE_TEST_SOURCE || path.join(__dirname,'../../src/hub_v2/static/app.js'),'utf8');
const start = source.indexOf('async function loadWork(fresh = false) {');
const end = source.indexOf('\nfunction fileList(files)',start);
assert(start > 0 && end > start);
function setup() {
  const requests=[],renders=[],statuses=[];
  const context={
    work:{cls:'forensics',week:'Wk 02',seq:0,data:{old_week:'Wk 04'},scores:{old:10},ui:{old:{open:'ic'}},marks:{old:{entered:true}}},
    closeSheets(){},history:{replaceState(){}},setStatus(...args){statuses.push(args);},
    $(){return {replaceChildren(){}};},
    getJSON(url){return new Promise((resolve,reject)=>requests.push({url,resolve,reject}));},
    renderWork(){renders.push(context.work.data);},loadMarks:async()=>{},when:v=>String(v),
    assessmentRows:()=>[],scoreForm:async()=>{},
  };
  vm.createContext(context);vm.runInContext(source.slice(start,end),context);
  return {context,requests,renders,statuses};
}
test('a helper reply during a new week read cannot restore the previous work',async()=>{
  const a=setup(),pending=a.context.loadWork();
  assert.equal(a.context.work.data,null);
  for(const key of ['scores','ui','marks'])assert.equal(Object.keys(a.context.work[key]).length,0);
  a.context.renderWork();assert.equal(a.renders.at(-1),null);
  a.requests[0].resolve({lessons:[],current_week:'Wk 02'});await pending;
  assert.equal(a.context.work.data.current_week,'Wk 02');
});
test('a failed week read leaves the previous scores and controls unavailable',async()=>{
  const b=setup(),rejected=b.context.loadWork();
  b.requests[0].reject(new Error('controlled read failure'));await rejected;
  b.context.renderWork();assert.equal(b.renders.at(-1),null);
  assert.equal(b.statuses.at(-1)[1],'controlled read failure');
});
test('an older response cannot overwrite the subsequently selected week',async()=>{
  const c=setup(),first=c.context.loadWork();
  c.context.work.week='Wk 03';const second=c.context.loadWork();
  c.requests[1].resolve({lessons:[],current_week:'Wk 03'});await second;
  c.requests[0].resolve({lessons:[],current_week:'Wk 02'});await first;
  assert.equal(c.context.work.data.current_week,'Wk 03');
  assert(!c.renders.some(row=>row&&row.old_week));
});
