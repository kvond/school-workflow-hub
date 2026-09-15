import {levelBadge,renderLearningProgress,renderClassProgress} from './learning-progress.mjs';
import {safeLink} from './contract.mjs';
import {completionLines} from './completion-view.mjs';
export const FEEDBACK_HEADERS=['Current understanding','How my thinking changed','One question to think about','Evidence for my conceptual understanding',"What I understand now that I didn't before"];
export function validateResults(data,scope){
 if(!data||data.schema_version!=='hub-teaching-results/v1')throw Error('Results unavailable.');
 if(data.class_key!==scope.classKey||data.date!==scope.date||data.agenda_checked_at!==scope.checkedAt)throw Error('Results belong to a different lesson read. Reload this lesson.');
 if(!['source_read','synthetic'].includes(data.provenance)||!data.assignment_key||!data.source_revision||!data.checked_at)throw Error('Result provenance is incomplete.');
 return data;
}
export function validReport(report){return Array.isArray(report.sections)&&report.sections.length===5&&report.sections.every((s,i)=>s.heading===FEEDBACK_HEADERS[i]&&typeof s.text==='string');}
export function resultLabel(data){return `${data.provenance==='synthetic'?'Synthetic preview':'Source results'} · Proposed scores and reports for review; no posting or delivery confirmed.`;}
export function readableTime(value){const date=new Date(value);return Number.isNaN(date.valueOf())?'Unavailable':date.toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'});}
export function statusLabel(value){return ({missing_unscored:'Awaiting submission · unscored',zero_proposed:'Zero proposed · deadline passed',not_due:'Not yet due',needs_you:'Needs review',needs_review:'Needs review',prepared_for_review:'Ready for review',proposed:'Proposed',ready:'Ready for review',not_applicable:'Not applicable'})[value]||String(value||'Needs review').replaceAll('_',' ');}
export function scoreText(row,key){
 if(row.completion_state==='prior_revision')return 'Needs review';
 if(row.status==='zero_proposed'&&row.scores?.[key]===0)return '0 / 10';
 const score=row.completion?.scores?.[key];
 if(score?.status==='not_yet_due')return 'Not yet due';if(score?.status==='N/A')return 'N/A';
 return Number.isFinite(score?.value)?`${Number(score.value.toFixed(2))} / ${score.out_of}`:'—';
}
// This controller keeps private results in memory and discards old requests after any scope change.
export function createResultLoader({fetcher=fetch,onState}){
 let generation=0,controller;
 return async function load(scope){
  const current=++generation;controller?.abort();controller=new AbortController();
  onState({status:'empty'});if(!scope?.checkedAt)return;
  onState({status:'loading'});
  try{
   const query=new URLSearchParams({date:scope.date,class_key:scope.classKey,agenda_checked_at:scope.checkedAt});
   const response=await fetcher('/api/teaching-results?'+query,{cache:'no-store',credentials:'same-origin',signal:controller.signal});
   if(current!==generation)return;
   if(!response.ok)throw Error(response.status===503?'Completion, feedback and summary results are not connected yet.':'Results could not be loaded.');
   const data=validateResults(await response.json(),scope);if(current!==generation)return;
   onState({status:'loaded',data});
  }catch(error){if(current===generation&&error.name!=='AbortError')onState({status:'error',message:error.message});}
 };
}
function node(doc,tag,text){const n=doc.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;}
function section(root,title){const n=node(root.ownerDocument,'section');n.className='surface wide';n.append(node(root.ownerDocument,'h2',title));root.append(n);return n;}
function line(root,text){root.append(node(root.ownerDocument,'p',text));}
function sourceLink(root,label,url){if(!safeLink(url)||!String(url).startsWith('https://'))return;const a=node(root.ownerDocument,'a',label);a.href=url;a.target='_blank';a.rel='noopener noreferrer';root.append(a);}
function asList(value){return Array.isArray(value)?value:[];}
function displayCount(value){return Number.isFinite(value)?value:'Unavailable';}
export function renderTeachingResults({feedbackRoot,summaryRoot},state){
 feedbackRoot.replaceChildren();summaryRoot.replaceChildren();
 if(state.status!=='loaded'){
  const text=state.status==='loading'?'Loading assessed lesson results…':state.message||'Load a lesson to check completion, feedback and summary results. No students are classified as missing from an empty list.';
  line(feedbackRoot,text);line(summaryRoot,text);return;
 }
 const data=state.data;
 for(const root of [feedbackRoot,summaryRoot]){line(root,resultLabel(data));const checks=node(root.ownerDocument,'details');checks.append(node(root.ownerDocument,'summary','Source check · '+readableTime(data.checked_at)));line(checks,'Assignment: '+data.assignment_key);line(checks,'Source revision: '+data.source_revision);line(checks,'Agenda read: '+readableTime(data.agenda_checked_at));root.append(checks);}
 const completion=section(feedbackRoot,'Completion scores');
 const rows=asList(data.completion?.rows);
 if(!rows.length)line(completion,'No completion assessments supplied. Missing work and zero scores cannot be inferred.');
 if(rows.length){
 const wrap=node(completion.ownerDocument,'div');wrap.className='work-table-wrap';const table=node(completion.ownerDocument,'table');table.className='work-table completion-table';table.setAttribute('aria-label','Proposed completion scores');const head=node(table.ownerDocument,'thead'),header=node(table.ownerDocument,'tr');
 for(const title of ['Student','Content / notes','Revision','Status','Details'])header.append(node(table.ownerDocument,'th',title));head.append(header);table.append(head);const body=node(table.ownerDocument,'tbody');
 for(const row of rows){
  const tr=node(table.ownerDocument,'tr');for(const text of [row.display_name||'Identity unavailable',scoreText(row,'content_notes'),scoreText(row,'revision'),statusLabel(row.status)])tr.append(node(table.ownerDocument,'td',text));
  const cell=node(table.ownerDocument,'td'),entry=node(table.ownerDocument,'details');entry.append(node(table.ownerDocument,'summary','View'));
  const normalized={...row,completion_state:row.completion_state||(row.completion?.scores?'proposed':'unassessed')};
  for(const text of completionLines(normalized))line(entry,text);
  if(row.submission_revision)line(entry,'Submission revision: '+row.submission_revision);
  for(const reason of [...asList(row.review_reasons),...asList(row.issues)])line(entry,statusLabel(reason));
  if(row.cutoff_at)line(entry,'Two-week deadline: '+readableTime(row.cutoff_at));
  sourceLink(entry,'Open submitted work ↗',row.submission_url||row.evidence_reference);
  line(entry,'Infinite Campus posting: not verified by this result.');cell.append(entry);tr.append(cell);body.append(tr);
 }
 table.append(body);wrap.append(table);completion.append(wrap);
 }
 const reports=section(feedbackRoot,'Feedback to return');
 if(!asList(data.feedback?.reports).length)line(reports,'No student feedback reports supplied.');
 for(const report of asList(data.feedback?.reports)){
  const entry=node(reports.ownerDocument,'details');entry.append(node(reports.ownerDocument,'summary',report.display_name||'Student identity unavailable'));
  line(entry,'Prepared for teacher review · '+statusLabel(report.status));
  if(data.provenance==='synthetic')line(entry,'Fictional practice sample — answers and graph history are invented.');
  if(report.class_key!==(data.class_stable_id||data.class_key)||report.assignment_key!==data.assignment_key){line(entry,'Report belongs to another lesson. Report withheld.');reports.append(entry);continue;}
  if(report.report_version==='missing-work/v1'){
   entry.append(node(entry.ownerDocument,'h3','Your thinking matters'),levelBadge(entry.ownerDocument,null));
   line(entry,report.message);
   entry.append(node(entry.ownerDocument,'h3',report.contract_heading));
   for(const text of asList(report.contract_lines))line(entry,text);
   reports.append(entry);continue;
  }
  for(const reason of asList(report.review_reasons))line(entry,'Needs review: '+reason);
  if(report.status==='needs_review'){line(entry,'Feedback is withheld until these evidence issues are reviewed.');reports.append(entry);continue;}
  if(!validReport(report)){line(entry,'Feedback format needs review. Report withheld.');reports.append(entry);continue;}
  renderLearningProgress(entry,report.learning_progress,{learnerKey:report.learner_key,classKey:data.class_stable_id||data.class_key,provenance:data.provenance});
  for(const part of report.sections){
   entry.append(node(entry.ownerDocument,'h3',part.heading));
   if(part.heading==='Current understanding'&&Array.isArray(report.current_understanding)){
    for(const item of report.current_understanding){const block=node(entry.ownerDocument,'div');block.className='understanding-item';block.append(node(entry.ownerDocument,'strong',item.label),node(entry.ownerDocument,'br'),levelBadge(entry.ownerDocument,item.level),node(entry.ownerDocument,'p',item.explanation));entry.append(block);}
   }else{const content=node(entry.ownerDocument,'p',part.text);content.style.whiteSpace='pre-wrap';entry.append(content);}
  }
  sourceLink(entry,'Open feedback document ↗',report.document_url);
  const sourceDetails=node(entry.ownerDocument,'details');sourceDetails.append(node(entry.ownerDocument,'summary','Teacher source details'));line(sourceDetails,'Submission revision: '+report.submission_revision+' · Instructions: '+report.prompt_version);entry.append(sourceDetails);reports.append(entry);
 }
 const summary=data.summary;
 if(!summary){line(summaryRoot,'No class summary supplied.');return;}
 if(summary.class_key!==(data.class_stable_id||data.class_key)||summary.assignment_key!==data.assignment_key){line(summaryRoot,'Summary belongs to another lesson. Summary withheld.');return;}
 line(summaryRoot,`Coverage: ${summary.coverage?.state||'unavailable'} · ${displayCount(summary.coverage?.received)} received / ${displayCount(summary.coverage?.expected)} expected. ${summary.status==='partial'?'Partial evidence; this is not a whole-class conclusion.':''}`);
 renderClassProgress(summaryRoot,summary.learning_progress,{classKey:data.class_stable_id||data.class_key,provenance:data.provenance});
 const participation=section(summaryRoot,'Participation');
 line(participation,'Decks in: '+displayCount(summary.participation?.decks_in));
 if(summary.missing_feedback_count)line(participation,'Missing-work feedback notes prepared: '+summary.missing_feedback_count);
 for(const [stage,counts]of Object.entries(summary.participation?.by_stage||{})){
  line(participation,`${stage}: ${displayCount(counts.complete)} complete / ${displayCount(counts.decks)} decks · completion unavailable: ${displayCount(counts.completion_unavailable)}`);
  for(const [category,count]of Object.entries(counts.categories||{}))line(participation,`${category}: ${displayCount(count.completed_items)}/${displayCount(count.required_items)} items · ${count.status||'unavailable'} · ${displayCount(count.unavailable_students)} students with unavailable evidence`);
 }
 for(const [kind,count]of Object.entries(summary.participation?.timing||{}))line(participation,kind.replaceAll('_',' ')+': '+displayCount(count));
 if(summary.participation?.comparison_timing){line(participation,'Comparison evidence by student (categories may overlap):');for(const [kind,count]of Object.entries(summary.participation.comparison_timing))line(participation,kind.replaceAll('_',' ')+': '+displayCount(count));}
 const spread=section(summaryRoot,'Proficiency spread across the three levels, per question');
 if(!Object.keys(summary.proficiency_by_question||{}).length)line(spread,'Question-level evidence unavailable.');
 for(const [question,levels]of Object.entries(summary.proficiency_by_question||{}))line(spread,question+': '+Object.entries(levels).map(([level,count])=>level+' '+displayCount(count)).join(' · '));
 const misconceptions=section(summaryRoot,'Recurring misconceptions');
 if(!asList(summary.recurring_misconceptions).length)line(misconceptions,'No recurring misconception established by the supplied evidence.');
 for(const idea of asList(summary.recurring_misconceptions)){line(misconceptions,idea.description);for(const evidence of asList(idea.evidence)){line(misconceptions,`${evidence.display_name||'Identity unavailable'} · ${evidence.question_id} · ${evidence.timing||'timing unavailable'}`);misconceptions.append(node(misconceptions.ownerDocument,'blockquote',evidence.quote));sourceLink(misconceptions,'Supporting work ↗',evidence.evidence_reference);}}
 const contrasts=section(summaryRoot,'The next instructional contrast');
 if(!asList(summary.instructional_contrasts).length)line(contrasts,'No evidence-backed contrast supplied.');
 for(const contrast of asList(summary.instructional_contrasts)){line(contrasts,'Case 1: '+contrast.case_a);line(contrasts,'Case 2: '+contrast.case_b);line(contrasts,contrast.rationale);}
 const ideas=section(summaryRoot,'Teacher notes · An idea worth chasing');
 for(const note of asList(summary.teacher_notes)){const item=node(ideas.ownerDocument,'div');item.className='teacher-discussion';line(item,(note.display_name||'Student')+' · '+note.slide_title);line(item,note.idea);ideas.append(item);}
 for(const report of asList(data.feedback?.reports))for(const flag of asList(report.teacher_only_flags))line(ideas,(report.display_name||'Student')+': '+flag);
 const checks=section(summaryRoot,'Anything I should check myself');
 if(!asList(summary.teacher_checks).length)line(checks,'No additional check supplied.');
 for(const check of asList(summary.teacher_checks))line(checks,(check.display_name||'Identity unavailable')+': '+check.reason);
}
