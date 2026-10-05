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
vm.runInContext(source.slice(source.indexOf('function morningTotals('),source.indexOf('function renderTodayLessons(')),context);
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

test('changing lesson during generation cannot attach a draft to the new selection',async()=>{
  const row=student();const old={feedback:{students:[row]}};context.work.ui.form=old;
  context.postJSON=async()=>{context.work.seq++;context.work.ui.form={feedback:{students:[]}};return {id:'wrong-scope'};};
  await context.feedbackAction({form_id:'form'},row,'draft');
  assert.equal(row.draft.id,'fictional-draft');
  assert.equal(context.work.ui.form.feedback.students.length,0);
});
