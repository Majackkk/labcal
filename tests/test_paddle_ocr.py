"""Optional model adapter tests use synthetic inputs, never real confirmations."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from labcal.adapters.paddle_ocr import TableHTML, decode_page, PaddleSession
from labcal.adapters.pdf import extract_pdf

try:
    import fitz
except ImportError:
    fitz = None


class PaddleAdapterTests(unittest.TestCase):
    def test_merged_cells_and_typography_preserve_raw_output(self):
        html = '<table><tr><td>校准点(mm)</td><td>示值误差(%)</td><td>允许误差</td></tr><tr><td>5</td><td>-0.30</td><td rowspan="2">$ \\pm 0.5 $</td></tr><tr><td>10</td><td>0.20</td></tr></table>'
        value = {"width": 1000, "height": 2000, "parsing_res_list": [
            {"block_label": "table", "block_bbox": [100, 200, 900, 600], "block_content": html}]}
        lines, tables, warnings = decode_page(value)
        self.assertEqual(tables[0]["rows"][2], ["10", "0.20", "± 0.5"])
        self.assertEqual(tables[0]["raw_html"], html)
        self.assertEqual(lines[0]["bbox"], [.1, .1, .9, .3])
        self.assertIsNone(lines[0]["confidence"])
        self.assertEqual(warnings, [])

    def test_incomplete_generated_table_never_becomes_result_rows(self):
        value = {"width": 100, "height": 100, "parsing_res_list": [
            {"block_label": "table", "block_bbox": [1, 2, 90, 80], "block_content": '<table><tr><td>12.3'}]}
        lines, tables, warnings = decode_page(value)
        self.assertFalse(tables)
        self.assertEqual(warnings, ["PADDLE_TABLE_INCOMPLETE"])

    def test_invalid_geometry_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_page({"width": 0, "height": 1})
        with self.assertRaises(ValueError):
            TableHTML().extract('<table><tr><td colspan="9999">x</td></tr></table>')

    def test_unavailable_runtime_does_not_retry_every_page(self):
        session = PaddleSession()
        with patch('labcal.adapters.paddle_ocr.runtime_status', return_value={"available": False}) as status:
            for _ in range(2):
                self.assertEqual(session.recognize('/not-read.png'), ([], [], ["PADDLE_OCR_UNAVAILABLE"]))
            self.assertEqual(status.call_count, 1)
        session.close()

    @unittest.skipUnless(fitz, 'Requires optional PDF dependency')
    def test_pdf_fallback_is_visible_and_native_text_stays_fast(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.pdf'
            with fitz.open() as doc:
                p = doc.new_page(); p.insert_text((50, 50), 'Calibration certificate with enough native text to read directly.')
                doc.save(path)
            native = extract_pdf(path, 'SRC-TEST', 'hash')
            self.assertEqual(native['pages'][0]['engine'], 'pdf_text')
            with patch.object(PaddleSession, 'recognize', return_value=([], [], ['PADDLE_OCR_TIMEOUT'])), patch(
                    'labcal.adapters.pdf._ocr_lines', return_value=([{'text': 'fallback result', 'bbox': [.1,.1,.9,.9], 'method': 'ocr', 'confidence': .8}], None)):
                value = extract_pdf(path, 'SRC-TEST', 'hash', ocr_engine='paddle', force_ocr=True)
            self.assertEqual(value['pages'][0]['engine'], 'tesseract')
            self.assertIn({'code': 'PADDLE_OCR_TIMEOUT', 'page': 1}, value['warnings'])


if __name__ == '__main__': unittest.main()
