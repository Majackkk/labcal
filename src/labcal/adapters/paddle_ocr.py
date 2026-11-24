"""Local, optional PaddleOCR-VL adapter with bounded time and table preservation."""
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import time


def runtime_status():
    home = Path.home()
    python = Path(os.environ.get("LABCAL_PADDLE_PYTHON", str(home / ".local/share/ocr-runtime/paddleocr-vl-1.6/.venv/bin/python")))
    models = Path(os.environ.get("LABCAL_PADDLE_MODELS", str(home / ".paddlex/official_models")))
    return {"engine": "paddleocr-vl-1.6", "available": python.is_file() and
            (models / "PaddleOCR-VL-1.6/model.safetensors").is_file() and
            (models / "PP-DocLayoutV3/inference.json").is_file(),
            "python": str(python), "vl_model": str(models / "PaddleOCR-VL-1.6"),
            "layout_model": str(models / "PP-DocLayoutV3"),
            "availability_check": "files_only", "mode": "local_cpu",
            "policy": os.environ.get("LABCAL_OCR_ENGINE", "auto")}


class TableHTML(HTMLParser):
    """Expand explicit merged cells only; reject broken or oversized generated tables."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []; self.row = None; self.cell = None; self.spans = {}; self.closed = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            if self.row is not None or len(self.rows) >= 500: raise ValueError("Invalid table rows")
            self.row = []
        elif tag in {"td", "th"}:
            if self.row is None or self.cell is not None: raise ValueError("Invalid table cell")
            attrs = dict(attrs)
            self.cell = [[], int(attrs.get("rowspan", 1)), int(attrs.get("colspan", 1))]
            if not 1 <= self.cell[1] <= 500 or not 1 <= self.cell[2] <= 64: raise ValueError("Invalid span")
        elif tag == "br" and self.cell is not None: self.cell[0].append("\n")

    def handle_data(self, data):
        if self.cell is not None: self.cell[0].append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(("".join(self.cell[0]).strip(), self.cell[1], self.cell[2])); self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.cell is not None: raise ValueError("Unclosed cell")
            result = []; col = 0; pending = self.spans; self.spans = {}
            def carry():
                nonlocal col
                while col in pending:
                    value, remaining = pending.pop(col); result.append(value)
                    if remaining > 1: self.spans[col] = (value, remaining - 1)
                    col += 1
            for text, rowspan, colspan in self.row:
                carry()
                for _ in range(colspan):
                    if col in pending: raise ValueError("Overlapping spans")
                    result.append(text)
                    if rowspan > 1: self.spans[col] = (text, rowspan - 1)
                    col += 1
            carry()
            if pending or len(result) > 64: raise ValueError("Invalid merged cells")
            self.rows.append(result); self.row = None
        elif tag == "table": self.closed = True

    def extract(self, html):
        self.feed(html); self.close()
        if not self.closed or self.cell is not None or self.row is not None or self.spans: raise ValueError("Incomplete table")
        return self.rows


def decode_page(value):
    width, height = float(value["width"]), float(value["height"])
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0: raise ValueError("Invalid image size")
    lines, tables, warnings = [], [], []
    for block in value.get("parsing_res_list", []):
        box = [float(x) for x in block["block_bbox"]]
        if len(box) != 4 or not all(math.isfinite(x) for x in box): raise ValueError("Invalid block coordinates")
        box = [max(0, min(1, n / (width if i % 2 == 0 else height))) for i, n in enumerate(box)]
        if box[0] >= box[2] or box[1] >= box[3]: raise ValueError("Empty block")
        text = block.get("block_content", "")
        if not isinstance(text, str): raise ValueError("Invalid block content")
        if block.get("block_label") == "table":
            try:
                rows = TableHTML().extract(text)
                # Normalize only explicit typography, preserving exact model output for audit.
                def plain(cell):
                    for before, after in [(r"\pm", "±"), (r"\leq", "≤"), (r"\le", "≤"), (r"\geq", "≥"), (r"\ge", "≥"), (r"\%", "%")]:
                        cell = cell.replace(before, after)
                    return re.sub(r"\$\s*([^$]+?)\s*\$", r"\1", cell).strip()
                rows = [[plain(cell) for cell in row] for row in rows]
                tables.append({"bbox": box, "rows": rows, "state": "candidate", "coordinate_precision": "block", "raw_html": text})
                text = "\n".join(" | ".join(row) for row in rows)
            except (ValueError, TypeError):
                warnings.append("PADDLE_TABLE_INCOMPLETE")
        for line in text.splitlines():
            if line.strip(): lines.append({"text": line.strip(), "bbox": box, "method": "ocr", "confidence": None,
                                          "engine": "paddleocr-vl-1.6", "coordinate_precision": "block"})
    return lines, tables, warnings


class PaddleSession:
    """One model per document; lock serializes heavy inference across import workers."""
    def __init__(self):
        self.process = None; self.lock = None; self.log = None; self.messages = queue.Queue()
        self.failure = None

    def _receive(self, timeout):
        try: message = self.messages.get(timeout=timeout)
        except queue.Empty: raise TimeoutError("PADDLE_OCR_TIMEOUT")
        if not isinstance(message, dict) or message.get("error"): raise RuntimeError("PADDLE_OCR_FAILED")
        return message

    def _start(self):
        config = runtime_status()
        if not config["available"]: raise RuntimeError("PADDLE_OCR_UNAVAILABLE")
        import fcntl
        self.lock = open(Path(tempfile.gettempdir()) / f"labcal-paddle-{os.getuid()}.lock", "a")
        deadline = time.monotonic() + 60
        while True:
            try: fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError:
                if time.monotonic() >= deadline: raise TimeoutError("PADDLE_OCR_BUSY")
                time.sleep(.25)
        env = dict(os.environ, PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True", HF_HUB_OFFLINE="1",
                   TRANSFORMERS_OFFLINE="1", PYTHONDONTWRITEBYTECODE="1")
        self.log = tempfile.TemporaryFile()
        self.process = subprocess.Popen([config["python"], str(Path(__file__).with_name("paddle_worker.py"))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, env=env)
        def read():
            try:
                for line in iter(lambda: self.process.stdout.readline(32 * 1024 * 1024 + 1), b""):
                    if len(line) > 32 * 1024 * 1024: break
                    self.messages.put(json.loads(line))
            except (ValueError, OSError): pass
            finally: self.messages.put(None)
        self.reader = threading.Thread(target=read, daemon=True); self.reader.start()
        self._send(config); self._receive(120)

    def _send(self, value):
        self.process.stdin.write(json.dumps(value).encode() + b"\n"); self.process.stdin.flush()

    def recognize(self, image):
        if self.failure: return [], [], [self.failure]
        try:
            if self.process is None: self._start()
            self._send({"image": str(Path(image).resolve())})
            lines, tables, warnings = decode_page(self._receive(300)["result"])
            if not lines: warnings.append("PADDLE_OCR_EMPTY")
            return lines, tables, warnings
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, TimeoutError) as exc:
            self.failure = str(exc) if str(exc) in {"PADDLE_OCR_UNAVAILABLE", "PADDLE_OCR_TIMEOUT", "PADDLE_OCR_BUSY"} else "PADDLE_OCR_FAILED"
            self.close(); return [], [], [self.failure]

    def close(self):
        if self.process:
            try: self.process.stdin.close()
            except OSError: pass
            if self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=5)
                except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()
            self.process.stdout.close(); self.reader.join(timeout=1); self.process = None
        if self.log: self.log.close(); self.log = None
        if self.lock: self.lock.close(); self.lock = None
