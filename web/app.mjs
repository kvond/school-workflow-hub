import {describeDraft,stagePreviewNotes} from './draft-review.mjs';
import {validateSnapshot,displayStatus,KINDS,safeLink,manualInstructions} from './contract.mjs';
const $=id=>document.getElementById(id);
const classes=[{key:'class:synthetic:biology',name:'Biology',color:'#247b88',title:'',notes:''},{key:'class:synthetic:forensics',name:'Forensics',color:'#a26336',title:'',notes:''},{key:'class:synthetic:anatomy',name:'Anatomy',color:'#67539c',title:'',notes:''}];
let selected='2026-09-14',snapshot=null,dirty=false;
const drafts=new Map();
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function button(text,fn,cls){const n=node('button',text,cls);n.type='button';n.addEventListener('click',fn);return n;}
function dayDate(s){return new Date(s+'T12:00:00');}
function dateKey(d){return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
function monday(s){const d=dayDate(s);d.setDate(d.getDate()-(d.getDay()+6)%7);return d;}
function draft(c){const key=c.key+':'+selected;if(!drafts.has(key)) drafts.set(key,{title:selected==='2026-09-14'?c.title:'',notes:selected==='2026-09-14'?c.notes:'',plan:'',links:[],dirty:false});return drafts.get(key);}
function changed(d,status){d.dirty=true;dirty=true;status.textContent='Unsynced edits';$('draft-status').textContent='Unsynced edits · kept only in this open tab. Switching days keeps them here; closing or reloading loses them. Sheets have not been updated.';}
function relevant(c){return snapshot?.operations.filter(o=>o.scope.class_key===c.key&&(o.scope.week_key===null||dateKey(monday(selected))==='2026-09-14'&&o.scope.week_key==='week:synthetic:03'))||[];}
function render(){
 $('date').value=selected;const start=monday(selected),end=new Date(start);end.setDate(start.getDate()+4);
 $('week-title').textContent=`${start.toLocaleDateString('en-US',{month:'short',day:'numeric'})} – ${end.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})}`;
 $('days').replaceChildren();for(let i=0;i<7;i++){const d=new Date(start);d.setDate(start.getDate()+i);if(i>4&&dateKey(d)!==selected)continue;const b=button('',()=>selectDate(dateKey(d)));b.append(node('small',d.toLocaleDateString('en-US',{weekday:'short'})),node('strong',String(d.getDate())));if(dateKey(d)===selected)b.setAttribute('aria-current','date');$('days').append(b);}
 $('classes').replaceChildren();classes.forEach((c,i)=>{
  const d=draft(c),card=node('article',undefined,'class-card');card.style.setProperty('--accent',c.color);
  const head=node('div',undefined,'class-head'),heading=node('div');heading.append(node('h2',c.name),node('p','Dated Agenda source · not connected'));head.append(heading,node('span',String(i+1).padStart(2,'0'),'class-number'));card.append(head);
  const lesson=node('div',undefined,'lesson'),title=node('input',undefined,'lesson-title');title.value=d.title;title.placeholder='Lesson title · awaiting Agenda';title.setAttribute('aria-label',c.name+' lesson title');
  const label=node('label','My teaching notes','field-label'),notes=node('textarea',undefined,'notes');notes.id='notes-'+i;label.htmlFor=notes.id;notes.value=d.notes;notes.placeholder='Draft a note for this class and date…';
  const status=node('span',d.dirty?'Unsynced edits':'Preview draft · source not loaded'),save=button('Save to Sheets',()=>{});save.disabled=true;save.title='The backend contract does not support edit saving yet.';
  title.addEventListener('input',()=>{d.title=title.value;changed(d,status)});notes.addEventListener('input',()=>{d.notes=notes.value;changed(d,status)});
  const resources=node('div',undefined,'resources');function links(){resources.replaceChildren();if(!d.links.length){for(const text of ['No additional resources loaded'])resources.append(node('span',text,'resource-chip'));}d.links.forEach(l=>{const a=node('a',l.label+' ↗');a.href=l.url;a.target='_blank';a.rel='noopener noreferrer';resources.append(a)});}links();
  const form=node('form',undefined,'resource-form');form.hidden=true;const name=node('input'),url=node('input');name.placeholder='Link name';name.setAttribute('aria-label',c.name+' resource name');url.placeholder='https://…';url.type='url';url.setAttribute('aria-label',c.name+' resource URL');const error=node('small');
  const add=button('Add to draft',()=>{const valid=safeLink(url.value);if(!name.value.trim()||!valid){error.textContent='Add a name and an HTTPS link without embedded credentials.';return;}d.links.push({label:name.value.trim(),url:valid});links();changed(d,status);form.hidden=true;name.value='';url.value='';error.textContent='';});form.addEventListener('submit',e=>{e.preventDefault();add.click()});form.append(name,url,error,add);
  const resourceHead=node('div',undefined,'resource-header');resourceHead.append(node('span','Materials for this lesson','field-label'),button('+ Link',()=>{form.hidden=!form.hidden;if(!form.hidden)name.focus()}));
  const slides=button('Open teacher Slides ↗',()=>{});slides.disabled=true;slides.className='slides-link';slides.title='The actual teacher /edit link will come from the authenticated lesson service.';const sourceNote=node('p','Teacher deck opens here when the dated lesson is connected. Student /copy links stay separate.','hint');const planLabel=node('label','Plan for this date','field-label'),plan=node('textarea',undefined,'plan-text');plan.id='plan-'+i;planLabel.htmlFor=plan.id;plan.value=d.plan;plan.placeholder='Awaiting this date’s Agenda plan. Draft an unsynced change here…';plan.addEventListener('input',()=>{d.plan=plan.value;changed(d,status)});const state=node('div',undefined,'card-status');state.append(status,button('Review draft',()=>reviewDraft(c,d)),save);lesson.append(slides,sourceNote,title,planLabel,plan,label,notes,resourceHead,resources,form,state);card.append(lesson);if(d.sourceRevision||d.incomingSource)lesson.append(node('p',d.incomingSource?'Incoming source waiting for review · your draft is preserved':'Draft based on '+d.sourceRevision+' · not saved','hint'));
  const checks=node('details',undefined,'class-checks'),ops=relevant(c);checks.append(node('summary',ops.length ? 'Synthetic check examples · not live tasks' : 'No checks supplied · not checked'));const list=node('ul');for(const kind of Object.keys(KINDS)){const o=ops.find(o=>o.kind===kind);list.append(node('li',`${KINDS[kind]} · ${o?displayStatus(o):'Not checked'}`));}checks.append(list);card.append(checks);$('classes').append(card);
 });renderOperations();
}
function reviewDraft(c,d){
 const data=describeDraft(d),box=node('div',undefined,'draft-comparison');
 function pane(title,fields){const panel=node('section');panel.append(node('h3',title));for(const [name,value]of fields){panel.append(node('h4',name),node('pre',value||'Not supplied','review-text'));}return panel;}
 box.append(pane('Your unsynced draft',[['Lesson title',data.title],['Plan for this date',data.plan],['Teaching notes',data.notes]]));
 if(data.incoming)box.append(pane('Incoming fictional source',[['Source revision',data.incoming.revision],['Observed at',data.incoming.observedAt],['Lesson title',data.incoming.title],['Source notes',data.incoming.notes]]));
 const meta=node('p',data.sourceRevision?'Draft based on source revision '+data.sourceRevision+'; observed '+data.sourceObservedAt:'No source revision is attached to this draft.','hint');
 const note=node('p',data.incoming?'Your edits are preserved. Incoming source text is shown separately and has not replaced them. This is not a saved Agenda revision.':'This is your current in-tab draft, not a saved Agenda revision. Reloading will lose it.');
 openDetail(c.name+' · Review draft',[note,meta,box,button('Keep my draft',()=>$('detail').close())]);
}
function selectDate(value){if(!/^\d{4}-\d{2}-\d{2}$/.test(value)||!Number.isFinite(dayDate(value).getTime()))return;selected=value;render();}
function openDetail(title,contents){$('detail-title').textContent=title;$('detail-body').replaceChildren(...contents);$('detail').showModal();}
function showInstructions(o){const intro=node('p','Manual handoff template. Approved instruction retrieval and the canonical record link are not connected yet. These fictional preview IDs must not be used for a live run.');const area=node('textarea');area.readOnly=true;area.value=manualInstructions(o);area.setAttribute('aria-label','Manual workflow instructions');const notice=node('p',''),copy=button('Copy instructions',async()=>{try{await navigator.clipboard.writeText(area.value);notice.textContent='Copied manual handoff template.';}catch{area.focus();area.select();notice.textContent='Clipboard unavailable. Copy the selected text manually.';}},'primary');openDetail(KINDS[o.kind], [intro,area,copy,notice]);}
function renderOperations(){const out=$('operation-list');out.replaceChildren();const ops=classes.flatMap(relevant);if(!snapshot){out.append(node('p','No valid operations snapshot loaded. Run and saving remain unavailable.','empty'));return;}
 if(!ops.length){out.append(node('p','No checks supplied for this week. Nothing has been queued or verified.','empty'));return;}
 for(const o of ops){const card=node('article',undefined,'operation');card.append(node('h3',KINDS[o.kind]),node('span',displayStatus(o),'badge'),node('p',`${classes.find(c=>c.key===o.scope.class_key)?.name??o.scope.class_key} · Preview data`),node('p',o.last_checked_at?'Last check: '+new Date(o.last_checked_at).toLocaleString():'Last check: Not checked','hint'),node('p','Evidence level: '+o.verification.replaceAll('_',' '),'hint'));
 card.append(node('p',o.next_action?.label??'No next action supplied.'));
 const evidence=button(`Evidence (${o.evidence.length})`,()=>{const rows=o.evidence.flatMap(e=>[node('h3',e.kind),node('p',e.summary),node('p','Receipt reference: '+e.reference),node('p','Observed: '+e.observed_at)]);openDetail('Supporting evidence',[...rows,node('p','Private evidence navigation is not connected. No destination URL has been inferred.')]);});evidence.disabled=!o.evidence.length;
 const run=button('Run',()=>{});run.disabled=true;run.title='Authenticated execution and shared run claims have not been verified.';const actions=node('div',undefined,'action-row');actions.append(button('Copy instructions',()=>showInstructions(o)),run,evidence);card.append(actions,node('span','Execution not connected','hint'));out.append(card);}
}
$('prev').addEventListener('click',()=>{const d=dayDate(selected);d.setDate(d.getDate()-7);selectDate(dateKey(d))});$('next').addEventListener('click',()=>{const d=dayDate(selected);d.setDate(d.getDate()+7);selectDate(dateKey(d))});$('date').addEventListener('change',e=>selectDate(e.target.value));$('close-detail').addEventListener('click',()=>$('detail').close());
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
render();
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

// Explicit fictional-draft staging; never overwrite an existing unsynced draft.
document.addEventListener('workspace-preview-draft',event=>{
 const lesson=event.detail,c=classes.find(c=>c.key===lesson.class_key);
 if(!c||!lesson.lesson_key?.startsWith('lesson:synthetic:'))return;
 selectDate(lesson.teaching_date);const d=draft(c);
 const result=stagePreviewNotes(d,lesson);dirty=true;render();
 if(result==='review_needed'){reviewDraft(c,d);return;}
 $('draft-status').textContent='Fictional source notes staged as unsynced edits. Nothing was saved to Agenda. Reloading loses this draft.';
 location.hash='agenda';
});
