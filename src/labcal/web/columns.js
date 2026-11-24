"use strict";
// Presentation preferences only: no business values or limits are stored here.
const ColumnSizing={
  scopeKey(key,popup){return key+":"+(popup?"popup":"desk");},
  clamp(width){return Math.max(56,Math.min(1000,Math.round(width)));},
  normalize(value,count){
    if(!Array.isArray(value)||value.length!==count||value.some(w=>w!==null&&(!Number.isFinite(w)||w<56||w>1000)))return null;
    return value;
  },
  resize(widths,index,delta){return widths.map((w,i)=>i===index?this.clamp(w+delta):w);}
};
if(typeof module!=="undefined")module.exports={ColumnSizing};
if(typeof document!=="undefined"){
  const prefix="labcal.columns.v1:",prepared=new WeakSet();let drag=null;
  const preferenceKey=table=>ColumnSizing.scopeKey(table.dataset.resizeKey,!!table.closest('#reportPanelBody'));
  function read(key,count){try{return ColumnSizing.normalize(JSON.parse(localStorage.getItem(prefix+key)),count);}catch{return null;}}
  function save(key,widths){try{localStorage.setItem(prefix+key,JSON.stringify(widths));}catch{}}
  function columns(table){
    const header=table.tHead?.rows[table.tHead.rows.length-1];if(!header||Array.from(header.cells).some(c=>c.colSpan!==1))return null;
    let group=table.querySelector("colgroup");
    if(!group){group=document.createElement("colgroup");for(const _ of header.cells)group.append(document.createElement("col"));table.prepend(group);}
    if(group.children.length!==header.cells.length)return null;
    return {header,cols:Array.from(group.children)};
  }
  function widths(table,info){return Array.from(info.header.cells).map(c=>getComputedStyle(c).display==="none"?null:ColumnSizing.clamp(c.getBoundingClientRect().width));}
  function apply(table,info,values){
    info.cols.forEach((col,i)=>{if(values[i]!==null)col.style.width=values[i]+"px";});
    const total=values.reduce((sum,w)=>sum+(w||0),0);table.style.width=total+"px";table.style.minWidth=total+"px";table.classList.add("columns-sized");
    for(const h of info.header.querySelectorAll('.column-grip')){const w=values[Number(h.dataset.columnIndex)];if(w!==null)h.setAttribute('aria-valuenow',String(w));}
  }
  function resetDisplay(table){
    table.querySelectorAll('col').forEach(c=>c.style.width=c.dataset.defaultWidth||"");
    table.style.width="";table.style.minWidth="";table.classList.remove("columns-sized");
    table.querySelectorAll(".column-grip").forEach(h=>h.setAttribute("aria-valuenow",String(ColumnSizing.clamp(h.parentElement.getBoundingClientRect().width))));
  }
  function restoreLayout(table){const info=columns(table);if(!info)return;resetDisplay(table);const stored=read(preferenceKey(table),info.cols.length);if(stored)apply(table,info,stored);}
  function reset(table){if(!table)return;try{localStorage.removeItem(prefix+preferenceKey(table));}catch{}resetDisplay(table);}
  function finish(cancel=false){
    if(!drag)return;const d=drag;drag=null;document.body.classList.remove("resizing-columns");
    if(cancel){apply(d.table,d.info,d.before);return;}
    if(d.table.isConnected)save(preferenceKey(d.table),widths(d.table,d.info));
  }
  function prepare(table){
    if(prepared.has(table)||!table.dataset.resizeKey)return;
    const info=columns(table);if(!info)return;prepared.add(table);
    info.cols.forEach(c=>c.dataset.defaultWidth=c.style.width);
    const stored=read(preferenceKey(table),info.cols.length);if(stored)apply(table,info,stored);
    Array.from(info.header.cells).forEach((cell,index)=>{
      if(getComputedStyle(cell).display==="none")return;
      const grip=document.createElement('span');grip.className='column-grip';grip.tabIndex=0;grip.setAttribute('role','separator');
      grip.setAttribute('aria-orientation','vertical');grip.setAttribute('aria-label','调整“'+cell.textContent.trim()+'”列宽');
      grip.setAttribute('aria-valuenow',String(ColumnSizing.clamp(cell.getBoundingClientRect().width)));grip.setAttribute('aria-valuemin','56');grip.setAttribute('aria-valuemax','1000');grip.title='拖动调整列宽；双击恢复默认列宽';grip.dataset.columnIndex=index;
      grip.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();e.stopPropagation();finish();const before=widths(table,info);apply(table,info,before);drag={table,info,index,before,start:e.clientX};document.body.classList.add('resizing-columns');grip.setPointerCapture?.(e.pointerId);});
      grip.addEventListener('dblclick',e=>{e.preventDefault();reset(table);});
      grip.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();const values=ColumnSizing.resize(widths(table,info),index,(e.key==='ArrowRight'?1:-1)*(e.shiftKey?40:10));apply(table,info,values);save(preferenceKey(table),values);});
      cell.append(grip);
    });
  }
  document.addEventListener('pointermove',e=>{if(!drag)return;if(!drag.table.isConnected){finish();return;}apply(drag.table,drag.info,ColumnSizing.resize(drag.before,drag.index,e.clientX-drag.start));});
  document.addEventListener('pointerup',()=>finish());document.addEventListener('pointercancel',()=>finish(true));
  document.addEventListener('keydown',e=>{if(e.key==='Escape')finish(true);});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-reset-columns]');if(!b)return;const scope=b.dataset.resetColumns;
    const table=scope==='diagnostic'?document.querySelector('#comparisonRows table'):document.querySelector('#recognizedFields:not([hidden]) table,#recognizedResults:not([hidden]) table');reset(table);});
  const scan=()=>document.querySelectorAll('table[data-resize-key]').forEach(prepare);
  new MutationObserver(scan).observe(document.body,{childList:true,subtree:true});scan();
  document.addEventListener('labcal-panel-layout',e=>document.getElementById(e.detail.panel)?.querySelectorAll('table[data-resize-key]').forEach(restoreLayout));
}
