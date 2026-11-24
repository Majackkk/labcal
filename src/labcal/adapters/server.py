"""Loopback-only workbench; uses the same application services as the CLI."""
from .layout import output_directory, upload_directory
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import parse_qs, quote, urlparse

from ..application.certificates import (add_certificate, list_certificates, get_extraction, evidence_template,
                                      record_evidence, get_review, list_reviews, record_resolution,
                                      record_institution, institution_records, certificate_store, record_candidate_findings)
from ..application.certificate_reviews import create_review
from ..application.laboratories import create_laboratory, update_laboratory, add_equipment, list_equipment
from ..application.sources import list_sources, import_source, verify_source_integrity
from ..application.campaigns import (create_campaign, update_campaign, get_campaign, attach_item, require_item, refresh_index)
from ..application.console import console_state, library_items
from ..application.workspace_catalog import storage_role, archive_contents, storage_overview, require_working_laboratory
from ..application.workbooks import import_workbook, get_workbook, draft_from_rows
from ..application.plans import (get_plan, assess_plan, create_plan, confirmation_template, confirm_plan, compile_baseline, diff_plans)
from ..application.intelligence import create_packet, get_packet, import_result, list_results
from ..application.report_intake import ReportIntake, get_job, list_jobs
from ..application.report_workspace import report_detail, device_catalog, review_report, review_related, associate_report
from .. import __version__
from ..errors import LabcalError
from ..util import ensure_inside, identifier
from .certificate_export import export_review
from .pdf_process import render_page_isolated as render_page, extract_pdf_isolated

WEB = Path(__file__).resolve().parents[1] / "web"
MAX_BODY = 45 * 1024 * 1024


