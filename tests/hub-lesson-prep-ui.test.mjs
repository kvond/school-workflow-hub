import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; }
  set textContent(v) { this.text = String(v); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(c => c instanceof Element ? c.textContent : String(c)).join(''); }
  append(...c) { this.children.push(...c); }
  replaceChildren(...c) { this.children = c; }
  setAttribute(k, v) { this[k] = v; }
  addEventListener() {}
}
const source = readFileSync(new URL('../src/hub_v2/static/app.js', import.meta.url), 'utf8');
const before = source.slice(0, source.indexOf('// ---------- Hub v3'));
const helpers = source.slice(source.indexOf('function block('), source.indexOf('// ---------- Editing a lesson'));
const render = source.slice(source.indexOf('function renderPlans('), source.indexOf('// ---------- Student work ----------'));
const nodes = new Map();
const context = vm.createContext({Node: Element, Intl, Date,
  document: {createElement: tag => new Element(tag), getElementById: id => {
    if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id);
  }}, plans: {editing: null}, renderPreviousLessons: () => null, classNotesPanel: () => new Element('section'),
  dayEditButton: () => null, lessonNotesPanel: () => null,
  fetch: () => assert.fail('Rendering prep must not start any request or write')});
vm.runInContext(before + helpers + render + '\nglobalThis.ui = {renderPrepNotes, renderNextPrep, renderLesson, renderPlans};', context);
const ui = context.ui;
const walk = n => [n, ...n.children.filter(c => c instanceof Element).flatMap(walk)];
const prep = () => ({items: [{label:'BUY', text:'yeast'}, {label:'PRINT', text:'Student CER'}],
  tracks: [], links: [{label:'PRINT Student CER', url:'https://example.test/cer'}], issues: [], link_status:'not_checked'});
const lesson = () => ({row:3, week:'Wk 09', topic:'Lab', plans:[], materials:[], forms:[], prep_notes:prep()});

test('prep stays beneath each lesson, with readable categories and original clickable links in all subjects', () => {
  for (const key of ['biology','anatomy','forensics']) {
    const card = ui.renderLesson({key}, lesson(), 'Mon');
    const panel = walk(card).find(n => n.className === 'lesson-prep');
    assert.match(panel.textContent, /Prep notesBUYyeastPRINTStudent CER/);
    assert.equal(walk(panel).find(n => n.tagName === 'a').href, 'https://example.test/cer');
    assert.match(panel.textContent, /availability has not been checked/);
    assert.doesNotMatch(panel.textContent, /Demo|Independent|Ready|Form verified/);
    assert.ok(card.children.indexOf(panel) > card.children.findIndex(n => n.textContent?.includes('Agenda topic')));
  }
});

test('empty prep does not add headings and older responses preserve their source text', () => {
  assert.equal(ui.renderPrepNotes({}), null);
  assert.match(ui.renderPrepNotes({prep:'Keep goggles'}).textContent, /Prep notesKeep goggles/);
});

test('explicit Biology tracks keep source links and the missing-form limitation', () => {
  const item = lesson();
  item.prep_notes.tracks = [{label:'Demo', items:[{label:'PULL',text:'trays'}], links:[]},
    {label:'Independent Lab', items:[], links:[{label:'Student deck',url:'https://example.test/lab'}]}];
  item.prep_notes.issues = ['No Turn-in Form is linked in this Agenda row. Independent Lab release is unverified.'];
  const panel = ui.renderPrepNotes(item);
  assert.match(panel.textContent, /DemoPULLtraysIndependent LabStudent deck/);
  assert.match(panel.textContent, /release is unverified/);
  assert.equal(walk(panel).filter(n => n.tagName === 'a').length, 2);
});

test('next week has explicit dated provenance, closures and source links without moving the current plan', () => {
  const future = {...lesson(), kind:'closed', edit_url:'https://example.test/agenda#range=A3'};
  const panel = ui.renderNextPrep({next_prep:{week:'Wk 09',monday:'2026-10-26',closures:'Friday = B day',lessons:[future]}});
  assert.match(panel.textContent, /Prep for next weekWk 09 · Week of 2026-10-26/);
  assert.match(panel.textContent, /Friday = B day/);
  assert.match(panel.textContent, /unplanned or no class/);
  assert.equal(walk(panel).find(n => n.textContent === 'Agenda source ↗').href, future.edit_url);
});

test('unavailable next-week rows are distinct from empty prep and unsupported links stay explicit', () => {
  assert.equal(ui.renderNextPrep({next_prep:{lessons:[],problem:null}}), null);
  const panel = ui.renderNextPrep({next_prep:{monday:'2026-10-26',lessons:[],problem:'No lesson row; prep is unverified.'}});
  assert.match(panel.textContent, /prep is unverified/);
  const item = lesson(); item.prep_notes.links = []; item.prep_notes.issues = ['A prep link has an unsupported address.'];
  assert.equal(walk(ui.renderPrepNotes(item)).filter(n => n.tagName === 'a').length, 0);
  assert.match(ui.renderPrepNotes(item).textContent, /unsupported address/);
});

test('rendering several lessons adds only one future notice per class and deduplicates the same future source row', () => {
  const current = lesson(), other = {...lesson(), row:4};
  const future = {...lesson(), kind:'lesson'};
  ui.renderPlans({week:{label:'Wk 08',monday:'2026-10-19'},weekday:'Mon',problems:[],classes:[
    {key:'biology',name:'Biology',lessons:[current,other],next_prep:{week:'Wk 09',monday:'2026-10-26',lessons:[future,future]}}]});
  const root = nodes.get('cards');
  assert.equal(walk(root).filter(n => n.className === 'next-prep').length, 1);
  assert.equal(walk(root).filter(n => n.className === 'future-lesson').length, 1);
  assert.equal(walk(root).filter(n => n.className === 'lesson-prep').length, 3);
  assert.equal(current.week, 'Wk 09');
});
