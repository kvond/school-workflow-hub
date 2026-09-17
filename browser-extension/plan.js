(function(root){
 'use strict';
 const P=root.HubPostingProtocol||(typeof require==='function'?require('./protocol.js'):null);
 const COMPONENTS=['content_score','revision_score'];
 const CONTEXT_KEYS=['section_id','term_id','year','school_id','calendar_id','assignment_id','title','points'];
 const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
 const exact=(value,keys)=>object(value)&&Object.keys(value).sort().join('|')===[...keys].sort().join('|');
 const text=(value,max=200)=>typeof value==='string'&&value.length>0&&value.length<=max&&value.trim()===value&&!/[\u0000-\u001f]/.test(value);
 const freeze=value=>{if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;};
 const clone=value=>JSON.parse(JSON.stringify(value));
 const fail=code=>{throw Error(code);};
 function canonical(value){if(Array.isArray(value))return value.map(canonical);if(object(value))return Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])]));return value;}
 const encoded=value=>JSON.stringify(canonical(value));
 function context(value){
  if(!exact(value,CONTEXT_KEYS)||value.points!==10||CONTEXT_KEYS.filter(key=>key!=='points').some(key=>!text(value[key],key==='title'?1000:200)))fail('invalid_destination_context');
  return Object.fromEntries(CONTEXT_KEYS.map(key=>[key,value[key]]));
 }
 const contextIdentity=value=>encoded(CONTEXT_KEYS.filter(key=>!['title','points'].includes(key)).map(key=>value[key]));
 const sourceKey=(record,component)=>encoded([record,component]);
 function snapshots(values){
  if(!Array.isArray(values)||values.length>30)fail('invalid_destination_snapshot');
  const seen=new Set();
  return values.map(value=>{
   if(!exact(value,['context','students','saved'])||value.saved!==true||!Array.isArray(value.students)||value.students.length>1000)fail(value?.saved===false?'destination_not_saved':'invalid_destination_snapshot');
   const ctx=context(value.context),key=contextIdentity(ctx);if(seen.has(key))fail('duplicate_destination_context');seen.add(key);
   const students=new Set(),people=new Set();
   return {context:ctx,students:value.students.map(student=>{
    if(!exact(student,['student_id','person_id','display_name','input_label','current_score','flags'])||!text(student.student_id)||typeof student.person_id!=='string'||!/^\d{1,30}$/.test(student.person_id)||!text(student.display_name,500)||!text(student.input_label,1000)||students.has(student.student_id)||people.has(student.person_id)||!Array.isArray(student.flags)||student.flags.length>30||student.flags.some(flag=>!text(flag,100))||(student.current_score!==null&&(typeof student.current_score!=='number'||!Number.isFinite(student.current_score)||student.current_score<0||student.current_score>10)))fail('invalid_destination_student');
    students.add(student.student_id);people.add(student.person_id);return clone(student);
   }),saved:true};
  });
 }
 function mappingRows(values,source){
  if(!Array.isArray(values)||values.length>P.MAX_ROWS*2)fail('invalid_destination_mapping');
  const records=new Set(source.record_ids),seen=new Set();
  return values.map(value=>{
   if(!exact(value,['record_id','component','student_id','context'])||!records.has(value.record_id)||!COMPONENTS.includes(value.component)||!text(value.student_id))fail('invalid_destination_mapping');
   const key=sourceKey(value.record_id,value.component);if(seen.has(key))fail('duplicate_source_mapping');seen.add(key);
   return {record_id:value.record_id,component:value.component,student_id:value.student_id,context:context(value.context)};
  });
 }
 function buildPlan(sourceReview,mappings,destinations,{now=Date.now()}={}){
  if(!P)fail('posting_protocol_required');
  const source=P.validateReview(sourceReview,now),maps=mappingRows(mappings,source),live=snapshots(destinations);
  const mapBySource=new Map(maps.map(mapping=>[sourceKey(mapping.record_id,mapping.component),mapping]));
  const destinationByContext=new Map(live.map(snapshot=>[contextIdentity(snapshot.context),snapshot]));
  const counts=new Map(),labels=new Map();
  for(const snapshot of live){const key=contextIdentity(snapshot.context);for(const student of snapshot.students){const label=encoded([key,student.input_label]);labels.set(label,(labels.get(label)||0)+1);}}
  for(const mapping of maps){const key=encoded([contextIdentity(mapping.context),mapping.student_id]);counts.set(key,(counts.get(key)||0)+1);}
  const rows=[];
  for(const record of source.rows)for(const component of COMPONENTS){
   const score=record.scores[component],mapping=mapBySource.get(sourceKey(record.record_id,component))||null;
   const reasons=[...new Set([...record.exclusion_reasons,...score.exclusion_reasons])];
   if(!record.section_id.trim())reasons.push('source_section_missing');
   if(!record.student_id.trim())reasons.push('source_student_id_missing');
   if(record.hold_reason||/^(?:held|hold|on_hold)(?:_|$)/.test(record.status))reasons.push('source_held');
   if(['identity_unverified','superseded'].includes(record.status))reasons.push('source_'+record.status);
   if(score.value===null)reasons.push('source_unscored');
   const excluded=reasons.length>0;
   let previous_score=null,target=null;
   if(!excluded){
    if(!mapping)reasons.push('destination_mapping_required');
    else {
     if(mapping.student_id!==record.student_id)reasons.push('student_identity_mismatch');
     if(mapping.context.section_id!==record.section_id)reasons.push('section_identity_mismatch');
     const snapshot=destinationByContext.get(contextIdentity(mapping.context));
     if(!snapshot)reasons.push('destination_snapshot_required');
     else if(encoded(snapshot.context)!==encoded(mapping.context))reasons.push('destination_context_changed');
     else {
      const student=snapshot.students.find(item=>item.student_id===mapping.student_id);
      if(!student)reasons.push('destination_student_missing');
      else {
       previous_score=student.current_score;
       target={context:clone(snapshot.context),student_id:student.student_id,person_id:student.person_id,input_label:student.input_label,display_name:student.display_name,flags:[...student.flags]};
       if(student.flags.length)reasons.push('destination_flags_require_review');
       if(labels.get(encoded([contextIdentity(snapshot.context),student.input_label]))>1)reasons.push('destination_input_ambiguous');
       if(counts.get(encoded([contextIdentity(mapping.context),mapping.student_id]))>1)reasons.push('duplicate_destination_cell');
       if(previous_score!==null&&previous_score!==score.value)reasons.push('existing_grade_conflict');
      }
     }
    }
   }
   const disposition=excluded?'excluded':reasons.length?'needs_review':previous_score===score.value?'already_matches':'ready';
   rows.push({record_id:record.record_id,component,source_student_id:record.student_id,source_section_id:record.section_id,source_assignment_key:record.assignment_key,source_assignment_title:record.assignment_title,source_exact:score.exact,desired_score:score.value,previous_score,mapping:mapping?clone(mapping):null,target,disposition,reasons:[...new Set(reasons)]});
  }
  return freeze({schema_version:'hub-grade-posting-plan/v1',source_revision:source.source_revision,checked_at:source.checked_at,source:clone(source),mappings:maps,destinations:live,rows,counts:{ready:rows.filter(row=>row.disposition==='ready').length,already_matches:rows.filter(row=>row.disposition==='already_matches').length,needs_review:rows.filter(row=>row.disposition==='needs_review').length,excluded:rows.filter(row=>row.disposition==='excluded').length},external_writes:0});
 }
 function boundValue(plan){return {...plan,checked_at:null,source:{...plan.source,checked_at:null}};}
 function assertCurrentPlan(plan,freshSource,freshDestinations,options={}){
  if(plan?.schema_version!=='hub-grade-posting-plan/v1'||!Array.isArray(plan.mappings))fail('posting_plan_required');
  const current=buildPlan(freshSource,plan.mappings,freshDestinations,options);
  if(encoded(boundValue(plan))!==encoded(boundValue(current)))fail('posting_plan_changed');
  return current;
 }
 const api=Object.freeze({buildPlan,assertCurrentPlan});root.HubPostingPlan=api;
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(globalThis);
