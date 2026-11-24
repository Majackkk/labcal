"""Immutable local certificate extraction, evidence, review and resolution records."""
from datetime import date, datetime
import json
import math
from pathlib import Path

from ..adapters.pdf import extract_pdf, location
from ..domain.contracts import validate_contract
from ..domain.rules import number
from ..errors import LabcalError
from ..util import canonical_json, content_hash, identifier, utc_now
from .laboratories import get_equipment
from .sources import import_source, verify_source_integrity


def certificate_store(workspace, lab_id):
    store = workspace.lab_store(lab_id)
    initialized = store.has_certificate_storage()
    with store.connect() as db:
        version = db.execute("SELECT value FROM meta WHERE key='certificate_schema_version'").fetchone()
    if not initialized or not version or version[0] != "2":
        store.enable_certificate_storage()
        with store.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='laboratory'").fetchone()
            value = json.loads(row[0])
            value["features"].update(certificate_acceptance=True, pdf_annotations=True)
            db.execute("UPDATE meta SET value=? WHERE key='laboratory'", (canonical_json(value),))
            store.event(db, "certificate_module_enabled" if not initialized else "certificate_schema_updated", lab_id, {"schema_version": "2"})
    return store


def add_certificate(workspace, lab_id, *, file=None, source_id=None, ocr=True, progress=None, pdf_reader=None, ocr_engine=None, force_ocr=False):
    if (file is None) == (source_id is None):
        raise LabcalError("CERTIFICATE_INPUT_INVALID", "指定一个PDF文件或已有原件标识。")
    store = certificate_store(workspace, lab_id)
    if file:
        if Path(file).suffix.lower() != ".pdf":
            raise LabcalError("CERTIFICATE_FORMAT_INVALID", "当前证书接入使用PDF格式。")
        source_id = import_source(workspace, file, kind="certificate_document", lab_id=lab_id)["id"]
    source = verify_source_integrity(workspace, lab_id, source_id)
    if source["scope"] != lab_id or source["kind"] not in {"calibration_certificate", "verification_certificate", "certificate_document"}:
        raise LabcalError("CERTIFICATE_SOURCE_INVALID", "只能接入当前实验室的校准或检定证书原件。")
    if Path(source["absolute_path"]).suffix.lower() != ".pdf":
        raise LabcalError("CERTIFICATE_FORMAT_INVALID", "证书原件应为PDF。")
    cert_id = "CERT-" + source["sha256"][:24]
    options = {"ocr": ocr, "progress": progress}
    if ocr_engine is not None: options["ocr_engine"] = ocr_engine
    if force_ocr: options["force_ocr"] = True
    payload = (pdf_reader or extract_pdf)(source["absolute_path"], source_id, source["sha256"], **options)
    validate_contract("certificate-extraction.v1", payload)
    digest = content_hash(payload)
    extraction_id = "EXT-" + digest[:24]
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT OR IGNORE INTO certificates VALUES (?,?,?)", (cert_id, source_id, utc_now()))
        changed = db.execute("INSERT OR IGNORE INTO certificate_extractions VALUES (?,?,?,?,?)",
                             (extraction_id, cert_id, canonical_json(payload), digest, utc_now())).rowcount
        if changed:
            store.event(db, "certificate_extracted", cert_id, {"extraction_id": extraction_id, "source_hash": source["sha256"], "payload_hash": digest})
    return {"certificate_id": cert_id, "extraction_id": extraction_id, "extraction_hash": digest,
            "document_kind": payload["document_kind"], "pages": len(payload["pages"]), "warnings": payload["warnings"], "reused": not bool(changed)}


def get_extraction(workspace, lab_id, extraction_id):
    store = certificate_store(workspace, lab_id)
    with store.connect() as db:
        row = db.execute("SELECT * FROM certificate_extractions WHERE id=?", (extraction_id,)).fetchone()
    if not row:
        raise LabcalError("EXTRACTION_NOT_FOUND", "当前实验室没有该证书提取版本。")
    value = dict(row); value["payload"] = json.loads(value["payload"])
    if content_hash(value["payload"]) != value["payload_hash"]:
        raise LabcalError("EXTRACTION_INTEGRITY_FAILED", "证书提取快照发生变化。")
    source = verify_source_integrity(workspace, lab_id, value["payload"]["source_id"])
    if source["sha256"] != value["payload"]["source_hash"]:
        raise LabcalError("EXTRACTION_SOURCE_CHANGED", "提取版本与原件不一致。")
    return value


