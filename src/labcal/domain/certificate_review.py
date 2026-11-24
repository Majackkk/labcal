"""Explainable certificate checks. Candidate extraction never supplies numeric decisions."""
from datetime import date

from ..errors import LabcalError
from ..util import content_hash, normalize_text as normalize
from .rules import convert, evaluate_rule, number

CHECKS_VERSION = "certificate-checks-2.0"
LABELS = {"certificate_number": "证书唯一编号", "issuer": "出证机构", "client": "委托单位",
          "instrument_name": "器具名称", "model": "型号规格", "instrument_number": "器具唯一编号",
          "activity_date": "校准／检定日期", "issue_date": "签发日期", "method": "实施依据",
          "environment": "环境条件", "traceability": "计量溯源信息", "results": "测量结果",
          "uncertainty": "测量不确定度", "approval": "批准签署", "verification_conclusion": "检定结论"}
COMMON = ["certificate_number", "issuer", "client", "instrument_name", "instrument_number", "activity_date", "method", "results", "approval"]


def document_anchor(context, field=None):
    candidates = context.get("candidate_fields", context["payload"]["fields"]).get(field, []) if field else []
    if candidates: return candidates[0]["location"]
    return {"page": 1, "bbox": [.90, .02, .96, .05], "kind": "document", "quote": "文件级待核对事项"}


def finding(context, code, title, message, *, basis, suggestion, loc=None, confirmed=False, severity="medium", requirement_id=None):
    key = {"code": code, "extraction_id": context["extraction_id"], "requirement_id": requirement_id, "message": message}
    return {"id": "F-" + content_hash(key)[:10], **key, "title": title,
            "basis": basis, "suggestion": suggestion, "location": loc or document_anchor(context),
            "certainty": "confirmed" if confirmed else "pending", "severity": severity}


def basic_checks(context):
    checks, findings = [], []
    kind = context["document_kind"]
    if kind == "unknown":
        findings.append(finding(context, "DOCUMENT_KIND_PENDING", "文件类型待确认", "尚不能确定应使用校准还是检定检查规则。",
                                basis="文件类型决定适用检查项目", suggestion="回看标题、结论和实施依据，登记文件类型及原页位置。"))
    fields = COMMON + (["uncertainty", "traceability"] if kind == "calibration" else ["verification_conclusion"] if kind == "verification" else [])
    for field in fields:
        value = context["fields"].get(field)
        present = bool(context.get("candidate_fields", context["payload"]["fields"]).get(field))
        if field in context["absent_fields"]:
            state = "failed"
            findings.append(finding(context, "FIELD_ABSENT:" + field, LABELS[field] + "缺失", "原页核验记录确认未提供" + LABELS[field] + "。",
                                    basis="证书基础信息检查；按已核实文件类型适用", suggestion="补充相应内容或取得更正证书并重新审查。",
                                    loc=document_anchor(context, field), confirmed=True))
        elif value:
            state = "passed"
        else:
            state = "pending"
            if not present or field == "approval":
                message = "文字提取未找到可核实的" + LABELS[field] + "，需回看原页。"
                if field == "approval": message = "签名、签章可能以图片形式存在，需要核实原页的批准签署。"
                findings.append(finding(context, "FIELD_CHECK:" + field, LABELS[field] + "待核实", message,
                                        basis="基础资料检查，提取缺失不等于原件缺失", suggestion="回看原页；填写实际核验值、位置及核验人。",
                                        loc=document_anchor(context, field), severity="low" if field == "approval" else "medium"))
        checks.append({"field": field, "label": LABELS[field], "status": state, "candidate_present": present,
                       "location": value["location"] if value else document_anchor(context, field)})
    for warning in context["payload"]["warnings"]:
        if warning["code"] in {"OCR_UNAVAILABLE", "OCR_NOT_RUN", "OCR_TIMEOUT", "OCR_FAILED", "DOCUMENT_TEXT_UNAVAILABLE"}:
            loc = {"page": warning.get("page", 1), "bbox": [.90, .02, .96, .05], "kind": "document"}
            findings.append(finding(context, warning["code"], "页面识别待完成", "该页没有足够的可靠文字，需要识别或回看原页。",
                                    basis="审查输入完整性", suggestion="补充可读取PDF或完成原页核验。", loc=loc))
    if "activity_date" in context["fields"] and "issue_date" in context["fields"]:
        activity, issue = (date.fromisoformat(context["fields"][x]["value"]) for x in ["activity_date", "issue_date"])
        if issue < activity:
            checks.append({"field": "date_order", "label": "日期先后关系", "status": "failed", "location": context["fields"]["issue_date"]["location"]})
            findings.append(finding(context, "DATE_ORDER", "签发日期早于实施日期", "已核验的签发日期早于校准／检定日期。",
                                    basis="证书内部日期一致性", suggestion="核对日期或取得出证机构更正。",
                                    loc=context["fields"]["issue_date"]["location"], confirmed=True))
    if not context["kind_verified"]:
        checks.append({"field": "document_kind", "label": "文件类型核验", "status": "pending"})
    return checks, findings


