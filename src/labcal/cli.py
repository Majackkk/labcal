"""Structured CLI shared by operators and workspace agents."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import zipfile

from .adapters.storage import Workspace
from .adapters.legacy import candidate_from_markdown
from .adapters.export import export_plan
from .application.laboratories import create_laboratory, update_laboratory, add_equipment, get_equipment, list_equipment, update_equipment
from .application.sources import import_source, list_sources, source_gaps, verify_source_integrity
from .application.plans import (create_plan, get_plan, list_plans, assess_plan, confirmation_template,
                                confirm_plan, verify_evidence, compile_baseline, get_baseline, diff_plans)
from .application.maintenance import doctor, backup_laboratory, restore_laboratory
from .application.migration import migrate_legacy, import_official_downloads, CORE_CODES
from .application.certificates import (add_certificate, list_certificates, get_extraction, evidence_template,
                                      record_evidence, record_institution, institution_records, get_review, list_reviews, record_resolution, record_candidate_findings)
from .application.certificate_reviews import create_review
from .application.campaigns import (create_campaign, list_campaigns, get_campaign, update_campaign, list_items, attach_item, refresh_index, require_item)
from .application.workbooks import import_workbook, get_workbook, draft_from_rows
from .application.intelligence import create_packet, get_packet, import_result, list_results
from .application.report_workspace import report_detail, device_catalog, review_report, review_related, associate_report
from .application.report_intake import list_jobs
from .adapters.certificate_export import export_review
from .application.workspace_catalog import classify_storage, storage_overview, require_working_laboratory
from .domain.contracts import SCHEMAS, validate_contract
from .errors import LabcalError
from .util import read_json, write_json


def parser():
    root = argparse.ArgumentParser(prog="labcal", description="通用实验室校准方案、证据和验收基准")
    root.add_argument("--workspace", default=".", help="工程和运行数据根目录")
    groups = root.add_subparsers(dest="group", required=True)
    groups.add_parser("init", help="初始化参考目录和运行数据")
    ocr_status = groups.add_parser("ocr", help="检查本地OCR运行环境").add_subparsers(dest="action", required=True)
    ocr_status.add_parser("status")
    lab = groups.add_parser("lab").add_subparsers(dest="action", required=True)
    add = lab.add_parser("add")
    add.add_argument("--id", required=True); add.add_argument("--name", required=True)
    add.add_argument("--type", default="testing"); add.add_argument("--profile", default="cnas_cl01")
    add.add_argument("--test-only", action="store_true")
    lab.add_parser("list")
    role = lab.add_parser("classify-storage"); role.add_argument("--id", required=True); role.add_argument("--role", choices=["laboratory","sample_archive","migration_archive"], required=True); role.add_argument("--reason", required=True)
    layout = groups.add_parser("organize", help="预览或执行实验室文件分层整理")
    layout.add_argument("--lab", required=True); layout.add_argument("--apply", action="store_true")
    self_review = groups.add_parser("self-diagnosis", help="聊天式证书自身诊断，不读取方案").add_subparsers(dest="action", required=True)
    for action in ["prepare", "show", "page", "finish"]:
        cmd = self_review.add_parser(action); cmd.add_argument("--lab", required=True); cmd.add_argument("--campaign", required=True)
        if action == "prepare": cmd.add_argument("--extractions", nargs="+", required=True)
        else: cmd.add_argument("--task", required=True)
        if action == "page": cmd.add_argument("--extraction", required=True); cmd.add_argument("--page", type=int, required=True)
        if action == "finish":
            cmd.add_argument("--record", required=True); cmd.add_argument("--no-annotate", action="store_true")
            cmd.add_argument('--no-summary', action='store_true')
    storage = groups.add_parser("storage"); storage.add_argument("--lab", required=True); storage.add_argument("--campaign")
    methods = groups.add_parser('method', help='共享方法要求及实验室用途关联').add_subparsers(dest='action', required=True)
    for action in ['import', 'search', 'show', 'page', 'use-import', 'uses', 'adopt-import', 'adoptions']:
        cmd = methods.add_parser(action)
        if action in {'import', 'use-import', 'adopt-import'}: cmd.add_argument('--record', required=True)
        if action in {'use-import', 'uses', 'adopt-import', 'adoptions'}: cmd.add_argument('--lab', required=True)
        if action == 'show': cmd.add_argument('--id', required=True)
        if action == 'page':
            cmd.add_argument('--source', required=True); cmd.add_argument('--page', type=int, required=True)
        if action == 'uses': cmd.add_argument('--equipment'); cmd.add_argument('--history', action='store_true')
        if action == 'search':
            for flag in ['query', 'standard', 'edition', 'equipment', 'parameter']: cmd.add_argument('--' + flag)
    diagnosis = groups.add_parser('method-diagnosis', help='先证书自身诊断，再对照方法用途').add_subparsers(dest='action', required=True)
    for action in ['prepare', 'show', 'finish']:
        cmd = diagnosis.add_parser(action); cmd.add_argument('--lab', required=True); cmd.add_argument('--campaign', required=True)
        if action == 'prepare':
            cmd.add_argument('--extractions', nargs='+', required=True); cmd.add_argument('--uses', nargs='+', required=True)
        else: cmd.add_argument('--task', required=True)
        if action == 'finish':
            cmd.add_argument('--record', required=True); cmd.add_argument('--no-annotate', action='store_true')
            cmd.add_argument('--no-summary', action='store_true')
    summary = groups.add_parser('summary', help='生成当前实验室唯一诊断汇总表')
    summary.add_argument('--lab', required=True)
    context = groups.add_parser('context', help='汇集实验室用途、测量分工与有据选择').add_subparsers(dest='action', required=True)
    for action in ['show', 'note-import', 'notes']:
        cmd = context.add_parser(action); cmd.add_argument('--lab', required=True)
        if action == 'show':
            cmd.add_argument('--equipment-key'); cmd.add_argument('--campaign')
        if action == 'note-import': cmd.add_argument('--record', required=True)
        if action == 'notes': cmd.add_argument('--history', action='store_true')
    templates = groups.add_parser('document-template', help='实验室专用Word模板').add_subparsers(dest='action', required=True)
    for action in ['register','show']:
        cmd = templates.add_parser(action); cmd.add_argument('--lab', required=True); cmd.add_argument('--role', required=True)
        if action == 'register':
            for name in ['campaign','file','title','reason']: cmd.add_argument('--' + name, required=True)
    confirmation = groups.add_parser('confirmation', help='准备单台设备检定校准确认表填写任务').add_subparsers(dest='action', required=True)
    cmd = confirmation.add_parser('prepare')
    for name in ['lab','campaign','extraction','equipment']: cmd.add_argument('--' + name, required=True)
    cmd.add_argument('--context-sources', nargs='*', default=[])
    cmd.add_argument('--equipment-key', help='明确设备身份键；不从显示名称推断')
    cmd.add_argument('--replaces-task'); cmd.add_argument('--replacement-reason')
    cmd.add_argument('--fresh-review', action='store_true', help='重新从原件确认，不读取历史确认表及其关联范围')
    cmd = confirmation.add_parser('analyze', help='填表前核对技术判断，返回阻断原因，不登记业务结果')
    for name in ['lab','campaign','task','record']: cmd.add_argument('--' + name, required=True)
    cmd = confirmation.add_parser('format', help='保留正文与模板结构，另存三维分色副本')
    for name in ['lab','campaign','task','record','document','output']: cmd.add_argument('--' + name, required=True)
    cmd.add_argument('--appendix-table', type=int, required=True)
    cmd = confirmation.add_parser('finish')
    for name in ['lab','campaign','task','record','document']: cmd.add_argument('--' + name, required=True)
    cmd.add_argument('--appendix-table', type=int, required=True)
    cmd.add_argument('--no-summary', action='store_true')
    cmd.add_argument('--annotations'); cmd.add_argument('--annotation-manifest')
    cmd = confirmation.add_parser('impact', help='查看变更影响；不改写历史技术结论')
    cmd.add_argument('--lab', required=True); cmd.add_argument('--campaign'); cmd.add_argument('--task')
    cmd.add_argument('--history', action='store_true')
    cmd = confirmation.add_parser('followup-import', help='追加机构沟通和更正资料的处理记录')
    for name in ['lab', 'task', 'record']: cmd.add_argument('--' + name, required=True)
    cmd = confirmation.add_parser('followups')
    cmd.add_argument('--lab', required=True); cmd.add_argument('--task'); cmd.add_argument('--history', action='store_true')
    storage.add_argument("--deep", action="store_true", help="只读核对原件、固定记录、批次目录及导出文件摘要")
    lab_update = lab.add_parser("update"); lab_update.add_argument("--id", required=True); lab_update.add_argument("--record", required=True)
    equipment = groups.add_parser("equipment").add_subparsers(dest="action", required=True)
    for action in ["add", "list", "show", "update"]:
        cmd = equipment.add_parser(action); cmd.add_argument("--lab", required=True)
        if action in {"show", "update"}: cmd.add_argument("--id", required=True)
        if action == "add":
            cmd.add_argument("--asset-code", required=True); cmd.add_argument("--name", required=True)
            cmd.add_argument("--model", default=""); cmd.add_argument("--configuration")
        if action == "update": cmd.add_argument("--file", required=True)
    source = groups.add_parser("source").add_subparsers(dest="action", required=True)
    for action in ["add", "list", "show", "gaps"]:
        cmd = source.add_parser(action); cmd.add_argument("--lab")
        if action == "add":
            cmd.add_argument("--file", required=True); cmd.add_argument("--kind", required=True)
            cmd.add_argument("--code"); cmd.add_argument("--name"); cmd.add_argument("--metadata")
            cmd.add_argument("--shareable", action="store_true")
        if action == "list": cmd.add_argument("--include-references", action="store_true")
        if action == "show": cmd.add_argument("--id", required=True)
        if action == "gaps": cmd.add_argument("--codes", nargs="+", default=CORE_CODES)
    plan = groups.add_parser("plan").add_subparsers(dest="action", required=True)
    for action in ["import", "draft", "import-markdown", "list", "show", "validate", "export", "confirmation-template", "confirm", "evidence-review", "diff"]:
        cmd = plan.add_parser(action); cmd.add_argument("--lab", required=True)
        if action in {"import", "draft"}:
            cmd.add_argument("--file", required=True); cmd.add_argument("--plan-id")
        if action == "import-markdown":
            cmd.add_argument("--file", required=True); cmd.add_argument("--equipment-id", required=True)
        if action in {"show", "validate", "export", "confirmation-template", "evidence-review", "diff"}:
            cmd.add_argument("--id", required=True); cmd.add_argument("--revision", type=int)
        if action == "confirmation-template": cmd.add_argument("--output", required=True)
        if action in {"confirm", "evidence-review"}: cmd.add_argument("--record", required=True)
        if action == "diff":
            cmd.add_argument("--before", type=int, required=True); cmd.add_argument("--after", type=int, required=True)
    baseline = groups.add_parser("baseline").add_subparsers(dest="action", required=True)
    for action in ["build", "show"]:
        cmd = baseline.add_parser(action); cmd.add_argument("--lab", required=True); cmd.add_argument("--id", required=True)
        if action == "build": cmd.add_argument("--revision", type=int)
    schemas = groups.add_parser("schema"); schemas.add_argument("--output-directory", default="schemas")
    status = groups.add_parser("status"); status.add_argument("--lab", required=True)
    health = groups.add_parser("doctor"); health.add_argument("--lab")
    backup = groups.add_parser("backup"); backup.add_argument("--lab", required=True); backup.add_argument("--output", required=True)
    restore = groups.add_parser("restore"); restore.add_argument("--archive", required=True)
    migration = groups.add_parser("migrate-legacy"); migration.add_argument("--source-root", required=True)
    migration.add_argument("--lab", default="LAB_LEGACY"); migration.add_argument("--sample-lab", default="LAB_CERT_SAMPLES")
    official = groups.add_parser("import-official"); official.add_argument("--directory", default="inbox/official")
    certificate = groups.add_parser("certificate", help="证书接入、提取及原页核验").add_subparsers(dest="action", required=True)
    for action in ["add", "list", "show", "evidence-template", "evidence", "findings"]:
        cmd = certificate.add_parser(action); cmd.add_argument("--lab", required=True)
        if action == "add":
            choice = cmd.add_mutually_exclusive_group(required=True)
            choice.add_argument("--file"); choice.add_argument("--source-id")
            cmd.add_argument("--no-ocr", action="store_true")
            cmd.add_argument("--ocr-engine", choices=["auto", "tesseract", "paddle"])
            cmd.add_argument("--force-ocr", action="store_true", help="对有文字层的PDF也重新识别，生成新快照")
        if action in {"show", "evidence-template"}: cmd.add_argument("--id", required=True, help="提取版本EXT标识")
        if action == "evidence-template": cmd.add_argument("--output", required=True)
        if action in {"evidence", "findings"}: cmd.add_argument("--record", required=True)
    review = groups.add_parser("review", help="审查、复核及批注导出").add_subparsers(dest="action", required=True)
    for action in ["run", "list", "show", "resolve", "export"]:
        cmd = review.add_parser(action); cmd.add_argument("--lab", required=True)
        if action == "run":
            cmd.add_argument("--extractions", nargs="+", required=True); cmd.add_argument("--baseline")
            cmd.add_argument("--certificate-group"); cmd.add_argument("--previous-review")
        if action in {"show", "export"}: cmd.add_argument("--id", required=True)
        if action == "export": cmd.add_argument("--annotation-style", choices=["callouts", "standard"], default="callouts", help="默认B3批改副本；standard仅用于旧式边框批注")
        if action == "resolve": cmd.add_argument("--record", required=True)
    institution = groups.add_parser("institution", help="机构范围原件核验记录").add_subparsers(dest="action", required=True)
    for action in ["add", "list"]:
        cmd = institution.add_parser(action); cmd.add_argument("--lab", required=True)
        if action == "add": cmd.add_argument("--record", required=True)
    serve = groups.add_parser("serve", help="打开本地证书审查工作台")
    serve.add_argument("--port", type=int, default=8765)
    launch = groups.add_parser("open", help="自动启动或复用本机服务并打开工作台")
    launch.add_argument("--port", type=int, default=8765)
    launch.add_argument("--no-browser", action="store_true")
    desktop = groups.add_parser("install-desktop", help="在桌面安装可双击的工作台启动入口")
    desktop.add_argument("--directory")
    campaign = groups.add_parser("campaign", help="年度及校准批次").add_subparsers(dest="action", required=True)
    for action in ["add", "list", "show", "update", "attach", "index"]:
        cmd = campaign.add_parser(action); cmd.add_argument("--lab", required=True)
        if action == "add":
            cmd.add_argument("--name", required=True); cmd.add_argument("--year", type=int); cmd.add_argument("--id"); cmd.add_argument("--notes", default="")
        if action in {"show", "update", "attach", "index"}: cmd.add_argument("--id", required=True)
        if action == "update": cmd.add_argument("--record", required=True)
        if action == "attach":
            cmd.add_argument("--kind", required=True); cmd.add_argument("--entity-id", required=True); cmd.add_argument("--revision", type=int, default=0); cmd.add_argument("--expected-hash")
    workbook = groups.add_parser("workbook", help="XLSX候选方案").add_subparsers(dest="action", required=True)
    for action in ["add", "show", "draft"]:
        cmd = workbook.add_parser(action); cmd.add_argument("--lab", required=True)
        if action in {"add", "draft"}: cmd.add_argument("--campaign", required=True)
        if action == "add": cmd.add_argument("--file", required=True); cmd.add_argument("--mapping")
        if action in {"show", "draft"}: cmd.add_argument("--id", required=True)
        if action == "draft":
            cmd.add_argument("--rows", nargs="+", required=True); cmd.add_argument("--equipment-id", required=True); cmd.add_argument("--title")
    report = groups.add_parser("report", help="自动设备匹配与报告工作台").add_subparsers(dest="action", required=True)
    for action in ["catalog", "show", "process", "group-review", "associate", "jobs"]:
        cmd = report.add_parser(action); cmd.add_argument("--lab", required=True); cmd.add_argument("--campaign", required=True)
        if action not in {"catalog", "jobs"}: cmd.add_argument("--id", required=True)
        if action == "associate": cmd.add_argument("--record", required=True)
    intelligence = groups.add_parser("intelligence", help="智能审查上下文与候选结果").add_subparsers(dest="action", required=True)
    for action in ["prepare", "show", "import", "list"]:
        cmd = intelligence.add_parser(action); cmd.add_argument("--lab", required=True)
        if action != "show": cmd.add_argument("--campaign", required=True)
        if action == "prepare": cmd.add_argument("--extractions", nargs="+", required=True); cmd.add_argument("--baseline")
        if action == "show": cmd.add_argument("--id", required=True)
        if action == "import": cmd.add_argument("--record", required=True)
    return root


def execute(args):
    workspace = Workspace(args.workspace)
    if args.group == "ocr":
        from .adapters.paddle_ocr import runtime_status
        return runtime_status()
    writes = {
        "equipment": {"add", "update"}, "source": {"add"},
        "plan": {"import", "draft", "import-markdown", "confirm", "evidence-review", "export"},
        "baseline": {"build"}, "campaign": {"add", "update", "attach", "index"},
        "workbook": {"add", "draft"}, "report": {"process", "group-review", "associate"},
        "intelligence": {"prepare", "import"}, "certificate": {"add", "evidence", "findings"},
        "review": {"run", "resolve", "export"}, "institution": {"add"}}
    if getattr(args, 'lab', None) and getattr(args, 'action', None) in writes.get(args.group, set()):
        require_working_laboratory(workspace, args.lab)
    if args.group == 'lab' and args.action == 'update': require_working_laboratory(workspace, args.id)
    if args.group == "init": return workspace.initialize()
    if args.group == "lab":
        if args.action == "classify-storage": return classify_storage(workspace, args.id, args.role, args.reason)
        if args.action == "list": return workspace.labs()
        if args.action == "update": return update_laboratory(workspace, args.id, read_json(args.record))
        return create_laboratory(workspace, args.id, args.name, laboratory_type=args.type, profile=args.profile, test_only=args.test_only)
    if args.group == "equipment":
        if args.action == "add": return add_equipment(workspace, args.lab, args.asset_code, args.name, args.model, read_json(args.configuration) if args.configuration else {})
        if args.action == "list": return list_equipment(workspace, args.lab)
        if args.action == "show": return get_equipment(workspace, args.lab, args.id)
        return update_equipment(workspace, args.lab, args.id, read_json(args.file))
    if args.group == "source":
        if args.action == "add": return import_source(workspace, args.file, kind=args.kind, lab_id=args.lab, code=args.code, name=args.name, metadata=read_json(args.metadata) if args.metadata else None, shareable=args.shareable)
        if args.action == "list": return list_sources(workspace, args.lab, include_references=args.include_references)
        if args.action == "show": return verify_source_integrity(workspace, args.lab, args.id)
        if not args.lab: raise LabcalError("LAB_REQUIRED", "资料缺口查询需要指定实验室。")
        return source_gaps(workspace, args.lab, args.codes)
    if args.group == "plan":
        if args.action in {"import", "draft"}:
            payload = read_json(args.file)
            if args.action == "draft": payload["origin"] = "evidence_draft"
            else: payload.setdefault("origin", "user_plan")
            return create_plan(workspace, args.lab, payload, plan_id=args.plan_id)
        if args.action == "import-markdown":
            path = Path(args.file)
            source = import_source(workspace, path, kind="user_plan", lab_id=args.lab)
            payload = candidate_from_markdown(path.read_text(encoding="utf-8"), title=path.stem, equipment_id=args.equipment_id, source_id=source["id"])
            return create_plan(workspace, args.lab, payload)
        if args.action == "list":
            return [{"id": p["plan_id"], "revision": p["revision"], "state": p["state"], "title": p["payload"]["title"], "requirements": len(p["payload"]["requirements"])} for p in list_plans(workspace, args.lab)]
        if args.action == "show": return get_plan(workspace, args.lab, args.id, args.revision)
        if args.action == "validate": return assess_plan(workspace, args.lab, args.id, args.revision)
        if args.action == "export": return export_plan(workspace, args.lab, args.id, args.revision)
        if args.action == "confirmation-template":
            record = confirmation_template(workspace, args.lab, args.id, args.revision)
            write_json(args.output, record)
            return {"output": str(Path(args.output).resolve()), "status": "awaiting_direct_user_confirmation"}
        if args.action == "confirm": return confirm_plan(workspace, args.lab, read_json(args.record))
        if args.action == "evidence-review":
            revision = args.revision or get_plan(workspace, args.lab, args.id)["revision"]
            return verify_evidence(workspace, args.lab, args.id, revision, read_json(args.record))
        return diff_plans(workspace, args.lab, args.id, args.before, args.after)
    if args.group == "baseline":
        if args.action == "build": return compile_baseline(workspace, args.lab, args.id, args.revision)
        return get_baseline(workspace, args.lab, args.id)
    if args.group == "organize":
        from .application.layout_migration import organize_layout
        return organize_layout(workspace, args.lab, apply=args.apply)
    if args.group == "self-diagnosis":
        from .application.self_diagnosis import prepare, load_task, page, finish
        if args.action == "prepare": return prepare(workspace, args.lab, args.campaign, args.extractions)
        if args.action == "show": return load_task(workspace, args.lab, args.campaign, args.task)[1]
        if args.action == "page": return page(workspace, args.lab, args.campaign, args.task, args.extraction, args.page)
        result = finish(workspace, args.lab, args.campaign, args.task, read_json(args.record), annotate=not args.no_annotate)
        if not args.no_summary:
            from .application.laboratory_summary import export_summary
            try: result['summary'] = export_summary(workspace, args.lab)
            except LabcalError as exc: result['summary_pending'] = exc.as_dict()
        return result
    if args.group == "storage": return storage_overview(workspace, args.lab, args.campaign, deep=args.deep)
    if args.group == 'method':
        from .application.methods import import_method, search_methods, get_method, method_page, import_use, list_uses, import_adoption, list_adoptions
        if args.action == 'import': return import_method(workspace, read_json(args.record))
        if args.action == 'show': return get_method(workspace, args.id)
        if args.action == 'search': return search_methods(workspace, query=args.query or '', standard=args.standard, edition=args.edition, equipment=args.equipment, parameter=args.parameter)
        if args.action == 'page': return method_page(workspace, args.source, args.page)
        if args.action == 'use-import': return import_use(workspace, args.lab, read_json(args.record))
        if args.action == 'adopt-import': return import_adoption(workspace, args.lab, read_json(args.record))
        if args.action == 'adoptions': return list_adoptions(workspace, args.lab)
        return list_uses(workspace, args.lab, equipment=args.equipment, include_history=args.history)
    if args.group == 'method-diagnosis':
        from .application.method_diagnosis import prepare, load_task, finish
        if args.action == 'prepare': return prepare(workspace, args.lab, args.campaign, args.extractions, args.uses)
        if args.action == 'show': return load_task(workspace, args.lab, args.campaign, args.task)[1]
        result = finish(workspace, args.lab, args.campaign, args.task, read_json(args.record), annotate=not args.no_annotate)
        if not args.no_summary:
            from .application.laboratory_summary import export_summary
            try: result['summary'] = export_summary(workspace, args.lab)
            except LabcalError as exc: result['summary_pending'] = exc.as_dict()
        return result
    if args.group == 'summary':
        from .application.laboratory_summary import export_summary
        return export_summary(workspace, args.lab)
    if args.group == 'context':
        from .application.business_context import assemble_context, import_note, list_notes
        if args.action == 'show': return assemble_context(workspace, args.lab, args.equipment_key, args.campaign)
        if args.action == 'note-import': return import_note(workspace, args.lab, read_json(args.record))
        return list_notes(workspace, args.lab, history=args.history)
    if args.group == 'document-template':
        from .application.document_templates import register, get_template
        if args.action == 'show': return get_template(workspace, args.lab, args.role)
        return register(workspace, args.lab, args.campaign, args.file, args.role, args.title, args.reason)
    if args.group == 'confirmation':
        from .application.document_templates import prepare_confirmation, finish_confirmation
        if args.action in {'impact', 'followup-import', 'followups'}:
            from .application.confirmation_updates import impact, import_followup, list_followups
            if args.action == 'impact': return impact(workspace, args.lab, args.campaign, args.task, history=args.history)
            if args.action == 'followups': return list_followups(workspace, args.lab, args.task, history=args.history)
            return import_followup(workspace, args.lab, args.task, read_json(args.record))
        if args.action == 'prepare':
            return prepare_confirmation(workspace, args.lab, args.campaign, args.extraction, args.equipment, context_sources=args.context_sources, fresh_review=args.fresh_review, equipment_key=args.equipment_key,
                                        replaces_task=args.replaces_task, replacement_reason=args.replacement_reason)
        if args.action == 'analyze':
            from .application.document_templates import analyze_confirmation
            return analyze_confirmation(workspace, args.lab, args.campaign, args.task, read_json(args.record))
        if args.action == 'format':
            from .application.document_templates import review_confirmation
            from .adapters.confirmation_style import format_confirmation
            record = read_json(args.record)
            _, checks = review_confirmation(workspace, args.lab, args.campaign, args.task, record)
            if checks is None:
                raise LabcalError('CONFIRMATION_SELF_REQUIRED', '正式确认表分色前请关联同版本证书的自身诊断。')
            return format_confirmation(args.document, args.output, record, args.appendix_table, self_checks=checks)
        result = finish_confirmation(workspace, args.lab, args.campaign, args.task, read_json(args.record), args.document, args.appendix_table,
                                     annotations=args.annotations, annotation_manifest=args.annotation_manifest)
        if not args.no_summary:
            from .application.laboratory_summary import export_summary
            try: result['summary'] = export_summary(workspace, args.lab)
            except LabcalError as exc: result['summary_pending'] = exc.as_dict()
        return result
    if args.group == "schema":
        directory = Path(args.output_directory)
        if not directory.is_absolute(): directory = workspace.root / directory
        for name, schema in SCHEMAS.items(): write_json(directory / (name + ".json"), schema, overwrite=True)
        return {"directory": str(directory), "contracts": list(SCHEMAS)}
    if args.group == "status":
        laboratory = next((x for x in workspace.labs() if x["id"] == args.lab), None)
        if laboratory is None: raise LabcalError("LAB_NOT_FOUND", "实验室尚未建立。")
        return {"laboratory": laboratory, "plans": [assess_plan(workspace, args.lab, p["plan_id"], p["revision"]) for p in list_plans(workspace, args.lab)], "core_source_gaps": source_gaps(workspace, args.lab, CORE_CODES)}
    if args.group == "doctor": return doctor(workspace, args.lab)
    if args.group == "backup": return backup_laboratory(workspace, args.lab, args.output)
    if args.group == "restore": return restore_laboratory(workspace, args.archive)
    if args.group == "migrate-legacy": return migrate_legacy(workspace, args.source_root, args.lab, args.sample_lab)
    if args.group == "import-official":
        directory = Path(args.directory)
        return import_official_downloads(workspace, directory if directory.is_absolute() else workspace.root / directory)
    if args.group == "campaign":
        if args.action == "add": return create_campaign(workspace, args.lab, args.name, year=args.year, notes=args.notes, campaign_id=args.id)
        if args.action == "list": return list_campaigns(workspace, args.lab)
        if args.action == "show": return {"campaign": get_campaign(workspace, args.lab, args.id), "items": list_items(workspace, args.lab, args.id)}
        if args.action == "update": return update_campaign(workspace, args.lab, args.id, read_json(args.record))
        if args.action == "attach": return attach_item(workspace, args.lab, args.id, args.kind, args.entity_id, args.revision, expected_hash=args.expected_hash)
        refresh_index(workspace, args.lab, args.id)
        return {"index_refreshed": True}
    if args.group == "workbook":
        if args.action == "add": return import_workbook(workspace, args.lab, args.campaign, args.file, mapping=read_json(args.mapping) if args.mapping else None)
        if args.action == "show": return get_workbook(workspace, args.lab, args.id)
        return draft_from_rows(workspace, args.lab, args.campaign, args.id, args.rows, args.equipment_id, title=args.title)
    if args.group == "report":
        if args.action == "catalog": return device_catalog(workspace, args.lab, args.campaign)
        if args.action == "show": return report_detail(workspace, args.lab, args.campaign, args.id)
        if args.action == "process": return review_report(workspace, args.lab, args.campaign, args.id)
        if args.action == "group-review": return review_related(workspace, args.lab, args.campaign, args.id)
        if args.action == "associate": return associate_report(workspace, args.lab, args.campaign, args.id, choice=read_json(args.record))
        return list_jobs(workspace, args.lab, args.campaign)
    if args.group == "intelligence":
        if args.action == "prepare": return create_packet(workspace, args.lab, args.campaign, args.extractions, baseline_id=args.baseline)
        if args.action == "show": return get_packet(workspace, args.lab, args.id)
        if args.action == "import": return import_result(workspace, args.lab, args.campaign, read_json(args.record))
        return list_results(workspace, args.lab, args.campaign)
    if args.group == "certificate":
        if args.action == "add": return add_certificate(workspace, args.lab, file=args.file, source_id=args.source_id, ocr=not args.no_ocr, ocr_engine=args.ocr_engine, force_ocr=args.force_ocr)
        if args.action == "list": return list_certificates(workspace, args.lab)
        if args.action == "show": return get_extraction(workspace, args.lab, args.id)
        if args.action == "evidence": return record_evidence(workspace, args.lab, read_json(args.record))
        if args.action == "findings": return record_candidate_findings(workspace, args.lab, read_json(args.record))
        record = evidence_template(workspace, args.lab, args.id)
        write_json(args.output, record)
        return {"output": str(Path(args.output).resolve()), "status": "awaiting_actual_page_review"}
    if args.group == "review":
        if args.action == "run": return create_review(workspace, args.lab, args.extractions, baseline_id=args.baseline, certificate_group=args.certificate_group, previous_review_id=args.previous_review)
        if args.action == "list": return list_reviews(workspace, args.lab)
        if args.action == "show": return get_review(workspace, args.lab, args.id)
        if args.action == "resolve": return record_resolution(workspace, args.lab, read_json(args.record))
        return export_review(workspace, args.lab, args.id, annotation_style=args.annotation_style)
    if args.group == "institution":
        if args.action == "list": return institution_records(workspace, args.lab)
        return record_institution(workspace, args.lab, read_json(args.record))
    if args.group == "serve":
        from .adapters.server import serve
        return serve(workspace, args.port)
    if args.group == "open":
        from .application.desktop import open_workbench
        return open_workbench(workspace, args.port, open_browser=not args.no_browser)
    if args.group == "install-desktop":
        from .application.desktop import install_desktop_entry
        return install_desktop_entry(workspace, args.directory)
    raise LabcalError("COMMAND_UNSUPPORTED", "未实现的命令。")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except LabcalError as exc:
        print(json.dumps({"ok": False, "error": exc.as_dict()}, ensure_ascii=False, indent=2))
        return 2
    except (OSError, ValueError, KeyError, sqlite3.Error, zipfile.BadZipFile) as exc:
        print(json.dumps({"ok": False, "error": {"code": "OPERATION_FAILED", "message": str(exc)}}, ensure_ascii=False, indent=2))
        return 2