def list_certificates(workspace, lab_id):
    store = certificate_store(workspace, lab_id)
    with store.connect() as db:
        rows = [dict(x) for x in db.execute("SELECT c.id,c.source_id,s.name,c.created_at FROM certificates c JOIN sources s ON c.source_id=s.id ORDER BY c.created_at DESC,c.id")]
        for row in rows:
            ext = db.execute("SELECT id,payload,payload_hash FROM certificate_extractions WHERE certificate_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (row["id"],)).fetchone()
            if ext:
                payload = json.loads(ext["payload"])
                row.update(extraction_id=ext["id"], extraction_hash=ext["payload_hash"], document_kind=payload["document_kind"], pages=len(payload["pages"]))
    return rows


def validate_location(extraction, loc):
    from ..domain.report_markup import validate_markup_location
    validate_markup_location(loc)
    pages = extraction["pages"]
    page_number, box = loc["page"], loc["bbox"]
    if not 1 <= page_number <= len(pages) or len(box) != 4 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not 0 <= x <= 1 for x in box):
        raise LabcalError("LOCATION_INVALID", "位置必须属于原件，并使用0至1的有限坐标。")
    if box[0] >= box[2] or box[1] >= box[3]:
        raise LabcalError("LOCATION_INVALID", "位置区域不能为空或反向。")
    lines = {x["id"]: x for x in pages[page_number - 1]["lines"]}
    if any(x not in lines for x in loc.get("line_ids", [])):
        raise LabcalError("LOCATION_LINE_MISMATCH", "原文锚点不属于该证书页面。")
    for key in loc.get("line_ids", []):
        other = lines[key]["bbox"]
        if box[0] >= other[2] or box[2] <= other[0] or box[1] >= other[3] or box[3] <= other[1]:
            raise LabcalError("LOCATION_LINE_MISMATCH", "位置区域与原文锚点不相交。")


def _actor(record):
    if not record["reviewer"].strip():
        raise LabcalError("REVIEWER_REQUIRED", "核验记录需要实际核验人。")
    try:
        moment = datetime.fromisoformat(record["reviewed_at"].replace("Z", "+00:00"))
        if moment.tzinfo is None:
            raise ValueError("timezone required")
    except ValueError as exc:
        raise LabcalError("REVIEW_TIME_INVALID", "核验时间需要带时区的ISO日期时间。") from exc


def evidence_template(workspace, lab_id, extraction_id):
    ext = get_extraction(workspace, lab_id, extraction_id)
    return {"extraction_id": extraction_id, "expected_extraction_hash": ext["payload_hash"],
            "source_hash": ext["payload"]["source_hash"], "reviewer": "", "reviewed_at": "",
            "fields": {}, "observations": [], "pages_checked": [], "observations_complete": False,
            "note": "仅填写实际回看原页的字段和结果；原文、位置见证书提取快照。"}


