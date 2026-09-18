import {createRecordsLoader,renderRecords,renderSummaries,sectionOptions} from './grading-records.mjs';
import {safeLink} from './contract.mjs';
import {showRunInstructions} from './browser-runs.mjs';
import {buildPostingReview,renderPostingReview} from './grade-posting.mjs';
import {createCompanionClient,companionMessage} from './browser-companion.mjs';
import {mountControlPanel} from './control-panel.mjs';
import {mountSubmissionMonitor} from './submission-monitor.mjs';
import {mountAssessmentReview} from './assessment-review.mjs';
const $=id=>document.getElementById(id);
let submissionMonitor,assessmentReview;
const allowed=['control','agenda','feedback','summary','research'];
const researchURL='https://github.com/kvond/answerable-skills/blob/a9a8fc1992c6564dac68d818f3597f11671f9667/skills/formative-pipeline-v2/SKILL.md';
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function btn(text,fn,disabled=false){const n=el('button',text);n.type='button';n.disabled=disabled;if(fn)n.addEventListener('click',fn);if(disabled)n.title='Requires authenticated HTTPS, authorization, and verified end-to-end execution for this action.';return n;}
function link(text,url){const a=el('a',text,'source-link');a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;}
function heading(target,title,description){const div=el('div',undefined,'page-heading');div.append(el('p','TEACHER WORKSPACE · PREVIEW','eyebrow'),el('h1',title),el('p',description));target.append(div);}
function panel(title,paragraph,wide=false){const p=el('section',undefined,'surface'+(wide?' wide':''));p.append(el('h2',title),el('p',paragraph));return p;}
function actions(p,...items){const d=el('div',undefined,'action-row');d.append(...items);p.append(d);}
function empty(p,title,description){const d=el('div',undefined,'document-empty');d.append(el('strong',title),el('span',description));p.append(d);}
function metrics(p,labels){const d=el('div',undefined,'metrics');for(const l of labels){const m=el('div',undefined,'metric');m.append(el('strong','—'),el('span',l));d.append(m);}p.append(d);}
function steps(p,labels){const list=el('ol',undefined,'workflow-steps');for(const text of labels)list.append(el('li',text));p.append(list);}

let promptSourcePromise;
function agendaPrompts(refresh=false){
 if(!promptSourcePromise||refresh)promptSourcePromise=fetch('/api/agenda-prompts',{cache:'no-store'}).then(async r=>{
  if(!r.ok)throw Error('Agenda prompts unavailable');const d=await r.json();
  if(d.schema_version!=='hub-agenda-prompts/v1'||d.status!=='source_read'||d.external_writes!==0)throw Error();
  for(const k of ['completion','feedback','summary'])if(typeof d.prompts?.[k]!=='string'||!d.prompts[k]||!safeLink(d.links?.[k])||!d.links[k].startsWith('https://docs.google.com/spreadsheets/'))throw Error();
  return d;
 });return promptSourcePromise;
}
async function copyPrompt(kind){const titles={completion:'Completion check',feedback:'Student feedback',summary:'Teacher class summary'};$('detail-title').textContent=titles[kind];const body=$('detail-body');body.replaceChildren(el('p','Loading your supplied instructions…'));$('detail').showModal();try{const r=await fetch('./teacher-prompts.json',{cache:'no-store'});if(!r.ok)throw Error();const p=await r.json();const live=await agendaPrompts(true);const original=live.prompts[kind];const lesson=scope.data?.lessons.find(l=>l.class_key===scope.classKey);const context=lesson?'Selected lesson context (Agenda read-only):\nClass: '+classes[scope.classKey]+'\nDate: '+scope.date+'\nTopic: '+lesson.title+'\nTeacher Slides: '+(lesson.teacher_slides||'Not supplied')+'\nTurn-in folder: '+(lesson.turnin_folder||'Not supplied')+'\nStudent evidence: supply privately; the dashboard has not collected it.\n\n':'';const workflow=kind==='completion'?p.workflows?.due_completion?.instructions:'';if(kind==='completion'&&typeof workflow!=='string')throw Error();const text=typeof original==='string'?context+(workflow||'')+original:original;if(typeof text!=='string')throw Error();const area=el('textarea');area.readOnly=true;area.value=text;area.setAttribute('aria-label',titles[kind]+' instructions');const status=el('p','','hint'),copy=btn('Copy instructions',async()=>{try{await navigator.clipboard.writeText(text);status.textContent='Copied instructions.';}catch{area.focus();area.select();status.textContent='Use your browser’s copy command on the selected text.';}});body.replaceChildren(el('p',`${live.source}. ${live.change_rule} ${live.alignment_review_needed?"Agenda prompts differ from the packaged version: review the matching skills and Agenda pages before execution.":""} This is not a running job.`),area,copy,status,el('p','Agenda prompt fingerprint: '+live.sha256[kind],'hint'));}catch{body.replaceChildren(el('p','Agenda instructions could not be loaded. Retry or open the Agenda prompt link. Nothing was executed.','error'));}}

