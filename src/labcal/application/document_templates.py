"""Laboratory-owned Word originals and fixed preparation for confirmation forms."""
import json
from pathlib import Path
import shutil
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from ..adapters.layout import segment, processing_directory, template_directory
from ..domain.contracts import validate_contract
from ..errors import LabcalError
from ..util import canonical_json, content_hash, file_hash, identifier, utc_now, write_json, read_json, ensure_inside
from .campaigns import get_campaign, attach_item, require_item
from .certificates import get_extraction
from .sources import import_source, verify_source_integrity
from .workspace_catalog import require_working_laboratory


def inspect_docx(path):
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    try:
        with ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > 50_000_000:
                raise LabcalError('TEMPLATE_TOO_LARGE', '模板解压后体积过大。')
            root = ET.fromstring(z.read('word/document.xml'))
        text = lambda cell: '\n'.join(''.join(p.itertext()) for p in cell.findall('w:p', ns))
        tables = [{'index': i, 'rows': [[text(c) for c in row.findall('w:tc', ns)] for row in t.findall('w:tr', ns)]}
                  for i, t in enumerate(root.findall('.//w:tbl', ns))]
        return {'tables': tables, 'content_controls': len(root.findall('.//w:sdt', ns)),
                'sections': len(root.findall('.//w:sectPr', ns))}
    except (BadZipFile, KeyError, ET.ParseError) as exc:
        raise LabcalError('TEMPLATE_DOCX_INVALID', '文件不是可读取的Word模板。') from exc


def register(workspace, lab_id, campaign_id, path, role, title, reason):
    require_working_laboratory(workspace, lab_id)
    get_campaign(workspace, lab_id, campaign_id, writable=True)
    role = segment(role); path = Path(path).resolve()
    if path.suffix.lower() != '.docx' or not title.strip() or not reason.strip():
        raise LabcalError('TEMPLATE_INPUT_INVALID', '请提供DOCX模板、名称及用户选用依据。')
    inventory = inspect_docx(path)
    source = import_source(workspace, path, kind='supporting_document', lab_id=lab_id,
                           metadata={'document_role': role, 'user_selection': reason})
    store = workspace.lab_store(lab_id)
    destination = template_directory(store, role, source['id']) / path.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with destination.open('xb') as stream, path.open('rb') as original: shutil.copyfileobj(original, stream)
    if file_hash(destination) != source['sha256']:
        raise LabcalError('TEMPLATE_ARCHIVE_CHANGED', '模板查阅原件摘要不一致，未覆盖。')
    payload = {'lab_id': lab_id, 'role': role, 'title': title, 'source_id': source['id'],
               'source_hash': source['sha256'], 'archive_path': str(destination.relative_to(store.directory)),
               'inventory': inventory, 'user_statement': reason, 'technical_approval': False}
    digest = content_hash(payload); key = 'TPL-' + digest[:24]
    payload = {'id': key, **payload}; validate_contract('document-template.v1', payload)
    record = {'payload': payload, 'payload_hash': content_hash(payload)}
    version_key = 'document_template_version:' + key
    with store.connect() as db:
        db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', (version_key, canonical_json(record)))
        old = db.execute('SELECT value FROM meta WHERE key=?', ('document_template:' + role,)).fetchone()
        if not old or old[0] != key:
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('document_template:' + role, key))
            store.event(db, 'document_template_selected', key, {'record_hash': record['payload_hash'],
                'campaign_id': campaign_id, 'previous_id': old[0] if old else None})
    index = destination.parent / (key + '.json')
    if not index.exists(): write_json(index, record)
    attach_item(workspace, lab_id, campaign_id, 'source', source['id'])
    return get_template(workspace, lab_id, role)


