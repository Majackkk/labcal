"""Versioned exchange contracts and the subset of JSON Schema used by them."""
import re

from ..errors import LabcalError


STRING = {"type": "string"}
TEXT = {"type": "string", "minLength": 1}
NULLABLE_OBJECT = {"type": ["object", "null"]}
ROUTES = ["external_calibration", "legal_verification", "internal_calibration",
          "functional_check", "pre_use_check", "unresolved"]

EVIDENCE = {
    "type": "object", "required": ["source_id", "claim_kind"],
    "properties": {
        "source_id": TEXT, "claim_kind": {"enum": ["normative", "manufacturer", "lab_requirement", "legacy"]},
        "page": {"type": ["integer", "null"], "minimum": 1}, "anchor": STRING,
        "fields": {"type": "array", "items": TEXT, "uniqueItems": True},
    }, "additionalProperties": False,
}

REQUIREMENT = {
    "type": "object", "required": ["id", "parameter", "unit", "delivery_route", "coverage_policy", "points", "report_fields", "evidence"],
    "properties": {
        "id": TEXT, "parameter": TEXT, "measurand": STRING, "unit": STRING,
        "component": STRING, "channel": STRING, "mode": STRING,
        "conditions": {"type": "object"}, "mandatory": {"type": "boolean"},
        "delivery_route": {"enum": ROUTES}, "certificate_group": STRING,
        "coverage_policy": {"enum": ["explicit_points", "range", "representative_points", "unresolved"]},
        "calibration_range": NULLABLE_OBJECT, "points": {"type": "array", "items": STRING, "uniqueItems": True},
        "report_fields": {"type": "array", "items": TEXT, "uniqueItems": True},
        "relative_basis": {"enum": ["reading", "reference_value", "full_scale", "not_applicable", "unresolved"]},
        "limit": NULLABLE_OBJECT, "uncertainty": NULLABLE_OBJECT,
        "decision_rule": NULLABLE_OBJECT,
        "evidence": {"type": "array", "items": EVIDENCE}, "raw": {"type": "object"},
    }, "additionalProperties": False,
}

SCHEMAS = {
    "plan.v1": {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "Calibration plan revision",
        "type": "object", "required": ["schema_version", "lab_id", "equipment_id", "equipment_config_hash", "title", "origin", "requirements", "source_ids"],
        "properties": {
            "schema_version": {"const": "1.0"}, "lab_id": TEXT, "equipment_id": TEXT,
            "equipment_config_hash": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "title": TEXT, "origin": {"enum": ["user_plan", "evidence_draft", "legacy_markdown"]},
            "source_ids": {"type": "array", "items": TEXT, "uniqueItems": True},
            "requirements": {"type": "array", "items": REQUIREMENT, "minItems": 1},
            "open_items": {"type": "array", "items": {"type": "object"}},
            "notes": STRING,
        }, "additionalProperties": False,
    },
    "confirmation.v1": {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "required": ["lab_id", "plan_id", "revision", "payload_hash", "equipment_config_hash", "confirmed_by", "confirmed_at", "evidence_kind", "user_statement", "confirmed_fields", "decision"],
        "properties": {
            "lab_id": TEXT, "plan_id": TEXT, "revision": {"type": "integer", "minimum": 1}, "decision": {"const": "confirm"},
            "payload_hash": TEXT, "equipment_config_hash": TEXT, "confirmed_by": TEXT,
            "confirmed_at": TEXT, "evidence_kind": {"enum": ["direct_user_message", "operator_command"]},
            "user_statement": TEXT, "evidence_reference": STRING,
            "confirmed_fields": {"type": "array", "items": {"enum": ["equipment_configuration", "plan_scope", "requirements"]}, "uniqueItems": True},
        }, "additionalProperties": False,
    },
    "source.v1": {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "required": ["id", "sha256", "kind", "name", "metadata"],
        "properties": {
            "id": TEXT, "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "kind": TEXT, "code": {"type": ["string", "null"]}, "name": TEXT,
            "metadata": {"type": "object"}, "object_path": STRING,
            "created_at": STRING, "scope": STRING, "absolute_path": STRING,
        }, "additionalProperties": False,
    },
    "baseline.v1": {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "required": ["schema_version", "lab_id", "plan_id", "revision", "plan_hash", "configuration_hash", "rules_version", "requirements", "source_snapshots", "readiness"],
        "properties": {
            "schema_version": {"const": "1.0"}, "lab_id": TEXT, "plan_id": TEXT,
            "revision": {"type": "integer", "minimum": 1}, "plan_hash": TEXT,
            "configuration_hash": TEXT, "rules_version": TEXT,
            "requirements": {"type": "array", "items": REQUIREMENT},
            "source_snapshots": {"type": "array", "items": {"type": "object"}},
            "readiness": {"type": "object"}, "external_certificate_groups": {"type": "object"},
            "confirmation_id": TEXT,
        }, "additionalProperties": False,
    },

}


