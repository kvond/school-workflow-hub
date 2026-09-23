import test from 'node:test';
import assert from 'node:assert/strict';
import {recordsFailure,createRecordsLoader,renderRecords,renderSummaries} from '../grading-records.mjs';

const fixture=revision=>({schema_version:'hub-grading-records/v1',provenance:'source_read',external_writes:0,source_revision:revision,checked_at:'2026-09-17T16:00:00Z',submissions:[],summaries:[]});
const failure=(error,status=503)=>({ok:false,status,json:async()=>({error,external_writes:0})});
const success=revision=>({ok:true,status:200,json:async()=>fixture(revision)});
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
async function loadResult(response){const states=[];await createRecordsLoader({fetcher:async()=>response,onState:s=>states.push(s)})();return states.at(-1);}

const expected={
 grading_records_not_connected:['connection',false],
 grading_records_access_denied:['access',false],
 grading_records_source_not_found:['not_found',false],
 grading_records_authentication_required:['identity',false],
 grading_records_invalid_credential:['identity',false],
 grading_workbook_layout_required:['layout',false],
 grading_workbook_headers_required:['layout',false],
 grading_workbook_changed:['changed',true],
 grading_workbook_limit:['limit',false],
 grading_records_response_limit:['limit',false],
 grading_records_range_limit:['limit',false],
 grading_records_source_rate_limited:['temporary',true],
 grading_records_source_transient_failure:['temporary',true],
 grading_records_source_unavailable:['temporary',true],
 grading_records_invalid_response:['invalid',false],
 grading_record_conflict:['records',false],
 grading_identity_conflict:['records',false],
 grading_assignment_unresolved:['records',false],
 grading_answer_unresolved:['records',false],
 grading_score_invalid:['records',false],
 grading_class_invalid:['records',false],
 grading_summary_invalid:['records',false],
};
for(const [code,[category,retryable]] of Object.entries(expected))test(`specific recovery for ${code}`,async()=>{
 const result=await loadResult(failure(code));
 assert.equal(result.code,code);assert.equal(result.category,category);assert.equal(result.retryable,retryable);
 assert.ok(result.title);assert.ok(result.message);assert.ok(result.next_action.label);assert.ok(!('data' in result));
 assert.ok(['connect','review','recheck'].includes(result.next_action.kind));
});

test('teacher authentication is separate from the server reader identity',async()=>{
 for(const status of [401,403]){
  const result=await loadResult({ok:false,status,json:async()=>{throw Error('fictional proxy HTML');}});
  assert.equal(result.code,'sign_in_required');assert.equal(result.category,'sign_in');assert.equal(result.authExpired,true);
 }
 const server=await loadResult(failure('grading_records_authentication_required'));
 assert.equal(server.category,'identity');assert.equal(server.authExpired,false);assert.match(server.message,/separate from your dashboard sign-in/);
});

test('unknown, inherited, and malicious diagnostics never appear in the error state',()=>{
 for(const payload of [null,[],{error:'__proto__'},{error:'toString'},{error:'<script>fictional-private-token</script>',message:'fictional-private-body'},'fictional-private-body',{error:42}]){
  const result=recordsFailure(503,payload);
  assert.equal(result.code,'grading_records_unavailable');
  assert.doesNotMatch(JSON.stringify(result),/fictional-private|<script>|__proto__|toString/);
 }
});

test('known diagnostic does not copy extra server response fields',async()=>{
 const result=await loadResult({ok:false,status:503,json:async()=>({error:'grading_records_access_denied',account:'fictional-private-account',source:'fictional-private-source',detail:'fictional-private-detail'})});
 assert.equal(result.code,'grading_records_access_denied');assert.doesNotMatch(JSON.stringify(result),/fictional-private/);
});

test('non-JSON service failure remains a safe unknown, not a false connection diagnosis',async()=>{
 const result=await loadResult({ok:false,status:502,json:async()=>{throw Error('fictional-private-body');}});
 assert.equal(result.category,'unknown');assert.match(result.message,/cause has not been confirmed/);
 assert.doesNotMatch(JSON.stringify(result),/fictional-private/);
});

test('transport exceptions are not displayed or automatically retried',async()=>{
 let calls=0;const states=[];
 await createRecordsLoader({fetcher:async()=>{calls++;throw Error('fictional-private-token');},onState:s=>states.push(s)})();
 assert.equal(calls,1);assert.equal(states.at(-1).category,'request');assert.ok(!states.at(-1).data);
 assert.doesNotMatch(JSON.stringify(states),/fictional-private-token/);
});

