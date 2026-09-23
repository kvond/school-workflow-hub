const colors={'Beginning':['#FBE1CB','#7A3E0B'],'Developing':['#FFF0BA','#6A5300'],'Almost there':['#EFDF85','#655000'],'Proficient':['#C9E7D2','#125b45'],'Extending':['#E9E1F4','#4A3C6E'],'Names an idea':['#eef1fb','#4c5589'],'Connects ideas':['#e0effb','#22557f'],'Explains how or why':['#C9E7D2','#125b45']};
export function levelBadge(doc,level){const n=doc.createElement('span');n.className='understanding-badge';n.textContent=level||'Evidence unavailable';const c=colors[level]||['#edf0f3','#506074'];n.style.background=c[0];n.style.color=c[1];return n;}
function el(doc,tag,text){const n=doc.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
function svgNode(doc,tag,attrs,text){const n=doc.createElementNS('http://www.w3.org/2000/svg',tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;return n;}
function plot(root,points,dimension,rubricVersion){
 const doc=root.ownerDocument,levels=dimension==='understanding'?(rubricVersion==='critical-aspect-4/v1'?['Beginning','Developing','Proficient','Extending']:['Developing','Almost there','Proficient']):['Names an idea','Connects ideas','Explains how or why'];
 const card=el(doc,'section');card.className='learning-chart';card.append(el(doc,'h4',dimension==='understanding'?'My conceptual understanding':'How I explain concepts'));
 const svg=svgNode(doc,'svg',{viewBox:'0 0 640 200',role:'img','aria-label':(dimension==='understanding'?'Conceptual understanding':'Explanation')+' over time; exact evidence follows.'});
 const left=160,right=610; const dates=points.map(p=>Date.parse(p.date));const lo=Math.min(...dates),hi=Math.max(...dates);
 const step=levels.length>3?39:52,x=p=>lo===hi?(left+right)/2:left+(Date.parse(p.date)-lo)/(hi-lo)*(right-left),y=p=>142-levels.indexOf(p[dimension])*step;
 levels.forEach((level,i)=>{const yy=142-i*step;svg.append(svgNode(doc,'line',{x1:left,y1:yy,x2:right,y2:yy,stroke:'#d9e1e8'}),svgNode(doc,'text',{x:150,y:yy+5,'text-anchor':'end','font-size':12,fill:'#304b65'},level));});
 // Break the line at every missing observation.
 for(let i=1;i<points.length;i++)if(levels.includes(points[i-1][dimension])&&levels.includes(points[i][dimension]))svg.append(svgNode(doc,'line',{x1:x(points[i-1]),y1:y(points[i-1]),x2:x(points[i]),y2:y(points[i]),stroke:'#4b6176','stroke-width':2}));
 for(const p of points){if(levels.includes(p[dimension])){const dot=svgNode(doc,'circle',{cx:x(p),cy:y(p),r:6,fill:(colors[p[dimension]]||[])[1]||'#52697d'});dot.append(svgNode(doc,'title',{},p.date+' · '+p[dimension]+' · '+p.quote));svg.append(dot);}svg.append(svgNode(doc,'text',{x:x(p),y:180,'text-anchor':'middle','font-size':11,fill:'#405870'},p.date.slice(5)));}
 card.append(svg);root.append(card);
}
export function renderLearningProgress(root,history,{learnerKey,classKey,provenance}={}){
 const doc=root.ownerDocument,wrap=el(doc,'section');wrap.className='learning-progress';wrap.append(el(doc,'h3','My progress across the year'));
 if(!history||history.class_key!==classKey||history.provenance!==provenance){wrap.append(el(doc,'p','Progress history is not connected yet.'));root.append(wrap);return;}
 const series=Array.isArray(history.series)?history.series.filter(s=>s.learner_key===learnerKey):[];
 if(!series.length){wrap.append(el(doc,'p','No dated evidence yet. One observation will establish a starting point; it cannot show improvement by itself.'));root.append(wrap);return;}
 const select=el(doc,'select');select.setAttribute('aria-label','Concept to view');series.forEach((s,i)=>{const opt=el(doc,'option',s.concept_label+' · '+(s.stage==='final'?'Final response':'Initial response'));opt.value=i;select.append(opt);});wrap.append(select);
 const body=el(doc,'div');wrap.append(body);
 function draw(){body.replaceChildren();const s=series[Number(select.value)],points=s.points;if(!Array.isArray(points)||!points.length)return;
 const charts=el(doc,'div');charts.className='learning-chart-grid';plot(charts,points,'understanding',s.rubric_version);if(s.rubric_version!=='critical-aspect-4/v1')plot(charts,points,'explanation',s.rubric_version);body.append(charts);
 body.append(el(doc,'p',points.length<2?'One observation: no trend yet.':'These show my understanding and how I explain it. Completion grades are separate.'));
 const detail=el(doc,'details');detail.append(el(doc,'summary','Writing behind these points'));for(const p of points){detail.append(el(doc,'p',p.date+' · '+(p.understanding||'Understanding unavailable')+' · '+(p.explanation||'Explanation unavailable')),el(doc,'blockquote',p.quote||'No written evidence available.'));}body.append(detail);}
 select.addEventListener('change',draw);draw();root.append(wrap);
}
export function renderClassProgress(root,history,{classKey,provenance}={}){
 const doc=root.ownerDocument,wrap=el(doc,'section');wrap.className='surface wide';wrap.append(el(doc,'h2','Learning across the year'));
 if(!history||history.class_key!==classKey||history.provenance!==provenance||!history.distributions?.length){wrap.append(el(doc,'p','Year history is not connected yet. No trend can be inferred.'));root.append(wrap);return;}
 wrap.append(el(doc,'p','Observed work only. Counts show the evidence received; changing membership or tasks can change a distribution without proving individual growth.'));
 for(const dimension of ['understanding','explanation']){
 wrap.append(el(doc,'h3',dimension==='understanding'?'Conceptual understanding':'Explaining concepts'));
 for(const row of history.distributions){wrap.append(el(doc,'p',row.date+' · '+row.concept_label+' · '+row.stage+' · '+row.rubric_version+' · '+row.sample_size+' observed'));
 const bar=el(doc,'div');bar.className='distribution-bar';for(const[level,count]of Object.entries(row[dimension]||{})){if(!count)continue;const seg=levelBadge(doc,level==='Unavailable'?null:level);seg.textContent+=' '+count;seg.style.flex=count;bar.append(seg);}wrap.append(bar);}}
 const individual=el(doc,'details');individual.append(el(doc,'summary','Individual evidence'));
 for(const learner of [...new Set(history.series.map(s=>s.learner_key))]){individual.append(el(doc,'h3',history.series.find(s=>s.learner_key===learner)?.display_name||'Student name unavailable'));renderLearningProgress(individual,history,{learnerKey:learner,classKey,provenance});}
 wrap.append(individual);root.append(wrap);
}
