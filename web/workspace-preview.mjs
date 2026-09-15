import {reviewReasons,deliveryText} from './review-view.mjs';
import {coverageText,completionLines} from './completion-view.mjs';
import {validatePreviewWorkspace,scopedCases} from './workspace-contract.mjs';
const titles={agenda:'Dated lesson feed',feedback:'Submission & feedback feed',summary:'Class evidence feed'};
const mounts={};let snapshot=null,choice='',state='',scenario='complete',requestSequence=0;
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function button(text,fn){const n=el('button',text);n.type='button';n.addEventListener('click',fn);return n;}
function label(value){return String(value??'Not supplied').replaceAll('_',' ');}
function lessons(){return snapshot.pages.teaching_agenda.lessons;}
function selection(){return lessons().find(l=>l.lesson_key===choice);}
function render(){for(const [page,mount] of Object.entries(mounts)){
 const root=document.getElementById('page-'+page);for(const n of root.querySelectorAll('.scope-bar,.page-scope-note'))n.hidden=!!snapshot;
 mount.replaceChildren(el('summary','Try fictional data · '+titles[page]),el('p','Optional integration demo · fictional backend-generated records. No school connection.','hint'));
 const scenarioLabel=el('label','Fictional data coverage','field-label'),scenarioSelect=el('select');scenarioSelect.setAttribute('aria-label',page+' preview coverage');scenarioSelect.append(new Option('Complete supplied history','complete'),new Option('Incomplete submission history','partial'));scenarioSelect.value=scenario;scenarioSelect.addEventListener('change',()=>{scenario=scenarioSelect.value;load()});scenarioLabel.append(scenarioSelect);mount.append(scenarioLabel,button(snapshot?'Refresh synthetic snapshot':'Load synthetic integration demo',load));
 const status=el('p',state||'No preview records loaded.','hint');status.setAttribute('role','status');mount.append(status);
 if(!snapshot)continue;
 mount.open=true;
 mount.append(button('Close synthetic demo',()=>{requestSequence++;snapshot=null;choice='';state='Demo closed. Any staged unsynced drafts are preserved.';render()}));
 const l=el('label','Preview lesson','field-label'),select=el('select');select.setAttribute('aria-label',page+' preview lesson');select.append(new Option('All preview lessons',''));for(const lesson of lessons())select.append(new Option(lesson.title+' · '+lesson.teaching_date,lesson.lesson_key));select.value=choice;select.addEventListener('change',()=>{choice=select.value;render()});l.append(select);mount.append(l);
 if(page==='agenda'){
  const visible=selection()?[selection()]:lessons();
  for(const lesson of visible){const card=el('article',undefined,'workspace-record');card.append(el('h3',lesson.title),el('p',lesson.teaching_date+' · '+lesson.class_key),el('p',lesson.notes),el('p','Source revision: '+lesson.source_revision+' · observed '+lesson.observed_at,'hint'));
   const slides=el('button',lesson.open_slides?'Teacher Slides reference supplied · preview only':'No Slides URL supplied');slides.disabled=true;card.append(slides,button('Use fictional notes as an unsynced draft',()=>{document.dispatchEvent(new CustomEvent('workspace-preview-draft',{detail:lesson}));}));mount.append(card);}
 }else{
  const selected=selection(),rows=scopedCases(snapshot,selected?.class_key,selected?.assignment_key);for(const feed of snapshot.pages.submissions_feedback.classes.filter(f=>!selected||f.class_key===selected.class_key))mount.append(el('p',coverageText(feed),'readiness'));
  if(!rows.length){mount.append(el('p','No cases supplied for this scope. Missing data is not zero submissions.','document-empty'));continue;}
  if(page==='summary'){
   const counts=new Map();for(const row of rows)counts.set(row.submission_state,(counts.get(row.submission_state)||0)+1);
   mount.append(el('p','Counts are learner-assignment cases, not unique students or lesson-completion scores.'));
   for(const [name,count] of counts)mount.append(el('p',label(name)+': '+count));
   const unresolved=rows.map(row=>({row,reasons:reviewReasons(row)})).filter(item=>item.reasons.length);
   const reviews=el('details');reviews.append(el('summary','Unresolved evidence items · '+unresolved.length+' cases'));for(const item of unresolved){reviews.append(el('h3',item.row.learner_key+' · fictional'));for(const reason of item.reasons)reviews.append(el('p',reason));}if(!unresolved.length)reviews.append(el('p','No review reasons supplied for these cases. This does not certify live readiness.'));mount.append(reviews);
   mount.append(el('p','Instructional summary: '+label(snapshot.pages.teacher_summary.instructional_summary_state)+'. No misconceptions or proficiency judgments are generated here.','readiness'));
  }else for(const row of rows){const card=el('article',undefined,'workspace-record');card.append(el('h3',row.learner_key+' · fictional'),el('p','Submission: '+label(row.submission_state)+' · Deadline: '+label(row.deadline_state)),el('p','Feedback: '+label(row.feedback_state)+' · Completion: '+label(row.completion_state)),el('p','Parent contact route: '+label(row.parent_email_route)),el('p','Checked: '+row.last_checked_at,'hint'));
   const reasons=reviewReasons(row);if(reasons.length){const review=el('div',undefined,'readiness');review.append(el('strong','Unresolved evidence'));const list=el('ul');for(const reason of reasons)list.append(el('li',reason));review.append(list);card.append(review);}const completion=el('details');completion.append(el('summary','Proposed completion details'));for(const line of completionLines(row))completion.append(el('p',line));card.append(completion);
   if(row.feedback_document_reference){card.append(el('p','Private document reference: '+row.feedback_document_reference,'hint'));const open=el('button','Open feedback · resolver not connected');open.disabled=true;card.append(open);}
   const detail=el('details'),sum=el('summary','Delivery evidence');detail.append(sum);for(const [kind,outcome]of Object.entries(row.delivery))detail.append(el('p',deliveryText(kind,outcome)));card.append(detail);mount.append(card);
  }
 }
}}
async function load(){const sequence=++requestSequence;state='Loading fictional workspace…';render();try{const r=await fetch(scenario==='partial'?'./workspace.partial.preview.json':'./workspace.preview.json',{cache:'no-store'});if(!r.ok)throw Error('Unavailable');const next=validatePreviewWorkspace(await r.json());if(sequence!==requestSequence)return;snapshot=next;if(choice&&!lessons().some(l=>l.lesson_key===choice))choice='';state='Synthetic snapshot assembled '+snapshot.generated_at+'. No live operations.';}catch{if(sequence!==requestSequence)return;state=snapshot?'Refresh failed. Retaining last valid synthetic snapshot from '+snapshot.generated_at+'.':'Workspace preview unavailable. No records or actions loaded.';}render();}
// Add only in-scope mounts; do not inspect or render research payloads.
for(const page of Object.keys(titles)){const target=document.getElementById('page-'+page);const mount=el('details',undefined,'surface workspace-demo');const anchor=page==='agenda'?target.querySelector('#classes'):target.querySelector('.section-grid');target.insertBefore(mount,anchor);mounts[page]=mount;}render();
