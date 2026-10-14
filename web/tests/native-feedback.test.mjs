import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
const source = readFileSync(new URL('../../src/hub_v2/static/app.js', import.meta.url), 'utf8');
class Element {
  constructor(tag, props={}, children=[]) { this.tag=tag; this.props=props; this.children=[]; this.append(...children); }
  append(...children) { this.children.push(...children.flat().filter(v => v !== null && v !== false)); }
  get text() { return (this.props.text || '') + this.children.map(v => v instanceof Element ? v.text : String(v)).join(' '); }
  get all() { return [this, ...this.children.flatMap(v => v instanceof Element ? v.all : [])]; }
}
const el=(tag,props,...children)=>new Element(tag,props,children);
const context = vm.createContext({el, note:(text)=>el('p',{text}), link:(text,href)=>el('a',{text,href}),
  cockpitPanel:(title,...children)=>el('section',{},title,...children),
  when:v=>v, work:{seq:1, cls:'biology',week:'Wk 04',ui:{}}, renderWork(){}, Set});
const morningStart = source.indexOf('function morningTotals(');
const morningEnd = source.indexOf('function issueAction(');
assert.ok(morningStart >= 0 && morningEnd > morningStart, 'morning receipt helpers must remain bounded');
vm.runInContext(source.slice(morningStart, morningEnd), context);
vm.runInContext(source.slice(source.indexOf('async function feedbackAction('),source.indexOf('function studentRow(')),context);
const student=()=>({name:'Ana R.',section:'A4',file_id:'fictional-source',hold:'',draft:{id:'fictional-draft',saved_at:null,source_modified:'today',report:{student_summary:'You explain carbon.',question:'How does it move?',aspects:[{name:'Carbon moves',current_level:'Developing'}],teacher_notes:['Private note']}}});

test('failed or incomplete morning receipts never show an all-clear',()=>{
  for (const report of [{code:'hub_unavailable',items:[]},{code:'running',items:[]},
    {code:'finished',items:[{assignment:'Lesson',code:'mapping_required',filled:0,raised:0,kept:0,left:0}]}]) {
    const panel=context.renderMorningCockpit(report,{});
    assert.doesNotMatch(panel.text,/Nothing .* unresolved/);
    assert.match(panel.text,/not verified|did not finish/);
    assert.match(context.renderNeedsYou(report,[]).text,/not verified|did not finish/);
  }
  assert.match(context.renderMorningCockpit({code:'finished',items:[]},{}).text,/completed receipt/);
});

test('feedback has separate save and print controls, never a grade-save action', async()=>{
  const row=student();context.work.ui.form={feedback:{students:[row]}};
  let panel=context.feedbackPanel({form_id:'form'});
  assert.ok(panel.all.some(n=>n.props.text==='Save private draft'||n.text==='Save private draft'));
  assert.ok(!panel.all.some(n=>n.tag==='a'&&n.props.href));
  const requests=[];context.postJSON=async(url,body)=>{requests.push({url,body});return {...row.draft,saved_at:'now',pdf_url:'/v2/api/feedback/pdf?id=fictional'};};
  await context.feedbackAction({form_id:'form'},row,'save');
  assert.equal(requests[0].url,'api/feedback/save');
  assert.equal(JSON.stringify(requests[0].body),JSON.stringify({class:'biology',week:'Wk 04',form:'form',file:'fictional-source',id:'fictional-draft'}));
  panel=context.feedbackPanel({form_id:'form'});
  assert.equal(panel.all.filter(n=>n.tag==='a')[0].props.href,'/v2/api/feedback/pdf?id=fictional');
  assert.ok(!requests.some(r=>/assessment-save|record|morning/.test(r.url)));
});

test('only confirmed receipt rows contribute to the saved count',()=>{
  const panel=context.renderMorningCockpit({code:'save_uncertain',items:[
    {assignment:'Confirmed lesson',code:'read_back',filled:2,raised:1,kept:3,left:0},
    {assignment:'Uncertain lesson',code:'save_uncertain',filled:20,raised:10,kept:0,left:0},
  ]},{});
  assert.equal(panel.all.find(n=>n.props.class==='cockpit-number').props.text,'6');
  assert.match(panel.text,/Uncertain lesson/);
  assert.match(panel.text,/inspect the scores, correct\/save only if needed, and reload/);
  assert.doesNotMatch(panel.text,/22 new|11 raised/);
});

test('no source scores and no receipt are not described as a successful IC comparison',()=>{
  for (const details of [{}, {class_name:'Biology',week:'Wk 04',reasons:{exempt:0,flag:0,excused:0,check:0,name:0,missing:0}}]) {
    const report={code:'finished',items:[{assignment:'Lesson',code:'no_scores',filled:0,raised:0,kept:0,left:0,...details}]};
    assert.match(context.renderMorningCockpit(report,{}).text,/not entered or compared in IC/);
    const followup=context.renderNeedsYou(report).text;
    assert.match(followup,/no IC comparison is confirmed/);
    assert.doesNotMatch(followup,/Biology · Wk 04 · Lesson/);
    assert.doesNotMatch(followup,/No grade rows were left out|No grade rows need attention|older receipt|Detailed follow-up is unavailable/);
  }
  assert.match(context.renderNeedsYou(null).text,/not verified/);
});

test('changing lesson during generation cannot attach a draft to the new selection',async()=>{
  const row=student();const old={feedback:{students:[row]}};context.work.ui.form=old;
  context.postJSON=async()=>{context.work.seq++;context.work.ui.form={feedback:{students:[]}};return {id:'wrong-scope'};};
  await context.feedbackAction({form_id:'form'},row,'draft');
  assert.equal(row.draft.id,'fictional-draft');
  assert.equal(context.work.ui.form.feedback.students.length,0);
});