def get_template(workspace, lab_id, role, *, template_id=None):
    store = workspace.lab_store(lab_id); role = segment(role)
    with store.connect() as db:
        if not template_id:
            selected = db.execute('SELECT value FROM meta WHERE key=?', ('document_template:' + role,)).fetchone()
            if not selected: raise LabcalError('TEMPLATE_NOT_SELECTED', '当前实验室尚未选用该类模板。')
            template_id = selected[0]
        row = db.execute('SELECT value FROM meta WHERE key=?', ('document_template_version:' + segment(template_id),)).fetchone()
    if not row: raise LabcalError('TEMPLATE_NOT_FOUND', '当前实验室没有该模板版本。')
    record = json.loads(row[0]); payload = record['payload']
    validate_contract('document-template.v1', payload)
    if content_hash(payload) != record['payload_hash'] or payload['lab_id'] != lab_id or payload['role'] != role:
        raise LabcalError('TEMPLATE_CHANGED', '模板记录摘要或实验室范围不一致。')
    source = verify_source_integrity(workspace, lab_id, payload['source_id'])
    archive = ensure_inside(store.directory / payload['archive_path'], store.directory / 'templates')
    if source['sha256'] != payload['source_hash'] or not archive.is_file() or file_hash(archive) != payload['source_hash']:
        raise LabcalError('TEMPLATE_CHANGED', '模板原件或查阅副本摘要不一致。')
    if read_json(archive.parent / (payload['id'] + '.json')) != record:
        raise LabcalError('TEMPLATE_CHANGED', '模板版本索引与固定记录不一致。')
    return {**record, 'path': str(archive)}


