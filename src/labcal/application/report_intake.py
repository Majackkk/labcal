"""Bounded background report intake, with durable real progress and partial failures."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

from ..adapters.report_archive import preserve_report
from ..adapters.pdf_process import extract_pdf_isolated
from ..domain.contracts import validate_contract
from ..errors import LabcalError
from ..util import canonical_json, identifier, utc_now
from .campaigns import get_campaign, campaign_directory, attach_item, require_item
from .certificates import add_certificate, get_extraction
from .sources import import_source, verify_source_integrity
from .report_workspace import intake_store, review_report


def get_job(workspace, lab_id, campaign_id, job_id):
    get_campaign(workspace, lab_id, campaign_id)
    with intake_store(workspace, lab_id).connect() as db:
        row = db.execute("SELECT record FROM report_jobs WHERE id=? AND campaign_id=?", (job_id, campaign_id)).fetchone()
    if not row: raise LabcalError("REPORT_JOB_NOT_FOUND", "当前实验室与批次没有该导入任务。")
    value = json.loads(row[0]); validate_contract("report-job.v1", value)
    return value


def list_jobs(workspace, lab_id, campaign_id):
    get_campaign(workspace, lab_id, campaign_id)
    with intake_store(workspace, lab_id).connect() as db:
        return [json.loads(r[0]) for r in db.execute("SELECT record FROM report_jobs WHERE campaign_id=? ORDER BY updated_at DESC LIMIT 100", (campaign_id,))]


class ReportIntake:
    def __init__(self, workspace):
        self.workspace = workspace
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="labcal-report")
        self.lock = threading.Lock(); self.active = 0
        # Server restarts cannot leave a fictitious running scan on screen.
        for lab in workspace.labs():
            store = workspace.lab_store(lab["id"])
            with store.connect() as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='report_jobs'").fetchone(): continue
                for row in list(db.execute("SELECT id,record FROM report_jobs")):
                    job = json.loads(row["record"])
                    if job["state"] in {"queued", "running"}:
                        job.update(state="interrupted", stage="interrupted", updated_at=utc_now(),
                                   error={"code": "SERVER_RESTARTED", "message": "服务重启中断了任务；已接入的原件保留，可重新接入继续。"})
                        db.execute("UPDATE report_jobs SET record=?,updated_at=? WHERE id=?", (canonical_json(job), job["updated_at"], row["id"]))

    def update(self, lab_id, campaign_id, job_id, **changes):
        store = intake_store(self.workspace, lab_id)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record FROM report_jobs WHERE id=? AND campaign_id=?", (job_id, campaign_id)).fetchone()
            if not row: raise LabcalError("REPORT_JOB_NOT_FOUND", "导入任务不存在。")
            job = json.loads(row[0]); job.update(changes, updated_at=utc_now())
            validate_contract("report-job.v1", job)
            db.execute("UPDATE report_jobs SET record=?,updated_at=? WHERE id=?", (canonical_json(job), job["updated_at"], job_id))
        return job

    def submit(self, lab_id, campaign_id, *, file=None, source_id=None, ocr_engine=None, force_ocr=False):
        get_campaign(self.workspace, lab_id, campaign_id, writable=True)
        if ocr_engine not in {None, "auto", "paddle", "tesseract"}:
            raise LabcalError("OCR_ENGINE_INVALID", "OCR方式无效。")
        if (file is None) == (source_id is None): raise LabcalError("REPORT_INPUT_INVALID", "接入一个PDF文件或已有原件。")
        if file and Path(file).suffix.lower() != ".pdf": raise LabcalError("REPORT_FORMAT_INVALID", "报告接入支持PDF。")
        if source_id: require_item(self.workspace, lab_id, campaign_id, "source", source_id)
        with self.lock:
            if self.active >= 50: raise LabcalError("REPORT_QUEUE_FULL", "当前待处理报告较多，请等待部分任务完成后再接入。")
            self.active += 1
        try:
            name = Path(file).name if file else verify_source_integrity(self.workspace, lab_id, source_id)["name"]
            job = {"id": identifier("JOB"), "lab_id": lab_id, "campaign_id": campaign_id, "filename": name,
                "state": "queued", "stage": "queued", "page": 0, "pages": 0,
                "source_id": None, "result": None, "error": None, "updated_at": utc_now()}
            validate_contract("report-job.v1", job)
            with intake_store(self.workspace, lab_id).connect() as db:
                db.execute("INSERT INTO report_jobs VALUES (?,?,?,?)", (job["id"], campaign_id, canonical_json(job), job["updated_at"]))
            self.executor.submit(self.run, job, file, source_id, ocr_engine, force_ocr)
            return job
        except BaseException:
            with self.lock: self.active -= 1
            raise

    def run(self, job, file, source_id, ocr_engine=None, force_ocr=False):
        lab, campaign, key = job["lab_id"], job["campaign_id"], job["id"]
        try:
            self.update(lab, campaign, key, state="running", stage="archiving")
            get_campaign(self.workspace, lab, campaign, writable=True)
            if file: source_id = import_source(self.workspace, file, kind="certificate_document", lab_id=lab)["id"]
            source = verify_source_integrity(self.workspace, lab, source_id)
            if source["kind"] not in {"certificate_document", "calibration_certificate", "verification_certificate"}:
                raise LabcalError("CERTIFICATE_SOURCE_INVALID", "该原件不是校准或检定报告。")
            attach_item(self.workspace, lab, campaign, "source", source_id)
            preserve_report(campaign_directory(self.workspace, lab, get_campaign(self.workspace, lab, campaign)), source)
            self.update(lab, campaign, key, source_id=source_id, stage="reading")
            def progress(stage, page, pages): self.update(lab, campaign, key, stage=stage, page=page, pages=pages)
            result = add_certificate(self.workspace, lab, source_id=source_id, progress=progress, pdf_reader=extract_pdf_isolated,
                                     ocr_engine=ocr_engine, force_ocr=force_ocr)
            attach_item(self.workspace, lab, campaign, "extraction", result["extraction_id"])
            self.update(lab, campaign, key, stage="matching")
            diagnosis = review_report(self.workspace, lab, campaign, result["extraction_id"])
            self.update(lab, campaign, key, state="completed", stage="completed", result={**result, **diagnosis})
        except Exception as exc:
            error = exc.as_dict() if isinstance(exc, LabcalError) else {"code": "REPORT_INTAKE_FAILED", "message": str(exc)}
            self.update(lab, campaign, key, state="failed", stage="failed", error=error)
        finally:
            with self.lock: self.active -= 1

    def close(self):
        self.executor.shutdown(wait=True)
