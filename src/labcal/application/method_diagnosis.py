"""Chat orchestration: self diagnosis first, then independent method comparison."""
import json
from pathlib import Path

from ..adapters.layout import output_directory, processing_directory, segment
from ..adapters.pdf_process import annotate_pdf_isolated
from ..adapters.pdf_callouts import RENDERER_VERSION
from ..domain.contracts import validate_contract
from ..domain.method_requirements import compare, decimal, ENGINE
from ..domain.rules import convert
from ..errors import LabcalError
from ..util import canonical_json, content_hash, file_hash, identifier, read_json, utc_now, write_json
from . import self_diagnosis
from .campaigns import get_campaign
from .certificates import get_extraction
from .methods import get_method, get_use, list_uses, list_adoptions, store_for
from .sources import verify_source_integrity

LABELS = {'meets_requirement': '满足要求', 'does_not_meet': '不满足要求',
          'insufficient_evidence': '证据不足／待核实', 'not_checked': '未检查'}


def prepare(workspace, lab_id, campaign_id, extraction_ids, use_ids):
    get_campaign(workspace, lab_id, campaign_id, writable=True)
    if not use_ids or len(set(use_ids)) != len(use_ids):
        raise LabcalError('METHOD_USE_REQUIRED', '请选择不重复的方法用途关联。')
    if len(extraction_ids) != 1:
        raise LabcalError('METHOD_ONE_DEVICE_PER_TASK', '每个用途对照任务固定一份报告；多份报告分别创建任务后汇总，避免设备交叉对应。')
    uses = [get_use(workspace, lab_id, key) for key in use_ids]
    adopted = {a['id'] for a in list_adoptions(workspace, lab_id)}
    if any(u['payload'].get('adoption_id') and u['payload']['adoption_id'] not in adopted for u in uses):
        raise LabcalError('METHOD_ADOPTION_SUPERSEDED', '所引用的标准采用关系已被替代，请先更新设备用途。')
    if len({u['payload']['equipment_key'] for u in uses}) != 1:
        raise LabcalError('METHOD_ONE_DEVICE_PER_TASK', '同一任务的多个方法必须对应同一设备。')
    active = {u['id'] for u in list_uses(workspace, lab_id)}
    if set(use_ids) - active: raise LabcalError('METHOD_USE_SUPERSEDED', '用途关联已被替代，请选择当前版本。')
    inner = self_diagnosis.prepare(workspace, lab_id, campaign_id, extraction_ids)
    _, fixed = self_diagnosis.load_task(workspace, lab_id, campaign_id, inner['task_id'])
    task = {'id': identifier('METH'), 'lab_id': lab_id, 'campaign_id': campaign_id,
            'engine': ENGINE, 'created_at': utc_now(), 'self_task_id': inner['task_id'],
            'self_task_hash': inner['task_hash'], 'documents': fixed['payload']['documents'],
            'uses': uses, 'methods': {u['payload']['method_id']: get_method(workspace, u['payload']['method_id']) for u in uses},
            'technical_approval': False}
    folder = processing_directory(store_for(workspace, lab_id), 'certificates', task['id'])
    write_json(folder / 'task.json', {'payload': task, 'payload_hash': content_hash(task)})
    write_json(folder / 'record-template.json', {'task_hash': content_hash(task), 'author': 'Codex 智能体', 'observations': []})
    return {'task_id': task['id'], 'task_hash': content_hash(task), 'self_task_id': inner['task_id'],
            'self_task_directory': inner['directory'], 'directory': str(folder), 'technical_approval': False}