const classes={biology:'Biology',forensics:'Forensics',anatomy:'Anatomy'};
let scope={date:$('date').value,classKey:'biology',data:null,loading:false,error:''};
let recordState={status:'empty'},sectionId='matched',sectionSelect;
const views=[];
function scopeBar(target,page){
 const bar=el('div',undefined,'workspace-controls');
 const classLabel=el('label','Class'),select=el('select');select.setAttribute('aria-label',page+' class');
 for(const [key,name]of Object.entries(classes)){const option=el('option',name);option.value=key;select.append(option);}select.value=scope.classKey;
 select.addEventListener('change',()=>{scope.classKey=select.value;sectionId='matched';renderScope();loadResults()});classLabel.append(select);
 const dateLabel=el('label','Lesson date'),date=el('input');date.type='date';date.value=scope.date;date.setAttribute('aria-label',page+' date');dateLabel.append(date);
 date.addEventListener('change',()=>requestDay(date.value));
 const load=btn('Load lesson',()=>requestDay(date.value));bar.append(classLabel);
 if(page==='Feedback'){
  const sectionLabel=el('label','Section');sectionSelect=el('select');sectionSelect.setAttribute('aria-label','Feedback section');sectionSelect.append(el('option','Matched sections'));sectionSelect.firstChild.value='matched';
  sectionSelect.addEventListener('change',()=>{sectionId=sectionSelect.value;renderPrivateRecords()});sectionLabel.append(sectionSelect);bar.append(sectionLabel);
 }
 bar.append(dateLabel,load);
 const context=el('section',undefined,'lesson-context');const notice=el('p','','hint');notice.setAttribute('role','status');
 target.append(bar,context,notice);views.push({select,date,load,context,notice});
}
function requestDay(date){document.dispatchEvent(new CustomEvent('agenda-request-day',{detail:{date}}));}
function renderScope(){
 const lesson=scope.data?.lessons.find(l=>l.class_key===scope.classKey);
 const monitorReady=Boolean(scope.data&&!scope.loading&&!scope.error&&lesson?.turnin_folder);
 submissionMonitor?.update({date:scope.date,classKey:scope.classKey,ready:monitorReady});
 assessmentReview?.update({date:scope.date,classKey:scope.classKey,ready:monitorReady});
 for(const v of views){
  v.select.value=scope.classKey;v.date.value=scope.date;v.load.disabled=scope.loading;
  v.context.replaceChildren();v.context.append(el('strong',lesson?.title||'Choose a date and load its lesson'));
  if(lesson){
   const links=el('div',undefined,'action-row');
   if(lesson.teacher_slides&&safeLink(lesson.teacher_slides))links.append(link('Teacher Slides ↗',lesson.teacher_slides));
   if(lesson.turnin_folder&&safeLink(lesson.turnin_folder))links.append(link('Agenda turn-in folder ↗',lesson.turnin_folder));
   const folder=lesson.links.find(l=>l.label==='Lesson folder');if(folder&&safeLink(folder.url))links.append(link('Lesson materials ↗',folder.url));
   v.context.append(links);
   if(lesson.assignment_label)v.context.append(el('p',lesson.assignment_label,'hint'));
   if(!lesson.turnin_folder)v.context.append(el('p','Turn-in folder mapping unavailable for this lesson.','hint'));
  }
  v.notice.textContent=scope.loading?'Loading lesson…':scope.error||(scope.data?'Agenda read '+new Date(scope.data.checked_at).toLocaleString()+'. Assessed results are checked separately below.':'Lesson source not loaded.');
 }
}
document.addEventListener('agenda-day-loading',e=>{scope.date=e.detail.date;scope.loading=true;scope.error='';if(scope.data?.date!==scope.date)scope.data=null;renderScope();loadResults()});
document.addEventListener('agenda-day-loaded',e=>{scope.data=e.detail;scope.date=e.detail.date;scope.loading=false;scope.error='';renderScope();loadResults()});
document.addEventListener('agenda-day-failed',()=>{scope.loading=false;scope.error=scope.data?'Refresh failed. The earlier lesson read may be out of date.':'Lesson could not be loaded. Check Agenda access on page 1.';renderScope();loadResults()});
function compactHeading(target,title){const row=el('div',undefined,'workspace-title');row.append(el('h1',title));target.append(row);}
function promptAction(target,label,kind){
 target.append(btn(label,()=>copyPrompt(kind)));
 const a=el('a','Open '+({completion:'completion',feedback:'feedback',summary:'summary'}[kind])+' prompt in Agenda ↗','source-link');a.hidden=true;a.target='_blank';a.rel='noopener noreferrer';target.append(a);
 agendaPrompts().then(d=>{a.href=d.links[kind];a.hidden=false;a.title=d.change_rule;}).catch(()=>{a.replaceWith(el('span','Agenda prompt link unavailable','hint'));});
}
function heldAction(target,label,reason){const button=btn(label,null,true);button.title=reason;target.append(button);}
const feedback=$('page-feedback');compactHeading(feedback,'Submissions & feedback');scopeBar(feedback,'Feedback');
feedback.append(el('p','Submissions include all recorded assignments for this class. Lesson date selects the Agenda links and folder check.','hint'));
const feedbackGrid=el('div',undefined,'section-grid');
const queue=el('section',undefined,'surface wide');const qhead=el('div',undefined,'section-heading');qhead.append(el('h2','Student work'));promptAction(qhead,'Copy completion instructions','completion');queue.append(qhead);
const inventory=el('div');inventory.setAttribute('aria-live','polite');
const scan=btn('Check lesson folder',()=>scanSubmissions());
let scanGeneration=0,scanController;
function clearInventory(){scanGeneration++;scanController?.abort();inventory.replaceChildren();scan.disabled=!scope.data||scope.loading||!!scope.error;}
async function scanSubmissions(){
 const generation=++scanGeneration,day=scope.date,classKey=scope.classKey;
 scanController?.abort();scanController=new AbortController();scan.disabled=true;
 inventory.replaceChildren(el('p','Checking the lesson folder and nested uploads…'));
 try{
  const r=await fetch('/api/submission-inventory?'+new URLSearchParams({date:day,class_key:classKey}),{cache:'no-store',credentials:'same-origin',redirect:'error',signal:scanController.signal});
  const d=await r.json();if(generation!==scanGeneration)return;
  const messages={submission_folder_access_required:'The cloud service cannot read this folder yet. Share the active turn-in folder with the dashboard service account as Viewer, then retry.',submission_lesson_mapping_required:'The Agenda needs a specific lesson turn-in folder. Its current mapping is unavailable or points to a whole course.',submission_scan_incomplete:'The folder check was incomplete. No student conclusions were made.',submission_agenda_changed:'The Agenda changed during the check. Load the lesson again.',submission_folder_inactive:'The mapped folder is inactive. Its Agenda link needs repair.'};
  if(!r.ok)throw Error(messages[d.error]||'The submission source could not be checked. No work was assessed.');
  if(d.schema_version!=='hub-submission-inventory/v1'||d.date!==day||d.class_key!==classKey||!Array.isArray(d.files)||d.external_writes!==0)throw Error('Submission scope could not be verified.');
  inventory.replaceChildren(el('h3',d.folder_name),el('p',`${d.files.length} visible files · ${d.folders_checked} folders checked · ${new Date(d.checked_at).toLocaleString()}`));
  if(safeLink(d.folder_url))inventory.append(link('Open turn-in folder ↗',d.folder_url));
  for(const file of d.files){const row=el('p');if(safeLink(file.url))row.append(link(file.name,file.url));inventory.append(row);}
  inventory.append(el('p','File inventory only. Student matching, due dates, completion scores and feedback are not verified. An empty folder does not establish missing work.','hint'));
 }catch(e){if(generation===scanGeneration&&e.name!=='AbortError')inventory.replaceChildren(el('p',e.message,'error'));}
 finally{if(generation===scanGeneration)scan.disabled=false;}
}
const feedbackResults=el('div');feedbackResults.setAttribute('aria-live','polite');queue.append(feedbackResults);
const reviewBox=el('div',undefined,'assessment-review');
const monitorBox=el('div',undefined,'submission-monitor');submissionMonitor=mountSubmissionMonitor(monitorBox,{onFiles:list=>assessmentReview?.setFiles(list)});queue.append(monitorBox);
assessmentReview=mountAssessmentReview(reviewBox,{records:()=>recordState,onSaved:()=>retrieveRecords()});queue.append(reviewBox);
const inventoryDetails=el('details',undefined,'workflow-details');inventoryDetails.append(el('summary','Lesson folder inventory'),scan,inventory);queue.append(inventoryDetails);
const reports=el('section',undefined,'surface');reports.append(el('h2','Feedback actions'),el('p','Open each student’s report from the list above.'));const reportActions=el('div',undefined,'action-row');promptAction(reportActions,'Copy feedback instructions','feedback');heldAction(reportActions,'Print feedback documents','Needs current feedback documents, a verified school printer, and your direction.');reports.append(reportActions);
async function reviewGrades(){
 const selection={classKey:scope.classKey,sectionId};
 const modal=$('detail'),body=el('div');
 $('detail-title').textContent='Review grades for Infinite Campus';
 body.append(el('p','Refreshing the recorded grades…'));
 $('detail-body').replaceChildren(body);modal.showModal();
 try{
  const response=await fetch('/api/grading-records',{cache:'no-store',credentials:'same-origin',redirect:'error'});
  if(!response.ok)throw Error('The grading workbook could not be refreshed. Close this review and try again.');
  const review=buildPostingReview(await response.json(),selection);
  if(!body.isConnected||!modal.open)return;
  if(scope.classKey!==selection.classKey||sectionId!==selection.sectionId)throw Error('The selected class or section changed. Open a new grade review.');
  renderPostingReview(body,review);
  body.append(el('p','Open the matching assignment’s Score panel in Infinite Campus in this same school browser. The companion checks the current assignment and asks you to confirm there before posting.','hint'));
  const state=el('p','Checking the school browser companion…','hint');state.setAttribute('role','status');body.append(state);
  const companion=createCompanionClient();
  modal.addEventListener('close',()=>companion.dispose(),{once:true});
  const available=await companion.status();if(!body.isConnected||!modal.open){companion.dispose();return;}
  state.textContent=companionMessage(available);
  const start=btn('Review & post in school browser',async()=>{
   start.disabled=true;state.textContent='Refreshing the recorded grades before the Infinite Campus review…';
   try{
    if(scope.classKey!==selection.classKey||sectionId!==selection.sectionId)throw Error('The selection changed. Open a new review.');
    const latest=await fetch('/api/grading-records',{cache:'no-store',credentials:'same-origin',redirect:'error'});
    if(!latest.ok)throw Error('The grading workbook could not be refreshed. Nothing was sent.');
    const fresh=buildPostingReview(await latest.json(),selection);
    if(fresh.source_revision!==review.source_revision)throw Error('The workbook changed. Open a new review.');
    const result=await companion.prepare(fresh,{onProgress:reply=>{state.textContent=companionMessage(reply);}});
    state.textContent=companionMessage(result);
   }catch(error){state.textContent=error.message;}
  },available.code!=='companion_available');
  body.append(start);
 }catch(error){if(body.isConnected&&modal.open)body.replaceChildren(el('p',error.message,'error'));}
}
const delivery=el('section',undefined,'surface');delivery.append(el('h2','Grades & parent contact'),el('p','Post recorded grades through your signed-in school browser. Existing matching grades are skipped.'));const deliveryActions=el('div',undefined,'action-row');deliveryActions.append(btn('Post grades to Infinite Campus',()=>reviewGrades()),btn('Manual completion grading',()=>showRunInstructions('grading')));heldAction(deliveryActions,'Review overdue emails','Needs verified due dates, cumulative submissions, and parent contacts from Infinite Campus.');delivery.append(deliveryActions);const details=el('details',undefined,'workflow-details');details.append(el('summary','What is needed to enable these actions'),el('p','Grade posting uses the School Workflow Hub browser companion in the same school Chrome or Edge profile on this computer. It verifies the open assignment, preserves existing grades and checks the saved result. Manual completion grading copies a separate grading request. Parent notices need verified non-submission 14 days after the due date and current parent contacts.'));delivery.append(details);
feedbackGrid.append(queue,reports,delivery);feedback.append(feedbackGrid);
const summary=$('page-summary');compactHeading(summary,'Teacher summary');summary.append(el('p','One teaching summary for each class, using the reviewed submission records.','hint'));
const summaryActions=el('div',undefined,'action-row summary-actions');promptAction(summaryActions,'Copy class-summary instructions','summary');summary.append(summaryActions);
const summaryResults=el('div',undefined,'section-grid');summaryResults.setAttribute('aria-live','polite');summary.append(summaryResults);
function renderPrivateRecords(){
 if(sectionSelect){const options=recordState.status==='loaded'?sectionOptions(recordState.data,scope.classKey):[];sectionSelect.replaceChildren();for(const [id,name]of [['matched','Matched sections'],['all','All records'],...options]){const option=el('option',name);option.value=id;sectionSelect.append(option);}if(recordState.status==='loaded'&&!['matched','all'].includes(sectionId)&&!options.some(([id])=>id===sectionId))sectionId='matched';sectionSelect.value=sectionId;sectionSelect.disabled=recordState.status!=='loaded';}
 renderRecords(feedbackResults,recordState,{classKey:scope.classKey,sectionId});renderSummaries(summaryResults,recordState);
}
const retrieveRecords=createRecordsLoader({onState:state=>{recordState=state;renderPrivateRecords()}});
qhead.append(btn('Refresh submissions',()=>retrieveRecords()));summaryActions.append(btn('Refresh summaries',()=>retrieveRecords()));
function loadResults(){clearInventory();renderPrivateRecords();}
renderScope();loadResults();retrieveRecords();
const research=$('page-research');heading(research,'Research workspace','Keep research evidence separate from student-facing feedback. Reuse versioned instructions and the same private workflow records.');const rg=el('div',undefined,'section-grid');const instructions=panel('Shared research instructions','A relevant skill was located in your answerable-skills repository. Its referenced codebook and current applicability still need review before research execution is enabled.');instructions.append(link('Open versioned research workflow ↗',researchURL),el('p','Pinned repository version · access requires your repository permissions. This is an instruction source, not the student-data store.','hint'));actions(instructions,btn('Run research workflow',null,true));const evidence=panel('Evidence & coding record','Research observations must link back to approved private evidence and the applied instruction version. Unknown response timing remains unknown.');metrics(evidence,['Accepted observations','Needs review','Verified evidence links']);empty(evidence,'Research data not connected','There is no student research data in this preview or its source files.');const history=panel('Provenance & review history','The shared backend holds the persistent record. This page will display its accepted results and unresolved reviews, without creating a second store.',true);history.append(el('p','Instruction/codebook acceptance: Pending · Authenticated data access: Not verified · Last run: Not checked','readiness'));rg.append(instructions,evidence,history);research.append(rg);
mountControlPanel($('page-control'));

function navigate(){const requested=location.hash.slice(1);const page=allowed.includes(requested)?requested:'control';for(const id of allowed)$('page-'+id).hidden=id!==page;for(const a of document.querySelectorAll('[data-page]')){if(a.dataset.page===page)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');}document.title=`${{control:'Control panel',agenda:'Teaching Agenda',feedback:'Submissions & feedback',summary:'Teacher summary',research:'Research'}[page]} · Agenda`;}
window.addEventListener('hashchange',navigate);navigate();
