"""Evidence contracts for certificate review; machine extraction grants no approval."""
from .report_markup import MARKUP
TEXT = {"type": "string", "minLength": 1}
STRING = {"type": "string"}
LOCATION = {
    "type": "object", "required": ["page", "bbox"],
    "properties": {"page": {"type": "integer", "minimum": 1},
                   "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                   "line_ids": {"type": "array", "items": TEXT}, "quote": STRING,
                   "kind": {"enum": ["text", "visual", "section", "document"]}, "markup": MARKUP},
    "additionalProperties": False,
}
DATUM = {"type": "object", "required": ["value", "location"],
         "properties": {"value": {"type": ["string", "boolean"]}, "location": LOCATION,
                        "note": STRING}, "additionalProperties": False}
OBSERVATION = {
    "type": "object", "required": ["id", "parameter", "unit", "location", "inputs"],
    "properties": {"id": TEXT, "parameter": TEXT, "unit": TEXT, "location": LOCATION,
                   "requirement_id": STRING, "component": STRING, "channel": STRING, "mode": STRING,
                   "point": STRING, "range": {"type": "object"}, "conditions": {"type": "object"},
                   "inputs": {"type": "object"}, "reported_fields": {"type": "array", "items": TEXT}},
    "additionalProperties": False,
}
HEADERS = ["certificate_number", "issuer", "client", "instrument_name", "model", "instrument_number",
           "activity_date", "issue_date", "method", "environment", "traceability", "results",
           "uncertainty", "approval", "verification_conclusion", "verification_valid_until"]

SCHEMAS = {
    "certificate-findings.v1": {
        "type": "object", "required": ["extraction_id", "expected_extraction_hash", "origin", "author", "findings"],
        "properties": {"extraction_id": TEXT, "expected_extraction_hash": TEXT,
                       "origin": {"enum": ["agent_candidate", "operator_candidate"]}, "author": TEXT,
                       "findings": {"type": "array", "minItems": 1, "items": {
                           "type": "object", "required": ["code", "title", "message", "basis", "suggestion", "location"],
                           "properties": {"code": TEXT, "title": TEXT, "message": TEXT, "basis": TEXT,
                                          "suggestion": TEXT, "location": LOCATION,
                                          "severity": {"enum": ["low", "medium", "high"]},
                                          "basis_sources": {"type": "array", "items": {
                                              "type": "object", "required": ["source_id", "source_hash", "anchor"],
                                              "properties": {"source_id": TEXT, "source_hash": TEXT, "anchor": TEXT}, "additionalProperties": False}}},
                           "additionalProperties": False}}}, "additionalProperties": False,
    },
    "certificate-extraction.v1": {
        "type": "object", "required": ["schema_version", "source_id", "source_hash", "document_kind", "pages", "fields", "parser_version"],
        "properties": {"schema_version": {"const": "1.0"}, "source_id": TEXT, "source_hash": TEXT,
                       "document_kind": {"enum": ["calibration", "verification", "unknown"]},
                       "pages": {"type": "array", "items": {"type": "object", "properties": {
                           "engine": {"enum": ["pdf_text", "tesseract", "paddleocr-vl-1.6", "pdf_text+tesseract_header"]}}}, "minItems": 1},
                       "fields": {"type": "object"}, "parser_version": TEXT, "warnings": {"type": "array"},
                       "digital_signatures_present": {"type": "boolean"}}, "additionalProperties": False,
    },
    "certificate-evidence.v1": {
        "type": "object", "required": ["extraction_id", "expected_extraction_hash", "source_hash", "reviewer", "reviewed_at", "fields", "observations"],
        "properties": {"extraction_id": TEXT, "expected_extraction_hash": TEXT, "source_hash": TEXT,
                       "reviewer": TEXT, "reviewed_at": TEXT, "fields": {"type": "object", "properties": {x: DATUM for x in HEADERS}, "additionalProperties": False},
                       "observations": {"type": "array", "items": OBSERVATION},
                       "observations_complete": {"type": "boolean"},
                       "equipment_id": TEXT, "equipment_config_hash": TEXT, "identity_checked": {"type": "boolean"},
                       "document_kind": {"enum": ["calibration", "verification"]}, "kind_location": LOCATION,
                       "absent_fields": {"type": "array", "items": {"enum": HEADERS}, "uniqueItems": True},
                       "pages_checked": {"type": "array", "items": {"type": "integer", "minimum": 1}, "uniqueItems": True},
                       "note": STRING}, "additionalProperties": False,
    },
    "institution-evidence.v1": {
        "type": "object", "required": ["source_id", "source_hash", "institution", "scheme", "valid_from", "valid_until", "parameters", "reviewer", "reviewed_at", "anchor"],
        "properties": {"source_id": TEXT, "source_hash": TEXT, "institution": TEXT,
                       "scheme": {"enum": ["cnas", "legal_verification", "nmi", "other"]},
                       "valid_from": TEXT, "valid_until": TEXT, "authority_url": STRING,
                       "scope_complete": {"type": "boolean"},
                       "anchor": TEXT, "parameters": {"type": "array", "items": {"type": "object"}, "minItems": 1},
                       "reviewer": TEXT, "reviewed_at": TEXT, "note": STRING}, "additionalProperties": False,
    },
    "finding-resolution.v1": {
        "type": "object", "required": ["review_id", "expected_review_hash", "finding_id", "action", "reviewer", "reviewed_at", "reason"],
        "properties": {"review_id": TEXT, "expected_review_hash": TEXT, "finding_id": TEXT,
                       "action": {"enum": ["confirmed", "dismissed", "resolved"]}, "reviewer": TEXT,
                       "reviewed_at": TEXT, "reason": TEXT, "evidence_locations": {"type": "array", "items": LOCATION}},
        "additionalProperties": False,
    },
    "acceptance.v1": {
        "title": "Certificate review request", "type": "object", "required": ["lab_id", "certificate_source_id"],
        "properties": {"lab_id": TEXT, "certificate_source_id": TEXT, "baseline_id": TEXT,
                       "locations": {"type": "array", "items": LOCATION}}, "additionalProperties": False,
    },
}
for schema in SCHEMAS.values():
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