def prepare_confirmation(workspace, lab_id, campaign_id, extraction_id, equipment_label, *, context_sources=(), fresh_review=False, equipment_key=None, replaces_task=None, replacement_reason=None):
    get_campaign(workspace, lab_id, campaign_id, writable=True)
    require_item(workspace, lab_id, campaign_id, 'extraction', extraction_id)
    template = get_template(workspace, lab_id, 'calibration-confirmation')
    extraction = get_extraction(workspace, lab_id, extraction_id)
    source = verify_source_integrity(workspace, lab_id, extraction['payload']['source_id'])
    if not equipment_label.strip(): raise LabcalError('EQUIPMENT_LABEL_REQUIRED', '请明确本次确认的设备。')
    from .methods import list_adoptions
    store = workspace.lab_store(lab_id)
    if bool(replaces_task) != bool(replacement_reason):
        raise LabcalError('CONFIRMATION_REPLACEMENT_EVIDENCE', '明确替代任务时须同时记录替代原因及身份依据。')
    if replaces_task:
        with store.connect() as db:
            old_meta = db.execute('SELECT value FROM meta WHERE key=?', ('equipment_confirmation:' + replaces_task,)).fetchone()
        old_task = verify_confirmation(store, json.loads(old_meta[0]))['task'] if old_meta else None
        if not equipment_key or not old_task or old_task.get('equipment_key') != equipment_key or not replacement_reason.strip():
            raise LabcalError('CONFIRMATION_REPLACEMENT_SCOPE', '替代须引用本实验室同一明确设备的确认；不从同名报告推断身份。')
    coverage_scope = {}
    for adoption in list_adoptions(workspace, lab_id):
        a = adoption['payload']; source_record = verify_source_integrity(workspace, None, a['source_id'])
        coverage_scope[a['source_id']] = {'source_id': a['source_id'], 'source_hash': source_record['sha256'],
            'standard': a['standard_code'] + '—' + a['edition'], 'relationship': 'laboratory_adopted'}
    # Reuse the same equipment's recorded reference context across later years.
    # This is a review scope, never automatic adoption of those standards.
    previous = []
    if not fresh_review:
        with store.connect() as db:
            previous = [json.loads(r[0]) for r in db.execute("SELECT value FROM meta WHERE key LIKE 'equipment_confirmation:%' ORDER BY rowid")]
    prior_sources = []
    for meta in previous:
        if meta['equipment_label'] == equipment_label:
            old = verify_confirmation(store, meta)
            prior_sources.extend(m['source_id'] for m in old['task'].get('coverage_scope', []))
    business_context = None
    if equipment_key:
        from .business_context import assemble_context
        business_context = assemble_context(workspace, lab_id, equipment_key, campaign_id)
        prior_sources.extend(m['payload']['source_id'] for m in business_context['methods'].values())
    for sid in list(context_sources) + prior_sources:
        if sid in coverage_scope: continue
        source_record = verify_source_integrity(workspace, None, sid)
        if source_record['kind'] != 'test_method_standard':
            raise LabcalError('CONFIRMATION_CONTEXT_INVALID', '关联范围只能引用检测方法标准原件。')
        coverage_scope[sid] = {'source_id': sid, 'source_hash': source_record['sha256'],
            'standard': source_record.get('code') or sid, 'relationship': 'reference_context_only'}
    task_id = identifier('CONF')
    task = {'id': task_id, 'lab_id': lab_id, 'campaign_id': campaign_id,
        'equipment_label': equipment_label, 'coverage_scope': list(coverage_scope.values()), 'extraction_id': extraction_id,
        'extraction_hash': extraction['payload_hash'], 'source_id': source['id'], 'source_hash': source['sha256'],
        'template_id': template['payload']['id'], 'template_hash': template['payload_hash'],
        'template_source_hash': template['payload']['source_hash'], 'created_at': utc_now(),
        'state': 'awaiting_evidence_and_form_filling', 'technical_approval': False, 'review_version': 2, 'quality_version': 1, 'use_basis_version': 1, 'basis_role_version': 1, 'reasoning_version': 1, 'uncertainty_review_version': 1}
    if fresh_review:
        task['input_policy'] = 'originals_and_direct_user_evidence'
    if business_context is not None:
        task.update(equipment_key=equipment_key, context_hash=content_hash(business_context))
    if replaces_task:
        task.update(replaces_task=replaces_task, replacement_reason=replacement_reason)
    validate_contract('confirmation-task.v1', task)
    folder = processing_directory(workspace.lab_store(lab_id), 'certificates', task_id)
    if business_context is not None:
        write_json(folder / 'business-context.json', business_context)
    write_json(folder / 'confirmation-task.json', {'payload': task, 'payload_hash': content_hash(task)})
    return {'task': task, 'directory': str(folder), 'reference_path': template['path'],
            'business_context_path': str(folder / 'business-context.json') if business_context else None,
            'next_step': '先为每条规则填写basis_role和正式表basis_scope_quote，区分适用验收要求、器具技术参考和特性记录；参考未满足时给出reference_impact_quote。先核清要求含义、比较量、规则依据和U作用，在technical_review.rules中完成reasoning及uncertainty_review；执行confirmation analyze，无阻断后才生成Word。每条U评价摘录正式栏中的依据、计算或不计算的理由、判断。需要采用JJF 1094时，按docs/uncertainty-decision-rules.md区分适用路径，保留同量同单位的逐点输入、MPEV来源、U/MPEV关系及后续判据；不硬套1/3或切换既定规则。证据不足不自动限定使用。完成证书自身诊断，technical_review关联self_run_id、certificate_items逐项原页到附页映射，规则明确dimension/state/judgment_quote；执行既有要求类型、覆盖及发布检查。填写Word与结果/修正选项，confirmation format另存三维分色副本；全部页面渲染后finish。无独立判限特性中性记录，无据周期及人员签署留空。详见docs/equipment-confirmation-forms.md。'}


