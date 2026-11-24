"""Local PDF text/OCR and page coordinates. Normalized boxes use rendered orientation."""
import csv
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from ..errors import LabcalError
from ..util import normalize_text as normalize

PARSER_VERSION = "local-pdf-1.2"
LABELS = {
    "certificate_number": ["证书编号", "报告编号", "Certificate No"],
    "client": ["委托单位", "送检单位", "客户名称", "Client"],
    "instrument_name": ["器具名称", "仪器名称", "设备名称", "Instrument name"],
    "model": ["型号规格", "型号", "Model/Type"],
    "instrument_number": ["仪器编号", "出厂编号", "器具编号", "设备编号", "Serial No"],
    "activity_date": ["校准日期", "检定日期", "Date of calibration", "Date of verification"],
    "issue_date": ["批准日期", "签发日期", "出证日期", "Date of approval"],
    "method": ["校准依据", "检定依据", "依据的技术文件", "依据的规程", "校准规范", "检定规程"],
    "environment": ["环境条件", "环境温度", "环境湿度", "温度和湿度"],
    "traceability": ["计量溯源", "溯源性", "溯源", "Traceability"],
    "results": ["校准结果", "检定结果", "测量结果", "Calibration results"],
    "uncertainty": ["不确定度", "Uncertainty"],
    "approval": ["批准人", "批准", "签发人", "Approved by"],
    "verification_conclusion": ["检定结论", "检定结果"],
    "verification_valid_until": ["有效期至", "有效日期", "有效期", "Valid until"],
}


def fitz_module():
    try:
        import fitz
        return fitz
    except ImportError as exc:
        raise LabcalError("PDF_DEPENDENCY_UNAVAILABLE", "证书定位和批注需要安装项目的pdf可选依赖（PyMuPDF）。") from exc


def _box(rect, page):
    rotated = rect * page.rotation_matrix
    w, h = page.rect.width, page.rect.height
    return [round(max(0, min(1, rotated.x0 / w)), 6), round(max(0, min(1, rotated.y0 / h)), 6),
            round(max(0, min(1, rotated.x1 / w)), 6), round(max(0, min(1, rotated.y1 / h)), 6)]


def _native_lines(page):
    fitz = fitz_module()
    result = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"])
            if text.strip():
                result.append({"text": text, "bbox": _box(fitz.Rect(line["bbox"]), page),
                               "method": "pdf_text", "confidence": None})
    return result


