"use strict";
// Daily flow: import -> archive -> recognize -> associate -> diagnose -> original-page review.
let reportView=null, reportPoll=null, reportContext="", reportRequest=0, scanJob=null;
const jobSeen=new Set();
let displayedScan=null,lastHadActive=false;
const reportScan=new ReportScan({line:$("scanLine"),caption:$("scanCaption"),canvas:$("pageCanvas"),progress:$("diagnosisProgress"),status:$("diagnosisStatus")});
const matchLabels={matched:"已自动关联",suggested:"名称候选关联",ambiguous:"多个设备待区分",conflict:"身份信息冲突",unmatched:"待匹配",operator_linked:"已澄清资料归属"};
const stageLabels={queued:"排队中",archiving:"留存原件",reading:"读取报告",reading_page:"识别原页",paddle_ocr:"PaddleOCR-VL 模型识别（CPU可能需数分钟）",tesseract_ocr:"Tesseract 快速识别",page_read:"该页识别完成",matching:"匹配设备与生成诊断",completed:"已完成",failed:"处理失败",interrupted:"任务已中断"};
function currentReportContext(){return `${state?.lab_id||""}/${currentCampaign||""}`;}
function resetReports(){
  if(typeof closeReportPanel === "function")closeReportPanel();
  reportRequest++;reportView=null;scanJob=null;displayedScan=null;reportContext="";lastHadActive=false;jobSeen.clear();
  clearTimeout(reportPoll);reportPoll=null;
  $("intakePanel").hidden=true;reportScan.reset();
  $("reportStatus").textContent="选择报告或批量导入PDF，系统自动识别、匹配并诊断。";
  $("reportIdentity").textContent="选择报告后，自动匹配方案设备。";
  $("recognizedResults").innerHTML="";$("pointParameter").innerHTML='<option value="">全部参数</option>';$("pointSignal").value="";$("pointSearch").value="";
  $("recognizedFields").innerHTML="";$("diagnostics").innerHTML="";$("comparisonRows").innerHTML="";$("comparisonSummary").textContent="选择报告后，点击“开始诊断”。";$("comparisonCount").textContent="";$("reportArchive").textContent="";
  $("reviewDevice").disabled=true;$("pageZoom").value="fit";fitPage();
  $("diagnosisContext").hidden=true;$("correctionDialog").close();$("exportDialog").close();
}
function applyReportReview(item){
  review=item;$("export").disabled=!item;$("downloads").hidden=true;
  if(!item){$("findings").innerHTML="";$("findingCount").textContent="0 项";drawOverlay();return;}
  selectedExtractions.clear();item.payload.certificate_snapshots.forEach(x=>selectedExtractions.add(x.extraction_id));
  $("baseline").value=item.payload.baseline_id||"";
  $("axes").innerHTML=Object.entries({document:"证书资料",coverage:"方案覆盖",fitness:"用途判定"}).map(([key,label])=>`<div class="${item.payload.axes[key]}">${label}<strong>${statuses[item.payload.axes[key]]}</strong></div>`).join("");
  renderFindings();drawOverlay();
}
async function ensureReportOpen(){
  if(!currentCampaign||extraction||scanJob)return;
  let remembered="";try{remembered=localStorage.getItem("labcal.last-report:"+currentReportContext())||"";}catch{}
  if(state.certificates.length)await openReport(state.certificates.some(c=>c.extraction_id===remembered)?remembered:state.certificates[0].extraction_id);
  else $("reportQueue").open=true;
}
async function openReport(id){
  scanJob=null;displayedScan=null;reportScan.reset();reportRequest++;
  const ctx=currentReportContext();
  let detail=await api("/api/report?"+query({id}));
  if(ctx!==currentReportContext())return;
  if(!detail.association && state.campaign?.state==="active"){
    await api("/api/reports/process",{lab_id:state.lab_id,extraction_id:id});
    if(ctx!==currentReportContext())return;
    detail=await api("/api/report?"+query({id}));
  }
  if(ctx!==currentReportContext())return;
  reportView=detail;applyReportReview(detail.diagnosis_stale?null:detail.review);
  if(!detail.review){review=null;$("findings").innerHTML='<p class="muted">尚无审查记录。</p>';}
  await preview(id);
  $("reportQueue").open=false;
  try{localStorage.setItem("labcal.last-report:"+ctx,id);}catch{}
}
async function showRecognizedInfo(id){
  const seq=++reportRequest,ctx=currentReportContext();
  const detail=await api("/api/report?"+query({id,...(review?.payload.certificate_snapshots.some(s=>s.extraction_id===id)?{review_id:review.id}:{})}));
  if(seq!==reportRequest||ctx!==currentReportContext()||extraction?.id!==id)return;
  reportView=detail;
  applyReportReview(detail.diagnosis_stale?null:detail.review);
  const association=detail.association?.payload,match=association?.match,device=association?.device;
  $("documentTitle").textContent="证书原页";
  $("reportStatus").textContent=detail.diagnosis_stale?"资料或识别已变化，请更新诊断":review?`已生成${review.payload.baseline_id?"基准审查":"基础诊断"} · 对照结果见右下诊断报告` : "识别完成，待生成诊断";
  $("reportIdentity").innerHTML=`<div class="identity-top"><strong>${esc(matchLabels[match?.state]||"尚未匹配")}</strong><h3>${esc(device?.name||"尚未确定方案设备")}</h3><button class="quiet" id="clarifyAssociation" ${!detail.association?"disabled":""}>澄清对应关系</button></div><details><summary>匹配说明${device?` · ${device.technical_state==="confirmed"?"已确认版本":"候选方案"}${device.rows?" · "+device.rows.length+" 项参数":""}`:""}</summary><p>${esc(match?.reason||"更新诊断后，系统将自动匹配方案设备。")}</p>${device?`<p class="muted">${device.plan_id?`方案版本 ${device.revision} / ${esc(device.plan_id)}`:`工作簿 ${esc(device.workbook_id)}`}</p>`:""}</details>${detail.catalog_stale?'<p class="stale-note">方案清单已变化，请更新诊断。</p>':""}`;
  $("clarifyAssociation").onclick=()=>work("正在读取本批次方案设备…",async()=>{
    const catalog=await api("/api/report-catalog?"+query({}));
    if(!catalog.devices.length)throw new Error("本批次尚无可匹配的方案设备，请先导入校准方案。");
    $("associationForm").reset();
    $("associationDevice").innerHTML='<option value="">选择报告对应的设备</option>'+catalog.devices.map(d=>`<option value="${esc(d.id)}">${esc(d.name)} / ${esc(d.identity.equipment_number||d.identity.model||"编号未明确")} / ${esc(d.id.slice(-8))}</option>`).join("");
    $("associationDevice").value=match?.device_id||"";$("associationDialog").showModal();
  });
  const fields=detail.fields;
  $("recognizedFields").innerHTML='<table class="data-table report-fields-table" data-resize-key="report-fields"><colgroup><col style="width:90px"><col><col style="width:68px"></colgroup><thead><tr><th>字段</th><th>识别信息</th><th>原页</th></tr></thead><tbody>'+Object.entries(labels).filter(([key])=>!(detail.extraction.payload.document_kind==="calibration"&&key.startsWith("verification_"))).map(([key,label])=>{
    const title=key==="results"?"结果页线索":key==="uncertainty"?"不确定度线索":label;
    const values=fields[key]||[];
    const fact=(v,index)=>`<button class="fact-value" data-field-anchor="${esc(key)}" data-field-index="${index}" title="查看第 ${v.location.page} 页原文">${esc(v.value)}</button>`;
    const more=values.length>1?`<details><summary>另 ${values.length-1} 处候选原文</summary>${values.slice(1).map((v,i)=>`<div class="additional-fact">${fact(v,i+1)}<button class="quiet page-link" data-field-anchor="${esc(key)}" data-field-index="${i+1}">第${v.location.page}页 ↗</button></div>`).join("")}</details>`:"";
    return `<tr><th>${esc(title)}</th><td>${values.length?fact(values[0],0)+more:'<span class="missing-value">-</span>'}</td><td>${values.length?`<button class="quiet page-link" data-field-anchor="${esc(key)}" data-field-index="0">第${values[0].location.page}页 ↗</button>`:"-"}</td></tr>`;
  }).join("")+"</tbody></table>";
  const engines=[...new Set(detail.extraction.payload.pages.map(p=>p.engine||(p.method==="pdf_text"?"pdf_text":"tesseract")))];
  const engineNames={pdf_text:"PDF文字直接提取", "pdf_text+tesseract_header":"PDF文字＋页眉图像补充OCR",tesseract:"Tesseract", "paddleocr-vl-1.6":"本地 PaddleOCR-VL-1.6"};
  const warningLabels={PADDLE_OCR_TIMEOUT:"模型识别超时",PADDLE_OCR_BUSY:"模型正在处理另一份报告",PADDLE_OCR_UNAVAILABLE:"本地模型环境不可用",PADDLE_OCR_FAILED:"模型运行失败",PADDLE_OCR_EMPTY:"模型未提取到文字",PADDLE_TABLE_INCOMPLETE:"模型表格输出不完整，未纳入逐点对照"};
  const warningCodes=(detail.extraction.payload.warnings||[]).filter(w=>w.code.startsWith("PADDLE_")).map(w=>`第${w.page}页 ${warningLabels[w.code]||w.code}`);
  $("recognitionMode").textContent="本次使用："+engines.map(e=>engineNames[e]||e).join("、")+"。自动提取均为候选；模型定位精度为内容块，需回看原页。"+(warningCodes.length?" 模型提示："+warningCodes.join("；")+"；未能识别的页面已尝试备用OCR。":"");
  $("reportArchive").textContent=detail.archive_directory||"原件已在实验室对象库留存；更新诊断后建立批次报告目录。";
  $("reviewDevice").disabled=!match?.device_id;
  if(typeof renderRecognizedResults === "function")renderRecognizedResults();
  renderDiagnostics(detail.diagnostics);
  renderComparison();
  if(typeof renderDiagnosisContext === "function")renderDiagnosisContext();
  if(typeof refreshReportPanelContext === "function")refreshReportPanelContext();
}
function renderDiagnostics(rows){
  $("diagnostics").innerHTML=rows.map(d=>`<article class="diagnostic ${d.status}"><div><h3>${esc(d.title)}</h3><strong>${d.status==="pending"?esc(d.state_label||"待核对"):statuses[d.status]}</strong></div><p>${esc(d.reason)}</p>${d.next_action?`<p class="next-action">下一步：${esc(d.next_action)}</p>`:""}${d.checks?.length?`<details><summary>查看 ${d.checks.length} 项检查证据</summary>${d.checks.map((c,i)=>`<button class="diagnostic-evidence quiet" data-diagnostic-key="${d.key}" data-check-index="${i}">${esc(c.label)} · ${statuses[c.status]}${c.location?" · 第 "+c.location.page+" 页":""}</button>`).join("")}</details>`:""}</article>`).join("")||'<p class="muted">尚未生成诊断。</p>';

}
function renderComparison(){
  const report=reportView?.comparison_report;if(!report)return;
  const pointMode=$("comparisonFilter").value==="points";
  for(const id of ["pointParameter","pointSignal","pointSearch","pointColumns"])$(id).hidden=!pointMode;
  $("pointWarnings").hidden=true;
  if(pointMode && typeof renderPointMatrix === "function"){renderPointMatrix();return;}
  const filter=$("comparisonFilter").value,rows=report.rows.filter(r=>filter==="all"||r.category===filter);
  const counts=report.rows.reduce((all,r)=>{all[r.status]=(all[r.status]||0)+1;return all;},{});
  $("comparisonSummary").textContent=`${report.basis.label} · ${counts.passed||0} 项符合 / ${counts.failed||0} 项不符合 / ${counts.pending||0} 项待确定${counts.not_applicable?" / "+counts.not_applicable+" 项不适用":""}。${report.basis.mode==="candidate_preview"?"当前为候选方案预对照，技术符合性待确定。":report.basis.mode==="unmatched"?"尚未匹配具体方案设备。":"按所引用的确认版本进行对照。"}`;
  $("comparisonCount").textContent=`显示 ${rows.length} / ${report.rows.length} 项`;
  const signal={matched:"信息已对应",possible:"发现关联线索",conflict:"识别值有差异",insufficient:"信息不足"};
  const anchorButton=(r,a,i)=>`<button class="quiet" data-comparison-row="${esc(r.id)}" data-comparison-anchor="${i}" title="查看第 ${a.location.page} 页原文">第${a.location.page}页 ↗</button>`;
  const evidence=r=>{
    const parts=r.certificate.text.split("\n\n"),anchors=r.certificate.anchors;
    if(parts.length===anchors.length&&anchors.length)return parts.map((part,i)=>`<div class="comparison-evidence"><div class="comparison-text">${esc(part.replace(/^相关原文（第 \d+ 页）：/,""))}</div><div class="comparison-anchors">${anchorButton(r,anchors[i],i)}</div></div>`).join("");
    return `<div class="comparison-cell"><div class="comparison-text">${esc(r.certificate.text)}</div><div class="comparison-anchors">${anchors.map((a,i)=>anchorButton(r,a,i)).join("")}</div></div>`;
  };
  $("comparisonRows").innerHTML='<table class="comparison-table" data-resize-key="comparison-summary"><thead><tr><th>方案</th><th>校准／检定证书信息</th></tr></thead><tbody>'+rows.map(r=>`<tr class="comparison-heading ${r.status}"><th colspan="2"><div><span class="item-title">${esc(r.title)}</span><strong class="conclusion">${statuses[r.status]}</strong><details class="comparison-explanation"><summary>诊断说明 · ${signal[r.signal]}</summary><p>${esc(r.reason)}</p></details></div></th></tr><tr class="comparison-values"><td><div class="comparison-cell"><div class="comparison-text">${esc(r.category==="parameters"?r.plan.text.replace(/^参数：[^\n]*\n/,""):r.plan.text)}</div>${r.plan_anchor?`<small class="comparison-location" title="方案原文位置">${esc(r.plan_anchor)}</small>`:""}</div></td><td>${evidence(r)}</td></tr>`).join("")+'</tbody></table>';
}
$("comparisonFilter").onchange=renderComparison;
async function focusReportLocation(loc){
  if(typeof closeReportPanel === "function")closeReportPanel();
  if(!extraction)return;
  if(typeof setComparisonFocus === "function")setComparisonFocus(false);
  page=loc.page;selectedLocation=loc;showPage();drawOverlay();
  $("anchorLabel").textContent=`第 ${page} 页：${loc.quote||"对应检查位置"}`;
}
document.addEventListener("click",event=>{
  const b=event.target.closest("button");if(!b)return;
  if(b.dataset.comparisonRow){
    const row=reportView.comparison_report.rows.find(r=>r.id===b.dataset.comparisonRow),anchor=row.certificate.anchors[Number(b.dataset.comparisonAnchor)];
    work("正在定位证书原文…",async()=>{if(extraction?.id!==anchor.extraction_id)await preview(anchor.extraction_id,anchor.location.page);await focusReportLocation(anchor.location);if(matchMedia("(max-width:720px)").matches)$("documentTitle").scrollIntoView({block:"start"});notice("已定位对应证书原文。");});
  }
  if(b.dataset.fieldAnchor){const v=reportView.fields[b.dataset.fieldAnchor][Number(b.dataset.fieldIndex)];focusReportLocation(v.location);}
  if(b.dataset.diagnosticKey){const d=reportView.diagnostics.find(d=>d.key===b.dataset.diagnosticKey),c=d.checks[Number(b.dataset.checkIndex)];if(c.location)focusReportLocation(c.location);}
});
$("pageZoom").onchange=fitPage;
async function diagnoseReport(group=false){
  scoped();if(!extraction)throw new Error("请先打开一份报告。");
  const id=extraction.id,ctx=currentReportContext(),scan=reportScan.start(group?"正在联合诊断同设备报告":"正在对照方案与证书");
  $("runReview").textContent="正在诊断…";
  try{
    // Paint the processing state before the request; fast local diagnostics also get completion feedback.
    await new Promise(resolve=>requestAnimationFrame(()=>setTimeout(resolve,0)));
    const result=await api(group?"/api/reports/group-review":"/api/reports/process",{lab_id:state.lab_id,extraction_id:id});
    if(ctx!==currentReportContext())return;
    await load(state.lab_id);await openReview(result.review_id);
    reportScan.complete(scan);
    notice(group?"联合诊断完成，诊断区对照已更新。":"诊断完成，诊断区已更新方案与证书的逐项对照。页码可直接定位原文。");
  }catch(e){reportScan.fail(scan);throw e;}
  finally{$("runReview").textContent="开始诊断";if(ctx!==currentReportContext())reportScan.fail(scan);}
}
$("runReview").onclick=()=>work("正在诊断：匹配设备并逐项对照方案与证书…",()=>diagnoseReport());
$("reviewDevice").onclick=()=>work("正在联合诊断同设备报告…",()=>diagnoseReport(true));
$("associationForm").onsubmit=e=>{e.preventDefault();work("正在保存设备对应关系…",async()=>{
  const id=extraction.id;
  await api("/api/reports/associate",{lab_id:state.lab_id,extraction_id:id,choice:{expected_association_id:reportView.association.id,device_id:$("associationDevice").value,operator:$("associationOperator").value.trim(),statement:$("associationStatement").value.trim()}});
  $("associationDialog").close();await showRecognizedInfo(id);notice("资料对应关系已记录。下次诊断将使用该对应关系。");
});};
async function submitExistingReport(sourceId){
  const job=await api("/api/report-jobs",{lab_id:state.lab_id,source_id:sourceId,ocr_engine:$("ocrEngine").value,force_ocr:$("ocrEngine").value!=="auto"});scanJob=job.id;
  await refreshReportJobs();
}
$("upload").onchange=()=>work("正在接入报告并提交识别任务…",async()=>{
  scoped();const files=Array.from($("upload").files);if(!files.length)return;
  if(files.length>50)throw new Error("每批最多接入50份报告，请分批接入。");
  const errors=[];let submitted=0;
  for(const file of files){
    try{
      const job=await api("/api/report-jobs",{lab_id:state.lab_id,ocr_engine:$("ocrEngine").value,force_ocr:$("ocrEngine").value!=="auto",...await fileBody(file)});
      submitted++;scanJob=job.id;showModule("certificates");await refreshReportJobs();
    }catch(e){errors.push(`${file.name}：${e.message}`);}
  }
  $("upload").value="";await refreshReportJobs();
  notice(`已提交 ${submitted} 份报告，后台自动归档、识别和诊断。${errors.length?" 接入失败："+errors.join("；"):""}`,!!errors.length);
});
async function refreshReportJobs(){
  if(!state?.lab_id||!currentCampaign)return;
  clearTimeout(reportPoll);
  const ctx=currentReportContext(),jobs=await api("/api/report-jobs?"+query({}));
  if(ctx!==currentReportContext())return;
  if(reportContext!==ctx){reportContext=ctx;jobSeen.clear();jobs.filter(j=>!["queued","running"].includes(j.state)&&j.id!==scanJob).forEach(j=>jobSeen.add(j.id));}
  const active=jobs.filter(j=>["queued","running"].includes(j.state));
  $("intakePanel").hidden=!jobs.length;
  if(active.length)$("intakeDetails").open=true;else if(lastHadActive)$("intakeDetails").open=false;
  lastHadActive=!!active.length;
  const failures=jobs.filter(j=>["failed","interrupted"].includes(j.state)).length;
  $("intakeSummary").textContent=`${active.length?active.length+" 份处理中":"当前队列已处理完毕"} · ${jobs.filter(j=>j.state==="completed").length} 份完成${failures?" · "+failures+" 份失败或中断":""}`;
  if(!scanJob&&!extraction&&active.length)scanJob=active[0].id;
  $("intakeJobs").innerHTML=[...active,...jobs.filter(j=>!["queued","running"].includes(j.state))].slice(0,8).map(j=>`<div class="intake-job ${j.state}"><strong>${esc(j.filename)}</strong><span>${esc(stageLabels[j.stage]||j.stage)}${j.pages?` · 第 ${j.page} / ${j.pages} 页`:""}</span>${j.error?`<p>${esc(j.error.message)}${j.source_id?" 原件已留存，可在报告清单重新读取。":""}</p>`:""}${j.result?`<button class="quiet" data-preview="${esc(j.result.extraction_id)}">查看诊断 ↗</button>`:""}</div>`).join("");
  const scanning=active.find(j=>j.id===scanJob)||(!extraction?active.find(j=>j.state==="running"):null);
  if(scanning?.source_id && scanning.page){
    if(displayedScan!==scanning.id){
      displayedScan=scanning.id;reportRequest++;extraction=review=selectedLocation=selectedFinding=null;reportView=null;
      const option=document.createElement("option");option.value=scanning.id;option.textContent=scanning.filename+"（识别中）";option.disabled=true;
      $("documentSelect").append(option);$("documentSelect").value=scanning.id;
      $("export").disabled=true;$("prevPage").disabled=true;$("nextPage").disabled=true;$("reviewDevice").disabled=true;
      $("reportIdentity").textContent=`正在识别：${scanning.filename}`;
      $("recognizedFields").innerHTML='<p class="muted">逐页识别完成后，将列出本报告的信息。</p>';
      $("diagnostics").innerHTML="";$("comparisonRows").innerHTML='<p class="muted">正在准备原页信息，尚未生成诊断。</p>';$("comparisonSummary").textContent="报告识别中…";
      $("findings").innerHTML="";$("findingCount").textContent="0 项";
    }
    const image="/api/intake-page?"+query({id:scanning.id,page:scanning.page});
    $("documentEmpty").hidden=true;$("pageWrap").hidden=false;
    if($("pageImage").getAttribute("src")!==image){$("pageImage").src=image;$("overlay").innerHTML="";}
    $("pageLabel").textContent=`${scanning.page} / ${scanning.pages}`;
    reportScan.intake(["reading_page","paddle_ocr","tesseract_ocr","page_read","matching"].includes(scanning.stage)?`${stageLabels[scanning.stage]} · 第 ${scanning.page} / ${scanning.pages} 页`:"");
    $("reportStatus").textContent=`${stageLabels[scanning.stage]} · 第 ${scanning.page} / ${scanning.pages} 页`;
  }else reportScan.intake("");
  const finished=jobs.filter(j=>j.state==="completed"&&!jobSeen.has(j.id));
  jobs.filter(j=>!["queued","running"].includes(j.state)).forEach(j=>jobSeen.add(j.id));
  if(finished.length){
    const selected=finished.find(j=>j.id===scanJob);
    // Refresh the scoped queue without recursively polling.
    if(selected && !busy){scanJob=null;await work("正在打开已完成的报告诊断…",async()=>{await load(state.lab_id);if(ctx===currentReportContext()){await openReport(selected.result.extraction_id);notice("报告已完成归档、识别和基础诊断，对照结果可在下方诊断报告查看。");}});}
    else if(selected){jobSeen.delete(selected.id);}
  }
  const failedScan=jobs.find(j=>j.id===scanJob&&["failed","interrupted"].includes(j.state));
  if(failedScan){scanJob=null;displayedScan=null;reportScan.intake("");$("reportStatus").textContent=failedScan.error?.message||"识别未完成";}
  if(active.length||jobs.some(j=>!jobSeen.has(j.id)))reportPoll=setTimeout(()=>refreshReportJobs().catch(e=>notice(e.message,true)),800);
}

