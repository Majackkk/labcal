"""Workspace navigation, archive separation and scoped storage health."""
import json
from time import perf_counter
from ..domain.contracts import validate_contract
from ..errors import LabcalError
from ..util import canonical_json
from .campaigns import campaign_store, get_campaign, campaign_directory


def storage_role(workspace, lab_id):
    with workspace.lab_store(lab_id).connect() as db:
        row = db.execute("SELECT value FROM meta WHERE key='workspace_role'").fetchone()
    return json.loads(row[0])['role'] if row else 'unclassified'


def require_working_laboratory(workspace, lab_id):
    if storage_role(workspace, lab_id) != 'laboratory':
        raise LabcalError('ARCHIVE_READ_ONLY', '历史或未分类资料档案仅供查看；请进入实际实验室开展业务。')


def classify_storage(workspace, lab_id, role, reason):
    record = {'schema_version':'1.0', 'role':role, 'reason':reason}
    validate_contract('workspace-role.v1', record)
    if not reason.strip(): raise LabcalError('ROLE_REASON_REQUIRED', '请说明资料空间分类依据。')
    store = workspace.lab_store(lab_id)
    with store.connect() as db:
        old = db.execute("SELECT value FROM meta WHERE key='workspace_role'").fetchone()
        if old and json.loads(old[0]) == record: return record
        db.execute("INSERT OR REPLACE INTO meta VALUES ('workspace_role',?)", (canonical_json(record),))
        store.event(db, 'workspace_role_classified', lab_id, {'before':json.loads(old[0]) if old else None, 'after':record, 'origin':'workspace_organization'})
    return record


def catalog(workspace):
    labs, archives = [], []
    for lab in workspace.labs():
        role = storage_role(workspace, lab['id'])
        if role == 'laboratory': labs.append(lab)
        else: archives.append({**lab, 'storage_role':role, 'directory':str(workspace.lab_store(lab['id']).directory)})
    return labs, archives


def archive_contents(workspace, lab_id, page=1):
    if storage_role(workspace, lab_id) == 'laboratory': raise LabcalError('ARCHIVE_REQUIRED', '此入口仅用于历史资料档案。')
    if not isinstance(page, int) or not 1 <= page <= 100000: raise LabcalError('PAGE_INVALID', '清单页码无效。')
    store = workspace.lab_store(lab_id)
    with store.connect() as db:
        total = db.execute('SELECT COUNT(*) FROM sources').fetchone()[0]
        rows = [dict(r) for r in db.execute('SELECT id,name,kind,sha256,object_path FROM sources ORDER BY name,id LIMIT 30 OFFSET ?', ((page-1)*30,))]
        plans = db.execute('SELECT COUNT(*) FROM plans').fetchone()[0]
    return {'directory':str(store.directory), 'items':rows, 'total':total, 'plans':plans, 'page':page, 'page_size':30}


def storage_overview(workspace, lab_id, campaign_id=None, *, deep=False):
    started = perf_counter(); store = campaign_store(workspace, lab_id)
    campaign = get_campaign(workspace, lab_id, campaign_id) if campaign_id else None
    directory = campaign_directory(workspace, lab_id, campaign) if campaign else store.directory / 'campaigns'
    with store.connect() as db:
        integrity = db.execute('PRAGMA quick_check').fetchone()[0]
        foreign_keys = [tuple(r) for r in db.execute('PRAGMA foreign_key_check')]
        counts = {table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in
                  ['sources','equipment','workbook_imports','plan_revisions','certificate_extractions','institution_evidence','campaigns']}
        links = {r[0]:r[1] for r in db.execute('SELECT kind,COUNT(*) FROM campaign_links WHERE campaign_id=? GROUP BY kind',(campaign_id,))} if campaign else {}
        missing = [r['name'] for r in db.execute('SELECT name,object_path FROM sources') if not (store.directory / r['object_path']).is_file()]
    modules = [
        ('实验室设置','meta / laboratory',store.directory,1,'实验室配置'),
        ('年度与批次','campaigns / campaign_links',directory,1 if campaign else counts['campaigns'],'固定版本引用'),
        ('校准方案','workbook_imports / plan_revisions',directory/'校准方案',links.get('workbook',0)+links.get('plan',0),'当前批次'),
        ('证书审查','certificate_extractions / certificate_reviews',directory/'证书报告',links.get('extraction',0),'当前批次'),
        ('引用已有资料','sources + 固定版本索引',store.directory/'objects',counts['sources'],'当前实验室，引用不复制限值'),
        ('机构范围资料','institution_evidence + sources',store.directory/'objects',counts['institution_evidence'],'实验室共享，按有效期和参数使用'),
        ('设备台账','equipment',store.path,counts['equipment'],'实验室跨年度持续使用'),
        ('方案输出','runs / plan_export',store.directory/'outputs/calibration',None,'方案编制线路'),
        ('诊断输出','runs / certificate_review_export',store.directory/'outputs/certificates',None,'证书诊断线路；历史路径兼容'),
        ('资料输入','sources / report_jobs',store.directory/'inputs',None,'按批次ID与线路隔离'),
        ('中间处理','固定快照 / intelligence_packets',store.directory/'processing',None,'两条线路独立；工程维护另列')]
    result = {'layout_version':2,'lab_id':lab_id,'campaign_id':campaign_id,'database':str(store.path),'directory':str(directory),
            'integrity':integrity,'foreign_key_errors':foreign_keys,'missing_originals':missing,
            'query_ms':round((perf_counter()-started)*1000,2),
            'modules':[{'module':m,'tables':t,'path':str(p),'exists':p.exists(),'count':n,'scope':s} for m,t,p,n,s in modules]}
    if deep:
        from .storage_audit import audit_storage
        result['audit'] = audit_storage(workspace, lab_id, campaign_id)
    return result
