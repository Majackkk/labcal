TEXT = {'type': 'string', 'minLength': 1}
SCHEMAS = {'document-template.v1': {'type': 'object',
    'required': ['id','lab_id','role','title','source_id','source_hash','archive_path','inventory','user_statement','technical_approval'],
    'properties': {**{key: TEXT for key in ['id','lab_id','role','title','source_id','source_hash','archive_path','user_statement']},
                   'inventory': {'type': 'object'}, 'technical_approval': {'const': False}}, 'additionalProperties': False}}

TASK_FIELDS = ['id','lab_id','campaign_id','equipment_label','extraction_id','extraction_hash',
               'source_id','source_hash','template_id','template_hash','template_source_hash','created_at']
SCHEMAS['confirmation-task.v1'] = {'type': 'object', 'required': TASK_FIELDS + ['state','technical_approval'],
    'properties': {**{key: TEXT for key in TASK_FIELDS}, 'state': {'const': 'awaiting_evidence_and_form_filling'},
                   'technical_approval': {'const': False}}, 'additionalProperties': False}
SCHEMAS['confirmation-task.v1']['properties'].update({key: TEXT for key in
    ['equipment_key', 'context_hash', 'replaces_task', 'replacement_reason']})

# Five output columns are projections of one record, not editable limit sources.
ROW_FIELDS = ['parameter_results', 'method_judgment', 'uncertainty_evaluation',
              'traceability', 'application']
SCHEMAS['confirmation-record.v1'] = {
    'type': 'object', 'required': ['author', 'scope', 'evidence_references', 'rows'],
    'properties': {'author': TEXT, 'scope': TEXT,
        'evidence_references': {'type': 'array', 'minItems': 1, 'items': TEXT},
        'rows': {'type': 'array', 'minItems': 1, 'items': {'type': 'object',
            'required': ROW_FIELDS + ['function', 'purpose', 'state'],
            'properties': {**{key: TEXT for key in ROW_FIELDS + ['function', 'purpose']},
                'state': {'enum': ['meets_requirement', 'does_not_meet',
                                  'insufficient_evidence', 'not_assigned', 'reported_characteristic']}},
            'additionalProperties': False}}}, 'additionalProperties': False}

SCHEMAS['confirmation-task.v1']['properties']['coverage_scope'] = {
    'type': 'array', 'items': {'type': 'object',
    'required': ['source_id','source_hash','standard','relationship'],
    'properties': {key: TEXT for key in ['source_id','source_hash','standard','relationship']},
    'additionalProperties': False}}
COVERAGE_ITEM = {'type': 'object',
    'required': ['task','disposition','reason','row_numbers','evidence'],
    'properties': {'task': TEXT, 'reason': TEXT,
        'disposition': {'enum': ['assessed','other_instrument','not_applicable','insufficient_evidence']},
        'row_numbers': {'type': 'array', 'uniqueItems': True, 'items': {'type': 'integer','minimum':1}},
        'evidence': {'type': 'array','minItems':1,'items': {'type':'object',
            'required':['page','clause','quote','image_sha256'],
            'properties': {'page': {'type':'integer','minimum':1},'clause':TEXT,'quote':TEXT,'image_sha256':TEXT},
            'additionalProperties':False}}}, 'additionalProperties':False}
COVERAGE_ITEM['properties']['use_ids'] = {'type': 'array', 'uniqueItems': True, 'items': TEXT}
SCHEMAS['confirmation-record.v1']['properties']['coverage'] = {
    'type':'object','required':['claim','scope_note','methods'],
    'properties': {'claim': {'enum':['complete_for_declared_scope','scoped']},'scope_note':TEXT,
        'methods': {'type':'array','items': {'type':'object',
            'required':['source_id','search_note','items'],
            'properties': {'source_id':TEXT,'search_note':TEXT,
                'items': {'type':'array','minItems':1,'items':COVERAGE_ITEM}},
            'additionalProperties':False}}}, 'additionalProperties':False}