from .certificate_contracts import SCHEMAS as CERTIFICATE_SCHEMAS
SCHEMAS.update(CERTIFICATE_SCHEMAS)
from .campaign_contracts import SCHEMAS as CAMPAIGN_SCHEMAS
SCHEMAS.update(CAMPAIGN_SCHEMAS)
from .intake_contracts import SCHEMAS as INTAKE_SCHEMAS
SCHEMAS.update(INTAKE_SCHEMAS)
from .storage_contracts import SCHEMAS as STORAGE_SCHEMAS
SCHEMAS.update(STORAGE_SCHEMAS)


def validate_contract(name, value):
    errors = []
    _validate(value, SCHEMAS[name], "$", errors)
    if errors:
        raise LabcalError("CONTRACT_INVALID", "输入不符合版本化数据结构。", {"contract": name, "errors": errors})


def _validate(value, schema, path, errors):
    types = schema.get("type")
    types = [types] if isinstance(types, str) else types
    checks = {
        "object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list),
        "string": lambda x: isinstance(x, str), "boolean": lambda x: isinstance(x, bool),
        "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
        "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
        "null": lambda x: x is None,
    }
    if types and not any(checks[t](value) for t in types):
        errors.append({"path": path, "reason": "type", "expected": types})
        return
    if "const" in schema and value != schema["const"]:
        errors.append({"path": path, "reason": "const"})
    if "enum" in schema and value not in schema["enum"]:
        errors.append({"path": path, "reason": "enum"})
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append({"path": path, "reason": "empty"})
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append({"path": path, "reason": "pattern"})
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value < schema.get("minimum", float("-inf")):
        errors.append({"path": path, "reason": "minimum"})
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append({"path": f"{path}.{key}", "reason": "required"})
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], f"{path}.{key}", errors)
            elif schema.get("additionalProperties") is False:
                errors.append({"path": f"{path}.{key}", "reason": "unknown_field"})
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append({"path": path, "reason": "minItems"})
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append({"path": path, "reason": "maxItems"})
        if schema.get("uniqueItems") and len({repr(x) for x in value}) != len(value):
            errors.append({"path": path, "reason": "duplicate_items"})
        for index, item in enumerate(value):
            _validate(item, schema.get("items", {}), f"{path}[{index}]", errors)


from .self_diagnosis_contracts import SCHEMAS as SELF_DIAGNOSIS_SCHEMAS
SCHEMAS.update(SELF_DIAGNOSIS_SCHEMAS)

from .method_contracts import SCHEMAS as METHOD_SCHEMAS
SCHEMAS.update(METHOD_SCHEMAS)

from .document_template_contracts import SCHEMAS as DOCUMENT_TEMPLATE_SCHEMAS
SCHEMAS.update(DOCUMENT_TEMPLATE_SCHEMAS)

from .context_contracts import SCHEMAS as CONTEXT_SCHEMAS
SCHEMAS.update(CONTEXT_SCHEMAS)
