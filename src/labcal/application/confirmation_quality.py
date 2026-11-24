"""Strengthen existing publication checks; no new workflow or editable limits."""
import json

from ..adapters.layout import output_directory
from ..errors import LabcalError
# Compatibility imports; the domain module is the only implementation.
from ..domain.confirmation_basis import validate_method_basis, validate_basis_roles
from ..util import read_json, file_hash


def load_self_review(workspace, task, run_id):
    from .self_diagnosis import load_task
    from .laboratory_summary import validate_export
    store = workspace.lab_store(task['lab_id'])
    with store.connect() as db:
        run = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
    if not run or run['kind'] != 'certificate_self_diagnosis':
        raise LabcalError('CONFIRMATION_SELF_REQUIRED', '确认表须引用已完成的证书自身诊断。')
    manifest = json.loads(run['manifest'])
    if manifest['lab_id'] != task['lab_id'] or manifest['campaign_id'] != task['campaign_id']:
        raise LabcalError('CONFIRMATION_SELF_SCOPE', '自身诊断不属于本实验室及批次。')
    folder = output_directory(store, run_id, run['kind'], existing=True)
    validate_export(folder, manifest)
    result = read_json(folder/'diagnosis.json')
    self_folder, fixed = load_task(workspace, task['lab_id'], task['campaign_id'], manifest['task_id'])
    if result['task_hash'] != fixed['payload_hash'] or manifest['task_hash'] != fixed['payload_hash']:
        raise LabcalError('CONFIRMATION_SELF_SCOPE', '自身诊断的固定输入不一致。')
    doc = next((d for d in result['task']['documents'] if d['extraction_id'] == task['extraction_id']), None)
    if not doc or doc['extraction_hash'] != task['extraction_hash'] or doc['source_hash'] != task['source_hash']:
        raise LabcalError('CONFIRMATION_SELF_SCOPE', '自身诊断未覆盖本次固定证书版本。')
    return self_folder, [c for c in result['checks'] if c['extraction_id'] == task['extraction_id']]


def validate_quality(workspace, task, record, extraction):
    """Check inventory references and layer consistency, not semantic completeness."""
    from .self_diagnosis import validate_page_evidence
    review = record.get('technical_review', {})
    if not review.get('self_run_id') or not review.get('certificate_items'):
        raise LabcalError('CONFIRMATION_QUALITY_REQUIRED', '请关联自身诊断，并逐项列出证书现有项目到附页的对应关系。')
    folder, checks = load_self_review(workspace, task, review['self_run_id'])
    mapped = set(); seen = set()
    for item in review['certificate_items']:
        signature = (item['label'], item['evidence']['page'], tuple(item['evidence']['bbox']))
        if signature in seen:
            raise LabcalError('CONFIRMATION_ITEM_DUPLICATE', '证书项目重复登记，不能用重复条目替代遗漏项目。')
        seen.add(signature)
        if any(n < 1 or n > len(record['rows']) for n in item['row_numbers']):
            raise LabcalError('CONFIRMATION_ITEM_ROWS', '证书项目引用了不存在的附页行。')
        mapped.update(item['row_numbers'])
        validate_page_evidence(extraction, folder, task['extraction_id'], [item['evidence']])
    if mapped != set(range(1, len(record['rows'])+1)):
        raise LabcalError('CONFIRMATION_ITEM_ROWS', '存在没有证书原页项目支持的附页行。')
    for rule in review['rules']:
        if not all(k in rule for k in ('dimension', 'state', 'judgment_quote')):
            raise LabcalError('CONFIRMATION_LAYER_REQUIRED', '每条判断须明确器具要求或检测用途维度、状态及正式结论摘录。')
        row = record['rows'][rule['row_number']-1]
        if rule['judgment_quote'] not in row['method_judgment'] or not rule['judgment_quote'].strip():
            raise LabcalError('CONFIRMATION_LAYER_QUOTE', '分层结论必须引用正式表中的实际判断。')
        if rule['limit_basis'] == 'instrument_specification' and rule['dimension'] != 'instrument_requirement':
            raise LabcalError('CONFIRMATION_LAYER_BASIS', '器具规格比较应归入器具技术要求。')
        if rule['limit_basis'] == 'method_requirement' and rule['dimension'] != 'intended_use':
            raise LabcalError('CONFIRMATION_LAYER_BASIS', '检测方法要求应归入检测用途。')
        if rule['decision_mode'] == 'report_only' and rule['state'] == 'meets_requirement':
            raise LabcalError('CONFIRMATION_LAYER_STATE', '中性特性记录不能标为符合。')
        if rule['dimension'] == 'intended_use' and row['state'] == 'meets_requirement' and rule['state'] not in {'meets_requirement', 'reported_characteristic', 'not_assigned'}:
            raise LabcalError('CONFIRMATION_LAYER_STATE', '用途分项存在不满足或证据不足，不能将该用途行写为满足。')
    if record['publication_review']['selections']['result']['choice'] == 'meets_declared_uses':
        unresolved = [c for c in checks if c['group'] != 'filing' and c['state'] not in {'no_anomaly_detected', 'not_applicable'}]
        dispositions = review.get('self_issue_dispositions', [])
        for disposition in dispositions:
            check = next((c for c in checks if c['id'] == disposition['check_id']), None)
            numbers = disposition['row_numbers']
            if (not check or check['state'] != 'unable_to_check' or not disposition['reason'].strip()
                    or not numbers or any(n < 1 or n > len(record['rows']) or record['rows'][n-1]['state'] not in {'reported_characteristic', 'not_assigned'} for n in numbers)):
                raise LabcalError('CONFIRMATION_SELF_DISPOSITION', '只有不参与用途合格判定的中性特性原始记录复核，才能明确范围后另列处理。')
        excluded = {d['check_id'] for d in dispositions}
        if any(c['id'] not in excluded for c in unresolved):
            raise LabcalError('CONFIRMATION_SELF_UNRESOLVED', '证书自身仍有错误、疑点或未检查项，不能选择全部声明用途满足。')
        for n, row in enumerate(record['rows'], 1):
            if row['state'] == 'meets_requirement' and not any(r['row_number'] == n and r['dimension'] == 'intended_use' and r['state'] == 'meets_requirement' for r in review['rules']):
                raise LabcalError('CONFIRMATION_LAYER_STATE', '器具要求符合不能代替检测用途符合。')
    return checks
