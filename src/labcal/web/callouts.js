"use strict";
// Geometry is display-only. It never creates findings or infers nonconformities.
const CalloutLayout={
  place(items,height,gap=10){
    let cursor=8;
    return items.map((item,index)=>({...item,index})).sort((a,b)=>a.y-b.y||a.index-b.index).map(item=>{
      const top=Math.max(cursor,Math.min(item.y-item.height/2,Math.max(8,height-item.height-8)));
      cursor=top+item.height+gap;return {...item,top};
    });
  },
  active(findings,resolutions,extractionId,page){
    return findings.filter(f=>(!f.category||f.category==='content_issue')&&f.location.kind!=='document'&&f.extraction_id===extractionId&&f.location.page===page&&!["dismissed","resolved"].includes(resolutions[f.id]?.action));
  },
  confirmed(f,resolutions){return resolutions[f.id]?.action==="confirmed"||f.certainty==="confirmed";}
};
if(typeof module!=="undefined")module.exports={CalloutLayout};
if(typeof document!=="undefined"){
  let layoutCallouts=()=>{};
  function pageFindings(){return review&&extraction?CalloutLayout.active(review.payload.findings,review.resolutions||{},extraction.id,page):[];}
  const expanded=()=>!!$("documentPanel").closest('#reportPanelDialog')||$("pageWrap").clientWidth>=850;
  window.problemCalloutGutter=()=>expanded()&&$("showCallouts").checked&&pageFindings().length?224:0;
  window.renderProblemCallouts=()=>{
    const rail=$("problemCallouts"),svg=$("problemLeaders"),stage=$("pageStage"),canvas=$("pageCanvas");
    const openCards=new Set(Array.from(rail.querySelectorAll("details[open]")).map(d=>d.closest("article").dataset.calloutId));
    layoutCallouts=()=>{};rail.replaceChildren();svg.replaceChildren();
    const findings=pageFindings(),enabled=$("showCallouts").checked&&findings.length>0;
    const width=canvas.clientWidth,height=$("pageImage").clientHeight;
    const full=expanded();stage.style.width=(width+(enabled&&full?224:0))+"px";stage.style.height=height+"px";
    rail.hidden=!enabled||!full;svg.toggleAttribute("hidden",!enabled);$("calloutCount").textContent=findings.length?`${findings.length} 项`:(review?"本页无定位提示":"诊断后显示");
    if(!enabled||!width||!height)return;
    if(!full){
      svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.style.width=width+'px';svg.style.height=height+'px';
      const ns='http://www.w3.org/2000/svg';for(const f of findings){const dot=document.createElementNS(ns,'circle');dot.setAttribute('cx',Math.min(width-4,f.location.bbox[2]*width));dot.setAttribute('cy',(f.location.bbox[1]+f.location.bbox[3])/2*height);dot.setAttribute('r','4');dot.setAttribute('fill',CalloutLayout.confirmed(f,review.resolutions||{})?'#be2930':'#ab680b');dot.setAttribute('stroke','white');dot.style.pointerEvents='auto';dot.setAttribute('role','button');dot.setAttribute('tabindex','0');dot.setAttribute('aria-label',f.title);const title=document.createElementNS(ns,'title');title.textContent=f.title;dot.append(title);dot.onclick=()=>focusFinding(f);dot.onkeydown=e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();focusFinding(f);}};svg.append(dot);}return;
    }
    const cards=findings.map(f=>{
      const confirmed=CalloutLayout.confirmed(f,review.resolutions||{}),card=document.createElement("article");
      const documentLevel=f.location.kind==="document";
      card.className="problem-callout "+(confirmed?"confirmed":"pending")+(selectedFinding?.id===f.id?" selected":"");
      card.dataset.calloutId=f.id;
      card.innerHTML=`<div class="callout-heading"><span class="callout-state">${confirmed?"已确认问题":"待核实"}</span><button class="callout-focus" data-callout-focus="${esc(f.id)}" title="定位原页并查看问题">${esc(f.title)}</button></div><p>${esc(f.message)}</p><details><summary>依据与建议</summary><p>依据：${esc(f.basis)}</p><p>建议：${esc(f.suggestion)}</p></details><small>${esc(f.id)} · ${documentLevel?"文件级提示，圆点不代表具体错误单元格":"定位为原文区域；表格块需进一步核对单元格"}</small>`;
      if(openCards.has(f.id))card.querySelector("details").open=true;
      rail.append(card);
      return {f,card,height:card.offsetHeight,y:(f.location.bbox[1]+f.location.bbox[3])/2*height};
    });
    layoutCallouts=()=>{
    svg.replaceChildren();cards.forEach(c=>c.height=c.card.offsetHeight);
    const placed=CalloutLayout.place(cards,height),total=Math.max(height,...placed.map(c=>c.top+c.height+8));
    stage.style.height=total+"px";rail.style.left=(width+16)+"px";
    svg.setAttribute("viewBox",`0 0 ${width+224} ${total}`);svg.style.width=(width+224)+"px";svg.style.height=total+"px";
    const ns="http://www.w3.org/2000/svg";
    for(const item of placed){
      const {f,card,top}=item;card.style.top=top+"px";
      const x=Math.min(width-3,Math.max(3,f.location.bbox[2]*width)),y=item.y;
      const confirmed=CalloutLayout.confirmed(f,review.resolutions||{}),color=confirmed?"#be2930":"#ab680b";
      const path=document.createElementNS(ns,"path");
      path.setAttribute("d",`M${x},${y} L${width+7},${y} L${width+7},${top+18} L${width+16},${top+18}`);
      path.setAttribute("stroke",color);path.setAttribute("fill","none");path.setAttribute("stroke-width",selectedFinding?.id===f.id?"2":"1.2");
      if(f.location.kind==="document")path.setAttribute("stroke-dasharray","3 3");
      svg.append(path);
      const dot=document.createElementNS(ns,"circle");dot.setAttribute("cx",x);dot.setAttribute("cy",y);dot.setAttribute("r","4");dot.setAttribute("fill",color);dot.setAttribute("stroke","white");dot.setAttribute("stroke-width","1.5");
      dot.setAttribute("tabindex","0");dot.setAttribute("role","button");dot.setAttribute("aria-label",f.title+(confirmed?"，已确认问题":"，待核实"));dot.style.pointerEvents="auto";
      const focus=()=>focusFinding(f);dot.addEventListener("click",focus);dot.addEventListener("keydown",e=>{if(["Enter"," "].includes(e.key)){e.preventDefault();focus();}});svg.append(dot);
    }
    };layoutCallouts();
  };
  $("problemCallouts").addEventListener("toggle",()=>layoutCallouts(),true);
  $("showCallouts").onchange=()=>{fitPage();drawOverlay();};
  document.addEventListener("click",event=>{
    const button=event.target.closest("[data-callout-focus]");
    if(button&&review){const finding=review.payload.findings.find(f=>f.id===button.dataset.calloutFocus);if(finding)focusFinding(finding);}
  });
}
