"""Reusable standard facts. A checked page is not laboratory adoption."""
import json
import re
from pathlib import Path

from ..adapters.layout import processing_directory
from ..adapters.pdf_process import render_page_isolated
from ..domain.contracts import validate_contract
from ..domain.method_requirements import decimal, limit_value
from ..errors import LabcalError
from ..util import canonical_json, content_hash, file_hash, identifier, utc_now
from .sources import verify_source_integrity


def store_for(workspace, lab_id=None):
    store = workspace.lab_store(lab_id) if lab_id else workspace.reference_store()
    if lab_id: store.enable_campaign_storage()
    store.enable_method_storage()
    return store


def method_page(workspace, source_id, page_number):
    if not source_id.startswith('REF-'):
        raise LabcalError('METHOD_REFERENCE_REQUIRED', '共用方法要求需要共享参考库原件。')
    source = verify_source_integrity(workspace, None, source_id)
    if source['kind'] != 'test_method_standard' or not 1 <= page_number <= source['metadata'].get('pages', 0):
        raise LabcalError('METHOD_PAGE_INVALID', '页面不属于已接入的检测方法标准。')
    folder = processing_directory(store_for(workspace), 'methods', source_id) / 'pages'
    target = folder / f'P{page_number}.png'
    if not target.exists():
        data = render_page_isolated(source['absolute_path'], page_number)
        folder.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream: stream.write(data)
    return {'path': str(target), 'image_sha256': file_hash(target), 'page': page_number}


def import_method(workspace, record):
    validate_contract('method-package.v1', record)
    source = verify_source_integrity(workspace, None, record['source_id'])
    if not record['source_id'].startswith('REF-') or source['kind'] != 'test_method_standard':
        raise LabcalError('METHOD_REFERENCE_REQUIRED', '方法要求只能引用共享参考库的检测方法原件。')
    if not all(record[k].strip() for k in ['standard_code', 'edition', 'title', 'author', 'coverage']):
        raise LabcalError('METHOD_TEXT_REQUIRED', '标准版本、覆盖范围和实际作者不能为空。')
    ids = [r['id'] for r in record['requirements']]
    if len(set(ids)) != len(ids): raise LabcalError('METHOD_DUPLICATE_REQUIREMENT', '要求编号重复。')
    for r in record['requirements']:
        if r['state'] == 'page_checked' and not r['evidence']:
            raise LabcalError('METHOD_EVIDENCE_REQUIRED', '已核对要求必须引用原页。')
        for e in r['evidence']:
            if not 1 <= e['page'] <= source['metadata'].get('pages', 0):
                raise LabcalError('METHOD_PAGE_INVALID', '要求证据页码无效。')
            if r['state'] == 'page_checked':
                image = processing_directory(store_for(workspace), 'methods', source['id']) / 'pages' / f"P{e['page']}.png"
                if not image.is_file() or file_hash(image) != e.get('image_sha256'):
                    raise LabcalError('METHOD_PAGE_NOT_READ', '请先读取原页并引用实际图像摘要。')
        c = r['criterion']
        if c['operator'] == 'none': continue
        if ('value' in c) == ('expression' in c):
            raise LabcalError('METHOD_CRITERION_INVALID', '判定要求需指定唯一的明确值或公式。')
        if c['operator'] == 'eq' and 'expression' in c:
            raise LabcalError('METHOD_CRITERION_INVALID', '等级判定不采用计算公式。')
        if c['unit'] == '%' and c['relative_basis'] not in {'reading', 'reference_value', 'full_scale'}:
            raise LabcalError('METHOD_RELATIVE_BASIS', '百分比要求必须明确计算基准。')
        samples = {}
        for key, spec in c.get('inputs', {}).items():
            if not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,40}', key) or not isinstance(spec, dict) or set(spec) - {'unit', 'positive'} or not spec.get('unit'):
                raise LabcalError('METHOD_INPUT_INVALID', '公式输入定义无效。')
            samples[key] = {'value': '1', 'unit': spec['unit']}
        if set(c.get('constants', {})) & set(samples):
            raise LabcalError('METHOD_INPUT_INVALID', '标准常数不能被实验室输入覆盖。')
        for datum in c.get('constants', {}).values():
            validate_contract_value(datum); decimal(datum['value'])
        limit_value(r, samples)  # Reject unsupported or dimensionally inconsistent expressions on import.
    payload = {**record, 'source_hash': source['sha256'], 'technical_approval': False}
    digest = content_hash(payload); key = 'METHOD-' + digest[:24]; store = store_for(workspace)
    with store.connect() as db:
        if not db.execute('SELECT 1 FROM method_packages WHERE id=?', (key,)).fetchone():
            db.execute('INSERT INTO method_packages VALUES (?,?,?,?,?,?)',
                (key, record['standard_code'], record['edition'], canonical_json(payload), digest, utc_now()))
            for r in record['requirements']:
                db.execute('INSERT INTO method_requirement_index VALUES (?,?,?,?)',
                    (key, r['id'], r['equipment_category'], r['parameter']))
            store.event(db, 'method_package_imported', key, {'payload_hash': digest})
    return get_method(workspace, key)


