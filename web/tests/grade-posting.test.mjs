import test from 'node:test';
import assert from 'node:assert/strict';
import {buildPostingReview,sourceReviewFromData,assertPostingReviewCurrent,renderPostingReview} from '../grade-posting.mjs';

const revision='a'.repeat(64);
function row(overrides={}){return {record_id:'one',class_key:'forensics',section_id:'section-a',section_name:'Period A',student_id:'student-one',display_name:'Fictional Alex',assignment_key:'assignment-one',assignment_title:'Fictional evidence',source_revision:'submission-r1',status:'assessed',hold_reason:'',content_score:60/13,content_score_exact:'60/13',revision_score:null,ic_posting_status:'not_posted',answers:[],...overrides};}
function source(rows=[row()]){return {schema_version:'hub-grading-records/v1',provenance:'source_read',external_writes:0,source_revision:revision,checked_at:'2026-09-17T12:00:00Z',submissions:rows,summaries:[]};}
const scope={classKey:'forensics',sectionId:'matched'};
class Element{
 constructor(document,tag){this.ownerDocument=document;this.tagName=tag;this.children=[];this.textContent='';this.attributes={};}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(key,value){this.attributes[key]=value;}
 get allText(){return this.textContent+' '+this.children.map(child=>child.allText).join(' ');}
}
const document={createElement(tag){return new Element(document,tag);}};