# Quotations point into the five existing output columns, not a second limit store.
REVIEW_QUOTES = {
    'instrument_comparison': 'parameter_results',
    'method_comparison': 'method_judgment',
    'decision_rule': 'method_judgment',
    'uncertainty_reasoning': 'uncertainty_evaluation',
    'use_conditions': 'application',
}
SELECTION_CHOICES = {
    'result': ['meets_declared_uses', 'limited_use', 'does_not_meet', 'undetermined'],
    'correction': ['not_required', 'required', 'mixed', 'undetermined'],
    'period': ['maintain', 'change', 'unspecified'],
}
SCHEMAS['confirmation-record.v1']['properties']['publication_review'] = {
    'type': 'object', 'required': ['rows', 'selections'],
    'properties': {
        'rows': {'type': 'array', 'minItems': 1, 'items': {
            'type': 'object', 'required': ['row_number', 'correction'] + list(REVIEW_QUOTES),
            'properties': {'row_number': {'type': 'integer', 'minimum': 1},
                'correction': {'enum': ['not_required', 'required', 'not_evaluated']},
                **{key: TEXT for key in REVIEW_QUOTES}}, 'additionalProperties': False}},
        'selections': {'type': 'object', 'required': list(SELECTION_CHOICES),
            'properties': {name: {
                'type': 'object', 'required': ['choice', 'basis', 'document_texts'],
                'properties': {'choice': {'enum': choices}, 'basis': TEXT,
                    'document_texts': {'type': 'array', 'minItems': 1, 'uniqueItems': True, 'items': TEXT}},
                'additionalProperties': False} for name, choices in SELECTION_CHOICES.items()},
            'additionalProperties': False}}, 'additionalProperties': False}

# Versioned publication checks: classifications and quotations, not another limit store.
SCHEMAS['confirmation-task.v1']['properties']['review_version'] = {'enum': [2]}
SCHEMAS['confirmation-record.v1']['properties']['technical_review'] = {
    'type': 'object', 'required': ['version', 'use_evidence', 'decision_evidence', 'rules'],
    'properties': {
        'version': {'const': 2}, 'use_evidence': TEXT, 'decision_evidence': TEXT,
        'rules': {'type': 'array', 'minItems': 1, 'items': {
            'type': 'object',
            'required': ['row_number', 'source_reference', 'requirement_kind', 'compared_quantity',
                         'decision_mode', 'decision_group', 'limit_basis', 'requirement_quote', 'comparison_quote', 'uncertainty_quote'],
            'properties': {
                'row_number': {'type': 'integer', 'minimum': 1},
                **{key: TEXT for key in ['source_reference', 'decision_group', 'requirement_quote', 'comparison_quote', 'uncertainty_quote']},
                'limit_basis': {'enum': ['method_requirement', 'instrument_specification', 'no_independent_limit']},
                'requirement_kind': {'enum': ['maximum_error', 'resolution', 'weighing_precision', 'repeatability_range', 'characteristic']},
                'compared_quantity': {'enum': ['indication_error', 'resolution', 'repeatability_range', 'repeatability_sd', 'position_difference', 'reported_result']},
                'decision_mode': {'enum': ['direct_comparison', 'guard_band', 'specification_check', 'report_only']},
                'interpretation_basis': TEXT,
                'configuration_evidence': TEXT},
            'additionalProperties': False}}}, 'additionalProperties': False}

# Small, opt-in publication revision. Existing archived records remain unchanged.
from .assessment import DIMENSION
from .self_diagnosis_contracts import LOCATION
SCHEMAS['confirmation-task.v1']['properties']['quality_version'] = {'const': 1}
SCHEMAS['confirmation-task.v1']['properties']['use_basis_version'] = {'const': 1}
SCHEMAS['confirmation-task.v1']['properties']['basis_role_version'] = {'const': 1}
technical = SCHEMAS['confirmation-record.v1']['properties']['technical_review']['properties']
technical['self_run_id'] = TEXT
technical['self_issue_dispositions'] = {'type': 'array', 'items': {'type': 'object',
    'required': ['check_id', 'row_numbers', 'reason'], 'properties': {
        'check_id': TEXT, 'reason': TEXT, 'row_numbers': {'type': 'array', 'minItems': 1,
            'uniqueItems': True, 'items': {'type': 'integer', 'minimum': 1}}}, 'additionalProperties': False}}
technical['certificate_items'] = {
    'type': 'array', 'minItems': 1, 'items': {'type': 'object',
    'required': ['label', 'evidence', 'row_numbers'],
    'properties': {'label': TEXT, 'evidence': LOCATION,
        'row_numbers': {'type': 'array', 'minItems': 1, 'uniqueItems': True,
                        'items': {'type': 'integer', 'minimum': 1}}}, 'additionalProperties': False}}