def record_evidence(workspace, lab_id, record):
    record = dict(record)
    validate_contract("certificate-evidence.v1", record)
    _actor(record)
    ext = get_extraction(workspace, lab_id, record["extraction_id"])
    payload = ext["payload"]
    if record["expected_extraction_hash"] != ext["payload_hash"] or record["source_hash"] != payload["source_hash"]:
        raise LabcalError("CERTIFICATE_EVIDENCE_STALE", "核验记录不属于当前提取版本或原件。")
    for field, datum in record["fields"].items():
        validate_location(payload, datum["location"])
        if isinstance(datum["value"], str) and not datum["value"].strip():
            raise LabcalError("EVIDENCE_VALUE_EMPTY", "核验值不能为空；缺失内容请使用absent_fields。")
        if isinstance(datum["value"], bool) and (datum["value"] is False or field not in {"approval", "results", "traceability", "uncertainty", "environment"}):
            raise LabcalError("EVIDENCE_VALUE_INVALID", "身份、日期及结论字段需填写原页核验值；不存在的内容用缺项记录。")
    for field in ["activity_date", "issue_date", "verification_valid_until"]:
        if field in record["fields"]:
            try:
                date.fromisoformat(record["fields"][field]["value"])
            except (ValueError, TypeError) as exc:
                raise LabcalError("CERTIFICATE_DATE_INVALID", "核验日期采用YYYY-MM-DD。") from exc
    checked = set(record.get("pages_checked", []))
    if not checked.issubset(set(range(1, len(payload["pages"]) + 1))):
        raise LabcalError("PAGE_NOT_FOUND", "核验页码超出证书。")
    if record.get("absent_fields") or record.get("observations_complete"):
        if checked != set(range(1, len(payload["pages"]) + 1)):
            raise LabcalError("WHOLE_DOCUMENT_REVIEW_REQUIRED", "声明缺项或结果录入完整，需要实际核对全部证书页面。")
    if set(record.get("absent_fields", [])) & set(record["fields"]):
        raise LabcalError("EVIDENCE_CONTRADICTORY", "同一字段不能同时声明存在和缺失。")
    if record.get("document_kind"):
        if not record.get("kind_location"):
            raise LabcalError("DOCUMENT_KIND_ANCHOR_REQUIRED", "文件类型确认需要原页位置。")
        validate_location(payload, record["kind_location"])
    if record.get("identity_checked"):
        if not record.get("equipment_id") or "instrument_number" not in record["fields"]:
            raise LabcalError("IDENTITY_EVIDENCE_REQUIRED", "设备绑定核验需要实际设备和原页仪器编号。")
    if record.get("equipment_id"):
        equipment = get_equipment(workspace, lab_id, record["equipment_id"])
        if record.get("equipment_config_hash") and record["equipment_config_hash"] != equipment["config_hash"]:
            raise LabcalError("CERTIFICATE_CONFIG_STALE", "设备配置在核验前已变化，请重新读取实际配置。")
        record["equipment_config_hash"] = equipment["config_hash"]
    ids = set()
    for row in record["observations"]:
        if row["id"] in ids:
            raise LabcalError("OBSERVATION_ID_DUPLICATE", "同一记录中的结果标识不能重复。")
        ids.add(row["id"])
        validate_location(payload, row["location"])
        if "point" in row: number(row["point"])
        if row.get("range"):
            interval = row["range"]
            if set(interval) - {"minimum", "maximum", "unit"} or number(interval.get("minimum")) > number(interval.get("maximum")):
                raise LabcalError("OBSERVATION_RANGE_INVALID", "结果范围无效。")
        for datum in row["inputs"].values():
            if not isinstance(datum, dict) or set(datum) - {"value", "unit", "relative_basis", "location"} or "value" not in datum:
                raise LabcalError("RESULT_DATUM_INVALID", "结果输入需要明确value及适用的unit，不能包含任意公式。")
            if isinstance(datum["value"], str) and datum.get("unit"):
                number(datum["value"])
            elif not isinstance(datum["value"], (str, bool)) or isinstance(datum["value"], float):
                raise LabcalError("DECIMAL_REQUIRED", "数值结果请使用带单位的十进制字符串。")
            if datum.get("location"): validate_location(payload, datum["location"])
    store = certificate_store(workspace, lab_id)
    digest = content_hash(record); evidence_id = "CEV-" + digest[:24]
    with store.connect() as db:
        changed = db.execute("INSERT OR IGNORE INTO certificate_evidence VALUES (?,?,?,?,?)",
                             (evidence_id, ext["id"], canonical_json(record), digest, utc_now())).rowcount
        if changed: store.event(db, "certificate_evidence_recorded", evidence_id, {"extraction_id": ext["id"], "record_hash": digest})
    return {"evidence_id": evidence_id, "reused": not bool(changed)}