def review_confirmation(workspace, lab_id, campaign_id, task_id, record, *, require_reasoning=False):
    """Shared pre-document and publication gate; read-only, never personnel approval."""
    get_campaign(workspace, lab_id, campaign_id, writable=True)
    store = workspace.lab_store(lab_id)
    fixed = read_json(processing_directory(store, 'certificates', segment(task_id)) / 'confirmation-task.json')
    task = fixed['payload']
    validate_contract('confirmation-task.v1', task)
    if content_hash(task) != fixed['payload_hash'] or task['lab_id'] != lab_id or task['campaign_id'] != campaign_id:
        raise LabcalError('CONFIRMATION_SCOPE_CHANGED', '确认任务摘要或实验室批次不一致。')
    require_item(workspace, lab_id, campaign_id, 'extraction', task['extraction_id'])
    template = get_template(workspace, lab_id, 'calibration-confirmation', template_id=task['template_id'])
    extraction = get_extraction(workspace, lab_id, task['extraction_id'])
    source = verify_source_integrity(workspace, lab_id, task['source_id'])
    if (template['payload_hash'] != task['template_hash'] or
        extraction['payload_hash'] != task['extraction_hash'] or source['sha256'] != task['source_hash']):
        raise LabcalError('CONFIRMATION_INPUT_CHANGED', '固定模板、证书或提取版本发生变化。')
    validate_contract('confirmation-record.v1', record)
    if not record.get('publication_review'):
        raise LabcalError('CONFIRMATION_REVIEW_REQUIRED', '请先完成分析记录及结果/修正/周期选择依据，再生成正式表。')
    validate_coverage(workspace, task, record)
    if task.get('context_hash'):
        from .business_context import validate_context_coverage
        snapshot = read_json(processing_directory(store, 'certificates', task_id) / 'business-context.json')
        if content_hash(snapshot) != task['context_hash'] or snapshot['lab_id'] != lab_id:
            raise LabcalError('CONTEXT_CHANGED', '任务上下文快照摘要或实验室归属不一致。')
        validate_context_coverage(snapshot, record)
        from .business_context import assemble_context
        from .confirmation_updates import context_changes
        changed = context_changes(snapshot, assemble_context(workspace, lab_id, task['equipment_key']))
        if changed:
            raise LabcalError('CONTEXT_REVIEW_CHANGED', '准备后实际用途、配置或适用依据发生变化；请按新上下文建立任务。', {'changes': changed})
    validate_technical_review(record, required=task.get('review_version') == 2)
    checks = None
    if task.get('quality_version') == 1:
        from .confirmation_quality import validate_quality
        from ..domain.confirmation_basis import validate_method_basis, validate_basis_roles
        checks = validate_quality(workspace, task, record, extraction['payload'])
        if task.get('use_basis_version') == 1:
            validate_method_basis(task, record)
        validate_basis_roles(record, required=task.get('basis_role_version') == 1)
    if require_reasoning or task.get('reasoning_version') == 1:
        from ..domain.confirmation_reasoning import reasoning_issues
        issues = reasoning_issues(record)
        if issues:
            raise LabcalError('CONFIRMATION_ANALYSIS_BLOCKED', '技术分析尚有矛盾或依据未核清，停止生成正式确认表。', {'issues': issues})
    from ..domain.confirmation_uncertainty import uncertainty_issues
    issues = uncertainty_issues(record, required=task.get('uncertainty_review_version') == 1)
    if issues:
        raise LabcalError('CONFIRMATION_UNCERTAINTY_BLOCKED', '不确定度评价缺少依据、比较或结论，或与分支复算不一致。', {'issues': issues})
    validate_publication_record(record)
    return task, checks


def analyze_confirmation(workspace, lab_id, campaign_id, task_id, record):
    """Explain blockers before Word exists; do not register business results."""
    from ..util import content_hash
    try:
        task, _ = review_confirmation(workspace, lab_id, campaign_id, task_id, record, require_reasoning=True)
    except LabcalError as exc:
        return {'status': 'analysis_required', 'ready_for_document': False,
                'issues': exc.details.get('issues', [exc.as_dict()]),
                'technical_approval': False, 'semantic_verification': False}
    return {'status': 'ready_for_document', 'ready_for_document': True, 'issues': [],
            'task_id': task['id'], 'record_hash': content_hash(record),
            'reasoning_gate': task.get('reasoning_version'),
            'technical_approval': False, 'semantic_verification': False,
            'note': '结构与声明一致性检查通过；原页含义、依据适用性和数值仍须执行智能体实际核实。'}


