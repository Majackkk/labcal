"use strict";
let workbookView=null, workbookPage=1, planView=null, campaignEditing=false, libraryPage=1;
function scoped() { if(!state?.lab_id||!currentCampaign) throw new Error("请先选择实验室与校准批次。"); }
function showModule(name) {
  const previousModule=activeModule;
  activeModule=["overview","plans","certificates"].includes(name)?name:"overview";
  if(previousModule!==activeModule)window.scrollTo(0,0);
  document.body.dataset.module=activeModule;
  for(const [key,id] of [["overview","overviewModule"],["plans","plansModule"],["certificates","certificateModule"]]) $(id).hidden=key!==activeModule;
  document.querySelectorAll("[data-module]").forEach(b=>b.classList.toggle("active",b.dataset.module===activeModule));
  $("moduleTitle").textContent={overview:"实验室校准中控台",plans:"校准方案",certificates:"证书审查"}[activeModule];
  const params=new URLSearchParams(); if(state?.lab_id)params.set("lab",state.lab_id);if(currentCampaign)params.set("campaign",currentCampaign);params.set("module",activeModule);
  history.replaceState(null,"","/?"+params);
  if(state?.lab_id)try{localStorage.setItem("labcal.last-context",JSON.stringify({lab:state.lab_id,campaign:currentCampaign,module:activeModule}));}catch{}
}
function resetConsole() {
  workbookView=planView=null;workbookPage=1;
  $("workbookPanel").hidden=true;$("workbookRows").innerHTML="";$("baseline").value="";
  document.querySelectorAll("dialog[open]").forEach(d=>d.close());
}
function renderConsole() {
  const lab=state.laboratories.find(l=>l.id===state.lab_id), c=state.campaign;
  $("contextLabel").textContent=lab?[lab.name,c?`${c.year||"年度待定"} / ${c.name}`:"选择校准批次"].join(" / "):"选择实验室与校准批次，进入本次工作。";
  const previousYear=$("year").value;
  const years=[...new Set(state.campaigns.map(x=>String(x.year||"undated")))];
  $("year").innerHTML='<option value="all">全部年度</option>'+years.map(y=>`<option value="${y}">${y==="undated"?"年度待定":y+" 年"}</option>`).join("");
  $("year").value=c?String(c.year||"undated"):years.includes(previousYear)?previousYear:"all";
  renderCampaignChoices();
  const metrics=c?[["方案提取版本",state.totals.workbook||0],["方案版本",state.totals.plan||0],["证书版本",state.totals.extraction||0],["审查任务",state.totals.review||0]]:
    lab?[["校准批次",state.campaigns.length],["设备台账",state.equipment.length],["未归档证书版本",state.unfiled?.extraction||0],["未归档审查任务",state.unfiled?.review||0]]:[];
  $("overviewCards").innerHTML=metrics.length?metrics.map(([label,value])=>`<div class="console-card"><span>${label}</span><strong>${value}</strong></div>`).join(""):
    state.laboratories.map(l=>`<button class="console-card lab-card" data-lab-select="${esc(l.id)}"><span>${esc(l.id)}</span><h3>${esc(l.name)}</h3><p>进入实验室 ↗</p></button>`).join("");
  renderCampaignCards();
  $("workbookList").innerHTML=state.workbooks.map(w=>`<div class="history-row"><div><strong>${esc(w.name)}</strong><p class="muted">${w.groups} 个原文分组 · ${w.candidates} 行候选参数 · 未确认</p></div><div><button class="quiet" data-workbook="${esc(w.id)}">查看自动分类 ↗</button>${w.previous_versions?.length?`<details><summary>历史提取（${w.previous_versions.length}）</summary>${w.previous_versions.map(v=>`<button class="quiet" data-workbook="${esc(v.id)}">${esc(new Date(v.created_at).toLocaleString())}</button>`).join("")}</details>`:""}</div></div>`).join("")||'<p class="muted">在当前批次接入XLSX方案，系统自动整理设备和参数。</p>';
  $("planList").innerHTML=state.plans.map(p=>`<div class="history-row"><div><strong>${esc(p.title)}</strong><p class="muted">版本 ${p.revision} · ${p.requirements} 项 · ${{draft:"候选稿",confirmed:"已确认",superseded:"已替代"}[p.state]||esc(p.state)}</p></div><button class="quiet" data-plan="${esc(p.id)}" data-revision="${p.revision}">查看版本 ↗</button></div>`).join("")||'<p class="muted">工作簿已自动分类为候选清单。已结构化的设备方案在这里管理具体版本与确认。</p>';
  const pages=Math.max(1,Math.ceil((state.certificate_matches||0)/state.page_size));
  $("certificatePage").textContent=`${state.page} / ${pages} · ${state.certificate_matches||0} 份`;
  $("certPrev").disabled=state.page<=1;$("certNext").disabled=state.page>=pages;
  showModule(activeModule);
}
function renderCampaignChoices() {
  const year=$("year").value;
  $("campaign").innerHTML='<option value="">选择校准批次</option>'+state.campaigns.filter(c=>year==="all"||String(c.year||"undated")===year).map(c=>`<option value="${esc(c.id)}">${esc(c.name)}${c.state==="archived"?" · 已归档":""}</option>`).join("");
  $("campaign").value=currentCampaign||"";
}
function renderCampaignCards() {
  const year=$("year").value,rows=state.campaigns.filter(c=>year==="all"||String(c.year||"undated")===year);
  $("campaignSummary").textContent=state.lab_id?rows.length+" 个批次":"先选择实验室";
  $("campaignCards").innerHTML=rows.map(c=>`<button class="console-card ${c.id===currentCampaign?"current":""}" data-campaign-select="${esc(c.id)}"><span>${c.year||"年度待定"} / ${c.state==="archived"?"已归档":"进行中"}</span><h3>${esc(c.name)}</h3><p>${esc(c.notes||"进入批次工作")} ↗</p></button>`).join("")||'<p class="muted">选择实验室后创建年度校准、补充校准等批次。</p>';
}
async function changeContext(lab,campaign) { resetWorkspace();resetConsole();await load(lab,campaign); }
document.addEventListener("click",event=>{
  const b=event.target.closest("button");if(!b)return;
  if(b.dataset.module)work("正在切换工作模块…",async()=>{showModule(b.dataset.module);if(b.dataset.module==="certificates"){if(extraction)await showRecognizedInfo(extraction.id);else await ensureReportOpen();}});
  if(b.dataset.labSelect)work("正在进入实验室…",async()=>{await changeContext(b.dataset.labSelect,null);showModule("overview");});
  if(b.dataset.campaignSelect)work("正在进入校准批次…",async()=>{await changeContext(state.lab_id,b.dataset.campaignSelect);showModule("overview");});
  if(b.dataset.workbook)work("正在读取候选方案…",async()=>{await openWorkbook(b.dataset.workbook);notice("已自动分类设备与参数；原文及限值仍保持候选。");});
  if(b.dataset.deviceGroup){$("workbookGroup").value=b.dataset.deviceGroup;workbookPage=1;renderWorkbook();}
  if(b.dataset.plan)work("正在读取具体方案版本…",async()=>openPlan(b.dataset.plan,Number(b.dataset.revision)));
  if(b.dataset.attach)work("正在引用资料…",async()=>{scoped();await api("/api/campaigns/attach",{lab_id:state.lab_id,kind:$("libraryKind").value,entity_id:b.dataset.attach,revision:Number(b.dataset.revision),expected_hash:b.dataset.hash});await load(state.lab_id);notice("固定资料版本已加入当前批次。");});
  if(b.dataset.draftEvidence)work("正在打开候选结果原页…",async()=>openDraftEvidence(Number(b.dataset.draftEvidence),Number(b.dataset.recordIndex),b.dataset.fieldName,b.dataset.observationIndex));
});
$("lab").onchange=()=>work("正在切换实验室…",async()=>{const id=$("lab").value;await changeContext(id,null);showModule("overview");notice("请选择年度及校准批次。");});
$("year").onchange=()=>work("正在切换年度…",async()=>{const y=$("year").value;await changeContext(state.lab_id,null);$("year").value=y;renderCampaignChoices();renderCampaignCards();notice("请选择本年度的校准批次。");});
$("campaign").onchange=()=>work("正在切换批次…",async()=>{const id=$("campaign").value;await changeContext(state.lab_id,id);notice("已切换批次，资料选择已清空。");});
$("newCampaign").onclick=()=>{if(!state.lab_id){notice("请先选择或创建实验室。",true);return;}campaignEditing=false;$("campaignForm").reset();$("campaignState").disabled=true;$("campaignDialogTitle").textContent="新建校准批次";$("campaignDialog").showModal();};
$("editCampaign").onclick=()=>{try{scoped();campaignEditing=true;const c=state.campaign;$("campaignDialogTitle").textContent="批次信息与归档";$("campaignName").value=c.name;$("campaignYear").value=c.year||"";$("campaignNotes").value=c.notes||"";$("campaignState").disabled=false;$("campaignState").value=c.state;$("campaignDialog").showModal();}catch(e){notice(e.message,true);}};
$("campaignForm").onsubmit=e=>{e.preventDefault();work("正在保存批次…",async()=>{const values={name:$("campaignName").value.trim(),year:$("campaignYear").value?Number($("campaignYear").value):null,notes:$("campaignNotes").value.trim()};const c=campaignEditing?await api("/api/campaigns/update",{lab_id:state.lab_id,record:{...values,state:$("campaignState").value,expected_record_hash:state.campaign.record_hash}}):await api("/api/campaigns",{lab_id:state.lab_id,...values});$("campaignDialog").close();await changeContext(state.lab_id,c.id);notice("批次已保存。");});};
$("workbookUpload").onchange=()=>work("正在接入方案原件…",async()=>{scoped();const file=$("workbookUpload").files[0];if(!file)return;const r=await api("/api/workbooks",{lab_id:state.lab_id,...await fileBody(file)});await load(state.lab_id);showModule("plans");await openWorkbook(r.workbook_id);$("workbookUpload").value="";notice("候选方案已接入当前批次，原件保持留存。");});
async function openWorkbook(id) {
  workbookView=await api("/api/workbook?"+query({id}));workbookPage=1;$("workbookSearch").value="";
  const groups=[...new Set(workbookView.candidate_view.map(r=>r.group))];
  $("workbookGroup").innerHTML='<option value="">全部分组</option>'+groups.map(g=>`<option>${esc(g)}</option>`).join("");
  $("workbookPanel").hidden=false;$("workbookTitle").textContent="设备与校准参数清单";$("workbookOriginal").href="/api/original?"+query({id:workbookView.payload.source_id});
  $("workbookFolder").hidden=!workbookView.import_directory;$("workbookFolderPath").textContent=workbookView.import_directory||"";
  $("classifiedDevices").innerHTML=workbookView.devices.map(d=>`<button class="device-chip quiet" data-device-group="${esc(d.group)}"><strong>${esc(d.name)}</strong><span>${d.rows.length} 项参数 · ${d.identity.equipment_number?esc(d.identity.equipment_number):"具体设备编号待明确"}</span></button>`).join("");
  renderWorkbook();
}
function candidateRows() { const term=$("workbookSearch").value.trim().toLowerCase(),group=$("workbookGroup").value;return workbookView.candidate_view.filter(r=>(!group||r.group===group)&&(!term||JSON.stringify([r.fields,r.values]).toLowerCase().includes(term))); }
function renderWorkbook() {
  if(!workbookView)return;
  if($("workbookDisplay").value==="points"){renderWorkbookPoints(candidateRows());return;}
  const rows=candidateRows(),pages=Math.max(1,Math.ceil(rows.length/8));workbookPage=Math.min(workbookPage,pages);
  $("workbookStats").textContent=`${workbookView.devices.length} 个候选设备分组 · ${rows.length} 行参数 · 自动分类完成，技术内容尚未确认`;
  const columns=[["equipment_name","设备名称"],["manufacturer","厂家"],["model","设备型号"],["equipment_number","设备编号"],["parameter","校准/检定参数"],["mpe","允许误差 MPE 要求"],["uncertainty","不确定度要求"]];
  $("workbookRows").innerHTML='<table class="data-table workbook-table"><colgroup><col style="width:16%"><col style="width:9%"><col style="width:11%"><col style="width:11%"><col style="width:18%"><col style="width:21%"><col style="width:14%"></colgroup><thead><tr>'+columns.map(([,title])=>`<th>${title}</th>`).join("")+'</tr></thead><tbody>'+rows.slice((workbookPage-1)*8,workbookPage*8).map(r=>'<tr>'+columns.map(([key])=>{
    const value=r.fields[key],source=r.field_sources[key];
    const title=source?`来自 '${source.sheet}'!${source.cell}，候选内容待核对`:"方案中未明确或缺失";
    const cell=value===null||value===undefined||value===""?'<span class="missing-value">-</span>':esc(value);
    const details=key==="parameter"?`<details class="candidate-details"><summary>原文与位置</summary><small>${esc(r.anchor)}</small><p>原文条件 / 点位：${esc(r.values.conditions||"-")}</p><p>${esc(r.values.method_claim||"")}</p><p>${esc(r.values.metrology_claim||"")}</p>${r.formula_cells.length?'<p>含公式，缓存值待核对</p>':""}</details>`:"";
    return `<td title="${esc(title)}">${cell}${details}</td>`;
  }).join("")+'</tr>').join("")+'</tbody></table>';
  $("workbookPage").textContent=`${workbookPage} / ${pages}`;$("workbookPrev").disabled=workbookPage<=1;$("workbookNext").disabled=workbookPage>=pages;

}
$("workbookGroup").onchange=$("workbookSearch").oninput=()=>{workbookPage=1;renderWorkbook();};
$("workbookPrev").onclick=()=>{workbookPage--;renderWorkbook();};$("workbookNext").onclick=()=>{workbookPage++;renderWorkbook();};
async function openPlan(id,revision) {
  planView=await api("/api/plan?"+query({id,revision}));const p=planView.plan,r=planView.readiness;
  $("planTitle").textContent=p.payload.title+" / 版本 "+p.revision;
  $("planStatus").textContent=`${r.user_confirmed?"已有具体确认":"候选稿，未确认"}；覆盖${r.coverage_ready?"已就绪":"待补齐"}；结果判定${r.decision_ready?"已就绪":"待明确"}。`;
  $("planRows").innerHTML='<table class="data-table"><thead><tr><th>参数</th><th>范围 / 点位 / 单位</th><th>待补充事项</th></tr></thead><tbody>'+p.payload.requirements.map(req=>{const check=r.requirements.find(x=>x.requirement_id===req.id);return `<tr><td>${esc(req.id)} / ${esc(req.parameter)}</td><td>${esc(req.points.join(", "))} ${esc(req.unit)}${req.calibration_range?'<br>'+esc(req.calibration_range.minimum)+" ～ "+esc(req.calibration_range.maximum):""}<details><summary>查看来源原文</summary><pre>${esc(JSON.stringify(req.raw?.values||req.raw||{},null,2))}</pre></details></td><td>${esc([...check.coverage_issues,...check.decision_issues].map(issueLabel).join("；")||"结构已就绪")}</td></tr>`;}).join("")+'</tbody></table>';
  $("planEditor").value=JSON.stringify(p.payload,null,2);$("editRequirement").innerHTML=p.payload.requirements.map(x=>`<option value="${esc(x.id)}">${esc(x.id)} / ${esc(x.parameter)}</option>`).join("");fillRequirement();
  $("confirmPlanButton").disabled=!r.coverage_ready||!r.configuration_current||r.user_confirmed;
  $("buildBaseline").disabled=!r.automatic_baseline_available;$("comparePlan").disabled=p.revision<2;$("planDiff").hidden=true;$("planDialog").showModal();
}
function issueLabel(code) {return {DELIVERY_ROUTE_UNDEFINED:"实施方式待明确",UNIT_UNDEFINED:"单位待明确",COVERAGE_UNDEFINED:"覆盖方式待明确",REPORT_FIELDS_UNDEFINED:"报告字段待明确",POINTS_MISSING:"校准点待补充",LEGACY_REQUIREMENT_UNREVIEWED:"原文要求待核对",EVIDENCE_MISSING:"依据待补充",DECISION_RULE_MISSING:"判定规则待明确",PARAMETER_EVIDENCE_REVIEW_PENDING:"依据原件待核验"}[code]||code;}
function fillRequirement() {try{const p=JSON.parse($("planEditor").value),r=p.requirements.find(x=>x.id===$("editRequirement").value);if(!r)return;for(const[k,id]of[["parameter","reqParameter"],["unit","reqUnit"],["delivery_route","reqRoute"],["coverage_policy","reqCoverage"]])$(id).value=r[k];$("reqPoints").value=r.points.join(", ");$("reqFields").value=r.report_fields.join(", ");$("reqMin").value=r.calibration_range?.minimum||"";$("reqMax").value=r.calibration_range?.maximum||"";$("reqClaim").value=r.evidence.every(e=>e.claim_kind==="lab_requirement")?"lab_requirement":"legacy";}catch(e){notice("方案结构无法读取，请先修正完整结构。",true);}}
$("editRequirement").onchange=fillRequirement;
$("requirementForm").onsubmit=e=>{e.preventDefault();try{const p=JSON.parse($("planEditor").value),r=p.requirements.find(x=>x.id===$("editRequirement").value);for(const[k,id]of[["parameter","reqParameter"],["unit","reqUnit"],["delivery_route","reqRoute"],["coverage_policy","reqCoverage"]])r[k]=$(id).value.trim();const split=id=>$(id).value.split(/[,，\n]/).map(s=>s.trim()).filter(Boolean);r.points=split("reqPoints");r.report_fields=split("reqFields");if($("reqMin").value||$("reqMax").value)r.calibration_range={minimum:$("reqMin").value.trim(),maximum:$("reqMax").value.trim(),unit:r.unit};else delete r.calibration_range;r.evidence=r.evidence.map(ev=>ev.claim_kind==="legacy"||ev.claim_kind==="lab_requirement"?{...ev,claim_kind:$("reqClaim").value}:ev);$("planEditor").value=JSON.stringify(p,null,2);notice("这一项已更新到草稿。保存新版本后重新检查就绪状态。");}catch(err){notice(err.message,true);}};
$("savePlan").onclick=()=>work("正在保存新候选版本…",async()=>{const p=planView.plan,r=await api("/api/plans/save",{lab_id:state.lab_id,plan_id:p.plan_id,revision:p.revision,expected_hash:p.payload_hash,payload:JSON.parse($("planEditor").value)});$("planDialog").close();await load(state.lab_id);await openPlan(r.plan_id,r.revision);notice(r.reused?"内容未变化，保留原版本。":"新候选版本已保存，历史版本保留。");});
$("comparePlan").onclick=()=>work("正在比较方案版本…",async()=>{const p=planView.plan,d=await api("/api/plan-diff?"+query({id:p.plan_id,before:p.revision-1,after:p.revision}));$("planDiff").textContent=`新增：${d.added.join("、")||"无"}\n移除：${d.removed.join("、")||"无"}\n变化：${d.changed.join("、")||"无"}\n设备配置变化：${d.configuration_changed?"是":"否"}`;$("planDiff").hidden=false;});
$("confirmPlanButton").onclick=()=>{$("confirmSummary").textContent=$("planTitle").textContent+" / 摘要 "+planView.plan.payload_hash.slice(0,16);$("confirmForm").reset();$("confirmDialog").showModal();};
$("confirmForm").onsubmit=e=>{e.preventDefault();work("正在记录明确确认…",async()=>{const record={...planView.confirmation_template,confirmed_by:$("planConfirmer").value.trim(),confirmed_at:new Date().toISOString(),evidence_kind:"operator_command",user_statement:$("planStatement").value.trim()};await api("/api/plans/confirm",{lab_id:state.lab_id,record});$("confirmDialog").close();$("planDialog").close();await load(state.lab_id);await openPlan(record.plan_id,record.revision);notice("该具体版本的确认已记录。可生成固定基准。");});};
$("buildBaseline").onclick=()=>work("正在生成固定验收基准…",async()=>{const p=planView.plan;await api("/api/plans/baseline",{lab_id:state.lab_id,plan_id:p.plan_id,revision:p.revision});await load(state.lab_id);notice("固定基准已生成并加入当前批次，可供证书审查选择。");});
async function findLibrary(){scoped();const r=await api("/api/library?"+query({kind:$("libraryKind").value,search:$("librarySearch").value,list_page:libraryPage}));$("libraryRows").innerHTML=r.items.map(x=>`<div class="history-row"><div><strong>${esc(x.name)}</strong><p class="muted">${esc(x.id)}${x.revision?" / 版本 "+x.revision:""}</p></div><button class="quiet" data-attach="${esc(x.id)}" data-revision="${x.revision}" data-hash="${esc(x.entity_hash)}">加入当前批次</button></div>`).join("")||'<p class="muted">没有匹配资料。</p>';$("libraryPage").textContent=`${r.page} / ${Math.max(1,Math.ceil(r.total/20))} · ${r.total} 项`;$("libraryPrev").disabled=libraryPage<=1;$("libraryNext").disabled=libraryPage>=Math.ceil(r.total/20);}
$("libraryButton").onclick=()=>work("正在读取本实验室资料…",async()=>{scoped();libraryPage=1;await findLibrary();$("libraryDialog").showModal();});
$("libraryFind").onclick=$("libraryKind").onchange=()=>work("正在搜索资料…",async()=>{libraryPage=1;await findLibrary();});$("libraryPrev").onclick=()=>work("正在翻页…",async()=>{libraryPage--;await findLibrary();});$("libraryNext").onclick=()=>work("正在翻页…",async()=>{libraryPage++;await findLibrary();});
$("searchCertificates").onclick=()=>work("正在搜索当前批次…",async()=>{listPage=1;await load(state.lab_id);});$("certPrev").onclick=()=>work("正在翻页…",async()=>{listPage--;await load(state.lab_id);});$("certNext").onclick=()=>work("正在翻页…",async()=>{listPage++;await load(state.lab_id);});
let intelligenceResults=[];
async function renderIntelligenceResults(){intelligenceResults=await api("/api/intelligence-results?"+query({}));$("intelligenceResults").innerHTML=intelligenceResults.map((r,ri)=>`<div class="panel"><h3>智能体候选 / ${esc(r.record.author)}</h3>`+r.record.records.map((c,ci)=>Object.entries(c.fields).map(([key,v])=>`<p>${esc(labels[key]||key)}：${esc(v.value)} <button class="quiet" data-draft-evidence="${ri}" data-record-index="${ci}" data-field-name="${esc(key)}">查看原页</button></p>`).join("")+c.observations.map((o,oi)=>`<p>${esc(o.parameter)} / ${esc(o.point||"")} ${esc(o.unit)} <button class="quiet" data-draft-evidence="${ri}" data-record-index="${ci}" data-observation-index="${oi}">查看原页</button></p>`).join("")+`<p class="muted">${c.findings.length} 项候选问题；重新审查可加入原页批注。</p>`).join("")+"</div>").join("")||'<p class="muted">尚未接入智能体候选结果。</p>';}
async function prepareIntelligencePacket(ids){scoped();if(!ids.length)throw new Error("请先选择证书。");const r=await api("/api/intelligence/prepare",{lab_id:state.lab_id,extraction_ids:ids,baseline_id:$("baseline").value||null});const packet=await api("/api/packet?"+query({id:r.packet_id}));const blob=new Blob([JSON.stringify(packet,null,2)],{type:"application/json"});const url=URL.createObjectURL(blob);const old=$("intelligencePacket").querySelector("a");if(old?.href.startsWith("blob:"))URL.revokeObjectURL(old.href);$("intelligencePacket").innerHTML=`<p>已固定 ${ids.length} 份证书的上下文。状态：等待项目智能体处理。</p><a href="${url}" download="${esc(r.packet_id)}.json">下载审查包</a><p class="muted">${esc(r.packet_id)}</p>`;await renderIntelligenceResults();$("intelligenceDialog").showModal();notice("审查包已准备，尚未执行独立模型服务。");}
$("prepareIntelligence").onclick=()=>work("正在固定智能审查上下文…",()=>prepareIntelligencePacket([...selectedExtractions]));
$("intelligenceUpload").onchange=()=>work("正在校验智能体候选结果…",async()=>{
  const file=$("intelligenceUpload").files[0];if(!file)return;
  const record=JSON.parse(await file.text()),ctx=currentReportContext();
  await api("/api/intelligence/result",{lab_id:state.lab_id,record});
  await renderIntelligenceResults();$("intelligenceUpload").value="";
  if(ctx===currentReportContext()&&extraction&&record.records.some(r=>r.extraction_id===extraction.id)){
    $("intelligenceDialog").close();await diagnoseReport();
    notice("候选问题已接入，并更新原页问题引线。关键值与依据仍需回看原页。");
  }else notice("候选问题已接入对应固定版本，打开该报告后可生成问题标注。");
});
async function openDraftEvidence(ri,ci,field,observationIndex){
  const c=intelligenceResults[ri].record.records[ci],value=field?c.fields[field]:c.observations[Number(observationIndex)];
  $("intelligenceDialog").close();await preview(c.extraction_id,value.location.page);selectedLocation=value.location;drawOverlay();
  $("anchorLabel").textContent=`第 ${value.location.page} 页：${value.location.quote||"对应原文"}`;
}

$("labSettingsButton").onclick=()=>{if(!state.lab_id){notice("请先选择实验室。",true);return;}const l=state.laboratories.find(x=>x.id===state.lab_id);$("labSettingsForm").reset();$("settingsLabName").value=l.name;$("settingsLabType").value=l.laboratory_type;$("settingsLabProfile").value=l.profile;$("labSettingsDialog").showModal();};
$("labSettingsForm").onsubmit=e=>{e.preventDefault();work("正在保存实验室设置…",async()=>{await api("/api/laboratories/update",{lab_id:state.lab_id,record:{name:$("settingsLabName").value.trim(),laboratory_type:$("settingsLabType").value,profile:$("settingsLabProfile").value,operator:$("settingsOperator").value.trim(),user_statement:$("settingsStatement").value.trim(),expected_hash:state.laboratory_hash}});$("labSettingsDialog").close();await load(state.lab_id);notice("实验室设置已保存，技术确认状态按具体方案和证据检查。");});};
