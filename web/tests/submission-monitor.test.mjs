import test from 'node:test';
import assert from 'node:assert/strict';
import {validateStatus,describeFailure,renderStatus,mountSubmissionMonitor} from '../submission-monitor.mjs';

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