def finish_confirmation(workspace, lab_id, campaign_id, task_id, record, document, appendix_table, *, annotations=None, annotation_manifest=None):
    """Register an agent-authored form and its shared Excel projection; no approval."""
    from ..domain.document_template_contracts import ROW_FIELDS
    task, self_checks = review_confirmation(workspace, lab_id, campaign_id, task_id, record)
    store = workspace.lab_store(lab_id)
    document = Path(document).resolve()
    inventory = inspect_docx(document)
    if appendix_table < 0 or appendix_table >= len(inventory['tables']):
        raise LabcalError('CONFIRMATION_TABLE_REQUIRED', '请指定实际附页表格索引。')
    actual = inventory['tables'][appendix_table]['rows'][1:]
    expected = [[r[key] for key in ROW_FIELDS] for r in record['rows']]
    if actual != expected:
        raise LabcalError('CONFIRMATION_OUTPUT_MISMATCH', 'Word附页与共享结果记录不一致，停止发布。')
    if any(word in cell for row in actual for cell in row for word in ['待补', '待确认']):
        raise LabcalError('CONFIRMATION_PROCESS_TEXT', '最终确认表附页含过程用语，请先完成分析。')
    validate_publication_review(record, document, appendix_table)
    if task.get('quality_version') == 1:
        from ..adapters.confirmation_style import validate_colours
        validate_colours(document, record, appendix_table, self_checks=self_checks)
    payload = {'task': task, 'record': record, 'technical_approval': False, 'personnel_signed': False}
    digest = content_hash(payload)
    from .confirmation_delivery import annotation_inputs, write_delivery, verify_delivery
    extras = annotation_inputs(task, record, document, annotations, annotation_manifest)
    names = [document.name, 'confirmation-result.json'] + [p.name for _, p in extras]
    if task.get('context_hash'): names.append('business-context.json')
    if len(names) != len(set(names)) or 'delivery-manifest.json' in names:
        raise LabcalError('DELIVERY_NAME_COLLISION', '交付文件存在重名，请在发布前调整副本文件名。')
    with store.connect() as db:
        existing = db.execute('SELECT value FROM meta WHERE key=?', ('equipment_confirmation:' + task_id,)).fetchone()
        if existing:
            old = json.loads(existing[0])
            if old['record_hash'] != digest or file_hash(document) != old['document_hash']:
                raise LabcalError('CONFIRMATION_IMMUTABLE', '确认任务已有交付版本，请建立新任务修订。')
            verify_confirmation(store, old)
            if extras:
                fixed = verify_delivery(store, old)
                roles = {a['role']: a['sha256'] for a in fixed['artifacts']} if fixed else {}
                if any(roles.get(role) != file_hash(path) for role, path in extras):
                    raise LabcalError('CONFIRMATION_IMMUTABLE', '已登记任务的批注不同或尚未登记；请建立新版本。')
            return {**old, 'reused': True}
        output = store.directory / 'outputs' / 'certificates' / task_id
        output.mkdir(parents=True, exist_ok=True)
        target = output / document.name
        if target.exists():
            if file_hash(target) != file_hash(document):
                raise LabcalError('CONFIRMATION_IMMUTABLE', '目标文件已存在且内容不同。')
        else:
            with target.open('xb') as stream, document.open('rb') as original: shutil.copyfileobj(original, stream)
        result = output / 'confirmation-result.json'
        if result.exists():
            if read_json(result) != payload: raise LabcalError('CONFIRMATION_IMMUTABLE', '结果清单已有不同内容。')
        else: write_json(result, payload)
        metadata = {'task_id': task_id, 'campaign_id': campaign_id, 'extraction_id': task['extraction_id'],
            'equipment_label': task['equipment_label'], 'record_hash': digest,
            'document_path': str(target.relative_to(store.directory)), 'document_hash': file_hash(target),
            'result_path': str(result.relative_to(store.directory)), 'result_hash': file_hash(result),
            'technical_approval': False}
        context = processing_directory(store, 'certificates', task_id) / 'business-context.json' if task.get('context_hash') else None
        metadata.update(write_delivery(store, task, output, target, result, extras, context))
        db.execute('INSERT INTO meta VALUES (?,?)', ('equipment_confirmation:' + task_id, canonical_json(metadata)))
        store.event(db, 'equipment_confirmation_recorded', task_id, metadata)
    return {**metadata, 'reused': False}


