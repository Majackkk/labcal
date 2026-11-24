"""Workflow contracts are separate from technical acceptance facts."""
TEXT = {"type": "string", "minLength": 1}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
SCHEMAS = {
    "report-association.v1": {"type": "object", "required": ["schema_version", "lab_id", "campaign_id", "extraction_id", "extraction_hash", "recognizer", "catalog_hash", "match", "device", "technical_approval"],
        "properties": {"schema_version": {"const": "1.0"}, "lab_id": TEXT, "campaign_id": TEXT,
            "extraction_id": TEXT, "extraction_hash": HASH, "recognizer": TEXT, "catalog_hash": HASH,
            "match": {"type": "object", "required": ["state", "device_id", "reason", "candidates", "technical_approval"],
                "properties": {"state": {"enum": ["matched", "suggested", "ambiguous", "conflict", "unmatched", "operator_linked"]},
                    "device_id": {"type": ["string", "null"]}, "reason": TEXT, "candidates": {"type": "array"},
                    "identity_conflicts": {"type": "array"}, "technical_approval": {"const": False}}},
            "device": {"type": ["object", "null"]}, "operator": TEXT, "statement": TEXT,
            "previous_id": TEXT, "technical_approval": {"const": False}}, "additionalProperties": False},
    "report-association-choice.v1": {"type": "object", "required": ["expected_association_id", "device_id", "operator", "statement"],
        "properties": {"expected_association_id": TEXT, "device_id": TEXT, "operator": TEXT, "statement": TEXT}, "additionalProperties": False},
    "report-job.v1": {"type": "object", "required": ["id", "lab_id", "campaign_id", "filename", "state", "stage", "page", "pages", "source_id", "result", "error", "updated_at"],
        "properties": {"id": TEXT, "lab_id": TEXT, "campaign_id": TEXT, "filename": TEXT,
            "state": {"enum": ["queued", "running", "completed", "failed", "interrupted"]}, "stage": TEXT,
            "page": {"type": "integer", "minimum": 0}, "pages": {"type": "integer", "minimum": 0},
            "source_id": {"type": ["string", "null"]}, "result": {"type": ["object", "null"]},
            "error": {"type": ["object", "null"]}, "updated_at": TEXT}, "additionalProperties": False}
}

from .certificate_contracts import LOCATION
SIDE = {"type": "object", "required": ["text", "anchors"], "properties": {
    "text": {"type": "string"}, "anchors": {"type": "array", "items": {"type": "object",
        "required": ["extraction_id", "location"], "properties": {"extraction_id": TEXT, "location": LOCATION}, "additionalProperties": False}}}, "additionalProperties": False}
SCHEMAS["report-comparison.v1"] = {"type": "object", "required": ["schema_version", "lab_id", "campaign_id", "extraction_id", "extraction_hash", "basis", "review_id", "rows", "technical_approval"],
    "properties": {"schema_version": {"const": "1.0"}, "lab_id": TEXT, "campaign_id": TEXT,
        "extraction_id": TEXT, "extraction_hash": HASH, "review_id": {"type": ["string", "null"]},
        "basis": {"type": "object", "required": ["mode", "id", "hash", "label"], "properties": {
            "mode": {"enum": ["confirmed_baseline", "candidate_preview", "unmatched"]}, "id": {"type": "string"},
            "hash": {"type": "string"}, "label": TEXT}, "additionalProperties": False},
        "rows": {"type": "array", "items": {"type": "object", "required": ["id", "category", "title", "plan", "certificate", "status", "signal", "reason", "plan_anchor"],
            "properties": {"id": TEXT, "category": {"enum": ["identity", "parameters", "assurance"]}, "title": TEXT,
                "plan": SIDE, "certificate": SIDE, "status": {"enum": ["passed", "failed", "pending", "not_applicable"]},
                "signal": {"enum": ["matched", "possible", "conflict", "insufficient"]}, "reason": TEXT, "plan_anchor": {"type": "string"}}, "additionalProperties": False}},
        "technical_approval": {"const": False}, "projection_hash": HASH}, "additionalProperties": False}

STRING = {"type": "string"}
def string_object(names, extra=None):
    properties = {n: STRING for n in names.split()}; properties.update(extra or {})
    return {"type": "object", "required": list(properties), "properties": properties, "additionalProperties": False}
