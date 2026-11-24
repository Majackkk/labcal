"""Point detail is a derived view, with fixed inputs and no writable limit copy."""
from ..domain.point_matrix import VERSION, plan_points, certificate_points, align_points, family, point_key
from ..domain.contracts import validate_contract
from ..util import content_hash
from .diagnostic_report import _requirements
from .certificates import get_extraction
from .campaigns import require_item


def _verified_points(contexts):
    rows = []
    for context in contexts or []:
        for observation in context["observations"]:
            if "point" not in observation: continue
            def datum(*names):
                value = next((observation["inputs"][n] for n in names if n in observation["inputs"]), None)
                if not isinstance(value, dict): return ""
                return str(value.get("value", "")) + (" " + value["unit"] if value.get("unit") else "")
            condition = " / ".join(str(observation[k]) for k in ("component", "channel", "mode") if observation.get(k))
            row = {k: "" for k in ("id parameter family point unit condition measured error repeatability mpe repeatability_mpe uncertainty uncertainty_scope anchor_precision state").split()}
            row.update(id=context["extraction_id"]+":"+observation["id"], parameter=observation["parameter"],
                family=family(observation["parameter"]), point=observation["point"], unit=observation["unit"],
                condition=condition, measured=datum("measured", "indication"), error=datum("error", "indication_error"),
                repeatability=datum("repeatability"), uncertainty=datum("uncertainty"), state="source_verified",
                anchor_precision="evidence", anchor={"extraction_id":context["extraction_id"], "location":observation["location"]},
                evidence_observation_id=observation["id"])
            rows.append(row)
    return rows


def _same_region(a, b):
    if a["anchor"]["extraction_id"] != b["anchor"]["extraction_id"]: return False
    x, y = a["anchor"]["location"], b["anchor"]["location"]
    if x["page"] != y["page"]: return False
    u,v=x["bbox"],y["bbox"]
    return min(u[2],v[2]) > max(u[0],v[0]) and min(u[3],v[3]) > max(u[1],v[1])


def build_point_matrix(workspace, lab_id, campaign_id, ext, association, review, *, contexts=None):
    requirements, _, basis = _requirements(workspace, lab_id, campaign_id, association, review)
    ids = [s['extraction_id'] for s in review['payload']['certificate_snapshots']] if review else [ext['id']]
    observations, warnings, snapshots = [], [], []
    for key in ids:
        require_item(workspace, lab_id, campaign_id, 'extraction', key)
        item = ext if key == ext['id'] else get_extraction(workspace, lab_id, key)
        rows, notes = certificate_points(item)
        observations.extend(rows); warnings.extend(notes)
        snapshots.append({'id':key, 'hash':item['payload_hash']})
    verified = _verified_points(contexts)
    observations = [o for o in observations if not any(v["family"]==o["family"] and
        point_key(v["point"],v["unit"])==point_key(o["point"],o["unit"]) and _same_region(v,o) for v in verified)] + verified
    rows = align_points(plan_points(requirements), observations)
    comparisons = {c["requirement_id"]:c for c in review["payload"]["comparisons"]} if review else {}
    for row in rows:
        row.update(decision_state="needs_review", decision_reason="识别结果尚未形成可判定的原页证据。", decision_evidence=[])
        if basis["mode"] == "unmatched":
            row.update(signal="uncompared", decision_state="not_started", decision_reason="尚未匹配方案设备，未进行覆盖或结果判定。")
        elif row["signal"] == "other_route":
            row.update(status="not_applicable",decision_state="not_applicable",decision_reason="此项目采用其他实施方式。")
        elif basis["mode"] != "confirmed_baseline":
            row.update(decision_state="not_started", decision_reason="候选方案仅作预对照；需明确确认具体版本后开展技术判定。")
        else:
            comparison = comparisons.get((row.get("plan") or {}).get("requirement_id"), {})
            observed = row.get("certificate") or {}
            decisions = [d for d in comparison.get("decisions",[]) if d.get("observation_id")==observed.get("evidence_observation_id")
                and d.get("extraction_id",observed.get("anchor",{}).get("extraction_id"))==observed.get("anchor",{}).get("extraction_id")]
            if len(decisions)==1 and row["signal"] in {"paired","condition_pending"}:
                d=decisions[0]; row["decision_evidence"]=[d]
                row.update(status=d["status"],decision_state="concluded" if d["status"]!="pending" else "missing_input",
                    decision_reason="依据确认方案与已核对结果判定。" if d["status"]!="pending" else "判定输入不足："+d.get("code","未明确"))
            elif row["signal"]=="plan_only" and comparison.get("coverage")=="failed" and row["plan"]["point"] in comparison.get("missing_points",[]):
                row.update(status="failed",decision_state="concluded",decision_reason="已核对完整结果，确认方案要求的该点未被覆盖。")
            elif row["signal"]=="unresolved":
                row.update(decision_state="unsupported",decision_reason="范围或复杂工况尚不能逐点判定，需按原文专项核对。")
    result = {'schema_version':'2.0', 'parser':VERSION, 'lab_id':lab_id, 'campaign_id':campaign_id,
              'basis':basis, 'snapshots':snapshots, 'rows':rows,
              'observations':observations, 'warnings':list(dict.fromkeys(warnings)), 'technical_approval':False}
    validate_contract('point-matrix.v2', result)
    return {**result, 'projection_hash':content_hash(result)}
