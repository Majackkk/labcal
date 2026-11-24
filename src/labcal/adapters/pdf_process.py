"""Local PDF process boundary: MuPDF never shares concurrent request threads.

Protocol is private JSON lines over pipes. Documents are data; the operation is
selected by this adapter, never by document text. Each page emits real progress.
"""
import base64
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading

from ..errors import LabcalError

MAX_MESSAGE = 128 * 1024 * 1024


def _run(operation, arguments, progress=None):
    worker = Path(__file__).with_name("pdf_worker.py")
    process = subprocess.Popen([sys.executable, str(worker)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    messages = queue.Queue()
    def read():
        try:
            while True:
                line = process.stdout.readline(MAX_MESSAGE + 1)
                if not line: break
                if len(line) > MAX_MESSAGE:
                    messages.put({"type": "error", "error": {"code": "PDF_RESULT_TOO_LARGE", "message": "PDF识别结果过大，请按证书拆分。"}}); return
                messages.put(json.loads(line))
        except (ValueError, OSError):
            messages.put({"type": "error", "error": {"code": "PDF_PROCESS_PROTOCOL", "message": "PDF处理进程未返回有效结果。"}})
        finally: messages.put({"type": "closed"})
    reader = threading.Thread(target=read, daemon=True); reader.start()
    try:
        process.stdin.write(json.dumps({"operation": operation, "arguments": arguments}, ensure_ascii=False).encode("utf-8"))
        process.stdin.close()
        while True:
            try: message = messages.get(timeout=600 if operation == "extract" else 90)
            except queue.Empty as exc:
                raise LabcalError("PDF_PROCESS_TIMEOUT", "PDF页面处理超时，原件保持留存。") from exc
            kind = message["type"]
            if kind == "progress":
                if progress: progress(message["stage"], message["page"], message["pages"])
            elif kind == "error":
                error = message["error"]
                raise LabcalError(error["code"], error["message"], error.get("details"))
            elif kind == "result":
                if process.wait(timeout=5): raise LabcalError("PDF_PROCESS_FAILED", "PDF处理进程异常退出，原件保持留存。")
                return base64.b64decode(message["value"]) if message.get("encoding") == "base64" else message["value"]
            else:
                raise LabcalError("PDF_PROCESS_FAILED", "PDF处理进程未完成，原件保持留存。")
    finally:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        process.stdout.close(); reader.join(timeout=1)


def extract_pdf_isolated(path, source_id, source_hash, *, ocr=True, language="chi_sim+eng", progress=None, ocr_engine=None, force_ocr=False):
    return _run("extract", {"path": str(path), "source_id": source_id, "source_hash": source_hash,
                            "ocr": ocr, "language": language, "ocr_engine": ocr_engine, "force_ocr": force_ocr}, progress)


def render_page_isolated(path, page_number, *, scale=1.4):
    return _run("render", {"path": str(path), "page_number": page_number, "scale": scale})


def annotate_pdf_isolated(path, output, findings, *, style="callouts"):
    return _run("annotate", {"path": str(path), "output": str(output), "findings": findings, "style": style})
