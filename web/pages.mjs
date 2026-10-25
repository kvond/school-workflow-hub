import {createRecordsLoader,renderRecords,renderSummaries,verifiedSectionOptions,lessonOptions,filterRecords,printFeedbackPacket,recordsWithDiscovery} from './grading-records.mjs';
import {safeLink} from './contract.mjs';
import {showRunInstructions} from './browser-runs.mjs';
import {buildPostingReview,renderPostingReview} from './grade-posting.mjs';
import {createCompanionClient,companionMessage} from './browser-companion.mjs';
import {mountControlPanel} from './control-panel.mjs';
import {createDiscoveryLoader,createCatalogLoader} from './submission-monitor.mjs';
import {mountAssessmentReview} from './assessment-review.mjs';
import {mountIcGradeSheet} from './ic-grade-sheet.mjs';
const $=id=>document.getElementById(id);
let assessmentReview;
const allowed=['control','agenda','feedback','summary','research'];
const researchURL='https://github.com/kvond/optimized-system/blob/a9a8fc1992c6564dac68d818f3597f11671f9667/skills/formative-pipeline-v2/SKILL.md';
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
async function copyPrompt(kind){const titles={completion:'Completion check',feedback:'Student feedback',summary:'Teacher class summary'};$('detail-title').textContent=titles[kind];const body=$('detail-body');body.replaceChildren(el('p','Loading your supplied instructions…'));$('detail').showModal();try{const r=await fetch('./teacher-prompts.json',{cache:'no-store'});if(!r.ok)throw Error();const p=await r.json();const live=await agendaPrompts(true);const original=live.prompts[kind];const lesson=selectedLesson();const context=lesson?'Selected lesson context:\nClass: '+classes[scope.classKey]+'\nLesson: '+lesson.assignment_title+'\nLesson deck: '+(lesson.source_url||'Not supplied')+'\nTurn-in folder: '+(lesson.destination_folder_url||'Not supplied')+'\nStudent evidence: supply privately; the dashboard has not collected it.\n\n':'';const workflow=kind==='completion'?p.workflows?.due_completion?.instructions:'';if(kind==='completion'&&typeof workflow!=='string')throw Error();const text=typeof original==='string'?context+(workflow||'')+original:original;if(typeof text!=='string')throw Error();const area=el('textarea');area.readOnly=true;area.value=text;area.setAttribute('aria-label',titles[kind]+' instructions');const status=el('p','','hint'),copy=btn('Copy instructions',async()=>{try{await navigator.clipboard.writeText(text);status.textContent='Copied instructions.';}catch{area.focus();area.select();status.textContent='Use your browser’s copy command on the selected text.';}});body.replaceChildren(el('p',`${live.source}. ${live.change_rule} ${live.alignment_review_needed?"Agenda prompts differ from the packaged version: review the matching skills and Agenda pages before execution.":""} This is not a running job.`),area,copy,status,el('p','Agenda prompt fingerprint: '+live.sha256[kind],'hint'));}catch{body.replaceChildren(el('p','Agenda instructions could not be loaded. Retry or open the Agenda prompt link. Nothing was executed.','error'));}}

