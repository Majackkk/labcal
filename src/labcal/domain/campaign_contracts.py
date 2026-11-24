"""Organisation contracts; technical snapshots keep their existing identity."""
import copy
TEXT = {"type": "string", "minLength": 1}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
SCHEMAS = {
    "intelligence-packet.v1": {"type":"object", "required":["schema_version","lab_id","campaign_id","campaign_hash","provider","baseline","certificates","instructions","response_schema"],
        "properties":{"schema_version":{"const":"1.0"},"lab_id":{"type":"string","minLength":1},"campaign_id":{"type":"string","minLength":1},
            "campaign_hash":{"type":"string"},"provider":{"const":"project_agent"},"baseline":{"type":["object","null"]},
            "certificates":{"type":"array","minItems":1},"instructions":{"type":"array","items":{"type":"string"}},"response_schema":{"type":"object"},
            "candidate_plan_context":{"type":"object","required":["catalog_hash","devices","technical_approval"],"properties":{"catalog_hash":{"type":"string"},"devices":{"type":"array"},"technical_approval":{"const":False}},"additionalProperties":False}},"additionalProperties":False},
    "laboratory-settings.v1": {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "required": ["operator", "user_statement", "expected_hash"],
        "properties": {"operator": TEXT, "user_statement": TEXT, "expected_hash": HASH,
                       "name": TEXT, "laboratory_type": TEXT, "profile": TEXT}, "additionalProperties": False,
    },
    "campaign.v1": {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "required": ["schema_version", "lab_id", "id", "name", "year", "state", "created_at"],
        "properties": {"schema_version": {"const": "1.0"}, "lab_id": TEXT,
            "id": {"type": "string", "pattern": "^CAM-[A-Za-z0-9_-]{1,64}$"}, "name": TEXT,
            "year": {"type": ["integer", "null"], "minimum": 1900},
            "state": {"enum": ["active", "archived"]}, "notes": {"type": "string"}, "created_at": TEXT},
        "additionalProperties": False,
    },
    "campaign-link.v1": {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "required": ["kind", "entity_id", "revision", "entity_hash"],
        "properties": {"kind": {"enum": ["source", "workbook", "plan", "baseline", "extraction", "review", "intelligence"]},
            "entity_id": TEXT, "revision": {"type": "integer", "minimum": 0}, "entity_hash": HASH},
        "additionalProperties": False,
    },
    "workbook-extraction.v1": {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "required": ["schema_version", "lab_id", "source_id", "source_hash", "parser", "sheets", "candidates", "mapping", "technical_review"],
        "properties": {"schema_version": {"const": "1.0"}, "lab_id": TEXT, "source_id": TEXT, "source_hash": HASH,
            "parser": TEXT, "sheets": {"type": "array", "items": {"type": "object"}},
            "candidates": {"type": "array", "items": {"type": "object"}}, "mapping": {"type": "object"},
            "technical_review": {"const": "pending"}}, "additionalProperties": False,
    },
    "intelligence-result.v1": {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "required": ["packet_id", "expected_packet_hash", "author", "records"],
        "properties": {"packet_id": TEXT, "expected_packet_hash": HASH, "author": TEXT,
            "records": {"type": "array", "items": {"type": "object"}, "minItems": 1}},
        "additionalProperties": False,
    },
}

WORKBOOK_FIELDS = ["equipment_name", "manufacturer", "model", "equipment_number", "parameter", "mpe", "uncertainty"]
SCHEMAS["workbook-extraction.v2"] = copy.deepcopy(SCHEMAS["workbook-extraction.v1"])
SCHEMAS["workbook-extraction.v2"]["properties"]["schema_version"] = {"const": "2.0"}
SCHEMAS["workbook-extraction.v2"]["properties"]["candidates"]["items"] = {
    "type": "object", "required": ["id", "sheet", "row", "group", "parameter", "values", "anchor", "formula_cells", "state", "fields", "field_sources"],
    "properties": {"id": TEXT, "sheet": TEXT, "row": {"type": "integer", "minimum": 1}, "group": TEXT,
        "parameter": TEXT, "values": {"type": "object"}, "anchor": TEXT,
        "formula_cells": {"type": "array", "items": TEXT}, "state": {"const": "candidate"},
        "fields": {"type": "object", "required": WORKBOOK_FIELDS,
            "properties": {key: {"type": ["string", "null"]} for key in WORKBOOK_FIELDS}, "additionalProperties": False},
        "field_sources": {"type": "object", "properties": {key: {"type": "object", "required": ["sheet", "cell", "origin"],
            "properties": {"sheet": TEXT, "cell": TEXT, "origin": {"enum": ["column", "label", "section", "inline"]}},
            "additionalProperties": False} for key in WORKBOOK_FIELDS}, "additionalProperties": False}},
    "additionalProperties": False,
}

from .certificate_contracts import SCHEMAS as CERTIFICATE_SCHEMAS, OBSERVATION
SCHEMAS["intelligence-result.v1"]["properties"]["records"]["items"] = {
    "type": "object", "required": ["extraction_id", "expected_extraction_hash", "findings", "fields", "observations"],
    "properties": {"extraction_id": TEXT, "expected_extraction_hash": HASH,
        "findings": {"type": "array", "items": CERTIFICATE_SCHEMAS["certificate-findings.v1"]["properties"]["findings"]["items"]},
        "fields": CERTIFICATE_SCHEMAS["certificate-evidence.v1"]["properties"]["fields"],
        "observations": {"type": "array", "items": OBSERVATION}}, "additionalProperties": False,
}
