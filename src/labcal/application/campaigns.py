"""Laboratory scoped work containers referencing immutable business versions."""
from ..adapters.layout import output_directory
import json
import os

from ..domain.contracts import validate_contract
from ..errors import LabcalError
from ..util import canonical_json, content_hash, identifier, utc_now, ensure_inside, write_json, atomic_write_text


def campaign_store(workspace, lab_id):
    store = workspace.lab_store(lab_id)
    store.enable_campaign_storage()
    return store


def get_campaign(workspace, lab_id, campaign_id, *, writable=False):
    with campaign_store(workspace, lab_id).connect() as db:
        row = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    if not row:
        raise LabcalError("CAMPAIGN_NOT_FOUND", "当前实验室没有该校准批次。")
    record = json.loads(row["record"])
    if content_hash(record) != row["record_hash"] or record["lab_id"] != lab_id:
        raise LabcalError("CAMPAIGN_INTEGRITY_FAILED", "批次资料摘要或实验室归属不一致。")
    if writable and record["state"] != "active":
        raise LabcalError("CAMPAIGN_ARCHIVED", "批次已归档；恢复为进行中后再追加资料。")
    return {**record, "record_hash": row["record_hash"]}


def list_campaigns(workspace, lab_id):
    with campaign_store(workspace, lab_id).connect() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM campaigns")]
    return sorted([get_campaign(workspace, lab_id, key) for key in ids],
                  key=lambda c: (c["year"] or 0, c["created_at"]), reverse=True)


def create_campaign(workspace, lab_id, name, *, year=None, notes="", campaign_id=None):
    value = {"schema_version": "1.0", "lab_id": lab_id, "id": campaign_id or identifier("CAM"),
             "name": name.strip(), "year": year, "notes": notes, "state": "active", "created_at": utc_now()}
    validate_contract("campaign.v1", value)
    if year is not None and year > 9999:
        raise LabcalError("CAMPAIGN_YEAR_INVALID", "年度需为1900至9999之间的整数或待定。")
    store = campaign_store(workspace, lab_id)
    with store.connect() as db:
        existing = db.execute("SELECT record FROM campaigns WHERE id=?", (value["id"],)).fetchone()
        if existing:
            old = json.loads(existing[0])
            if (old["name"], old["year"], old["notes"]) != (value["name"], year, notes):
                raise LabcalError("CAMPAIGN_ALREADY_EXISTS", "批次标识已有不同内容。")
            return get_campaign(workspace, lab_id, value["id"])
        db.execute("INSERT INTO campaigns VALUES (?,?,?)", (value["id"], canonical_json(value), content_hash(value)))
        store.event(db, "campaign_created", value["id"], value)
    refresh_index(workspace, lab_id, value["id"])
    return get_campaign(workspace, lab_id, value["id"])


def update_campaign(workspace, lab_id, campaign_id, record):
    old = get_campaign(workspace, lab_id, campaign_id)
    if record.get("expected_record_hash") != old["record_hash"]:
        raise LabcalError("CAMPAIGN_VERSION_CONFLICT", "批次信息已变化，请刷新后重试。")
    value = {key: old[key] for key in old if key != "record_hash"}
    value.update({key: record[key] for key in ("name", "year", "notes", "state") if key in record})
    validate_contract("campaign.v1", value)
    if not value["name"].strip() or (value["year"] is not None and value["year"] > 9999):
        raise LabcalError("CAMPAIGN_METADATA_INVALID", "请填写批次名称及有效年度。")
    store = campaign_store(workspace, lab_id)
    previous = campaign_directory(workspace, lab_id, old)
    destination = campaign_directory(workspace, lab_id, value)
    moving = previous != destination
    if moving and destination.exists():
        raise LabcalError("CAMPAIGN_DIRECTORY_EXISTS", "目标年度的批次目录已存在，未覆盖其资料。")
    moved = False
    try:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("UPDATE campaigns SET record=?,record_hash=? WHERE id=? AND record_hash=?",
                (canonical_json(value), content_hash(value), campaign_id, old["record_hash"]))
            if cursor.rowcount != 1:
                raise LabcalError("CAMPAIGN_VERSION_CONFLICT", "批次信息在操作期间发生变化。")
            if moving and previous.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                previous.rename(destination); moved = True
            store.event(db, "campaign_metadata_changed", campaign_id, {"before": old, "after": value})
    except BaseException:
        if moved and not previous.exists(): destination.rename(previous)
        raise
    refresh_index(workspace, lab_id, campaign_id)
    return get_campaign(workspace, lab_id, campaign_id)


def object_snapshot(workspace, lab_id, kind, entity_id, revision=0):
    if kind == "source":
        from .sources import verify_source_integrity
        item = verify_source_integrity(workspace, lab_id, entity_id)
        if item["scope"] == "reference":
            raise LabcalError("SHARED_REFERENCE_NOT_CAMPAIGN_FILE", "共享参考资料保持参考库归属，通过方案证据引用。")
        return item["sha256"]
    if kind == "plan":
        from .plans import get_plan
        if not revision: raise LabcalError("FIXED_REVISION_REQUIRED", "方案归档必须明确修订号。")
        return get_plan(workspace, lab_id, entity_id, revision)["payload_hash"]
    if revision:
        raise LabcalError("REVISION_NOT_APPLICABLE", "只有方案引用使用修订号。")
    if kind == "baseline":
        from .plans import get_baseline
        return get_baseline(workspace, lab_id, entity_id)["payload_hash"]
    if kind == "extraction":
        from .certificates import get_extraction
        return get_extraction(workspace, lab_id, entity_id)["payload_hash"]
    if kind == "review":
        from .certificates import get_review
        return get_review(workspace, lab_id, entity_id)["payload_hash"]
    table = {"workbook": "workbook_imports", "intelligence": "intelligence_packets"}.get(kind)
    if not table: raise LabcalError("CAMPAIGN_KIND_INVALID", "不支持的批次资料类别。")
    with campaign_store(workspace, lab_id).connect() as db:
        row = db.execute(f"SELECT payload,payload_hash FROM {table} WHERE id=?", (entity_id,)).fetchone()
    if not row: raise LabcalError("CAMPAIGN_OBJECT_NOT_FOUND", "当前实验室中没有该资料版本。")
    if content_hash(json.loads(row[0])) != row[1]:
        raise LabcalError("CAMPAIGN_OBJECT_INTEGRITY_FAILED", "资料版本摘要不一致。")
    return row[1]


