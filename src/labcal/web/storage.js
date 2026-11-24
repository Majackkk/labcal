"use strict";
let archivePage=1;
const sourceKindLabels={calibration_certificate:"校准证书",verification_certificate:"检定证书",certificate_document:"证书报告",legacy_plan:"历史方案",legacy_table:"历史参数表",legacy_form:"历史表单",user_plan:"校准方案",institution_scope:"机构范围附件",cnas_document:"CNAS资料",metrology_document:"计量技术资料"};
$("storageButton").onclick=()=>work("正在检查当前资料关联…",async()=>{
  if(!state.lab_id)throw new Error("请先选择实验室。");
  const r=await api("/api/storage?"+query({}));
  $("storageHealth").textContent=`数据库${r.integrity==="ok"?"结构正常":"需要检查"} · 外键异常 ${r.foreign_key_errors.length} · 原件路径缺失 ${r.missing_originals.length} · 本次读取 ${r.query_ms} ms`;
  $("storageRows").innerHTML='<table class="data-table storage-table"><thead><tr><th>模块</th><th>数据范围 / 数量</th><th>数据库记录</th><th>关联文件位置</th></tr></thead><tbody>'+r.modules.map(m=>`<tr><th>${esc(m.module)}</th><td>${esc(m.scope)}${m.count!==null?`<small>${m.count} 条记录</small>`:""}</td><td><code>${esc(m.tables)}</code></td><td><code>${esc(m.path)}</code>${m.exists?"":'<small>尚无资料，首次接入时建立</small>'}</td></tr>`).join("")+"</tbody></table>";
  $("storageAudit").textContent="完整校验按需核对原件摘要、归档快照及导出文件，不修改资料。";
  $("storageDialog").showModal();notice("模块与数据库、文件目录的关联检查完成。");
});
async function renderArchive(){
  const lab=$("archiveSelect").value;if(!lab){$("archiveRows").innerHTML='<p class="muted">没有历史资料档案。</p>';return;}
  const r=await api("/api/archive?"+new URLSearchParams({lab,list_page:archivePage}));
  $("archivePath").textContent=r.directory;
  $("archiveRows").innerHTML='<table class="data-table"><thead><tr><th>原件名称</th><th>资料类型</th><th>原件</th></tr></thead><tbody>'+r.items.map(s=>`<tr><td>${esc(s.name)}</td><td>${esc(sourceKindLabels[s.kind]||s.kind)}</td><td><a href="/api/archive-original?${new URLSearchParams({lab,id:s.id})}">下载原件</a></td></tr>`).join("")+"</tbody></table>";
  const pages=Math.max(1,Math.ceil(r.total/r.page_size));$("archivePage").textContent=`${r.page} / ${pages} · ${r.total} 份原件 · ${r.plans} 份历史方案`;
  $("archivePrev").disabled=r.page<=1;$("archiveNext").disabled=r.page>=pages;
}
$("archivesButton").onclick=()=>work("正在打开独立资料档案…",async()=>{$("archiveSelect").innerHTML=(state.archives||[]).map(a=>`<option value="${esc(a.id)}">${esc(a.name)}</option>`).join("");archivePage=1;await renderArchive();$("archivesDialog").showModal();});
$("archiveSelect").onchange=()=>work("正在切换资料档案…",async()=>{archivePage=1;await renderArchive();});
$("archivePrev").onclick=()=>work("正在读取上一页…",async()=>{archivePage--;await renderArchive();});
$("archiveNext").onclick=()=>work("正在读取下一页…",async()=>{archivePage++;await renderArchive();});
$("equipmentButton").onclick=()=>{
  if(!state.lab_id){notice("请先选择实验室。",true);return;}
  $("ledgerRows").innerHTML=state.equipment.length?'<table class="data-table"><thead><tr><th>设备编号</th><th>设备名称</th><th>型号</th><th>配置状态</th></tr></thead><tbody>'+state.equipment.map(e=>`<tr><td>${esc(e.asset_code)}</td><td>${esc(e.name)}</td><td>${valueCell(e.model)}</td><td>${esc({pending:"配置待明确",confirmed:"已确认",superseded:"已替代"}[e.state]||e.state)}</td></tr>`).join("")+"</tbody></table>":'<div class="empty small">尚未登记正式设备。校准方案中的候选设备可在“校准方案”查看。</div>';
  $("ledgerDialog").showModal();
};
$("addEquipment").onclick=()=>{$("ledgerDialog").close();$("equipmentDialog").showModal();};
$("institutionButton").onclick=()=>work("正在读取机构范围资料…",async()=>{
  if(!state.lab_id)throw new Error("请先选择实验室。");const rows=await api("/api/institutions?"+query({}));
  $("institutionRows").innerHTML=rows.length?'<table class="data-table"><thead><tr><th>机构</th><th>类型</th><th>有效时间</th><th>核对项目</th></tr></thead><tbody>'+rows.map(r=>`<tr><td>${esc(r.institution)}</td><td>${esc(r.scheme)}</td><td>${esc(r.valid_from)} — ${esc(r.valid_until)}</td><td>${r.parameters.map(p=>esc(p.parameter)).join("、")}</td></tr>`).join("")+"</tbody></table>":'<div class="empty small">尚未接入机构能力范围原件与核对项目。</div>';
  $("institutionListDialog").showModal();
});
$("addInstitution").onclick=()=>{$("institutionListDialog").close();$("institutionDialog").showModal();};

$("auditStorage").onclick=()=>work("正在逐项核对原件、归档和导出文件…",async()=>{
  const ctx=`${state.lab_id}/${currentCampaign}`;
  const result=await api("/api/storage?"+query({deep:1}));
  if(ctx!==`${state.lab_id}/${currentCampaign}`)return;
  const a=result.audit, counts=a.counts;
  $("storageAudit").innerHTML=`<p><strong>${a.status==="passed"?"文件与固定版本校验通过":a.status==="warnings"?"存在需要整理的目录索引":"存在文件或记录异常"}</strong> · 原件 ${counts.objects??0} · 固定记录 ${counts.fixed_records??0} · 归档文件 ${counts.archive_files??0} · 导出文件 ${counts.exports??0} · ${a.query_ms} ms</p>`+
    (a.issues.length?'<ul>'+a.issues.map(i=>`<li>${esc(i.message)}${i.path?`<code>${esc(i.path)}</code>`:""}</li>`).join("")+"</ul>":"")+"<p class=muted>此项仅检查软件数据结构和文件完整性，不代表参数技术核验或验收批准。</p>";
  notice("文件与数据库核对已完成。",a.status==="errors");
});
