"""Read-only two-column comparison; candidate text never grants technical acceptance."""
from difflib import SequenceMatcher
import json
import re

from ..adapters.recognition import recognized_fields
from ..adapters.pdf import location
from ..domain.certificate_review import scope_matches
from ..domain.contracts import validate_contract
from ..domain.recognition import normalized, name_clue
from ..errors import LabcalError
from ..util import content_hash
from .campaigns import require_item
from .certificates import certificate_context
from .plans import get_baseline, get_plan
from .workbooks import get_workbook


def _text(value):
    if value is None or value == "" or value == [] or value == {}: return "-"
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


def _side(text, anchors=None):
    return {"text": _text(text), "anchors": anchors or []}


def _field_side(fields, key, extraction_id):
    candidates = fields.get(key, [])
    return _side("\n".join(dict.fromkeys(c["value"] for c in candidates)),
                 [{"extraction_id": extraction_id, "location": c["location"]} for c in candidates])


def _requirements(workspace, lab_id, campaign_id, association, review):
    device = association["payload"]["device"] if association else None
    identity = device["identity"] if device else {}
    baseline_id = review["payload"]["baseline_id"] if review else None
    if baseline_id:
        baseline = get_baseline(workspace, lab_id, baseline_id)
        p = baseline["payload"]
        # The baseline fixes the technical version even if the current association changed.
        if not device or (device.get("plan_id"), device.get("revision")) != (p["plan_id"], p["revision"]): identity = {}
        return p["requirements"], identity, {"mode": "confirmed_baseline", "id": baseline_id,
            "hash": baseline["payload_hash"], "label": f"确认方案 {p['plan_id']} / 版本 {p['revision']}"}
    if not device: return [], {}, {"mode": "unmatched", "id": "", "hash": "", "label": "尚未匹配具体方案设备"}
    if device["kind"] == "workbook_candidate":
        key = device["workbook_id"]; require_item(workspace, lab_id, campaign_id, "workbook", key)
        book = get_workbook(workspace, lab_id, key)
        if book["payload_hash"] != device["workbook_hash"]:
            raise LabcalError("DIAGNOSTIC_PLAN_CHANGED", "候选方案与设备关联的固定版本不一致。")
        rows = [r for r in book["candidate_view"] if r["id"] in device["rows"]]
        if len(rows) != len(device["rows"]): raise LabcalError("DIAGNOSTIC_ROWS_CHANGED", "方案参数行与设备关联不一致。")
        requirements = [{"id": r["id"], "parameter": r["parameter"], "fields": r["fields"], "raw": r,
                         "plan_anchor": r["anchor"]} for r in rows]
        return requirements, identity, {"mode": "candidate_preview", "id": key, "hash": book["payload_hash"],
                                      "label": device["name"] + " / 候选方案"}
    require_item(workspace, lab_id, campaign_id, "plan", device["plan_id"], device["revision"])
    plan = get_plan(workspace, lab_id, device["plan_id"], device["revision"])
    if plan["payload_hash"] != device["plan_hash"]: raise LabcalError("DIAGNOSTIC_PLAN_CHANGED", "方案版本摘要不一致。")
    return plan["payload"]["requirements"], identity, {"mode": "candidate_preview", "id": device["plan_id"],
        "hash": plan["payload_hash"], "label": f"{plan['payload']['title']} / 版本 {device['revision']}（未绑定有效基准）"}


def _parameter_clues(payload, parameter, extraction_id):
    """Similar original text is a navigation hint, never a covered parameter/result."""
    base = re.sub(r"[（(].*?[）)]", "", parameter)
    query = normalized(base)
    if len(query) < 2: return _side(None)
    result_pages = {p["page"] for p in payload["pages"] if any(re.match(
        r"^(?:\d+[.、])?(?:校准结果|检定结果|测量结果|calibrationresults|resultofcalibration)",
        normalized(l["text"])) for l in p["lines"])}
    candidates = []
    for p in payload["pages"]:
        if result_pages and p["page"] not in result_pages: continue
        for i, line in enumerate(p["lines"]):
            text = normalized(line["text"])
            if not text: continue
            score = 1.0 if query in text else SequenceMatcher(None, query, text).ratio()
            if score >= .58:
                candidates.append((score, p["page"], i, line))
    candidates.sort(key=lambda r: (-r[0], r[1], r[2]))
    chosen = candidates[:3]
    return _side("\n\n".join(f"相关原文（第 {p} 页）：{line['text']}" for _, p, _, line in chosen),
                 [{"extraction_id": extraction_id, "location": location(line)} for _, _, _, line in chosen])


