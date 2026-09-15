export const CONTRACT_URL = 'https://github.com/kvond/school-workflow-hub/blob/main/docs/HTML_AGENDA_INTEGRATION.md';
export const KINDS = {assignment_links:'Assignment links',date_alignment:'Assignment dates',grading_review:'Grading review',roster_changes:'Roster changes'};
const statuses = ['not_checked','in_progress','passed','needs_review','blocked','failed','not_applicable'];
const levels = ['unverified','synthetic_test','live_read','live_end_to_end'];
export function validateSnapshot(p) {
  if (!p || p.schema_version !== 'agenda-operations/v1' || p.edit_saving_supported !== false || !Array.isArray(p.operations)) throw Error('Unsupported operations contract.');
  if (!['not_connected','preview_only','connected_readonly','connected_live'].includes(p.connection_state)) throw Error('Unknown connection state.');
  const stamp = value => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
  if (!stamp(p.generated_at)) throw Error('Missing snapshot timestamp.');
  function check(c) {
    if (!c || !statuses.includes(c.status) || !levels.includes(c.verification) || !Array.isArray(c.evidence)) throw Error('Invalid check.');
    if (c.last_checked_at !== null && (!stamp(c.last_checked_at) || Date.parse(c.last_checked_at)>Date.parse(p.generated_at))) throw Error('Invalid check time.');
    for (const e of c.evidence) if (!['test_receipt','source_snapshot','readback','error_receipt'].includes(e.kind) || typeof e.summary !== 'string' || !/^[A-Za-z0-9:_-]{1,160}$/.test(e.reference) || !stamp(e.observed_at) || !c.last_checked_at || Date.parse(e.observed_at)>Date.parse(c.last_checked_at)) throw Error('Invalid evidence.');
    if (c.status==='not_checked' && (c.verification!=='unverified' || c.last_checked_at!==null || c.evidence.length)) throw Error('Unchecked result contains verification.');
    if (c.next_action !== null && (!c.next_action || typeof c.next_action.label!=='string' || !['review','recheck','connect','open_task'].includes(c.next_action.kind))) throw Error('Invalid next action.');
    if (['needs_review','blocked','failed'].includes(c.status) && !c.next_action) throw Error('Missing next action.');
    if (c.status==='passed') {
      const kind={synthetic_test:'test_receipt',live_read:'source_snapshot',live_end_to_end:'readback'}[c.verification];
      if (!c.last_checked_at || !kind || !c.evidence.some(e=>e.kind===kind)) throw Error('Pass lacks matching evidence.');
    }
  }
  check(p.automatic_sync);
  if (typeof p.automatic_sync.active!=='boolean' || (p.automatic_sync.active && (p.connection_state!=='connected_live' || p.automatic_sync.status!=='passed' || p.automatic_sync.verification!=='live_end_to_end'))) throw Error('Invalid synchronization claim.');
  const seen=new Set(), ids=new Set();
  for (const o of p.operations) {
    if (!Object.hasOwn(KINDS,o.kind) || typeof o.id!=='string' || !/^[A-Za-z0-9:_-]{1,160}$/.test(o.id) || !o.scope) throw Error('Invalid operation identity.');
    const keys=Object.keys(o).sort().join(',');
    if (keys!=='evidence,id,kind,last_checked_at,next_action,scope,status,verification') throw Error('Unexpected operation fields.');
    if (Object.keys(o.scope).sort().join(',')!=='assignment_key,class_key,week_key') throw Error('Unexpected scope fields.');
    for(const [key,val] of Object.entries(o.scope)) if (!(val===null && (key==='week_key'||key==='assignment_key'&&o.kind==='roster_changes')) && (typeof val!=='string'||!/^[A-Za-z0-9:_-]{1,160}$/.test(val))) throw Error('Invalid scope.');
    const identity=JSON.stringify([o.kind,o.scope.class_key,o.scope.assignment_key,o.scope.week_key]);
    if(seen.has(identity)||ids.has(o.id)) throw Error('Duplicate operation.');
    seen.add(identity);ids.add(o.id);check(o);
  }
  return p;
}
export function displayStatus(o) {
  if(o.status==='passed') return {synthetic_test:'Test passed',live_read:'Read checked',live_end_to_end:'Verified in destination'}[o.verification] || 'Unverified';
  return {not_checked:'Not checked',in_progress:'Working',needs_review:'Needs you',blocked:'Needs you · blocked',failed:'Needs you · failed',not_applicable:'Not applicable'}[o.status] || 'Unverified';
}
export function safeLink(raw) {
  try {const u=new URL(raw);return u.protocol==='https:'&&!u.username&&!u.password ? u.href : null;}catch{return null;}
}
export function manualInstructions(o) {
  return `MANUAL HANDOFF TEMPLATE — NOT EXECUTION AUTHORIZATION\nContract: agenda-operations/v1\nWorkflow: ${o.kind}\nOperation: ${o.id}\nClass: ${o.scope.class_key}\nAssignment: ${o.scope.assignment_key ?? 'not applicable'}\nWeek: ${o.scope.week_key ?? 'unspecified'}\n\nShared contract: ${CONTRACT_URL}\n\n1. Retrieve the approved, versioned instructions and canonical task record through authenticated HTTPS. No instruction-service version or record URL has been supplied to this preview. Stop if either is unavailable.\n2. Verify scope, permissions, and the shared execution claim before starting. Do not run concurrently with another worker or create another task/status record.\n3. Current teacher scope: compare Agenda with Infinite Campus. Schoology and assignment documents are already set. Do not execute older parent-page comparison instructions from a legacy fixture. Review grading evidence or roster changes only within the approved workflow scope. Preserve history.\n4. Record the actual destination evidence, check time, and next action in that same canonical record. Tests alone do not establish live verification.\n\nDo not paste student data or credentials into public pages, source code, or this handoff. This preview uses fictional identities; never execute against them.`;
}
