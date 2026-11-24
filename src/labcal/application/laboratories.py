import json

from ..errors import LabcalError
from ..util import canonical_json, check_lab_id, content_hash, identifier, utc_now


def create_laboratory(workspace, lab_id, name, *, laboratory_type="testing", profile="cnas_cl01", test_only=False):
    check_lab_id(lab_id)
    if not name.strip():
        raise LabcalError("LAB_NAME_EMPTY", "实验室显示名称不能为空。")
    store = workspace.lab_store(lab_id, create=True)
    value = {"id": lab_id, "name": name, "laboratory_type": laboratory_type,
             "profile": profile, "test_only": bool(test_only), "created_at": utc_now(),
             "features": {"certificate_acceptance": True, "pdf_annotations": True,
                          "interval_management": False, "historical_trend": False, "abnormal_impact": False}}
    with store.connect() as db:
        existing = db.execute("SELECT value FROM meta WHERE key='laboratory'").fetchone()
        if existing:
            old = json.loads(existing[0])
            if any(old[k] != value[k] for k in ["id", "name", "laboratory_type", "profile", "test_only"]):
                raise LabcalError("LAB_ALREADY_EXISTS", "实验室标识已有不同配置。")
            return {"laboratory": old, "reused": True}
        db.execute("INSERT INTO meta VALUES ('laboratory', ?)", (canonical_json(value),))
        db.execute("INSERT INTO meta VALUES ('workspace_role', ?)", (canonical_json({
            "schema_version": "1.0", "role": "laboratory", "reason": "通过新建实验室入口建立的工作空间。"}),))
        store.event(db, "laboratory_created", lab_id, value)
    return {"laboratory": value, "reused": False}


def add_equipment(workspace, lab_id, asset_code, name, model="", configuration=None):
    if not asset_code.strip() or not name.strip():
        raise LabcalError("EQUIPMENT_IDENTITY_EMPTY", "设备名称和资产标识不能为空。")
    configuration = configuration or {}
    if not isinstance(configuration, dict):
        raise LabcalError("CONFIGURATION_INVALID", "实际配置必须是JSON对象。")
    store = workspace.lab_store(lab_id)
    equipment_id = identifier("EQ")
    identity = {"asset_code": asset_code, "name": name, "model": model, "configuration": configuration}
    digest = content_hash({**identity, "revision": 1})
    with store.connect() as db:
        existing = db.execute("SELECT * FROM equipment WHERE asset_code=?", (asset_code,)).fetchone()
        if existing:
            current_identity = {"asset_code": existing["asset_code"], "name": existing["name"], "model": existing["model"], "configuration": json.loads(existing["configuration"])}
            if current_identity != identity:
                raise LabcalError("EQUIPMENT_ALREADY_EXISTS", "相同资产标识已有不同配置，请通过设备更新登记变更。")
            return {"id": existing["id"], "config_hash": existing["config_hash"], "reused": True}
        db.execute("INSERT INTO equipment VALUES (?,?,?,?,?,?,?,?,?)",
                   (equipment_id, asset_code, name, model, canonical_json(configuration), digest, "pending", utc_now(), 1))
        store.event(db, "equipment_created", equipment_id, {"configuration_hash": digest, **identity})
    return {"id": equipment_id, "config_hash": digest, "reused": False}


def update_laboratory(workspace, lab_id, record):
    """Explicit operator choices; existing lab identities and technical records survive."""
    from ..domain.contracts import validate_contract
    validate_contract("laboratory-settings.v1", record)
    if not all(isinstance(record.get(k), str) and record[k].strip() for k in ("operator", "user_statement", "expected_hash")):
        raise LabcalError("LAB_SETTINGS_EVIDENCE_REQUIRED", "实验室设置需填写实际操作人、明确选择说明及当前版本。")
    store = workspace.lab_store(lab_id)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        old = json.loads(db.execute("SELECT value FROM meta WHERE key='laboratory'").fetchone()[0])
        if content_hash(old) != record["expected_hash"]:
            raise LabcalError("LAB_SETTINGS_VERSION_CONFLICT", "实验室设置已变化，请刷新。")
        value = dict(old)
        for key in ("name", "laboratory_type", "profile"):
            if key in record:
                if not isinstance(record[key], str) or not record[key].strip(): raise LabcalError("LAB_SETTINGS_INVALID", "实验室名称、类型和准则不能为空。")
                value[key] = record[key].strip()
        value["settings_revision"] = old.get("settings_revision", 1) + 1
        db.execute("UPDATE meta SET value=? WHERE key='laboratory'", (canonical_json(value),))
        store.event(db, "laboratory_settings_changed", lab_id, {"before": old, "after": value, "operator": record["operator"], "user_statement": record["user_statement"]})
    return {"laboratory": value, "settings_hash": content_hash(value)}


def get_equipment(workspace, lab_id, equipment_id):
    with workspace.lab_store(lab_id).connect() as db:
        row = db.execute("SELECT * FROM equipment WHERE id=?", (equipment_id,)).fetchone()
    if not row:
        raise LabcalError("EQUIPMENT_NOT_FOUND", "当前实验室中没有该设备。", {"equipment_id": equipment_id})
    result = dict(row)
    result["configuration"] = json.loads(result["configuration"])
    return result


def list_equipment(workspace, lab_id):
    with workspace.lab_store(lab_id).connect() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM equipment ORDER BY asset_code")]
    return [get_equipment(workspace, lab_id, key) for key in ids]


def update_equipment(workspace, lab_id, equipment_id, value):
    old = get_equipment(workspace, lab_id, equipment_id)
    expected = value.get("expected_config_hash")
    if expected != old["config_hash"]:
        raise LabcalError("CONFIGURATION_VERSION_CONFLICT", "设备配置已变化或未提供期望版本，请重新读取当前配置。")
    identity = {key: value.get(key, old[key]) for key in ["asset_code", "name", "model", "configuration"]}
    if not isinstance(identity["configuration"], dict):
        raise LabcalError("CONFIGURATION_INVALID", "实际配置必须是JSON对象。")
    current_identity = {key: old[key] for key in ["asset_code", "name", "model", "configuration"]}
    if identity == current_identity:
        return {"id": equipment_id, "config_hash": old["config_hash"], "changed": False}
    new_revision = old["config_revision"] + 1
    digest = content_hash({**identity, "revision": new_revision})
    store = workspace.lab_store(lab_id)
    with store.connect() as db:
        cursor = db.execute("UPDATE equipment SET asset_code=?,name=?,model=?,configuration=?,config_hash=?,configuration_state='pending',config_revision=? WHERE id=? AND config_hash=?",
                            (identity["asset_code"], identity["name"], identity["model"], canonical_json(identity["configuration"]), digest, new_revision, equipment_id, expected))
        if cursor.rowcount != 1:
            raise LabcalError("CONFIGURATION_VERSION_CONFLICT", "设备配置在操作期间发生变化。")
        store.event(db, "equipment_configuration_changed", equipment_id, {"before": expected, "after": digest, "configuration": identity})
    return {"id": equipment_id, "config_hash": digest, "changed": digest != expected}

