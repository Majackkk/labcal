import copy
import json
from pathlib import Path

from ..domain.contracts import validate_contract
from ..domain.readiness import assess, requirement_hash
from ..domain.rules import RULES_VERSION
from ..errors import LabcalError
from ..util import canonical_json, content_hash, identifier, utc_now
from .laboratories import get_equipment
from .sources import verify_source_integrity


def get_plan(workspace, lab_id, plan_id, revision=None):
    with workspace.lab_store(lab_id).connect() as db:
        if revision is None:
            row = db.execute("SELECT * FROM plan_revisions WHERE plan_id=? ORDER BY revision DESC LIMIT 1", (plan_id,)).fetchone()
        else:
            row = db.execute("SELECT * FROM plan_revisions WHERE plan_id=? AND revision=?", (plan_id, revision)).fetchone()
    if not row:
        raise LabcalError("PLAN_NOT_FOUND", "当前实验室中没有该方案版本。", {"plan_id": plan_id, "revision": revision})
    result = dict(row)
    result["payload"] = json.loads(result["payload"])
    if content_hash(result["payload"]) != result["payload_hash"]:
        raise LabcalError("PLAN_INTEGRITY_FAILED", "方案内容与记录的摘要不一致。")
    return result


def list_plans(workspace, lab_id):
    with workspace.lab_store(lab_id).connect() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM plans ORDER BY created_at,id")]
    return [get_plan(workspace, lab_id, key) for key in ids]


def create_plan(workspace, lab_id, payload, *, plan_id=None):
    payload = copy.deepcopy(payload)
    if payload.get("lab_id", lab_id) != lab_id:
        raise LabcalError("LAB_SCOPE_MISMATCH", "输入方案不属于选定实验室。")
    equipment = get_equipment(workspace, lab_id, payload.get("equipment_id"))
    if payload.get("equipment_config_hash", equipment["config_hash"]) != equipment["config_hash"]:
        raise LabcalError("CONFIGURATION_VERSION_CONFLICT", "输入引用的设备配置已经变化。")
    payload.update({"schema_version": "1.0", "lab_id": lab_id, "equipment_config_hash": equipment["config_hash"]})
    payload.setdefault("origin", "evidence_draft")
    payload.setdefault("source_ids", [])
    validate_contract("plan.v1", payload)
    ids = [r["id"] for r in payload["requirements"]]
    if len(set(ids)) != len(ids):
        raise LabcalError("DUPLICATE_REQUIREMENT_ID", "同一方案的参数要求标识不能重复。")
    for source_id in set(payload["source_ids"] + [e["source_id"] for r in payload["requirements"] for e in r["evidence"]]):
        verify_source_integrity(workspace, lab_id, source_id)
    digest = content_hash(payload)
    store = workspace.lab_store(lab_id)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if plan_id:
            existing = db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
            if not existing:
                raise LabcalError("PLAN_NOT_FOUND", "当前实验室中没有该方案。")
            if existing["equipment_id"] != payload["equipment_id"]:
                raise LabcalError("PLAN_EQUIPMENT_CHANGED", "不同设备必须使用独立方案。")
            duplicate = db.execute("SELECT revision,payload_hash FROM plan_revisions WHERE plan_id=? ORDER BY revision DESC LIMIT 1", (plan_id,)).fetchone()
            if duplicate and duplicate[1] == digest:
                return {"plan_id": plan_id, "revision": duplicate[0], "payload_hash": digest, "reused": True}
            revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM plan_revisions WHERE plan_id=?", (plan_id,)).fetchone()[0]
        else:
            # Repeated imports of exactly the same content are idempotent.
            duplicate = db.execute("SELECT plan_id,revision FROM plan_revisions WHERE payload_hash=? AND plan_id IN (SELECT id FROM plans WHERE equipment_id=?)", (digest, payload["equipment_id"])).fetchone()
            if duplicate:
                return {"plan_id": duplicate[0], "revision": duplicate[1], "payload_hash": digest, "reused": True}
            plan_id, revision = identifier("PLAN"), 1
            db.execute("INSERT INTO plans VALUES (?,?,?,?)", (plan_id, payload["equipment_id"], payload["title"], utc_now()))
        db.execute("INSERT INTO plan_revisions VALUES (?,?,?,?,?,?)", (plan_id, revision, canonical_json(payload), digest, "draft", utc_now()))
        store.event(db, "plan_revision_created", plan_id, {"revision": revision, "payload_hash": digest, "origin": payload["origin"]})
    return {"plan_id": plan_id, "revision": revision, "payload_hash": digest, "reused": False}