def verify_confirmation(store, metadata):
    from .confirmation_delivery import verify_delivery
    verify_delivery(store, metadata)
    for name in ['document', 'result']:
        path = ensure_inside(store.directory / metadata[name + '_path'], store.directory / 'outputs' / 'certificates')
        if not path.is_file() or file_hash(path) != metadata[name + '_hash']:
            raise LabcalError('CONFIRMATION_OUTPUT_CHANGED', '确认表或共享结果记录已发生外部修改，停止汇总。')
    payload = read_json(store.directory / metadata['result_path'])
    if content_hash(payload) != metadata['record_hash']:
        raise LabcalError('CONFIRMATION_OUTPUT_CHANGED', '确认结果摘要不一致。')
    return payload


def validate_publication_record(record):
    """Check text references and aggregate choices before a Word file exists."""
    from ..domain.document_template_contracts import REVIEW_QUOTES
    review = record.get('publication_review')
    if review is None:
        raise LabcalError('CONFIRMATION_REVIEW_REQUIRED', '发布前请补齐逐用途判断链和结果/周期/修正选择检查。')
    numbers = [r['row_number'] for r in review['rows']]
    if len(numbers) != len(set(numbers)) or set(numbers) != set(range(1, len(record['rows']) + 1)):
        raise LabcalError('CONFIRMATION_REVIEW_ROWS', '每一附页行必须有且仅有一条判断链检查。')
    for check in review['rows']:
        row = record['rows'][check['row_number'] - 1]
        if any(not row[key].strip() or row[key] not in row['parameter_results'] for key in ['function', 'purpose']):
            raise LabcalError('CONFIRMATION_USE_TITLE', '附页首列应显示该行功能及用途名称。')
        for key, column in REVIEW_QUOTES.items():
            quote = check[key]
            if not quote.strip() or quote not in row[column]:
                raise LabcalError('CONFIRMATION_REASONING_QUOTE', f'第{check["row_number"]}行{key}须引用对应输出列中的实际评价文字。')
    selections = review['selections']
    active = [r['state'] for r in record['rows'] if r['state'] not in {'not_assigned', 'reported_characteristic'}]
    result = selections['result']['choice']
    if result == 'meets_declared_uses' and (not active or any(s != 'meets_requirement' for s in active)):
        raise LabcalError('CONFIRMATION_SELECTION_CONFLICT', '存在不满足或证据不足的评价行，结果不得选择全部声明用途满足。')
    if result == 'does_not_meet' and 'does_not_meet' not in active:
        raise LabcalError('CONFIRMATION_SELECTION_CONFLICT', '未有不满足的评价行，不能把证据不足写为不符合。')
    corrections = {r['correction'] for r in review['rows']
                   if record['rows'][r['row_number'] - 1]['state'] != 'not_assigned'}
    correction = selections['correction']['choice']
    if (correction == 'not_required' and corrections != {'not_required'} or
        correction == 'required' and corrections != {'required'} or
        correction == 'mixed' and not {'required', 'not_required'} <= corrections):
        raise LabcalError('CONFIRMATION_SELECTION_CONFLICT', '首页修正选择与逐用途修正评价不一致。')
    if correction == 'undetermined' and corrections and 'not_evaluated' not in corrections:
        raise LabcalError('CONFIRMATION_SELECTION_CONFLICT', '修正评价已有结论，请填写相应选择项。')
    if result == 'undetermined' and active and all(s == 'meets_requirement' for s in active):
        raise LabcalError('CONFIRMATION_SELECTION_CONFLICT', '声明用途均已满足，请填写相应结果选择项。')


