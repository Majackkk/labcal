"""Automatic candidate grouping, scoped report associations and diagnostic projection."""
import json

from ..adapters.recognition import recognized_fields
from ..adapters.report_archive import preserve_report, preserve_association
from ..domain.contracts import validate_contract
from ..domain.recognition import VERSION, match_identity
from ..domain.certificate_review import status
from ..errors import LabcalError
from ..util import canonical_json, content_hash, identifier, utc_now
from .campaigns import campaign_store, get_campaign, require_item, list_items, campaign_directory, attach_item
from .certificates import get_extraction, get_review
from .sources import verify_source_integrity
from .workbooks import get_workbook


def intake_store(workspace, lab_id):
    store = campaign_store(workspace, lab_id); store.enable_intake_storage()
    return store


def device_catalog(workspace, lab_id, campaign_id):
    get_campaign(workspace, lab_id, campaign_id)
    store = intake_store(workspace, lab_id)
    devices, sources_seen = [], set()
    with store.connect() as db:
        books = list(db.execute("SELECT w.* FROM workbook_imports w JOIN campaign_links l ON l.entity_id=w.id AND l.kind='workbook' WHERE l.campaign_id=? ORDER BY w.created_at DESC,w.id", (campaign_id,)))
    for row in books:
        book = get_workbook(workspace, lab_id, row["id"])
        source_id = book["payload"]["source_id"]
        if source_id in sources_seen: continue
        sources_seen.add(source_id)
        devices.extend(book["devices"])
    # Existing structured plans remain available. Only their latest scoped revision
    # is considered; current baseline readiness is rechecked before every review.
    from .plans import get_plan, assess_plan
    from .laboratories import get_equipment
    latest = {}
    for item in list_items(workspace, lab_id, campaign_id):
        if item["kind"] == "plan":
            latest[item["entity_id"]] = max(latest.get(item["entity_id"], 0), item["revision"])
    for key, revision in latest.items():
        p = get_plan(workspace, lab_id, key, revision); payload = p["payload"]
        eq = get_equipment(workspace, lab_id, payload["equipment_id"])
        ready = assess_plan(workspace, lab_id, key, revision)
        baseline_id = None
        if ready["automatic_baseline_available"] and ready["configuration_current"]:
            with store.connect() as db:
                b = db.execute("SELECT b.id FROM baselines b JOIN campaign_links l ON l.entity_id=b.id AND l.kind='baseline' WHERE l.campaign_id=? AND b.plan_id=? AND b.revision=?", (campaign_id, key, revision)).fetchone()
            if b: baseline_id = b[0]
        covered_rows = {r.get("raw", {}).get("id") for r in payload["requirements"]}
        covered_books = {r.get("raw", {}).get("workbook_id") for r in payload["requirements"]}
        devices = [d for d in devices if not (d.get("workbook_id") in covered_books and set(d["rows"]) <= covered_rows)]
        devices.append({"id": key + "@" + str(revision), "kind": "structured_plan", "name": eq["name"],
            "identity": {"equipment_name": eq["name"], "model": eq["model"], "equipment_number": eq["asset_code"], "manufacturer": None},
            "equipment_id": eq["id"], "plan_id": key, "revision": revision, "plan_hash": p["payload_hash"],
            "technical_state": "confirmed" if ready["user_confirmed"] else "candidate", "baseline_id": baseline_id,
            "rows": [r["id"] for r in payload["requirements"]], "anchors": []})
    return {"devices": devices, "catalog_hash": content_hash(devices), "recognizer": VERSION}


