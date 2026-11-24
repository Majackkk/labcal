import json
from pathlib import Path
import shutil
import logging
import os
import tempfile

from ..errors import LabcalError
from ..util import canonical_json, file_hash, utc_now

SOURCE_KINDS = {"test_method_standard", "metrology_regulation", "manufacturer_manual", "cnas_document",
                "calibration_certificate", "verification_certificate", "legacy_plan", "legacy_table",
                "legacy_form", "supporting_document", "lab_configuration", "user_plan", "institution_scope", "certificate_document"}
SHARED_KINDS = {"test_method_standard", "metrology_regulation", "cnas_document"}


def inspect_file(path):
    path = Path(path)
    result = {"bytes": path.stat().st_size, "format": path.suffix.lower().lstrip("."),
              "content_completeness": "pending", "technical_review": "pending"}
    if path.suffix.lower() == ".pdf":
        if path.read_bytes()[:5] != b"%PDF-":
            raise LabcalError("FILE_NOT_PDF", "文件扩展名为PDF，但内容不是PDF。")
        try:
            from pypdf import PdfReader
            logger = logging.getLogger("pypdf")
            messages = []
            class Collector(logging.Handler):
                def emit(self, record):
                    if len(messages) < 20:
                        messages.append(record.getMessage())
            handler = Collector()
            old_handlers, old_propagate = logger.handlers[:], logger.propagate
            logger.handlers, logger.propagate = [handler], False
            try:
                reader = PdfReader(path)
            finally:
                logger.handlers, logger.propagate = old_handlers, old_propagate
            if messages:
                result["structural_warnings"] = messages
            result.update({"pages": len(reader.pages), "encrypted": reader.is_encrypted,
                           "container_readable": True})
        except ImportError:
            result["container_readable"] = "pdf_dependency_unavailable"
        except Exception as exc:
            result.update({"container_readable": False, "inspection_error": str(exc)})
    return result


def import_source(workspace, path, *, kind, lab_id=None, code=None, name=None, metadata=None, shareable=False):
    path = Path(path).resolve()
    if not path.is_file():
        raise LabcalError("SOURCE_FILE_MISSING", "原件文件不存在。", {"path": str(path)})
    if kind not in SOURCE_KINDS:
        raise LabcalError("SOURCE_KIND_INVALID", "原件类别不在支持范围。")
    if lab_id is None and (kind not in SHARED_KINDS or not shareable):
        raise LabcalError("SHARING_SCOPE_REQUIRED", "共享资料必须是允许共用的标准或CNAS资料，并明确指定可共享。")
    store = workspace.reference_store() if lab_id is None else workspace.lab_store(lab_id)
    digest = file_hash(path)
    source_id = ("REF-" if lab_id is None else "SRC-") + digest[:24]
    with store.connect() as db:
        existing = db.execute("SELECT * FROM sources WHERE sha256=?", (digest,)).fetchone()
        if existing:
            record = dict(existing)
            record["metadata"] = json.loads(record["metadata"])
            aliases = record["metadata"].setdefault("aliases", [])
            alias = {"path": str(path), "name": name or path.name, "code": code, "kind": kind}
            if alias not in aliases:
                aliases.append(alias)
                db.execute("UPDATE sources SET metadata=? WHERE id=?", (canonical_json(record["metadata"]), record["id"]))
                store.event(db, "source_alias_added", record["id"], alias)
            return {"id": record["id"], "sha256": digest, "reused": True}
    details = inspect_file(path)
    details.update(metadata or {})
    details["original_path"] = str(path)
    details.setdefault("acquired_at", utc_now())
    details.setdefault("version_status", "pending")
    details.setdefault("applicability_status", "pending")
    # Data supplied by a document is never allowed to grant a technical review.
    details["technical_review"] = "pending"
    relative = f"objects/{digest[:2]}/{digest}{path.suffix.lower()}"
    destination = store.directory / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if file_hash(destination) != digest:
            raise LabcalError("OBJECT_HASH_CONFLICT", "原件存储位置中的内容与哈希不一致。")
    else:
        # Publish complete bytes atomically. Two background imports of identical
        # content cannot observe or truncate a partially copied object.
        descriptor, staged = tempfile.mkstemp(prefix=".source-", dir=destination.parent)
        os.close(descriptor)
        try:
            shutil.copyfile(path, staged)
            if file_hash(staged) != digest:
                raise LabcalError("SOURCE_CHANGED_DURING_IMPORT", "复制过程中原件发生变化，请重新接入。")
            try: os.link(staged, destination)
            except FileExistsError:
                if file_hash(destination) != digest:
                    raise LabcalError("OBJECT_HASH_CONFLICT", "原件存储位置中的内容与哈希不一致。")
        finally:
            Path(staged).unlink(missing_ok=True)
    with store.connect() as db:
        inserted = db.execute("INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?,?,?,?)",
                   (source_id, digest, kind, code, name or path.name, relative, canonical_json(details), utc_now()))
        if inserted.rowcount: store.event(db, "source_imported", source_id, {"sha256": digest, "kind": kind, "code": code})
    if not inserted.rowcount:
        return import_source(workspace, path, kind=kind, lab_id=lab_id, code=code, name=name, metadata=metadata, shareable=shareable)
    return {"id": source_id, "sha256": digest, "reused": False, "inspection": details}


def list_sources(workspace, lab_id=None, *, include_references=False):
    stores = [workspace.reference_store()] if lab_id is None else [workspace.lab_store(lab_id)]
    if lab_id is not None and include_references:
        stores.append(workspace.reference_store())
    result = []
    for store in stores:
        with store.connect() as db:
            for row in db.execute("SELECT * FROM sources ORDER BY created_at,id"):
                item = dict(row)
                item["metadata"] = json.loads(item["metadata"])
                item["scope"] = "reference" if store.lab_id is None else lab_id
                result.append(item)
    return result


def verify_source_integrity(workspace, lab_id, source_id):
    source = workspace.resolve_source(lab_id, source_id)
    path = Path(source["absolute_path"])
    if not path.is_file() or file_hash(path) != source["sha256"]:
        raise LabcalError("SOURCE_INTEGRITY_FAILED", "原件存储内容缺失或发生变化。", {"source_id": source_id})
    return source


def source_gaps(workspace, lab_id, expected_codes):
    records = list_sources(workspace, lab_id, include_references=True)
    def normalize(value):
        return "".join(c for c in (value or "").upper() if c.isalnum())
    found = {normalize(row["code"]): row for row in records if row["code"]}
    result = []
    for code in expected_codes:
        item = found.get(normalize(code))
        if item is None:
            bare = code.rsplit(":", 1)[0] if ":" in code else code
            year = code.rsplit(":", 1)[1] if ":" in code else None
            candidates = [row for row in records if normalize(row["code"]) == normalize(bare)]
            matching = [row for row in candidates if not year or year in row["name"] or year == str(row["metadata"].get("edition", ""))]
            if matching:
                item = matching[0]
            else:
                result.append({"code": code, "status": "version_pending" if candidates else "original_missing",
                               "candidate_source_ids": [row["id"] for row in candidates]})
                continue
        if item is not None:
            metadata = item["metadata"]
            status = "incomplete" if metadata.get("content_completeness") == "incomplete" else "available_pending_review"
            result.append({"code": code, "status": status, "source_id": item["id"],
                           "version_status": metadata.get("version_status", "pending")})
    return result