const classes={biology:'Biology',forensics:'Forensics',anatomy:'Anatomy'};
const selectionStorageKey='hub-page2-selection/v1';
function restoredSelection(){
 try{
  const value=JSON.parse(sessionStorage.getItem(selectionStorageKey));
  if(!classes[value?.classKey]||typeof value.assignmentKey!=='string'||!value.assignmentKey||value.assignmentKey.length>128||typeof value.sectionId!=='string'||value.sectionId.length>128||!['all','initial','final'].includes(value.stage))return null;
  return value;
 }catch{return null;}
}
const restored=restoredSelection();
let scope={date:$('date').value,classKey:restored?.classKey||'biology',assignmentKey:restored?.assignmentKey||'all',stage:restored?.stage||'all',data:null,loading:false,error:''};
let recordState={status:'empty'},sectionId=restored?.sectionId||'',sectionSelectionExplicit=Boolean(restored?.sectionId),sectionSelect,sectionLabel,lessonSelect,workSelect;
let discoveryState={status:'empty'},discoveryLoader,lastAutomaticScope='';
let catalogState={status:'empty'},catalogLoader,lastCatalogClass='';
const discoveredAssignments=new Map();
function rememberAssignments(data,replace=false){
 if(replace)for(const [key,value]of discoveredAssignments)if(value.class_key===data.class_key)discoveredAssignments.delete(key);
 for(const assignment of data.assignments||[])if(assignment.class_key===data.class_key&&assignment.assignment_key)discoveredAssignments.set(assignment.class_key+'\0'+assignment.assignment_key,{...assignment,discovery_only:true});
}
function expirePrivateView(){
 recordState={status:'error',message:'Sign in again to read your private grading records.'};discoveredAssignments.clear();
 discoveryLoader.clear({purge:true});resetProcess();
}
function rememberSelection(){
 // Only the teacher's filters survive a reload. No submissions are stored here.
 try{sessionStorage.setItem(selectionStorageKey,JSON.stringify({classKey:scope.classKey,assignmentKey:scope.assignmentKey,sectionId,stage:scope.stage}));}catch{}
}
function resetDiscoveryForSelection(){
 lastAutomaticScope='';
 const current=discoveryState.data?{classKey:discoveryState.data.class_key,assignmentKey:discoveryState.data.assignment_key}:discoveryState.scope;
 if(current&&(current.classKey!==scope.classKey||(scope.assignmentKey!=='all'&&current.assignmentKey!==scope.assignmentKey)))discoveryLoader?.clear();
}
function ensureDiscoveryForSelection(){
 // Discovery-only identities are source-derived, exact keys. On reload the
 // server must reproduce this key from the current Agenda before returning it.
 const discoveryTarget=new RegExp('^discovery:'+scope.classKey+':[a-f0-9]{32}$').test(scope.assignmentKey);
 if(location.hash!=='#feedback'||recordState.status!=='loaded'||scope.assignmentKey==='all'||(!selectedLesson()&&!discoveryTarget))return;
 const key=JSON.stringify([scope.classKey,scope.assignmentKey]);
 if(lastAutomaticScope===key)return;
 lastAutomaticScope=key;discoveryLoader.load({classKey:scope.classKey,assignmentKey:scope.assignmentKey});
}
function ensureCatalogForSelection(){
 if(location.hash!=='#feedback'||recordState.status!=='loaded'||lastCatalogClass===scope.classKey)return;
 lastCatalogClass=scope.classKey;catalogLoader.load(scope.classKey);
}
const views=[];
// Page 02 uses one stable lesson selection for viewing and assessing work.
function scopeBar(target,page){
 const bar=el('div',undefined,'workspace-controls');
 const classLabel=el('label','Class'),select=el('select');select.setAttribute('aria-label',page+' class');
 for(const [key,name]of Object.entries(classes)){const option=el('option',name);option.value=key;select.append(option);}select.value=scope.classKey;
 select.addEventListener('change',()=>{scope.classKey=select.value;sectionId='';sectionSelectionExplicit=false;scope.assignmentKey='all';resetDiscoveryForSelection();resetProcess();renderScope();renderPrivateRecords();ensureCatalogForSelection();ensureDiscoveryForSelection();});classLabel.append(select);
 sectionLabel=el('label','Section');sectionSelect=el('select');sectionSelect.setAttribute('aria-label','Feedback section');
 sectionSelect.addEventListener('change',()=>{sectionId=sectionSelect.value;sectionSelectionExplicit=true;resetProcess();renderPrivateRecords()});sectionLabel.append(sectionSelect);
 const lessonLabel=el('label','Lesson name');lessonSelect=el('select');lessonSelect.setAttribute('aria-label','Feedback lesson');lessonSelect.append(el('option','All lessons'));lessonSelect.firstChild.value='all';
 lessonSelect.addEventListener('change',()=>{scope.assignmentKey=lessonSelect.value;resetDiscoveryForSelection();resetProcess();renderPrivateRecords();ensureDiscoveryForSelection()});lessonLabel.append(lessonSelect);
 const workLabel=el('label','Work');workSelect=el('select');workSelect.setAttribute('aria-label','Feedback checkpoint');for(const [v,name]of [['all','Notes & drafts'],['initial','Notes'],['final','Drafts']]){const o=el('option',name);o.value=v;workSelect.append(o);}workSelect.value=scope.stage;
 workSelect.addEventListener('change',()=>{scope.stage=workSelect.value;resetProcess();renderPrivateRecords()});workLabel.append(workSelect);
 bar.append(classLabel,sectionLabel,lessonLabel,workLabel);
 const advanced=el('details',undefined,'workflow-details');
 advanced.append(el('summary','Assess new submissions'),el('p','Each assignment has its own turn-in Form. Choose its lesson name above, then check its submissions. Due dates do not change which lesson is assessed.','hint'));
 const context=el('section',undefined,'lesson-context');const notice=el('p','','hint');notice.setAttribute('role','status');advanced.append(context,notice);
 advanced.append(btn('Check selected lesson submissions',()=>processWork()));
 target.append(bar,advanced);views.push({select,context,notice});
 return advanced;
}
function displayedRecords(){
 if(recordState.status!=='loaded')return null;
 const data=recordsWithDiscovery(recordState.data,discoveryState),assignments=[...(data.assignments||[])],known=new Set(assignments.map(a=>a.class_key+'\0'+a.assignment_key));
 for(const [key,assignment]of discoveredAssignments)if(!known.has(key))assignments.push(assignment);
 return {...data,assignments};
}
function selectedLesson(){return displayedRecords()?.assignments?.find(a=>a.class_key===scope.classKey&&a.assignment_key===scope.assignmentKey);}
function selectedSection(){return recordState.status==='loaded'?verifiedSectionOptions(recordState.data,scope.classKey).find(([id])=>id===sectionId):null;}
function renderScope(){
 const lesson=selectedLesson(),section=selectedSection();
 for(const v of views){
  v.select.value=scope.classKey;
  v.context.replaceChildren();v.context.append(el('strong',lesson?.assignment_title||'Choose a lesson name above'));
  if(lesson){
   const links=el('div',undefined,'action-row');
   for(const [label,url]of [['Lesson deck ↗',lesson.source_url],['Turn-in Form ↗',lesson.form_url],['Submission folder ↗',lesson.destination_folder_url]])if(url&&safeLink(url))links.append(link(label,url));
   v.context.append(links);
   if(lesson.due_date)v.context.append(el('p','Due: '+lesson.due_date,'hint'));
  }
  v.notice.textContent=!lesson?'Select one lesson to check its submissions.':lesson.discovery_only?'This lesson was discovered in the Agenda. Its assessment registry binding needs verification before grading.':!section?'The class section needs verification before assessment.':(section[1]+' · '+lesson.assignment_title);
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
const feedback=$('page-feedback');compactHeading(feedback,'Submissions & feedback');const advancedIntake=scopeBar(feedback,'Feedback');
feedback.append(el('p','Choose the class/section, lesson name, and Notes or Drafts to review that lesson’s student work, grade it, and print feedback. Assessing brand-new submissions is inside “Assess new submissions” above.','hint'));
const feedbackGrid=el('div',undefined,'section-grid');
const queue=el('section',undefined,'surface wide');const qhead=el('div',undefined,'section-heading');qhead.append(el('h2','Student work'));promptAction(qhead,'Copy completion instructions','completion');queue.append(qhead);
const catalogNotice=el('p','','hint');catalogNotice.setAttribute('role','status');queue.append(catalogNotice);
const feedbackResults=el('div');feedbackResults.setAttribute('aria-live','polite');queue.append(feedbackResults);
// Directly below the Student work table: the same saved records as a spreadsheet
// to type into Infinite Campus by hand. Reads what is already loaded — it never
// assesses, calls an API, or rescans student work.
const optionText=select=>select?.selectedOptions?.[0]?.textContent||'';
mountIcGradeSheet(queue,{
 state:()=>recordState,
 scope:()=>({classKey:scope.classKey,sectionId,assignmentKey:scope.assignmentKey,
  classLabel:classes[scope.classKey],
  sectionLabel:['matched','all'].includes(sectionId)?'':optionText(sectionSelect),
  lessonLabel:optionText(lessonSelect)})});
// Process work: find and assess the selected lesson's submissions by name — the
// lesson's turn-in sources are resolved from where its recorded work actually
// lives, so no date is needed. The review workspace opens below.
const processStatus=el('p','','hint');processStatus.setAttribute('role','status');
const processRow=el('div',undefined,'action-row');processRow.append(btn('Process work',()=>processWork()),btn('Review approved first assessment',()=>reviewApprovedFirst()));queue.append(processRow,processStatus);
const reviewBox=el('div',undefined,'assessment-review');queue.append(reviewBox);
assessmentReview=mountAssessmentReview(reviewBox,{records:()=>recordState,onSaved:()=>retrieveRecords()});
const LESSON_INTAKE={
 submission_root_not_configured:'The dashboard has not been told where 00 ALL TURN IN lives. This is a deployment setting.',
 submission_reader_scope_missing:'The dashboard’s Drive read permission is misconfigured.',
 submission_parents_unavailable:'The dashboard can read this lesson’s saved files but cannot verify where they live.',
 lesson_has_no_recorded_submissions:'This lesson’s submission source is not verified yet. Refresh submissions to check its current Form and folder.',
 submission_folder_access_required:'The runtime reader cannot see one of this lesson’s verified source folders.',
 lesson_sources_too_scattered:'This lesson’s recorded submissions are spread across too many verified source folders for the bounded scan.',
 submission_folder_inactive:'A verified source folder is inactive or unavailable.',
 submission_scan_incomplete:'The verified-source scan hit a bound before completing. Nothing was assessed.',
 submission_shortcut_needs_review:'A verified source contains a shortcut; it is held for review.',
 invalid_submission_scope:'The lesson selection was not valid. Reload the page and choose the lesson again.',
 sign_in_required:'Your dashboard sign-in expired. Reload the page and sign in again.'};
let processGeneration=0;
function resetProcess(){processGeneration++;assessmentReview?.update({classKey:scope.classKey,assignmentKey:scope.assignmentKey,sectionId,stage:scope.stage,ready:false});if(processStatus)processStatus.textContent='';}
function reviewApprovedFirst(){
 const lesson=selectedLesson(),section=selectedSection();
 if(!lesson||lesson.discovery_only||!section){processStatus.textContent='Choose a verified registry lesson and section before reviewing an approved first assessment.';return;}
 if(!['initial','final'].includes(scope.stage)){processStatus.textContent='Choose Notes or Drafts explicitly before importing its approved first assessment.';return;}
 processGeneration++;
 const opened=assessmentReview.openFirstImport({classKey:scope.classKey,assignmentKey:scope.assignmentKey,sectionId,sectionName:section[1],stage:scope.stage});
 processStatus.textContent=opened?'Review the complete approved packet below. Its exact source, student and saved-record absence will be verified before Save.': 'The approved first-assessment review could not open. Resolve the current review or refresh the verified lesson and section.';
}
async function processWork(){
 if(!scope.assignmentKey||scope.assignmentKey==='all'){processStatus.textContent='Choose a specific lesson name first, then Process work.';return;}
 const lesson=selectedLesson(),section=selectedSection();
 if(lesson?.discovery_only){processStatus.textContent='This lesson’s assessment registry binding must be verified before processing.';return;}
 if(!lesson||!section){processStatus.textContent='The selected lesson and section must be verified before processing.';return;}
 const classKey=scope.classKey,assignmentKey=scope.assignmentKey,selectedSectionId=sectionId,generation=++processGeneration;
 processStatus.textContent='Checking submissions for '+lesson.assignment_title+' · '+section[1]+'…';
 try{
  const r=await fetch('/api/submission-inventory?'+new URLSearchParams({class_key:classKey,assignment:assignmentKey,section_id:selectedSectionId}),{cache:'no-store',credentials:'same-origin',redirect:'error'});
  const d=await r.json();
  if(generation!==processGeneration||scope.classKey!==classKey||scope.assignmentKey!==assignmentKey||sectionId!==selectedSectionId)return;
  if(!r.ok)throw Error(LESSON_INTAKE[d.error]||'The lesson’s submissions could not be checked. Nothing was assessed.');
  if(d.schema_version!=='hub-submission-inventory/v1'||d.class_key!==classKey||d.assignment_key!==assignmentKey||d.section_id!==selectedSectionId||!Array.isArray(d.files)||d.external_writes!==0)throw Error('The lesson’s submissions could not be verified.');
  assessmentReview.update({classKey,assignmentKey,assignmentTitle:lesson.assignment_title,sectionId:selectedSectionId,sectionName:section[1],stage:scope.stage,ready:true});
  assessmentReview.setFiles(d.files,d.deadline||null);
  const eligible=d.files.filter(f=>f.status==='present'&&f.section_id===selectedSectionId&&f.stage!=='assessed').length;
  const extra=d.sources_unreadable?(' · '+d.sources_unreadable+' source file(s) not shared with the reader'):'';
  processStatus.textContent=d.files.length?(d.files.length+' submission'+(d.files.length===1?'':'s')+' found in '+(d.folder_name||'the lesson’s sources')+' · '+eligible+' not yet assessed'+extra+'. Review and generate feedback below.'):('No submission files found in this lesson’s sources'+extra+'.');
 }catch(e){if(generation===processGeneration)processStatus.textContent=e.message||'Process work could not run.';}
}
const reports=el('section',undefined,'surface');reports.append(el('h2','Feedback actions'),el('p','Print the selected lesson’s student feedback as a packet (one student per page), or print a single student from their row above. Printing uses the saved reports and makes no new AI calls.'));const reportActions=el('div',undefined,'action-row');const packetStatus=el('p','','hint');packetStatus.setAttribute('role','status');promptAction(reportActions,'Copy feedback instructions','feedback');reportActions.append(btn('Print feedback packet',()=>{const rows=recordState.status==='loaded'?filterRecords(recordState.data,scope.classKey,sectionId,scope.assignmentKey,scope.stage):[];const n=printFeedbackPacket(rows,recordState.status==='loaded'?recordState.data.submissions:[]);packetStatus.textContent=n?('Printing '+n+' student report'+(n===1?'':'s')+'.'):'No saved feedback to print for this selection yet.';}));reports.append(reportActions,packetStatus);
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
 const displayed=displayedRecords();
 if(sectionSelect){
  const options=recordState.status==='loaded'?verifiedSectionOptions(recordState.data,scope.classKey):[];
  const priorLabel=[...sectionSelect.children].find(option=>option.value===sectionId)?.textContent;
  if(recordState.status==='loaded'){
   if(scope.classKey!=='forensics')sectionId=options.length===1?options[0][0]:'';
   else if(!sectionSelectionExplicit&&!options.some(([id])=>id===sectionId))sectionId=options[0]?.[0]||'';
  }
  sectionLabel.hidden=scope.classKey!=='forensics';
  sectionSelect.replaceChildren();
  const choices=[...options];
  if(sectionId&&!choices.some(([id])=>id===sectionId))choices.push([sectionId,priorLabel||'Selected section unavailable']);
  if(!choices.length)choices.push(['','Sections unavailable']);
  for(const [id,name]of choices){const option=el('option',name);option.value=id;sectionSelect.append(option);}
  sectionSelect.value=sectionId;sectionSelect.disabled=recordState.status!=='loaded'||!options.length;
 }
 if(lessonSelect){
  const lessons=displayed?lessonOptions(displayed,scope.classKey):[];
  const priorLabel=[...lessonSelect.children].find(option=>option.value===scope.assignmentKey)?.textContent;
  if(scope.assignmentKey!=='all'&&!lessons.some(([id])=>id===scope.assignmentKey))lessons.push([scope.assignmentKey,priorLabel||'Selected lesson unavailable']);
  lessonSelect.replaceChildren();for(const [id,name]of [['all','All lessons'],...lessons]){const option=el('option',name);option.value=id;lessonSelect.append(option);}
  lessonSelect.value=scope.assignmentKey;lessonSelect.disabled=recordState.status!=='loaded';
 }
 if(workSelect)workSelect.value=scope.stage;
 catalogNotice.textContent=catalogState.classKey!==scope.classKey?'':catalogState.status==='loading'?'Checking the current Agenda lesson list…':catalogState.status==='error'?catalogState.message:catalogState.status==='loaded'&&!catalogState.data.coverage.complete?'Some Agenda lesson links need review; verified lesson choices remain available.':'';
 rememberSelection();
 renderRecords(feedbackResults,recordState,{classKey:scope.classKey,sectionId,assignmentKey:scope.assignmentKey,stage:scope.stage,discovery:discoveryState,includeUnresolved:true});renderSummaries(summaryResults,recordState);renderScope();
}
const retrieveRecords=createRecordsLoader({onState:state=>{if(state.authExpired)expirePrivateView();if(state.status==='loading')resetProcess();recordState=state;renderPrivateRecords();if(state.status==='loaded'){ensureCatalogForSelection();ensureDiscoveryForSelection()}}});
discoveryLoader=createDiscoveryLoader({onState:state=>{if(state.authExpired)expirePrivateView();if(state.status==='loading')resetProcess();if(state.status==='loaded')rememberAssignments(state.data);discoveryState=state;renderPrivateRecords()}});
catalogLoader=createCatalogLoader({onState:state=>{if(state.authExpired)expirePrivateView();if(state.status==='loaded')rememberAssignments(state.data,true);catalogState=state;renderPrivateRecords();if(state.status==='loaded')ensureDiscoveryForSelection()}});
qhead.append(btn('Refresh submissions',()=>{catalogLoader.load(scope.classKey);if(scope.assignmentKey==='all'){for(const view of views)view.notice.textContent='Choose a lesson name to check its current submissions. All lessons shows saved work and previews already read.';return;}return discoveryLoader.load({classKey:scope.classKey,assignmentKey:scope.assignmentKey});}));summaryActions.append(btn('Refresh summaries',()=>retrieveRecords()));
function loadResults(){renderPrivateRecords();}
renderScope();loadResults();retrieveRecords();
const research=$('page-research');heading(research,'Research workspace','Keep research evidence separate from student-facing feedback. Reuse versioned instructions and the same private workflow records.');const rg=el('div',undefined,'section-grid');const instructions=panel('Shared research instructions','A relevant skill was located in your optimized-system repository. Its referenced codebook and current applicability still need review before research execution is enabled.');instructions.append(link('Open versioned research workflow ↗',researchURL),el('p','Pinned repository version · access requires your repository permissions. This is an instruction source, not the student-data store.','hint'));actions(instructions,btn('Run research workflow',null,true));const evidence=panel('Evidence & coding record','Research observations must link back to approved private evidence and the applied instruction version. Unknown response timing remains unknown.');metrics(evidence,['Accepted observations','Needs review','Verified evidence links']);empty(evidence,'Research data not connected','There is no student research data in this preview or its source files.');const history=panel('Provenance & review history','The shared backend holds the persistent record. This page will display its accepted results and unresolved reviews, without creating a second store.',true);history.append(el('p','Instruction/codebook acceptance: Pending · Authenticated data access: Not verified · Last run: Not checked','readiness'));rg.append(instructions,evidence,history);research.append(rg);
mountControlPanel($('page-control'));

function navigate(){const requested=location.hash.slice(1);const page=allowed.includes(requested)?requested:'control';for(const id of allowed)$('page-'+id).hidden=id!==page;for(const a of document.querySelectorAll('[data-page]')){if(a.dataset.page===page)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');}document.title=`${{control:'Control panel',agenda:'Teaching Agenda',feedback:'Submissions & feedback',summary:'Teacher summary',research:'Research'}[page]} · Agenda`;if(page==='feedback'){ensureCatalogForSelection();ensureDiscoveryForSelection();}}
window.addEventListener('hashchange',navigate);navigate();
