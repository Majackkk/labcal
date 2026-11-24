"use strict";
const DiagnosisState={
  labels:{concluded:"已判定",not_started:"尚未开展",needs_review:"待核对",missing_input:"缺少资料",unsupported:"需专项核对",not_applicable:"不适用"},
  isContent(f){return (!f.category||f.category==="content_issue")&&!['BASELINE_NOT_BOUND','INSTITUTION_SCOPE_PENDING','DOCUMENT_KIND_PENDING','OCR_UNAVAILABLE','OCR_NOT_RUN','OCR_TIMEOUT','OCR_FAILED','DOCUMENT_TEXT_UNAVAILABLE'].includes(f.code);},
  conclusion(row){return row.status==='passed'?'符合':row.status==='failed'?'不符合':row.status==='not_applicable'?'不适用':this.labels[row.decision_state]||'待核对';}
};
if(typeof module!=="undefined")module.exports={DiagnosisState};
if(typeof document!=="undefined"){
  function renderDiagnosisContext(){
    const box=$("diagnosisContext"),d=reportView?.diagnosis;
    box.hidden=!reportView;if(!reportView)return;
    box.classList.toggle('stale',reportView.diagnosis_stale);
    box.innerHTML=`<strong>${reportView.diagnosis_stale?'诊断待更新':d?.summary.mode==='baseline'?'固定基准诊断':'基础审查 / 候选预对照'}</strong><span>${esc(reportView.diagnosis_stale?reportView.diagnosis_note:d?.preconditions.map(p=>p.text).join(' ')||'请选择报告并开始诊断。')}</span><button class="quiet" data-diagnosis-action="plans">查看方案</button>${d?.preconditions.some(p=>p.code==='INSTITUTION_EVIDENCE_MISSING')?'<button class="quiet" data-diagnosis-action="institution">补机构资料</button>':''}`;
    $("correctRecognition").disabled=!!reportView.diagnosis_stale;
    $("export").disabled=!review||!!reportView.diagnosis_stale||!d;
  }
  window.renderDiagnosisContext=renderDiagnosisContext;
  document.addEventListener('click',e=>{const b=e.target.closest('[data-diagnosis-action]');if(!b)return;if(b.dataset.diagnosisAction==='plans')showModule('plans');else $("institutionButton").click();});
  const editable=Object.keys(labels).filter(k=>k!=='results');
  $("correctionField").innerHTML=editable.map(k=>`<option value="${k}">${esc(labels[k])}</option>`).join('');
  function fillCorrection(){
    const item=reportView?.fields[$("correctionField").value]?.[0];
    $("correctionValue").value=item?.value||'';$("correctionPage").value=item?.location.page||page;
    $("correctionPage").max=extraction.payload.pages.length;$("correctionReason").value='';
  }
  $("correctRecognition").onclick=()=>{if(!extraction||!reportView||reportView.diagnosis_stale)return;if(typeof closeReportPanel==='function')closeReportPanel();fillCorrection();$("correctionDialog").showModal();};
  $("correctionField").onchange=fillCorrection;
  $("locateCorrection").onclick=()=>{const p=Number($("correctionPage").value);if(p<1||p>extraction.payload.pages.length)return;$("correctionDialog").close();focusReportLocation({page:p,bbox:[0,0,1,1],kind:'document',quote:'核对原页后点击更正识别继续'});};
  $("correctionForm").onsubmit=e=>{e.preventDefault();work('正在保存报告更正并更新诊断…',async()=>{
    const key=$("correctionField").value,p=Number($("correctionPage").value),prior=reportView.fields[key]?.[0]?.location;
    const loc=prior&&prior.page===p?prior:{page:p,bbox:[0,0,1,1],kind:'document',quote:$("correctionValue").value};
    await api('/api/reports/correct-field',{lab_id:state.lab_id,extraction_id:extraction.id,change:{field:key,value:$("correctionValue").value,reason:$("correctionReason").value,location:loc,expected_fields_hash:reportView.fields_hash}});
    $("correctionDialog").close();await diagnoseReport();notice('报告识别更正和新诊断已保存；方案及原件未改动。');
  });};
}
