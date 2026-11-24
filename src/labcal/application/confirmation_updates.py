"""Dependency reminders and evidence-linked follow-up; never alter fixed judgments."""
import json

from ..adapters.layout import processing_directory, segment
from ..domain.contracts import validate_contract
from ..errors import LabcalError
from ..util import canonical_json, content_hash, read_json
from .business_context import assemble_context, context_signature
from .sources import verify_source_integrity


def published(workspace, lab_id):
    from .document_templates import verify_confirmation
    store = workspace.lab_store(lab_id)
    with store.connect() as db:
        rows = list(db.execute("SELECT value FROM meta WHERE key LIKE 'equipment_confirmation:%' ORDER BY rowid"))
    return [(meta, verify_confirmation(store, meta)) for meta in (json.loads(r[0]) for r in rows)]


def task_context(workspace, task, metadata=None):
    if not task.get('context_hash'): return None
    store = workspace.lab_store(task['lab_id'])
    if metadata and metadata.get('delivery_path'):
        folder = store.directory / metadata['delivery_path']
        path = folder.parent / 'business-context.json'
    else: path = processing_directory(store, 'certificates', segment(task['id'])) / 'business-context.json'
    value = read_json(path)
    if content_hash(value) != task['context_hash'] or value['lab_id'] != task['lab_id']:
        raise LabcalError('CONTEXT_CHANGED', '固定业务上下文摘要或归属不一致。')
    return value


def context_changes(before, after):
    old, new = context_signature(before), context_signature(after)
    changes = []
    for kind in old:
        if old[kind] != new[kind]:
            changes.append({'kind': kind, 'previous': old[kind], 'current': new[kind]})
    return changes


def impact(workspace, lab_id, campaign_id=None, task_id=None, *, history=False):
    results = []
    records = published(workspace, lab_id)
    if records and not history and not task_id:
        from .laboratory_summary import current_confirmations
        with workspace.lab_store(lab_id).connect() as db:
            parents = {r[0]: r[1] for r in db.execute('SELECT id,certificate_id FROM certificate_extractions')}
        projected = current_confirmations([{**m, 'payload': p} for m, p in records], parents)
        selected = {m['task_id'] for m in projected}
        records = [(m, p) for m, p in records if m['task_id'] in selected]
    for meta, value in records:
        task = value['task']
        if campaign_id and task['campaign_id'] != campaign_id or task_id and task['id'] != task_id: continue
        results.append(impact_for_confirmation(workspace, meta, value))
    if task_id and not results: raise LabcalError('CONFIRMATION_NOT_FOUND', '本实验室没有指定的已发布确认。')
    return results


def impact_for_confirmation(workspace, meta, value):
    task = value['task']; lab_id = task['lab_id']; store = workspace.lab_store(lab_id)
    snapshot = task_context(workspace, task, meta)
    changes = context_changes(snapshot, assemble_context(workspace, lab_id, task['equipment_key'])) if snapshot else []
    with store.connect() as db:
        parent = db.execute('SELECT certificate_id FROM certificate_extractions WHERE id=?', (task['extraction_id'],)).fetchone()
        newer = db.execute("SELECT e.id FROM certificate_extractions e JOIN campaign_links c ON c.entity_id=e.id AND c.kind='extraction' WHERE e.certificate_id=? AND c.campaign_id=? ORDER BY e.rowid DESC LIMIT 1",
                           (parent[0], task['campaign_id'])).fetchone() if parent else None
    if newer and newer[0] != task['extraction_id']:
        changes.append({'kind': 'extraction', 'previous': task['extraction_id'], 'current': newer[0]})
    responses = [v['id'] for v in list_followups(workspace, lab_id, task['id'])
                 if v['payload']['state'] == 'response_received']
    if responses: changes.append({'kind': 'response_received', 'records': responses})
    purposes = sorted({u['payload']['purpose'] for u in snapshot['uses'] + snapshot['related_uses']}) if snapshot else []
    return {'task_id': task['id'], 'campaign_id': task['campaign_id'], 'equipment': task['equipment_label'],
            'status': 'review_required' if changes else ('unchanged' if snapshot else 'context_not_recorded'),
            'changes': changes, 'purposes_to_review': purposes if changes else [],
            'note': '变更提醒不自动改变技术结论；历史未固定上下文不代表设备不符合。'}


