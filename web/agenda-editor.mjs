// Presentation only. A successful capability read is not a saved edit.
export function saveAccessMessage(error){
 const messages={
  agenda_writer_permission_or_scope_denied:'Saving needs Agenda Editor access',
  agenda_override_needs_review:'This date has a separate Dashboard entry; its save target needs review',
  agenda_day_not_writable:'This class has no weekly Agenda column for this date',
  agenda_formula_not_writable:'This lesson uses a protected formula; its save target needs review',
  agenda_layout_changed:'The Agenda layout changed; reload before saving',
 };
 return messages[error]||'Saving unavailable for this date';
}

export function editorStatus(loaded,access){
 const ready=access.filter(a=>a?.revision).length;
 const pending=access.filter(a=>!a).length;
 if(!loaded)return {ready:0,pending:0,note:'Load an Agenda date to write plans and teaching notes',pill:'Agenda loading · Infinite Campus uses the school browser'};
 if(ready)return {ready,pending,note:'Write here, then Save to Agenda · '+ready+' of '+access.length+' classes ready',pill:'Dashboard → Agenda editing · Infinite Campus uses the school browser'};
 if(pending)return {ready,pending,note:'Checking Agenda save access · your drafts stay here',pill:'Agenda loaded · checking editing access'};
 return {ready,pending,note:'Drafts are not saved yet · see each class for its save status',pill:'Agenda loaded · saving needs attention'};
}
