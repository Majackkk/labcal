import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import zipfile
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from labcal.adapters.storage import Workspace
from labcal.adapters.spreadsheet import read_workbook
from labcal.adapters.server import make_server
from labcal.application.laboratories import create_laboratory, add_equipment, update_laboratory
from labcal.application.campaigns import (create_campaign, get_campaign, update_campaign, attach_item, require_item, list_items, campaign_directory)
from labcal.application.workbooks import import_workbook, get_workbook, draft_from_rows
from labcal.application.console import console_state, library_items
from labcal.application.plans import get_plan, assess_plan, create_plan, compile_baseline
from labcal.application.sources import verify_source_integrity
from labcal.application.maintenance import backup_laboratory, restore_laboratory, doctor
from labcal.application.certificates import add_certificate, get_extraction, certificate_context
from labcal.application.intelligence import create_packet, import_result, list_results
from labcal.application.certificate_reviews import create_review
from labcal.errors import LabcalError
from labcal.util import file_hash


def sample_workbook(path):
    rows = [["设备名称", "序号", "参数名称", "校准点", "允许误差"],
            ["测试设备组"], ["仪器", "01", "温度", "10, 20 ℃", "±0.5 ℃"],
            ["仪器", "01", "压力", "1 MPa", "±1%"], ["其他设备组"], ["仪器", "02", "长度", "10 mm", "待明确"]]
    letters = "ABCDE"; data = []
    for i, row in enumerate(rows, 1):
        cells = ''.join(f'<c r="{letters[j]}{i}" t="inlineStr"><is><t>{escape(value)}</t></is></c>' for j, value in enumerate(row))
        data.append(f'<row r="{i}">{cells}</row>')
    data.append('<row r="7"><c r="A7"><f>1+1</f><v>2</v></c></row>')
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="方案" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{ns}"><sheetData>{"".join(data)}</sheetData><mergeCells><mergeCell ref="A2:E2"/><mergeCell ref="A5:E5"/></mergeCells></worksheet>')


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.ws = Workspace(self.root); self.ws.initialize()
        for lab in ("LAB_A", "LAB_B"): create_laboratory(self.ws, lab, lab, test_only=True)
        self.c = create_campaign(self.ws, "LAB_A", "年度校准", year=2026)
        self.other = create_campaign(self.ws, "LAB_A", "年度校准", year=2027)
        self.book = self.root / "input.xlsx"; sample_workbook(self.book)

    def tearDown(self): self.temp.cleanup()

    def imported(self): return import_workbook(self.ws, "LAB_A", self.c["id"], self.book)

    def test_year_and_lab_are_independent_containers(self):
        foreign = create_campaign(self.ws, "LAB_B", "年度校准", year=2026)
        self.assertNotEqual(self.c["id"], self.other["id"])
        with self.assertRaises(LabcalError): get_campaign(self.ws, "LAB_B", self.c["id"])
        self.assertEqual(console_state(self.ws, "LAB_B", foreign["id"])["totals"], {})

    def test_unknown_year_and_same_year_multiple_batches(self):
        unknown = create_campaign(self.ws, "LAB_A", "未定年度")
        extra = create_campaign(self.ws, "LAB_A", "补充校准", year=2026)
        self.assertIsNone(unknown["year"])
        self.assertTrue((campaign_directory(self.ws, "LAB_A", unknown) / "index.json").is_file())
        self.assertNotEqual(extra["id"], self.c["id"])

    def test_year_does_not_accept_bool_or_path_text(self):
        for value in (True, "../2026", 10000):
            with self.assertRaises(LabcalError): create_campaign(self.ws, "LAB_A", "bad", year=value)

    def test_read_only_workbook_preserves_formulas_merges_and_raw_values(self):
        digest = file_hash(self.book); r = self.imported(); item = get_workbook(self.ws, "LAB_A", r["workbook_id"])
        self.assertEqual(file_hash(self.book), digest)
        self.assertEqual(r["candidates"], 3)
        self.assertEqual(len(r["groups"]), 2)
        self.assertEqual([x["values"]["sequence"] for x in item["payload"]["candidates"]][:2], ["01", "01"])
        formula = next(c for c in item["payload"]["sheets"][0]["cells"] if c["address"] == "A7")
        self.assertEqual(formula["formula"], "1+1"); self.assertEqual(formula["cached_value"], "2")
        self.assertEqual(item["payload"]["technical_review"], "pending")

    def test_repeat_import_is_idempotent_but_new_batch_requires_explicit_import(self):
        a = self.imported(); b = self.imported()
        self.assertEqual(a["workbook_id"], b["workbook_id"]); self.assertTrue(b["reused"])
        with self.assertRaises(LabcalError): require_item(self.ws, "LAB_A", self.other["id"], "workbook", a["workbook_id"])
        c = import_workbook(self.ws, "LAB_A", self.other["id"], self.book)
        self.assertEqual(c["workbook_id"], a["workbook_id"])

    def test_excel_does_not_create_real_assets_confirmations_or_baselines(self):
        self.imported(); s = console_state(self.ws, "LAB_A", self.c["id"])
        self.assertEqual(s["equipment"], []); self.assertEqual(s["plans"], []); self.assertEqual(s["baselines"], [])

    def test_candidate_can_be_bound_to_selected_asset_without_approval(self):
        r = self.imported(); eq = add_equipment(self.ws, "LAB_A", "TEST-1", "测试设备")
        rows = get_workbook(self.ws, "LAB_A", r["workbook_id"])["payload"]["candidates"]
        p = draft_from_rows(self.ws, "LAB_A", self.c["id"], r["workbook_id"], [rows[0]["id"]], eq["id"])
        state = assess_plan(self.ws, "LAB_A", p["plan_id"])
        self.assertFalse(state["user_confirmed"]); self.assertFalse(state["coverage_ready"])
        self.assertEqual(get_plan(self.ws, "LAB_A", p["plan_id"])["payload"]["requirements"][0]["raw"]["anchor"], "'方案'!A3:E3")
        with self.assertRaises(LabcalError): compile_baseline(self.ws, "LAB_A", p["plan_id"])

    def test_fixed_plan_reference_survives_new_revision(self):
        r = self.imported(); eq = add_equipment(self.ws, "LAB_A", "T1", "测试设备")
        rows = get_workbook(self.ws, "LAB_A", r["workbook_id"])["payload"]["candidates"]
        p = draft_from_rows(self.ws, "LAB_A", self.c["id"], r["workbook_id"], [rows[0]["id"]], eq["id"])
        old = get_plan(self.ws, "LAB_A", p["plan_id"], 1); payload = copy.deepcopy(old["payload"]); payload["title"] = "修订标题"
        new = create_plan(self.ws, "LAB_A", payload, plan_id=p["plan_id"])
        self.assertEqual(new["revision"], 2)
        require_item(self.ws, "LAB_A", self.c["id"], "plan", p["plan_id"], 1)
        with self.assertRaises(LabcalError): require_item(self.ws, "LAB_A", self.c["id"], "plan", p["plan_id"], 2)

    def test_reassign_year_preserves_original_and_unmanaged_files(self):
        r = self.imported(); old = campaign_directory(self.ws, "LAB_A", self.c)
        (old / "user-notes.txt").write_text("keep")
        new = update_campaign(self.ws, "LAB_A", self.c["id"], {"year": 2025, "expected_record_hash": self.c["record_hash"]})
        self.assertTrue((campaign_directory(self.ws, "LAB_A", new) / "user-notes.txt").exists())
        self.assertFalse(old.exists())
        self.assertTrue((campaign_directory(self.ws, "LAB_A", new) / "index.json").exists())
        self.assertEqual(verify_source_integrity(self.ws, "LAB_A", r["source_id"])["sha256"], file_hash(self.book))

    def test_archive_blocks_additions_and_can_be_restored(self):
        c = update_campaign(self.ws, "LAB_A", self.c["id"], {"state": "archived", "expected_record_hash": self.c["record_hash"]})
        with self.assertRaises(LabcalError): self.imported()
        update_campaign(self.ws, "LAB_A", c["id"], {"state": "active", "expected_record_hash": c["record_hash"]})
        self.imported()

    def test_foreign_reference_and_wrong_hash_are_rejected(self):
        r = self.imported()
        with self.assertRaises(LabcalError): attach_item(self.ws, "LAB_B", create_campaign(self.ws, "LAB_B", "test")["id"], "source", r["source_id"])
        with self.assertRaises(LabcalError): attach_item(self.ws, "LAB_A", self.other["id"], "workbook", r["workbook_id"], expected_hash="0"*64)

    def test_backup_restore_retains_campaign_and_workbook(self):
        r = self.imported(); archive = self.root / "backup.zip"
        backup_laboratory(self.ws, "LAB_A", archive); target = Workspace(self.root / "restored")
        restore_laboratory(target, archive)
        self.assertEqual(get_workbook(target, "LAB_A", r["workbook_id"])["payload_hash"], r["payload_hash"])
        self.assertEqual(len(list_items(target, "LAB_A", self.c["id"])), 2)
        self.assertEqual(doctor(target, "LAB_A")["status"], "structural_checks_passed")

    def certificate(self):
        import fitz
        file = self.root / "certificate.pdf"; doc = fitz.open(); p = doc.new_page(); p.insert_text((40,50), "Calibration certificate 2026 TEST"); doc.save(file); doc.close()
        r = add_certificate(self.ws, "LAB_A", file=file, ocr=False)
        attach_item(self.ws, "LAB_A", self.c["id"], "extraction", r["extraction_id"])
        return get_extraction(self.ws, "LAB_A", r["extraction_id"])

    def test_intelligence_drafts_do_not_become_actual_evidence(self):
        ext = self.certificate(); packet = create_packet(self.ws, "LAB_A", self.c["id"], [ext["id"]])
        r = packet["template"]; r["author"] = "TEST_ONLY_AGENT"
        location = {"page": 1, "bbox": [0.05,0.05,0.8,0.1], "kind": "visual"}
        r["records"][0]["fields"] = {"certificate_number": {"value": "TEST", "location": location}}
        r["records"][0]["findings"] = [{"code": "TEST", "title": "test", "message": "test", "basis": "test", "suggestion": "test", "location": location}]
        imported = import_result(self.ws, "LAB_A", self.c["id"], r)
        self.assertEqual(imported["state"], "candidate")
        context = certificate_context(self.ws, "LAB_A", ext["id"])
        self.assertEqual(context["fields"], {}); self.assertEqual(context["evidence_ids"], [])
        self.assertEqual(len(context["candidate_findings"]), 1)
        self.assertEqual(len(list_results(self.ws, "LAB_A", self.c["id"])), 1)
        self.assertTrue(import_result(self.ws, "LAB_A", self.c["id"], r)["reused"])

    def test_intelligence_result_cannot_assert_review_or_use_wrong_context(self):
        ext = self.certificate(); packet = create_packet(self.ws, "LAB_A", self.c["id"], [ext["id"]]); r = packet["template"]; r["author"] = "TEST_ONLY_AGENT"
        bad = copy.deepcopy(r); bad["records"][0]["reviewer"] = "fake"
        with self.assertRaises(LabcalError): import_result(self.ws, "LAB_A", self.c["id"], bad)
        bad = copy.deepcopy(r); bad["expected_packet_hash"] = "0"*64
        with self.assertRaises(LabcalError): import_result(self.ws, "LAB_A", self.c["id"], bad)
        with self.assertRaises(LabcalError): import_result(self.ws, "LAB_A", self.other["id"], r)

    def test_http_requires_batch_and_rejects_cross_batch_writes(self):
        ext = self.certificate(); server = make_server(self.ws, 0); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            headers = {"Content-Type": "application/json", "X-Labcal-Token": server.token}
            base = {"lab_id": "LAB_A", "extraction_ids": [ext["id"]]}
            for body, code in [(base, "CAMPAIGN_REQUIRED"), ({**base,"campaign_id":self.other["id"]}, "CAMPAIGN_SCOPE_MISMATCH")]:
                client.request("POST", "/api/reviews", json.dumps(body), headers); response = client.getresponse()
                self.assertEqual(response.status, 400); self.assertEqual(json.loads(response.read())["error"]["code"], code)
            client.request("POST", "/api/reviews", json.dumps({**base,"campaign_id":self.c["id"]}), headers)
            response = client.getresponse(); self.assertEqual(response.status, 200); review = json.loads(response.read())["result"]
            client.request("GET", f"/api/extraction?lab=LAB_A&campaign={self.other['id']}&id={ext['id']}")
            response = client.getresponse(); self.assertEqual(response.status, 400); response.read()
            client.request("POST", "/api/exports", json.dumps({"lab_id":"LAB_A","campaign_id":self.c["id"],"review_id":review["review_id"]}), headers)
            response = client.getresponse(); self.assertEqual(response.status,200); exported=json.loads(response.read())["result"]
            # No located issue means no empty annotated PDF; download scope is
            # still enforced on the structured report that always exists.
            self.assertFalse(any(x['name'].endswith('.pdf') for x in exported['manifest']['artifacts']))
            pdf = 'review.json'
            client.request("GET", f"/api/download?lab=LAB_A&campaign={self.c['id']}&run={exported['run_id']}&name={pdf}")
            response=client.getresponse();self.assertEqual(response.status,200);self.assertIsInstance(json.loads(response.read()),dict)
            client.request("GET", f"/api/download?lab=LAB_A&campaign={self.other['id']}&run={exported['run_id']}&name={pdf}")
            response=client.getresponse();self.assertEqual(response.status,400);response.read()
            client.request("POST", "/api/campaigns/attach", json.dumps({"lab_id":"LAB_A","campaign_id":self.other["id"],"kind":"review","entity_id":review["review_id"]}), headers)
            response=client.getresponse();self.assertEqual(response.status,200);response.read()
            require_item(self.ws,"LAB_A",self.other["id"],"extraction",ext["id"])
            client.close()
        finally: server.shutdown();server.server_close();thread.join(timeout=5)

    def test_console_search_and_pagination_are_scoped(self):
        ext = self.certificate()
        self.assertEqual(len(console_state(self.ws, "LAB_A", self.c["id"], search="certificate")["certificates"]), 1)
        self.assertEqual(console_state(self.ws, "LAB_A", self.other["id"])["certificates"], [])
        self.assertEqual(console_state(self.ws, "LAB_A", self.c["id"], search="missing")["certificate_matches"], 0)
        self.assertEqual(library_items(self.ws, "LAB_B", "extraction")["total"], 0)

    def test_model_packet_cannot_compare_against_a_silently_changed_candidate_catalog(self):
        ext = self.certificate()
        packet = create_packet(self.ws,"LAB_A",self.c['id'],[ext['id']])
        self.imported()
        result = packet['template']; result['author'] = 'TEST_ONLY_MODEL'
        with self.assertRaises(LabcalError) as error: import_result(self.ws,'LAB_A',self.c['id'],result)
        self.assertEqual(error.exception.code,'INTELLIGENCE_CONTEXT_CONFLICT')

    def test_lab_settings_need_explicit_choice_and_current_version(self):
        state = console_state(self.ws, "LAB_A")
        with self.assertRaises(LabcalError): update_laboratory(self.ws, "LAB_A", {"profile": "other"})
        record = {"operator": "TEST_ONLY_OPERATOR", "user_statement": "isolated test profile choice", "expected_hash": state["laboratory_hash"], "profile": "other"}
        updated = update_laboratory(self.ws, "LAB_A", record)
        self.assertEqual(updated["laboratory"]["profile"], "other")
        with self.assertRaises(LabcalError): update_laboratory(self.ws, "LAB_A", record)

    def test_library_pagination_and_literal_search_do_not_mix_records(self):
        from labcal.application.sources import import_source
        for i in range(23):
            file = self.root / f"plan-{i:02d}.txt"; file.write_text(f"TEST ONLY {i}")
            import_source(self.ws, file, kind="user_plan", lab_id="LAB_A")
        first = library_items(self.ws, "LAB_A", "source", page=1); second = library_items(self.ws, "LAB_A", "source", page=2)
        self.assertEqual(first["total"], 23);self.assertEqual(len(first["items"]),20);self.assertEqual(len(second["items"]),3)
        self.assertFalse({r["id"] for r in first["items"]}&{r["id"] for r in second["items"]})
        self.assertEqual(library_items(self.ws,"LAB_A","source",search="%")["total"],0)


if __name__ == "__main__": unittest.main()