def validate_publication_review(record, document, appendix_table):
    """Check reasoning coverage and visible selections, not technical validity.

    Older archived records remain readable; every new publication needs review.
    All quotes refer to existing output text, with no new editable limits.
    """
    validate_publication_record(record)
    selections = record['publication_review']['selections']
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    with ZipFile(document) as z:
        root = ET.fromstring(z.read('word/document.xml'))
    # Require selections outside the appendix; a quote copied into analysis is not a selection.
    tables = root.findall('.//w:tbl', ns)
    excluded = {id(p) for p in tables[appendix_table].findall('.//w:p', ns)}
    paragraphs = [''.join(t.text or '' for t in p.findall('.//w:t', ns))
                  for p in root.findall('.//w:p', ns) if id(p) not in excluded]
    marks = '☑☒√✓✔'
    for name, selection in selections.items():
        if not selection['basis'].strip():
            raise LabcalError('CONFIRMATION_SELECTION_BASIS', '选择项须说明依据；周期无依据时记录原因并留空。')
        unresolved = selection['choice'] in ['unspecified', 'undetermined']
        for quote in selection['document_texts']:
            if not quote.strip() or quote not in paragraphs:
                raise LabcalError('CONFIRMATION_SELECTION_TEXT', f'{name}须引用Word主表/正文中完整的选择项段落。')
            marked = sum(quote.count(mark) for mark in marks)
            if marked != (0 if unresolved else 1):
                raise LabcalError('CONFIRMATION_SELECTION_MARK', f'{name}应明确单选；无依据的项目保留未选。')


def validate_technical_review(record, *, required=False):
    """Reject known category errors; referenced evidence still requires human/agent reading.

    A reported characteristic is neutral, never a substitute for a pass or a way
    to suppress an unresolved mandatory requirement. Historical files stay intact.
    """
    review = record.get('technical_review')
    if review is None:
        if required:
            raise LabcalError('CONFIRMATION_TECHNICAL_REQUIRED', '新任务需逐项记录要求类型、比较对象、判定规则及直接用途证据。')
        return
    if any(not review[k].strip() for k in ['use_evidence', 'decision_evidence']):
        raise LabcalError('CONFIRMATION_TECHNICAL_EVIDENCE', '用途及规则依据不能为空。')
    covered = set(); modes = {}
    for rule in review['rules']:
        n = rule['row_number']
        if n < 1 or n > len(record['rows']):
            raise LabcalError('CONFIRMATION_TECHNICAL_ROWS', '技术判断引用了不存在的附页行。')
        covered.add(n); row = record['rows'][n - 1]
        for key, column in [('requirement_quote', 'method_judgment'), ('comparison_quote', 'method_judgment'),
                            ('uncertainty_quote', 'uncertainty_evaluation')]:
            if not rule[key].strip() or rule[key] not in row[column]:
                raise LabcalError('CONFIRMATION_TECHNICAL_QUOTE', '技术检查必须摘录正式表中的要求、比较和U评价。')
        if not rule['source_reference'].strip():
            raise LabcalError('CONFIRMATION_TECHNICAL_EVIDENCE', '要求必须有来源。')
        kind, quantity, mode = rule['requirement_kind'], rule['compared_quantity'], rule['decision_mode']
        group = rule['decision_group']
        if not group.strip() or (group in modes and modes[group] != mode):
            raise LabcalError('CONFIRMATION_RULE_INCONSISTENT', '同组结果须采用一致判定规则，不可选择性忽略U。')
        modes[group] = mode
        if rule['limit_basis'] == 'instrument_specification' and not rule.get('configuration_evidence', '').strip():
            raise LabcalError('CONFIRMATION_CONFIGURATION_REQUIRED', '按器具等级或规格取限值时须提供本设备的配置依据。')
        if rule['limit_basis'] == 'no_independent_limit' and mode != 'report_only':
            raise LabcalError('CONFIRMATION_CHARACTERISTIC_CONFLICT', '没有独立判限时应记录特性，不编造符合性判断。')
        if kind == 'resolution' and (quantity != 'resolution' or mode != 'specification_check'):
            raise LabcalError('CONFIRMATION_QUANTITY_MISMATCH', '分度值不能作为示值误差或U的允许界限。')
        if kind == 'weighing_precision' and not rule.get('interpretation_basis', '').strip():
            raise LabcalError('CONFIRMATION_INTERPRETATION_REQUIRED', '精确到的含义须结合条款说明，不能自动转换为MPE。')
        if kind == 'repeatability_range' and quantity != 'repeatability_range':
            raise LabcalError('CONFIRMATION_QUANTITY_MISMATCH', '重复性极差限值不能直接与标准偏差比较。')
        if kind == 'maximum_error' and quantity != 'indication_error':
            raise LabcalError('CONFIRMATION_QUANTITY_MISMATCH', '示值误差限值不能直接用于其他特性量。')
        if mode == 'guard_band' and kind != 'maximum_error':
            raise LabcalError('CONFIRMATION_QUANTITY_MISMATCH', '保护带比较须有明确误差限，不能用读数要求代替。')
        if row['state'] == 'reported_characteristic' and mode != 'report_only':
            raise LabcalError('CONFIRMATION_CHARACTERISTIC_CONFLICT', '特性记录行不得包含已执行的符合性判定。')
        if mode == 'report_only' and row['state'] not in {'reported_characteristic', 'insufficient_evidence', 'not_assigned'}:
            raise LabcalError('CONFIRMATION_CHARACTERISTIC_CONFLICT', '仅记录特性不能写成符合要求。')
    if covered != set(range(1, len(record['rows']) + 1)):
        raise LabcalError('CONFIRMATION_TECHNICAL_ROWS', '技术检查遗漏了附页项目。')


