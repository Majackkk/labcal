"""Review orchestration binds a fixed baseline and exact certificate evidence snapshots."""
from ..domain.certificate_review import CHECKS_VERSION, basic_checks, compare_requirement, compare_institution, document_anchor, finding, status
from ..errors import LabcalError
from ..domain.rules import RULES_VERSION
from ..util import canonical_json, content_hash, identifier, utc_now
from .certificates import certificate_store, certificate_context, institution_records
from .plans import get_baseline, assess_plan, get_plan
from .laboratories import get_equipment


def create_review(workspace, lab_id, extraction_ids, *, baseline_id=None, certificate_group=None, previous_review_id=None, campaign_id=None):
    if not extraction_ids or len(extraction_ids) != len(set(extraction_ids)):
        raise LabcalError("REVIEW_INPUT_INVALID", "审查需要至少一个不重复的证书提取版本。")
    contexts = [certificate_context(workspace, lab_id, key) for key in extraction_ids]
    from ..adapters.recognition import recognized_fields
    fields_states = {}
    for context in contexts:
        if campaign_id:
            from .campaigns import require_item
            from .report_corrections import report_fields
            require_item(workspace, lab_id, campaign_id, "extraction", context["extraction_id"], writable=True)
            fields_states[context["extraction_id"]] = report_fields(workspace, lab_id, campaign_id, get_extraction_for_context(workspace, lab_id, context))
        else:
            fields = recognized_fields(context["payload"])
            fields_states[context["extraction_id"]] = {"fields":fields,"fields_hash":content_hash(fields),"corrections":[]}
        context["candidate_fields"] = fields_states[context["extraction_id"]]["fields"]
    if len({x["certificate_id"] for x in contexts}) != len(contexts):
        raise LabcalError("CERTIFICATE_VERSION_DUPLICATE", "同一审查不能同时使用同一证书的多个提取版本。")
    store = certificate_store(workspace, lab_id)
    baseline = None
    if baseline_id:
        baseline = get_baseline(workspace, lab_id, baseline_id)
        b = baseline["payload"]
        if b["rules_version"] != RULES_VERSION:
            raise LabcalError("BASELINE_RULE_VERSION_MISMATCH", "固定基准的计量规则版本与当前程序不同，需要核对规则版本。")
        ready = assess_plan(workspace, lab_id, b["plan_id"], b["revision"])
        if not ready["automatic_baseline_available"] or not ready["configuration_current"]:
            raise LabcalError("BASELINE_NOT_CURRENT", "所选基准已被替代或设备配置已变化；历史基准仍可单独查看。")
        if b["plan_hash"] != ready["payload_hash"]:
            raise LabcalError("BASELINE_VERSION_MISMATCH", "基准和确认方案版本不一致。")
        plan = get_plan(workspace, lab_id, b["plan_id"], b["revision"])
        for context in contexts:
            if context.get("identity_checked") and context.get("equipment_id") != plan["payload"]["equipment_id"]:
                raise LabcalError("CERTIFICATE_EQUIPMENT_MISMATCH", "已核验的证书设备与方案设备不同。")
            if context.get("equipment_config_hash") != b["configuration_hash"]:
                context["identity_checked"] = False
        if certificate_group and certificate_group not in b["external_certificate_groups"]:
            raise LabcalError("CERTIFICATE_GROUP_NOT_FOUND", "所选基准没有该证书组。")
    if previous_review_id:
        from .certificates import get_review
        get_review(workspace, lab_id, previous_review_id)
    checks, findings = [], []
    for context in contexts:
        rows, issues = basic_checks(context)
        from ..domain.report_integrity import check_integrity
        consistency_rows, consistency_issues = check_integrity(context)
        rows.extend(consistency_rows); issues.extend(consistency_issues)
        checks.extend({"extraction_id": context["extraction_id"], **r} for r in rows)
        findings.extend(issues)
        for candidate in context["candidate_findings"]:
            item = finding(context, "SUPPLEMENT:" + candidate["code"], candidate["title"], candidate["message"],
                           basis=candidate["basis"] + ("（依据含义和适用性待复核）" if candidate.get("basis_sources") else "（依据待核实）"),
                           suggestion=candidate["suggestion"], loc=candidate["location"], severity=candidate.get("severity", "medium"))
            item.update(candidate_record_id=candidate["record_id"], origin=candidate["origin"], author=candidate["author"], basis_sources=candidate.get("basis_sources", []))
            findings.append(item)
    comparisons, institutions, extra = [], [], []
    institution_evidence = institution_records(workspace, lab_id)
    if not baseline:
        findings.append(finding(contexts[0], "BASELINE_NOT_BOUND", "方案基准待绑定", "本任务仅进行证书基础检查；参数覆盖和设备用途判定尚未开展。",
                                basis="参数验收以用户确认的具体方案为准", suggestion="取得具体方案确认后选择对应固定基准，再开展审查。", severity="low"))
    else:
        b = baseline["payload"]
        ids = set(b["external_certificate_groups"].get(certificate_group, [])) if certificate_group else {r for group in b["external_certificate_groups"].values() for r in group}
        expected = [r for r in b["requirements"] if r["id"] in ids]
        readiness = {r["requirement_id"]: r for r in b["readiness"]["requirements"]}
        matched = set()
        for requirement in expected:
            result, issues, pairs = compare_requirement(requirement, contexts, decision_ready=readiness[requirement["id"]]["decision_ready"])
            comparisons.append(result); findings.extend(issues)
            if not pairs:
                findings.append(finding(contexts[0], "PARAMETER_NOT_MATCHED", requirement["parameter"] + "未匹配",
                                        "尚未找到符合参数、组件、通道及工况的已核验结果。",
                                        basis="用户确认的方案参数 " + requirement["id"], suggestion="核对证书和结果录入；确认缺项后安排补充。",
                                        confirmed=result["coverage"] == "failed", requirement_id=requirement["id"]))
            for context in contexts:
                observations = [o for c, o in pairs if c["extraction_id"] == context["extraction_id"]]
                matched.update((context["extraction_id"], o["id"]) for o in observations)
                if observations:
                    institutions.append(compare_institution(context, requirement, observations, institution_evidence))
        for context in contexts:
            for observation in context["observations"]:
                if (context["extraction_id"], observation["id"]) not in matched:
                    extra.append({"extraction_id": context["extraction_id"], "observation_id": observation["id"], "parameter": observation["parameter"], "location": observation["location"]})
    for context in contexts:
        relevant = [x for x in institutions if x["extraction_id"] == context["extraction_id"]]
        if not relevant or any(x["status"] != "passed" or x.get("cmc_status") == "pending" for x in relevant):
            findings.append(finding(context, "INSTITUTION_SCOPE_PENDING", "出证机构对应能力待核实", "尚未完成机构身份、活动日期及具体项目范围证据的核对。",
                                    basis="机构资格与具体项目能力分别核对", suggestion="接入官方范围附件并登记实际核验；必要时保留历史范围证据。",
                                    loc=document_anchor(context, "issuer")))
    axes = {"document": status(r["status"] for r in checks),
            "coverage": status(r["coverage"] for r in comparisons if next((x.get("mandatory", True) for x in (baseline["payload"]["requirements"] if baseline else []) if x["id"] == r["requirement_id"]), True)),
            "fitness": status(r["decision"] for r in comparisons if next((x.get("mandatory", True) for x in (baseline["payload"]["requirements"] if baseline else []) if x["id"] == r["requirement_id"]), True))}
    if axes["document"] == "passed" and (not institutions or any(x["status"] != "passed" or x.get("cmc_status") == "pending" for x in institutions)):
        axes["document"] = "pending"
    if any(x["candidate_findings"] for x in contexts) and axes["document"] == "passed":
        axes["document"] = "pending"
    payload = {"schema_version": "1.0", "lab_id": lab_id, "baseline_id": baseline_id, "baseline_hash": baseline["payload_hash"] if baseline else None,
               "certificate_group": certificate_group, "previous_review_id": previous_review_id, "checks_version": CHECKS_VERSION,
               "certificate_snapshots": [{"extraction_id": c["extraction_id"], "extraction_hash": c["extraction_hash"],
                                           "certificate_id": c["certificate_id"], "source_id": c["payload"]["source_id"], "source_hash": c["payload"]["source_hash"],
                                           "document_kind": c["document_kind"], "evidence_ids": c["evidence_ids"]} for c in contexts],
               "institution_evidence_ids": [x["id"] for x in institution_evidence], "axes": axes,
               "checks": checks, "comparisons": comparisons, "institution_checks": institutions, "extra_observations": extra,
               "findings": list({f["id"]: f for f in findings}.values()), "technical_approval": False,
               "state": "awaiting_operator_review", "created_at": utc_now()}
    review_id = identifier("CRV")
    from .report_diagnosis import finding_category
    for issue in payload["findings"]: issue["category"] = finding_category(issue)
    payload["input_views"] = fields_states
    if campaign_id:
        from .report_diagnosis import freeze_views
        payload["report_views"] = freeze_views(workspace, lab_id, campaign_id, {"id":review_id,"payload":payload}, contexts, fields_states)
    with store.connect() as db:
        digest = content_hash(payload)
        db.execute("INSERT INTO certificate_reviews VALUES (?,?,?,?)", (review_id, canonical_json(payload), digest, utc_now()))
        store.event(db, "certificate_review_created", review_id, {"payload_hash": digest, "baseline_id": baseline_id, "extraction_ids": extraction_ids})
    return {"review_id": review_id, "payload_hash": digest, "axes": axes, "findings": len(payload["findings"]), "technical_approval": False}


def get_extraction_for_context(workspace, lab_id, context):
    # A shallow envelope only; the immutable payload is never changed.
    return {"id":context["extraction_id"],"payload":context["payload"],"payload_hash":context["extraction_hash"]}