def make_server(workspace, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, data, mime="application/json; charset=utf-8", status=200, filename=None):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            if filename: self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(filename, safe=""))
            self.end_headers(); self.wfile.write(data)

        def guard(self, mutation=False):
            authority = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if authority not in allowed:
                raise LabcalError("LOCAL_HOST_REQUIRED", "工作台仅接受本机访问。")
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + x for x in allowed}:
                raise LabcalError("ORIGIN_REJECTED", "请求来源不属于本地工作台。")
            if mutation and not secrets.compare_digest(self.headers.get("X-Labcal-Token", ""), self.server.token):
                raise LabcalError("SESSION_REQUIRED", "请重新打开本地工作台。")

        def do_GET(self):
            try:
                self.guard()
                parsed = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                path = parsed.path
                if path == "/api/health":
                    from ..application.desktop import health_identity
                    return self.send(health_identity(workspace))
                if path == "/api/ocr-status":
                    from .paddle_ocr import runtime_status
                    return self.send({"ok": True, "result": runtime_status()})
                if path in {"/", "/app.css", "/app.js", "/console.js", "/reports.js", "/scan.js", "/matrix.js", "/storage.js", "/columns.js", "/panes.js", "/report-panels.js", "/callouts.js", "/diagnosis-ui.js"}:
                    name = "index.html" if path == "/" else path[1:]
                    data = (WEB / name).read_bytes()
                    if path == "/": data = data.replace(b"__SESSION_TOKEN__", self.server.token.encode()).replace(b"__APP_VERSION__", __version__.encode())
                    return self.send(data, "text/html; charset=utf-8" if name.endswith('.html') else "text/css; charset=utf-8" if name.endswith('.css') else "text/javascript; charset=utf-8")
                lab = q.get("lab")
                if path == "/api/state":
                    result = console_state(workspace, lab, q.get("campaign"), search=q.get("search", ""), page=int(q.get("list_page", 1)))
                    return self.send({"ok": True, "result": result})
                if not lab: raise LabcalError("LAB_REQUIRED", "请选择实验室。")
                campaign = q.get("campaign")
                scoped_reads = {"/api/extraction", "/api/evidence-template", "/api/review", "/api/page", "/api/workbook", "/api/packet", "/api/plan", "/api/plan-diff", "/api/intelligence-results", "/api/original", "/api/download", "/api/report", "/api/report-catalog", "/api/report-jobs", "/api/intake-page"}
                if path in scoped_reads and not campaign:
                    raise LabcalError("CAMPAIGN_REQUIRED", "请选择校准批次，再读取业务资料。")
                if campaign:
                    get_campaign(workspace, lab, campaign)
                    kind = {"/api/extraction": "extraction", "/api/evidence-template": "extraction", "/api/page": "extraction", "/api/review": "review", "/api/workbook": "workbook", "/api/packet": "intelligence"}.get(path)
                    if kind: require_item(workspace, lab, campaign, kind, q["id"])
                if path == "/api/storage": result = storage_overview(workspace, lab, campaign, deep=q.get("deep") == "1")
                elif path == "/api/archive": result = archive_contents(workspace, lab, int(q.get("list_page", 1)))
                elif path == "/api/archive-original":
                    if storage_role(workspace, lab) == "laboratory": raise LabcalError("ARCHIVE_REQUIRED", "请从实验室批次读取业务原件。")
                    source = verify_source_integrity(workspace, lab, q["id"])
                    return self.send(Path(source["absolute_path"]).read_bytes(), mimetypes.guess_type(source["name"])[0] or "application/octet-stream", filename=source["name"])
                elif path == "/api/extraction": result = get_extraction(workspace, lab, q["id"])
                elif path == "/api/report": result = report_detail(workspace, lab, campaign, q["id"], review_id=q.get("review_id"))
                elif path == "/api/report-catalog": result = device_catalog(workspace, lab, campaign)
                elif path == "/api/report-jobs": result = list_jobs(workspace, lab, campaign)
                elif path == "/api/intake-page":
                    job = get_job(workspace, lab, campaign, q["id"])
                    if not job["source_id"]: raise LabcalError("REPORT_NOT_ARCHIVED", "报告原件尚未归档。")
                    require_item(workspace, lab, campaign, "source", job["source_id"])
                    source = verify_source_integrity(workspace, lab, job["source_id"])
                    return self.send(render_page(source["absolute_path"], int(q.get("page", 1))), "image/png")
                elif path == "/api/library": result = library_items(workspace, lab, q["kind"], search=q.get("search", ""), page=int(q.get("list_page", 1)))
                elif path == "/api/workbook": result = get_workbook(workspace, lab, q["id"], campaign)
                elif path == "/api/plan":
                    revision = int(q["revision"])
                    if campaign: require_item(workspace, lab, campaign, "plan", q["id"], revision)
                    result = {"plan": get_plan(workspace, lab, q["id"], revision), "readiness": assess_plan(workspace, lab, q["id"], revision), "confirmation_template": confirmation_template(workspace, lab, q["id"], revision)}
                elif path == "/api/plan-diff":
                    before, after = int(q["before"]), int(q["after"])
                    if campaign:
                        for rev in (before, after): require_item(workspace, lab, campaign, "plan", q["id"], rev)
                    result = diff_plans(workspace, lab, q["id"], before, after)
                elif path == "/api/packet": result = get_packet(workspace, lab, q["id"])
                elif path == "/api/intelligence-results": result = list_results(workspace, lab, campaign)
                elif path == "/api/original":
                    if not campaign: raise LabcalError("CAMPAIGN_REQUIRED", "请选择校准批次。")
                    require_item(workspace, lab, campaign, "source", q["id"])
                    source = verify_source_integrity(workspace, lab, q["id"])
                    return self.send(Path(source["absolute_path"]).read_bytes(), mimetypes.guess_type(source["name"])[0] or "application/octet-stream", filename=source["name"])
                elif path == "/api/evidence-template": result = evidence_template(workspace, lab, q["id"])
                elif path == "/api/review": result = get_review(workspace, lab, q["id"])
                elif path == "/api/institution-evidence": result = institution_records(workspace, lab)
                elif path == "/api/page":
                    ext = get_extraction(workspace, lab, q["id"])
                    source = verify_source_integrity(workspace, lab, ext["payload"]["source_id"])
                    return self.send(render_page(source["absolute_path"], int(q.get("page", 1))), "image/png")
                elif path == "/api/download":
                    store = certificate_store(workspace, lab)
                    with store.connect() as db:
                        run = db.execute("SELECT manifest FROM runs WHERE id=?", (q["run"],)).fetchone()
                    if not run: raise LabcalError("EXPORT_NOT_FOUND", "导出任务不存在。")
                    manifest = json.loads(run[0]); name = q["name"]
                    if campaign: require_item(workspace, lab, campaign, "review", manifest["review_id"])
                    if name not in {x["name"] for x in manifest["artifacts"]} | {"manifest.json"}:
                        raise LabcalError("EXPORT_FILE_NOT_FOUND", "导出清单没有该文件。")
                    folder = output_directory(store, q["run"], manifest["kind"], existing=True)
                    file = ensure_inside(folder / name, folder)
                    return self.send(file.read_bytes(), mimetypes.guess_type(name)[0] or "application/octet-stream", filename=name)
                else: return self.send({"ok": False, "error": {"code": "NOT_FOUND", "message": "请求不存在。"}}, status=404)
                self.send({"ok": True, "result": result})
            except (LabcalError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                self.error(exc)

        def uploaded(self, body, lab):
            name = Path(body["filename"]).name
            if name in {"", ".", ".."} or "\\" in name: raise LabcalError("UPLOAD_NAME_INVALID", "文件名无效。")
            data = base64.b64decode(body["file_base64"], validate=True)
            lane = "calibration" if urlparse(self.path).path == "/api/workbooks" else "institution" if urlparse(self.path).path == "/api/source" else "certificates"
            get_campaign(workspace, lab, body["campaign_id"], writable=True)
            folder = upload_directory(workspace.lab_store(lab), body["campaign_id"], lane, identifier("UPLOAD"))
            folder.mkdir(parents=True, exist_ok=False)
            file = folder / name; file.write_bytes(data)
            return file

        def do_POST(self):
            try:
                self.guard(mutation=True)
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= MAX_BODY: raise LabcalError("UPLOAD_TOO_LARGE", "单次请求最多45MB；较大资料请用本地命令接入。")
                body = json.loads(self.rfile.read(size)); path = urlparse(self.path).path
                if not isinstance(body, dict): raise LabcalError("REQUEST_INVALID", "请求需要对象结构。")
                if path == "/api/laboratories":
                    return self.send({"ok": True, "result": create_laboratory(workspace, body["id"], body["name"], laboratory_type=body.get("laboratory_type", "unresolved"), profile=body.get("profile", "unresolved"))})
                lab = body["lab_id"]; workspace.lab_store(lab)
                require_working_laboratory(workspace, lab)
                if path == "/api/laboratories/update":
                    return self.send({"ok": True, "result": update_laboratory(workspace, lab, body["record"])})
                if path == "/api/campaigns":
                    return self.send({"ok": True, "result": create_campaign(workspace, lab, body["name"], year=body.get("year"), notes=body.get("notes", ""))})
                campaign = body.get("campaign_id")
                if not campaign: raise LabcalError("CAMPAIGN_REQUIRED", "请先选择校准批次，再写入业务资料。")
                get_campaign(workspace, lab, campaign, writable=path != "/api/campaigns/update")
                if path == "/api/certificates":
                    if body.get("source_id"): require_item(workspace, lab, campaign, "source", body["source_id"])
                    file = self.uploaded(body, lab) if "file_base64" in body else None
                    result = add_certificate(workspace, lab, file=file, source_id=body.get("source_id"), pdf_reader=extract_pdf_isolated)
                    attach_item(workspace, lab, campaign, "extraction", result["extraction_id"])
                    ext = get_extraction(workspace, lab, result["extraction_id"])
                    attach_item(workspace, lab, campaign, "source", ext["payload"]["source_id"])
                elif path == "/api/report-jobs":
                    file = self.uploaded(body, lab) if "file_base64" in body else None
                    result = self.server.intake.submit(lab, campaign, file=file, source_id=body.get("source_id"),
                        ocr_engine=body.get("ocr_engine"), force_ocr=body.get("force_ocr", False) is True)
                elif path == "/api/reports/process": result = review_report(workspace, lab, campaign, body["extraction_id"])
                elif path == "/api/reports/correct-field":
                    from ..application.report_corrections import correct_report_field
                    result = correct_report_field(workspace, lab, campaign, body["extraction_id"], body["change"])
                elif path == "/api/reports/group-review": result = review_related(workspace, lab, campaign, body["extraction_id"])
                elif path == "/api/reports/associate":
                    result = associate_report(workspace, lab, campaign, body["extraction_id"], choice=body["choice"])
                elif path == "/api/campaigns/update": result = update_campaign(workspace, lab, campaign, body["record"])
                elif path == "/api/campaigns/attach":
                    result = attach_item(workspace, lab, campaign, body["kind"], body["entity_id"], body.get("revision", 0), expected_hash=body.get("expected_hash"))
                    if body["kind"] == "extraction":
                        attach_item(workspace, lab, campaign, "source", get_extraction(workspace, lab, body["entity_id"])["payload"]["source_id"])
                    elif body["kind"] == "review":
                        previous = get_review(workspace, lab, body["entity_id"])
                        for snapshot in previous["payload"]["certificate_snapshots"]:
                            key = snapshot["extraction_id"]
                            attach_item(workspace, lab, campaign, "extraction", key)
                            attach_item(workspace, lab, campaign, "source", get_extraction(workspace, lab, key)["payload"]["source_id"])
                elif path == "/api/workbooks": result = import_workbook(workspace, lab, campaign, self.uploaded(body, lab), mapping=body.get("mapping"))
                elif path == "/api/workbooks/draft": result = draft_from_rows(workspace, lab, campaign, body["workbook_id"], body["row_ids"], body["equipment_id"], title=body.get("title"))
                elif path == "/api/plans/save":
                    old = get_plan(workspace, lab, body["plan_id"], body["revision"])
                    require_item(workspace, lab, campaign, "plan", body["plan_id"], body["revision"])
                    latest = get_plan(workspace, lab, body["plan_id"])
                    if old["payload_hash"] != body["expected_hash"] or latest["revision"] != old["revision"]:
                        raise LabcalError("PLAN_VERSION_CONFLICT", "方案已产生更新版本，请刷新并比较后再保存。")
                    result = create_plan(workspace, lab, body["payload"], plan_id=body["plan_id"])
                    attach_item(workspace, lab, campaign, "plan", result["plan_id"], result["revision"])
                elif path == "/api/plans/confirm":
                    record = body["record"]
                    require_item(workspace, lab, campaign, "plan", record["plan_id"], record["revision"])
                    result = confirm_plan(workspace, lab, record)
                elif path == "/api/plans/baseline":
                    require_item(workspace, lab, campaign, "plan", body["plan_id"], body["revision"])
                    result = compile_baseline(workspace, lab, body["plan_id"], body["revision"])
                    attach_item(workspace, lab, campaign, "baseline", result["baseline_id"])
                elif path == "/api/intelligence/prepare": result = create_packet(workspace, lab, campaign, body["extraction_ids"], baseline_id=body.get("baseline_id"))
                elif path == "/api/intelligence/result": result = import_result(workspace, lab, campaign, body["record"])
                elif path == "/api/equipment":
                    result = add_equipment(workspace, lab, body["asset_code"], body["name"], body.get("model", ""), body.get("configuration", {}))
                elif path == "/api/source":
                    result = import_source(workspace, self.uploaded(body, lab), kind="institution_scope", lab_id=lab)
                    attach_item(workspace, lab, campaign, "source", result["id"])
                elif path == "/api/reviews":
                    for key in body["extraction_ids"]: require_item(workspace, lab, campaign, "extraction", key)
                    if body.get("baseline_id"): require_item(workspace, lab, campaign, "baseline", body["baseline_id"])
                    if body.get("previous_review_id"): require_item(workspace, lab, campaign, "review", body["previous_review_id"])
                    result = create_review(workspace, lab, body["extraction_ids"], baseline_id=body.get("baseline_id"), certificate_group=body.get("certificate_group"), previous_review_id=body.get("previous_review_id"), campaign_id=campaign)
                    attach_item(workspace, lab, campaign, "review", result["review_id"])
                elif path in {"/api/evidence", "/api/findings"}:
                    require_item(workspace, lab, campaign, "extraction", body["record"]["extraction_id"])
                    result = (record_evidence if path == "/api/evidence" else record_candidate_findings)(workspace, lab, body["record"])
                elif path == "/api/institution-evidence":
                    require_item(workspace, lab, campaign, "source", body["record"]["source_id"])
                    result = record_institution(workspace, lab, body["record"])
                elif path == "/api/resolutions":
                    require_item(workspace, lab, campaign, "review", body["record"]["review_id"])
                    result = record_resolution(workspace, lab, body["record"])
                elif path == "/api/exports":
                    require_item(workspace, lab, campaign, "review", body["review_id"])
                    result = export_review(workspace, lab, body["review_id"], annotation_style=body.get("annotation_style", "callouts"), campaign_id=campaign)
                    refresh_index(workspace, lab, campaign)
                else: return self.send({"ok": False, "error": {"code": "NOT_FOUND", "message": "请求不存在。"}}, status=404)
                self.send({"ok": True, "result": result})
            except (LabcalError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                self.error(exc)

        def error(self, exc):
            error = exc.as_dict() if isinstance(exc, LabcalError) else {"code": "OPERATION_FAILED", "message": str(exc)}
            self.send({"ok": False, "error": error}, status=400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.token = secrets.token_urlsafe(32)
    server.daemon_threads = True
    server.intake = ReportIntake(workspace)
    close_http = server.server_close
    def close():
        close_http(); server.intake.close()
    server.server_close = close
    return server


def serve(workspace, port=8765):
    server = make_server(workspace, port)
    print(f"本地证书工作台：http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return {"status": "stopped"}