def validate_contract_value(value):
    from ..domain.contracts import _validate
    from ..domain.method_contracts import VALUE
    errors = []; _validate(value, VALUE, '$', errors)
    if errors: raise LabcalError('METHOD_INPUT_INVALID', '公式条件需带十进制值和单位。')


def get_method(workspace, method_id):
    with store_for(workspace).connect() as db:
        row = db.execute('SELECT * FROM method_packages WHERE id=?', (method_id,)).fetchone()
    if not row: raise LabcalError('METHOD_NOT_FOUND', '共享要求库没有该固定版本。')
    payload = json.loads(row['payload'])
    if content_hash(payload) != row['payload_hash']: raise LabcalError('METHOD_CHANGED', '方法要求快照摘要不一致。')
    source = verify_source_integrity(workspace, None, payload['source_id'])
    if source['sha256'] != payload['source_hash']: raise LabcalError('METHOD_CHANGED', '方法原件摘要不一致。')
    return {'id': method_id, 'payload_hash': row['payload_hash'], 'payload': payload}


def search_methods(workspace, query='', standard=None, edition=None, equipment=None, parameter=None):
    # Metadata-only index; all limits are read from the one immutable package.
    with store_for(workspace).connect() as db:
        ids = [r[0] for r in db.execute('SELECT id FROM method_packages ORDER BY standard_code,edition,id')]
    results = []
    for key in ids:
        item = get_method(workspace, key); p = item['payload']
        if standard and standard != p['standard_code'] or edition and edition != p['edition']: continue
        if query and query.casefold() not in canonical_json(p).casefold(): continue
        rows = [r for r in p['requirements'] if (not equipment or equipment.casefold() in r['equipment_category'].casefold())
                and (not parameter or parameter.casefold() in r['parameter'].casefold())]
        if rows: results.append({**item, 'matching_requirement_ids': [r['id'] for r in rows]})
    return results


def import_use(workspace, lab_id, record):
    validate_contract('method-use.v1', record); method = get_method(workspace, record['method_id'])
    if set(record['requirement_ids']) - {r['id'] for r in method['payload']['requirements']}:
        raise LabcalError('METHOD_REQUIREMENT_NOT_FOUND', '设备用途只能关联该方法版本内的明确要求。')
    if record['state'] == 'user_adopted' and not (record['user_statement'].strip() and record['evidence_reference'].strip()):
        raise LabcalError('METHOD_USER_EVIDENCE_REQUIRED', '采用版本、设备用途和条件需要直接用户证据。')
    if record.get('adoption_id'):
        adoptions = {r['id']: r for r in list_adoptions(workspace, lab_id)}
        adoption = adoptions.get(record['adoption_id'])
        if not adoption or adoption['payload']['source_id'] != method['payload']['source_id']:
            raise LabcalError('METHOD_ADOPTION_MISMATCH', '采用记录必须属于本实验室和所引用标准原件。')
    if record.get('equipment_id'):
        from .laboratories import get_equipment
        get_equipment(workspace, lab_id, record['equipment_id'])
    for value in record['conditions'].values():
        validate_contract_value(value); decimal(value['value'])
    payload = {**record, 'lab_id': lab_id, 'method_hash': method['payload_hash'], 'technical_approval': False}
    digest = content_hash(payload); key = 'USE-' + digest[:24]; store = store_for(workspace, lab_id)
    with store.connect() as db:
        if db.execute('SELECT 1 FROM method_uses WHERE id=?', (key,)).fetchone(): return get_use(workspace, lab_id, key)
        if record.get('supersedes'):
            old = get_use(workspace, lab_id, record['supersedes'])['payload']
            old_method = get_method(workspace, old['method_id'])['payload']
            if old['equipment_key'] != record['equipment_key'] or old_method['standard_code'] != method['payload']['standard_code']:
                raise LabcalError('METHOD_USE_SCOPE', '替代关系必须对应同一设备与方法标准。')
            if any(u['payload'].get('supersedes') == record['supersedes'] for u in list_uses(workspace, lab_id, include_history=True)):
                raise LabcalError('METHOD_USE_CONFLICT', '原用途版本已经被替代。')
        db.execute('INSERT INTO method_uses VALUES (?,?,?,?,?,?)',
            (key, record['equipment_key'], record['method_id'], canonical_json(payload), digest, utc_now()))
        store.event(db, 'method_use_imported', key, {'record_hash': digest})
    return get_use(workspace, lab_id, key)