def _ocr_lines(page, language, *, clip=None):
    if not shutil.which("tesseract"):
        return [], "OCR_UNAVAILABLE"
    if not re.fullmatch(r"[a-z_]+(?:\+[a-z_]+)*", language):
        raise LabcalError("OCR_LANGUAGE_INVALID", "OCR语言标识无效。")
    fitz = fitz_module()
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False, clip=clip)
    with tempfile.TemporaryDirectory(prefix="labcal-ocr-") as directory:
        path = Path(directory) / "page.png"
        pix.save(path)
        try:
            process = subprocess.run(["tesseract", str(path.resolve()), "stdout", "-l", language, "--psm", "3", "tsv"],
                                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except subprocess.TimeoutExpired:
            return [], "OCR_TIMEOUT"
    if process.returncode:
        return [], "OCR_FAILED"
    groups = {}
    for row in csv.DictReader(io.StringIO(process.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        key = (row["block_num"], row["par_num"], row["line_num"])
        groups.setdefault(key, []).append(row)
    result = []
    for words in groups.values():
        x0 = min(int(x["left"]) for x in words); y0 = min(int(x["top"]) for x in words)
        x1 = max(int(x["left"]) + int(x["width"]) for x in words)
        y1 = max(int(x["top"]) + int(x["height"]) for x in words)
        box=[x0 / pix.width, y0 / pix.height, x1 / pix.width, y1 / pix.height]
        if clip is not None:
            box=[(clip.x0+box[0]*clip.width)/page.rect.width,(clip.y0+box[1]*clip.height)/page.rect.height,
                 (clip.x0+box[2]*clip.width)/page.rect.width,(clip.y0+box[3]*clip.height)/page.rect.height]
        result.append({"text": " ".join(x["text"] for x in words),
                       "bbox": box,
                       "method": "ocr", "confidence": min(float(x["conf"]) for x in words) / 100})
    return result, None


def _supplement_header(page, lines, language):
    """Only fill image header regions; native text remains authoritative candidates."""
    if page.rotation: return [], None
    header=[line for line in lines if line["bbox"][1]<.25]
    if any(re.search(r"公司|计量院|研究院|实验室|company|laborator",l["text"],re.I) for l in header): return [],None
    if not any(info["bbox"][1] < page.rect.height*.25 for info in page.get_image_info()): return [],None
    fitz=fitz_module();clip=fitz.Rect(0,0,page.rect.width,page.rect.height*.25)
    candidates,error=_ocr_lines(page,language,clip=clip)
    def native_overlap(candidate):
        b=candidate["bbox"];area=max(0,(b[2]-b[0])*(b[3]-b[1]))
        return any(max(0,min(b[2],n["bbox"][2])-max(b[0],n["bbox"][0]))*max(0,min(b[3],n["bbox"][3])-max(b[1],n["bbox"][1]))>area*.4 for n in header)
    return [c for c in candidates if not native_overlap(c)],error


def location(line):
    return {"page": line["page"], "bbox": line["bbox"], "line_ids": [line["id"]], "quote": line["text"], "kind": "text"}


def extract_fields(pages):
    lines = [line for page in pages for line in page["lines"]]
    fields = {}
    for field, aliases in LABELS.items():
        candidates = []
        for index, line in enumerate(lines):
            alias = next((x for x in aliases if normalize(x) in normalize(line["text"])), None)
            if not alias:
                continue
            value = line["text"]
            # Candidate only: never infer a verified value from a nearby unrelated cell.
            normalized = normalize(value)
            if normalized.rstrip(":：") == normalize(alias):
                neighbors = [x for x in lines[index + 1:index + 5] if x["page"] == line["page"]
                             and x["bbox"][1] <= line["bbox"][3] + .08]
                value = " ".join(x["text"] for x in neighbors) or value
            candidates.append({"value": value, "location": location(line), "state": "candidate"})
        if candidates:
            fields[field] = candidates
    if pages[0]["lines"]:
        first = pages[0]["lines"][0]
        fields["issuer"] = [{"value": first["text"], "location": location(first), "state": "candidate"}]
    return fields


def extract_pdf(path, source_id, source_hash, *, ocr=True, language="chi_sim+eng", progress=None, ocr_engine=None, force_ocr=False):
    from .paddle_ocr import PaddleSession, runtime_status
    engine = ocr_engine or os.environ.get("LABCAL_OCR_ENGINE", "auto")
    if engine not in {"auto", "tesseract", "paddle"}:
        raise LabcalError("OCR_ENGINE_INVALID", "OCR方式应为auto、tesseract或paddle。")
    fitz = fitz_module()
    warnings, pages = [], []
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise LabcalError("PDF_UNREADABLE", "PDF无法读取。") from exc
    paddle = PaddleSession()
    try:
        with document:
            if document.needs_pass:
                raise LabcalError("PDF_ENCRYPTED", "PDF需要密码，请接入可读取的证书副本。")
            if not 0 < len(document) <= 100:
                raise LabcalError("PDF_PAGE_LIMIT", "本地证书审查支持1至100页，请按证书拆分较大的文件。")
            signatures = any(widget.field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE for p in document for widget in (p.widgets() or []))
            for index, page in enumerate(document):
                if progress: progress("reading_page", index + 1, len(document))
                lines = _native_lines(page)
                method = "pdf_text"
                tables = []
                page_engine = "pdf_text"
                if force_ocr or len(normalize("".join(x["text"] for x in lines))) < 30:
                    if ocr:
                        recognized = []
                        if engine != "tesseract" and (engine == "paddle" or runtime_status()["available"]):
                            if progress: progress("paddle_ocr", index + 1, len(document))
                            with tempfile.TemporaryDirectory(prefix="labcal-paddle-page-") as directory:
                                image = Path(directory) / "page.png"
                                page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(image)
                                recognized, tables, errors = paddle.recognize(image)
                            warnings.extend({"code": e, "page": index + 1} for e in errors)
                            if recognized: page_engine = "paddleocr-vl-1.6"
                        if not recognized:
                            if progress: progress("tesseract_ocr", index + 1, len(document))
                            recognized, error = _ocr_lines(page, language)
                            if recognized: page_engine = "tesseract"
                            if error: warnings.append({"code": error, "page": index + 1})
                        if recognized:
                            lines, method = recognized, "ocr"
                    else:
                        warnings.append({"code": "OCR_NOT_RUN", "page": index + 1})
                elif index==0 and ocr and engine=="auto":
                    supplement,error=_supplement_header(page,lines,language)
                    if error: warnings.append({"code":"HYBRID_"+error,"page":1})
                    if supplement:
                        lines.extend(supplement); lines.sort(key=lambda l:(round(l["bbox"][1],2),l["bbox"][0]))
                        page_engine="pdf_text+tesseract_header"
                for number, line in enumerate(lines, 1):
                    line.update({"id": f"P{index+1:04d}-L{number:04d}", "page": index + 1})
                if method == "pdf_text" and lines:
                    try:
                        for table in page.find_tables().tables:
                            tables.append({"bbox": _box(fitz.Rect(table.bbox), page), "rows": table.extract(), "state": "candidate"})
                    except Exception:
                        warnings.append({"code": "TABLE_EXTRACTION_UNAVAILABLE", "page": index + 1})
                pages.append({"page": index + 1, "width": page.rect.width, "height": page.rect.height,
                              "rotation": page.rotation, "method": method, "engine": page_engine, "lines": lines, "tables": tables,
                              "text": "\n".join(x["text"] for x in lines), "needs_visual_check": method == "ocr" or page_engine=="pdf_text+tesseract_header" or not lines})
                if progress: progress("page_read", index + 1, len(document))
    finally:
        paddle.close()
    full = normalize("\n".join(x["text"] for x in pages))
    title = normalize(pages[0]["text"][:1600])
    is_calibration = "校准证书" in title or "calibrationcertificate" in title or "校准报告" in title
    is_verification = any(x in title for x in ["检定证书", "检定结果通知书", "检定报告", "verificationcertificate"])
    kind = "unknown" if is_calibration == is_verification else ("calibration" if is_calibration else "verification")
    if not full:
        warnings.append({"code": "DOCUMENT_TEXT_UNAVAILABLE"})
    return {"schema_version": "1.0", "source_id": source_id, "source_hash": source_hash,
            "document_kind": kind, "parser_version": PARSER_VERSION, "pages": pages,
            "fields": extract_fields(pages), "warnings": warnings, "digital_signatures_present": signatures}


def render_page(path, page_number, *, scale=1.4):
    fitz = fitz_module()
    with fitz.open(path) as document:
        if not 1 <= page_number <= len(document):
            raise LabcalError("PAGE_NOT_FOUND", "证书中没有该页。")
        return document[page_number - 1].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")


def annotate_pdf(path, output, findings, *, style="callouts"):
    if style == "callouts":
        from .pdf_callouts import annotate_callouts
        return annotate_callouts(path, output, findings)
    if style != "standard": raise LabcalError("ANNOTATION_STYLE_INVALID", "批注方式应为standard或callouts。")
    fitz = fitz_module()
    output = Path(output)
    if output.exists() or output.resolve() == Path(path).resolve():
        raise LabcalError("OUTPUT_EXISTS", "批注副本不能覆盖原件或已有输出。")
    with fitz.open(path) as document:
        note_positions = {}
        for finding in findings:
            if finding.get("resolution", {}).get("action") in {"dismissed", "resolved"}:
                continue
            loc = finding["location"]
            page = document[loc["page"] - 1]
            w, h = page.rect.width, page.rect.height
            displayed = fitz.Rect(loc["bbox"][0]*w, loc["bbox"][1]*h, loc["bbox"][2]*w, loc["bbox"][3]*h)
            rect = displayed * page.derotation_matrix
            color = (0.85, 0.20, 0.20) if finding["certainty"] == "confirmed" else (0.91, 0.55, 0.06)
            content = f"{finding['id']}｜{finding['title']}\n状态：{'已证实' if finding['certainty']=='confirmed' else '待核实'}\n{finding['message']}\n依据：{finding['basis']}\n建议：{finding['suggestion']}"
            if loc.get("kind") != "document":
                mark = page.add_rect_annot(rect)
                mark.set_colors(stroke=color); mark.set_border(width=1)
                mark.set_info(title="证书审查", subject=finding["id"], content=content); mark.update()
            # Keep notes in the margin; separate icons that share the same anchor.
            y = min(h-23, max(5, displayed.y0))
            used = note_positions.setdefault(loc["page"], [])
            candidates = [y] + [y + direction * 22 * step for step in range(1, int(h / 22) + 2)
                                for direction in (1, -1) if 5 <= y + direction * 22 * step <= h - 23]
            y = next((value for value in candidates if all(abs(value-other) >= 22 for other in used)), y)
            used.append(y)
            point = fitz.Point(w-23, y) * page.derotation_matrix
            note = page.add_text_annot(point, content, icon="Comment")
            note.set_info(title="证书审查", subject=finding["id"]); note.set_colors(stroke=color); note.update()
        document.save(output, garbage=3, deflate=True)
