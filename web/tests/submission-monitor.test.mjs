import test from 'node:test';
import assert from 'node:assert/strict';
import {validateStatus,describeFailure,renderStatus,mountSubmissionMonitor,deadlineLine,validateDiscovery,createDiscoveryLoader} from '../submission-monitor.mjs';

class Element{
 constructor(doc,tag){this.ownerDocument=doc;this.tagName=tag;this.children=[];this.textContent='';this.attributes={};this.listeners={};this.disabled=false;}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(key,value){this.attributes[key]=value;}
 addEventListener(name,fn){this.listeners[name]=fn;}
 get allText(){return this.textContent+' '+this.children.map(child=>child.allText).join(' ');}
}
const document={createElement(tag){return new Element(document,tag);}};
const root=()=>new Element(document,'div');

function status(extra={}){
 return {schema_version:'hub-submission-monitor-status/v1',external_writes:0,run_enabled:false,
  stream:{class_key:'biology',folder_id:'f'.repeat(24),assignment_label:'B01'},
  date:'2026-09-14',due_date:'2026-09-14',due_basis:'agenda_lesson_date',
  observed_at:'2026-09-15T11:00:00+00:00',baseline_established_at:'2026-09-15T11:00:00+00:00',
  scan_count:2,coverage:{complete:true,folders_checked:2,forms_found:0},
  roster:{source:'grading_workbook',checked_at:'2026-09-15T10:00:00+00:00',coverage:'verified_file_mappings_only',ic_roster_reader:'not_integrated'},
  counts:{present:2,inaccessible:0,unmatched:1,awaiting_assessment:1,needs_review:1},
  files:[{file_id:'a'.repeat(24),name:'Fictional doc',status:'present',timing:'baseline_start',
   modified_at:'2026-09-14T15:00:00+00:00',first_observed_at:'2026-09-15T11:00:00+00:00',
   student_id:'9001',section_id:'101',ic_status_recorded:'content=unverified',stage:'assessed'},
  {file_id:'b'.repeat(24),name:'Unmatched doc',status:'present',timing:'modified_after_due_needs_review',
   modified_at:'2026-09-15T02:00:00+00:00',first_observed_at:'2026-09-15T11:00:00+00:00',
   student_id:null,section_id:null,ic_status_recorded:null,stage:'detected'}],
  needs_review:[{file_id:'b'.repeat(24),reason:'unmatched_identity_needs_review'}],
  events_last_scan:[{event_id:'submission-event_x',kind:'appeared',file_id:'b'.repeat(24),observed_at:'2026-09-15T11:00:00+00:00'}],
  unchanged_rerun:false,last_attempt:{last_attempt_at:'2026-09-15T11:00:00+00:00',outcome:'complete',reason:null,last_success_at:'2026-09-15T11:00:00+00:00'},
  automation:{active:false,cadence:null,basis:'manual_scans_only'},...extra};
}

test('status contract is validated before anything renders',()=>{
 const data=status();
 assert.equal(validateStatus(data),data);
 assert.throws(()=>validateStatus({schema_version:'other'}));
 assert.throws(()=>validateStatus({...status(),external_writes:1}));
});

test('every failure code names one specific recovery action',()=>{
 for(const code of ['submission_folder_access_required','monitor_scan_in_progress','monitor_scan_conflict','monitor_state_unavailable'])
  assert.notEqual(describeFailure(code),describeFailure('unknown_code'));
 assert.match(describeFailure('submission_folder_access_required'),/Viewer/);
});

test('rendered state keeps detection, assessment and review distinct and honest',()=>{
 const target=root();renderStatus(target,status());
 const text=target.allText;
 assert.match(text,/Automatic monitoring is not active/);
 assert.match(text,/Awaiting|Detected/);
 assert.match(text,/identity unmatched/);
 assert.match(text,/modified after due · needs review/);
 assert.match(text,/IC roster reader is not integrated/);
 assert.match(text,/not proof of who submitted/);
 assert.doesNotMatch(text,/zero proposed|assigned a zero|score: 0/i);
 assert.doesNotMatch(text,/is missing|marked missing/i);
 assert.match(text,/grounds for a zero/); // the disclaimer explicitly refuses zeros
});

test('first-scan state explains the baseline without late claims',()=>{
 const target=root();
 renderStatus(target,validateStatus({schema_version:'hub-submission-monitor-status/v1',external_writes:0,run_enabled:false,status:'no_complete_scan_recorded',last_attempt:null,automation:{active:false}}));
 assert.match(target.allText,/first-observed baseline/);
 assert.match(target.allText,/never late/);
});

test('the deadline line states the rule honestly in every state',()=>{
 assert.match(deadlineLine(null),/not available/);
 assert.match(deadlineLine({status:'no_topic_start_recorded'}),/Started <topic>/);
 assert.match(deadlineLine({status:'no_topic_start_recorded'}),/third class meeting/);
 const calculated={status:'calculated',start_date:'2026-09-14',due_date:'2026-09-18',basis:'third_meeting_rule',provisional:false,meetings:['2026-09-14','2026-09-16','2026-09-18'],conflicts:[]};
 assert.match(deadlineLine(calculated),/third meeting/);
 assert.match(deadlineLine(calculated),/Unfinished topics do not move it/);
 assert.match(deadlineLine(calculated),/not yet verified in Infinite Campus/);
 assert.match(deadlineLine({...calculated,status:'start_conflict_needs_review',conflicts:['2026-09-16']}),/Conflicting topic start/);
 assert.match(deadlineLine({...calculated,basis:'teacher_exception',due_date:'2026-09-25'}),/exception/);
 assert.match(deadlineLine({...calculated,provisional:true,due_date:null}),/provisional/);
});