def certificate_context(workspace, lab_id, extraction_id):
    ext = get_extraction(workspace, lab_id, extraction_id)
    context = {"extraction_id": ext["id"], "extraction_hash": ext["payload_hash"], "payload": ext["payload"],
               "certificate_id": ext["certificate_id"], "fields": {}, "observations": {}, "absent_fields": [],
               "evidence_ids": [], "identity_checked": False, "observations_complete": False,
               "document_kind": ext["payload"]["document_kind"], "kind_verified": False, "candidate_findings": []}
    with certificate_store(workspace, lab_id).connect() as db:
        records = list(db.execute("SELECT * FROM certificate_evidence WHERE extraction_id=? ORDER BY created_at,id", (ext["id"],)))
        candidates = list(db.execute("SELECT * FROM certificate_findings WHERE extraction_id=? ORDER BY created_at,id", (ext["id"],)))
    for row in candidates:
        record = json.loads(row["record"])
        if content_hash(record) != row["record_hash"]:
            raise LabcalError("FINDING_EVIDENCE_INTEGRITY_FAILED", "补充问题记录发生变化。")
        for issue in record["findings"]:
            for source in issue.get("basis_sources", []):
                verified = verify_source_integrity(workspace, lab_id, source["source_id"])
                if verified["sha256"] != source["source_hash"]:
                    raise LabcalError("FINDING_SOURCE_CHANGED", "补充问题依据与原件摘要不一致。")
            context["candidate_findings"].append({"record_id": row["id"], "origin": record["origin"], "author": record["author"], **issue})
    for row in records:
        record = json.loads(row["record"])
        if content_hash(record) != row["record_hash"]:
            raise LabcalError("CERTIFICATE_EVIDENCE_INTEGRITY_FAILED", "证书核验记录内容发生变化。")
        context["evidence_ids"].append(row["id"])
        context["fields"].update(record["fields"])
        for field in record["fields"]:
            if field in context["absent_fields"]: context["absent_fields"].remove(field)
        for field in record.get("absent_fields", []):
            context["fields"].pop(field, None)
            if field not in context["absent_fields"]: context["absent_fields"].append(field)
        for observation in record["observations"]:
            context["observations"][observation["id"]] = observation
        if "observations_complete" in record: context["observations_complete"] = record["observations_complete"]
        if "equipment_id" in record:
            context["equipment_id"] = record["equipment_id"]
            context["identity_checked"] = record.get("identity_checked", False)
            context["equipment_config_hash"] = record.get("equipment_config_hash")
        if "document_kind" in record:
            context["document_kind"] = record["document_kind"]; context["kind_verified"] = True
    context["observations"] = list(context["observations"].values())
    return context


def record_candidate_findings(workspace, lab_id, record):
    validate_contract("certificate-findings.v1", record)
    if not record["author"].strip(): raise LabcalError("FINDING_AUTHOR_REQUIRED", "请明确补充问题的提出人或智能体。")
    ext = get_extraction(workspace, lab_id, record["extraction_id"])
    if record["expected_extraction_hash"] != ext["payload_hash"]:
        raise LabcalError("CERTIFICATE_EVIDENCE_STALE", "补充问题不属于该原件提取版本。")
    for issue in record["findings"]:
        validate_location(ext["payload"], issue["location"])
        for source in issue.get("basis_sources", []):
            verified = verify_source_integrity(workspace, lab_id, source["source_id"])
            if verified["sha256"] != source["source_hash"]:
                raise LabcalError("FINDING_SOURCE_CHANGED", "问题依据原件摘要不匹配。")
    digest = content_hash(record); record_id = "CFD-" + digest[:24]
    store = certificate_store(workspace, lab_id)
    with store.connect() as db:
        changed = db.execute("INSERT OR IGNORE INTO certificate_findings VALUES (?,?,?,?,?)",
                             (record_id, ext["id"], canonical_json(record), digest, utc_now())).rowcount
        if changed: store.event(db, "certificate_findings_proposed", record_id, {"extraction_id": ext["id"], "record_hash": digest})
    return {"candidate_record_id": record_id, "reused": not bool(changed), "certainty": "pending", "rerun_required": True}


def record_institution(workspace, lab_id, record):
    validate_contract("institution-evidence.v1", record); _actor(record)
    source = verify_source_integrity(workspace, lab_id, record["source_id"])
    if source["scope"] != lab_id or source["kind"] != "institution_scope" or source["sha256"] != record["source_hash"]:
        raise LabcalError("INSTITUTION_SOURCE_INVALID", "机构范围记录需要当前实验室登记的范围附件原件及匹配摘要。")
    try:
        start, end = date.fromisoformat(record["valid_from"]), date.fromisoformat(record["valid_until"])
        if start > end: raise ValueError("reversed")
    except ValueError as exc:
        raise LabcalError("INSTITUTION_DATES_INVALID", "机构范围有效日期无效。") from exc
    for row in record["parameters"]:
        if not isinstance(row, dict) or set(row) - {"parameter", "unit", "minimum", "maximum", "component", "mode", "conditions", "cmc", "anchor"} or not isinstance(row.get("parameter"), str) or not row["parameter"].strip():
            raise LabcalError("INSTITUTION_PARAMETER_INVALID", "范围项目需明确parameter，且仅使用支持的字段。")
        if "minimum" in row or "maximum" in row:
            if not row.get("unit") or number(row.get("minimum")) > number(row.get("maximum")):
                raise LabcalError("INSTITUTION_RANGE_INVALID", "机构项目范围需要单位和有效上下限。")
    digest = content_hash(record); record_id = "INS-" + digest[:24]
    store = certificate_store(workspace, lab_id)
    with store.connect() as db:
        changed = db.execute("INSERT OR IGNORE INTO institution_evidence VALUES (?,?,?,?,?)",
                             (record_id, source["id"], canonical_json(record), digest, utc_now())).rowcount
        if changed: store.event(db, "institution_evidence_recorded", record_id, {"record_hash": digest, "source_hash": source["sha256"]})
    return {"institution_evidence_id": record_id, "reused": not bool(changed)}