def validate_coverage(workspace, task, record):
    """Completeness for a frozen scope, not proof that every clause was understood."""
    if 'coverage_scope' not in task or 'coverage' not in record:
        raise LabcalError('CONFIRMATION_COVERAGE_REQUIRED', '先建立用途覆盖清单；旧任务需重新prepare以固定方法范围。')
    coverage = record['coverage']; expected = {s['source_id']:s for s in task['coverage_scope']}
    from .methods import list_adoptions
    adopted = {a['payload']['source_id'] for a in list_adoptions(workspace, task['lab_id'])}
    if adopted - set(expected):
        raise LabcalError('CONFIRMATION_COVERAGE_STALE', '方法采用范围已有新增或换版，请重新建立确认任务。')
    actual = [m['source_id'] for m in coverage['methods']]
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise LabcalError('CONFIRMATION_COVERAGE_MISSING', '方法范围未逐项处理，或包含任务外标准；停止发布。')
    referenced = set(); unresolved = False
    for method in coverage['methods']:
        source = verify_source_integrity(workspace, None, method['source_id'])
        if source['sha256'] != expected[source['id']]['source_hash']:
            raise LabcalError('CONFIRMATION_INPUT_CHANGED', '覆盖清单的标准原件摘要发生变化。')
        for item in method['items']:
            numbers = item['row_numbers']; disposition = item['disposition']
            if any(n > len(record['rows']) for n in numbers) or (disposition != 'not_applicable' and not numbers):
                raise LabcalError('CONFIRMATION_COVERAGE_ROW', '用途没有对应到确认表有效行。')
            referenced.update(numbers)
            if disposition == 'insufficient_evidence': unresolved = True
            for evidence in item['evidence']:
                image = workspace.reference_store().directory / 'processing' / 'methods' / source['id'] / 'pages' / f"P{evidence['page']}.png"
                if (not 1 <= evidence['page'] <= source['metadata'].get('pages', 0) or
                    not image.is_file() or file_hash(image) != evidence['image_sha256']):
                    raise LabcalError('CONFIRMATION_COVERAGE_EVIDENCE', '用途覆盖需要已渲染的标准原页及一致摘要。')
    if expected and referenced != set(range(1, len(record['rows']) + 1)):
        raise LabcalError('CONFIRMATION_COVERAGE_ROW', '确认表存在没有方法用途去向的行。')
    if any(r['state'] == 'insufficient_evidence' for r in record['rows']): unresolved = True
    if unresolved and coverage['claim'] == 'complete_for_declared_scope':
        raise LabcalError('CONFIRMATION_COVERAGE_UNRESOLVED', '仍有证据不足的用途，不能声明范围内完整确认。')
