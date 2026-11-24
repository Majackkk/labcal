"""One regenerable workbook per laboratory, immutable export snapshots behind it."""
import json
from pathlib import Path

from ..adapters.diagnosis_export import write_xlsx
from ..adapters.layout import output_directory
from ..errors import LabcalError
from ..util import atomic_write_text, canonical_json, content_hash, file_hash, identifier, read_json, utc_now, write_json
from .campaigns import get_campaign
from .method_diagnosis import LABELS
from .methods import store_for, list_uses, list_adoptions

SELF_STATES = {'confirmed_error': '报告内容错误', 'candidate_concern': '待核实',
    'no_anomaly_detected': '已检查范围未发现异常', 'unable_to_check': '无法判断',
    'not_applicable': '不适用', 'not_checked': '未检查'}


def current_confirmations(records, certificate_parents):
    """One latest confirmation per batch, certificate and explicit device label.

    Callers verify every fixed record before projection. Separate certificates
    remain separate even if device labels match. Different devices on one
    certificate also remain separate; aliases require evidence-backed label
    normalization, never fuzzy inference from a shared certificate.
    """
    current = {}
    for record in records:
        extraction = record['extraction_id']
        certificate = certificate_parents.get(extraction, extraction)
        current[(record['campaign_id'], certificate, record['equipment_label'])] = record
    replaced = set()
    by_task = {r['task_id']: r for r in records}
    for record in records:
        task = record.get('payload', {}).get('task', {})
        old = by_task.get(task.get('replaces_task'))
        old_task = old.get('payload', {}).get('task', {}) if old else {}
        if (old and old['campaign_id'] == record['campaign_id'] and task.get('equipment_key') and
                old_task.get('equipment_key') == task['equipment_key'] and task.get('replacement_reason')):
            replaced.add(old['task_id'])
    return [r for r in current.values() if r['task_id'] not in replaced]


def collect(workspace, lab_id):
    store = store_for(workspace, lab_id)
    with store.connect() as db:
        diagnoses = [dict(r) for r in db.execute('SELECT * FROM method_diagnoses ORDER BY seq')]
        runs = [dict(r) for r in db.execute("SELECT * FROM runs WHERE kind='certificate_self_diagnosis' ORDER BY rowid")]
        extraction_versions = list(db.execute('SELECT id,certificate_id FROM certificate_extractions ORDER BY rowid'))
        extraction_links = list(db.execute("SELECT campaign_id,entity_id FROM campaign_links WHERE kind='extraction'"))
    parents = {r['id']: r['certificate_id'] for r in extraction_versions}
    links = {(r['campaign_id'], r['entity_id']) for r in extraction_links}
    latest = {(c, r['certificate_id']): r['id'] for r in extraction_versions for c, ext in links if ext == r['id']}
    payloads = {}
    groups = {}; internal = {}; communications = {}
    for run in runs:
        manifest = json.loads(run['manifest']); campaign_id = manifest['campaign_id']
        folder = output_directory(store, run['id'], run['kind'], existing=True)
        validate_export(folder, manifest)
        payload = read_json(folder / 'diagnosis.json')
        for document in payload['task']['documents']:
            ext = document['extraction_id']
            internal[(campaign_id, parents[ext])] = {'campaign_id': campaign_id, 'run_id': run['id'], 'document': document,
                'checks': [c for c in payload['checks'] if c['extraction_id'] == ext]}
    for item in diagnoses:
        payload = json.loads(item['payload'])
        payloads[item['id']] = payload
        if content_hash(payload) != item['payload_hash']:
            raise LabcalError('METHOD_OUTPUT_CHANGED', '诊断记录摘要不一致，停止汇总。')
        campaign_id = item['campaign_id']; per_run = {}
        for row in payload['rows']:
            key = (campaign_id, parents[row['extraction_id']], row['equipment_key'], row['standard_code'], row['edition'], row['purpose'])
            per_run.setdefault(key, []).append({**row, 'campaign_id': campaign_id, 'run_id': item['id']})
        groups.update(per_run)  # The complete newer check set supersedes its previous set.
        for key in per_run:
            communications[key] = []  # New diagnosis must not inherit obsolete requests.
        for communication in payload.get('communications', []):
            use = next(u['payload'] for u in payload['task']['uses'] if u['id'] == communication['use_id'])
            method = payload['task']['methods'][use['method_id']]['payload']
            key = (campaign_id, parents[communication['extraction_id']], use['equipment_key'], method['standard_code'], method['edition'], use['purpose'])
            communications.setdefault(key, []).append({**communication, 'campaign_id': campaign_id, 'equipment_name': use['equipment_name'], 'run_id': item['id']})
    adopted = {a['id'] for a in list_adoptions(workspace, lab_id)}
    active = {u['id'] for u in list_uses(workspace, lab_id) if not u['payload'].get('adoption_id') or u['payload']['adoption_id'] in adopted}
    stale_groups = set()
    for group_key, group in groups.items():
        for row in group:
            if row['use_id'] not in active:
                stale_groups.add(group_key)
                row['decision'] = {**row['decision'], 'state': 'not_checked', 'reason': '设备用途关联已有新版本；本行保留历史引用，需按新版本重新诊断。'}
            parent = parents[row['extraction_id']]
            if latest.get((row['campaign_id'], parent)) != row['extraction_id']:
                stale_groups.add(group_key)
                row['decision'] = {**row['decision'], 'state': 'not_checked', 'reason': '该证书已有更新提取版本；本行保留历史引用，需重新诊断。'}
            else:
                current_internal = internal.get((row['campaign_id'], parent))
                diagnosis = payloads[row['run_id']]
                if current_internal and current_internal['run_id'] != diagnosis.get('self_run_id'):
                    stale_groups.add(group_key)
                    row['decision'] = {**row['decision'], 'state': 'not_checked', 'reason': '证书自身诊断已有新版本，需核对新发现后重新进行用途诊断。'}
    for item in internal.values():
        ext = item['document']['extraction_id']
        if latest.get((item['campaign_id'], parents[ext])) != ext:
            item['checks'] = [{**c, 'state': 'not_checked', 'reason': '证书已有更新提取版本，本项为旧版诊断，需更新检查。'} for c in item['checks']]
    from .document_templates import verify_confirmation
    confirmations = []
    with store.connect() as db:
        records = list(db.execute("SELECT value FROM meta WHERE key LIKE 'equipment_confirmation:%' ORDER BY rowid"))
    for item in records:
        meta = json.loads(item[0]); payload = verify_confirmation(store, meta)
        confirmations.append({**meta, 'payload': payload})
    from .confirmation_updates import impact_for_confirmation, list_followups
    current_forms = current_confirmations(confirmations, parents)
    updates = [impact_for_confirmation(workspace, c, c['payload']) for c in current_forms]
    return {'confirmations': current_forms, 'confirmation_updates': updates, 'followups': list_followups(workspace, lab_id),
            'lab_id': lab_id, 'method_rows': [r for rows in groups.values() for r in rows],
            'internal': list(internal.values()), 'communications': [c for key, rows in communications.items() if key not in stale_groups for c in rows if c['use_id'] in active], 'technical_approval': False}


