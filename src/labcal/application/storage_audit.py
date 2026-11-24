"""On-demand storage audit: database facts, originals and disposable projections.

This check never rewrites records, archives or technical conclusions. Hashes prove
identity/integrity only. File reads are bounded to the selected lab/reference roots.
"""
from ..adapters.layout import output_directory
import json
from pathlib import Path
import sqlite3
from contextlib import closing
from time import perf_counter

from ..domain.contracts import validate_contract
from ..errors import LabcalError
from ..util import content_hash, ensure_inside, file_hash
from ..adapters.workbook_archive import import_directory


def audit_storage(workspace, lab_id, campaign_id=None):
    try: return _audit_storage(workspace, lab_id, campaign_id)
    except (json.JSONDecodeError, KeyError, TypeError, sqlite3.DatabaseError) as exc:
        # Broken records must produce a failed audit, never a false green result.
        return {"schema_version":"1.0", "lab_id":lab_id, "campaign_id":campaign_id,
            "status":"errors", "technical_approval":False, "counts":{}, "query_ms":0,
            "issues":[{"severity":"error", "code":"AUDIT_RECORD_UNREADABLE", "message":"存在无法解析或结构不完整的数据库记录，请保留资料并检查。"}]}


def _audit_storage(workspace, lab_id, campaign_id=None):
    started = perf_counter(); store = workspace.lab_store(lab_id)
    issues = []; counts = {"objects": 0, "fixed_records": 0, "campaigns": 0, "archive_files": 0, "exports": 0}
    cache = {}
    def issue(code, message, *, severity="error", path=None):
        item = {"code": code, "severity": severity, "message": message}
        if path is not None: item["path"] = str(path)
        issues.append(item)
    def json_file(path, expected=None, *, projection=False):
        try:
            actual = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(actual, dict): raise ValueError("Expected an object")
            if expected is not None and actual != expected: raise ValueError("snapshot differs")
            counts["archive_files"] += 1
            return actual
        except (OSError, ValueError, UnicodeError):
            issue("PROJECTION_STALE" if projection else "ARCHIVE_JSON_MISMATCH",
                  "目录索引缺失或与数据库不一致，可从固定事实重建。" if projection else "固定归档记录缺失或内容与数据库不同。",
                  path=path, severity="warning" if projection else "error")
    def checked_file(path, digest, root, category):
        try:
            path = ensure_inside(path, root)
            key = str(path)
            if key not in cache: cache[key] = file_hash(path)
            if cache[key] != digest: raise ValueError("hash differs")
            counts[category] += 1
        except (OSError, ValueError, LabcalError):
            issue("FILE_INTEGRITY_FAILED", "文件缺失、越界或摘要不符。", path=path)

    def snapshot(selected):
        # Read-only connection and one consistent SQLite snapshot, no schema migration.
        with closing(sqlite3.connect(selected.path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row; db.execute("BEGIN")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok": issue("DATABASE_INTEGRITY_FAILED", "数据库结构检查失败。", path=selected.path)
            if list(db.execute("PRAGMA foreign_key_check")): issue("DATABASE_FOREIGN_KEY_FAILED", "数据库引用存在异常。", path=selected.path)
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            records = {t: [dict(r) for r in db.execute("SELECT * FROM " + t)] for t in tables if not t.startswith("sqlite_")}
        return records

    records = snapshot(store)
    campaigns = {r["id"]: r for r in records.get("campaigns", [])}
    if campaign_id and campaign_id not in campaigns: raise LabcalError("CAMPAIGN_NOT_FOUND", "当前实验室没有该校准批次。")
    for selected, data in [(store, records), (workspace.reference_store(), snapshot(workspace.reference_store()))]:
        for row in data.get("sources", []):
            checked_file(selected.directory / row["object_path"], row["sha256"], selected.directory / "objects", "objects")
            if json.loads(row["metadata"]).get("content_completeness") == "incomplete":
                issue("SOURCE_BODY_INCOMPLETE", "参考资料正文不完整，仍需补充原件。", severity="pending")
        counts["fixed_records"] += len(data.get("events", []))
        previous = ""
        for row in sorted(data.get("events", []), key=lambda r:r["seq"]):
            value = {k: row[k] for k in ("kind", "subject_id", "occurred_at", "previous_hash")}
            value["payload"] = json.loads(row["payload"])
            if row["previous_hash"] != previous or content_hash(value) != row["event_hash"]:
                issue("EVENT_CHAIN_CHANGED", "追加事件记录链不一致。", path=selected.path)
            previous = row["event_hash"]
        for row in data.get('method_packages', []):
            if content_hash(json.loads(row['payload'])) != row['payload_hash']:
                issue('METHOD_RECORD_CHANGED', '共享方法要求摘要不一致。')
    for table, body, digest in [("plan_revisions","payload","payload_hash"), ("baselines","payload","payload_hash"),
            ("certificate_extractions","payload","payload_hash"), ("certificate_evidence","record","record_hash"),
            ("certificate_reviews","payload","payload_hash"), ("certificate_findings","record","record_hash"),
            ("report_corrections","record","record_hash"),
            ("finding_resolutions","record","record_hash"), ("institution_evidence","record","record_hash"),
            ("campaigns","record","record_hash"), ("workbook_imports","payload","payload_hash"),
            ("report_associations","payload","payload_hash"), ("intelligence_packets","payload","payload_hash"),
            ("intelligence_results","record","record_hash")]:
        for row in records.get(table, []):
            counts["fixed_records"] += 1
            if content_hash(json.loads(row[body])) != row[digest]: issue("FIXED_RECORD_CHANGED", "数据库固定记录摘要不一致：" + table)
    for table, field, digest in [('method_uses', 'record', 'record_hash'), ('method_adoptions', 'record', 'record_hash'), ('method_diagnoses', 'payload', 'payload_hash')]:
        for row in records.get(table, []):
            if content_hash(json.loads(row[field])) != row[digest]: issue('METHOD_RECORD_CHANGED', '方法用途或诊断摘要不一致。')
    from .document_templates import verify_confirmation
    for entry in records.get('meta', []):
        if entry['key'].startswith('equipment_confirmation:'):
            metadata = json.loads(entry['value'])
            if campaign_id and metadata['campaign_id'] != campaign_id: continue
            try:
                verify_confirmation(store, metadata)
                counts['exports'] += 2
            except LabcalError as exc:
                issue(exc.code, '设备确认表或共享记录缺失或摘要不一致。')
    current_summary = next((r for r in records.get('meta', []) if r['key'] == 'summary_current'), None)
    for row in records.get('meta', []):
        if not row['key'].startswith('document_template_version:'): continue
        template = json.loads(row['value']); payload = template['payload']
        if content_hash(payload) != template['payload_hash'] or payload['lab_id'] != lab_id:
            issue('TEMPLATE_CHANGED', '实验室专用模板固定记录摘要或归属不一致。')
        path = ensure_inside(store.directory / payload['archive_path'], store.directory / 'templates')
        checked_file(path, payload['source_hash'], store.directory / 'templates', 'archive_files')
        json_file(path.parent / (payload['id'] + '.json'), template)
    if current_summary:
        metadata = json.loads(current_summary['value'])
        path = store.directory / 'outputs' / 'summaries' / '设备校准诊断汇总.xlsx'
        if not path.is_file() or file_hash(path) != metadata['sha256']:
            issue('SUMMARY_LOCALLY_CHANGED', '当前汇总表缺失或存在外部修改；历史导出保留，不自动覆盖。', severity='warning', path=path)

    sources = {r["id"]: r for r in records.get("sources", [])}
    workbooks = {r["id"]: r for r in records.get("workbook_imports", [])}
    extractions = {r["id"]: r for r in records.get("certificate_extractions", [])}
    snapshots = {kind: {r[key]:r[digest] for r in records.get(table, [])} for kind,table,key,digest in [
        ("source","sources","id","sha256"), ("workbook","workbook_imports","id","payload_hash"),
        ("extraction","certificate_extractions","id","payload_hash"), ("review","certificate_reviews","id","payload_hash"),
        ("baseline","baselines","id","payload_hash"), ("intelligence","intelligence_packets","id","payload_hash")]}
    snapshots["plan"] = {(r["plan_id"],r["revision"]):r["payload_hash"] for r in records.get("plan_revisions", [])}
    for row in records.get("campaign_links", []):
        key = (row["entity_id"],row["revision"]) if row["kind"] == "plan" else row["entity_id"]
        if snapshots.get(row["kind"],{}).get(key) != row["entity_hash"]: issue("CAMPAIGN_LINK_CHANGED", "批次固定引用与实际记录摘要不同。")

    for key, row in campaigns.items():
        if campaign_id and key != campaign_id: continue
        counts["campaigns"] += 1
        campaign = json.loads(row["record"])
        directory = ensure_inside(store.directory / "campaigns" / str(campaign["year"] or "undated") / key, store.directory)
        links = [r for r in records.get("campaign_links", []) if r["campaign_id"] == key]
        index = json_file(directory / "index.json", projection=True)
        if index:
            signature = lambda values: sorted((r["kind"],r["entity_id"],r["revision"],r["entity_hash"]) for r in values)
            expected_campaign = {**campaign, "record_hash":row["record_hash"]}
            if index.get("campaign") != expected_campaign or signature(index.get("items", [])) != signature(links):
                issue("PROJECTION_STALE", "批次索引没有反映当前数据库，可重建。", severity="warning", path=directory / "index.json")
        for link in links:
            if link["kind"] == "workbook":
                item = workbooks[link["entity_id"]]; payload = json.loads(item["payload"]); source = sources[payload["source_id"]]
                folder = import_directory(directory, source["name"], item["id"])
                checked_file(folder / "原件.xlsx", source["sha256"], directory, "archive_files")
                json_file(folder / "提取记录.json", payload)
                manifest = json_file(folder / "索引.json")
                if manifest and any(manifest.get(k) != v for k,v in {"lab_id":lab_id,"campaign_id":key,"workbook_id":item["id"],"workbook_hash":item["payload_hash"],"source_hash":source["sha256"]}.items()):
                    issue("WORKBOOK_MANIFEST_CHANGED", "方案归属索引与固定版本不同。", path=folder)
        report_sources = {json.loads(extractions[r["entity_id"]]["payload"])["source_id"] for r in links if r["kind"] == "extraction" and r["entity_id"] in extractions}
        for source_id in report_sources:
            source = sources[source_id]; folder = directory / "证书报告" / "原件档案" / source_id
            checked_file(folder / "原件.pdf", source["sha256"], directory, "archive_files")
            json_file(folder / "原件索引.json", {"source_id":source_id,"source_hash":source["sha256"],"source_name":source["name"],"lab_relative_object_path":source["object_path"],"original_file":"原件.pdf"})
        for association in records.get("report_associations", []):
            if association["campaign_id"] != key: continue
            payload = json.loads(association["payload"]); match = payload["match"]
            category = "设备关联" if match["state"] in {"matched","operator_linked"} else "待确认关联" if match["state"] == "suggested" else "待匹配"
            ext = extractions.get(association["extraction_id"])
            if not ext: continue
            source_id = json.loads(ext["payload"])["source_id"]
            folder = ensure_inside(directory / "证书报告" / category / (match["device_id"] or "未确定设备") / source_id, directory)
            json_file(folder / (association["id"] + ".json"), {"id":association["id"],"payload":payload,"payload_hash":association["payload_hash"]})
    reviews = {r["entity_id"] for r in records.get("campaign_links", []) if r["kind"] == "review" and (not campaign_id or r["campaign_id"] == campaign_id)}
    plans = {(r["entity_id"],r["revision"]) for r in records.get("campaign_links", []) if r["kind"] == "plan" and (not campaign_id or r["campaign_id"] == campaign_id)}
    for run in records.get("runs", []):
        manifest = json.loads(run["manifest"])
        if run["kind"] not in {"plan_export", "certificate_review_export", "certificate_self_diagnosis", "method_diagnosis", "laboratory_summary"}: continue
        if campaign_id and not (manifest.get("review_id") in reviews or (manifest.get("plan_id"),manifest.get("revision")) in plans or run["kind"] in {'certificate_self_diagnosis','method_diagnosis'} and manifest.get("campaign_id") == campaign_id or run['kind'] == 'laboratory_summary'): continue
        folder = output_directory(store, run["id"], run["kind"])
        json_file(folder / "manifest.json", manifest)
        for artifact in manifest.get("artifacts", []): checked_file(folder / artifact["name"], artifact["sha256"], folder, "exports")
    if any(r.get("state") in {"running", "queued"} for r in (json.loads(j["record"]) for j in records.get("report_jobs", []))):
        issue("ACTIVE_JOBS", "校验期间有识别任务，文件投影可能正在更新；任务结束后可再检查。", severity="warning")
    result = {"schema_version":"1.0", "lab_id":lab_id, "campaign_id":campaign_id,
        "status":"errors" if any(i["severity"] == "error" for i in issues) else "warnings" if any(i["severity"] == "warning" for i in issues) else "passed",
        "technical_approval":False, "counts":counts, "issues":issues, "query_ms":round((perf_counter()-started)*1000,2)}
    validate_contract("storage-audit.v1", result)
    return result