def _evidence_checker(workspace, lab_id, plan, reviews):
    def check(requirement, evidence):
        try:
            source = verify_source_integrity(workspace, lab_id, evidence["source_id"])
        except LabcalError as exc:
            return [exc.code]
        metadata = source["metadata"]
        if metadata.get("content_completeness") == "incomplete" or metadata.get("container_readable") is False:
            return ["SOURCE_INCOMPLETE_OR_UNREADABLE"]
        if evidence["claim_kind"] == "legacy":
            return ["LEGACY_REQUIREMENT_UNREVIEWED"]
        if evidence["claim_kind"] == "lab_requirement":
            return []
        expected_roles = {"normative": {"test_method_standard", "metrology_regulation", "cnas_document"}, "manufacturer": {"manufacturer_manual"}}
        if source["kind"] not in expected_roles.get(evidence["claim_kind"], set()):
            return ["SOURCE_ROLE_MISMATCH"]
        page = evidence.get("page")
        if source["metadata"].get("format") == "pdf" and (page is None or page > metadata.get("pages", 0)):
            return ["EVIDENCE_PAGE_INVALID"]
        fields = set(evidence.get("fields") or [key for key in ["parameter", "unit", "calibration_range", "points", "limit", "uncertainty"] if requirement.get(key) is not None])
        matches = [r for r in reviews if r["requirement_id"] == requirement["id"] and r["source_id"] == evidence["source_id"]
                   and r["requirement_hash"] == requirement_hash(requirement) and r["source_hash"] == source["sha256"]]
        for record in matches:
            value = json.loads(record["record"])
            if fields.issubset(set(value["checked_fields"])) and value.get("page") == page and value.get("anchor", "") == evidence.get("anchor", "") and value.get("version_checked") is True and value.get("applicability_checked") is True:
                return []
        return ["PARAMETER_EVIDENCE_REVIEW_PENDING"]
    return check


