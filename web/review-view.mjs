// Presentation of supplied states only; no re-scoring or source comparison.
export function reviewReasons(row){
 const reasons=[];
 if(row.submission_state==='unverified')reasons.push('Submission status is unverified; source coverage needs checking.');
 if(row.deadline_state==='unverified')reasons.push('Deadline eligibility is unverified; do not send an overdue notice.');
 const feedback={stale_feedback:'Feedback is for an older submission or prompt version.',folder_needs_review:'Feedback folder does not match the expected lesson folder.',needs_feedback:'Current submitted work still needs feedback.'};
 if(feedback[row.feedback_state])reasons.push(feedback[row.feedback_state]);
 if(row.completion_state==='prior_revision')reasons.push('Completion assessment belongs to an earlier submission revision.');
 if(row.completion_state==='needs_review')reasons.push('Completion is held for review; no score should be inferred.');
 if(row.parent_email_route==='contact_needs_verification')reasons.push('The parent contact needs verification before a notice can be prepared.');
 const known={unreadable_or_ambiguous_response:'A required response is unreadable or ambiguously mapped.',required_response_not_assessed:'Required response evidence has not all been assessed.',ambiguous_response_mapping:'Response-to-question mapping is ambiguous.',required_item_marked_optional:'A required item was marked optional.'};
 for(const issue of row.completion?.issues??[])reasons.push(known[issue]??'Backend review issue: '+String(issue));
 return [...new Set(reasons)];
}
export function deliveryText(kind,outcome){
 const name=kind.replaceAll('_',' '),state=outcome.state.replaceAll('_',' ');
 return `Preview outcome — ${name}: ${state}. Checked: ${outcome.last_checked_at??'Not checked'}. Receipt: ${outcome.receipt_reference??'Not supplied'}.`;
}