def latest_association(workspace, lab_id, campaign_id, extraction_id):
    require_item(workspace, lab_id, campaign_id, "extraction", extraction_id)
    with intake_store(workspace, lab_id).connect() as db:
        row = db.execute("SELECT * FROM report_associations WHERE campaign_id=? AND extraction_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (campaign_id, extraction_id)).fetchone()
    if not row: return None
    value = dict(row); value["payload"] = json.loads(value["payload"])
    if content_hash(value["payload"]) != value["payload_hash"]:
        raise LabcalError("ASSOCIATION_INTEGRITY_FAILED", "报告设备关联记录摘要不一致。")
    return value


def archive_association(workspace, lab_id, campaign_id, ext, item):
    source = verify_source_integrity(workspace, lab_id, ext["payload"]["source_id"])
    directory = campaign_directory(workspace, lab_id, get_campaign(workspace, lab_id, campaign_id))
    preserve_report(directory, source); preserve_association(directory, item, source)
    return item


def associate_report(workspace, lab_id, campaign_id, extraction_id, *, choice=None):
    require_item(workspace, lab_id, campaign_id, "extraction", extraction_id, writable=True)
    ext = get_extraction(workspace, lab_id, extraction_id); catalog = device_catalog(workspace, lab_id, campaign_id)
    previous = latest_association(workspace, lab_id, campaign_id, extraction_id)
    if not choice and previous and previous["payload"].get("operator"):
        # Automatic reclassification cannot overwrite an operator's choice.
        return archive_association(workspace, lab_id, campaign_id, ext, previous)
    from .report_corrections import report_fields
    fields_state = report_fields(workspace, lab_id, campaign_id, ext)
    match = match_identity(fields_state["fields"], catalog["devices"])
    payload = {"schema_version": "1.0", "lab_id": lab_id, "campaign_id": campaign_id,
        "extraction_id": extraction_id, "extraction_hash": ext["payload_hash"], "recognizer": VERSION,
        "catalog_hash": catalog["catalog_hash"], "match": match, "device": None, "technical_approval": False}
    if choice:
        validate_contract("report-association-choice.v1", choice)
        if not choice["operator"].strip() or not choice["statement"].strip():
            raise LabcalError("ASSOCIATION_EVIDENCE_REQUIRED", "请填写实际操作人及选择依据。")
        if not previous or previous["id"] != choice["expected_association_id"]:
            raise LabcalError("ASSOCIATION_STALE", "设备关联记录已变化，请刷新后再选择。")
        match.update(state="operator_linked", device_id=choice["device_id"], reason="操作人已明确资料归属；技术验收仍按确认基准和原页核验开展。")
        payload.update(operator=choice["operator"].strip(), statement=choice["statement"].strip(), previous_id=previous["id"])
    if match["device_id"]:
        payload["device"] = next((d for d in catalog["devices"] if d["id"] == match["device_id"]), None)
        if payload["device"] is None: raise LabcalError("DEVICE_CANDIDATE_NOT_FOUND", "所选候选设备不属于当前批次的方案清单。")
    validate_contract("report-association.v1", payload)
    digest = content_hash(payload); key = identifier("ASC")
    store = intake_store(workspace, lab_id)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        latest = db.execute("SELECT * FROM report_associations WHERE campaign_id=? AND extraction_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (campaign_id, extraction_id)).fetchone()
        if latest and latest["payload_hash"] == digest:
            item = {**dict(latest), "payload": json.loads(latest["payload"])}
        else:
            if (latest["id"] if latest else None) != (previous["id"] if previous else None):
                raise LabcalError("ASSOCIATION_STALE", "设备关联在处理期间发生变化，请刷新。")
            db.execute("INSERT INTO report_associations VALUES (?,?,?,?,?,?)", (key, campaign_id, extraction_id, canonical_json(payload), digest, utc_now()))
            store.event(db, "report_associated", key, {"payload_hash": digest, "state": match["state"]})
            item = {"id": key, "payload": payload, "payload_hash": digest}
    return archive_association(workspace, lab_id, campaign_id, ext, item)


def review_report(workspace, lab_id, campaign_id, extraction_id):
    """A single report always gets a scoped review; matching never asserts identity_checked."""
    from .certificate_reviews import create_review
    association = associate_report(workspace, lab_id, campaign_id, extraction_id)
    d = association["payload"]["device"]
    baseline = d.get("baseline_id") if d and association["payload"]["match"]["state"] in {"matched", "operator_linked"} else None
    # If a previously selected version is no longer current, keep the fixed
    # association for history and conduct a basic review until explicitly resolved.
    if baseline:
        from .plans import assess_plan
        readiness = assess_plan(workspace, lab_id, d["plan_id"], d["revision"])
        if not readiness["automatic_baseline_available"] or not readiness["configuration_current"]: baseline = None
    result = create_review(workspace, lab_id, [extraction_id], baseline_id=baseline, campaign_id=campaign_id)
    attach_item(workspace, lab_id, campaign_id, "review", result["review_id"])
    return {**result, "association_id": association["id"], "match_state": association["payload"]["match"]["state"]}


def review_related(workspace, lab_id, campaign_id, extraction_id):
    """Union only explicit report associations with exactly the same device reference."""
    from .certificate_reviews import create_review
    association = latest_association(workspace, lab_id, campaign_id, extraction_id)
    if not association or not association["payload"]["match"]["device_id"]:
        raise LabcalError("REPORT_DEVICE_UNRESOLVED", "先澄清该报告对应的设备，再联合审查。")
    target = association["payload"]["match"]["device_id"]
    with intake_store(workspace, lab_id).connect() as db:
        keys = [r[0] for r in db.execute("SELECT entity_id FROM campaign_links WHERE campaign_id=? AND kind='extraction'", (campaign_id,))]
    ids, certs = [], set()
    for key in keys:
        candidate = latest_association(workspace, lab_id, campaign_id, key)
        if candidate and candidate["payload"]["match"]["device_id"] == target:
            ext = get_extraction(workspace, lab_id, key)
            if ext["certificate_id"] in certs: raise LabcalError("REPORT_VERSION_AMBIGUOUS", "同一证书存在多个提取版本，请先明确使用版本。")
            certs.add(ext["certificate_id"]); ids.append(key)
    if len(ids) < 2: raise LabcalError("REPORT_GROUP_SINGLE", "当前设备尚无多份关联报告。")
    d = association["payload"]["device"]; baseline = d.get("baseline_id")
    if association["payload"]["match"]["state"] == "suggested": baseline = None
    if baseline:
        from .plans import assess_plan
        readiness = assess_plan(workspace, lab_id, d["plan_id"], d["revision"])
        if not readiness["automatic_baseline_available"] or not readiness["configuration_current"]: baseline = None
    result = create_review(workspace, lab_id, ids, baseline_id=baseline, campaign_id=campaign_id)
    attach_item(workspace, lab_id, campaign_id, "review", result["review_id"])
    return result


from .report_diagnosis import diagnostic_projection


def report_detail(workspace, lab_id, campaign_id, extraction_id, *, review_id=None):
    require_item(workspace, lab_id, campaign_id, "extraction", extraction_id)
    ext = get_extraction(workspace, lab_id, extraction_id)
    association = latest_association(workspace, lab_id, campaign_id, extraction_id)
    latest_review = None
    if review_id:
        require_item(workspace, lab_id, campaign_id, "review", review_id)
        latest_review = get_review(workspace, lab_id, review_id)
        if extraction_id not in {s["extraction_id"] for s in latest_review["payload"]["certificate_snapshots"]}:
            raise LabcalError("REVIEW_REPORT_MISMATCH", "该报告不属于所选固定审查任务。")
    if latest_review is None:
        found_id=None
        with intake_store(workspace, lab_id).connect() as db:
            rows = db.execute("SELECT r.id,r.payload FROM certificate_reviews r JOIN campaign_links l ON l.entity_id=r.id AND l.kind='review' WHERE l.campaign_id=? ORDER BY r.created_at DESC", (campaign_id,))
            try:
                for row in rows:
                    if extraction_id in {s["extraction_id"] for s in json.loads(row["payload"])["certificate_snapshots"]}:
                        found_id=row["id"];break
            finally:rows.close()
        if found_id:latest_review=get_review(workspace,lab_id,found_id)
    source = verify_source_integrity(workspace, lab_id, ext["payload"]["source_id"])
    directory = campaign_directory(workspace, lab_id, get_campaign(workspace, lab_id, campaign_id)) / "证书报告" / "原件档案" / source["id"]
    diagnostics = diagnostic_projection(latest_review, extraction_id) if latest_review else []
    from .diagnostic_report import build_comparison
    from .point_comparison import build_point_matrix
    from .report_corrections import report_fields
    from .report_diagnosis import ENGINE, input_hash
    from .certificates import certificate_context, institution_records
    fields_state=report_fields(workspace, lab_id, campaign_id, ext)
    context=certificate_context(workspace,lab_id,extraction_id)
    catalog=device_catalog(workspace,lab_id,campaign_id)
    digest=input_hash(context,fields_state,association,catalog["catalog_hash"],institution_records(workspace,lab_id))
    stored=(latest_review or {}).get("payload",{}).get("report_views",{}).get(extraction_id)
    frozen=stored["payload"] if stored else None
    if stored and content_hash(frozen)!=stored["payload_hash"]:
        raise LabcalError("DIAGNOSIS_SNAPSHOT_INTEGRITY", "诊断快照摘要不一致。")
    if frozen and frozen["campaign_id"]!=campaign_id: frozen=None
    stale=bool(latest_review and (not frozen or frozen["engine"]!=ENGINE or frozen["input_hash"]!=digest))
    # A historical view is displayed whole, never mixed with current candidate fields.
    fields=frozen["fields"] if frozen else fields_state["fields"]
    comparison=frozen["comparison_report"] if frozen else build_comparison(workspace, lab_id, campaign_id, ext, association, latest_review, diagnostics, fields=fields)
    matrix=frozen["point_matrix"] if frozen else build_point_matrix(workspace, lab_id, campaign_id, ext, association, latest_review,contexts=[context])
    return {"extraction": ext, "fields": fields, "fields_hash":content_hash(fields), "association": frozen["association"] if frozen else association,
        "review": latest_review, "diagnostics": diagnostics,
        "point_matrix": matrix, "comparison_report": comparison,
        "diagnostics": frozen["diagnostics"] if frozen else diagnostics,
        "diagnosis":frozen, "diagnosis_stale":stale,
        "diagnosis_note":"识别、资料或方案版本已变化；请开始诊断以生成新快照，旧结果保留。" if stale else "识别、对照、批注与导出引用同一固定诊断快照。",
        "archive_directory": str(directory) if directory.is_dir() else None,
        "catalog_stale": bool(association and (association["payload"]["catalog_hash"] != catalog["catalog_hash"] or association["payload"]["recognizer"] != VERSION)),
        "recognition_provider": "local_rules", "model_service_connected": False}
