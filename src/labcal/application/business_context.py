"""Assemble existing laboratory facts for both lanes, without technical decisions."""
import json

from ..domain.contracts import validate_contract
from ..errors import LabcalError
from ..util import canonical_json, content_hash, utc_now
from .methods import list_adoptions, list_uses, get_method
from .sources import verify_source_integrity
from .workspace_catalog import require_working_laboratory


def list_notes(workspace, lab_id, *, history=False):
    store = workspace.lab_store(lab_id)
    with store.connect() as db:
        rows = list(db.execute("SELECT value FROM meta WHERE key LIKE 'context_note:%' ORDER BY rowid"))
    notes = [json.loads(r[0]) for r in rows]
    for note in notes:
        if (note['payload']['lab_id'] != lab_id or content_hash(note['payload']) != note['payload_hash']):
            raise LabcalError('CONTEXT_CHANGED', '上下文记录摘要或实验室归属不一致。')
        source_id = note['payload'].get('source_id')
        if source_id:
            source = verify_source_integrity(workspace, lab_id, source_id)
            if source['sha256'] != note['payload']['source_hash']:
                raise LabcalError('CONTEXT_CHANGED', '上下文依据原件已变化。')
    replaced = {n['payload'].get('supersedes') for n in notes}
    return [n for n in notes if history or n['id'] not in replaced]


def import_note(workspace, lab_id, record):
    require_working_laboratory(workspace, lab_id)
    validate_contract('context-note.v1', record)
    if any(not v.strip() for v in record.values() if isinstance(v, str)):
        raise LabcalError('CONTEXT_TEXT_REQUIRED', '上下文内容、适用范围及证据不能为空白。')
    payload = {**record, 'lab_id': lab_id, 'technical_approval': False}
    if record.get('source_id'):
        source = verify_source_integrity(workspace, lab_id, record['source_id'])
        if record.get('page', 1) > source['metadata'].get('pages', 1):
            raise LabcalError('CONTEXT_PAGE_INVALID', '依据页码超出原件。')
        payload['source_hash'] = source['sha256']
    elif record.get('page'):
        raise LabcalError('CONTEXT_SOURCE_REQUIRED', '页码必须关联原件。')
    digest = content_hash(payload); key = 'CTX-' + digest[:24]
    value = {'id': key, 'payload': payload, 'payload_hash': digest}
    store = workspace.lab_store(lab_id)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        all_notes = list_notes(workspace, lab_id, history=True)
        if any(n['id'] == key for n in all_notes): return value
        replaced = {n['payload'].get('supersedes') for n in all_notes}
        old = next((n for n in all_notes if n['id'] == record.get('supersedes')), None)
        signature = lambda p: (p['equipment_key'], p['kind'], p['key'])
        if record.get('supersedes') and (not old or old['id'] in replaced or
                                        signature(old['payload']) != signature(payload)):
            raise LabcalError('CONTEXT_VERSION_CONFLICT', '替代记录必须是同一实验室、设备及主题的当前版本。')
        if record['state'] == 'recorded' and any(
                n['id'] not in replaced and n['id'] != record.get('supersedes') and
                n['payload']['state'] == 'recorded' and signature(n['payload']) == signature(payload)
                for n in all_notes):
            raise LabcalError('CONTEXT_VERSION_CONFLICT', '已有有效记录；修改请显式引用supersedes，保留历史。')
        if old and old['payload']['state'] == 'recorded' and record['state'] == 'candidate':
            raise LabcalError('CONTEXT_VERSION_CONFLICT', '候选意见不能替代已明确的记录。')
        db.execute('INSERT INTO meta VALUES (?,?)', ('context_note:' + key, canonical_json(value)))
        store.event(db, 'context_note_recorded', key, {'payload_hash': digest, 'created_at': utc_now()})
    return value


