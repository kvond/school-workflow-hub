const {test}=require('node:test');const assert=require('node:assert/strict');
globalThis.HubPostingProtocol=require('../protocol.js');const A=require('../ic-adapter.js');
const now=()=>new Date().toISOString();
function fixture(){const raw={record_id:'fictional-r1',class_key:'forensics',section_id:'101',section_name:'Fictional section',student_id:'9001',display_name:'Fictional learner',assignment_key:'fictional-task',assignment_title:'Fictional assignment',source_revision:'snapshot',status:'assessed',hold_reason:'',ic_posting_status:'unverified',ic_assignment_id:'IC_RESOURCE:301;SCORE_GROUP:401;TERM:501;TASK:601;CODE:EX1',content_points_possible:'10',revision_points_possible:'',content_score:5,content_score_exact:'5',revision_score:null};const row={...raw,exclusion_reasons:[],scores:{content_score:{value:5,exact:'5',exclusion_reasons:[]},revision_score:{value:null,exact:'',exclusion_reasons:['score_blank']}},destination_verified:false};for(const k of ['content_score','content_score_exact','revision_score'])delete row[k];return {raw,review:{schema_version:'hub-grade-posting-review/v1',source_revision:'a'.repeat(64),checked_at:now(),scope:{classKey:'forensics',sectionId:'101'},record_ids:['fictional-r1'],rows:[row],external_writes:0,posting_enabled:false}};}
test('reference grammar rejects extra parameters, partial IDs and guessed revision mappings',()=>{assert.deepEqual(A.parseReference('IC_RESOURCE:301;SCORE_GROUP:401;TERM:501;TASK:601;CODE:EX1'),{resource:'301',group:'401',term:'501',task:'601',code:'EX1'});for(const s of ['',null,'301','IC_RESOURCE:301;SCORE_GROUP:401','IC_RESOURCE:301;SCORE_GROUP:401;TERM:501;TASK:601;CODE:EX1;revision=9','IC_RESOURCE:3x;SCORE_GROUP:401;TERM:501;TASK:601;CODE:EX1'])assert.equal(A.parseReference(s),null);});
test('fresh source requires matching authenticated provenance, full revision, IDs, points and exact grades',()=>{const {raw,review}=fixture(),data={schema_version:'hub-grading-records/v1',provenance:'source_read',source_revision:review.source_revision,checked_at:now(),external_writes:0,submissions:[raw]};assert.equal(A.sameSource(review,data).rows[0].scores.content_score.value,5);for(const change of [d=>d.source_revision='b'.repeat(64),d=>d.provenance='fixture',d=>d.external_writes=1,d=>d.submissions[0].student_id='9002',d=>d.submissions[0].ic_assignment_id='other',d=>d.submissions[0].content_points_possible='100',d=>d.submissions[0].content_score=6,d=>d.submissions[0].content_score_exact='6',d=>d.submissions[0].hold_reason='review']){const changed=structuredClone(data);change(changed);assert.throws(()=>A.sameSource(review,changed),/^Error: source_changed$/);}});
test('current assignment selection requires all section, resource, term and score-group identifiers',()=>{const {review}=fixture(),s={context:{assignment_id:'301',section_id:'101',term_id:'501'},group:'401'};assert.deepEqual(A.selectAssignmentReview(review,s).record_ids,review.record_ids);for(const changed of [{...s,group:'402'},{...s,context:{...s.context,assignment_id:'302'}},{...s,context:{...s.context,term_id:'502'}},{...s,context:{...s.context,section_id:'102'}}])assert.throws(()=>A.selectAssignmentReview(review,changed),/^Error: destination_mapping_required$/);});
// Minimal DOM stub for surface() regressions: the selectors below are exactly
// the ones ic-adapter.js uses against the score panel.
function el(tag,attrs={},children=[],text){const node={tag,attrs:{...attrs},children,parent:null,text:text??''};for(const child of children)child.parent=node;return node;}
function matchesCompound(node,part){const m=/^([a-zA-Z0-9-]*)((?:[#.[][^#.[\]]*\]?)*)$/.exec(part.trim());if(!m)return false;if(m[1]&&node.tag!==m[1])return false;const rest=m[2]||'';const tokens=rest.match(/[#.][^#.[\]]+|\[[^\]]+\]/g)||[];return tokens.every(tok=>{if(tok[0]==='#')return node.attrs.id===tok.slice(1);if(tok[0]==='.')return String(node.attrs.class||'').split(/\s+/).includes(tok.slice(1));const inner=tok.slice(1,-1);let mm;if((mm=/^([^\^=]+)\^="(.*)"$/.exec(inner)))return String(node.attrs[mm[1]]??'').startsWith(mm[2]);if((mm=/^([^=]+)="(.*)"$/.exec(inner)))return node.attrs[mm[1]]===mm[2];return node.attrs[inner]!==undefined;});}
function matches(node,selector){return selector.split(',').some(part=>matchesCompound(node,part));}
function walk(node,out){for(const child of node.children){out.push(child);walk(child,out);}return out;}
function dress(node,doc){node.ownerDoc=doc;node.getClientRects=()=>node.attrs.hidden!==undefined?[]:[1];node.closest=sel=>{let cur=node;while(cur){if(matches(cur,sel))return cur;cur=cur.parent;}return null;};node.querySelectorAll=sel=>walk(node,[]).filter(n=>matches(n,sel));node.contains=other=>{let cur=other;while(cur){if(cur===node)return true;cur=cur.parent;}return false;};node.getAttribute=name=>node.attrs[name]??null;node.id=node.attrs.id||'';Object.defineProperty(node,'textContent',{get(){return node.text+node.children.map(c=>c.textContent).join('');}});Object.defineProperty(node,'firstElementChild',{get(){return node.children[0]||null;}});for(const child of node.children)dress(child,doc);return node;}
function panelFixture({zombie=true,extraHeading=false,scoreClass='multiscore__simple-score--input'}={}){
 const origin='https://fictional-school.test';
 const inner=origin+'/campus/sis/control-center/classroom/curriculum/resource/301/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/101/objectSectionID/301/calendarID/7/structureID/7)?classroomSectionID=101&classroomTermID=501';
 const outer=origin+'/campus/sis/x?classroomSectionID=101&classroomTermID=501&year=2001&schoolID=9&calendarID=7';
 const scoreChildren=[
  el('h2',{},[],'Fictional assignment'),
  el('th',{},[],'MP (10 pts)'),
  el('input',{class:scoreClass,'aria-label':'EX1 MP: Marking Period Score input for Fictional learner',id:'as_alignmentMultiScore_401-0'}),
  el('input',{class:'multiscore__simple-score--input multiscore__simple-score-fill--input','aria-label':'Fill score input',id:'text-fill-401'}),
  el('button',{title:'All scores have been saved.'},[],'Save')];
 if(zombie)scoreChildren.push(el('div',{role:'dialog'},[el('h2',{},[],'Student Summary Go to Student Information'),el('button',{},[],'Close')]));
 if(extraHeading)scoreChildren.push(el('h2',{},[],'Another visible heading'));
 const score=el('ic-multiscore',{},scoreChildren);
 const innerRoot=el('root',{},[score]);
 const innerDoc={querySelectorAll:sel=>walk(innerRoot,[]).filter(n=>matches(n,sel))};
 dress(innerRoot,innerDoc);
 const frame=el('iframe',{id:'main-workspace'});
 const topRoot=el('root',{},[frame]);
 const topDoc={querySelectorAll:sel=>walk(topRoot,[]).filter(n=>matches(n,sel))};
 dress(topRoot,topDoc);
 frame.contentDocument=innerDoc;
 frame.contentWindow={location:{href:inner}};
 return {document:topDoc,location:{origin,href:outer}};
}
function withDom(dom,run){const had={document:globalThis.document,location:globalThis.location};globalThis.document=dom.document;globalThis.location=dom.location;const restore=()=>{globalThis.document=had.document;globalThis.location=had.location;};try{const result=run();if(result&&typeof result.then==='function')return result.finally(restore);restore();return result;}catch(error){restore();throw error;}}
test('surface accepts the score panel despite the bulk-fill input and a lingering Student Summary dialog',()=>{
 // inspect() runs surface() and reports destination_unverified only on success.
 const outcome=withDom(panelFixture(),()=>A.inspect());
 return outcome.then(value=>assert.equal(value.code,'destination_unverified'));
});
test('surface accepts per-student rows when IC changes the score input CSS class',()=>{
 const outcome=withDom(panelFixture({scoreClass:'ic-score-entry-v2'}),()=>A.inspect());
 return outcome.then(value=>assert.equal(value.code,'destination_unverified'));
});
test('waitSurface tolerates score rows that render after the assignment route',async()=>{
 const dom=panelFixture(),input=walk(dom.document.querySelectorAll('iframe#main-workspace')[0].contentDocument.querySelectorAll('ic-multiscore')[0],[]).find(n=>n.tag==='input'&&n.attrs.id?.startsWith('as_alignmentMultiScore_'));
 input.attrs.hidden='';
 const outcome=withDom(dom,async()=>{
  setTimeout(()=>{delete input.attrs.hidden;},150);
  const s=await A.panel.waitSurface(1000);
  assert.equal(s.inputs.length,1);
 });
 await outcome;
});
test('waitSurface still rejects a persistently unsupported score panel without writing',async()=>{
 const dom=panelFixture(),input=walk(dom.document.querySelectorAll('iframe#main-workspace')[0].contentDocument.querySelectorAll('ic-multiscore')[0],[]).find(n=>n.tag==='input'&&n.attrs.id?.startsWith('as_alignmentMultiScore_'));
 input.attrs.hidden='';
 await withDom(dom,async()=>assert.rejects(A.panel.waitSurface(120),/^Error: unsupported_gradebook$/));
});
test('surface still rejects a second assignment heading that is not a Student Summary overlay',()=>{
 const outcome=withDom(panelFixture({extraHeading:true}),()=>A.inspect());
 return outcome.then(value=>assert.equal(value.code,'unsupported_gradebook'));
});
