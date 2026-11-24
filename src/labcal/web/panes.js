"use strict";
// Native split panes with keyboard support; sizes are display preferences only.
let paneDrag=null;
const desk=document.getElementById('certificateDesk'),rightPane=desk.querySelector('.inspection-sidebar');
function panePreference(key,fallback){try{const n=Number(localStorage.getItem('labcal.pane.'+key));return n>=20&&n<=75?n:fallback;}catch{return fallback;}}
function setPane(axis,value,persist=true){const n=Math.max(axis==='width'?25:20,Math.min(axis==='width'?62:65,value));desk.style.setProperty(axis==='width'?'--report-width':'--info-height',n+'%');const handle=document.getElementById(axis==='width'?'deskDivider':'infoDivider');handle.setAttribute('aria-valuenow',String(Math.round(n)));if(persist)try{localStorage.setItem('labcal.pane.'+axis,String(n));}catch{}fitPage();}
setPane('width',panePreference('width',43),false);setPane('height',panePreference('height',40),false);
for(const [id,axis,fallback] of [['deskDivider','width',43],['infoDivider','height',40]]){
 const handle=document.getElementById(id);handle.setAttribute('aria-valuemin',axis==='width'?'25':'20');handle.setAttribute('aria-valuemax',axis==='width'?'62':'65');
 handle.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();paneDrag={axis};document.body.classList.add('resizing-'+axis);handle.setPointerCapture?.(e.pointerId);});
 handle.addEventListener('dblclick',()=>setPane(axis,fallback));
 handle.addEventListener('keydown',e=>{const keys=axis==='width'?['ArrowLeft','ArrowRight']:['ArrowUp','ArrowDown'];if(!keys.includes(e.key))return;e.preventDefault();setPane(axis,Number(handle.getAttribute('aria-valuenow'))+(e.key===keys[1]?2:-2));});
}
document.addEventListener('pointermove',e=>{if(!paneDrag)return;const rect=(paneDrag.axis==='width'?desk:rightPane).getBoundingClientRect();setPane(paneDrag.axis,paneDrag.axis==='width'?(e.clientX-rect.left)/rect.width*100:(e.clientY-rect.top)/rect.height*100);});
function endPaneDrag(){paneDrag=null;document.body.classList.remove('resizing-width','resizing-height');}
document.addEventListener('pointerup',endPaneDrag);document.addEventListener('pointercancel',endPaneDrag);
