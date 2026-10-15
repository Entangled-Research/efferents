// Authored demo records only. Render using scripts/render_network_film.cjs.
// This script calls the real dashboard renderer and preserves its actual markup,
// measured lab geometry, branches, review gates, publications and reading paths.
const filmNames=['Numerical methods','Adaptive quadrature','Error bounds','Graph algorithms','Spectral methods','Combinatorics','Mesh refinement','Optimization','Probabilistic methods','Formal verification','Orbital dynamics','Conservation laws','Fluid simulation','Particle systems','Climate dynamics','Wave propagation','Transport models','Plasma simulation','Materials','Inverse problems','Representation learning','Sparse models','Causal inference','Geometric learning','Diffusion models','Tensor networks','Compression','Scientific discovery','Robust estimation','Symbolic regression'];
const filmLabs=filmNames.map((name,i)=>({lab_id:name.toLowerCase().replaceAll(' ','-'),owner_name:'Research collective',domain:['math','physics','machine-learning'][Math.floor(i/10)],mine:i===0,status:'running',budget:{spent:8+i*.63,cap:50},headline:{column:'error',direction:'min',latest:.07+i*.002,best:.06+i*.001,observations:12+i},ideas:Array.from({length:2+i%4},(_,q)=>({id:`idea-${q}`,name:`Idea ${String.fromCharCode(65+q)}`,focus:'Compare against the baseline at equal compute.',verdict:q===2&&i%3===0?'falsified':'undecided'})),review_board:{status:i%6===3?'rejected':'accepted',scores:{critical:6,neutral:7,optimistic:8}}}));
const filmFindings=filmLabs.filter((l,i)=>i%6!==3).map((lab,i)=>({id:'paper-'+i,lab_id:lab.lab_id,domain:lab.domain,journal:homeJournal(lab),kind:'publication',publication_status:'accepted',campaign_id:'bounded-study',title:'Matched-budget comparison',review_scores:{critical:6,neutral:7,optimistic:8}}));
const filmReceipts=filmLabs.flatMap((lab,i)=>{const own=filmFindings.filter(p=>p.domain===lab.domain);return [{finding_id:own[i%own.length].id,target:lab.lab_id},...(i%5===0?[{finding_id:filmFindings[(i+7)%filmFindings.length].id,target:lab.lab_id}]:[])]});
document.querySelectorAll('[data-route-view]').forEach(el=>el.hidden=el.id!=='network-view');
portfolioState={labs:filmLabs,findings:filmFindings,observations:filmReceipts,eventNetwork:null};
networkDrift=false;renderNetwork();if(networkGravityTimer)clearInterval(networkGravityTimer);
const filmSmooth=x=>{x=Math.max(0,Math.min(1,x));return x*x*x*(x*(x*6-15)+10)},filmMix=(a,b,q)=>a+(b-a)*q;
const filmCards=[...document.querySelectorAll('.efferent-lab')],filmGates=[...document.querySelectorAll('.review-gate')];
const filmPositions=filmCards.map(el=>({x:parseFloat(el.style.left),y:parseFloat(el.style.top),h:el.offsetHeight,w:el.offsetWidth}));
const filmPaths=[...document.querySelectorAll('#network-lines>path')];
const filmPackets=[...document.querySelectorAll('#network-lines>circle')];
const filmWorld=document.querySelector('#map-world')||document.querySelector('#network-world');
const filmMap=document.querySelector('#lab-map'),filmSvg=document.querySelector('#network-lines');
filmSvg.pauseAnimations();
function filmLife(i,t){const birth=i<20?-5:3+(i-20)*.55,death=[4,8,13,18].includes(i)?6+[4,8,13,18].indexOf(i)*1.15:99;return filmSmooth((t-birth)/.8)*(1-filmSmooth((t-death)/.85))}
window.renderFrame=function(t){
 const pull=filmSmooth((t-.5)/4.1),focus=filmSmooth((t-10.8)/3.2),p=filmPositions[0],target=filmPositions[0];
 const fit=Math.min((filmMap.clientWidth-70)/mapView.width,(filmMap.clientHeight-30)/mapView.height);
 const scale=filmMix(filmMix(1.42,fit,pull),.62,focus);
 const cx=filmMix(filmMix(p.x,mapView.width/2,pull),target.x+120,focus),cy=filmMix(filmMix(p.y+p.h*.42,mapView.height/2,pull),target.y-30,focus);
 mapView.x=filmMap.clientWidth/2-cx*scale;mapView.y=filmMap.clientHeight/2-cy*scale;mapView.k=scale;applyMapView(false);
 filmCards.forEach((el,i)=>{const life=filmLife(i,t);el.style.opacity=life;el.style.transform=`translateX(-50%) scale(${.6+.4*life})`;el.style.transformOrigin='center top';filmGates[i].style.opacity=life;});
 const opacityForTitle=s=>{let idx=filmLabs.findIndex(l=>s.includes(l.lab_id)||s.includes(labDisplayName(l.lab_id)));return idx<0?1:filmLife(idx,t)};
 filmPaths.forEach(el=>el.style.opacity=opacityForTitle(el.textContent));
 filmPackets.forEach(el=>{let d=el.querySelector('animateMotion')?.getAttribute('path');let edge=filmPaths.find(p=>p.getAttribute('d')===d);el.style.opacity=edge?edge.style.opacity:1});
 filmSvg.setCurrentTime(t*1.7);
 const count=filmLabs.filter((_,i)=>filmLife(i,t)>.5).length;
 document.querySelector('#network-node-count').textContent=`${count} active labs · 3 journals`;
 document.querySelector('#film-caption').textContent=t<5?'ONE LAB → A WIDER RESEARCH ECOSYSTEM':t<10.8?'LABS JOIN / WORK RETIRES / EVIDENCE STAYS':'ACCEPTED PAPER → JOURNAL → RECORDED READING → NEXT TEST';
};
renderFrame(0);
if(!new URLSearchParams(location.search).has('capture')){let start=performance.now();function play(now){renderFrame(Math.min(15,(now-start)/1000));if(now-start<15000)requestAnimationFrame(play)}requestAnimationFrame(play)}
