import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this._text = ''; this.value = ''; this.checked = false; this.disabled = false; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(x => x?.textContent ?? String(x)).join(''); }
  append(...x) { this.children.push(...x); } replaceChildren(...x) { this._text = ''; this.children = x; }
}
const nodes = root => [root, ...root.children.filter(x => x instanceof Element).flatMap(nodes)];
const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const writer = readFileSync(new URL('../src/hub_v2/static/feedback-delivery.js', import.meta.url), 'utf8');
const packetID = 'dailyfeedbackpacket_' + 'a'.repeat(64), sha = 'b'.repeat(64);
const response = value => ({ok: true, json: async () => value});
function renderer(fetch = async () => {throw Error('unexpected fetch');}) {
  const el = (tag, attrs = {}, ...kids) => {const n = new Element(tag); Object.assign(n, attrs); if (attrs.text) n.textContent = attrs.text; n.append(...kids); return n;};
  const context = {el, note: text => el('p', {text}), cockpitPanel: title => el('section', {}, el('h2', {text: title})),
    link: (label, url) => el('a', {text: label, href: url}), cockpit: {}, todayISO: () => '2032-01-21', fetch,
    window: {open: () => ({addEventListener() {}, close() {}, location: null})}, URL: {createObjectURL: () => 'blob:fake', revokeObjectURL() {}},
    setTimeout() {}, loadToday() {}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('async function printTodayFeedback('), source.indexOf('function renderLearningReport(')) + '\nthis.ui={renderTodayFeedback,printTodayFeedback};', context);
  return context.ui;
}
const file = id => ({id, name: 'student feedback.pdf', url: 'https://drive.google.com/open?id=' + id, course_name: 'Forensics', feedback_day: '2032-01-20'});
const data = overrides => ({date: '2032-01-20', requested_date: '2032-01-21', files: [file('REPORT1'),file('REPORT2')],
  pending_files: [], current_sources_verified: true, daily_packet: {status: 'ready', configured: true, date: '2032-01-20',
    id: packetID, source_count: 2, pages: 4, pdf_url: '/v2/api/feedback/daily/pdf?id=' + packetID},
  delivery: {status: 'verified', printed_file_ids: ['REPORT1','REPORT2'], handed_file_ids: ['REPORT1'], pending_file_ids: []}, ...overrides});

test('recorded exact versions are compact historical reprints, unknown delivery remains visible pending', () => {
  const ui = renderer(), known = ui.renderTodayFeedback(data());
  assert.match(known.textContent, /No pending printing for these exact report versions/);
  assert.match(known.textContent, /recorded Printed.*1 are recorded Handed/);
  assert.equal(nodes(known).filter(n => n.tagName === 'button')[0].textContent, 'Reprint saved feedback — 2032-01-20');
  assert.ok(nodes(known).some(n => n.tagName === 'a' && n.href.includes('/daily/pdf')));
  const unknown = ui.renderTodayFeedback(data({delivery:{status:'unavailable',pending_file_ids:['REPORT1','REPORT2'],problem:'Private records unavailable.'},pending_files:[file('REPORT1'),file('REPORT2')]}));
  assert.match(unknown.textContent, /Private records unavailable.*2 pending report versions/);
  assert.doesNotMatch(unknown.textContent, /No pending printing/);
});

test('partial or older-date pending preview sends only exact pending IDs and never rebuilds the old daily packet', async () => {
  const previewID = 'feedbackprintpreview_' + 'b'.repeat(64);
  const preview = {schema:'hub-feedback-print-preview/v1',status:'ready',id:previewID,preview_only:true,
    daily_document_saved:false,delivery_changed:false,source_count:1,pages:2,
    name:'Feedback-print-preview-'+'b'.repeat(12)+'.pdf',pdf_url:'/v2/api/feedback/preview/pdf?id='+previewID};
  const calls = [], ui = renderer(async (url, options) => { calls.push([url, options]); return {ok:true,json:async()=>preview}; });
  const view = data({packet_matches_sources:false,pending_files:[file('REPORT2')],delivery:{status:'verified',pending_file_ids:['REPORT2'],printed_file_ids:['REPORT1'],handed_file_ids:[]}});
  const panel = ui.renderTodayFeedback(view), button = nodes(panel).find(n=>n.tagName==='button');
  assert.equal(button.textContent,'Print pending feedback (1)'); await button.onclick();
  assert.equal(calls.length,1); assert.equal(calls[0][0],'api/feedback/preview');
  assert.match(panel.textContent,/Print pending opens only the pending preview/);
  assert.deepEqual(JSON.parse(calls[0][1].body),{ids:['REPORT2']});
  assert.doesNotMatch(panel.textContent,/Daily feedback PDF ready|Print all feedback saves an updated version|Printing saves this day/); assert.match(panel.textContent,/Pending feedback preview ready/);
  assert.ok(nodes(panel).some(node=>node.tagName==='a'&&node.href===preview.pdf_url));
});

