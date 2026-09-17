// Fictional DOM test. No browser, no network, no private data.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fillDirections,directionsText,ROWS,mountControlPanel,loadDirections} from '../control-panel.mjs';

class Element extends EventTarget{
 constructor(doc,tag){super();this.ownerDocument=doc;this.tagName=tag;this.children=[];this.attributes={};this.textContent='';this.hidden=false;this.disabled=false;this.type='';}
 append(...c){for(const n of c){n.parentNode=this;this.children.push(n);}}
 replaceChildren(...c){this.children=[];this.append(...c);}
 setAttribute(k,v){this.attributes[k]=v;}
 get all(){return [this,...this.children.flatMap(c=>c.all)];}
 get allText(){return this.textContent+' '+this.children.map(c=>c.allText).join(' ');}
}
class Document{
 constructor(){this.nodes=new Map();for(const id of ['page-control','detail','detail-body','detail-title'])this.nodes.set(id,new Element(this,'div'));}
 createElement(tag){return new Element(this,tag);}
 getElementById(id){return this.nodes.get(id);}
}
globalThis.document=new Document();

const TEMPLATE={title:'Roster check and completion grading',cadence:'Weekday morning',sections:[
 {heading:'Access',body:'Verify {{school_account}} in {{timezone}}.'},
 {heading:'Current checkpoint',body:'{{checkpoint_override}}',optional:true,note:'Remove once the checkpoint passes.'},
 {heading:'Grading',body:'Missing work remains unscored through the full 14th day.'},
]};

// Unfilled values are named, never guessed.
{
 const filled=fillDirections(TEMPLATE,{school_account:'teacher@example.edu'});
 assert.deepEqual(filled.missing,['timezone']);
 assert.match(filled.sections[0].body,/teacher@example\.edu/);
 assert.match(filled.sections[0].body,/\{\{timezone\}\}/);
}

// An optional section with no value is dropped; with a value it is kept.
{
 const without=fillDirections(TEMPLATE,{});
 assert.deepEqual(without.sections.map(s=>s.heading),['Access','Grading']);
 const with_=fillDirections(TEMPLATE,{checkpoint_override:'This checkpoint only.'});
 assert.deepEqual(with_.sections.map(s=>s.heading),['Access','Current checkpoint','Grading']);
 assert.equal(with_.sections[1].body,'This checkpoint only.');
}

// The copyable text keeps every heading and body.
{
 const text=directionsText(fillDirections(TEMPLATE,{school_account:'a@b.edu',timezone:'ET'}));
 assert.match(text,/^ACCESS\n/);
 assert.match(text,/14th day/);
}

// The committed template carries no school identifiers. This is the repository rule
// in AGENTS.md made mechanical: values live in private configuration, not here.
{
 const raw=readFileSync(new URL('../run-directions.json',import.meta.url),'utf8');
 const parsed=JSON.parse(raw);
 assert.equal(parsed.schema_version,'hub-run-directions/v1');
 assert.doesNotMatch(raw,/docs\.google\.com|drive\.google\.com|infinitecampus|@[a-z0-9.-]+\.(?:edu|org|com)/i,
  'a real source URL or account address reached the template');
 assert.doesNotMatch(raw,/\b\d{5,}\b/,'a numeric school identifier reached the template');
 const bodies=parsed.directions.roster_completion.sections.map(s=>s.body).join('\n');
 for(const [,name] of bodies.matchAll(/\{\{([a-z_]+)\}\}/g))
  assert.ok(name in parsed.placeholders,'undeclared placeholder: '+name);
}

// Every row says what to bring, and no row starts anything on mount.
{
 assert.ok(ROWS.every(r=>r.bring && r.bring.length>10));
 assert.deepEqual(ROWS.filter(r=>r.kind==='run').map(r=>r.id),['roster','grading']);
 assert.deepEqual(ROWS.filter(r=>r.kind==='handoff').map(r=>r.id),['feedback','summary','research']);
 let fetched=0;
 const root=document.getElementById('page-control');
 mountControlPanel(root,{fetcher:async()=>{fetched++;return {ok:true,json:async()=>({})};}});
 assert.equal(fetched,0,'mounting must not fetch or run anything');
 for(const row of ROWS)assert.match(root.allText,new RegExp(row.label));
 assert.match(root.allText,/Nothing on it runs unattended/);
 const buttons=root.all.filter(n=>n.tagName==='button');
 assert.equal(buttons.length,ROWS.length);
 assert.ok(buttons.every(b=>!b.disabled));
}

// Where the private values come from. Hosted configuration reaches every computer
// she signs in from; the local file is a development fallback and is not served.
{
 const TEMPLATE_BODY={schema_version:'hub-run-directions/v1'};
 const reply=(ok,body)=>({ok,json:async()=>body});
 const record=[];
 const fetcherFor=map=>async url=>{record.push(url);
  if(url in map){const r=map[url];if(r==='throw')throw Error('offline');return r;}
  return {ok:false,json:async()=>({})};};

 // Hosted values win, and the local file is not even requested.
 record.length=0;
 const hosted=await loadDirections(fetcherFor({
  './run-directions.json':reply(true,TEMPLATE_BODY),
  './api/run-directions':reply(true,{values:{timezone:'America/New_York'},missing:[]}),
  './run-directions.local.json':reply(true,{timezone:'STALE'}),
 }));
 assert.deepEqual(hosted.values,{timezone:'America/New_York'});
 assert.ok(!record.includes('./run-directions.local.json'),'local file read despite hosted values');

 // The endpoint being unreachable falls back rather than failing the dialog.
 const offline=await loadDirections(fetcherFor({
  './run-directions.json':reply(true,TEMPLATE_BODY),
  './api/run-directions':'throw',
  './run-directions.local.json':reply(true,{timezone:'ET'}),
 }));
 assert.deepEqual(offline.values,{timezone:'ET'});

 // So does the endpoint answering without values, which is what an unconfigured
 // service does.
 const empty=await loadDirections(fetcherFor({
  './run-directions.json':reply(true,TEMPLATE_BODY),
  './api/run-directions':reply(false,{error:'sign_in_required'}),
  './run-directions.local.json':reply(true,{timezone:'ET'}),
 }));
 assert.deepEqual(empty.values,{timezone:'ET'});

 // With neither source the template still loads, so the dialog can name what is
 // missing instead of showing nothing.
 const none=await loadDirections(fetcherFor({'./run-directions.json':reply(true,TEMPLATE_BODY)}));
 assert.deepEqual(none.values,{});
 assert.equal(none.template.schema_version,'hub-run-directions/v1');

 // A missing template is the one condition worth failing on.
 await assert.rejects(loadDirections(fetcherFor({})));
}

console.log('control-panel: all checks passed');