test('a scope update refreshes status but a scan happens only from its button',async()=>{
 const calls=[];
 const fetcher=async(url,options)=>{calls.push({url,method:options?.method||'GET'});
  return {ok:true,json:async()=>status()};};
 const container=root();
 const panel=mountSubmissionMonitor(container,{fetcher});
 panel.update({date:'2026-09-14',classKey:'biology',ready:true});
 await new Promise(r=>setTimeout(r,0));
 assert.equal(calls.length,1);
 assert.equal(calls[0].method,'GET');
 panel.update({date:'2026-09-14',classKey:'biology',ready:true});
 await new Promise(r=>setTimeout(r,0));
 assert.equal(calls.length,1); // unchanged scope: no re-fetch, never a scan
 panel.update({date:'2026-09-14',classKey:'biology',ready:false});
 panel.update({date:'2026-09-14',classKey:'biology',ready:true});
 await new Promise(r=>setTimeout(r,0));
 assert.equal(calls.length,2); // becoming ready again re-reads status
 assert.equal(calls[1].method,'GET');
 const buttons=container.children.filter(c=>c.tagName==='div').flatMap(d=>d.children).filter(c=>c.tagName==='button');
 const scan=buttons.find(b=>b.textContent==='Scan submissions now');
 await scan.listeners.click();
 await new Promise(r=>setTimeout(r,0));
 assert.equal(calls.at(-1).method,'POST');
});

test('legacy shared intake is labeled historical and keeps its typed conflicts and receipt honest',()=>{
 const target=root();
 renderStatus(target,status({coverage:{form_source:'shared_collected_identity',responses_checked:4,root_verified_files:4},
  intake_receipt:{status:'dry_run',external_writes:0},
  needs_review:[{file_id:'a'.repeat(24),reason:'typed_section_disagreement'}]}));
 assert.match(target.allText,/collected school email/);
 assert.match(target.allText,/Legacy shared-Form observation/);
 assert.match(target.allText,/each assignment’s own Form/);
 assert.match(target.allText,/Typed class period disagrees/);
 assert.match(target.allText,/no intake rows were saved/);
 assert.doesNotMatch(target.allText,/registration verified/);
});

const discoveryScope={classKey:'forensics',assignmentKey:'all'};
function freshDiscovery(extra={}){return {schema_version:'hub-submission-refresh/v1',provenance:'source_read',external_writes:0,live_model_calls:0,class_key:'forensics',assignment_key:'all',discovered_at:'2026-09-22T12:00:00Z',coverage:{complete:true},files:[{file_id:'fictional-source',source_url:'https://docs.google.com/presentation/d/fictional-source/edit',name:'Fictional deck',class_key:'forensics',assignment_key:'fictional-lesson',assignment_title:'Fictional lesson',section_id:'a',section_name:'Period A',student_id:'fictional-student',display_name:'Alex F.',discovery_status:'new_ungraded',review_reasons:[]}],...extra};}
test('read-only discovery rejects wrong scopes, effects, duplicate files and unknown status',()=>{
 assert.equal(validateDiscovery(freshDiscovery(),discoveryScope).files.length,1);
 for(const patch of [{class_key:'biology'},{assignment_key:'other'},{external_writes:1},{live_model_calls:1},{coverage:{}},{discovered_at:'invalid'}])assert.throws(()=>validateDiscovery(freshDiscovery(patch),discoveryScope));
 const repeated=freshDiscovery();repeated.files.push({...repeated.files[0]});assert.throws(()=>validateDiscovery(repeated,discoveryScope));
 const unknown=freshDiscovery();unknown.files[0].discovery_status='assessed';assert.throws(()=>validateDiscovery(unknown,discoveryScope));
 const scoped=freshDiscovery({assignment_key:'selected'});assert.throws(()=>validateDiscovery(scoped,{classKey:'forensics',assignmentKey:'selected'}));
});
test('Refresh uses only the read-only discovery endpoint with exact assignment scope',async()=>{
 const calls=[],states=[];const loader=createDiscoveryLoader({fetcher:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>freshDiscovery()};},onState:s=>states.push(s)});
 await loader.load(discoveryScope);assert.deepEqual(states.map(s=>s.status),['loading','loaded']);
 assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/submission-monitor/refresh');assert.equal(calls[0].options.method,'POST');assert.deepEqual(JSON.parse(calls[0].options.body),{class_key:'forensics',assignment_key:'all'});assert.equal(calls[0].options.credentials,'same-origin');assert.equal(calls[0].options.cache,'no-store');
});
test('scope changes cancel stale discovery and failures discard the previous preview',async()=>{
 const waiting=[],states=[];const loader=createDiscoveryLoader({fetcher:(_url,options)=>new Promise(resolve=>waiting.push({resolve,options})),onState:s=>states.push(s)});
 const old=loader.load(discoveryScope);loader.clear();assert.equal(waiting[0].options.signal.aborted,true);waiting[0].resolve({ok:true,json:async()=>freshDiscovery()});assert.equal(await old,null);assert.equal(states.at(-1).status,'empty');
 const next=loader.load(discoveryScope);waiting[1].resolve({ok:false,json:async()=>({error:'submission_form_access_required'})});await next;assert.equal(states.at(-1).status,'error');assert.equal(states.at(-1).data,undefined);
});