def status(states):
    values = list(states)
    if "failed" in values: return "failed"
    if not values or "pending" in values: return "pending"
    return "passed"


def scope_matches(requirement, observation):
    if observation.get("requirement_id"):
        if observation["requirement_id"] != requirement["id"]: return False
    elif normalize(observation["parameter"]) != normalize(requirement["parameter"]):
        return False
    for field in ["component", "channel", "mode"]:
        if requirement.get(field) and normalize(requirement[field]) != normalize(observation.get(field, "")):
            return False
    return all(observation.get("conditions", {}).get(key) == value for key, value in requirement.get("conditions", {}).items())


def compare_requirement(requirement, contexts, *, decision_ready):
    expected_kind = "verification" if requirement["delivery_route"] == "legal_verification" else "calibration"
    eligible = [x for x in contexts if x["document_kind"] == expected_kind and x["kind_verified"] and x["identity_checked"]]
    pairs = [(c, o) for c in eligible for o in c["observations"] if scope_matches(requirement, o)]
    all_complete = bool(eligible) and all(x["observations_complete"] for x in eligible)
    result = {"requirement_id": requirement["id"], "parameter": requirement["parameter"], "coverage": "pending",
              "decision": "pending", "expected_points": requirement["points"], "matched_observations": [],
              "missing_points": [], "missing_report_fields": [], "decisions": [], "issues": []}
    if not pairs:
        result["issues"].append("PARAMETER_NOT_MATCHED" if eligible else "CERTIFICATE_IDENTITY_OR_TYPE_PENDING")
        if all_complete: result["coverage"] = "failed"
        return result, [], pairs
    first_context, first_observation = pairs[0]
    findings, used = [], set()
    try:
        if requirement["coverage_policy"] in {"explicit_points", "representative_points"}:
            points = {str(convert(o["point"], o["unit"], requirement["unit"]).normalize()) for _, o in pairs if "point" in o}
            result["missing_points"] = [p for p in requirement["points"] if str(number(p).normalize()) not in points]
            for c, o in pairs:
                if "point" in o and any(convert(o["point"], o["unit"], requirement["unit"]) == number(p) for p in requirement["points"]):
                    used.add((c["extraction_id"], o["id"]))
        elif requirement["coverage_policy"] == "range":
            expected = requirement["calibration_range"]
            ranges = sorted((convert(o["range"]["minimum"], o["range"].get("unit", o["unit"]), expected.get("unit", requirement["unit"])),
                             convert(o["range"]["maximum"], o["range"].get("unit", o["unit"]), expected.get("unit", requirement["unit"]))) for _, o in pairs if o.get("range"))
            merged = []
            for low, high in ranges:
                if merged and low <= merged[-1][1]: merged[-1] = (merged[-1][0], max(merged[-1][1], high))
                else: merged.append((low, high))
            if not any(low <= number(expected["minimum"]) and high >= number(expected["maximum"]) for low, high in merged):
                result["issues"].append("RANGE_NOT_COVERED")
            used = {(c["extraction_id"], o["id"]) for c, o in pairs if o.get("range")}
        else:
            result["issues"].append("COVERAGE_POLICY_UNSUPPORTED")
        selected = [(c, o) for c, o in pairs if (c["extraction_id"], o["id"]) in used]
        for c, o in selected:
            missing = set(requirement["report_fields"]) - set(o.get("reported_fields", []))
            result["missing_report_fields"].extend(sorted(missing))
            result["matched_observations"].append({"extraction_id": c["extraction_id"], "observation_id": o["id"], "location": o["location"]})
        blocked = bool(result["missing_points"] or result["missing_report_fields"] or result["issues"] or not selected)
        result["coverage"] = ("failed" if all_complete else "pending") if blocked else "passed"
        if blocked:
            findings.append(finding(first_context, "COVERAGE_GAP", requirement["parameter"] + "覆盖待补齐",
                                    f"缺少点位：{result['missing_points']}；缺少报告字段：{sorted(set(result['missing_report_fields']))}；其他：{result['issues']}。",
                                    basis="用户确认的方案参数 " + requirement["id"], suggestion="核对结果录入是否完整；如确为缺项，补充相应校准／检定。",
                                    loc=first_observation["location"], confirmed=all_complete, requirement_id=requirement["id"]))
        for c, o in selected:
            if not decision_ready: continue
            try:
                comparison = evaluate_rule(requirement, o["inputs"])
                uncertainty = requirement.get("uncertainty")
                if uncertainty:
                    datum = o["inputs"].get("uncertainty")
                    if not isinstance(datum, dict) or not datum.get("unit"):
                        raise LabcalError("UNCERTAINTY_INPUT_MISSING", "缺少方案要求的不确定度。")
                    if uncertainty["unit"] == "%" and datum.get("relative_basis") != uncertainty.get("relative_basis"):
                        raise LabcalError("RELATIVE_BASIS_MISMATCH", "不确定度的百分比基准不同。")
                    u = convert(datum["value"], datum["unit"], uncertainty["unit"])
                    if u < 0: raise LabcalError("UNCERTAINTY_NEGATIVE", "不确定度不能为负。")
                    comparison["uncertainty_conforms"] = u <= number(uncertainty["maximum"])
                    comparison["conforms"] = comparison["conforms"] and comparison["uncertainty_conforms"]
                result["decisions"].append({"observation_id": o["id"], "extraction_id": c["extraction_id"], "status": "passed" if comparison["conforms"] else "failed", **comparison})
                if not comparison["conforms"]:
                    findings.append(finding(c, "RESULT_OUTSIDE_REQUIREMENT", requirement["parameter"] + "结果未满足方案",
                                            "按固定方案规则比较的结果未满足要求。比较记录：" + str(comparison),
                                            basis="用户确认的方案参数 " + requirement["id"], suggestion="由实验室复核该结果、用途及后续处置。",
                                            loc=o["location"], confirmed=True, severity="high", requirement_id=requirement["id"]))
            except LabcalError as exc:
                result["decisions"].append({"observation_id": o["id"], "status": "pending", "code": exc.code})
        result["decision"] = status(x["status"] for x in result["decisions"])
        if result["coverage"] != "passed" and result["decision"] != "failed": result["decision"] = "pending"
    except LabcalError as exc:
        result["issues"].append(exc.code)
    return result, findings, pairs