test('all pending current-day reports retain the existing saved daily-document print path', async () => {
  const calls = [], ui = renderer(async (url, options) => { calls.push([url, options]); return {ok:true,blob:async()=>({type:'application/pdf',size:10})}; });
  const view = data({pending_files:[file('REPORT1'),file('REPORT2')],packet_matches_sources:true,delivery:{status:'verified',pending_file_ids:['REPORT1','REPORT2'],printed_file_ids:[],handed_file_ids:[]}});
  const panel=ui.renderTodayFeedback(view); await nodes(panel).find(n=>n.tagName==='button').onclick();
  assert.equal(calls.length,1); assert.equal(calls[0][0],view.daily_packet.pdf_url);
  assert.match(panel.textContent,/Daily feedback PDF ready/);
});

function page(fetch) {
  const elements = Object.fromEntries(['through','handed','handed-packet','packet-status','prepare','status','review','attest','confirm'].map(id=>[id,new Element(id)]));
  elements.through.value='2026-10-11'; elements.handed.checked=true; elements['handed-packet'].value=packetID;
  const context={document:{getElementById:id=>elements[id],createElement:tag=>new Element(tag)},fetch,encodeURIComponent};
  vm.createContext(context);vm.runInContext(writer,context); return elements;
}
const packet = {status:'ready',id:packetID,date:'2026-10-11',source_count:5,individual_reports:5,legacy_packets:0,report_counts:{science_writing:5},pages:10};
const review = {schema:'hub-feedback-delivery/v1',status:'review',through:'2026-10-11',handed_packet_id:packetID,snapshot_sha256:sha,printed_count:203,handed_count:5,unknown_count:0};
test('normal teacher page reviews privately entered exact packet then confirms the same snapshot with separate marks', async () => {
  const calls=[], elements=page(async(url, options)=>{calls.push([url,options]);if(!options.method)return response(packet);const request=JSON.parse(options.body);return response(request.action==='prepare'?review:{...review,status:'recorded'});});
  await elements.prepare.onclick(); assert.equal(calls.length,2);
  assert.equal(calls[0][0],'/v2/api/feedback/delivery/packet?id='+packetID);
  assert.match(elements.review.textContent,/203 exact reports.*5 to record Handed/);
  elements.attest.checked=true; elements.attest.onchange(); await elements.confirm.onclick();
  const body=JSON.parse(calls[2][1].body); assert.equal(body.snapshot_sha256,sha); assert.equal(body.handed_packet_id,packetID);
  assert.equal(body.printed_attested,true);assert.equal(body.handed_attested,true);
  assert.match(elements.status.textContent,/203 exact Printed versions and 5 Handed versions recorded/);
  assert.equal(calls[2][1].credentials,'same-origin'); assert.equal(elements.confirm.disabled,true);
});
test('stale review and a different packet with matching counts cannot enable confirmation', async () => {
  let finish;const elements=page(async(url, options)=>!options.method?response(packet):new Promise(resolve=>{finish=resolve;}));
  const pending=elements.prepare.onclick(); await new Promise(resolve => setImmediate(resolve));
  elements.through.value='2026-10-12'; elements.through.onchange();finish(response(review));await pending;
  assert.equal(elements.attest.disabled,true);assert.equal(elements.confirm.disabled,true);assert.equal(elements.review.textContent,'');
  const other=page(async()=>response({...packet,id:'dailyfeedbackpacket_'+'c'.repeat(64)}));await other.prepare.onclick();
  assert.match(other.status.textContent,/was not verified/);assert.equal(other.confirm.disabled,true);
});
test('uncertain confirm clears exact review and never automatically retries or marks delivery on a print action', async () => {
  const calls=[], elements=page(async(url,options)=>{calls.push(url);if(!options.method)return response(packet);if(JSON.parse(options.body).action==='prepare')return response(review);throw Error('State readback unavailable');});
  await elements.prepare.onclick();elements.attest.checked=true;await elements.confirm.onclick();
  assert.equal(calls.length,3);assert.match(elements.status.textContent,/No automatic retry was made/);assert.equal(elements.confirm.disabled,true);
  assert.doesNotMatch(writer,/localStorage|chrome\.storage|service_token|d63e59/);
});

test('confirmation locks every selector until exact readback and restores controls after it', async () => {
  let finish; const elements=page(async(url,options)=>!options.method?response(packet):JSON.parse(options.body).action==='prepare'?response(review):new Promise(resolve=>{finish=resolve;}));
  await elements.prepare.onclick();elements.attest.checked=true; const pending=elements.confirm.onclick();
  for(const id of ['through','handed','handed-packet','attest']) assert.equal(elements[id].disabled,true);
  finish(response({...review,status:'recorded'}));await pending;
  assert.match(elements.status.textContent,/203 exact Printed versions/);
  for(const id of ['through','handed','handed-packet']) assert.equal(elements[id].disabled,false);
});

test('unverified older pending packet remains visibly blocked with original link and cannot enter preview IDs', () => {
  const ui=renderer(),blocked=file('BLOCKEDOLD'); const panel=ui.renderTodayFeedback(data({pending_source_problems:[{date:'2032-01-01',files:[blocked],problem:'Student-only source not verified.'}],delivery:{status:'verified',pending_file_ids:['BLOCKEDOLD'],printed_file_ids:['REPORT1','REPORT2'],handed_file_ids:[]}}));
  assert.match(panel.textContent,/pending versions need source verification.*Student-only source not verified/);
  assert.ok(nodes(panel).some(node=>node.tagName==='a'&&node.href===blocked.url));
});
