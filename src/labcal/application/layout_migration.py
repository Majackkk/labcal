"""Explicit, journalled directory migration; no business record or artifact rewrite."""
import json
from ..adapters.layout import initialize_layout, output_directory, processing_directory, LAYOUT_VERSION
from ..errors import LabcalError
from ..util import file_hash, identifier, write_json, ensure_inside


def organize_layout(workspace, lab_id, *, apply=False):
    store = workspace.lab_store(lab_id)
    with store.connect() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'report_jobs' in tables:
            if any(json.loads(r[0]).get('state') in {'queued', 'running'} for r in db.execute('SELECT record FROM report_jobs')):
                raise LabcalError('LAYOUT_JOBS_ACTIVE', '识别任务仍在运行，请结束后整理目录。')
        runs = {r['id']: r['kind'] for r in db.execute('SELECT id,kind FROM runs')}
    moves = []
    old = store.directory / 'exports'
    if old.exists():
        for child in sorted(old.iterdir()):
            if not child.is_dir() or child.name not in runs:
                raise LabcalError('LAYOUT_UNTRACKED_OUTPUT', '旧输出区存在未登记项目，请先核对。', {'name': child.name})
            # Resolve the destination without allowing the legacy lookup to select itself.
            from ..adapters.layout import LANES, segment
            if runs[child.name] not in LANES: raise LabcalError('LAYOUT_KIND_INVALID', '历史任务类型无法归类。')
            destination = store.directory / 'outputs' / LANES[runs[child.name]] / segment(child.name)
            moves.append((child, destination))
    old_runs = store.directory / 'runs'
    if old_runs.exists():
        for child in sorted(old_runs.iterdir()):
            lane = 'certificates' if child.name.startswith('INT-') else 'calibration' if runs.get(child.name) == 'plan_export' else 'legacy'
            moves.append((child, processing_directory(store, lane, child.name)))
    records=[]
    for source, target in moves:
        ensure_inside(source, store.directory); ensure_inside(target, store.directory)
        if target.exists(): raise LabcalError('LAYOUT_TARGET_EXISTS', '目标已存在，拒绝合并或覆盖。', {'path':str(target)})
        files = list(source.rglob('*')) if source.is_dir() else [source]
        if source.is_symlink() or any(p.is_symlink() for p in files):
            raise LabcalError('LAYOUT_SYMLINK', '迁移目录含符号链接，拒绝迁移。')
        records.append({'from':source.relative_to(store.directory).as_posix(), 'to':target.relative_to(store.directory).as_posix(),
                        'files':[{'path':p.relative_to(source).as_posix() if source.is_dir() else p.name, 'sha256':file_hash(p)} for p in files if p.is_file()]})
    result={'layout_version':LAYOUT_VERSION,'lab_id':lab_id,'applied':False,'moves':records,
            'preserved':['lab.sqlite','objects','campaigns','inbox'], 'technical_approval':False}
    if not apply: return result
    initialize_layout(store)
    journal = processing_directory(store, 'maintenance', identifier('LAYOUT')) / 'migration.json'
    write_json(journal, result)
    completed=[]
    try:
        for source,target in moves:
            target.parent.mkdir(parents=True,exist_ok=True); source.rename(target); completed.append((source,target))
        for record in records:
            target=store.directory/record['to']
            for item in record['files']:
                p=target/item['path'] if target.is_dir() else target
                if file_hash(p)!=item['sha256']: raise LabcalError('LAYOUT_HASH_MISMATCH','整理后文件摘要不一致。')
    except BaseException:
        for source,target in reversed(completed): target.rename(source)
        result['rolled_back']=True; write_json(journal,result,overwrite=True)
        raise
    for directory in [old,old_runs]:
        if directory.exists() and not any(directory.iterdir()): directory.rmdir()
    result['applied']=True; write_json(journal,result,overwrite=True)
    # Only rebuild disposable batch projections. Fixed records remain byte-for-byte unchanged.
    from .campaigns import list_campaigns, refresh_index
    if 'campaigns' in tables:
        for campaign in list_campaigns(workspace,lab_id): refresh_index(workspace,lab_id,campaign['id'])
    return {**result,'journal':str(journal)}