$("rereadReport").onclick=()=>work("正在提交原件重新识别任务…",async()=>{
  scoped();if(!extraction)throw new Error("请先选择报告。");
  await submitExistingReport(extraction.payload.source_id);
  notice($("ocrEngine").value==="paddle"?"已提交模型增强识别，本地CPU处理每页可能需要数分钟，旧版本保留。":"已提交重新识别，旧版本保留。");
});

// Personal display preference only; this does not confirm laboratory settings.
try{
  const saved=localStorage.getItem("labcal.ocr-engine");
  if(["auto","tesseract","paddle"].includes(saved))$("ocrEngine").value=saved;
}catch{}
$("ocrEngine").onchange=()=>{
  try{localStorage.setItem("labcal.ocr-engine",$("ocrEngine").value);}catch{}
  notice($("ocrEngine").value==="paddle"?"已选择 PaddleOCR-VL-1.6。点击“重新识别并诊断”处理当前报告；每页可能需数分钟。":"已切换识别方式。点击“重新识别并诊断”更新当前报告，或直接导入新报告。");
};

$("modelFindings").onclick=()=>work("正在打开智能问题接入…",async()=>{
  scoped();if(!extraction)throw new Error("请先选择报告。");
  if(typeof closeReportPanel === "function")closeReportPanel();
  await renderIntelligenceResults();$("intelligenceDialog").showModal();
});
$("prepareCurrentPacket").onclick=()=>work("正在准备固定审查包…",async()=>{
  scoped();if(!extraction)throw new Error("请先选择报告。");
  await prepareIntelligencePacket([extraction.id]);
});