def load_task(workspace, lab_id, campaign_id, task_id):
    folder = processing_directory(store_for(workspace, lab_id), 'certificates', segment(task_id))
    fixed = read_json(folder / 'task.json'); task = fixed['payload']
    if content_hash(task) != fixed['payload_hash'] or task['lab_id'] != lab_id or task['campaign_id'] != campaign_id:
        raise LabcalError('METHOD_TASK_CHANGED', '任务摘要或实验室、批次范围不一致。')
    _, inner = self_diagnosis.load_task(workspace, lab_id, campaign_id, task['self_task_id'])
    if inner['payload_hash'] != task['self_task_hash']: raise LabcalError('METHOD_TASK_CHANGED', '自身诊断输入已变更。')
    for use in task['uses']:
        if get_use(workspace, lab_id, use['id']) != use: raise LabcalError('METHOD_TASK_CHANGED', '用途快照发生变化。')
    for key, method in task['methods'].items():
        if get_method(workspace, key) != method: raise LabcalError('METHOD_TASK_CHANGED', '方法要求快照发生变化。')
    return folder, fixed


def read_self_run(workspace, lab_id, campaign_id, run_id, task):
    with store_for(workspace, lab_id).connect() as db:
        row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
    if not row or row['kind'] != 'certificate_self_diagnosis':
        raise LabcalError('METHOD_SELF_RUN_INVALID', '请引用本任务已完成的证书自身诊断。')
    manifest = json.loads(row['manifest'])
    if manifest.get('campaign_id') != campaign_id or manifest.get('task_id') != task['self_task_id'] or manifest.get('task_hash') != task['self_task_hash']:
        raise LabcalError('METHOD_SELF_RUN_INVALID', '自身诊断属于其他任务或批次。')
    folder = output_directory(workspace.lab_store(lab_id), run_id, row['kind'], existing=True)
    if read_json(folder / 'manifest.json') != manifest: raise LabcalError('METHOD_OUTPUT_CHANGED', '自身诊断清单发生变化。')
    for item in manifest['artifacts']:
        if file_hash(folder / item['name']) != item['sha256']: raise LabcalError('METHOD_OUTPUT_CHANGED', '自身诊断输出摘要不一致。')
    return read_json(folder / 'diagnosis.json')


