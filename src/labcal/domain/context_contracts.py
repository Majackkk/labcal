"""Small laboratory context notes; method limits remain in method packages."""
TEXT = {'type': 'string', 'minLength': 1}
SCHEMAS = {'context-note.v1': {
    'type': 'object',
    'required': ['kind', 'key', 'equipment_key', 'scope', 'statement', 'state',
                 'evidence_reference'],
    'properties': {
        'kind': {'enum': ['decision_rule', 'communication_workbook']},
        **{key: TEXT for key in ['key', 'equipment_key', 'scope', 'statement',
                                'evidence_reference', 'supersedes']},
        'state': {'enum': ['candidate', 'recorded']},
        'source_id': TEXT, 'page': {'type': 'integer', 'minimum': 1}},
    'additionalProperties': False}}

SCHEMAS['confirmation-followup.v1'] = {
    'type': 'object', 'required': ['issue_id', 'category', 'state', 'statement', 'evidence_reference'],
    'properties': {
        **{key: TEXT for key in ['issue_id', 'statement', 'evidence_reference', 'supersedes', 'resolution_task']},
        'category': {'enum': ['correction', 'clarification', 'supplementary_calibration', 'use_condition']},
        'state': {'enum': ['open', 'response_received', 'addressed']},
        'response_source_ids': {'type': 'array', 'uniqueItems': True, 'items': TEXT}},
    'additionalProperties': False}
