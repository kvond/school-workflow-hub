export function coverageText(feed){
 const roster=feed.roster_complete===true?'Complete roster':'Roster coverage incomplete';
 const history=feed.full_history_complete===true?'Complete cumulative history':'Submission history incomplete';
 return `${roster} · ${history}. ${feed.roster_complete===true&&feed.full_history_complete===true?'Absence can be evaluated for this supplied scope.':'Do not infer missing work from absent records.'}`;
}
export function completionLines(row){
 if(row.completion_state!=='proposed'||!row.completion?.scores)return [row.completion_state==='prior_revision'?'Earlier-revision assessment; no current scores shown.':'Completion requires assessment or review; no score inferred.'];
 const c=row.completion;const lines=[`Assessment: ${c.stage} · proposed only, not posted`];
 for(const [key,name]of [['content_notes','Content / notes'],['revision','Revision']]){
  const s=c.scores[key];if(!s)continue;
  const value=s.status==='not_yet_due'?'Not yet due':s.status==='N/A'?'N/A':typeof s.value==='number'?`${s.value.toFixed(2).replace(/\.00$/,'')} / ${s.out_of}`:'Unavailable';
  lines.push(`${name}: ${value} (${s.completed}/${s.required} required items)`);
 }
 for(const [key,n]of Object.entries(c.counts??{}))lines.push(`${key}: ${n.completed}/${n.required}${n.due?'':' · not yet due'}`);
 lines.push('Optional work is excluded from scores. Completion does not establish correctness or changed thinking.');
 return lines;
}
