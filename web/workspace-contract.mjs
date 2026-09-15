import {validateSnapshot} from './contract.mjs';
export function validatePreviewWorkspace(p){
 if(!p||p.schema_version!=='agenda-workspace/v1'||p.external_writes!==0||p.privacy!=='authenticated_teacher_only')throw Error('Unsupported workspace envelope');
 validateSnapshot(p.operations);
 if(p.operations.connection_state!=='preview_only')throw Error('This demo cannot load live records');
 if(!p.actions||Object.values(p.actions).some(a=>a.supported!==false))throw Error('Execution must remain disabled');
 const page=p.pages?.teaching_agenda, feeds=p.pages?.submissions_feedback?.classes;
 if(!page||page.two_way_editing_supported!==false||!Array.isArray(page.lessons)||!Array.isArray(feeds)||!p.pages.teacher_summary)throw Error('Missing workspace pages');
 const seen=new Set();
 for(const l of page.lessons){
  if(typeof l.lesson_key!=='string'||!l.lesson_key.startsWith('lesson:synthetic:')||seen.has(l.lesson_key)||typeof l.title!=='string'||typeof l.notes!=='string'||typeof l.source_revision!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(l.teaching_date)||l.save_supported!==false)throw Error('Invalid fictional lesson');
  seen.add(l.lesson_key);
 }
 const requiredActions=['save_agenda','send_parent_email','file_feedback','record_roster_grade','post_ic_grade','print_feedback'];
 if(requiredActions.some(k=>p.actions[k]?.supported!==false))throw Error('Missing disabled capability');
 const statusFields=['submission_state','deadline_state','feedback_state','completion_state'];
 const checkStamp=v=>typeof v==='string'&&Number.isFinite(Date.parse(v));
 const cases=new Set();
 for(const f of feeds){if(typeof f.roster_complete!=='boolean'||typeof f.full_history_complete!=='boolean')throw Error('Missing coverage flags');if(!Array.isArray(f.rows))throw Error('Invalid submission feed');for(const row of f.rows){const key=JSON.stringify([row.class_key,row.assignment_key,row.learner_key]);if(typeof row.learner_key!=='string'||!row.learner_key.startsWith('learner:synthetic:')||cases.has(key)||row.class_key!==f.class_key)throw Error('Invalid fictional case');if(row.submission_state==='not_submitted'&&(!f.roster_complete||!f.full_history_complete))throw Error('Absence lacks complete source coverage');
 for(const field of statusFields)if(typeof row[field]!=='string')throw Error('Missing row status');
 if(!checkStamp(row.last_checked_at)||typeof row.parent_email_route!=='string'||!row.delivery)throw Error('Incomplete case record');
 for(const kind of ['parent_email','roster_grade','ic_grade','print_feedback']){const d=row.delivery[kind];if(!d||typeof d.state!=='string'||(d.last_checked_at!==null&&!checkStamp(d.last_checked_at)))throw Error('Missing delivery state');}
 if(row.feedback_document_reference!==null&&typeof row.feedback_document_reference!=='string')throw Error('Invalid document reference');
 cases.add(key);}}
 return p;
}
export function scopedCases(p,classKey,assignmentKey){return p.pages.submissions_feedback.classes.flatMap(f=>f.rows).filter(r=>(!classKey||r.class_key===classKey)&&(!assignmentKey||r.assignment_key===assignmentKey));}