def attach_item(workspace, lab_id, campaign_id, kind, entity_id, revision=0, *, expected_hash=None):
    campaign = get_campaign(workspace, lab_id, campaign_id, writable=True)
    digest = object_snapshot(workspace, lab_id, kind, entity_id, revision)
    value = {"kind": kind, "entity_id": entity_id, "revision": revision, "entity_hash": digest}
    validate_contract("campaign-link.v1", value)
    if expected_hash and expected_hash != digest:
        raise LabcalError("CAMPAIGN_OBJECT_VERSION_CONFLICT", "选择的资料版本已变化。")
    store = campaign_store(workspace, lab_id)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # Metadata changes and archival are checked again under the write lock.
        row = db.execute("SELECT record_hash FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if row[0] != campaign["record_hash"]:
            raise LabcalError("CAMPAIGN_VERSION_CONFLICT", "批次状态已变化，请刷新。")
        old = db.execute("SELECT entity_hash FROM campaign_links WHERE campaign_id=? AND kind=? AND entity_id=? AND revision=?",
                         (campaign_id, kind, entity_id, revision)).fetchone()
        if old and old[0] != digest:
            raise LabcalError("CAMPAIGN_OBJECT_VERSION_CONFLICT", "已有固定引用的摘要不同。")
        cursor = db.execute("INSERT OR IGNORE INTO campaign_links VALUES (?,?,?,?,?,?)",
                            (campaign_id, kind, entity_id, revision, digest, utc_now()))
        if cursor.rowcount: store.event(db, "campaign_item_attached", campaign_id, value)
    refresh_index(workspace, lab_id, campaign_id)
    return {**value, "campaign_id": campaign_id, "reused": not bool(cursor.rowcount)}


def list_items(workspace, lab_id, campaign_id):
    get_campaign(workspace, lab_id, campaign_id)
    with campaign_store(workspace, lab_id).connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM campaign_links WHERE campaign_id=? ORDER BY created_at,kind,entity_id", (campaign_id,))]


def require_item(workspace, lab_id, campaign_id, kind, entity_id, revision=0, *, writable=False):
    get_campaign(workspace, lab_id, campaign_id, writable=writable)
    with campaign_store(workspace, lab_id).connect() as db:
        row = db.execute("SELECT entity_hash FROM campaign_links WHERE campaign_id=? AND kind=? AND entity_id=? AND revision=?",
                         (campaign_id, kind, entity_id, revision)).fetchone()
    if not row:
        raise LabcalError("CAMPAIGN_SCOPE_MISMATCH", "该资料版本未加入当前批次，请明确引用后再操作。")
    if object_snapshot(workspace, lab_id, kind, entity_id, revision) != row[0]:
        raise LabcalError("CAMPAIGN_OBJECT_INTEGRITY_FAILED", "批次引用与固定资料摘要不一致。")


def campaign_directory(workspace, lab_id, campaign):
    root = workspace.lab_store(lab_id).directory / "campaigns"
    return ensure_inside(root / str(campaign["year"] or "undated") / campaign["id"], workspace.lab_store(lab_id).directory)


def refresh_index(workspace, lab_id, campaign_id):
    campaign = get_campaign(workspace, lab_id, campaign_id)
    directory = campaign_directory(workspace, lab_id, campaign)
    directory.mkdir(parents=True, exist_ok=True)
    items = list_items(workspace, lab_id, campaign_id)
    links = []
    for item in items:
        if item["kind"] == "source":
            source = workspace.resolve_source(lab_id, item["entity_id"])
            item.update(name=source["name"], lab_relative_object_path=source["object_path"])
            label = " ".join(source["name"].split()).replace("[", "\\[").replace("]", "\\]")
            links.append(f"- [{label}]({os.path.relpath(source['absolute_path'], directory)})")
    reviews = {i["entity_id"] for i in items if i["kind"] == "review"}
    exports = []
    with workspace.lab_store(lab_id).connect() as db:
        for row in db.execute("SELECT id,manifest FROM runs WHERE kind='certificate_review_export'"):
            manifest = json.loads(row["manifest"])
            if manifest["review_id"] in reviews:
                exports.append({"run_id": row["id"], "review_id": manifest["review_id"], "lab_relative_directory": output_directory(workspace.lab_store(lab_id), row["id"], "certificate_review_export").relative_to(workspace.lab_store(lab_id).directory).as_posix(), "artifacts": manifest["artifacts"]})
    write_json(directory / "index.json", {"read_only_projection": True, "campaign": campaign, "items": items, "exports": exports}, overwrite=True)
    atomic_write_text(directory / "README.md", f"# {campaign['name']}\n\n实验室：{lab_id}；年度：{campaign['year'] or '待定'}。\n\n此目录是批次索引。业务事实保存在实验室数据库，原件保存在实验室 objects 中。请使用 ./labcal 或中控台维护归属，不直接编辑索引。\n\n## 原件入口\n\n" + ("\n".join(links) or "暂无原件引用。") + "\n", overwrite=True)