def institution_records(workspace, lab_id):
    store = certificate_store(workspace, lab_id)
    with store.connect() as db:
        rows = list(db.execute("SELECT * FROM institution_evidence ORDER BY created_at,id"))
    result = []
    for row in rows:
        record = json.loads(row["record"])
        if content_hash(record) != row["record_hash"]:
            raise LabcalError("INSTITUTION_EVIDENCE_INTEGRITY_FAILED", "机构证据发生变化。")
        source = verify_source_integrity(workspace, lab_id, record["source_id"])
        if source["sha256"] != record["source_hash"]:
            raise LabcalError("INSTITUTION_SOURCE_CHANGED", "机构范围原件不匹配。")
        result.append({"id": row["id"], **record})
    return result


def get_review(workspace, lab_id, review_id):
    store = certificate_store(workspace, lab_id)
    with store.connect() as db:
        row = db.execute("SELECT * FROM certificate_reviews WHERE id=?", (review_id,)).fetchone()
        if not row: raise LabcalError("REVIEW_NOT_FOUND", "当前实验室没有该审查任务。")
        resolutions = list(db.execute("SELECT * FROM finding_resolutions WHERE review_id=? ORDER BY created_at,id", (review_id,)))
    result = dict(row); result["payload"] = json.loads(result["payload"])
    if content_hash(result["payload"]) != result["payload_hash"]:
        raise LabcalError("REVIEW_INTEGRITY_FAILED", "审查快照发生变化。")
    result["resolutions"] = {}
    for resolution in resolutions:
        record = json.loads(resolution["record"])
        if content_hash(record) != resolution["record_hash"]:
            raise LabcalError("RESOLUTION_INTEGRITY_FAILED", "复核记录发生变化。")
        result["resolutions"][record["finding_id"]] = {"id": resolution["id"], **record}
    return result


def list_reviews(workspace, lab_id):
    with certificate_store(workspace, lab_id).connect() as db:
        rows = list(db.execute("SELECT id,payload,payload_hash,created_at FROM certificate_reviews ORDER BY created_at DESC,id"))
    return [{"id": x["id"], "created_at": x["created_at"], "payload_hash": x["payload_hash"],
             "baseline_id": json.loads(x["payload"]).get("baseline_id"),
             "axes": json.loads(x["payload"])["axes"], "findings": len(json.loads(x["payload"])["findings"])} for x in rows]


def record_resolution(workspace, lab_id, record):
    validate_contract("finding-resolution.v1", record); _actor(record)
    review = get_review(workspace, lab_id, record["review_id"])
    if record["expected_review_hash"] != review["payload_hash"]:
        raise LabcalError("REVIEW_VERSION_CONFLICT", "复核记录不属于该审查快照。")
    finding = next((x for x in review["payload"]["findings"] if x["id"] == record["finding_id"]), None)
    if not finding: raise LabcalError("FINDING_NOT_FOUND", "该审查没有此问题。")
    if record["action"] == "resolved" and not record.get("evidence_locations"):
        raise LabcalError("RESOLUTION_EVIDENCE_REQUIRED", "问题关闭需要可回看的证据位置；更正证书应重新接入审查。")
    ext = get_extraction(workspace, lab_id, finding["extraction_id"])
    for loc in record.get("evidence_locations", []): validate_location(ext["payload"], loc)
    digest = content_hash(record); resolution_id = "RES-" + digest[:24]
    store = certificate_store(workspace, lab_id)
    with store.connect() as db:
        changed = db.execute("INSERT OR IGNORE INTO finding_resolutions VALUES (?,?,?,?,?,?)",
                             (resolution_id, record["review_id"], record["finding_id"], canonical_json(record), digest, utc_now())).rowcount
        if changed: store.event(db, "finding_reviewed", resolution_id, {"review_id": record["review_id"], "record_hash": digest})
    return {"resolution_id": resolution_id, "reused": not bool(changed), "rerun_required": True}
