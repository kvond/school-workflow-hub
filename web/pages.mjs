import {createRecordsLoader,renderRecords,renderSummaries,sectionOptions,lessonOptions,filterRecords,printFeedbackPacket} from './grading-records.mjs';
import {safeLink} from './contract.mjs';
import {showRunInstructions} from './browser-runs.mjs';
import {buildPostingReview,renderPostingReview} from './grade-posting.mjs';
import {createCompanionClient,companionMessage} from './browser-companion.mjs';
import {mountControlPanel} from './control-panel.mjs';
import {mountSubmissionMonitor} from './submission-monitor.mjs';
import {mountAssessmentReview} from './assessment-review.mjs';
import {mountIcGradeSheet} from './ic-grade-sheet.mjs';
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
let scope={date:$('date').value,classKey:'biology',assignmentKey:'all',stage:'all',data:null,loading:false,error:''};
let recordState={status:'empty'},sectionId='matched',sectionSelect,lessonSelect,workSelect;
const views=[];
// Page 02 primary selectors: Class / section → Lesson name → Notes or Drafts.
// No lesson-date picker in this row — a date is only used, internally, by the
// collapsed "Assess new submissions" section for finding a turn-in folder.
function scopeBar(target,page){
 const bar=el('div',undefined,'workspace-controls');
 const classLabel=el('label','Class'),select=el('select');select.setAttribute('aria-label',page+' class');
 for(const [key,name]of Object.entries(classes)){const option=el('option',name);option.value=key;select.append(option);}select.value=scope.classKey;
 select.addEventListener('change',()=>{scope.classKey=select.value;sectionId='matched';scope.assignmentKey='all';resetProcess();renderScope();renderPrivateRecords();});classLabel.append(select);
 const sectionLabel=el('label','Section');sectionSelect=el('select');sectionSelect.setAttribute('aria-label','Feedback section');sectionSelect.append(el('option','Matched sections'));sectionSelect.firstChild.value='matched';
 sectionSelect.addEventListener('change',()=>{sectionId=sectionSelect.value;renderPrivateRecords()});sectionLabel.append(sectionSelect);
 const lessonLabel=el('label','Lesson name');lessonSelect=el('select');lessonSelect.setAttribute('aria-label','Feedback lesson');lessonSelect.append(el('option','All lessons'));lessonSelect.firstChild.value='all';
 lessonSelect.addEventListener('change',()=>{scope.assignmentKey=lessonSelect.value;resetProcess();renderPrivateRecords()});lessonLabel.append(lessonSelect);
 const workLabel=el('label','Work');workSelect=el('select');workSelect.setAttribute('aria-label','Feedback checkpoint');for(const [v,name]of [['all','Notes & drafts'],['initial','Notes'],['final','Drafts']]){const o=el('option',name);o.value=v;workSelect.append(o);}workSelect.value=scope.stage;
 workSelect.addEventListener('change',()=>{scope.stage=workSelect.value;renderPrivateRecords()});workLabel.append(workSelect);
 bar.append(classLabel,sectionLabel,lessonLabel,workLabel);
 // Advanced, collapsed: assessing brand-new submissions still needs the lesson's
 // Agenda date to resolve its turn-in folder. Kept out of the primary flow.
 const advanced=el('details',undefined,'workflow-details');
 const dateLabel=el('label','Lesson date (advanced)'),date=el('input');date.type='date';date.value=scope.date;date.setAttribute('aria-label','Assess new submissions date');dateLabel.append(date);
 date.addEventListener('change',()=>requestDay(date.value));
 const load=btn('Load lesson',()=>requestDay(date.value));
 const advBar=el('div',undefined,'workspace-controls');advBar.append(dateLabel,load);
 advanced.append(el('summary','Assess new submissions (Process work)'),el('p','Assessing brand-new work uses this lesson’s Agenda date to resolve its exact shared-Form assignment, then verifies uploaded files inside the approved turn-in root. Pick the date, Load lesson, then scan and draft below. Reviewing, grading and printing already-assessed work needs only the selectors above.','hint'),advBar);
 const context=el('section',undefined,'lesson-context');const notice=el('p','','hint');notice.setAttribute('role','status');advanced.append(context,notice);
 target.append(bar,advanced);views.push({select,date,load,context,notice});
 return advanced;
}
function requestDay(date){document.dispatchEvent(new CustomEvent('agenda-request-day',{detail:{date}}));}
function renderScope(){
 const lesson=scope.data?.lessons.find(l=>l.class_key===scope.classKey);
 const monitorReady=Boolean(scope.data&&!scope.loading&&!scope.error&&lesson?.turnin_form&&lesson?.assignment_label);
 submissionMonitor?.update({date:scope.date,classKey:scope.classKey,ready:monitorReady});
 assessmentReview?.update({date:scope.date,classKey:scope.classKey,ready:monitorReady});
 for(const v of views){
  v.select.value=scope.classKey;v.date.value=scope.date;v.load.disabled=scope.loading;
  v.context.replaceChildren();v.context.append(el('strong',lesson?.title||'Choose a date and load its lesson'));
  if(lesson){
   const links=el('div',undefined,'action-row');
   if(lesson.teacher_slides&&safeLink(lesson.teacher_slides))links.append(link('Teacher Slides ↗',lesson.teacher_slides));
   if(lesson.turnin_form&&safeLink(lesson.turnin_form))links.append(link('Turn-in Form ↗',lesson.turnin_form));
   if(lesson.turnin_folder&&safeLink(lesson.turnin_folder))links.append(link('TurnIn Map locator ↗',lesson.turnin_folder));
   const folder=lesson.links.find(l=>l.label==='Lesson folder');if(folder&&safeLink(folder.url))links.append(link('Lesson materials ↗',folder.url));
   v.context.append(links);
   if(lesson.assignment_label)v.context.append(el('p',lesson.assignment_label,'hint'));
   if(!lesson.turnin_form)v.context.append(el('p','Assignment-bound turn-in Form mapping unavailable for this lesson.','hint'));
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
const feedback=$('page-feedback');compactHeading(feedback,'Submissions & feedback');const advancedIntake=scopeBar(feedback,'Feedback');
feedback.append(el('p','The monitor above shows current discovered files, including work not assessed yet. The Student work table below is saved assessment history only. Choose class/section, assignment name, and Notes or Drafts to review saved results, grade them, and print feedback.','hint'));
const feedbackGrid=el('div',undefined,'section-grid');
const queue=el('section',undefined,'surface wide');const qhead=el('div',undefined,'section-heading');qhead.append(el('h2','Student work'));promptAction(qhead,'Copy completion instructions','completion');queue.append(qhead);
const inventory=el('div');inventory.setAttribute('aria-live','polite');
const scan=btn('Check submissions',()=>scanSubmissions());
let scanGeneration=0,scanController;
function clearInventory(){scanGeneration++;scanController?.abort();inventory.replaceChildren();scan.disabled=!scope.data||scope.loading||!!scope.error;}
async function scanSubmissions(){
 const generation=++scanGeneration,day=scope.date,classKey=scope.classKey;
 scanController?.abort();scanController=new AbortController();scan.disabled=true;
 inventory.replaceChildren(el('p','Checking assignment Form responses and verifying uploaded files inside the approved turn-in root…'));
 try{
  const r=await fetch('/api/submission-inventory?'+new URLSearchParams({date:day,class_key:classKey}),{cache:'no-store',credentials:'same-origin',redirect:'error',signal:scanController.signal});
  const d=await r.json();if(generation!==scanGeneration)return;
  const messages={submission_root_not_configured:'The approved 00 ALL TURN IN root is not configured in the deployment.',submission_reader_scope_missing:'The dashboard’s Drive read permission is misconfigured.',submission_form_not_configured:'The turn-in Form API id is not configured in the deployment.',submission_form_access_required:'The cloud service account cannot read responses from the turn-in Form. Share the editable Form with the runtime reader, then retry.',submission_form_not_found:'The configured turn-in Form could not be found.',submission_form_rate_limited:'Google Forms rate-limited this check. Try again shortly.',submission_form_unavailable:'Google Forms could not be read right now.',submission_form_scan_incomplete:'The Form response read was incomplete. No student conclusions were made.',submission_root_coverage_incomplete:'The Form references a file that could not be verified inside 00 ALL TURN IN. Nothing was called missing.',submission_assignment_required:'The lesson does not have an exact assignment label for filtering the turn-in Form.',submission_lesson_mapping_required:'The Agenda does not expose an assignment-bound turn-in Form for this lesson.',submission_scan_incomplete:'The approved-root verification was incomplete. No student conclusions were made.',submission_agenda_changed:'The Agenda changed during the check. Load the lesson again.',submission_folder_inactive:'The approved submission root is inactive or unavailable.'};
  if(!r.ok)throw Error(messages[d.error]||'The submission source could not be checked. No work was assessed.');
  if(d.schema_version!=='hub-submission-inventory/v1'||d.date!==day||d.class_key!==classKey||!Array.isArray(d.files)||d.external_writes!==0)throw Error('Submission scope could not be verified.');
  const legacyGap=d.legacy_outside_root_files??0,offRoot=d.form_refs_outside_root??0,folderFiles=d.lesson_folder_files??0;
  inventory.replaceChildren(el('h3',d.folder_name),el('p',`${d.files.length} verified assignment files · ${d.matching_responses??0} Form responses · ${folderFiles} files in the exact lesson folder · ${d.folders_checked} folders verified · ${new Date(d.checked_at).toLocaleString()}`));
  if(offRoot)inventory.append(el('p',`${offRoot} Form-linked file${offRoot===1?' is':'s are'} still outside 00 ALL TURN IN. ${offRoot===1?'It is':'They are'} held as a coverage gap and not called missing.`,'hint'));
  if(legacyGap)inventory.append(el('p',`${legacyGap} recorded historical file${legacyGap===1?' is':'s are'} outside current intake. ${legacyGap===1?'It remains':'They remain'} a named coverage gap.`,'hint'));
  if(safeLink(d.folder_url))inventory.append(link('Open turn-in folder ↗',d.folder_url));
  for(const file of d.files){const row=el('p');if(safeLink(file.url))row.append(link(file.name,file.url));inventory.append(row);}
  inventory.append(el('p','File inventory only. Current Form references and files in the exact approved lesson folder are combined. Off-root Form references and older historical sources stay explicit coverage gaps; none are turned into missing-work conclusions or zeros.','hint'));
 }catch(e){if(generation===scanGeneration&&e.name!=='AbortError')inventory.replaceChildren(el('p',e.message,'error'));}
 finally{if(generation===scanGeneration)scan.disabled=false;}
}
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
const processRow=el('div',undefined,'action-row');processRow.append(btn('Process work',()=>processWork()));queue.append(processRow,processStatus);
const reviewBox=el('div',undefined,'assessment-review');queue.append(reviewBox);
const monitorBox=el('div',undefined,'submission-monitor');submissionMonitor=mountSubmissionMonitor(monitorBox,{onFiles:(list,deadline)=>assessmentReview?.setFiles(list,deadline)});advancedIntake.append(monitorBox);
assessmentReview=mountAssessmentReview(reviewBox,{records:()=>recordState,onSaved:()=>retrieveRecords()});
const inventoryDetails=el('details',undefined,'workflow-details');inventoryDetails.append(el('summary','Submission inventory'),scan,inventory);advancedIntake.append(inventoryDetails);
const LESSON_INTAKE={
 submission_root_not_configured:'The dashboard has not been told where 00 ALL TURN IN lives. This is a deployment setting.',
 submission_reader_scope_missing:'The dashboard’s Drive read permission is misconfigured.',
 submission_parents_unavailable:'The dashboard can read this lesson’s saved files but cannot verify where they live.',
 lesson_has_no_recorded_submissions:'This lesson has no saved assessment source yet. Use “Assess new submissions” with the lesson date once; that path now reads the turn-in Form directly.',
 submission_folder_access_required:'The runtime reader cannot see one of this lesson’s verified source folders.',
 lesson_sources_too_scattered:'This lesson’s recorded submissions are spread across too many verified source folders for the bounded scan.',
 submission_folder_inactive:'A verified source folder is inactive or unavailable.',
 submission_scan_incomplete:'The verified-source scan hit a bound before completing. Nothing was assessed.',
 submission_shortcut_needs_review:'A verified source contains a shortcut; it is held for review.',
 invalid_submission_scope:'The lesson selection was not valid. Reload the page and choose the lesson again.',
 sign_in_required:'Your dashboard sign-in expired. Reload the page and sign in again.'};
function resetProcess(){try{assessmentReview&&assessmentReview.update({classKey:scope.classKey,ready:false});}catch(e){}if(processStatus)processStatus.textContent='';}
async function processWork(){
 if(!scope.assignmentKey||scope.assignmentKey==='all'){processStatus.textContent='Choose a specific lesson name first, then Process work.';return;}
 const classKey=scope.classKey,assignmentKey=scope.assignmentKey;
 processStatus.textContent='Finding this lesson’s verified saved submission sources…';
 try{
  const r=await fetch('/api/submission-inventory?'+new URLSearchParams({class_key:classKey,assignment:assignmentKey}),{cache:'no-store',credentials:'same-origin',redirect:'error'});
  const d=await r.json();
  if(scope.classKey!==classKey||scope.assignmentKey!==assignmentKey)return;// selection moved on
  if(!r.ok)throw Error(LESSON_INTAKE[d.error]||'The lesson’s submissions could not be checked. Nothing was assessed.');
  if(d.schema_version!=='hub-submission-inventory/v1'||d.class_key!==classKey||d.assignment_key!==assignmentKey||!Array.isArray(d.files)||d.external_writes!==0)throw Error('The lesson’s submissions could not be verified.');
  assessmentReview.update({classKey,assignmentKey,date:'lesson:'+assignmentKey,ready:true});
  assessmentReview.setFiles(d.files,null);
  const eligible=d.files.filter(f=>f.stage!=='assessed').length;
  const extra=d.sources_unreadable?(' · '+d.sources_unreadable+' source file(s) not shared with the reader'):'';
  processStatus.textContent=d.files.length?(d.files.length+' submission'+(d.files.length===1?'':'s')+' found in '+(d.folder_name||'the lesson’s sources')+' · '+eligible+' not yet assessed'+extra+'. Review and generate feedback below.'):('No submission files found in this lesson’s sources'+extra+'.');
 }catch(e){processStatus.textContent=e.message||'Process work could not run.';}
}
const reports=el('section',undefined,'surface');reports.append(el('h2','Feedback actions'),el('p','Print the selected lesson’s student feedback as a packet (one student per page), or print a single student from their row above. Printing uses the saved reports and makes no new AI calls.'));const reportActions=el('div',undefined,'action-row');const packetStatus=el('p','','hint');packetStatus.setAttribute('role','status');promptAction(reportActions,'Copy feedback instructions','feedback');reportActions.append(btn('Print feedback packet',()=>{const rows=recordState.status==='loaded'?filterRecords(recordState.data,scope.classKey,sectionId,scope.assignmentKey,scope.stage):[];const n=printFeedbackPacket(rows);packetStatus.textContent=n?('Printing '+n+' student report'+(n===1?'':'s')+'.'):'No saved feedback to print for this selection yet.';}));reports.append(reportActions,packetStatus);
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
 if(lessonSelect){const lessons=recordState.status==='loaded'?lessonOptions(recordState.data,scope.classKey):[];lessonSelect.replaceChildren();for(const [id,name]of [['all','All lessons'],...lessons]){const option=el('option',name);option.value=id;lessonSelect.append(option);}if(scope.assignmentKey!=='all'&&!lessons.some(([id])=>id===scope.assignmentKey))scope.assignmentKey='all';lessonSelect.value=scope.assignmentKey;lessonSelect.disabled=recordState.status!=='loaded';}
 if(workSelect)workSelect.value=scope.stage;
 renderRecords(feedbackResults,recordState,{classKey:scope.classKey,sectionId,assignmentKey:scope.assignmentKey,stage:scope.stage});renderSummaries(summaryResults,recordState);
}
const retrieveRecords=createRecordsLoader({onState:state=>{recordState=state;renderPrivateRecords()}});
qhead.append(btn('Refresh submissions',()=>retrieveRecords()));summaryActions.append(btn('Refresh summaries',()=>retrieveRecords()));
function loadResults(){clearInventory();renderPrivateRecords();}
renderScope();loadResults();retrieveRecords();
const research=$('page-research');heading(research,'Research workspace','Keep research evidence separate from student-facing feedback. Reuse versioned instructions and the same private workflow records.');const rg=el('div',undefined,'section-grid');const instructions=panel('Shared research instructions','A relevant skill was located in your answerable-skills repository. Its referenced codebook and current applicability still need review before research execution is enabled.');instructions.append(link('Open versioned research workflow ↗',researchURL),el('p','Pinned repository version · access requires your repository permissions. This is an instruction source, not the student-data store.','hint'));actions(instructions,btn('Run research workflow',null,true));const evidence=panel('Evidence & coding record','Research observations must link back to approved private evidence and the applied instruction version. Unknown response timing remains unknown.');metrics(evidence,['Accepted observations','Needs review','Verified evidence links']);empty(evidence,'Research data not connected','There is no student research data in this preview or its source files.');const history=panel('Provenance & review history','The shared backend holds the persistent record. This page will display its accepted results and unresolved reviews, without creating a second store.',true);history.append(el('p','Instruction/codebook acceptance: Pending · Authenticated data access: Not verified · Last run: Not checked','readiness'));rg.append(instructions,evidence,history);research.append(rg);
mountControlPanel($('page-control'));

function navigate(){const requested=location.hash.slice(1);const page=allowed.includes(requested)?requested:'control';for(const id of allowed)$('page-'+id).hidden=id!==page;for(const a of document.querySelectorAll('[data-page]')){if(a.dataset.page===page)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');}document.title=`${{control:'Control panel',agenda:'Teaching Agenda',feedback:'Submissions & feedback',summary:'Teacher summary',research:'Research'}[page]} · Agenda`;}
window.addEventListener('hashchange',navigate);navigate();
