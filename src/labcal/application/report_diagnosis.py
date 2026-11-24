"""Fixed report-diagnosis views: read plans, never author or confirm them."""
from collections import Counter

from ..domain.certificate_review import CHECKS_VERSION, status
from ..domain.contracts import validate_contract
from ..util import content_hash
from .certificates import get_extraction, institution_records

ENGINE = "report-diagnosis-1.0"
STATE_LABELS = {"concluded":"已判定", "not_started":"尚未开展", "needs_review":"待核对",
                "missing_input":"缺少资料", "unsupported":"需专项核对", "not_applicable":"不适用"}
SYSTEM_CODES = {"BASELINE_NOT_BOUND", "INSTITUTION_SCOPE_PENDING", "OCR_UNAVAILABLE", "OCR_NOT_RUN",
    "OCR_TIMEOUT", "OCR_FAILED", "DOCUMENT_TEXT_UNAVAILABLE", "DOCUMENT_KIND_PENDING"}


def finding_category(finding):
    if finding["code"] == "BASELINE_NOT_BOUND": return "precondition"
    if finding["code"] in SYSTEM_CODES: return "evidence_gap"
    return "content_issue"


def diagnostic_projection(review, extraction_id):
    p=review["payload"]; checks=[c for c in p["checks"] if c.get("extraction_id")==extraction_id]
    result=[]
    def add(key,title,selected,state,reason,action,conclusion="pending"):
        result.append({"key":key,"title":title,"status":conclusion,"state":state,"state_label":STATE_LABELS[state],
            "reason":reason,"next_action":action,"checks":selected})
    info=[c for c in checks if c.get("field") in {"certificate_number","client","instrument_name","model","instrument_number","activity_date","issue_date","date_order","approval","document_kind"}]
    missing=[c["label"] for c in info if not c.get("candidate_present",True) and c["status"]!="passed"]
    add("information","设备身份与信息准确性",info,"missing_input" if missing else "needs_review",
        "缺少可用识别信息："+"、".join(missing) if missing else "已列出识别线索；准确性还需原页和实际设备交叉证据。",
        "更正空缺或错误的识别值，并澄清具体设备；更正不会确认方案。", "failed" if any(c["status"]=="failed" for c in info) else "pending")
    trace=[c for c in checks if c.get("field") in {"traceability","issuer"}]
    institutions=[r for r in p["institution_checks"] if r["extraction_id"]==extraction_id]
    trace_ok=bool(trace and institutions) and all(c["status"]=="passed" for c in trace) and all(r["status"]=="passed" and r.get("cmc_status")!="pending" for r in institutions)
    cmc=any("CMC_REVIEW_REQUIRED" in r.get("issues",[]) for r in institutions)
    add("traceability","机构能力与计量溯源",trace,"concluded" if trace_ok else "unsupported" if cmc else "missing_input",
        "具体项目、活动日期和溯源证据已核对。" if trace_ok else "CMC公式需专项评价。" if cmc else "尚缺具体项目、活动日期或相应溯源路线证据。",
        "接入适用日期的官方范围及必要溯源证据；其他路线按其实际要求评价。", "passed" if trace_ok else "pending")
    method=[c for c in checks if c.get("field") in {"method","environment"}]
    add("method","方法与标准适用性",method,"unsupported",
        "方法文字存在不能证明其适用；设备原理、量程、规程版本、环境及偏离需专项核对。",
        "提供适用规范的具体版本及条款；系统目前不自动完成任意规程的语义适用性批准。")
    for key,title,axis in [("coverage","参数与点位覆盖","coverage"),("results","结果与使用要求","fitness")]:
        axis_status=p["axes"][axis]
        add(key,title,[],"not_started" if not p["baseline_id"] else "concluded" if axis_status!="pending" else "missing_input",
            "尚未绑定用户确认的具体方案版本，仅作候选预对照。" if not p["baseline_id"] else "按固定确认基准与原页证据计算；对应状态与符合结论分开显示。",
            "在方案编制线路确认具体版本并生成基准。" if not p["baseline_id"] else "查看逐点未判定原因，补齐对应结果或判定输入。",axis_status)
    uncertain=[c for c in checks if c.get("field")=="uncertainty"]
    decisions=[d for c in p["comparisons"] for d in c["decisions"]]
    u_decisions=[d for d in decisions if "uncertainty_conforms" in d]
    u_ok=bool(u_decisions) and all(d["uncertainty_conforms"] for d in u_decisions) and all(c["status"]=="passed" for c in uncertain)
    add("uncertainty","不确定度适用性",uncertain,"concluded" if u_ok else "needs_review",
        "已按确认方案比较不确定度要求。" if u_ok else "需明确U数值、单位、包含因子、相对基准及其适用结果；整表声明不能直接套用全部点。",
        "核对原页不确定度声明与相应点位；用于溯源的检定结果按实际用途补齐证据。", "passed" if u_ok else "pending")
    integrity=[c for c in checks if c.get("field") in {"printed_page_sequence","cross_page_number","candidate_date_order"}]
    doubtful=[c['label'] for c in integrity if c.get('candidate_signal')=='inconsistent']
    add("integrity","报告完整性与内部一致性",integrity,"needs_review",
        "存在识别疑点："+'、'.join(doubtful) if doubtful else "已检查可识别的跨页编号、页码与日期线索；附件、结束标识及替代关系仍需核对原页。",
        "逐页查看原件；识别疑点和签章图像不直接认定为原件缺失。")
    add("numeric","数值与计算一致性",[],"not_started" if not p["comparisons"] else "needs_review",
        "只执行明确配置的单位换算和判定规则；误差定义、百分比基准和修正前后结果须明确。",
        "查看逐点使用的输入与判定记录；复杂公式或定义不明时保留专项核对。")
    return result


