"""Append-only report candidates. This module never writes plan or evidence tables."""
import copy
from datetime import date
import json

from ..adapters.recognition import recognized_fields
from ..domain.contracts import validate_contract
from ..errors import LabcalError
from ..util import canonical_json, content_hash, identifier, utc_now
from .campaigns import campaign_store, require_item
from .certificates import get_extraction, validate_location

# Identity and report metadata only. Calibration requirements are deliberately absent.
EDITABLE_FIELDS = {"certificate_number", "issuer", "manufacturer", "client", "instrument_name", "model",
                   "instrument_number", "activity_date", "issue_date", "method", "environment",
                   "traceability", "uncertainty", "approval", "verification_conclusion", "verification_valid_until"}


def report_fields(workspace, lab_id, campaign_id, ext, *, db=None):
    fields = copy.deepcopy(recognized_fields(ext["payload"]))
    store = campaign_store(workspace, lab_id); store.enable_report_corrections()
    def read(connection):
        return list(connection.execute("SELECT * FROM report_corrections WHERE campaign_id=? AND extraction_id=? ORDER BY created_at,id", (campaign_id, ext["id"])))
    if db is None:
        with store.connect() as connection: rows = read(connection)
    else: rows = read(db)
    records = []
    for row in rows:
        record = json.loads(row["record"])
        if content_hash(record) != row["record_hash"] or record["source_hash"] != ext["payload"]["source_hash"]:
            raise LabcalError("REPORT_CORRECTION_INTEGRITY", "报告更正记录或原件摘要不一致。")
        if record["value"]:
            fields[record["field"]] = [{"value": record["value"], "location": record["location"],
                "state": "candidate", "origin": "user_correction", "correction_id": row["id"]}]
        else: fields.pop(record["field"], None)
        records.append({"id": row["id"], "hash": row["record_hash"]})
    return {"fields": fields, "fields_hash": content_hash(fields), "corrections": records}


def correct_report_field(workspace, lab_id, campaign_id, extraction_id, change):
    require_item(workspace, lab_id, campaign_id, "extraction", extraction_id, writable=True)
    validate_contract("report-field-correction.v1", change)
    if change["field"] not in EDITABLE_FIELDS:
        raise LabcalError("REPORT_FIELD_NOT_EDITABLE", "此入口仅更正报告识别信息，不能修改方案参数、限值或技术批准。")
    ext = get_extraction(workspace, lab_id, extraction_id)
    validate_location(ext["payload"], change["location"])
    value = change["value"].strip()
    if value and change["field"] in {"activity_date", "issue_date", "verification_valid_until"}:
        try: date.fromisoformat(value)
        except ValueError as exc: raise LabcalError("REPORT_DATE_INVALID", "日期请用YYYY-MM-DD；不明确时不要猜填。") from exc
    if not change["reason"].strip(): raise LabcalError("CORRECTION_REASON_REQUIRED", "请简要说明原页内容或更正原因。")
    store = campaign_store(workspace, lab_id); store.enable_report_corrections()
    key = identifier("RCO")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        before = report_fields(workspace, lab_id, campaign_id, ext, db=db)
        if before["fields_hash"] != change["expected_fields_hash"]:
            raise LabcalError("REPORT_CORRECTION_STALE", "识别信息已更新，请刷新后更正。")
        record = {"schema_version": "1.0", "lab_id": lab_id, "campaign_id": campaign_id,
            "extraction_id": extraction_id, "extraction_hash": ext["payload_hash"], "source_hash": ext["payload"]["source_hash"],
            **change, "value": value, "before": before["fields"].get(change["field"], []),
            "origin": "user_correction", "technical_approval": False, "created_at": utc_now()}
        db.execute("INSERT INTO report_corrections VALUES (?,?,?,?,?,?)", (key, campaign_id, extraction_id,
            canonical_json(record), content_hash(record), record["created_at"]))
        store.event(db, "report_candidate_corrected", key, {"field": change["field"], "extraction_id": extraction_id,
            "campaign_id": campaign_id, "record_hash": content_hash(record)})
    return {"id": key, "technical_approval": False, "diagnosis_refresh_required": True}