def assess_plan(workspace, lab_id, plan_id, revision=None):
    plan = get_plan(workspace, lab_id, plan_id, revision)
    equipment = get_equipment(workspace, lab_id, plan["payload"]["equipment_id"])
    store = workspace.lab_store(lab_id)
    with store.connect() as db:
        laboratory = json.loads(db.execute("SELECT value FROM meta WHERE key='laboratory'").fetchone()[0])
        reviews = [dict(x) for x in db.execute("SELECT * FROM evidence_reviews WHERE plan_id=? AND revision=?", (plan_id, plan["revision"]))]
        confirmation = db.execute("SELECT * FROM confirmations WHERE plan_id=? AND revision=?", (plan_id, plan["revision"])).fetchone()
    readiness = assess(plan["payload"], _evidence_checker(workspace, lab_id, plan, reviews))
    for issue in plan["payload"].get("open_items", []):
        if issue.get("status") == "info" or issue.get("blocking") is False:
            continue
        if issue.get("status") == "resolved" and issue.get("resolution") and issue.get("resolved_by"):
            continue
        targets = set(issue.get("requirement_ids", []))
        affects = issue.get("affects", ["coverage", "decision"])
        for result in readiness["requirements"]:
            if targets and result["requirement_id"] not in targets:
                continue
            code = "OPEN_ITEM:" + str(issue.get("code", "UNRESOLVED"))
            if "coverage" in affects:
                result["coverage_ready"] = result["decision_ready"] = False
                result["coverage_issues"].append(code)
            if "decision" in affects:
                result["decision_ready"] = False
                result["decision_issues"].append(code)
    mandatory_ids = {item["id"] for item in plan["payload"]["requirements"] if item.get("mandatory", True)}
    mandatory = [item for item in readiness["requirements"] if item["requirement_id"] in mandatory_ids]
    readiness["coverage_ready"] = bool(mandatory) and all(item["coverage_ready"] for item in mandatory)
    readiness["decision_ready"] = bool(mandatory) and all(item["decision_ready"] for item in mandatory)
    config_current = plan["payload"]["equipment_config_hash"] == equipment["config_hash"]
    if not config_current:
        readiness["coverage_ready"] = readiness["decision_ready"] = False
    profile_supported = laboratory["profile"] == "cnas_cl01"
    if not profile_supported:
        readiness["decision_ready"] = False
        for result in readiness["requirements"]:
            result["decision_ready"] = False
            result["decision_issues"].append("LABORATORY_PROFILE_NOT_IMPLEMENTED")
    readiness.update({"user_confirmed": bool(confirmation), "configuration_current": config_current,
                      "profile_supported": profile_supported, "test_only": laboratory["test_only"],
                      "configuration_state": equipment["configuration_state"],
                      "plan_id": plan_id, "revision": plan["revision"], "payload_hash": plan["payload_hash"],
                      "plan_state": plan["state"], "automatic_baseline_available": bool(confirmation) and plan["state"] == "confirmed" and config_current and readiness["coverage_ready"]})
    return readiness


def verify_evidence(workspace, lab_id, plan_id, revision, record):
    plan = get_plan(workspace, lab_id, plan_id, revision)
    required = ["requirement_id", "source_id", "reviewer", "source_quote", "checked_fields", "applicability_note", "expected_plan_hash", "source_hash", "reviewed_at"]
    if any(not record.get(key) for key in required):
        raise LabcalError("EVIDENCE_REVIEW_INCOMPLETE", "原页核验需明确核验人、原文、字段、适用性、版本及时间。")
    if record.get("version_checked") is not True or record.get("applicability_checked") is not True:
        raise LabcalError("EVIDENCE_REVIEW_INCOMPLETE", "原页核验还需要明确核对版本和适用性。")
    if record["expected_plan_hash"] != plan["payload_hash"]:
        raise LabcalError("PLAN_VERSION_CONFLICT", "原页核验对应的方案版本不一致。")
    requirement = next((x for x in plan["payload"]["requirements"] if x["id"] == record["requirement_id"]), None)
    if not requirement or not any(e["source_id"] == record["source_id"] for e in requirement["evidence"]):
        raise LabcalError("EVIDENCE_BINDING_MISMATCH", "核验记录没有绑定该参数的证据来源。")
    source = verify_source_integrity(workspace, lab_id, record["source_id"])
    if source["sha256"] != record["source_hash"]:
        raise LabcalError("SOURCE_VERSION_CONFLICT", "原页核验对应的原件摘要不一致。")
    if source["metadata"].get("format") == "pdf":
        page = record.get("page")
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= source["metadata"].get("pages", 0):
            raise LabcalError("EVIDENCE_PAGE_INVALID", "核验页码超出原件范围。")
    if not isinstance(record["checked_fields"], list) or not all(x in requirement for x in record["checked_fields"]):
        raise LabcalError("EVIDENCE_FIELDS_INVALID", "核验字段必须是该参数要求中实际存在的字段。")
    store = workspace.lab_store(lab_id)
    review_id = "REVIEW-" + content_hash(record)[:24]
    with store.connect() as db:
        cursor = db.execute("INSERT OR IGNORE INTO evidence_reviews VALUES (?,?,?,?,?,?,?,?,?)",
                   (review_id, plan_id, revision, record["requirement_id"], record["source_id"], requirement_hash(requirement), source["sha256"], canonical_json(record), utc_now()))
        if cursor.rowcount:
            store.event(db, "parameter_evidence_review_recorded", review_id, {"plan_id": plan_id, "revision": revision, "record_hash": content_hash(record)})
    return {"review_id": review_id, "recorded": True, "reused": not bool(cursor.rowcount)}