def validate_export(folder, manifest):
    if read_json(folder / 'manifest.json') != manifest:
        raise LabcalError('SUMMARY_INPUT_CHANGED', '诊断输出清单摘要不一致。')
    from ..util import ensure_inside
    for artifact in manifest['artifacts']:
        if file_hash(ensure_inside(folder / artifact['name'], folder)) != artifact['sha256']:
            raise LabcalError('SUMMARY_INPUT_CHANGED', '诊断交付物缺失或摘要不一致。')


def sheets_for(workspace, lab_id, data):
    issues = [['问题编号', '年度', '批次', '设备／报告', '证书提取编号', '标准号', '版本', '参数／检查项',
               '结论', '问题与影响范围', '处理建议', '证书原页', '依据／来源', '诊断版本']]
    comparisons = [['年度', '批次', '设备名称', '设备标识', '证书提取编号', '标准号', '版本', '参数',
                    '工况', '点位', '实际使用范围', '方法要求', '允许误差／准确度限值', '不确定度要求', '证书结果', '证书U',
                    '判定规则', '结论', '说明', '评价范围', '方法原页依据', '证书原页', '诊断版本']]
    communication = [['年度', '批次', '设备／报告', '证书提取编号', '项目', '给校准机构的沟通内容', '数值要求状态', '诊断版本']]
    contexts = {}
    def context(campaign_id):
        if campaign_id not in contexts:
            c = get_campaign(workspace, lab_id, campaign_id)
            contexts[campaign_id] = [str(c['year']) if c['year'] else '待定', c['name']]
        return list(contexts[campaign_id])
    def pages(evidence): return '、'.join(str(p) for p in sorted({e['page'] for e in evidence})) or '-'
    def value(d): return (d['value'] + ' ' + d['unit']) if d else '-'
    for item in data['internal']:
        for check in item['checks']:
            if check['state'] in {'no_anomaly_detected', 'not_applicable'}: continue
            key = 'ISS-' + content_hash([item['campaign_id'], item['document']['extraction_id'], check['group'], check['item']])[:16]
            issues.append([key] + context(item['campaign_id']) + [item['document']['name'], item['document']['extraction_id'], '-', '-', check['item'],
                SELF_STATES[check['state']], check['reason'], check['suggestion'], pages(check['evidence']), check['basis'], item['run_id']])
    for row in data['method_rows']:
        c = context(row['campaign_id']); decision = row['decision']; label = LABELS[decision['state']]
        limit = (decision.get('limit', '-') + ' ' + decision.get('unit', '')).strip()
        comparisons.append(c + [row['equipment_name'], row['equipment_key'], row['extraction_id'], row['standard_code'], row['edition'],
            row['parameter'], row['condition'] or row['method_condition'] or '-', row['point'] or '-', row['use_range'], row['statement'],
            limit if row['requirement_type'] != 'uncertainty' else '-', row['statement'] if row['requirement_type'] == 'uncertainty' else '本条未规定；不能自行代填',
            value(row.get('value')), value(row.get('uncertainty')), row['rule'] + '：' + row['rule_basis'], label, decision['reason'] + '\n证书对应说明：' + row['reason'],
            row['scope'], row['standard_evidence'], pages(row['evidence']), row['run_id']])
        if decision['state'] == 'meets_requirement': continue
        key = 'ISS-' + content_hash([row[k] for k in ['campaign_id', 'equipment_key', 'extraction_id', 'standard_code', 'edition', 'purpose', 'requirement_id', 'point', 'condition', 'scope']])[:16]
        issues.append([key] + c + [row['equipment_name'], row['extraction_id'], row['standard_code'], row['edition'], row['parameter'], label,
            decision['reason'], '核对适用条件和原页；缺少实测项目时确认后补校。', pages(row['evidence']), row['standard_evidence'], row['run_id']])
        # Only concrete matched technical failures merit a request. Gaps are not fabricated commissions.
        if decision['state'] == 'does_not_meet':
            text = '1、请核对' + row['parameter'] + ('（' + row['point'] + '）' if row['point'] else '') + '的原始记录。\n2、适用要求：' + row['statement'] + '。\n3、据实更正；确需补测时请先确认。'
            communication.append(c + [row['equipment_name'], row['extraction_id'], row['parameter'], text,
                                       '未提出新的不确定度要求', row['run_id']])
    for item in data['communications']:
        request = item.get('uncertainty_request')
        text = '\n'.join(f'{i}、{line}' for i, line in enumerate(item['items'], 1))
        status = '未提出新的不确定度要求'
        if request:
            text += f"\n认可范围依据：{request['scope_source_id']} 第{request['scope_page']}页；{request['scope_quote']}"
            status = f"拟委托U≤{request['value']} {request['unit']}，k={request['k']}；适用CMC={request['cmc']['value']} {request['cmc']['unit']}，实际U待机构确认"
        communication.append(context(item['campaign_id']) + [item['equipment_name'], item['extraction_id'], '智能体整理的委托条目', text, status, item['run_id']])
    if len(communication) == 1:
        communication.append(['-', '-', '-', '-', '-', '尚无可直接发送的沟通条目；先明确用途及待核实事项。', '未提出数值委托要求', '-'])
    forms = [['年度', '批次', '设备', '证书提取编号', '功能', '测量任务', '结论', '参数及结果来源', '方法要求及判断', '不确定度评价', '溯源资料', '修正及应用', '评价范围', '依据引用', '确认版本', 'Word文件', '人员批准', '用途覆盖状态', '交付清单']]
    for item in data.get('confirmations', []):
        record = item['payload']['record']
        for row in record['rows']:
            forms.append(context(item['campaign_id']) + [item['equipment_label'], item['extraction_id'], row['function'], row['purpose'],
                ('校准特性记录（无独立判限）' if row['state'] == 'reported_characteristic' else LABELS.get(row['state'], '未分配该用途')), row['parameter_results'], row['method_judgment'], row['uncertainty_evaluation'], row['traceability'], row['application'],
                record['scope'], '\n'.join(record['evidence_references']), item['task_id'], item['document_path'], '未代填人员批准',
                {'complete_for_declared_scope':'声明范围已逐项处理','scoped':'限定范围，含证据缺口'}.get(record.get('coverage',{}).get('claim'), '旧版未进行用途覆盖检查'), item.get('delivery_path', '')])
    coverage_rows = [['年度','批次','设备','标准','关联性质','测量任务','处理去向','依据与理由','对应确认表行','原页','检索范围','确认版本']]
    disposition_labels = {'assessed':'本设备评价','other_instrument':'其他量具承担','not_applicable':'本次不适用','insufficient_evidence':'证据不足'}
    for item in data.get('confirmations', []):
        record = item['payload']['record']; scope = {m['source_id']:m for m in item['payload']['task'].get('coverage_scope', [])}
        for method in record.get('coverage',{}).get('methods',[]):
            source = scope[method['source_id']]
            for task in method['items']:
                coverage_rows.append(context(item['campaign_id']) + [item['equipment_label'], source['standard'],
                    '已采用' if source['relationship']=='laboratory_adopted' else '关联参考，非新增采用',task['task'],
                    disposition_labels[task['disposition']],task['reason'],'、'.join(map(str,task['row_numbers'])),
                    '\n'.join(f"第{e['page']}页 {e['clause']}：{e['quote']}" for e in task['evidence']),method['search_note'],item['task_id']])
    updates = [['年度', '批次', '设备/问题', '确认版本', '状态', '变化/处理说明', '依据/回复资料', '关联新确认']]
    for item in data.get('confirmation_updates', []):
        status = {'review_required': '有变化需要复核', 'unchanged': '依赖未变化', 'context_not_recorded': '历史未固定业务上下文'}[item['status']]
        labels = {'laboratory': '实验室技术配置', 'adoptions': '采用标准', 'uses': '实际用途/测量分工',
                  'equipment': '设备配置', 'notes': '有据选择/沟通表位置', 'methods': '方法要求版本',
                  'template_id': '确认表式', 'extraction': '证书提取版本', 'response_received': '收到机构回复'}
        text = '、'.join(labels[c['kind']] for c in item['changes'])
        if item['purposes_to_review']: text += '\n相关用途：' + '；'.join(item['purposes_to_review'])
        updates.append(context(item['campaign_id']) + [item['equipment'], item['task_id'], status, text, item['note'], ''])
    for item in data.get('followups', []):
        p = item['payload']
        updates.append(context(p['campaign_id']) + [p['issue_id'], p['task_id'],
            {'open': '沟通中', 'response_received': '已收到回复', 'addressed': '已关联处理记录'}[p['state']],
            p['statement'], p['evidence_reference'] + '\n' + '、'.join(p.get('response_source_ids', [])), p.get('resolution_task', '')])
    return [('变更与跟进', updates, [10,18,32,28,26,65,65,28]), ('用途覆盖清单', coverage_rows, [10,18,28,24,28,36,24,65,16,65,65,28]), ('设备确认记录', forms, [10,18,28,32,20,28,24,45,60,40,45,50,45,50,28,50,24,30,50]), ('问题清单', issues, [24, 10, 18, 32, 32, 20, 12, 28, 24, 60, 48, 12, 65, 30]),
            ('方法要求对照', comparisons, [10, 18, 24, 30, 32, 20, 12, 24, 36, 20, 30, 55, 24, 30, 24, 24, 35, 24, 60, 35, 70, 12, 30]),
            ('校准机构沟通', communication, [10, 18, 30, 32, 28, 90, 35, 30])]