def list_followups(workspace, lab_id, task_id=None, *, history=False):
    with workspace.lab_store(lab_id).connect() as db:
        rows = list(db.execute("SELECT value FROM meta WHERE key LIKE 'confirmation_followup:%' ORDER BY rowid"))
    values = [json.loads(r[0]) for r in rows]
    for value in values:
        p = value['payload']
        if p['lab_id'] != lab_id or content_hash(p) != value['payload_hash']:
            raise LabcalError('FOLLOWUP_CHANGED', '问题跟进摘要或实验室归属不一致。')
        for source in p.get('response_sources', []):
            if verify_source_integrity(workspace, lab_id, source['id'])['sha256'] != source['sha256']:
                raise LabcalError('FOLLOWUP_CHANGED', '机构回复或更正资料摘要不一致。')
    replaced = {v['payload'].get('supersedes') for v in values}
    return [v for v in values if (history or v['id'] not in replaced) and
            (not task_id or v['payload']['task_id'] == task_id)]


def import_followup(workspace, lab_id, task_id, record):
    from .workspace_catalog import require_working_laboratory
    require_working_laboratory(workspace, lab_id)
    validate_contract('confirmation-followup.v1', record)
    tasks = {m['task_id']: value['task'] for m, value in published(workspace, lab_id)}
    if task_id not in tasks: raise LabcalError('CONFIRMATION_NOT_FOUND', '问题须关联本实验室已发布的确认任务。')
    if any(not v.strip() for v in record.values() if isinstance(v, str)):
        raise LabcalError('FOLLOWUP_TEXT_REQUIRED', '处理说明和证据不能留空白。')
    sources = [verify_source_integrity(workspace, lab_id, sid) for sid in record.get('response_source_ids', [])]
    if record['state'] == 'addressed':
        resolution = tasks.get(record.get('resolution_task'))
        original = tasks[task_id]
        if (not resolution or resolution['id'] == task_id or not original.get('equipment_key') or
                resolution.get('equipment_key') != original['equipment_key']):
            raise LabcalError('FOLLOWUP_RESOLUTION_REQUIRED', '处理完成须关联同一明确设备的新确认记录，不能用机构回复自动改判。')
    payload = {**record, 'lab_id': lab_id, 'task_id': task_id, 'campaign_id': tasks[task_id]['campaign_id'],
               'response_sources': [{'id': s['id'], 'sha256': s['sha256']} for s in sources], 'technical_approval': False}
    digest = content_hash(payload); key = 'FUP-' + digest[:24]
    value = {'id': key, 'payload': payload, 'payload_hash': digest}
    with workspace.lab_store(lab_id).connect() as db:
        db.execute('BEGIN IMMEDIATE')
        all_values = list_followups(workspace, lab_id, history=True)
        if any(v['id'] == key for v in all_values): return value
        replaced = {v['payload'].get('supersedes') for v in all_values}
        old = next((v for v in all_values if v['id'] == record.get('supersedes')), None)
        signature = lambda p: (p['task_id'], p['issue_id'])
        if record.get('supersedes') and (not old or old['id'] in replaced or signature(old['payload']) != signature(payload)):
            raise LabcalError('FOLLOWUP_VERSION_CONFLICT', '跟进只能替代同一问题的当前记录。')
        if any(v['id'] not in replaced and v['id'] != record.get('supersedes') and
               signature(v['payload']) == signature(payload) for v in all_values):
            raise LabcalError('FOLLOWUP_VERSION_CONFLICT', '同一问题已有记录；追加进展须引用supersedes。')
        db.execute('INSERT INTO meta VALUES (?,?)', ('confirmation_followup:' + key, canonical_json(value)))
        workspace.lab_store(lab_id).event(db, 'confirmation_followup_recorded', key, {'payload_hash': digest})
    return value
