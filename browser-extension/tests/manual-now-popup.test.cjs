const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../popup.js'),'utf8');
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
function popup(status){
 const elements={},messages=[];
 const element=id=>elements[id]||(elements[id]={textContent:'',checked:false,disabled:false,hidden:false,value:'',listeners:{},addEventListener(type,fn){this.listeners[type]=fn;}});
 const context={document:{getElementById:element},HubMorning:{preflightText:()=>''},setInterval:()=>{},chrome:{runtime:{sendMessage:async m=>{
  messages.push(m);return m.type==='companion_popup_status'?{configured:true,source_origin:'https://hub.example.test',destination_origin:'https://school.example.test'}:status;
 }}}};vm.runInNewContext(source,context);
 return {elements,messages,show:context.morningShow,click:async(id,event)=>element(id).listeners.click(event),element};
}
function legacy(){return {enabled:true,blocked:false,busy:false,can_retry:false,can_run_now:false,can_verify_legacy:true,
 report:{day:'2026-10-09',run_id:'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa',code:'receipt_unconfirmed',items:[]}};}
test('legacy verification UI states current mapped scope and keeps old empty attempt unconfirmed',async()=>{
 const h=popup(legacy());await settle();
 assert.equal(h.element('morning-recovery').hidden,false);assert.equal(h.element('morning-run-now').hidden,true);
 assert.match(h.element('morning-next').textContent,/every currently mapped assignment/);
 assert.match(h.element('morning-next').textContent,/does not confirm the old attempt/);
 assert.match(h.element('morning-recovery-label').textContent,/every current mapped IC assignment/);
 assert.match(h.element('morning-clear').textContent,/current scores verified/);
 assert.match(h.element('morning-status').textContent,/receipt unconfirmed/);
});
test('only trusted checked legacy click sends the explicit current-mapped confirmation protocol',async()=>{
 const h=popup(legacy());await settle();const checked=h.element('morning-checked');h.element('morning-note').value='All current mapped panels checked.';
 await h.click('morning-clear',{isTrusted:true});assert.ok(!h.messages.some(m=>m.type==='morning_clear'));
 checked.checked=true;await h.click('morning-clear',{isTrusted:false});assert.ok(!h.messages.some(m=>m.type==='morning_clear'));
 await h.click('morning-clear',{isTrusted:true});
 const m=h.messages.find(m=>m.type==='morning_clear');assert.equal(m.confirmed,true);assert.equal(m.current_mapped_confirmed,true);assert.equal(m.note,'All current mapped panels checked.');
 assert.equal(checked.checked,false);
});
test('switching verification scope or old run requires a new checkbox click',async()=>{
 const h=popup(legacy());await settle();h.element('morning-checked').checked=true;h.element('morning-clear').disabled=false;
 h.show({...legacy(),report:{...legacy().report,run_id:'bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb'}});
 assert.equal(h.element('morning-checked').checked,false);assert.equal(h.element('morning-clear').disabled,true);
 h.element('morning-checked').checked=true;h.show({...legacy(),can_verify_legacy:false,blocked:true});
 assert.equal(h.element('morning-checked').checked,false);assert.match(h.element('morning-recovery-label').textContent,/interrupted run/);
 h.element('morning-checked').checked=true;h.element('morning-note').value='Interrupted assignment checked.';
 await h.click('morning-clear',{isTrusted:true});assert.equal(h.messages.at(-1).current_mapped_confirmed,false);
});
test('acknowledged current-score verification is distinct from completion of the old run',async()=>{
 const status={...legacy(),can_verify_legacy:false,can_run_now:true,verified:{run_id:legacy().report.run_id,scope:'current_mapped_assignments',prior_day:'2026-10-09',by:'teacher@fictional.example',at:'2026-10-10T16:00:00Z'}};
 const h=popup(status);await settle();
 assert.equal(h.element('morning-recovery').hidden,true);assert.equal(h.element('morning-run-now').hidden,false);
 assert.match(h.element('morning-status').textContent,/attempt remains unconfirmed/);
 assert.match(h.element('morning-next').textContent,/new class run/);
 await h.click('morning-run-now',{isTrusted:false,target:h.element('morning-run-now')});assert.ok(!h.messages.some(m=>m.type==='morning_run_now'));
 await h.click('morning-run-now',{isTrusted:true,target:h.element('morning-run-now')});assert.ok(h.messages.some(m=>m.type==='morning_run_now'));
});
