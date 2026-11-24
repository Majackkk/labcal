import json
from pathlib import Path
import shutil
import tempfile
import zipfile
import sqlite3
from contextlib import closing

from ..errors import LabcalError
from ..util import content_hash, file_hash, utc_now, write_json, check_lab_id
from .sources import list_sources
from .plans import list_plans


def doctor(workspace, lab_id=None):
    issues, sources = [], list_sources(workspace, lab_id, include_references=lab_id is not None)
    for record in sources:
        source = workspace.resolve_source(lab_id, record["id"])
        path = Path(source["absolute_path"])
        if not path.is_file() or file_hash(path) != source["sha256"]:
            issues.append({"severity": "error", "code": "SOURCE_INTEGRITY_FAILED", "source_id": source["id"]})
        if source["metadata"].get("content_completeness") == "incomplete":
            issues.append({"severity": "pending", "code": "SOURCE_BODY_INCOMPLETE", "source_id": source["id"]})
    stores = [workspace.reference_store()]
    counts = {"sources": len(sources), "plans": 0, "confirmed_plans": 0, "baselines": 0}
    if lab_id:
        store = workspace.lab_store(lab_id)
        stores.append(store)
        plans = list_plans(workspace, lab_id)
        counts["plans"] = len(plans)
        with store.connect() as db:
            counts["confirmed_plans"] = db.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0]
            baselines = list(db.execute("SELECT id,payload,payload_hash FROM baselines"))
            counts["baselines"] = len(baselines)
            for baseline in baselines:
                if content_hash(json.loads(baseline["payload"])) != baseline["payload_hash"]:
                    issues.append({"severity": "error", "code": "BASELINE_INTEGRITY_FAILED", "baseline_id": baseline["id"]})
            if store.has_certificate_storage():
                for table, payload_field, hash_field in [("certificate_extractions", "payload", "payload_hash"), ("certificate_evidence", "record", "record_hash"), ("certificate_reviews", "payload", "payload_hash"), ("institution_evidence", "record", "record_hash"), ("finding_resolutions", "record", "record_hash"), ("certificate_findings", "record", "record_hash")]:
                    if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone(): continue
                    rows = list(db.execute(f"SELECT id,{payload_field},{hash_field} FROM {table}"))
                    counts[table] = len(rows)
                    for row in rows:
                        if content_hash(json.loads(row[payload_field])) != row[hash_field]:
                            issues.append({"severity": "error", "code": "CERTIFICATE_RECORD_INTEGRITY_FAILED", "table": table, "id": row["id"]})
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='campaigns'").fetchone():
                for table, body, digest in [("campaigns", "record", "record_hash"), ("workbook_imports", "payload", "payload_hash"), ("intelligence_packets", "payload", "payload_hash"), ("intelligence_results", "record", "record_hash")]:
                    rows = list(db.execute(f"SELECT id,{body},{digest} FROM {table}")); counts[table] = len(rows)
                    for row in rows:
                        if content_hash(json.loads(row[body])) != row[digest]:
                            issues.append({"severity": "error", "code": "CAMPAIGN_RECORD_INTEGRITY_FAILED", "table": table, "id": row["id"]})
                from .campaigns import object_snapshot
                for row in list(db.execute("SELECT * FROM campaign_links")):
                    try:
                        if object_snapshot(workspace, lab_id, row["kind"], row["entity_id"], row["revision"]) != row["entity_hash"]:
                            raise ValueError("hash mismatch")
                    except (ValueError, LabcalError):
                        issues.append({"severity": "error", "code": "CAMPAIGN_LINK_INTEGRITY_FAILED", "campaign_id": row["campaign_id"], "entity_id": row["entity_id"]})
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='report_associations'").fetchone():
                from ..domain.contracts import validate_contract
                for table, body, contract in [("report_associations", "payload", "report-association.v1"), ("report_jobs", "record", "report-job.v1")]:
                    rows = list(db.execute(f"SELECT * FROM {table}")); counts[table] = len(rows)
                    for row in rows:
                        try:
                            value = json.loads(row[body]); validate_contract(contract, value)
                            if value["lab_id"] != lab_id or value["campaign_id"] != row["campaign_id"]:
                                raise ValueError("scope mismatch")
                            if table == "report_associations":
                                if content_hash(value) != row["payload_hash"]: raise ValueError("hash mismatch")
                                ext = db.execute("SELECT payload_hash FROM certificate_extractions WHERE id=?", (value["extraction_id"],)).fetchone()
                                if not ext or ext[0] != value["extraction_hash"]: raise ValueError("extraction mismatch")
                        except (LabcalError, ValueError, KeyError, TypeError):
                            issues.append({"severity": "error", "code": "REPORT_RECORD_INTEGRITY_FAILED", "table": table, "id": row["id"]})
    for store in stores:
        for problem in store.audit_chain():
            issues.append({"severity": "error", "scope": store.lab_id or "reference", **problem})
    return {"status": "errors" if any(x["severity"] == "error" for x in issues) else "structural_checks_passed",
            "technical_approval": False, "counts": counts, "issues": issues,
            "note": "结构和完整性检查通过不等于参数技术核验或用户确认。"}


