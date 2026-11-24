"use strict";
// Move the live panel rather than cloning controls, IDs, or technical data.
let panelWindow=null;
function closeReportPanel(){
  const dialog=$("reportPanelDialog");
  if(!panelWindow)return;
  const current=panelWindow;panelWindow=null;
  current.placeholder.replaceWith(current.panel);
  current.button.textContent="弹窗打开";
  document.dispatchEvent(new CustomEvent('labcal-panel-layout',{detail:{panel:current.panel.id}}));
  dialog.classList.remove("maximized");
  $("maximizeReportPanel").textContent="最大化";
  if(dialog.open)dialog.close();
  fitPage();current.button.focus();
}
function openReportPanel(id,button){
  if(panelWindow?.panel.id===id){closeReportPanel();return;}
  closeReportPanel();
  const panel=$(id),placeholder=document.createElement("div");
  placeholder.className="panel-placeholder";
  placeholder.textContent="此区域已在独立窗口打开";
  panel.before(placeholder);
  panelWindow={panel,placeholder,button};
  $("reportPanelTitle").textContent={factsPanel:"报告识别信息",diagnosticReport:"诊断报告",documentPanel:"报告原页与问题标注"}[id];
  $("reportPanelContext").textContent=$("contextLabel").textContent+" / "+($("documentSelect").selectedOptions[0]?.textContent||"尚未选择报告");
  $("reportPanelBody").append(panel);button.textContent="还原到工作台";
  $("reportPanelDialog").showModal();document.dispatchEvent(new CustomEvent('labcal-panel-layout',{detail:{panel:id}}));fitPage();
}
for(const [id,panel] of [["popFacts","factsPanel"],["popDiagnosis","diagnosticReport"],["popDocument","documentPanel"]]){
  $(id).onclick=()=>openReportPanel(panel,$(id));
}
$("reportPanelDialog").addEventListener("close",()=>{if(!$("reportPanelDialog").open)closeReportPanel();});
$("reportPanelDialog").addEventListener("cancel",event=>{event.preventDefault();closeReportPanel();});
$("maximizeReportPanel").onclick=()=>{
  const maximized=$("reportPanelDialog").classList.toggle("maximized");
  $("maximizeReportPanel").textContent=maximized?"还原窗口":"最大化";fitPage();
};

function refreshReportPanelContext(){if(panelWindow)$("reportPanelContext").textContent=$("contextLabel").textContent+" / "+($("documentSelect").selectedOptions[0]?.textContent||"尚未选择报告");}