def confirmation_template(workspace, lab_id, plan_id, revision=None):
    plan = get_plan(workspace, lab_id, plan_id, revision)
    return {"lab_id": lab_id, "plan_id": plan_id, "revision": plan["revision"],
            "payload_hash": plan["payload_hash"], "equipment_config_hash": plan["payload"]["equipment_config_hash"],
            "decision": "confirm", "confirmed_by": "", "confirmed_at": "",
            "evidence_kind": "direct_user_message", "user_statement": "", "evidence_reference": "",
            "confirmed_fields": ["equipment_configuration", "plan_scope", "requirements"]}


def confirm_plan(workspace, lab_id, record):
    validate_contract("confirmation.v1", record)
    if record["lab_id"] != lab_id:
        raise LabcalError("LAB_SCOPE_MISMATCH", "确认记录不属于选定实验室。")
    plan = get_plan(workspace, lab_id, record["plan_id"], record["revision"])
    if record["payload_hash"] != plan["payload_hash"] or record["equipment_config_hash"] != plan["payload"]["equipment_config_hash"]:
        raise LabcalError("CONFIRMATION_VERSION_CONFLICT", "确认记录与方案或设备配置版本不一致。")
    if set(record["confirmed_fields"]) != {"equipment_configuration", "plan_scope", "requirements"}:
        raise LabcalError("CONFIRMATION_SCOPE_INCOMPLETE", "确认范围必须包括实际配置、方案用途和参数要求。")
    readiness = assess_plan(workspace, lab_id, plan["plan_id"], plan["revision"])
    if not readiness["coverage_ready"] or not readiness["configuration_current"]:
        raise LabcalError("PLAN_NOT_READY_FOR_CONFIRMATION", "尚有参数结构、证据或配置问题，不能建立可用确认版本。", readiness)
    store = workspace.lab_store(lab_id)
    confirmation_id = "CONF-" + content_hash(record)[:24]
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM confirmations WHERE plan_id=? AND revision=?", (record["plan_id"], record["revision"])).fetchone()
        if existing:
            if existing["record"] != canonical_json(record):
                raise LabcalError("CONFIRMATION_ALREADY_EXISTS", "此版本已有不同确认记录；修订请创建新版本。")
            return {"confirmation_id": existing["id"], "reused": True}
        equipment = db.execute("SELECT config_hash FROM equipment WHERE id=?", (plan["payload"]["equipment_id"],)).fetchone()
        if not equipment or equipment[0] != record["equipment_config_hash"]:
            raise LabcalError("CONFIGURATION_VERSION_CONFLICT", "确认期间设备配置发生变化。")
        db.execute("INSERT INTO confirmations VALUES (?,?,?,?,?,?,?)", (confirmation_id, record["plan_id"], record["revision"], record["payload_hash"], record["equipment_config_hash"], canonical_json(record), utc_now()))
        db.execute("UPDATE plan_revisions SET state='superseded' WHERE plan_id=? AND state='confirmed'", (record["plan_id"],))
        db.execute("UPDATE plan_revisions SET state='confirmed' WHERE plan_id=? AND revision=?", (record["plan_id"], record["revision"]))
        db.execute("UPDATE equipment SET configuration_state='confirmed' WHERE id=?", (plan["payload"]["equipment_id"],))
        store.event(db, "plan_user_confirmed", record["plan_id"], {"revision": record["revision"], "confirmation_id": confirmation_id, "record_hash": content_hash(record)})
    return {"confirmation_id": confirmation_id, "reused": False, "decision_ready": readiness["decision_ready"]}