test('requires a verified source and exact valid scope',()=>{
 for(const change of [{provenance:'synthetic'},{external_writes:1},{source_revision:'not-a-hash'},{checked_at:'unknown'}])assert.throws(()=>buildPostingReview({...source(),...change},scope));
 for(const change of [{classKey:'other'},{sectionId:'section-z'},{sectionId:null}])assert.throws(()=>buildPostingReview(source(),{...scope,...change}));
 assert.throws(()=>buildPostingReview(source([row(),row()]),scope));
});
test('uses exact class and section with unmatched records available only in their selected view',()=>{
 const data=source([row(),row({record_id:'two',student_id:'two',section_id:'section-b'}),row({record_id:'three',class_key:'biology'}),row({record_id:'four',section_id:'',student_id:''})]);
 assert.deepEqual(buildPostingReview(data,{...scope,sectionId:'section-a'}).record_ids,['one']);
 assert.deepEqual(buildPostingReview(data,scope).record_ids,['one','two']);
 assert.deepEqual(buildPostingReview(data,{...scope,sectionId:''}).record_ids,['four']);
 assert.equal(buildPostingReview(data,{...scope,sectionId:'all'}).rows.length,3);
});
test('conserves exact scores and scopes in an immutable private-memory snapshot',()=>{
 const data=source(),review=buildPostingReview(data,scope),result=review.rows[0];
 assert.equal(result.scores.content_score.value,60/13);assert.equal(result.scores.content_score.exact,'60/13');
 assert.equal(result.scores.revision_score.value,null);assert.deepEqual(result.scores.revision_score.exclusion_reasons,['score_blank']);
 assert.equal(result.student_id,'student-one');assert.equal(result.assignment_key,'assignment-one');assert.equal(result.source_revision,'submission-r1');
 assert.equal(review.source_revision,revision);assert.equal(review.external_writes,0);assert.equal(review.posting_enabled,false);assert.equal(result.destination_verified,false);
 data.submissions[0].content_score=2;data.submissions[0].student_id='changed';assert.equal(result.scores.content_score.value,60/13);assert.equal(result.student_id,'student-one');
 assert.throws(()=>{review.record_ids.push('another');});assert.throws(()=>{result.scores.content_score.value=3;});
});
test('roster identities cannot replace IC IDs and held records cannot supply scores',()=>{
 for(const [change,reason] of [[{student_id:'',learner_key:'roster-one',included_in_counts:true},'student_id_missing'],[{section_id:''},'section_missing'],[{assignment_key:''},'assignment_key_missing'],[{hold_reason:'Teacher review'},'held'],[{status:'identity_unverified'},'identity_unverified'],[{status:'superseded'},'superseded']]){
  const result=buildPostingReview(source([row(change)]),{...scope,sectionId:'all'}).rows[0];
  assert.ok(result.exclusion_reasons.includes(reason));assert.equal(result.scores.content_score.value,null);
 }
});
test('preserves the official Student Number and raw content mapping and points without inference',()=>{
 const data=source([row({student_id:'0012345',ic_assignment_id:'IC_RESOURCE:123;SCORE_GROUP:456;TERM:789;TASK:123;CODE:F01',content_points_possible:'10.0',revision_points_possible:' 5 ',revision_score:4})]);
 const review=sourceReviewFromData(data,scope),record=review.rows[0];
 assert.equal(record.student_id,'0012345');assert.equal(record.ic_assignment_id,data.submissions[0].ic_assignment_id);assert.equal(record.content_points_possible,'10.0');assert.equal(record.revision_points_possible,' 5 ');assert.equal(record.scores.revision_score.value,4);
 assert.equal(record.destination_verified,false);assert.equal(Object.hasOwn(record,'ic_revision_assignment_id'),false);
 for(const field of ['ic_assignment_id','content_points_possible','revision_points_possible']){
  const changed=structuredClone(data);changed.submissions[0][field]+='changed';assert.throws(()=>assertPostingReviewCurrent(review,changed),/records changed/);
 }
 const absent=buildPostingReview(source(),scope).rows[0];assert.equal(absent.ic_assignment_id,'');assert.equal(absent.content_points_possible,'');assert.equal(absent.revision_points_possible,'');
});
test('blank and invalid grades remain unscored while actual zero is preserved',()=>{
 for(const value of [null,undefined,'',NaN,Infinity,'8',-1,11]){
  const result=buildPostingReview(source([row({content_score:value})]),scope).rows[0];assert.equal(result.scores.content_score.value,null);assert.ok(result.scores.content_score.exclusion_reasons.length);
 }
 assert.equal(buildPostingReview(source([row({content_score:0})]),scope).rows[0].scores.content_score.value,0);
});
test('held status suppresses scores even when its hold explanation is absent',()=>{
 for(const status of ['held','held_identity_review','hold','on_hold']){
  const review=buildPostingReview(source([row(),row({record_id:'held',status,hold_reason:''})]),scope);
  const current=review.rows.find(row=>row.record_id==='one'),held=review.rows.find(row=>row.record_id==='held');
  assert.equal(current.scores.content_score.value,60/13);assert.deepEqual(current.exclusion_reasons,[]);
  assert.equal(held.scores.content_score.value,null);assert.ok(held.exclusion_reasons.includes('held'));
  const target=new Element(document,'div');renderPostingReview(target,review);assert.ok(target.allText.includes(`Recorded assessment status: ${status}.`));
 }
});
test('duplicate active student assignment records require review but superseded history does not block current record',()=>{
 let review=buildPostingReview(source([row(),row({record_id:'two'})]),scope);
 assert.ok(review.rows.every(row=>row.exclusion_reasons.includes('duplicate_target')));
 review=buildPostingReview(source([row(),row({record_id:'two',status:'superseded'})]),scope);
 assert.deepEqual(review.rows[0].exclusion_reasons,[]);assert.equal(review.rows[0].scores.content_score.value,60/13);
});
test('fresh source check rejects changed revision, identities, scores or selected records',()=>{
 const review=buildPostingReview(source(),scope);
 assert.equal(assertPostingReviewCurrent(review,{...source(),checked_at:'2026-09-17T12:01:00Z'}).checked_at,'2026-09-17T12:01:00Z');
 for(const fresh of [{...source(),source_revision:'b'.repeat(64)},source([row({student_id:'two'})]),source([row({content_score:9})]),source([])])assert.throws(()=>assertPostingReviewCurrent(review,fresh));
});
test('renders literal text, unrounded scores and recorded posting state without claiming success',()=>{
 const target=new Element(document,'div');
 renderPostingReview(target,buildPostingReview(source([row({display_name:'<script>Fictional</script>',ic_posting_status:'posted'})]),scope));
 assert.match(target.allText,/<script>Fictional<\/script>/);assert.ok(target.allText.includes(`${60/13} / 10`));
 assert.match(target.allText,/Recorded IC status: posted. This is not a verified gradebook readback/);
 assert.match(target.allText,/assignment mapping has not been verified/);assert.match(target.allText,/has not posted grades/);
});
