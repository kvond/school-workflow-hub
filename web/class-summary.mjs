// Class growth summary: one lesson, one class section, on its own page
// (teacher decision, September 23, 2026). Counts, the understanding table,
// the teacher checks and the one-row-per-student table come from saved
// records. The three written sections come from the saved summary text that
// the "Write class summary" button drafts and the teacher reviews and saves.
import {createRecordsLoader,SCALE,RUBRIC_KEY,aspectKey,understandingItems} from './grading-records.mjs';
const COURSES={biology:'Biology',forensics:'Forensics',anatomy:'Anatomy & Physiology'};
export const SUMMARY_HEADINGS=['An idea worth chasing',"Recurring ideas in students' words",'A possible next instructional contrast','Check these yourself'];
const UNAVAILABLE='Evidence unavailable';
export function summarySections(text){
 const out={};let current=null;
 for(const raw of String(text||'').split(/\r?\n/)){
  const bare=raw.replace(/^[\s#*_]+|[\s*_:]+$/g,'').replace(/[’]/g,"'");
  const hit=SUMMARY_HEADINGS.find(h=>h.toLowerCase()===bare.toLowerCase());
  if(hit){current=hit;out[hit]=out[hit]||[];continue;}
  if(current)out[current].push(raw);
 }
 const result={};for(const [k,v] of Object.entries(out)){const t=v.join('\n').trim();if(t)result[k]=t;}
 return result;
}
export function studentRows(data,{classKey,sectionId,assignmentKey}){
 const best=new Map();
 for(const r of data?.submissions||[]){
  if(r.class_key!==classKey||r.section_id!==sectionId||r.assignment_key!==assignmentKey||!r.included_in_counts||r.discovery_only)continue;
  const key=r.count_key||r.record_id,prev=best.get(key);
  if(!prev||String(r.checked_at||'')>String(prev.checked_at||''))best.set(key,r);
 }
 return [...best.values()].sort((a,b)=>String(a.display_name||'').localeCompare(String(b.display_name||'')));
}
const CATEGORY={notes:'Notes',bank:'Concept Bank',drafts:'Revisions'};
export function completionState(row){
 const due=row.assessment_stage==='initial'?['notes']:['notes','bank','drafts'];let known=false;const short=[];
 for(const c of due){const req=row[c+'_required'],done=row[c+'_completed'];if(req===undefined||req===null||req==='')continue;known=true;
  if(Number(req)>0&&Number(done)<Number(req))short.push(CATEGORY[c]+' '+Number(done||0)+'/'+Number(req));}
 return known?{state:short.length?'Incomplete':'Complete',detail:short.join(', ')}:{state:'Completion unknown',detail:''};
}
function itemKey(item){return aspectKey(item.label)||item.label;}
export function aspectColumns(rows){
 const columns=new Map();
 for(const r of rows)for(const item of understandingItems(r)){const k=itemKey(item);if(!columns.has(k))columns.set(k,item.label);}
 return [...columns].map(([key,label])=>({key,label}));
}
export function levelTable(rows,columns){
 const levels=[...SCALE];if(rows.some(r=>understandingItems(r).some(i=>i.level==='Almost there')))levels.push('Almost there');levels.push(UNAVAILABLE);
 return {levels,rows:columns.map(col=>{const counts=Object.fromEntries(levels.map(l=>[l,0]));
  for(const r of rows){const item=understandingItems(r).find(i=>itemKey(i)===col.key);const level=item&&levels.includes(item.level)?item.level:UNAVAILABLE;counts[level]++;}
  return {label:col.label,counts};})};
}
export function teacherChecks(rows){
 const checks=[];
 for(const r of rows){const who=r.display_name||'Name unavailable';
  if(r.hold_reason)checks.push(who+': '+r.hold_reason);
  const first=new Map((r.answers||[]).filter(a=>a.stage==='NOTES').map(a=>[a.question_id,a]));
  for(const a of r.answers||[]){
   if(a.evidence_note)checks.push(who+': '+(a.critical_aspect||a.question_id)+' ('+({NOTES:'first answer',DRAFT:'revised answer',BANK:'Concept Bank'}[a.stage]||'answer')+'): '+a.evidence_note);
   if(a.stage==='DRAFT'&&!String(a.response||'').trim()&&String(first.get(a.question_id)?.response||'').trim())checks.push(who+': the revised answer for '+(a.critical_aspect||a.question_id)+' is blank; the rating comes from the first answer.');
  }}
 return checks;
}
function levelClass(level){return 'fb-level-'+String(level||'unavailable').toLowerCase().replace(/[^a-z]+/g,'-');}
function make(doc,tag,text,cls){const n=doc.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.className=cls;return n;}
function para(doc,parent,text,cls){const p=make(doc,'p',text,cls?'cs-text '+cls:'cs-text');parent.append(p);return p;}
function badge(doc,level){return make(doc,'span',level||UNAVAILABLE,'fb-badge '+levelClass(level));}
function table(doc,headers,body){const t=make(doc,'table',undefined,'cs-table');const head=make(doc,'thead'),hr=make(doc,'tr');for(const h of headers)hr.append(make(doc,'th',h));head.append(hr);const tb=make(doc,'tbody');for(const cells of body){const tr=make(doc,'tr');for(const c of cells){const td=make(doc,'td');if(c&&typeof c==='object'&&'tagName' in c)td.append(c);else td.textContent=String(c??'');tr.append(td);}tb.append(tr);}t.append(head,tb);return t;}
export function renderClassSummary(root,data,scope,{saved=null,controls=null}={}){
 const doc=root.ownerDocument;root.replaceChildren();
 if(controls)root.append(controls);
 const rows=studentRows(data,scope),columns=aspectColumns(rows),written=summarySections(saved?.summary);
 const lesson=(data?.assignments||[]).find(a=>a.assignment_key===scope.assignmentKey)?.assignment_title||'Lesson';
 const section=rows.find(r=>r.section_name)?.section_name||saved?.section_name||'Section';
 const one=make(doc,'div',undefined,'cs-sheet');root.append(one);
 one.append(make(doc,'p','Class growth summary','cs-kicker'),make(doc,'h1',lesson));
 para(doc,one,['Teacher copy',(COURSES[scope.classKey]||scope.classKey)+' · '+section,rows.length+(rows.length===1?' student':' students'),'One lesson'].join(' • '),'cs-subtitle');
 if(!rows.length){para(doc,one,'No saved, matched work for this lesson and section yet. Assessed and matched submissions appear here after they are saved.');return root;}
 const glance=make(doc,'section',undefined,'cs-section');glance.append(make(doc,'h2','Teacher notes at a glance'));
 const states=rows.map(completionState),count=s=>states.filter(x=>x.state===s).length;
 const stats=make(doc,'div',undefined,'cs-stats');
 for(const [value,label] of [[rows.length,'Students with saved work'],[count('Complete'),'Complete'],[count('Incomplete'),'Incomplete'],[count('Completion unknown'),'Completion unknown']]){const box=make(doc,'div',undefined,'cs-stat');box.append(make(doc,'strong',value),make(doc,'span',label));stats.append(box);}
 glance.append(stats);
 para(doc,glance,'Expected enrollment is not verified on this page, so missing work is not counted here. Completion is reported separately from understanding.','cs-note');
 if(written['An idea worth chasing']){const idea=para(doc,glance,'');idea.append(make(doc,'strong','An idea worth chasing: '),make(doc,'span',written['An idea worth chasing']));}
 one.append(glance);
 const current=make(doc,'section',undefined,'cs-section');current.append(make(doc,'h2','Current understanding across the class'));
 if(columns.length){const lt=levelTable(rows,columns);current.append(table(doc,['Critical aspect',...lt.levels],lt.rows.map(r=>[r.label,...lt.levels.map(l=>r.counts[l])])));
  para(doc,current,'Each row totals '+rows.length+(rows.length===1?' student':' students')+'. Ratings come from each student’s revised answer when one exists.','cs-note');}
 else para(doc,current,'No saved ratings for this lesson yet.','cs-note');
 one.append(current);
 const recurring=make(doc,'section',undefined,'cs-section');recurring.append(make(doc,'h2',"Recurring ideas in students' words"));
 para(doc,recurring,written["Recurring ideas in students' words"]||'Not written yet. Use Write class summary to draft this section from the saved answers.',written["Recurring ideas in students' words"]?'':'cs-note');
 one.append(recurring);
 const two=make(doc,'div',undefined,'cs-sheet cs-sheet-two');root.append(two);
 two.append(make(doc,'h2','From evidence to a teaching decision','cs-big'));
 const contrast=make(doc,'section',undefined,'cs-section cs-contrast');contrast.append(make(doc,'h3','A possible next instructional contrast'),make(doc,'p','Suggested teaching move—not a scheduled lesson','cs-note'));
 para(doc,contrast,written['A possible next instructional contrast']||'Not written yet.',written['A possible next instructional contrast']?'':'cs-note');two.append(contrast);
 const checks=make(doc,'section',undefined,'cs-section');checks.append(make(doc,'h2','Check these yourself'));
 const list=[...teacherChecks(rows),...String(written['Check these yourself']||'').split('\n').map(s=>s.trim()).filter(Boolean)];
 if(list.length){const ul=make(doc,'ul',undefined,'cs-checks');for(const c of list)ul.append(make(doc,'li',c));checks.append(ul);}else para(doc,checks,'Nothing flagged in the saved records.','cs-note');
 two.append(checks);
 const every=make(doc,'section',undefined,'cs-section');every.append(make(doc,'h2','Every student, one row each'));
 every.append(table(doc,['Student','Completion',...columns.map(c=>c.label),'Evidence or review note'],rows.map((r,i)=>{const items=understandingItems(r);const note=r.hold_reason||states[i].detail||((r.answers||[]).some(a=>a.stage==='DRAFT')?'Revised answers':'First answers only');
  return [r.display_name||'Name unavailable',states[i].state+(states[i].detail?' — '+states[i].detail:''),...columns.map(c=>{const it=items.find(x=>itemKey(x)===c.key);return badge(doc,it?.level);}),note];})));
 const legend=make(doc,'div',undefined,'cs-legend');for(const [level] of RUBRIC_KEY)legend.append(badge(doc,level));legend.append(badge(doc,UNAVAILABLE));every.append(legend);
 para(doc,every,'Completion is the gradebook measure. Ratings describe understanding of each critical aspect here and in the individual student reports. This summary changes no grades.','cs-note');
 two.append(every);
 if(saved?.checked_at)para(doc,two,'Written sections saved '+new Date(saved.checked_at).toLocaleDateString(undefined,{dateStyle:'long'})+'.','cs-note');
 return root;
}
function requestId(){return (globalThis.crypto?.randomUUID?.()||String(Date.now())+Math.random()).replace(/[^a-z0-9]/gi,'').slice(0,32);}
async function post(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),credentials:'same-origin',cache:'no-store'});let data={};try{data=await r.json();}catch{}if(!r.ok)throw Error(data.error||'request_failed');return data;}
const MESSAGES={provider_not_configured:'The assessment model is not connected.',no_saved_work_for_summary:'There is no saved, matched work for this lesson and section yet.',summary_lesson_column_required:'The grading workbook’s Class summaries tab needs an assignment_key column before lesson summaries can be saved.',summary_draft_invalid:'The draft did not follow the summary headings. Try again.',save_origin_required:'Open this page from the dashboard to write or save.'};
function boot(){
 const root=document.getElementById('class-summary');if(!root)return;
 const params=new URLSearchParams(location.search),scope={classKey:params.get('class')||'',sectionId:params.get('section')||'',assignmentKey:params.get('assignment')||''};
 let state={status:'loading'},draft='',busy=false,message='';
 const savedFor=data=>(data?.summaries||[]).find(s=>s.class_key===scope.classKey&&s.section_id===scope.sectionId&&s.assignment_key===scope.assignmentKey)||null;
 const controls=()=>{const bar=make(document,'div',undefined,'cs-controls no-print');const saved=savedFor(state.data);
  const write=make(document,'button',saved?'Rewrite class summary':'Write class summary');write.type='button';write.disabled=busy||state.status!=='loaded';
  write.addEventListener('click',async()=>{if(saved&&!confirm('Write a new draft? This makes one model call. The saved summary stays until you save the new one.'))return;busy=true;message='Writing the draft from the saved answers…';render();
   try{const r=await post('/api/class-summary-draft',{class_key:scope.classKey,section_id:scope.sectionId,assignment_key:scope.assignmentKey});draft=r.draft;message='Draft ready. Read and edit it, then save.';}catch(e){message=MESSAGES[e.message]||'The draft could not be written ('+e.message+').';}busy=false;render();});
  const print=make(document,'button','Print');print.type='button';print.addEventListener('click',()=>window.print());
  bar.append(write,print);if(message)bar.append(make(document,'p',message,'hint'));
  if(draft){const box=make(document,'textarea');box.className='cs-draft';box.value=draft;box.rows=18;box.addEventListener('input',()=>{draft=box.value;});
   const save=make(document,'button','Save summary');save.type='button';save.disabled=busy;
   save.addEventListener('click',async()=>{busy=true;message='Saving…';render();const rows=studentRows(state.data,scope);
    try{await post('/api/assessment-save',{request_id:requestId(),summary:{class_key:scope.classKey,section_id:scope.sectionId,section_name:rows.find(r=>r.section_name)?.section_name||scope.sectionId,assignment_key:scope.assignmentKey,summary:draft,coverage:rows.length+' students with saved, matched work in this lesson and section'}});draft='';message='Saved.';busy=false;await load();return;}
    catch(e){message=MESSAGES[e.message]||'The summary could not be saved ('+e.message+').';}busy=false;render();});
   const discard=make(document,'button','Discard draft');discard.type='button';discard.addEventListener('click',()=>{draft='';message='';render();});
   bar.append(box,save,discard);}
  return bar;};
 const render=()=>{if(state.status!=='loaded'){root.replaceChildren(make(document,'p',state.message||'Loading the class summary…'));return;}
  renderClassSummary(root,state.data,scope,{saved:savedFor(state.data),controls:controls()});};
 const loader=createRecordsLoader({onState:s=>{state=s;render();}});
 const load=()=>loader();
 if(!scope.classKey||!scope.sectionId||!scope.assignmentKey){root.replaceChildren(make(document,'p','Open this page from the Teacher summary page so it knows the class, section and lesson.'));return;}
 load();
}
if(typeof document!=='undefined'&&typeof location!=='undefined')boot();