def assemble_context(workspace, lab_id, equipment_key=None, campaign_id=None):
    """Read-only projection. Missing fields are prompts, never failures or defaults."""
    store = workspace.lab_store(lab_id)
    if campaign_id:
        from .campaigns import get_campaign
        get_campaign(workspace, lab_id, campaign_id)
    with store.connect() as db:
        laboratory = json.loads(db.execute("SELECT value FROM meta WHERE key='laboratory'").fetchone()[0])
        template = db.execute("SELECT value FROM meta WHERE key='document_template:calibration-confirmation'").fetchone()
    adoptions = list_adoptions(workspace, lab_id)
    all_uses = list_uses(workspace, lab_id)
    active_adoptions = {a['id'] for a in adoptions}
    current = [u for u in all_uses if u['payload']['state'] == 'user_adopted' and
               (not u['payload'].get('adoption_id') or u['payload']['adoption_id'] in active_adoptions)]
    selected = [u for u in current if not equipment_key or u['payload']['equipment_key'] == equipment_key]
    related_keys = {k for u in selected for k in u['payload'].get('measurement', {}).get('related_equipment_keys', [])}
    related = [u for u in current if u['payload']['equipment_key'] in related_keys and u not in selected]
    notes = [n for n in list_notes(workspace, lab_id) if
             not equipment_key or n['payload']['equipment_key'] in {'*', equipment_key}]
    candidates = [u for u in all_uses if u['payload']['state'] == 'candidate' and
                  (not equipment_key or u['payload']['equipment_key'] == equipment_key)]
    methods = {u['payload']['method_id']: get_method(workspace, u['payload']['method_id']) for u in selected + related}
    from .laboratories import list_equipment
    selected_keys = ({equipment_key} | related_keys) if equipment_key else set()
    equipment = [e for e in list_equipment(workspace, lab_id) if not equipment_key or
                 selected_keys.intersection({e['id'], 'asset:' + e['asset_code']}) or
                 any(u['payload'].get('equipment_id') == e['id'] for u in selected + related)]
    questions = []
    for u in selected:
        p = u['payload']; method = methods[p['method_id']]['payload']
        for req in method['requirements']:
            if req['id'] not in p['requirement_ids']: continue
            missing = sorted(set(req['criterion'].get('inputs', {})) - set(p['conditions']))
            if missing:
                questions.append({'key': u['id'] + ':' + req['id'], 'use_id': u['id'],
                                  'reason': '方法公式缺少实际输入', 'fields': missing})
    known = {u['payload']['equipment_key'] for u in current}
    for key in sorted(related_keys - known):
        questions.append({'key': 'related:' + key, 'reason': '协作设备尚无已明确用途关联', 'equipment_key': key})
    if equipment_key and not selected:
        questions.append({'key': 'uses:' + equipment_key, 'reason': '尚无已明确的方法用途；可先检查证书自身'})
    return {'lab_id': lab_id, 'equipment_key': equipment_key, 'laboratory': laboratory,
            'adoptions': adoptions, 'uses': selected, 'related_uses': related, 'equipment': equipment,
            'notes': [n for n in notes if n['payload']['state'] == 'recorded'],
            'candidates': candidates + [n for n in notes if n['payload']['state'] == 'candidate'],
            'methods': methods, 'template_id': template[0] if template else None,
            'questions': questions, 'technical_approval': False}


def context_signature(context):
    """Only dependencies, not conclusions, file timestamps or display wording."""
    sources = {m['payload']['source_id'] for m in context['methods'].values()}
    return {'laboratory': {k: context['laboratory'][k] for k in ('laboratory_type', 'profile')},
            'adoptions': {a['id']: a['record_hash'] for a in context['adoptions']
                          if not sources or a['payload']['source_id'] in sources},
            'uses': {u['id']: u['record_hash'] for u in context['uses'] + context['related_uses']},
            'equipment': {e['id']: e['config_hash'] for e in context['equipment']},
            'notes': {n['id']: n['payload_hash'] for n in context['notes']},
            'methods': {k: m['payload_hash'] for k, m in context['methods'].items()},
            'template_id': context['template_id']}


def validate_context_coverage(snapshot, record):
    """Each known use needs an explicit disposition in the existing coverage list."""
    uses = {u['id']: u for u in snapshot['uses']}
    seen = set()
    for method in record.get('coverage', {}).get('methods', []):
        for item in method['items']:
            for key in item.get('use_ids', []):
                use = uses.get(key)
                if not use or snapshot['methods'][use['payload']['method_id']]['payload']['source_id'] != method['source_id']:
                    raise LabcalError('CONTEXT_USE_SCOPE', '用途处理引用不属于本设备或对应标准。')
                seen.add(key)
    if uses.keys() - seen:
        raise LabcalError('CONTEXT_USE_OMITTED', '已有实际用途尚未在用途覆盖清单中说明处理去向。',
                          {'use_ids': sorted(uses.keys() - seen)})