def export_summary(workspace, lab_id):
    store = store_for(workspace, lab_id); data = collect(workspace, lab_id)
    digest = content_hash(data); current = store.directory / 'outputs' / 'summaries' / '设备校准诊断汇总.xlsx'
    # The publication lock protects the current workbook and its ownership hash.
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT value FROM meta WHERE key='summary_current'").fetchone()
        previous = json.loads(row[0]) if row else None
        if current.exists() and (not previous or file_hash(current) != previous['sha256']):
            raise LabcalError('SUMMARY_LOCALLY_EDITED', '汇总表已有外部修改，已保留；请先说明需要接收的修改，再重新生成。')
        if previous and previous['input_hash'] == digest and current.exists():
            return {**previous, 'path': str(current), 'reused': True}
        run_id = identifier('RUN'); output = output_directory(store, run_id, 'laboratory_summary'); output.mkdir(parents=True)
        write_json(output / 'summary.json', data)
        write_xlsx(output / 'summary.xlsx', sheets_for(workspace, lab_id, data))
        manifest = {'kind': 'laboratory_summary', 'run_id': run_id, 'lab_id': lab_id, 'created_at': utc_now(),
            'artifacts': [{'name': p.name, 'sha256': file_hash(p)} for p in sorted(output.iterdir())]}
        write_json(output / 'manifest.json', manifest)
        import os, tempfile
        fd, temporary = tempfile.mkstemp(prefix='.summary-', dir=current.parent)
        try:
            with os.fdopen(fd, 'wb') as stream: stream.write((output / 'summary.xlsx').read_bytes())
            os.replace(temporary, current)
        finally: Path(temporary).unlink(missing_ok=True)
        metadata = {'run_id': run_id, 'input_hash': digest, 'sha256': file_hash(current), 'technical_approval': False}
        db.execute("INSERT OR REPLACE INTO meta VALUES ('summary_current',?)", (canonical_json(metadata),))
        db.execute('INSERT INTO runs VALUES (?,?,?,?)', (run_id, 'laboratory_summary', canonical_json(manifest), utc_now()))
        store.event(db, 'laboratory_summary_exported', run_id, metadata)
    return {**metadata, 'path': str(current), 'directory': str(output), 'reused': False}
