"""Internal worker entry point; no shell evaluation, network or document commands."""
import base64
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from labcal.adapters.pdf import extract_pdf, render_page, annotate_pdf, fitz_module
from labcal.errors import LabcalError


def send(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def main():
    try:
        request = json.load(sys.stdin); arguments = request["arguments"]
        fitz = fitz_module()
        fitz.TOOLS.mupdf_display_errors(False); fitz.TOOLS.mupdf_display_warnings(False)
        if request["operation"] == "extract":
            def progress(stage, page, pages): send({"type": "progress", "stage": stage, "page": page, "pages": pages})
            value = extract_pdf(**arguments, progress=progress)
        elif request["operation"] == "render":
            send({"type": "result", "encoding": "base64", "value": base64.b64encode(render_page(**arguments)).decode("ascii")}); return
        elif request["operation"] == "annotate": value = annotate_pdf(**arguments)
        else: raise LabcalError("PDF_OPERATION_INVALID", "不支持的PDF处理操作。")
        send({"type": "result", "value": value})
    except Exception as exc:
        error = exc.as_dict() if isinstance(exc, LabcalError) else {"code": "PDF_PROCESS_FAILED", "message": str(exc)}
        send({"type": "error", "error": error})


if __name__ == "__main__": main()