def compile_baseline(workspace, lab_id, plan_id, revision=None):
    plan = get_plan(workspace, lab_id, plan_id, revision)
    readiness = assess_plan(workspace, lab_id, plan_id, plan["revision"])
    if not readiness["automatic_baseline_available"]:
        raise LabcalError("BASELINE_NOT_READY", "基准需要用户确认、当前设备配置和完整的参数覆盖要求。", readiness)
    store = workspace.lab_store(lab_id)
    source_ids = sorted(set(plan["payload"]["source_ids"] + [e["source_id"] for r in plan["payload"]["requirements"] for e in r["evidence"]]))
    snapshots = []
    for source_id in source_ids:
        source = verify_source_integrity(workspace, lab_id, source_id)
        snapshots.append({key: source[key] for key in ["id", "sha256", "code", "kind"]})
    with store.connect() as db:
        confirmation = db.execute("SELECT id FROM confirmations WHERE plan_id=? AND revision=?", (plan_id, plan["revision"])).fetchone()
    groups = {}
    for requirement in plan["payload"]["requirements"]:
        if requirement["delivery_route"] in {"external_calibration", "legal_verification"}:
            groups.setdefault(requirement.get("certificate_group") or "default", []).append(requirement["id"])
    payload = {"schema_version": "1.0", "lab_id": lab_id, "plan_id": plan_id, "revision": plan["revision"],
               "plan_hash": plan["payload_hash"], "configuration_hash": plan["payload"]["equipment_config_hash"],
               "rules_version": RULES_VERSION, "requirements": plan["payload"]["requirements"],
               "source_snapshots": snapshots, "readiness": {"coverage_ready": readiness["coverage_ready"], "decision_ready": readiness["decision_ready"], "profile_supported": readiness["profile_supported"], "test_only": readiness["test_only"], "requirements": readiness["requirements"]},
               "external_certificate_groups": groups, "confirmation_id": confirmation[0]}
    validate_contract("baseline.v1", payload)
    digest, baseline_id = content_hash(payload), "BASE-" + content_hash(payload)[:24]
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM baselines WHERE plan_id=? AND revision=?", (plan_id, plan["revision"])).fetchone()
        if existing:
            if existing["payload_hash"] != digest:
                raise LabcalError("BASELINE_VERSION_CONFLICT", "已有固定基准与当前状态不同；请建立新方案版本。")
            return {"baseline_id": existing["id"], "payload_hash": digest, "reused": True, "payload": payload}
        db.execute("INSERT INTO baselines VALUES (?,?,?,?,?,?)", (baseline_id, plan_id, plan["revision"], canonical_json(payload), digest, utc_now()))
        store.event(db, "baseline_compiled", baseline_id, {"plan_id": plan_id, "revision": plan["revision"], "payload_hash": digest})
    return {"baseline_id": baseline_id, "payload_hash": digest, "reused": False, "payload": payload}


def get_baseline(workspace, lab_id, baseline_id):
    with workspace.lab_store(lab_id).connect() as db:
        row = db.execute("SELECT * FROM baselines WHERE id=?", (baseline_id,)).fetchone()
    if not row:
        raise LabcalError("BASELINE_NOT_FOUND", "当前实验室中没有该验收基准。")
    result = dict(row)
    result["payload"] = json.loads(result["payload"])
    if content_hash(result["payload"]) != result["payload_hash"]:
        raise LabcalError("BASELINE_INTEGRITY_FAILED", "验收基准内容摘要不一致。")
    return result


def diff_plans(workspace, lab_id, plan_id, before, after):
    old = get_plan(workspace, lab_id, plan_id, before)["payload"]
    new = get_plan(workspace, lab_id, plan_id, after)["payload"]
    a, b = {x["id"]: x for x in old["requirements"]}, {x["id"]: x for x in new["requirements"]}
    return {"plan_id": plan_id, "before": before, "after": after,
            "configuration_changed": old["equipment_config_hash"] != new["equipment_config_hash"],
            "added": sorted(b.keys() - a.keys()), "removed": sorted(a.keys() - b.keys()),
            "changed": [key for key in sorted(a.keys() & b.keys()) if a[key] != b[key]],
            "needs_new_confirmation": content_hash(old) != content_hash(new)}