def input_hash(context, fields_state, association, catalog_hash, institutions):
    return content_hash({"source":context["extraction_hash"],"fields":fields_state["fields_hash"],
        "corrections":fields_state["corrections"], "evidence":context["evidence_ids"],
        "candidate_findings":context["candidate_findings"],"association":association["payload_hash"] if association else None,
        "catalog":catalog_hash,"institutions":institutions,"checks":CHECKS_VERSION,"engine":ENGINE})


def freeze_views(workspace, lab_id, campaign_id, review, contexts, fields_states):
    from .report_workspace import latest_association, device_catalog
    from .point_comparison import build_point_matrix
    from .diagnostic_report import build_comparison
    catalog=device_catalog(workspace,lab_id,campaign_id); institutions=institution_records(workspace,lab_id)
    views={}
    for context in contexts:
        key=context["extraction_id"]; ext=get_extraction(workspace,lab_id,key)
        association=latest_association(workspace,lab_id,campaign_id,key)
        fields_state=fields_states[key]; diagnostics=diagnostic_projection(review,key)
        matrix=build_point_matrix(workspace,lab_id,campaign_id,ext,association,review,contexts=contexts)
        preconditions=[]
        if matrix["basis"]["mode"]=="unmatched": preconditions.append({"code":"DEVICE_UNMATCHED","text":"设备尚未匹配，未进行方案对照。","action":"澄清对应关系"})
        if not review["payload"]["baseline_id"]: preconditions.append({"code":"BASELINE_NOT_BOUND","text":"方案尚未确认，当前为基础审查与候选预对照。","action":"前往方案编制"})
        if not institutions: preconditions.append({"code":"INSTITUTION_EVIDENCE_MISSING","text":"机构范围证据待补，不作为证书原文错误标注。","action":"接入机构范围资料"})
        findings=review["payload"]["findings"]
        content=[f for f in findings if f["extraction_id"]==key and finding_category(f)=="content_issue"]
        view={"schema_version":"1.0","engine":ENGINE,"lab_id":lab_id,"campaign_id":campaign_id,"review_id":review["id"],
            "extraction_id":key,"fields":fields_state["fields"],"fields_hash":fields_state["fields_hash"],
            "report":{"file_name":workspace.resolve_source(lab_id,context["payload"]["source_id"])["name"],
                "certificate_number":" / ".join(c["value"] for c in fields_state["fields"].get("certificate_number",[])),"document_kind":context["document_kind"]},
            "input_hash":input_hash(context,fields_state,association,catalog["catalog_hash"],institutions),"association":association,
            "diagnostics":diagnostics,"point_matrix":matrix,
            "comparison_report":build_comparison(workspace,lab_id,campaign_id,ext,association,review,diagnostics,fields=fields_state["fields"],contexts=contexts),
            "preconditions":preconditions,"summary":{"mode":"baseline" if review["payload"]["baseline_id"] else "basic",
                "content_issues":len(content),"confirmed_issues":sum(f["certainty"]=="confirmed" for f in content),
                "point_decisions":dict(Counter(r["decision_state"] for r in matrix["rows"])),
                "point_statuses":dict(Counter(r["status"] for r in matrix["rows"]))},"technical_approval":False}
        validate_contract("report-diagnosis.v1",view)
        views[key]={"payload":view,"payload_hash":content_hash(view)}
    return views
