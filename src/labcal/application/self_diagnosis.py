"""Chat-first certificate self diagnosis. No plan, baseline or acceptance service imports."""
import json
from pathlib import Path
from ..adapters.layout import processing_directory, output_directory, segment
from ..adapters.pdf_process import render_page_isolated, annotate_pdf_isolated
from ..domain.self_diagnosis import candidate_fields, automatic_checks, recalculate, GROUPS
from ..domain.contracts import validate_contract
from ..errors import LabcalError
from ..util import write_json, read_json, identifier, content_hash, file_hash, canonical_json, utc_now
from .campaigns import require_item, get_campaign
from .certificates import get_extraction, validate_location
from .sources import verify_source_integrity

ENGINE='certificate-self-diagnosis-1.0'


def prepare(workspace,lab_id,campaign_id,extraction_ids):
    get_campaign(workspace,lab_id,campaign_id,writable=True)
    if not extraction_ids or len(set(extraction_ids))!=len(extraction_ids):
        raise LabcalError('SELF_INPUT_INVALID','请选择不重复的证书提取版本。')
    task={'schema_version':'1.0','engine':ENGINE,'lab_id':lab_id,'campaign_id':campaign_id,'task_id':identifier('SELF'),
          'mode':'certificate_self_diagnosis','created_at':utc_now(),'technical_approval':False,'documents':[]}
    for key in extraction_ids:
        require_item(workspace,lab_id,campaign_id,'extraction',key)
        ext=get_extraction(workspace,lab_id,key);payload=ext['payload'];source=verify_source_integrity(workspace,lab_id,payload['source_id'])
        task['documents'].append({'extraction_id':key,'extraction_hash':ext['payload_hash'],'source_id':source['id'],'source_hash':source['sha256'],
         'name':source['name'],'document_kind':payload['document_kind'],'pages':len(payload['pages']),
         'fields':candidate_fields(payload),'automatic_checks':automatic_checks(payload)})
    folder=processing_directory(workspace.lab_store(lab_id),'certificates',task['task_id'])
    write_json(folder/'task.json',{'payload':task,'payload_hash':content_hash(task)})
    write_json(folder/'record-template.json',{'task_hash':content_hash(task),'author':'Codex 智能体','checks':[]})
    return {'task_id':task['task_id'],'task_hash':content_hash(task),'directory':str(folder),'documents':len(extraction_ids),'state':'awaiting_page_review'}


def load_task(workspace,lab_id,campaign_id,task_id):
    folder=processing_directory(workspace.lab_store(lab_id),'certificates',segment(task_id));fixed=read_json(folder/'task.json');task=fixed['payload']
    if content_hash(task)!=fixed['payload_hash']:raise LabcalError('SELF_TASK_CHANGED','诊断输入快照已变更。')
    if task['lab_id']!=lab_id or task['campaign_id']!=campaign_id:raise LabcalError('SELF_SCOPE_MISMATCH','诊断任务不属于当前实验室与批次。')
    for document in task['documents']:
        require_item(workspace,lab_id,campaign_id,'extraction',document['extraction_id'])
        ext=get_extraction(workspace,lab_id,document['extraction_id'])
        if ext['payload_hash']!=document['extraction_hash']:raise LabcalError('SELF_INPUT_CHANGED','证书提取快照发生变化。')
    return folder,fixed


def page(workspace,lab_id,campaign_id,task_id,extraction_id,page_number):
    folder,fixed=load_task(workspace,lab_id,campaign_id,task_id)
    document=next((d for d in fixed['payload']['documents'] if d['extraction_id']==extraction_id),None)
    if not document or not 1<=page_number<=document['pages']:raise LabcalError('SELF_PAGE_INVALID','页面不属于本任务。')
    source=verify_source_integrity(workspace,lab_id,document['source_id'])
    target=folder/'pages'/f'{segment(extraction_id)}-P{page_number}.png'
    if not target.exists():
        data=render_page_isolated(source['absolute_path'],page_number);target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream:stream.write(data)
    return {'path':str(target),'image_sha256':file_hash(target),'page':page_number}


