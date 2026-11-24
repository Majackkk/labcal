"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="labcal-session"]').content;
const labels = {certificate_number:"证书编号",issuer:"出证机构",manufacturer:"厂家",client:"委托单位",instrument_name:"器具名称",model:"型号规格",instrument_number:"器具编号",activity_date:"实施日期",issue_date:"签发日期",method:"实施依据",environment:"环境条件",traceability:"溯源信息",results:"测量结果",uncertainty:"不确定度",approval:"批准签署",verification_conclusion:"检定结论",verification_valid_until:"检定有效截止日"};
const statuses = {passed:"符合",failed:"不符合",pending:"待确定",not_applicable:"不适用"};
let state, extraction, review, page = 1, selectedLocation, selectedFinding, busy = false;
let currentCampaign = null, listPage = 1, activeModule = "overview";
const selectedExtractions = new Set();
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function notice(message, error=false) { $("notice").hidden=false; $("notice").textContent=message; $("notice").className=error?"error":""; $("appStatus").textContent=message; }
async function api(path, body) {
  if (body && body.lab_id && path !== "/api/laboratories" && path !== "/api/campaigns") body = {campaign_id:currentCampaign, ...body};
  const response = await fetch(path, body ? {method:"POST",headers:{"Content-Type":"application/json","X-Labcal-Token":token},body:JSON.stringify(body)} : {});
  const data = await response.json(); if (!response.ok || !data.ok) throw new Error(data.error?.message || "操作未完成"); return data.result;
}
async function work(message, action) {
  if (busy) return;
  busy=true; notice(message); $("runReview").disabled=true;
  for(const id of ["lab","year","campaign","documentSelect"]) $(id).disabled=true;
  try { await action(); if($("notice").textContent===message)notice("资料已就绪。"); } catch(e) { notice(e.message,true); } finally { busy=false; $("runReview").disabled=false; for(const id of ["lab","year","campaign","documentSelect"]) $(id).disabled=false; }
}
const query = params => new URLSearchParams({lab:state.lab_id,campaign:currentCampaign||"",...params}).toString();
async function load(lab, campaign=currentCampaign) {
  currentCampaign=campaign||null;
  state = await api("/api/state?"+new URLSearchParams({...(lab?{lab}:{}),...(currentCampaign?{campaign:currentCampaign}:{}),list_page:listPage,search:$("certificateSearch").value}));
  $("lab").innerHTML='<option value="">选择实验室</option>'+state.laboratories.map(l=>`<option value="${esc(l.id)}">${esc(l.name)}</option>`).join(""); $("lab").value=state.lab_id||"";
  const oldBaseline=$("baseline").value;
  $("baseline").innerHTML='<option value="">基础审查 · 暂不绑定方案</option>'+state.baselines.map(b=>`<option value="${esc(b.id)}">${esc(b.plan_id)} · 版本${b.revision}</option>`).join("");
  if(state.baselines.some(b=>b.id===oldBaseline)) $("baseline").value=oldBaseline;
  $("certificateList").innerHTML=state.certificates.map(c=>`<div class="certificate-card"><span title="${esc(c.name)}">${esc(c.name)}${c.version_count>1?` <small>保留 ${c.version_count} 个识别版本</small>`:""}</span><button class="quiet" data-preview="${esc(c.extraction_id)}">打开审查</button></div>`).join("")||'<p class="muted">接入PDF，或读取下方已登记的证书。</p>';
  document.querySelectorAll("input[data-extraction]").forEach(x => { x.checked = selectedExtractions.has(x.dataset.extraction); x.onchange=()=>{if(x.checked)selectedExtractions.add(x.dataset.extraction);else selectedExtractions.delete(x.dataset.extraction);}; });
  const registered=new Set(state.certificates.map(c=>c.source_id));
  const unread=state.sources.filter(s=>s.kind!=="institution_scope"&&!registered.has(s.id));
  $("sourceDetails").hidden=!unread.length;
  $("sourceList").innerHTML=unread.map(s=>`<button class="quiet" data-source="${esc(s.id)}">读取 · ${esc(s.name)}</button>`).join("");
  $("documentSelect").innerHTML=state.certificates.map(c=>`<option value="${esc(c.extraction_id)}">${esc(c.name)}</option>`).join("");
  if(extraction && state.certificates.some(c=>c.extraction_id===extraction.id)) $("documentSelect").value=extraction.id;
  $("queueSummary").textContent=`${state.report_count??state.totals.extraction??0} 份报告 · ${state.totals.extraction||0} 个识别版本 · 当前实验室和批次`;
  if(typeof renderConsole === "function") renderConsole();
  if(typeof refreshReportJobs === "function" && currentCampaign) await refreshReportJobs();
}
async function preview(id, newPage=1) {
  if(review&&!review.payload.certificate_snapshots.some(s=>s.extraction_id===id)){review=null;$("export").disabled=true;$("findings").innerHTML="";}
  const previewContext=`${state.lab_id}/${currentCampaign}`,nextExtraction=await api("/api/extraction?"+query({id}));
  if(previewContext!==`${state.lab_id}/${currentCampaign}`)return;
  if(extraction?.id!==nextExtraction.id)selectedFinding=null;
  extraction=nextExtraction; page=newPage; selectedLocation=null;
  $("anchorLabel").textContent="点击信息、诊断或问题圆点，可定位对应原页。";
  if(!Array.from($("documentSelect").options).some(o=>o.value===id)){const option=document.createElement("option");option.value=id;option.textContent="历史识别版本 · "+id;$("documentSelect").append(option);}
  $("documentSelect").value=id; $("documentEmpty").hidden=true; $("pageWrap").hidden=false;
  showPage();
  if(typeof showRecognizedInfo === "function") await showRecognizedInfo(id);
}
function showPage() {
  $("pageLabel").textContent=`${page} / ${extraction.payload.pages.length}`;
  $("pageImage").src="/api/page?"+query({id:extraction.id,page});
  $("prevPage").disabled=page<=1; $("nextPage").disabled=page>=extraction.payload.pages.length;
  drawOverlay();fitPage();
}
function fitPage() {
  const image=$("pageImage"),wrap=$("pageWrap"),zoom=$("pageZoom").value;
  if(zoom==="fit"&&image.naturalWidth&&image.naturalHeight){
    const gutter=typeof problemCalloutGutter === "function"?problemCalloutGutter():0;
    const width=Math.min(Math.max(220,wrap.clientWidth-14-gutter),(wrap.clientHeight-14)*image.naturalWidth/image.naturalHeight);
    $("pageCanvas").style.width=Math.max(80,width)+"px";
  }else {const gutter=typeof problemCalloutGutter === "function"?problemCalloutGutter():0;$("pageCanvas").style.width=(Number(zoom)||1)*Math.max(220,wrap.clientWidth-14-gutter)+"px";}
  if(typeof renderProblemCallouts === "function")renderProblemCallouts();
}
$("pageImage").addEventListener("load",fitPage);
new ResizeObserver(fitPage).observe($("pageWrap"));
function boxStyle(box) { return `left:${box[0]*100}%;top:${box[1]*100}%;width:${(box[2]-box[0])*100}%;height:${(box[3]-box[1])*100}%`; }
function drawOverlay() {
  if(typeof renderProblemCallouts === "function")renderProblemCallouts();
  const layer=$("overlay"); layer.innerHTML="";
  if(!extraction) return;
  const data=extraction.payload.pages[page-1];
  data.lines.forEach(line=>{
    const region=document.createElement("div"); region.className="anchor-box text-line"; region.style.cssText=boxStyle(line.bbox); region.title=line.text;
    region.onclick=()=>{selectedLocation={page,bbox:line.bbox,line_ids:[line.id],quote:line.text,kind:"text"}; $("anchorLabel").textContent=`第${page}页：${line.text.slice(0,65)}`;drawOverlay();}; layer.append(region);
  });
  if(review) review.payload.findings.filter(f=>DiagnosisState.isContent(f)&&f.location.kind!=="document"&&f.extraction_id===extraction.id&&f.location.page===page).forEach(f=>{
    if(["dismissed","resolved"].includes(review.resolutions[f.id]?.action)) return;
    const region=document.createElement("div");region.className="anchor-box "+(f.certainty==="confirmed"?"confirmed":"");region.style.cssText=boxStyle(f.location.bbox);region.title=f.title;region.onclick=()=>focusFinding(f);layer.append(region);
  });
  if(selectedLocation?.page===page){const region=document.createElement("div");region.className="anchor-box selected";region.style.cssText=boxStyle(selectedLocation.bbox);region.style.pointerEvents="none";layer.append(region);}
}
async function openReview(id) {
  review=await api("/api/review?"+query({id})); $("export").disabled=false; $("downloads").hidden=true;
  $("axes").innerHTML=Object.entries({document:"证书资料",coverage:"方案覆盖",fitness:"用途判定"}).map(([key,label])=>`<div class="${review.payload.axes[key]}">${label}<strong>${statuses[review.payload.axes[key]]}</strong></div>`).join("");
  renderFindings();
  $("baseline").value = review.payload.baseline_id || "";
  const selected = new Set(review.payload.certificate_snapshots.map(x => x.extraction_id));
  selectedExtractions.clear(); selected.forEach(id=>selectedExtractions.add(id));
  document.querySelectorAll("input[data-extraction]").forEach(x => { x.checked = selected.has(x.dataset.extraction); });
  if(review.payload.certificate_snapshots.length) await preview(review.payload.certificate_snapshots[0].extraction_id);
}
function renderFindings() {
  if(!review) return;
  const filter=$("findingFilter").value;
  const rows=review.payload.findings.filter(f=>DiagnosisState.isContent(f)&&(filter==="all" || (filter==="processed"?review.resolutions[f.id]:!review.resolutions[f.id]&&f.certainty===filter)));
  $("findingCount").textContent=rows.length+" 项";
  $("findings").innerHTML=rows.map(f=>{
    const resolved=review.resolutions[f.id]; const stateLabel=resolved?({confirmed:"问题已确认",dismissed:"已排除",resolved:"已关闭"}[resolved.action]):(f.certainty==="confirmed"?"已证实":"待核实");
    return `<article class="finding ${resolved?"processed":""} ${selectedFinding?.id===f.id?"active":""}" data-finding="${esc(f.id)}"><div class="finding-top"><span>${esc(f.id)} · 第${f.location.page}页</span><span class="pill ${f.certainty}">${stateLabel}</span></div><h3>${esc(f.title)}</h3><p>${esc(f.message)}</p><p><b>依据：</b>${esc(f.basis)}</p><p><b>建议：</b>${esc(f.suggestion)}</p>${resolved?`<p>复核：${esc(resolved.reviewer)} · ${esc(resolved.reason)}</p>`:""}</article>`;
  }).join("")||'<div class="empty small">当前筛选下没有问题。</div>';
}
async function focusFinding(f) {
  if(typeof closeReportPanel === "function")closeReportPanel();
  selectedFinding=f;
  if(extraction?.id!==f.extraction_id) await preview(f.extraction_id,f.location.page); else {page=f.location.page;showPage();}
  selectedLocation=f.location; $("anchorLabel").textContent=`${f.id} · 第${page}页 · ${f.title}`;drawOverlay();renderFindings();
}
async function fileBody(file) {
  if(file.size>30*1024*1024) throw new Error("工作台单个文件最多30MB，较大文件请通过项目智能体接入。");
  const bytes=new Uint8Array(await file.arrayBuffer()); let binary="";
  for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
  return {filename:file.name,file_base64:btoa(binary)};
}
function resetWorkspace() {
  if(typeof resetReports === "function") resetReports();
  if(typeof resetConsole === "function") resetConsole();
  selectedExtractions.clear(); listPage=1; $("certificateSearch").value="";
  extraction = review = selectedLocation = selectedFinding = null;
  $("documentEmpty").hidden = false; $("pageWrap").hidden = true; $("export").disabled = true;
  $("downloads").hidden = true; $("findings").innerHTML = ""; $("certificateList").innerHTML = "";
  $("findingCount").textContent = "0 项";
  $("axes").innerHTML = "<div>证书资料<strong>待审查</strong></div><div>方案覆盖<strong>待绑定</strong></div><div>用途判定<strong>待绑定</strong></div>";
}
$("lab").onchange = () => work("正在切换实验室…", async () => { const id = $("lab").value; resetWorkspace(); await load(id,null); notice("请选择年度和校准批次。"); });
$("refresh").onclick=()=>work("正在刷新…",async()=>{await load(state.lab_id);if(activeModule==="certificates"&&extraction)await showRecognizedInfo(extraction.id);notice("资料已刷新。");});
document.addEventListener("click",event=>{
  const target=event.target.closest("button,article[data-finding]");if(!target)return;
  if(target.dataset.close)$(target.dataset.close).close();
  if(target.dataset.preview)work("正在读取原页…",async()=>{await openReport(target.dataset.preview);notice("已打开报告、识别信息与诊断。");});
  if(target.dataset.source)work("正在读取已有证书…",async()=>{await submitExistingReport(target.dataset.source);notice("已有原件已加入识别队列。");});
  if(target.dataset.review)work("正在打开审查记录…",async()=>{await openReview(target.dataset.review);notice("已打开固定审查记录。");});
  if(target.dataset.finding)focusFinding(review.payload.findings.find(f=>f.id===target.dataset.finding)).catch(e=>notice(e.message,true));
});
$("documentSelect").onchange=()=>work("正在读取原页…",async()=>{await openReport($("documentSelect").value);notice("已切换预览证书。");});
$("prevPage").onclick=()=>{if(extraction&&page>1){page--;selectedLocation=null;showPage();}};
$("nextPage").onclick=()=>{if(extraction&&page<extraction.payload.pages.length){page++;selectedLocation=null;showPage();}};
$("findingFilter").onchange=renderFindings;
$("export").onclick=()=>work("正在生成固定诊断摘要、逐点对照和批注副本…",async()=>{
  if(!review||reportView?.diagnosis_stale)throw new Error('请先更新诊断。');
  const result=await api("/api/exports",{lab_id:state.lab_id,review_id:review.id,annotation_style:"callouts"});
  const names={'diagnosis.html':'完整诊断报告（HTML）','comparison.xlsx':'逐点对照（Excel）','comparison.csv':'逐点对照（CSV）','summary.md':'诊断摘要','issues.md':'内容问题清单','diagnosis.json':'固定诊断数据'};
  $("downloads").innerHTML='<strong>完整诊断包已生成</strong>'+result.manifest.artifacts.filter(f=>f.name.endsWith('.pdf')||names[f.name]).map(f=>`<a href="/api/download?${query({run:result.run_id,name:f.name})}">${esc(f.name.endsWith('.pdf')?'原页引线批注副本':names[f.name])}</a>`).join("");
  $("downloads").hidden=false;$("exportDialog").showModal();notice("摘要、逐点对照与批注副本已生成，引用同一诊断快照；原件和方案保留。");
});
for(const [button,dialog] of [["newLab","labDialog"],["equipmentButton","equipmentDialog"],["institutionButton","institutionDialog"]])$(button).onclick=()=>{if(button!=="newLab"&&!state.lab_id){notice("请先新建实验室。",true);return;}$(dialog).showModal();};
$("labForm").onsubmit=event=>{event.preventDefault();work("正在创建实验室…",async()=>{const id=$("labId").value.trim();await api("/api/laboratories",{id,name:$("labName").value.trim(),laboratory_type:$("labType").value,profile:$("labProfile").value});$("labDialog").close();resetWorkspace();await load(id,null);notice("实验室已建立。创建校准批次后接入资料。");});};
$("equipmentForm").onsubmit=event=>{event.preventDefault();work("正在接入设备…",async()=>{await api("/api/equipment",{lab_id:state.lab_id,asset_code:$("equipmentAsset").value.trim(),name:$("equipmentName").value.trim(),model:$("equipmentModel").value.trim()});$("equipmentDialog").close();await load(state.lab_id);notice("设备已接入，实际配置继续通过方案流程整理。");});};
$("institutionForm").onsubmit=event=>{event.preventDefault();work("正在接入机构范围资料…",async()=>{const source=await api("/api/source",{lab_id:state.lab_id,...await fileBody($("institutionFile").files[0])});const record={source_id:source.id,source_hash:source.sha256,institution:$("institutionName").value.trim(),scheme:$("institutionScheme").value,valid_from:$("institutionFrom").value,valid_until:$("institutionUntil").value,authority_url:$("institutionUrl").value,anchor:$("institutionAnchor").value.trim(),parameters:[{parameter:$("institutionParameter").value.trim(),unit:$("institutionUnit").value.trim(),minimum:$("institutionMin").value.trim(),maximum:$("institutionMax").value.trim()}],reviewer:$("institutionReviewer").value.trim(),reviewed_at:new Date().toISOString()};await api("/api/institutions",{lab_id:state.lab_id,record});$("institutionDialog").close();notice("范围原件及核验记录已保存；该记录只包含填写的项目。");});};
work("正在读取中控台资料…",async()=>{
  const params=new URLSearchParams(location.search);let remembered=null;
  if(!params.has("lab"))try{remembered=JSON.parse(localStorage.getItem("labcal.last-context"));}catch{}
  try{await load(params.get("lab")||remembered?.lab,params.get("campaign")||remembered?.campaign);}
  catch(e){if(!remembered)throw e;await load(null,null);remembered=null;}
  showModule(params.get("module")||remembered?.module||"overview");
  if(activeModule==="certificates")await ensureReportOpen();notice("中控台已就绪。选择实验室和批次进入工作。");
});
