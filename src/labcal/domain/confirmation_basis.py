"""Pure checks for method applicability and acceptance versus reference roles.

No source loading, database access, document output or invented limits.
"""
from ..errors import LabcalError


def validate_method_basis(task, record):
    """Link each use judgement to declared methods, not a default grade MPE.

    This checks provenance and declared interpretation. The executing reviewer
    must still read the clause and assess actual-use validity.
    """
    methods = {m['source_id']: m for m in record['coverage']['methods']}
    scope = {s['source_id'] for s in task['coverage_scope']}
    for rule in record['technical_review']['rules']:
        if rule['dimension'] != 'intended_use':
            continue
        if not all(rule.get(k) for k in ['purpose', 'acceptance_basis', 'applicability_basis', 'method_links', 'requirement_interpretation']):
            raise LabcalError('CONFIRMATION_METHOD_BASIS_REQUIRED', '用途判断须明确检测方法关联、条款解释、适用依据及用途名称。')
        row = record['rows'][rule['row_number']-1]
        if rule['purpose'] not in row['method_judgment']:
            raise LabcalError('CONFIRMATION_METHOD_PURPOSE', '方法列应显示每项被评价的具体用途。')
        for link in rule['method_links']:
            method = methods.get(link['source_id'])
            item = next((i for i in method['items'] if i['task'] == link['task']), None) if method else None
            if (link['source_id'] not in scope or not item or rule['row_number'] not in item['row_numbers']
                    or item['disposition'] not in {'assessed', 'insufficient_evidence'}):
                raise LabcalError('CONFIRMATION_METHOD_LINK', '用途判断必须关联固定方法范围内有原页依据、且对应本附页行的测量任务。')
            if rule['state'] == 'meets_requirement' and item['disposition'] != 'assessed':
                raise LabcalError('CONFIRMATION_METHOD_LINK', '方法任务证据不足不能支持用途符合。')
        basis = rule['acceptance_basis']
        if basis in {'method_explicit', 'method_referenced_instrument'} and rule['limit_basis'] != 'method_requirement':
            raise LabcalError('CONFIRMATION_METHOD_PRIORITY', '器具规格不能直接代替方法用途验收要求。')
        if basis == 'laboratory_established' and not rule.get('laboratory_criterion_evidence', '').strip():
            raise LabcalError('CONFIRMATION_LAB_CRITERION_REQUIRED', '方法未明确的验收要求须有实验室实际建立依据，不能默认套器具等级限值。')
        interpretation = rule['requirement_interpretation']
        if interpretation == 'division_or_resolution' and (rule['requirement_kind'] != 'resolution' or rule['compared_quantity'] != 'resolution'):
            raise LabcalError('CONFIRMATION_METHOD_INTERPRETATION', '分度/分辨力要求应与分度/分辨力比较。')
        if interpretation == 'measurement_accuracy' and rule['compared_quantity'] == 'resolution' and rule['state'] == 'meets_requirement':
            raise LabcalError('CONFIRMATION_METHOD_INTERPRETATION', '仅分度符合不足以证明测量准确度符合。')
        if rule['requirement_kind'] == 'weighing_precision' and not rule.get('interpretation_basis', '').strip():
            raise LabcalError('CONFIRMATION_METHOD_INTERPRETATION', '精确到的含义必须由原文及上下文支持。')


def validate_basis_roles(record, *, required=False):
    """Keep binding acceptance criteria separate from technical comparisons.

    The reviewer establishes applicability; this gate verifies declarations and
    visible explanations, without inventing limits or judging prose semantics.
    """
    all_uses = record['publication_review']['selections']['result']['choice'] == 'meets_declared_uses'
    for rule in record['technical_review']['rules']:
        if not required and 'basis_role' not in rule:
            continue
        role = rule.get('basis_role')
        row = record['rows'][rule['row_number']-1]
        visible = '\n'.join(row[k] for k in ('method_judgment', 'uncertainty_evaluation', 'application'))
        quote = rule.get('basis_scope_quote', '')
        if role not in {'acceptance_requirement', 'technical_reference', 'characteristic_record'} or not quote.strip() or quote not in visible:
            raise LabcalError('CONFIRMATION_BASIS_ROLE', '每条规则须区分适用验收要求、技术参考或特性记录，并在正式表说明其作用。')
        if role == 'technical_reference':
            if rule['dimension'] != 'instrument_requirement' or rule['limit_basis'] != 'instrument_specification':
                raise LabcalError('CONFIRMATION_REFERENCE_AS_USE', '器具技术参考不能标为检测用途验收要求。')
            if rule['state'] in {'does_not_meet', 'insufficient_evidence'}:
                impact = rule.get('reference_impact_quote', '')
                if not impact.strip() or impact not in visible:
                    raise LabcalError('CONFIRMATION_REFERENCE_IMPACT', '参考指标未满足或证据不足，须在正式表记录对实际用途的影响，不能忽略或自动限用。')
        elif role == 'characteristic_record':
            if rule['decision_mode'] != 'report_only' or rule['state'] not in {'reported_characteristic', 'not_assigned'}:
                raise LabcalError('CONFIRMATION_CHARACTERISTIC_ROLE', '特性记录不能声明独立验收合格或不合格。')
        else:
            if rule['limit_basis'] == 'no_independent_limit':
                raise LabcalError('CONFIRMATION_ACCEPTANCE_BASIS', '适用验收要求须有实际判据。')
            if rule['dimension'] == 'instrument_requirement' and not rule.get('applicability_basis', '').strip():
                raise LabcalError('CONFIRMATION_ACCEPTANCE_BASIS', '器具指标作为必须满足的验收要求，须记录其法定、明确引用或实验室采用依据。')
            if all_uses and rule['state'] in {'does_not_meet', 'insufficient_evidence'}:
                raise LabcalError('CONFIRMATION_ACCEPTANCE_UNMET', '适用的验收要求未满足或证据不足，不能选择全部声明用途满足。')
        if rule['dimension'] == 'intended_use' and rule['decision_mode'] != 'report_only' and role != 'acceptance_requirement':
            raise LabcalError('CONFIRMATION_REFERENCE_AS_USE', '用途符合须由适用验收要求支持，不能由器具参考代替。')