def finish(workspace,lab_id,campaign_id,task_id,record,*,annotate=True):
    folder,fixed=load_task(workspace,lab_id,campaign_id,task_id);task=fixed['payload']
    get_campaign(workspace,lab_id,campaign_id,writable=True);validate_contract('self-diagnosis-record.v1',record)
    if record['task_hash']!=fixed['payload_hash']:raise LabcalError('SELF_INPUT_CHANGED','核查结果对应另一输入快照。')
    if not record['author'].strip():raise LabcalError('SELF_AUTHOR_REQUIRED','请明确实际执行的智能体名称。')
    documents={d['extraction_id']:d for d in task['documents']};checks=[]
    for i,item in enumerate(record['checks'],1):
        if item['extraction_id'] not in documents:raise LabcalError('SELF_SCOPE_MISMATCH','核查结果含任务外证书。')
        ext=get_extraction(workspace,lab_id,item['extraction_id'])['payload']
        if item['state'] in {'confirmed_error','no_anomaly_detected'} and not item['evidence']:
            raise LabcalError('SELF_EVIDENCE_REQUIRED','明确判断需有原页证据。')
        validate_page_evidence(ext,folder,item['extraction_id'],item['evidence'])
        result={**item,'id':f'SD-{i:03d}','origin':'agent_page_review'}
        if 'calculation' in item:
            result['recalculation']=recalculate(item['calculation'])
            if item['state']=='no_anomaly_detected' and not result['recalculation']['rounding_compatible'] or item['state']=='confirmed_error' and result['recalculation']['rounding_compatible']:
                raise LabcalError('SELF_CALCULATION_CONFLICT','提交结论与复算结果不一致。')
        checks.append(result)
    # Missing groups stay explicitly unchecked, per document.
    for key in documents:
        for group,label in GROUPS.items():
            if not any(c['extraction_id']==key and c['group']==group for c in checks):
                checks.append({'id':f'SD-{len(checks)+1:03d}','extraction_id':key,'group':group,'item':label,'state':'not_checked',
                  'reason':'本次未提交该组核查记录。','basis':'本次检查范围','suggestion':'需要时继续核查。','evidence':[],'origin':'scope_record'})
    result={'schema_version':'1.0','task':task,'task_hash':fixed['payload_hash'],'author':record['author'],'checks':checks,
            'technical_approval':False,'note':'智能体报告自身诊断，不构成人员核验、设备用途判定或技术批准。'}
    store=workspace.lab_store(lab_id);run_id=identifier('RUN');output=output_directory(store,run_id,'certificate_self_diagnosis');output.mkdir(parents=True)
    write_json(output/'diagnosis.json',result)
    states={'confirmed_error':'确认存在的问题','candidate_concern':'待核实疑点','no_anomaly_detected':'已检查范围未发现异常','unable_to_check':'无法判断','not_applicable':'不适用','not_checked':'未检查'}
    md=['# 证书自身诊断','',result['note'],'',f'任务：{task_id}；执行：{record["author"]}。','']
    annotated_pdfs=[]
    for key,document in documents.items():
        md.extend(['## '+document['name'],''])
        for c in (c for c in checks if c['extraction_id']==key):
            md.extend([f"### {c['id']} {c['item']}｜{states[c['state']]}",'',c['reason'],'','依据：'+c['basis'],'','建议：'+c['suggestion'],''])
            for loc in c['evidence']:md.extend([f"第{loc['page']}页：{loc['quote']}",''])
            if c.get('recalculation'):md.extend(['复算：'+json.dumps(c['recalculation'],ensure_ascii=False),''])
        marks=[]
        for c in checks:
            if c['extraction_id']!=key or c['group']=='filing' or c['state'] not in {'confirmed_error','candidate_concern'}:continue
            for loc in c['evidence']:
                marks.append({'id':c['id'],'title':c['item'],'message':c['reason'],'basis':c['basis'],'suggestion':c['suggestion'],
                  'dimension':'certificate_self',
                  'certainty':'confirmed' if c['state']=='confirmed_error' else 'candidate','location':{**loc,'kind':'text'}})
        if annotate and marks:
            source=verify_source_integrity(workspace,lab_id,document['source_id'])
            mapping=annotate_pdf_isolated(source['absolute_path'],output/(key+'-annotated.pdf'),marks,style='callouts')
            annotated_pdfs.append({'extraction_id':key,'file':key+'-annotated.pdf','page_mapping':mapping})
    (output/'diagnosis.md').write_text('\n'.join(md),encoding='utf-8')
    manifest={'schema_version':'1.0','run_id':run_id,'kind':'certificate_self_diagnosis','lab_id':lab_id,'campaign_id':campaign_id,
              'task_id':task_id,'task_hash':fixed['payload_hash'],'record_hash':content_hash(record),'created_at':utc_now(),
              'annotated_pdfs':annotated_pdfs,
              'artifacts':[{'name':p.name,'sha256':file_hash(p)} for p in sorted(output.iterdir())]}
    write_json(output/'manifest.json',manifest)
    with store.connect() as db:
        db.execute('INSERT INTO runs VALUES (?,?,?,?)',(run_id,manifest['kind'],canonical_json(manifest),utc_now()))
        store.event(db,'certificate_self_diagnosis_exported',run_id,{'task_id':task_id,'manifest_hash':content_hash(manifest)})
    return {'run_id':run_id,'directory':str(output),'checks':len(checks),'technical_approval':False}


def validate_page_evidence(ext, folder, extraction_id, evidence):
    """Shared original-page evidence validation; no personnel approval."""
    for loc in evidence:
        validate_location(ext,{**loc,'line_ids':[]})
        if loc['origin']=='visual_page_read':
            image=folder/'pages'/f"{segment(extraction_id)}-P{loc['page']}.png"
            if not image.is_file() or file_hash(image)!=loc.get('image_sha256'):
                raise LabcalError('SELF_IMAGE_REQUIRED','请先读取该原页并引用渲染图摘要。')
        else:
            compact=lambda s:''.join(s.split())
            lines=ext['pages'][loc['page']-1]['lines']
            native=[l for l in lines if l.get('method')=='pdf_text' and l['bbox'][0]<loc['bbox'][2] and l['bbox'][2]>loc['bbox'][0] and l['bbox'][1]<loc['bbox'][3] and l['bbox'][3]>loc['bbox'][1]]
            if compact(loc['quote']) not in compact(' '.join(l['text'] for l in native)):
                raise LabcalError('SELF_QUOTE_MISMATCH','原文与该位置文字层不符；扫描或视觉转录需引用原页图。')