test('thrown null is handled without a secondary error',async()=>{
 const states=[];await createRecordsLoader({fetcher:async()=>{throw null;},onState:s=>states.push(s)})();
 assert.equal(states.at(-1).category,'request');
});

test('invalid success body cannot become a loaded or empty-submission result',async()=>{
 for(const response of [{ok:true,json:async()=>({submissions:[]})},{ok:true,json:async()=>{throw Error('fictional-private-body');}}]){
  const state=await loadResult(response);assert.equal(state.code,'grading_records_invalid_response');assert.equal(state.status,'error');assert.ok(!state.data);
 }
});

test('late error body cannot overwrite a newer successful refresh',async()=>{
 const body=deferred(),entered=deferred(),states=[];let calls=0;
 const load=createRecordsLoader({fetcher:async()=>++calls===1?{ok:false,status:503,json:()=>{entered.resolve();return body.promise;}}:success('newer'),onState:s=>states.push(s)});
 const first=load();await entered.promise;await load();body.resolve({error:'grading_records_access_denied'});await first;
 assert.equal(states.at(-1).status,'loaded');assert.equal(states.at(-1).data.source_revision,'newer');assert.equal(states.filter(s=>s.status==='error').length,0);
});

test('late success body cannot restore records after a newer failed refresh',async()=>{
 const body=deferred(),entered=deferred(),states=[];let calls=0;
 const load=createRecordsLoader({fetcher:async()=>++calls===1?{ok:true,json:()=>{entered.resolve();return body.promise;}}:failure('grading_workbook_headers_required'),onState:s=>states.push(s)});
 const first=load();await entered.promise;await load();body.resolve(fixture('older'));await first;
 assert.equal(states.at(-1).status,'error');assert.equal(states.at(-1).category,'layout');assert.equal(states.filter(s=>s.status==='loaded').length,0);
});

test('aborted old transport cannot replace the current recovery',async()=>{
 const waiting=[],states=[];
 const load=createRecordsLoader({fetcher:(_url,options)=>new Promise((resolve,reject)=>{waiting.push(resolve);options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')));}),onState:s=>states.push(s)});
 const first=load(),second=load();waiting[1](failure('grading_records_source_rate_limited'));await Promise.all([first,second]);
 assert.equal(states.at(-1).category,'temporary');assert.equal(states.filter(s=>s.status==='error').length,1);
});

class Element{
 constructor(doc,tag){this.ownerDocument=doc;this.tagName=tag;this.children=[];this.textContent='';this.attributes={};}
 append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}
 setAttribute(key,value){this.attributes[key]=value;}
 get allText(){return this.textContent+this.children.map(c=>c.allText).join(' ');}
}
const document={createElement:tag=>new Element(document,tag)};

test('Page 2 and Page 3 present the same actionable recovery, before another refresh',()=>{
 const state=recordsFailure(503,{error:'grading_records_access_denied'}),work=new Element(document,'div'),summaries=new Element(document,'div');
 renderRecords(work,state,{classKey:'biology'});renderSummaries(summaries,state);
 assert.equal(work.allText,summaries.allText);assert.equal(work.children[0].attributes.role,'alert');
 assert.match(work.allText,/Next action: Restore the existing reader/);assert.match(work.allText,/Diagnostic: grading_records_access_denied/);
 assert.match(work.allText,/failed read does not establish missing work/);assert.match(work.allText,/Refresh does not change grades/);
 assert.doesNotMatch(work.allText,/0 students|0 \/ 10|No submission records/);
});

test('recovery render clears earlier private text instead of retaining stale records',()=>{
 const target=new Element(document,'div'),old=new Element(document,'p');old.textContent='fictional-previous-student';target.append(old);
 renderRecords(target,recordsFailure(503,{error:'grading_records_not_connected'}),{classKey:'biology'});
 assert.doesNotMatch(target.allText,/fictional-previous-student/);assert.equal(target.children.length,1);
});

test('normal success is unchanged and refresh is a same-origin, no-store GET',async()=>{
 const states=[];let request;
 await createRecordsLoader({fetcher:async(url,options)=>{request={url,options};return success('current');},onState:s=>states.push(s)})();
 assert.deepEqual(states.map(s=>s.status),['loading','loaded']);assert.deepEqual(states.at(-1).data,fixture('current'));
 assert.equal(request.url,'/api/grading-records');assert.equal(request.options.cache,'no-store');assert.equal(request.options.credentials,'same-origin');assert.equal(request.options.redirect,'error');
 assert.ok(!request.options.method||request.options.method==='GET');assert.equal(request.options.body,undefined);
});
