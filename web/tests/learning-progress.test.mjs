import test from 'node:test';
import assert from 'node:assert/strict';
import {renderLearningProgress} from '../learning-progress.mjs';

class Element {
 constructor(doc,tag){this.ownerDocument=doc;this.tagName=tag;this.children=[];this.textContent='';this.attributes={};this.value='0';this.style={};}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(key,value){this.attributes[key]=value;}
 addEventListener(){}
 get allText(){return this.textContent+this.children.map(c=>c.allText).join(' ');}
}
function render(rubric_version,understanding){
 const doc={createElement:tag=>new Element(doc,tag),createElementNS:(_,tag)=>new Element(doc,tag)};
 const root=new Element(doc,'div');
 renderLearningProgress(root,{class_key:'biology',provenance:'synthetic',series:[{
  learner_key:'fictional-learner',concept_label:'Energy',stage:'final',rubric_version,
  points:[{date:'2026-09-01',understanding,explanation:null,quote:'Fictional answer.'}]
 }]},{learnerKey:'fictional-learner',classKey:'biology',provenance:'synthetic'});
 return root;
}
const all=n=>[n,...n.children.flatMap(all)];
test('legacy history retains its three-level axis and Almost there point',()=>{
 const root=render('legacy/v1','Almost there');
 assert.match(root.allText,/Almost there/);assert.doesNotMatch(root.allText,/Beginning|Extending/);
 assert.equal(all(root).filter(n=>n.tagName==='circle').length,1);
});
test('four-level history uses its versioned axis without a separate explanation chart',()=>{
 const root=render('critical-aspect-4/v1','Extending');
 assert.match(root.allText,/Beginning/);assert.match(root.allText,/Extending/);
 assert.equal(all(root).filter(n=>n.tagName==='svg').length,1);
 assert.equal(all(root).filter(n=>n.tagName==='circle').length,1);
});
