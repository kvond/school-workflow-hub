// In-memory preview review only. No storage, transport, or write permission.
export function describeDraft(draft){
 return {
  title:typeof draft.title==='string'?draft.title:'',
  plan:typeof draft.plan==='string'?draft.plan:'',
  notes:typeof draft.notes==='string'?draft.notes:'',
  sourceRevision:draft.sourceRevision??null,
  sourceObservedAt:draft.sourceObservedAt??null,
  incoming:draft.incomingSource?{...draft.incomingSource}:null,
  state:draft.incomingSource?'review_needed':draft.dirty?'unsynced':'empty',
 };
}
export function stagePreviewNotes(draft,lesson){
 if(!lesson||typeof lesson.lesson_key!=='string'||!lesson.lesson_key.startsWith('lesson:synthetic:')||typeof lesson.source_revision!=='string'||typeof lesson.notes!=='string'||typeof lesson.title!=='string')throw Error('Invalid fictional source');
 const incoming={lessonKey:lesson.lesson_key,title:lesson.title,notes:lesson.notes,revision:lesson.source_revision,observedAt:lesson.observed_at};
 if(draft.dirty){draft.incomingSource=incoming;return 'review_needed';}
 draft.title=lesson.title;draft.notes=lesson.notes;draft.sourceRevision=lesson.source_revision;draft.sourceObservedAt=lesson.observed_at;draft.sourceLessonKey=lesson.lesson_key;draft.dirty=true;return 'unsynced';
}
