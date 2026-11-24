"""Fixed delivery manifests for existing confirmation artifacts, not new judgments."""
from pathlib import Path
import shutil

from ..errors import LabcalError
from ..util import ensure_inside, file_hash, read_json, write_json


def annotation_inputs(task, record, document, annotations=None, annotation_manifest=None):
    if bool(annotations) != bool(annotation_manifest):
        raise LabcalError('DELIVERY_ANNOTATION_PAIR', '批注PDF须同时提供原件/任务关联清单。')
    if not annotations: return []
    pdf, manifest = Path(annotations).resolve(), Path(annotation_manifest).resolve()
    if not pdf.is_file() or pdf.suffix.lower() != '.pdf' or pdf.read_bytes()[:5] != b'%PDF-':
        raise LabcalError('DELIVERY_PDF_INVALID', '请提供实际批注PDF副本。')
    meta = read_json(manifest)
    digest = meta.get('output_hash', meta.get('pdf_hash'))
    name = meta.get('output', meta.get('pdf'))
    if (meta.get('confirmation_task') != task['id'] or meta.get('source_hash') != task['source_hash'] or
            name != pdf.name or digest != file_hash(pdf) or
            (meta.get('document_hash') and meta['document_hash'] != file_hash(document)) or
            (meta.get('self_run_id') and meta['self_run_id'] != record.get('technical_review', {}).get('self_run_id'))):
        raise LabcalError('DELIVERY_ANNOTATION_SCOPE', '批注副本不对应本次任务、原件、Word或自身诊断版本。')
    return [('annotations', pdf), ('annotation_manifest', manifest)]


def copy_immutable(source, target):
    source, target = Path(source), Path(target)
    digest = file_hash(source)
    if target.exists():
        if file_hash(target) != digest:
            raise LabcalError('CONFIRMATION_IMMUTABLE', '交付目标已有不同内容；请建立新确认版本。')
    else:
        with source.open('rb') as original, target.open('xb') as out: shutil.copyfileobj(original, out)
    if file_hash(target) != digest:
        raise LabcalError('DELIVERY_INPUT_CHANGED', '交付复制期间输入发生变化。')


def write_delivery(store, task, output, document, result, extras, context=None):
    files = [('document', Path(document)), ('result', Path(result))] + extras
    if context: files.append(('context', Path(context)))
    names = [p.name for _, p in files]
    if len(set(names)) != len(names) or 'delivery-manifest.json' in names:
        raise LabcalError('DELIVERY_NAME_COLLISION', '交付文件同名或与固定清单重名。')
    artifacts = []
    for role, source in files:
        target = output / source.name
        copy_immutable(source, target)
        artifacts.append({'role': role, 'name': target.name, 'sha256': file_hash(target)})
    manifest = {'task_id': task['id'], 'lab_id': task['lab_id'], 'campaign_id': task['campaign_id'],
                'source_hash': task['source_hash'], 'context_hash': task.get('context_hash'),
                'artifacts': artifacts, 'technical_approval': False}
    path = output / 'delivery-manifest.json'
    if path.exists():
        if read_json(path) != manifest:
            raise LabcalError('CONFIRMATION_IMMUTABLE', '固定交付清单已有不同内容。')
    else: write_json(path, manifest)
    return {'delivery_path': str(path.relative_to(store.directory)), 'delivery_hash': file_hash(path)}


def verify_delivery(store, metadata):
    if not metadata.get('delivery_path'): return None  # Unchanged historical outputs.
    path = ensure_inside(store.directory / metadata['delivery_path'], store.directory / 'outputs' / 'certificates')
    if not path.is_file() or file_hash(path) != metadata['delivery_hash']:
        raise LabcalError('DELIVERY_CHANGED', '交付清单缺失或摘要改变。')
    manifest = read_json(path)
    if manifest['task_id'] != metadata['task_id'] or manifest['lab_id'] != store.lab_id:
        raise LabcalError('DELIVERY_CHANGED', '交付清单归属不一致。')
    roles = set()
    for artifact in manifest['artifacts']:
        target = ensure_inside(path.parent / artifact['name'], path.parent)
        if artifact['role'] in roles or not target.is_file() or file_hash(target) != artifact['sha256']:
            raise LabcalError('DELIVERY_CHANGED', '交付文件缺失、角色重复或摘要改变。')
        roles.add(artifact['role'])
        if artifact['role'] in {'document', 'result'} and (
                str(target.relative_to(store.directory)) != metadata[artifact['role'] + '_path'] or
                artifact['sha256'] != metadata[artifact['role'] + '_hash']):
            raise LabcalError('DELIVERY_CHANGED', '交付文件与正式登记版本不一致。')
    if not {'document', 'result'} <= roles:
        raise LabcalError('DELIVERY_CHANGED', '交付清单缺少确认表或结果记录。')
    return manifest