PLAN_POINT = string_object("id requirement_id parameter family component condition point unit mpe uncertainty source raw_conditions parse_state note delivery_route")
REPORT_POINT = string_object("id parameter family point unit condition measured error repeatability mpe repeatability_mpe uncertainty uncertainty_scope anchor_precision state", {
    "anchor": {"type": "object", "required": ["extraction_id", "location"], "properties": {"extraction_id": TEXT, "location": LOCATION}, "additionalProperties": False}})
SCHEMAS["point-matrix.v1"] = {"type": "object", "required": ["schema_version", "parser", "lab_id", "campaign_id", "basis", "snapshots", "rows", "observations", "warnings", "technical_approval"],
    "properties": {"schema_version": {"const": "1.0"}, "parser": TEXT, "lab_id": TEXT, "campaign_id": TEXT,
        "basis": SCHEMAS["report-comparison.v1"]["properties"]["basis"],
        "snapshots": {"type": "array", "items": string_object("id hash")},
        "rows": {"type": "array", "items": {"type": "object", "required": ["id", "plan", "certificate", "signal", "reason", "alerts", "status"],
            "properties": {"id": TEXT, "plan": {**PLAN_POINT, "type": ["object", "null"]},
                "certificate": {**REPORT_POINT, "type": ["object", "null"]},
                "signal": {"enum": ["paired", "condition_pending", "ambiguous", "plan_only", "report_only", "unresolved", "other_route"]},
                "reason": TEXT, "alerts": {"type":"array","items":TEXT}, "status": {"const": "pending"}}, "additionalProperties": False}},
        "observations": {"type": "array", "items": REPORT_POINT}, "warnings": {"type": "array", "items": TEXT},
        "technical_approval": {"const": False}, "projection_hash": HASH}, "additionalProperties": False}
SCHEMAS["workspace-role.v1"] = string_object("schema_version role reason", {})
SCHEMAS["workspace-role.v1"]["properties"].update(schema_version={"const":"1.0"}, role={"enum":["laboratory","sample_archive","migration_archive"]})

# v1 remains readable for historical exports; v2 separates matching from judgement.
import copy
SCHEMAS["point-matrix.v2"] = copy.deepcopy(SCHEMAS["point-matrix.v1"])
matrix2 = SCHEMAS["point-matrix.v2"]
matrix2["properties"]["schema_version"] = {"const": "2.0"}
matrix2["properties"]["observations"]["items"]["properties"]["evidence_observation_id"] = TEXT
row2 = matrix2["properties"]["rows"]["items"]
row2["properties"]["certificate"]["properties"]["evidence_observation_id"] = TEXT
row2["properties"]["signal"]["enum"].append("uncompared")
row2["properties"].update(status={"enum": ["passed", "failed", "pending", "not_applicable"]},
    decision_state={"enum": ["concluded", "not_started", "needs_review", "missing_input", "unsupported", "not_applicable"]},
    decision_reason=TEXT, decision_evidence={"type": "array", "items": {"type": "object"}})
row2["required"] += ["decision_state", "decision_reason", "decision_evidence"]
SCHEMAS["report-field-correction.v1"] = {"type": "object",
    "required": ["field", "value", "reason", "location", "expected_fields_hash"],
    "properties": {"field": TEXT, "value": {"type": "string", "maxLength": 4000}, "reason": TEXT,
        "location": LOCATION, "expected_fields_hash": HASH}, "additionalProperties": False}
SCHEMAS["report-diagnosis.v1"] = {"type": "object",
    "required": ["schema_version", "engine", "lab_id", "campaign_id", "review_id", "extraction_id", "fields", "fields_hash",
        "input_hash", "report", "association", "diagnostics", "point_matrix", "comparison_report", "preconditions", "summary", "technical_approval"],
    "properties": {"schema_version": {"const": "1.0"}, "engine": TEXT, "lab_id": TEXT, "campaign_id": TEXT,
        "review_id": TEXT, "extraction_id": TEXT, "fields": {"type": "object"}, "fields_hash": HASH, "input_hash": HASH,
        "report": {"type":"object","required":["file_name","certificate_number","document_kind"],"properties":{"file_name":TEXT,"certificate_number":{"type":"string"},"document_kind":TEXT},"additionalProperties":False},
        "association": {"type": ["object", "null"]}, "diagnostics": {"type": "array", "items": {"type": "object"}},
        "point_matrix": matrix2, "comparison_report": SCHEMAS["report-comparison.v1"],
        "preconditions": {"type": "array", "items": {"type": "object"}}, "summary": {"type": "object"},
        "technical_approval": {"const": False}}, "additionalProperties": False}