def finish(workspace, lab_id, campaign_id, task_id, record, *, annotate=True):
    folder, fixed = load_task(workspace, lab_id, campaign_id, task_id); task = fixed['payload']
    get_campaign(workspace, lab_id, campaign_id, writable=True)
    validate_contract('method-diagnosis-record.v1', record)
    if record['task_hash'] != fixed['payload_hash'] or not record['author'].strip():
        raise LabcalError('METHOD_RECORD_INVALID', '提交记录的输入摘要或实际作者无效。')
    store = store_for(workspace, lab_id)
    digest = content_hash({'task': fixed['payload_hash'], 'record': record, 'annotate': annotate, 'engine': ENGINE,
                           'annotation_renderer': RENDERER_VERSION if annotate else None})
    with store.connect() as db:
        existing = db.execute('SELECT id FROM method_diagnoses WHERE record_hash=?', (digest,)).fetchone()
    if existing:
        target = output_directory(store, existing['id'], 'method_diagnosis', existing=True)
        return {'run_id': existing['id'], 'directory': str(target), 'reused': True, 'technical_approval': False}
    inner = read_self_run(workspace, lab_id, campaign_id, record['self_run_id'], task) if record.get('self_run_id') else None
    self_folder, _ = self_diagnosis.load_task(workspace, lab_id, campaign_id, task['self_task_id'])
    uses = {u['id']: u for u in task['uses']}; documents = {d['extraction_id']: d for d in task['documents']}
    pairs = set(); rows = []
    for obs in record['observations']:
        if obs['use_id'] not in uses or obs['extraction_id'] not in documents:
            raise LabcalError('METHOD_SCOPE_MISMATCH', '对照结果包含任务外设备用途或证书。')
        use = uses[obs['use_id']]['payload']; method = task['methods'][use['method_id']]['payload']
        requirement = next((r for r in method['requirements'] if r['id'] == obs['requirement_id']), None)
        if not requirement or obs['requirement_id'] not in use['requirement_ids']:
            raise LabcalError('METHOD_SCOPE_MISMATCH', '要求编号不属于该设备用途的关联要求。')
        key = (obs['use_id'], obs['requirement_id'], obs['extraction_id'], obs['point'], obs['condition'], obs['scope'])
        if key in pairs: raise LabcalError('METHOD_DUPLICATE_OBSERVATION', '同一参数、点位、工况和评价范围重复。')
        pairs.add(key)
        ext = get_extraction(workspace, lab_id, obs['extraction_id'])['payload']
        self_diagnosis.validate_page_evidence(ext, self_folder, obs['extraction_id'], obs['evidence'])
        decision = compare(requirement, use, obs)
        if inner is None:
            decision = {**decision, 'state': 'not_checked', 'reason': '尚未提交本任务的证书自身诊断，方法结果暂不判定。'}
        # Findings on values/identity block downstream endorsement of those values.
        elif any(c['extraction_id'] == obs['extraction_id'] and c['state'] == 'confirmed_error' and c['group'] in {'numeric', 'identity'} for c in inner['checks']):
            decision = {**decision, 'state': 'insufficient_evidence', 'reason': decision['reason'] + ' 证书存在已确认的信息或计算错误，请先澄清其对本项的影响。'}
        elif any(c['extraction_id'] == obs['extraction_id'] and c['group'] in {'identity', 'numeric', 'technical', 'conclusion'}
                 and c['state'] not in {'no_anomaly_detected', 'not_applicable'} for c in inner['checks']):
            decision = {**decision, 'state': 'insufficient_evidence', 'reason': decision['reason'] + ' 证书自身检查仍有未检查或待核实事项，本项数值比较仅作参考。'}
        rows.append(make_row(use, method, requirement, obs, decision))
    for use_id, item in uses.items():
        use = item['payload']; method = task['methods'][use['method_id']]['payload']
        for doc in documents:
            for req in method['requirements']:
                if req['id'] not in use['requirement_ids']: continue
                if any(k[:3] == (use_id, req['id'], doc) for k in pairs): continue
                obs = {'use_id': use_id, 'requirement_id': req['id'], 'extraction_id': doc, 'point': '',
                       'condition': '', 'scope': '未建立对应', 'match': 'missing', 'reason': '本次没有提交本项对应证据。',
                       'evidence': [], 'rule': 'none', 'rule_basis': '未指定'}
                rows.append(make_row(use, method, req, obs, {'state': 'not_checked', 'reason': '尚未建立对应或未检查，不能据此认定设备不合格。', 'engine': ENGINE}))
    communications = []
    for communication in record.get('communications', []):
        if communication['use_id'] not in uses or communication['extraction_id'] not in documents:
            raise LabcalError('METHOD_SCOPE_MISMATCH', '沟通内容不属于当前任务。')
        value = dict(communication)
        if 'uncertainty_request' in value:
            request = value['uncertainty_request']
            source = verify_source_integrity(workspace, lab_id, request['scope_source_id'])
            if source['kind'] != 'institution_scope' or not 1 <= request['scope_page'] <= source['metadata'].get('pages', 0):
                raise LabcalError('METHOD_SCOPE_REQUIRED', '不确定度委托要求需要对应机构认可范围原页。')
            proposed = decimal(request['value'])
            cmc = convert(str(decimal(request['cmc']['value'])), request['cmc']['unit'], request['unit'])
            if proposed <= 0 or cmc <= 0 or proposed < cmc or decimal(request['k']) != decimal(request['scope_k']):
                raise LabcalError('METHOD_CMC_CONFLICT', '拟委托U不能低于适用CMC，且需采用一致的包含因子。')
            value['scope_source_hash'] = source['sha256']
            value['note'] = 'CMC适用范围及时间由智能体结合原页分析；实际可达到的U仍需机构确认。'
        communications.append(value)
    result = {'task': task, 'task_hash': fixed['payload_hash'], 'evaluation_engine': ENGINE, 'author': record['author'], 'rows': rows, 'communications': communications,
              'self_run_id': record.get('self_run_id'), 'self_checks': inner['checks'] if inner else [],
              'technical_approval': False, 'scope_note': '每行仅评价声明的参数、工况和证据范围；不自动推断全量程或整台设备合格。'}
    run_id = identifier('RUN'); output = output_directory(store, run_id, 'method_diagnosis'); output.mkdir(parents=True)
    write_json(output / 'diagnosis.json', result)
    md = ['# 方法用途诊断', '', result['scope_note'], '', '证书自身诊断：' + (record.get('self_run_id') or '未完成'), '']
    for row in rows:
        md += [f"## {row['equipment_name']} · {row['parameter']} · {row['point'] or '未对应点位'}", '',
               f"{row['standard_code']}-{row['edition']}；{LABELS[row['decision']['state']]}", '',
               row['decision']['reason'], '', '标准要求：' + row['statement'], '', '原文依据：' + row['standard_evidence'], '']
    (output / 'diagnosis.md').write_text('\n'.join(md), encoding='utf-8')
    annotated_pdfs = []
    if annotate:
        for ext_id, doc in documents.items():
            marks = []
            for i, row in enumerate(rows, 1):
                if row['extraction_id'] != ext_id or row['decision']['state'] != 'does_not_meet': continue
                for loc in row['evidence']:
                    marks.append({'id': f'MD-{i}', 'title': row['parameter'], 'message': row['decision']['reason'],
                        'basis': row['standard_evidence'], 'suggestion': '请依据实际记录核对，必要时补校。',
                        'dimension': 'intended_use',
                        'certainty': 'confirmed', 'location': {**loc, 'kind': 'text'}})
            if marks:
                source = verify_source_integrity(workspace, lab_id, doc['source_id'])
                mapping = annotate_pdf_isolated(source['absolute_path'], output / (ext_id + '-annotated.pdf'), marks, style='callouts')
                annotated_pdfs.append({'extraction_id': ext_id, 'file': ext_id + '-annotated.pdf', 'page_mapping': mapping})
    manifest = {'run_id': run_id, 'kind': 'method_diagnosis', 'lab_id': lab_id, 'campaign_id': campaign_id,
        'task_id': task_id, 'created_at': utc_now(), 'annotated_pdfs': annotated_pdfs,
        'artifacts': [{'name': p.name, 'sha256': file_hash(p)} for p in sorted(output.iterdir())]}
    write_json(output / 'manifest.json', manifest)
    with store.connect() as db:
        db.execute('INSERT INTO method_diagnoses(id,campaign_id,payload,payload_hash,record_hash,created_at) VALUES (?,?,?,?,?,?)',
            (run_id, campaign_id, canonical_json(result), content_hash(result), digest, utc_now()))
        db.execute('INSERT INTO runs VALUES (?,?,?,?)', (run_id, 'method_diagnosis', canonical_json(manifest), utc_now()))
        store.event(db, 'method_diagnosis_completed', run_id, {'payload_hash': content_hash(result), 'task_hash': fixed['payload_hash']})
    return {'run_id': run_id, 'directory': str(output), 'rows': len(rows), 'technical_approval': False}


def make_row(use, method, req, obs, decision):
    return {**obs, 'equipment_key': use['equipment_key'], 'equipment_name': use['equipment_name'],
            'purpose': use['purpose'], 'standard_code': method['standard_code'], 'edition': method['edition'],
            'method_id': use['method_id'], 'parameter': req['parameter'], 'requirement_type': req['requirement_type'],
            'statement': req['statement'], 'method_condition': req['condition'], 'use_range': use['use_range'],
            'conditions': use['conditions'], 'standard_evidence': '\n'.join(f"第{e['page']}页 {e['clause']}：{e['quote']}" for e in req['evidence']),
            'criterion': req['criterion'], 'decision': decision}