def build_comparison(workspace, lab_id, campaign_id, ext, association, review, diagnostics, *, fields=None, contexts=None):
    requirements, identity, basis = _requirements(workspace, lab_id, campaign_id, association, review)
    ids = [s["extraction_id"] for s in review["payload"]["certificate_snapshots"]] if review else [ext["id"]]
    if contexts is None:
        contexts = []
        for key in ids:
            require_item(workspace, lab_id, campaign_id, "extraction", key)
            contexts.append(certificate_context(workspace, lab_id, key))
    if fields is None: fields = recognized_fields(ext["payload"])
    rows = []
    def add(key, category, title, left, right, reason, state="pending", signal="insufficient", plan_anchor=""):
        rows.append({"id": key, "category": category, "title": title, "plan": left, "certificate": right,
                     "status": state, "signal": signal, "reason": reason, "plan_anchor": plan_anchor})
    for key, field, title in [("equipment_name", "instrument_name", "设备名称"), ("manufacturer", "manufacturer", "厂家"),
                              ("model", "model", "设备型号"), ("equipment_number", "instrument_number", "设备编号")]:
        expected = identity.get(key); right = _field_side(fields, field, ext["id"])
        values = [normalized(c["value"]) for c in fields.get(field, [])]
        same = bool(expected and values and len(set(values)) == 1 and normalized(expected) == values[0])
        similar = key == "equipment_name" and bool(name_clue(expected)) and any(name_clue(expected) in name_clue(v) or name_clue(v) in name_clue(expected) for v in values if name_clue(v))
        signal = "matched" if same else "possible" if similar else "conflict" if expected and values else "insufficient"
        reason = {"matched": "识别文字一致。", "possible": "名称存在关联线索，具体设备身份待确定。",
                  "conflict": "方案与证书的识别值不同，需确定对应关系。", "insufficient": "一侧缺少明确身份信息，暂不能确定。"}[signal]
        add("identity:"+key, "identity", title, _side(expected), right, reason, signal=signal)
    comparisons = {c["requirement_id"]: c for c in review["payload"]["comparisons"]} if review else {}
    for req in requirements:
        raw = req.get("raw", {}); values = raw.get("values", {}); f = req.get("fields", raw.get("fields", {}))
        left = "\n".join(label+"："+_text(value) for label, value in [
            ("参数", req["parameter"]), ("范围／点位／条件", values.get("conditions") or req.get("calibration_range") or req.get("points")),
            ("允许误差 MPE", f.get("mpe") or req.get("limit")), ("不确定度要求", f.get("uncertainty") or req.get("uncertainty"))])
        observed = [(c, o) for c in contexts for o in c["observations"] if scope_matches(req, o)]
        if observed:
            texts, anchors = [], []
            for c, o in observed:
                texts.append("\n".join(["参数："+o["parameter"], "点位／范围："+_text(o.get("point") or o.get("range"))+" "+o["unit"],
                    "结果："+_text(o["inputs"])]))
                anchors.append({"extraction_id": c["extraction_id"], "location": o["location"]})
            right = _side("\n\n".join(texts), anchors)
        else: right = _parameter_clues(ext["payload"], req["parameter"], ext["id"])
        comparison = comparisons.get(req["id"]); state = "pending"
        if comparison and basis["mode"] == "confirmed_baseline":
            state = "failed" if "failed" in (comparison["coverage"], comparison["decision"]) else "passed" if comparison["coverage"] == comparison["decision"] == "passed" else "pending"
        reason = "符合确认方案的覆盖与结果要求。" if state == "passed" else "参数覆盖或测量结果不符合确认方案要求。" if state == "failed" else (
            "候选方案预对照；尚未绑定确认基准，不能确定技术符合性。" if basis["mode"] != "confirmed_baseline" else "已有确认基准，报告项目、点位或判定输入尚不充分。")
        if not observed: reason += " 已列出相关原文线索，尚未建立测量结果对应关系。" if right["anchors"] else " 暂未建立证书结果对应关系，不能据此认定报告缺项。"
        if req.get("delivery_route") in {"internal_calibration", "functional_check", "pre_use_check"}:
            state, reason = "not_applicable", "方案规定采用其他实施方式，不要求本份外部证书覆盖。"
        add("parameter:"+req["id"], "parameters", req["parameter"], _side(left), right, reason, state,
            "matched" if observed else "possible" if right["anchors"] else "insufficient", req.get("plan_anchor", ""))
    claims = lambda role: "\n\n".join(dict.fromkeys(r.get("raw", {}).get("values", {}).get(role, "") for r in requirements if r.get("raw", {}).get("values", {}).get(role)))
    combined = lambda keys: _side("\n\n".join(_field_side(fields, k, ext["id"])["text"] for k in keys if fields.get(k)),
        [a for k in keys for a in _field_side(fields, k, ext["id"])["anchors"]])
    for key, title, left, right in [
        ("method", "校准／检定依据与适用性", claims("metrology_claim"), combined(["method"])),
        ("traceability", "出证机构与计量溯源", None, combined(["issuer", "traceability"])),
        ("uncertainty", "不确定度适用性", "\n".join(dict.fromkeys(_text(r.get("fields", r.get("raw", {}).get("fields", {})).get("uncertainty") or r.get("uncertainty")) for r in requirements)), combined(["uncertainty"])),
        ("information", "证书信息完整性与一致性", None, combined(["certificate_number", "activity_date", "issue_date", "approval"]))]:
        diagnostic = next((d for d in diagnostics if d["key"] == key), None)
        reason = diagnostic["reason"] if diagnostic else "现有信息不足，暂不能确定。"
        add("assurance:"+key, "assurance", title, _side(left), right, reason, diagnostic["status"] if diagnostic else "pending")
    result = {"schema_version": "1.0", "lab_id": lab_id, "campaign_id": campaign_id,
        "extraction_id": ext["id"], "extraction_hash": ext["payload_hash"], "basis": basis,
        "review_id": review["id"] if review else None, "rows": rows, "technical_approval": False}
    validate_contract("report-comparison.v1", result)
    return {**result, "projection_hash": content_hash(result)}
