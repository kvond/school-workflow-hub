// Page 00 · Control panel. This page starts nothing by itself. It shows what each
// action needs, names what must be open before you pull it, and hands over the
// directions. School identifiers are never stored here: the directions template
// carries {{placeholders}} and private configuration fills them.

const PLACEHOLDER=/\{\{([a-z_]+)\}\}/g;

export const ROWS=[
 {id:'roster',kind:'run',label:'Roster check',directions:'roster_completion',
  bring:'Infinite Campus signed in on the school browser, roster open for the section.'},
 {id:'grading',kind:'run',label:'Completion grading',directions:'roster_completion',
  bring:'Infinite Campus signed in, gradebook open for that section and term.'},
 {id:'feedback',kind:'handoff',label:'Student feedback',page:'feedback',
  bring:'The feedback chat account, in its own browser profile, and the lesson folder to attach.'},
 {id:'summary',kind:'handoff',label:'Class summary',page:'summary',
  bring:'The summary chat account, in its own browser profile, and the compiled records.'},
 {id:'research',kind:'handoff',label:'Research coding',page:'research',
  bring:'The summary chat account, and approved records only. Collection stays disabled.'},
];

// Substitute private values into the template. Anything still unfilled is named
// rather than guessed, and an optional section with no value is dropped.
export function fillDirections(template,values={}){
 const missing=new Set(),sections=[];
 for(const section of template.sections||[]){
  const raw=String(section.body||'');
  const bare=raw.trim().match(/^\{\{([a-z_]+)\}\}$/);
  if(section.optional&&bare&&!values[bare[1]])continue;
  const body=raw.replace(PLACEHOLDER,(match,name)=>{
   if(values[name]===undefined||values[name]===''){missing.add(name);return match;}
   return String(values[name]);
  });
  sections.push({heading:section.heading,body,note:section.note});
 }
 return {sections,missing:[...missing].sort()};
}

export function directionsText(filled){
 return filled.sections.map(s=>s.heading.toUpperCase()+'\n'+s.body).join('\n\n');
}

function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function button(text,fn,disabled=false){const b=node('button',text);b.type='button';b.disabled=disabled;if(fn)b.addEventListener('click',fn);return b;}

async function loadDirections(fetcher){
 const template=await fetcher('./run-directions.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('template');return r.json();});
 let values={};
 try{
  const local=await fetcher('./run-directions.local.json',{cache:'no-store'});
  if(local.ok)values=await local.json();
 }catch{/* private values are optional; the panel says which are missing */}
 return {template,values};
}

export function showDirections(workflow,{fetcher=fetch}={}){
 const title=document.getElementById('detail-title'),body=document.getElementById('detail-body');
 title.textContent='Directions · '+workflow.replace(/_/g,' ');
 body.replaceChildren(node('p','Loading directions…'));
 document.getElementById('detail').showModal();
 return loadDirections(fetcher).then(({template,values})=>{
  const entry=template.directions?.[workflow];
  if(!entry)throw Error('unknown workflow');
  const filled=fillDirections(entry,values);
  const text=directionsText(filled);
  const area=node('textarea');area.readOnly=true;area.value=text;area.setAttribute('aria-label','Scheduled task directions');
  const status=node('p','','hint');
  const copy=button('Copy directions',async()=>{
   try{await navigator.clipboard.writeText(text);status.textContent='Copied. Paste these into the scheduled task.';}
   catch{area.focus();area.select();status.textContent='Use your browser’s copy command on the selected text.';}
  });
  const parts=[node('p',entry.title+' · '+(entry.cadence||'on demand')+'. These are directions for a task you set up yourself. This page does not start it.')];
  if(filled.missing.length)parts.push(node('p','Unfilled private values: '+filled.missing.join(', ')+'. They stay out of the repository; supply them in run-directions.local.json on this machine.','error'));
  for(const section of filled.sections)if(section.note)parts.push(node('p',section.heading+': '+section.note,'hint'));
  body.replaceChildren(...parts,area,copy,status);
  return filled;
 }).catch(()=>{
  body.replaceChildren(node('p','Directions could not be loaded. Nothing was started.','error'));
 });
}

export function mountControlPanel(root,{fetcher=fetch,onPage}={}){
 if(!root)return root;
 root.replaceChildren();
 root.append(node('h2','Control panel'));
 root.append(node('p','Everything that starts a run lives here. Pages 01 to 04 are what you read; this page is what you press. Nothing on it runs unattended.','hint'));

 const runs=node('section',undefined,'surface wide');
 runs.append(node('h3','Start a run'),node('p','These need your signed-in school browser. The directions open in a dialog; paste them into the scheduled task you keep in your school assistant.'));
 const runRow=node('div',undefined,'action-row');
 const handoffs=node('section',undefined,'surface wide');
 handoffs.append(node('h3','Hand to a chat'),node('p','These need judgment, so they are prepared here and composed in a free chat account. The result comes back for validation before anything is stored.'));
 const handoffRow=node('div',undefined,'action-row');

 for(const row of ROWS){
  const target=row.kind==='run'?runRow:handoffRow;
  const wrap=node('div',undefined,'surface');
  wrap.append(node('strong',row.label));
  wrap.append(node('p','Bring: '+row.bring,'hint'));
  if(row.kind==='run')wrap.append(button('Open directions',()=>showDirections(row.directions,{fetcher})));
  else wrap.append(button('Go to '+row.label,()=>{if(onPage)onPage(row.page);else location.hash='#'+row.page;}));
  target.append(wrap);
 }
 runs.append(runRow);handoffs.append(handoffRow);
 root.append(runs,handoffs);
 root.append(node('p','Run state, waiting counts and last-run times appear here once their sources are connected. Until then this page shows what to do, not what has happened.','hint'));
 return root;
}
