import {validateSnapshot,displayStatus,KINDS,safeLink,manualInstructions} from './contract.mjs';
import {editorStatus,saveAccessMessage} from './agenda-editor.mjs';
const $=id=>document.getElementById(id);
const classes=[{key:'class:synthetic:biology',name:'Biology',color:'#247b88',title:'',notes:''},{key:'class:synthetic:forensics',name:'Forensics',color:'#a26336',title:'',notes:''},{key:'class:synthetic:anatomy',name:'Anatomy',color:'#67539c',title:'',notes:''}];
let selected=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date()),snapshot=null,dirty=false;
const liveDays=new Map();
let loadSequence=0,liveRequested=false;
const drafts=new Map();
const saveAccess=new Map();
const draftKey=c=>c.key+':'+selected;
const fields=d=>({title:d.title,plan:d.plan,notes:d.notes});
const equalFields=(a,b)=>a&&b&&['title','plan','notes'].every(k=>a[k]===b[k]);
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function button(text,fn,cls){const n=node('button',text,cls);n.type='button';n.addEventListener('click',fn);return n;}
function dayDate(s){return new Date(s+'T12:00:00');}
function dateKey(d){return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
function monday(s){const d=dayDate(s);d.setDate(d.getDate()-(d.getDay()+6)%7);return d;}
function draft(c){const key=c.key+':'+selected;if(!drafts.has(key)) drafts.set(key,{title:selected==='2026-09-14'?c.title:'',notes:selected==='2026-09-14'?c.notes:'',plan:'',links:[],dirty:false});return drafts.get(key);}
function changed(d,status){d.dirty=true;d.message='';dirty=true;$('draft-status').hidden=false;status.textContent='Unsaved changes';$('draft-status').textContent='Unsaved changes. Use Save to Agenda before closing or reloading this page.';}
function relevant(c){return snapshot?.operations.filter(o=>o.scope.class_key===c.key&&(o.scope.week_key===null||dateKey(monday(selected))==='2026-09-14'&&o.scope.week_key==='week:synthetic:03'))||[];}
function render(){
 $('date').value=selected;const start=monday(selected),end=new Date(start);end.setDate(start.getDate()+4);
 $('week-title').textContent=`${start.toLocaleDateString('en-US',{month:'short',day:'numeric'})} – ${end.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})}`;
 $('days').replaceChildren();for(let i=0;i<7;i++){const d=new Date(start);d.setDate(start.getDate()+i);if(i>4&&dateKey(d)!==selected)continue;const b=button('',()=>selectDate(dateKey(d)));b.append(node('small',d.toLocaleDateString('en-US',{weekday:'short'})),node('strong',String(d.getDate())));if(dateKey(d)===selected)b.setAttribute('aria-current','date');$('days').append(b);}
 $('classes').replaceChildren();classes.forEach((c,i)=>{
  const d=draft(c),source=liveDays.get(selected)?.lessons.find(l=>c.key.endsWith(':'+l.class_key)),card=node('article',undefined,'class-card');card.style.setProperty('--accent',c.color);
  const head=node('div',undefined,'class-head'),heading=node('div');heading.append(node('h2',c.name),node('p',source?source.origin:'Loading Agenda'));head.append(heading,node('span',String(i+1).padStart(2,'0'),'class-number'));card.append(head);
  const lesson=node('div',undefined,'lesson'),title=node('input',undefined,'lesson-title');title.value=d.title;title.placeholder='Lesson title · awaiting Agenda';title.setAttribute('aria-label',c.name+' lesson title');
  const label=node('label','My teaching notes','field-label'),notes=node('textarea',undefined,'notes');notes.id='notes-'+i;label.htmlFor=notes.id;notes.value=d.notes;notes.placeholder='Draft a note for this class and date…';
  const access=saveAccess.get(draftKey(c));
  const status=node('span',d.message||(d.dirty?'Unsaved changes':access?.error?saveAccessMessage(access.error):source?'Loaded from Agenda':'Load this date to edit'));
  const save=button('Save to Agenda',()=>saveLesson(c));save.disabled=!access?.revision||d.saving===true;save.title=access?.revision?'Copy this dashboard title, plan and notes to the weekly Agenda':access?.error?saveAccessMessage(access.error):'Checking Agenda editing access';
  title.readOnly=notes.readOnly=!source;
  title.addEventListener('input',()=>{d.title=title.value;changed(d,status)});notes.addEventListener('input',()=>{d.notes=notes.value;changed(d,status)});
  const resources=node('div',undefined,'resources');function links(){resources.replaceChildren();if(!d.links.length){for(const text of ['No additional resources loaded'])resources.append(node('span',text,'resource-chip'));}d.links.forEach(l=>{const a=node('a',l.label+' ↗');a.href=l.url;a.target='_blank';a.rel='noopener noreferrer';resources.append(a)});}links();
  const form=node('form',undefined,'resource-form');form.hidden=true;const name=node('input'),url=node('input');name.placeholder='Link name';name.setAttribute('aria-label',c.name+' resource name');url.placeholder='https://…';url.type='url';url.setAttribute('aria-label',c.name+' resource URL');const error=node('small');
  const add=button('Add to draft',()=>{const valid=safeLink(url.value);if(!name.value.trim()||!valid){error.textContent='Add a name and an HTTPS link without embedded credentials.';return;}d.links.push({label:name.value.trim(),url:valid});links();changed(d,status);form.hidden=true;name.value='';url.value='';error.textContent='';});form.addEventListener('submit',e=>{e.preventDefault();add.click()});form.append(name,url,error,add);
  const resourceHead=node('div',undefined,'resource-header');resourceHead.append(node('span','Materials for this lesson','field-label'));
  const slides=button('Open teacher Slides ↗',()=>{});slides.disabled=!source?.teacher_slides;if(source?.teacher_slides)slides.addEventListener('click',()=>window.open(source.teacher_slides,'_blank','noopener,noreferrer'));slides.className='slides-link';slides.title='The actual teacher /edit link will come from the authenticated lesson service.';const sourceNote=node('p',source?(source.teacher_slides?'Teacher Slides from this week’s Agenda.':'No teacher /edit Slides link supplied for this week.'):'Teacher deck opens here when the dated lesson is connected. Student /copy links stay separate.','hint');const planLabel=node('label','Plan for this date','field-label'),plan=node('textarea',undefined,'plan-text');plan.id='plan-'+i;planLabel.htmlFor=plan.id;plan.readOnly=!source;plan.value=d.plan;plan.placeholder='Awaiting this date’s Agenda plan. Draft an unsynced change here…';plan.addEventListener('input',()=>{d.plan=plan.value;changed(d,status)});const state=node('div',undefined,'card-status');state.append(status,save);lesson.append(slides,sourceNote,title,planLabel,plan,label,notes,resourceHead,resources,form,state);if(source){
    lesson.append(node('p','Read '+new Date(liveDays.get(selected).checked_at).toLocaleString()+' · '+source.source_reference,'hint'));
    if(source.origin.startsWith('Dashboard')&&source.weekly_plan)lesson.append(node('h4','Weekly plan alongside Dashboard entry'),node('pre',source.weekly_plan,'review-text'));
    if(source.todo)lesson.append(node('h4','Weekly to-do'),node('pre',source.todo,'review-text'));
    if(source.prep)lesson.append(node('h4','Lab / prep'),node('pre',source.prep,'review-text'));
    if(d.dirty){const incoming=node('details');incoming.append(node('summary','Latest Agenda source · draft preserved'),node('pre',[source.title,source.plan,source.notes].join('\n\n'),'review-text'));lesson.append(incoming);}
  }card.append(lesson);if(d.sourceRevision||d.incomingSource)lesson.append(node('p',d.incomingSource?'Incoming source waiting for review · your draft is preserved':'Draft based on '+d.sourceRevision+' · not saved','hint'));
  const checks=node('details',undefined,'class-checks'),ops=relevant(c);checks.append(node('summary',ops.length ? 'Synthetic check examples · not live tasks' : 'No checks supplied · not checked'));const list=node('ul');for(const kind of Object.keys(KINDS)){const o=ops.find(o=>o.kind===kind);list.append(node('li',`${KINDS[kind]} · ${o?displayStatus(o):'Not checked'}`));}checks.append(list);card.append(checks);$('classes').append(card);
 });
 const editing=editorStatus(liveDays.has(selected),classes.map(c=>saveAccess.get(draftKey(c))));
 document.querySelector('.read-only-note').textContent=editing.note;
 document.querySelector('.preview-pill').textContent=editing.pill;
 document.dispatchEvent(new CustomEvent('agenda-saving-state',{detail:{date:selected,...editing}}));
 renderOperations();
}
function selectDate(value){if(!/^\d{4}-\d{2}-\d{2}$/.test(value)||!Number.isFinite(dayDate(value).getTime()))return;selected=value;render();if(liveRequested)loadDay();}
function openDetail(title,contents){$('detail-title').textContent=title;$('detail-body').replaceChildren(...contents);$('detail').showModal();}
function showInstructions(o){const intro=node('p','Manual handoff template. Approved instruction retrieval and the canonical record link are not connected yet. These fictional preview IDs must not be used for a live run.');const area=node('textarea');area.readOnly=true;area.value=manualInstructions(o);area.setAttribute('aria-label','Manual workflow instructions');const notice=node('p',''),copy=button('Copy instructions',async()=>{try{await navigator.clipboard.writeText(area.value);notice.textContent='Copied manual handoff template.';}catch{area.focus();area.select();notice.textContent='Clipboard unavailable. Copy the selected text manually.';}},'primary');openDetail(KINDS[o.kind], [intro,area,copy,notice]);}
function renderOperations(){const out=$('operation-list');out.replaceChildren();const ops=classes.flatMap(relevant);if(!snapshot){out.append(node('p','No valid operations snapshot loaded. Workflow runs remain unavailable.','empty'));return;}
 if(!ops.length){out.append(node('p','No checks supplied for this week. Nothing has been queued or verified.','empty'));return;}
 for(const o of ops){const card=node('article',undefined,'operation');card.append(node('h3',KINDS[o.kind]),node('span',displayStatus(o),'badge'),node('p',`${classes.find(c=>c.key===o.scope.class_key)?.name??o.scope.class_key} · Preview data`),node('p',o.last_checked_at?'Last check: '+new Date(o.last_checked_at).toLocaleString():'Last check: Not checked','hint'),node('p','Evidence level: '+o.verification.replaceAll('_',' '),'hint'));
 card.append(node('p',o.next_action?.label??'No next action supplied.'));
 const evidence=button(`Evidence (${o.evidence.length})`,()=>{const rows=o.evidence.flatMap(e=>[node('h3',e.kind),node('p',e.summary),node('p','Receipt reference: '+e.reference),node('p','Observed: '+e.observed_at)]);openDetail('Supporting evidence',[...rows,node('p','Private evidence navigation is not connected. No destination URL has been inferred.')]);});evidence.disabled=!o.evidence.length;
 const run=button('Run',()=>{});run.disabled=true;run.title='Authenticated execution and shared run claims have not been verified.';const actions=node('div',undefined,'action-row');actions.append(button('Copy instructions',()=>showInstructions(o)),run,evidence);card.append(actions,node('span','Execution not connected','hint'));out.append(card);}
}
const feedStatus=node('p','','hint lesson-feed-status');feedStatus.hidden=true;
feedStatus.setAttribute('role','status');
const loadButton=button('Load this date from Agenda',()=>{liveRequested=true;loadDay()});
$('lesson-load-control').append(loadButton);$('classes').before(feedStatus);
async function loadDay(){
 const requested=selected,sequence=++loadSequence;loadButton.disabled=true;
 feedStatus.hidden=false;feedStatus.textContent='Loading lessons…';document.dispatchEvent(new CustomEvent('agenda-day-loading',{detail:{date:requested}}));
 try{
  const r=await fetch('./api/agenda-day?date='+encodeURIComponent(requested),{cache:'no-store',credentials:'same-origin',redirect:'error'});
  if(!r.ok)throw Error();const data=await r.json();
  if(data.schema_version!=='hub-agenda-day/v1'||data.status!=='source_read'||data.date!==requested||data.save_enabled!==false||data.run_enabled!==false||data.external_writes!==0||!Number.isFinite(Date.parse(data.checked_at))||typeof data.closures!=='string'||!Array.isArray(data.lessons)||data.lessons.length!==3)throw Error();
  if(new Set(data.lessons.map(l=>l.class_key)).size!==3)throw Error();
  for(const l of data.lessons){
   if(!['biology','forensics','anatomy'].includes(l.class_key)||!['title','plan','notes','origin','weekly_plan','todo','prep','source_reference'].every(k=>typeof l[k]==='string'&&l[k].length<=20000)||!Array.isArray(l.links))throw Error();
   if(l.turnin_folder!=null&&!safeLink(l.turnin_folder))throw Error();
   if(l.assignment_label!=null&&typeof l.assignment_label!=='string')throw Error();
   if(l.teacher_slides!==null&&(!safeLink(l.teacher_slides)||!/^https:\/\/docs\.google\.com\/presentation\/d\/[A-Za-z0-9_-]+\/edit(?:[?#]|$)/.test(l.teacher_slides)))throw Error();
   for(const link of l.links)if(typeof link.label!=='string'||!safeLink(link.url))throw Error();
  }
  if(sequence!==loadSequence||selected!==requested)return;
  liveDays.set(requested,data);document.dispatchEvent(new CustomEvent('agenda-day-loaded',{detail:data}));
  for(const c of classes){const d=draft(c),l=data.lessons.find(l=>c.key.endsWith(':'+l.class_key));if(!d.dirty){d.title=l.title;d.plan=l.plan;d.notes=l.notes;d.links=l.links.map(x=>({...x}));}}
  render();
  for(const c of classes){const l=data.lessons.find(l=>c.key.endsWith(':'+l.class_key));if(!draft(c).dirty)checkSaving(c,requested,l,sequence);}
  document.dispatchEvent(new CustomEvent('agenda-source-loaded',{detail:{date:data.date,checkedAt:data.checked_at,classes:data.lessons.length}}));
  if(!dirty)$('draft-status').hidden=true;
  feedStatus.textContent='Agenda read '+new Date(data.checked_at).toLocaleString()+' · '+data.closures;
 }catch{if(sequence===loadSequence){document.dispatchEvent(new CustomEvent('agenda-day-failed'));feedStatus.textContent=liveDays.has(requested)?'Refresh failed. Showing the earlier source read; it may be out of date. Drafts are preserved.':'This date could not be read. Sign in and check Agenda access. The date may be outside the mapped school weeks. Drafts are preserved.';}}
 finally{if(sequence===loadSequence)loadButton.disabled=false;}
}
document.addEventListener('agenda-request-day',e=>{const value=e.detail?.date;if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value)||!Number.isFinite(dayDate(value).getTime())||dateKey(dayDate(value))!==value)return;selected=value;render();liveRequested=true;loadDay();});
$('prev').addEventListener('click',()=>{const d=dayDate(selected);d.setDate(d.getDate()-7);selectDate(dateKey(d))});$('next').addEventListener('click',()=>{const d=dayDate(selected);d.setDate(d.getDate()+7);selectDate(dateKey(d))});$('date').addEventListener('change',e=>selectDate(e.target.value));$('close-detail').addEventListener('click',()=>$('detail').close());
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
render();
liveRequested=true;loadDay();
try{const r=await fetch('./operations.preview.json',{cache:'no-store'});if(!r.ok)throw Error('Snapshot unavailable.');const incoming=validateSnapshot(await r.json());if(incoming.connection_state!=='preview_only')throw Error('This preview accepts fictional snapshots only.');snapshot=incoming;$('snapshot-status').textContent=`Preview snapshot assembled ${new Date(snapshot.generated_at).toLocaleString()}. This is not a source check time.`;render();}catch{$('snapshot-status').textContent='Preview checks could not be loaded. No live connection or verification is established.';$('snapshot-status').classList.add('error');}
// Optional browser agent navigation uses the same visible date selector.
if(document.modelContext?.registerTool){
 const lifecycle=new AbortController();
 Promise.resolve(document.modelContext.registerTool({
  name:'select_preview_teaching_date',title:'Select preview teaching date',
  description:'Navigate the fictional teaching planner to a date. Does not save notes or run a workflow.',
  inputSchema:{type:'object',properties:{date:{type:'string',pattern:'^\\d{4}-\\d{2}-\\d{2}$'}},required:['date'],additionalProperties:false},
  annotations:{readOnlyHint:false,untrustedContentHint:false},
  execute(input){if(!input||Object.keys(input).length!==1||typeof input.date!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(input.date)||!Number.isFinite(dayDate(input.date).getTime())||dateKey(dayDate(input.date))!==input.date)throw Error('Use a valid YYYY-MM-DD date.');selectDate(input.date);return {date:selected,mode:'preview_only',saving:false,execution:false};}
 },{signal:lifecycle.signal})).catch(()=>{});
 window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});
}

async function checkSaving(c,day,lesson,sequence){
 const key=c.key+':'+day;
 try{
  const response=await fetch('./api/agenda-save?date='+encodeURIComponent(day)+'&class_key='+lesson.class_key,{cache:'no-store',credentials:'same-origin',redirect:'error'});
  const data=await response.json();
  if(!response.ok||data.schema_version!=='hub-agenda-save/v1'||data.save_enabled!==true||!equalFields(data.values,lesson))throw Error(data.error||'Agenda changed; reload the lesson');
  if(sequence!==loadSequence)return;
  saveAccess.set(key,data);
 }catch(error){if(sequence!==loadSequence)return;saveAccess.set(key,{error:error.message});}
 if(selected===day)render();
}
function topicNote(topic){
 const line=(d)=>{
  if(!d)return '';
  const day=v=>v?new Date(v+'T12:00:00').toLocaleDateString():'—';
  if(d.status==='start_conflict_needs_review')return ' · Conflicting topic starts recorded — review needed';
  if(d.basis==='teacher_exception')return ' · Deadline exception recorded: due '+day(d.due_date);
  if(d.due_date)return ' · Topic started '+day(d.start_date)+' → 3 meetings '+(d.meetings||[]).map(day).join(', ')+' → final revised work due end of '+day(d.due_date)+' (calculated, not yet in Infinite Campus)';
  if(d.start_date)return ' · Topic start '+day(d.start_date)+' recorded; deadline provisional until the calendar ahead is filled in';
  return '';
 };
 if(!topic)return '';
 if(topic.recorded==='record_failed')return ' · Topic start could not be recorded — check the monitoring panel';
 return line(topic.deadline);
}
async function saveLesson(c){
 const day=selected,key=draftKey(c),d=draft(c),access=saveAccess.get(key);
 if(!access?.revision||d.saving)return;
 const values=fields(d),signature=JSON.stringify(values);
 if(d.pendingSignature!==signature){d.requestId=crypto.randomUUID();d.pendingSignature=signature;}
 d.saving=true;d.message='Saving…';render();
 try{
  const response=await fetch('./api/agenda-save',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',redirect:'error',body:JSON.stringify({date:day,class_key:c.key.split(':').at(-1),revision:access.revision,values,request_id:d.requestId})});
  const result=await response.json();
  if(!response.ok||result.status!=='saved'||result.readback_verified!==true||!equalFields(result.values,values))throw Error(response.status===409?'Agenda changed or another save is running. Your edits are still here.':result.error||'Save could not be verified');
  access.revision=result.revision;access.values=result.values;
  d.dirty=JSON.stringify(fields(d))!==signature;d.message=d.dirty?'Unsaved changes':'Saved to Agenda · verified'+topicNote(result.topic);d.requestId=null;d.pendingSignature=null;
  const lesson=liveDays.get(day)?.lessons.find(l=>c.key.endsWith(':'+l.class_key));if(lesson)Object.assign(lesson,result.values);
 }catch(error){d.message='Not saved: '+error.message;d.dirty=true;}
 finally{d.saving=false;dirty=[...drafts.values()].some(v=>v.dirty);$('draft-status').hidden=!dirty;if(selected===day)render();}
}
