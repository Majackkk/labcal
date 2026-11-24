"""Method facts, laboratory adoption and observations have separate contracts."""
from .self_diagnosis_contracts import LOCATION

TEXT = {'type': 'string', 'minLength': 1}
STR = {'type': 'string'}
VALUE = {'type': 'object', 'required': ['value', 'unit'], 'properties': {
    'value': TEXT, 'unit': TEXT}, 'additionalProperties': False}
CRITERION = {'type': 'object', 'required': ['operator', 'unit', 'relative_basis'], 'properties': {
    'operator': {'enum': ['abs_le', 'le', 'ge', 'eq', 'none']}, 'unit': TEXT,
    'relative_basis': TEXT, 'value': TEXT, 'expression': TEXT,
    'inputs': {'type': 'object'}, 'constants': {'type': 'object'}}, 'additionalProperties': False}
EVIDENCE = {'type': 'object', 'required': ['page', 'quote', 'clause'], 'properties': {
    'page': {'type': 'integer', 'minimum': 1}, 'quote': TEXT, 'clause': TEXT,
    'image_sha256': TEXT}, 'additionalProperties': False}
REQUIREMENT = {'type': 'object', 'required': ['id', 'equipment_category', 'parameter',
    'requirement_type', 'origin', 'statement', 'range_text', 'condition', 'state', 'evidence', 'criterion'],
    'properties': {'id': TEXT, 'equipment_category': TEXT, 'parameter': TEXT,
    'requirement_type': {'enum': ['mpe', 'accuracy', 'resolution', 'repeatability', 'class',
        'uncertainty', 'range', 'rounding', 'other']},
    'origin': {'enum': ['explicit', 'derived', 'recommendation']}, 'statement': TEXT,
    'range_text': STR, 'condition': STR, 'state': {'enum': ['candidate', 'page_checked', 'ambiguous']},
    'evidence': {'type': 'array', 'items': EVIDENCE}, 'criterion': CRITERION,
    'dependencies': {'type': 'array', 'items': TEXT}}, 'additionalProperties': False}

SCHEMAS = {
 'method-adoption.v1': {'type': 'object', 'required': ['source_id', 'standard_code', 'edition', 'user_statement', 'evidence_reference'],
    'properties': {'source_id': TEXT, 'standard_code': TEXT, 'edition': TEXT, 'user_statement': TEXT,
        'evidence_reference': TEXT, 'supersedes': TEXT}, 'additionalProperties': False},
 'method-package.v1': {'type': 'object', 'required': ['standard_code', 'edition', 'title',
    'source_id', 'author', 'coverage', 'requirements', 'gaps'], 'properties': {
    'standard_code': TEXT, 'edition': TEXT, 'title': TEXT, 'source_id': TEXT, 'author': TEXT,
    'coverage': TEXT, 'requirements': {'type': 'array', 'minItems': 1, 'items': REQUIREMENT},
    'gaps': {'type': 'array', 'items': TEXT}}, 'additionalProperties': False},
 'method-use.v1': {'type': 'object', 'required': ['equipment_key', 'equipment_name', 'method_id',
    'purpose', 'use_range', 'conditions', 'state', 'user_statement', 'evidence_reference', 'requirement_ids'], 'properties': {
    'equipment_key': TEXT, 'equipment_name': TEXT, 'equipment_id': TEXT, 'method_id': TEXT, 'adoption_id': TEXT,
    'purpose': TEXT, 'use_range': TEXT, 'conditions': {'type': 'object'},
    'requirement_ids': {'type': 'array', 'minItems': 1, 'uniqueItems': True, 'items': TEXT},
    'state': {'enum': ['candidate', 'user_adopted']}, 'user_statement': STR,
    'evidence_reference': STR, 'supersedes': TEXT}, 'additionalProperties': False},
 'method-diagnosis-record.v1': {'type': 'object', 'required': ['task_hash', 'author', 'observations'],
    'properties': {'task_hash': TEXT, 'author': TEXT, 'self_run_id': TEXT,
    'communications': {'type': 'array', 'items': {'type': 'object', 'required': ['use_id', 'extraction_id', 'items'],
        'properties': {'use_id': TEXT, 'extraction_id': TEXT, 'items': {'type': 'array', 'minItems': 1, 'items': TEXT},
        'uncertainty_request': {'type': 'object', 'required': ['value', 'unit', 'k', 'scope_source_id', 'scope_page',
            'scope_quote', 'scope_applicability', 'cmc', 'scope_k'], 'properties': {
            'value': TEXT, 'unit': TEXT, 'k': TEXT, 'scope_source_id': TEXT,
            'scope_page': {'type': 'integer', 'minimum': 1}, 'scope_quote': TEXT, 'scope_applicability': TEXT,
            'cmc': VALUE, 'scope_k': TEXT}, 'additionalProperties': False}}, 'additionalProperties': False}},
    'observations': {'type': 'array', 'items': {'type': 'object', 'required': ['use_id',
    'requirement_id', 'extraction_id', 'point', 'condition', 'match', 'scope', 'reason',
    'evidence', 'rule', 'rule_basis'], 'properties': {
    'use_id': TEXT, 'requirement_id': TEXT, 'extraction_id': TEXT, 'point': STR,
    'condition': STR, 'match': {'enum': ['matched', 'ambiguous', 'missing']},
    'scope': TEXT, 'reason': TEXT, 'evidence': {'type': 'array', 'items': LOCATION},
    'value': VALUE, 'uncertainty': VALUE, 'relative_basis': TEXT,
    'assessment': {'type': 'object', 'required': ['origin','state','reason','basis'], 'properties': {
        'origin': {'const':'agent_page_assessment'},
        'state': {'enum':['meets_requirement','does_not_meet','insufficient_evidence']},
        'reason': TEXT, 'basis': TEXT}, 'additionalProperties': False},
    'rule': {'enum': ['direct', 'guard_band', 'none']}, 'rule_basis': TEXT},
    'additionalProperties': False}}}, 'additionalProperties': False}
}

# Optional measurement assignment on the existing laboratory-owned use record.
# Related keys are explicit identities, never inferred from similar names.
SCHEMAS['method-use.v1']['properties']['measurement'] = {
    'type': 'object', 'required': ['role', 'quantity'], 'properties': {
        **{key: TEXT for key in ['role', 'quantity', 'component', 'channel', 'direction', 'range_text']},
        'related_equipment_keys': {'type': 'array', 'uniqueItems': True, 'items': TEXT}},
    'additionalProperties': False}