def backup_laboratory(workspace, lab_id, output):
    store = workspace.lab_store(lab_id)
    output = Path(output).resolve()
    if output.exists():
        raise LabcalError("OUTPUT_EXISTS", "备份已存在，请使用新的文件名。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="labcal-backup-") as directory:
        staged = Path(directory)
        store.backup_database(staged / "laboratory/lab.sqlite")
        workspace.reference_store().backup_database(staged / "references/catalog.sqlite")
        for prefix, origin in [("laboratory", store.directory), ("references", workspace.reference_store().directory)]:
            for child in origin.iterdir():
                if child.is_dir():
                    if child.is_symlink() or any(x.is_symlink() for x in child.rglob("*")):
                        raise LabcalError("BACKUP_SYMLINK", "运行数据区不应通过符号链接越过资料范围。")
                    shutil.copytree(child, staged / prefix / child.name, symlinks=False)
        entries = [{"path": p.relative_to(staged).as_posix(), "sha256": file_hash(p)} for p in sorted(staged.rglob("*")) if p.is_file()]
        manifest = {"schema_version": "1.0", "lab_id": lab_id, "created_at": utc_now(), "files": entries}
        write_json(staged / "backup.json", manifest)
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staged.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(staged).as_posix())
    return {"archive": str(output), "lab_id": lab_id, "files": len(entries), "sha256": file_hash(output)}


def restore_laboratory(workspace, archive_path):
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        for name in names:
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise LabcalError("BACKUP_PATH_INVALID", "备份包含超出目标范围的路径。")
        try:
            manifest = json.loads(archive.read("backup.json"))
        except (KeyError, ValueError) as exc:
            raise LabcalError("BACKUP_MANIFEST_INVALID", "备份清单缺失或无法解析。") from exc
        lab_id = check_lab_id(manifest["lab_id"])
        if manifest.get("schema_version") != "1.0":
            raise LabcalError("BACKUP_VERSION_UNSUPPORTED", "备份格式版本不受支持。")
        if (workspace.data / "laboratories" / lab_id).exists():
            raise LabcalError("RESTORE_TARGET_EXISTS", "目标实验室已经存在，不覆盖现有数据。")
        references = workspace.data / "references"
        if references.exists() and any(references.iterdir()):
            raise LabcalError("RESTORE_REFERENCE_TARGET_EXISTS", "完整恢复请使用没有参考库的新工作目录。")
        expected = {x["path"] for x in manifest["files"]} | {"backup.json"}
        if set(names) != expected or len(names) != len(expected):
            raise LabcalError("BACKUP_MEMBERS_MISMATCH", "备份文件集合与清单不一致。")
        with tempfile.TemporaryDirectory(prefix="labcal-restore-") as directory:
            staged = Path(directory)
            for record in manifest["files"]:
                if record["path"] not in names:
                    raise LabcalError("BACKUP_MEMBERS_MISMATCH", "备份清单引用了缺失文件。")
                destination = staged / record["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(record["path"]))
                if file_hash(destination) != record["sha256"]:
                    raise LabcalError("BACKUP_HASH_MISMATCH", "备份内容摘要不一致。")
            workspace.data.mkdir(parents=True, exist_ok=True)
            (workspace.data / "laboratories").mkdir(exist_ok=True)
            with closing(sqlite3.connect(staged / "laboratory/lab.sqlite")) as database:
                lab_record = database.execute("SELECT value FROM meta WHERE key='laboratory'").fetchone()
                if not lab_record or json.loads(lab_record[0]).get("id") != lab_id:
                    raise LabcalError("BACKUP_SCOPE_MISMATCH", "备份清单与实验室数据库标识不一致。")
            shutil.copytree(staged / "references", references)
            shutil.copytree(staged / "laboratory", workspace.data / "laboratories" / lab_id)
    report = doctor(workspace, lab_id)
    return {"lab_id": lab_id, "restored": True, "integrity": report}