technical['rules']['items']['properties'].update({
    'dimension': {'enum': ['instrument_requirement', 'intended_use']},
    'state': SCHEMAS['confirmation-record.v1']['properties']['rows']['items']['properties']['state'],
    'judgment_quote': TEXT})
technical['rules']['items']['properties'].update({
    'basis_role': {'enum': ['acceptance_requirement', 'technical_reference', 'characteristic_record']},
    'basis_scope_quote': TEXT,
    'reference_impact_quote': TEXT,
    'purpose': TEXT,
    'acceptance_basis': {'enum': ['method_explicit', 'method_referenced_instrument', 'laboratory_established']},
    'applicability_basis': TEXT,
    'method_links': {'type': 'array', 'minItems': 1, 'uniqueItems': True,
        'items': {'type': 'object', 'required': ['source_id', 'task'],
            'properties': {'source_id': TEXT, 'task': TEXT}, 'additionalProperties': False}},
    'laboratory_criterion_evidence': TEXT,
    'requirement_interpretation': {'enum': ['division_or_resolution', 'recording_precision', 'measurement_accuracy', 'other_method_requirement']},
})

# References and reasoning stay inside the existing record; no second limit store.
SCHEMAS['confirmation-task.v1']['properties']['reasoning_version'] = {'const': 1}
SCHEMAS['confirmation-task.v1']['properties']['input_policy'] = {'const': 'originals_and_direct_user_evidence'}
technical['rules']['items']['properties']['reasoning'] = {
    'type': 'object',
    'required': ['requirement_status', 'interpretation_status', 'interpretation_evidence',
                 'quantity_match', 'quantity_basis', 'decision_evidence', 'uncertainty_role'],
    'properties': {
        'requirement_status': {'enum': ['established', 'unresolved', 'no_independent_limit']},
        'interpretation_status': {'enum': ['unambiguous', 'resolved', 'unresolved']},
        'interpretation_evidence': TEXT,
        'quantity_match': {'enum': ['direct', 'derived', 'not_comparable']},
        'quantity_basis': TEXT, 'decision_evidence': TEXT,
        'uncertainty_role': {'enum': ['separate_evaluation', 'decision_component', 'not_applicable']},
    }, 'additionalProperties': False}
technical['use_restrictions'] = {'type': 'array', 'minItems': 1, 'items': {
    'type': 'object', 'required': ['rule_numbers', 'scope', 'reason', 'application_quotes'],
    'properties': {
        'rule_numbers': {'type': 'array', 'minItems': 1, 'uniqueItems': True,
                         'items': {'type': 'integer', 'minimum': 1}},
        'scope': TEXT, 'reason': TEXT,
        'application_quotes': {'type': 'array', 'minItems': 1, 'uniqueItems': True, 'items': TEXT},
    }, 'additionalProperties': False}}

# Calculation evidence belongs to each existing rule; archived records stay valid.
SCHEMAS['confirmation-task.v1']['properties']['uncertainty_review_version'] = {'const': 1}
technical['rules']['items']['properties']['reasoning']['properties']['uncertainty_review'] = {
    'type': 'object',
    'required': ['route', 'application', 'basis_quote', 'calculation_quote', 'judgment_quote', 'outcome', 'points'],
    'properties': {
        'route': {'enum': ['jjf1094_general', 'verification_regulation', 'simple_acceptance', 'qualitative', 'other_documented_rule']},
        'application': {'enum': ['decision', 'supplementary']},
        **{key: TEXT for key in ['basis_quote', 'calculation_quote', 'judgment_quote']},
        'coverage_basis': {'enum': ['u95', 'k2_equivalent']},
        'outcome': SCHEMAS['confirmation-record.v1']['properties']['rows']['items']['properties']['state'],
        'points': {'type': 'array', 'items': {'type': 'object',
            'required': ['label', 'error', 'expanded_uncertainty', 'mpev', 'unit'],
            'properties': {'label': TEXT, 'error': TEXT, 'mpev': TEXT, 'unit': TEXT,
                'expanded_uncertainty': {'type': ['string', 'null']}},
            'additionalProperties': False}},
    }, 'additionalProperties': False}