def compare_institution(context, requirement, observations, records):
    issuer, activity = context["fields"].get("issuer"), context["fields"].get("activity_date")
    outcome = {"extraction_id": context["extraction_id"], "requirement_id": requirement["id"], "status": "pending",
               "cmc_status": "pending" if context["document_kind"] == "calibration" else "not_applicable",
               "evidence_ids": [], "issues": []}
    if not issuer or not activity:
        outcome["issues"].append("INSTITUTION_IDENTITY_OR_DATE_PENDING"); return outcome
    scheme = "legal_verification" if context["document_kind"] == "verification" else "cnas"
    # Alternative traceability routes need their own evaluation; absence of CNAS is not rejection.
    candidates = [r for r in records if normalize(r["institution"]) == normalize(issuer["value"])
                  and r["scheme"] == scheme and r["valid_from"] <= activity["value"] <= r["valid_until"]]
    for record in candidates:
        for row in record["parameters"]:
            if normalize(row["parameter"]) != normalize(requirement["parameter"]): continue
            if any(requirement.get(k) and normalize(requirement[k]) != normalize(row.get(k, "")) for k in ["component", "mode"]): continue
            if any(row.get("conditions", {}).get(k) != v for k, v in requirement.get("conditions", {}).items()): continue
            if "minimum" not in row or "maximum" not in row: continue
            try:
                points = [convert(o["point"], o["unit"], row["unit"]) for o in observations if "point" in o]
                intervals = [(convert(o["range"]["minimum"], o["range"].get("unit", o["unit"]), row["unit"]),
                              convert(o["range"]["maximum"], o["range"].get("unit", o["unit"]), row["unit"])) for o in observations if o.get("range")]
                values = points + [v for interval in intervals for v in interval]
                if not values: continue
                if all(number(row["minimum"]) <= v <= number(row["maximum"]) for v in values):
                    # CMC can depend on range and conditions; never reduce arbitrary scope formulae to a guessed number.
                    if scheme == "cnas" and row.get("cmc"):
                        outcome["issues"].append("CMC_REVIEW_REQUIRED"); continue
                    outcome.update(status="passed", evidence_ids=[record["id"]]); return outcome
            except LabcalError:
                outcome["issues"].append("INSTITUTION_UNIT_PENDING")
    outcome["issues"].append("PROJECT_SCOPE_NOT_VERIFIED")
    return outcome