def get_use(workspace, lab_id, use_id):
    with store_for(workspace, lab_id).connect() as db:
        row = db.execute('SELECT * FROM method_uses WHERE id=?', (use_id,)).fetchone()
    if not row: raise LabcalError('METHOD_USE_NOT_FOUND', '当前实验室没有该用途关联。')
    payload = json.loads(row['record'])
    if content_hash(payload) != row['record_hash'] or payload['lab_id'] != lab_id:
        raise LabcalError('METHOD_USE_CHANGED', '用途关联摘要或实验室范围不一致。')
    method = get_method(workspace, payload['method_id'])
    if method['payload_hash'] != payload['method_hash']: raise LabcalError('METHOD_CHANGED', '所引用的方法版本摘要不同。')
    return {'id': use_id, 'record_hash': row['record_hash'], 'payload': payload}


def list_uses(workspace, lab_id, *, equipment=None, include_history=False):
    with store_for(workspace, lab_id).connect() as db:
        ids = [r[0] for r in db.execute('SELECT id FROM method_uses ORDER BY rowid')]
    values = [get_use(workspace, lab_id, k) for k in ids]
    replaced = {u['payload'].get('supersedes') for u in values}
    return [u for u in values if (include_history or u['id'] not in replaced)
            and (not equipment or u['payload']['equipment_key'] == equipment)]


def import_adoption(workspace, lab_id, record):
    validate_contract('method-adoption.v1', record)
    source = verify_source_integrity(workspace, None, record['source_id'])
    if not record['source_id'].startswith('REF-') or source['kind'] != 'test_method_standard' or not all(record[k].strip() for k in ['standard_code','edition','user_statement','evidence_reference']):
        raise LabcalError('METHOD_ADOPTION_INVALID', '标准采用需明确共享原件、版本及直接用户证据。')
    store = store_for(workspace, lab_id)
    value = {**record, 'lab_id': lab_id, 'source_hash': source['sha256'], 'equipment_configuration_confirmed': False}
    digest = content_hash(value); key = 'ADOPT-' + digest[:24]
    with store.connect() as db:
        if record.get('supersedes'):
            rows = list_adoptions(workspace, lab_id, include_history=True)
            old = next((r for r in rows if r['id'] == record['supersedes']), None)
            if not old or old['payload']['standard_code'] != record['standard_code']:
                raise LabcalError('METHOD_ADOPTION_INVALID', '采用版本替代需引用同一实验室的同一标准。')
            if any(r['payload'].get('supersedes') == record['supersedes'] and r['id'] != key for r in rows):
                raise LabcalError('METHOD_ADOPTION_INVALID', '采用记录已被其他版本替代。')
        if not db.execute('SELECT 1 FROM method_adoptions WHERE id=?', (key,)).fetchone():
            db.execute('INSERT INTO method_adoptions VALUES (?,?,?,?)', (key, canonical_json(value), digest, utc_now()))
            store.event(db, 'method_adopted_by_user', key, {'record_hash': digest})
    return {'id': key, 'payload': value, 'record_hash': digest}


def list_adoptions(workspace, lab_id, *, include_history=False):
    with store_for(workspace, lab_id).connect() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM method_adoptions ORDER BY rowid')]
    values = []
    for row in rows:
        payload = json.loads(row['record'])
        if content_hash(payload) != row['record_hash'] or payload['lab_id'] != lab_id:
            raise LabcalError('METHOD_ADOPTION_CHANGED', '采用记录摘要或实验室归属不一致。')
        values.append({'id': row['id'], 'payload': payload, 'record_hash': row['record_hash']})
    replaced = {r['payload'].get('supersedes') for r in values}
    return [r for r in values if include_history or r['id'] not in replaced]
