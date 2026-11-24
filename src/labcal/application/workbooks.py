"""Spreadsheet originals and candidate rows; no automatic asset confirmation."""
import json
from pathlib import Path

from ..adapters.spreadsheet import read_workbook, extract_candidates, PARSER_VERSION
from ..adapters.workbook_archive import import_directory, preserve_import
from ..domain.contracts import validate_contract
from ..domain.recognition import VERSION, classify_devices
from ..domain.point_matrix import plan_points
from ..adapters.report_archive import fixed_json
from ..errors import LabcalError
from ..util import canonical_json, content_hash, utc_now
from .campaigns import campaign_store, get_campaign, attach_item, require_item, campaign_directory
from .sources import import_source, verify_source_integrity


def get_workbook(workspace, lab_id, workbook_id, campaign_id=None):
    with campaign_store(workspace, lab_id).connect() as db:
        row = db.execute("SELECT * FROM workbook_imports WHERE id=?", (workbook_id,)).fetchone()
    if not row: raise LabcalError("WORKBOOK_NOT_FOUND", "当前实验室没有该工作簿提取版本。")
    item = dict(row); item["payload"] = json.loads(item["payload"])
    if content_hash(item["payload"]) != item["payload_hash"]:
        raise LabcalError("WORKBOOK_INTEGRITY_FAILED", "工作簿提取摘要不一致。")
    # Older snapshots stay byte-for-byte fixed; only their read-only view is projected.
    _, item["candidate_view"] = extract_candidates(item["payload"]["sheets"], item["payload"]["mapping"])
    item["point_view"] = plan_points([{ "id": r["id"], "parameter": r["parameter"], "fields": r["fields"], "raw": r, "plan_anchor": r["anchor"]} for r in item["candidate_view"]])
    item["devices"] = classify_devices(workbook_id, item["payload_hash"], item["candidate_view"])
    if campaign_id:
        require_item(workspace, lab_id, campaign_id, "workbook", workbook_id)
        source = verify_source_integrity(workspace, lab_id, item["payload"]["source_id"])
        directory = import_directory(campaign_directory(workspace, lab_id, get_campaign(workspace, lab_id, campaign_id)), source["name"], workbook_id)
        item["import_directory"] = str(directory) if directory.is_dir() else None
    return item


def import_workbook(workspace, lab_id, campaign_id, file=None, *, source_id=None, mapping=None):
    get_campaign(workspace, lab_id, campaign_id, writable=True)
    if file:
        if Path(file).suffix.lower() != ".xlsx": raise LabcalError("WORKBOOK_FORMAT_UNSUPPORTED", "方案接入支持XLSX；原件不会被修改。")
        # Parse first: a corrupt file does not create a candidate import.
        read_workbook(file)
        source_id = import_source(workspace, file, kind="user_plan", lab_id=lab_id)["id"]
    source = verify_source_integrity(workspace, lab_id, source_id)
    if source["scope"] != lab_id: raise LabcalError("LAB_SCOPE_MISMATCH", "方案原件需要当前实验室归属。")
    sheets = read_workbook(source["absolute_path"])
    mapping, candidates = extract_candidates(sheets, mapping)
    payload = {"schema_version": "2.0", "lab_id": lab_id, "source_id": source_id, "source_hash": source["sha256"],
               "parser": PARSER_VERSION, "sheets": sheets, "mapping": mapping, "candidates": candidates, "technical_review": "pending"}
    validate_contract("workbook-extraction.v2", payload)
    digest = content_hash(payload); key = "WB-" + digest[:24]
    store = campaign_store(workspace, lab_id)
    with store.connect() as db:
        cursor = db.execute("INSERT OR IGNORE INTO workbook_imports VALUES (?,?,?,?)", (key, canonical_json(payload), digest, utc_now()))
        if cursor.rowcount: store.event(db, "workbook_candidates_imported", key, {"payload_hash": digest, "source_id": source_id, "rows": len(candidates)})
    attach_item(workspace, lab_id, campaign_id, "source", source_id)
    attach_item(workspace, lab_id, campaign_id, "workbook", key)
    directory = import_directory(campaign_directory(workspace, lab_id, get_campaign(workspace, lab_id, campaign_id)), source["name"], key)
    preserve_import(directory, source, {"id": key, "payload": payload, "payload_hash": digest}, lab_id=lab_id, campaign_id=campaign_id)
    classification = {"recognizer": VERSION, "workbook_id": key, "workbook_hash": digest,
                      "devices": classify_devices(key, digest, candidates), "technical_approval": False}
    fixed_json(directory / ("设备分类__" + content_hash(classification)[:24] + ".json"), classification)
    return {"workbook_id": key, "payload_hash": digest, "source_id": source_id, "candidates": len(candidates),
            "groups": list(dict.fromkeys(c["group"] for c in candidates)), "devices": len(classification["devices"]),
            "reused": not bool(cursor.rowcount), "state": "candidate", "import_directory": str(directory)}


def draft_from_rows(workspace, lab_id, campaign_id, workbook_id, row_ids, equipment_id, *, title=None):
    from .plans import create_plan
    require_item(workspace, lab_id, campaign_id, "workbook", workbook_id, writable=True)
    item = get_workbook(workspace, lab_id, workbook_id); payload = item["payload"]
    if not isinstance(row_ids, list) or not row_ids or len(row_ids) != len(set(row_ids)):
        raise LabcalError("WORKBOOK_ROWS_REQUIRED", "请选择不重复的候选参数行。")
    rows = [r for r in item["candidate_view"] if r["id"] in row_ids]
    if len(rows) != len(row_ids): raise LabcalError("WORKBOOK_ROW_NOT_FOUND", "候选行不属于当前工作簿版本。")
    requirements = [{"id": f"R{i:03d}", "parameter": row["parameter"], "unit": "", "delivery_route": "unresolved",
        "coverage_policy": "unresolved", "points": [], "report_fields": [],
        "evidence": [{"source_id": payload["source_id"], "claim_kind": "legacy", "anchor": row["anchor"]}],
        "raw": {"workbook_id": workbook_id, "workbook_hash": item["payload_hash"], **row}} for i, row in enumerate(rows, 1)]
    result = create_plan(workspace, lab_id, {"equipment_id": equipment_id, "title": title or rows[0]["group"],
        "origin": "user_plan", "source_ids": [payload["source_id"]], "requirements": requirements,
        "notes": "由用户提供的工作簿整理的候选稿。原文中的规范摘录、限值和设备配置尚待核对。"})
    attach_item(workspace, lab_id, campaign_id, "plan", result["plan_id"], result["revision"])
    return result
