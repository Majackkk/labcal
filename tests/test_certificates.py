"""Phase-two tests use synthetic PDFs and isolated, explicitly simulated laboratories."""
import copy
import http.client
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
try:
    import fitz
except ImportError:
    fitz = None

from labcal.adapters.storage import Workspace
from labcal.adapters.pdf import location
from labcal.adapters.certificate_export import export_review
from labcal.adapters.server import make_server
from labcal.application.certificates import (add_certificate, get_extraction, evidence_template, record_evidence,
                                           certificate_context, get_review, record_resolution, record_institution, record_candidate_findings)
from labcal.application.certificate_reviews import create_review
from labcal.application.laboratories import create_laboratory, add_equipment, get_equipment, update_equipment
from labcal.application.sources import import_source
from labcal.application.plans import create_plan, confirmation_template, confirm_plan, compile_baseline
from labcal.application.maintenance import backup_laboratory, restore_laboratory
from labcal.errors import LabcalError
from labcal.util import file_hash, utc_now


@unittest.skipUnless(fitz, "Phase two requires the PDF optional dependency")
class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ws = Workspace(self.root / "workspace"); self.ws.initialize()
        create_laboratory(self.ws, "LAB_A", "TEST ONLY A", test_only=True)
        create_laboratory(self.ws, "LAB_B", "TEST ONLY B", test_only=True)
        self.equipment = add_equipment(self.ws, "LAB_A", "EQ_TEST", "Synthetic force instrument")
        self.pdf = self.make_pdf("calibration.pdf")
        self.cert = add_certificate(self.ws, "LAB_A", file=self.pdf, ocr=False)
        self.ext = get_extraction(self.ws, "LAB_A", self.cert["extraction_id"])
        self.loc = location(self.ext["payload"]["pages"][0]["lines"][0])

    def tearDown(self):
        self.temp.cleanup()

    def make_pdf(self, name, *, kind="calibration", rotation=0, scanned=False):
        file = self.root / name
        with fitz.open() as doc:
            page = doc.new_page(width=595, height=842)
            page.insert_text((45, 50), "CALIBRATION CERTIFICATE" if kind == "calibration" else "VERIFICATION CERTIFICATE", fontsize=16)
            title = "校准证书" if kind == "calibration" else "检定证书"
            page.insert_text((45, 80), title, fontname="china-s", fontsize=14)
            for y, text in [(115,"Certificate No: TEST-CERT"),(145,"Client: TEST ONLY"),(175,"Instrument name: Force tester"),
                            (205,"Calibration results: TEST DATA"),(235,"Uncertainty: 0.1 N"),(265,"Approved by: image signature"),
                            (295,"Traceability: simulated source"),(325,"Method: TEST ONLY"),(355,"Date of calibration: 2026-10-01"),
                            (385,"20 N    0.2 N"),(415,"50 N    0.3 N"),(445,"100 N    0.4 N")]:
                page.insert_text((45,y),text,fontsize=11)
            page.set_rotation(rotation)
            if scanned:
                pix = page.get_pixmap(matrix=fitz.Matrix(2,2))
                with fitz.open() as image_doc:
                    image_page = image_doc.new_page(width=page.rect.width,height=page.rect.height)
                    image_page.insert_image(image_page.rect,stream=pix.tobytes("png")); image_doc.save(file)
            else:
                doc.save(file)
        return file

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(LabcalError) as caught: function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def baseline(self, *, policy="explicit_points"):
        file=self.root/"policy.md"; file.write_text("TEST ONLY LABORATORY REQUIREMENTS")
        src=import_source(self.ws,file,kind="user_plan",lab_id="LAB_A")
        req={"id":"F1","parameter":"Force error","unit":"N","delivery_route":"external_calibration",
             "coverage_policy":policy,"points":["20","50","100"] if policy=="explicit_points" else [],
             "calibration_range":{"minimum":"0","maximum":"100"},"report_fields":["indication_error","uncertainty"],
             "evidence":[{"source_id":src["id"],"claim_kind":"lab_requirement"}],
             "limit":{"lower":"-1","upper":"1","unit":"N"},"decision_rule":{"type":"interval","input":"error"}}
        plan=create_plan(self.ws,"LAB_A",{"title":"TEST ONLY PLAN","equipment_id":self.equipment["id"],"origin":"user_plan","source_ids":[src["id"]],"requirements":[req]})
        record=confirmation_template(self.ws,"LAB_A",plan["plan_id"])
        record.update(confirmed_by="TEST_ONLY_OPERATOR",confirmed_at=utc_now(),evidence_kind="operator_command",user_statement="Synthetic test confirmation only")
        confirm_plan(self.ws,"LAB_A",record)
        return compile_baseline(self.ws,"LAB_A",plan["plan_id"])["baseline_id"]

    def evidence(self, points=("20","50","100"), *, cert=None, complete=True, error="0.2", unit="N", ranges=None):
        cert=cert or self.cert
        record=evidence_template(self.ws,"LAB_A",cert["extraction_id"])
        ext=get_extraction(self.ws,"LAB_A",cert["extraction_id"])
        loc=location(ext["payload"]["pages"][0]["lines"][0])
        record.update(reviewer="TEST_ONLY_OPERATOR",reviewed_at=utc_now(),document_kind="calibration",kind_location=loc,
                      equipment_id=self.equipment["id"],identity_checked=True,pages_checked=[1],observations_complete=complete)
        record["fields"]={k:{"value":v,"location":loc} for k,v in {"instrument_number":"EQ_TEST","issuer":"TEST ONLY ISSUER","activity_date":"2026-10-01"}.items()}
        record["observations"]=[{"id":"row-"+p,"parameter":"Force error","requirement_id":"F1","unit":"N","point":p,"location":loc,
                                 "inputs":{"error":{"value":error,"unit":unit},"uncertainty":{"value":"0.1","unit":"N"}},
                                 "reported_fields":["indication_error","uncertainty"]} for p in points]
        if ranges is not None:
            record["observations"]=[{"id":f"range-{i}","parameter":"Force error","requirement_id":"F1","unit":"N","range":{"minimum":low,"maximum":high},"location":loc,
                                      "inputs":{"error":{"value":error,"unit":unit}},"reported_fields":["indication_error","uncertainty"]} for i,(low,high) in enumerate(ranges)]
        record_evidence(self.ws,"LAB_A",record)
        return record

    def test_extraction_is_idempotent_and_locations_are_normalized(self):
        second=add_certificate(self.ws,"LAB_A",file=self.pdf,ocr=False)
        self.assertTrue(second["reused"]);self.assertEqual(second["extraction_id"],self.cert["extraction_id"])
        self.assertEqual(self.ext["payload"]["document_kind"],"calibration")
        for page in self.ext["payload"]["pages"]:
            for line in page["lines"]: self.assertTrue(all(0<=x<=1 for x in line["bbox"]))

    def test_machine_extraction_never_confirms_absence_or_device_fitness(self):
        r=create_review(self.ws,"LAB_A",[self.ext["id"]]);payload=get_review(self.ws,"LAB_A",r["review_id"])["payload"]
        self.assertEqual(payload["axes"],{"document":"pending","coverage":"pending","fitness":"pending"})
        self.assertFalse(payload["technical_approval"])
        self.assertTrue(all(x["certainty"]=="pending" for x in payload["findings"]))

    def test_verification_does_not_inherit_calibration_uncertainty_check(self):
        ver=add_certificate(self.ws,"LAB_A",file=self.make_pdf("verification.pdf",kind="verification"),ocr=False)
        r=create_review(self.ws,"LAB_A",[ver["extraction_id"]]);payload=get_review(self.ws,"LAB_A",r["review_id"])["payload"]
        self.assertEqual(payload["certificate_snapshots"][0]["document_kind"],"verification")
        self.assertNotIn("uncertainty",[x["field"] for x in payload["checks"]])

    def test_reviewed_date_contradiction_fails_document_axis(self):
        record=evidence_template(self.ws,"LAB_A",self.ext["id"])
        record.update(reviewer="TEST_ONLY_OPERATOR",reviewed_at=utc_now())
        record["fields"]={"activity_date":{"value":"2026-10-01","location":self.loc},"issue_date":{"value":"2026-09-30","location":self.loc}}
        record_evidence(self.ws,"LAB_A",record)
        result=create_review(self.ws,"LAB_A",[self.ext["id"]])
        self.assertEqual(result["axes"]["document"],"failed")

    def test_evidence_requires_correct_version_and_real_location(self):
        record=evidence_template(self.ws,"LAB_A",self.ext["id"])
        record.update(reviewer="TEST_ONLY_OPERATOR",reviewed_at=utc_now(),expected_extraction_hash="wrong")
        self.assert_error("CERTIFICATE_EVIDENCE_STALE",record_evidence,self.ws,"LAB_A",record)
        record["expected_extraction_hash"]=self.ext["payload_hash"]
        record["fields"]={"issuer":{"value":"TEST ONLY","location":{"page":1,"bbox":[0,0,1,1],"line_ids":["not-in-document"]}}}
        self.assert_error("LOCATION_LINE_MISMATCH",record_evidence,self.ws,"LAB_A",record)

    def test_certificate_evidence_does_not_cross_laboratories(self):
        self.assert_error("EXTRACTION_NOT_FOUND",get_extraction,self.ws,"LAB_B",self.ext["id"])

    def test_absence_declaration_requires_whole_document_review(self):
        record=evidence_template(self.ws,"LAB_A",self.ext["id"])
        record.update(reviewer="TEST_ONLY_OPERATOR",reviewed_at=utc_now(),absent_fields=["method"])
        self.assert_error("WHOLE_DOCUMENT_REVIEW_REQUIRED",record_evidence,self.ws,"LAB_A",record)

    def test_confirmed_baseline_and_reviewed_points_enable_comparison(self):
        baseline=self.baseline();self.evidence()
        r=create_review(self.ws,"LAB_A",[self.ext["id"]],baseline_id=baseline)
        self.assertEqual(r["axes"]["coverage"],"passed");self.assertEqual(r["axes"]["fitness"],"passed")
        self.assertEqual(r["axes"]["document"],"pending")

    def test_complete_result_record_confirms_missing_point(self):
        baseline=self.baseline();self.evidence(("20","100"))
        r=create_review(self.ws,"LAB_A",[self.ext["id"]],baseline_id=baseline)
        self.assertEqual(r["axes"]["coverage"],"failed");self.assertEqual(r["axes"]["fitness"],"pending")
        p=get_review(self.ws,"LAB_A",r["review_id"])["payload"]
        self.assertEqual(p["comparisons"][0]["missing_points"],["50"])

    def test_incomplete_result_entry_is_pending_instead_of_confirmed_missing(self):
        baseline=self.baseline();self.evidence(("20","100"),complete=False)
        r=create_review(self.ws,"LAB_A",[self.ext["id"]],baseline_id=baseline)
        self.assertEqual(r["axes"]["coverage"],"pending")

    def test_multiple_certificates_cover_one_device(self):
        baseline=self.baseline();second=add_certificate(self.ws,"LAB_A",file=self.make_pdf("second.pdf",rotation=90),ocr=False)
        self.evidence(("20",));self.evidence(("50","100"),cert=second)
        r=create_review(self.ws,"LAB_A",[self.ext["id"],second["extraction_id"]],baseline_id=baseline)
        self.assertEqual(r["axes"]["coverage"],"passed")

    def test_disconnected_ranges_do_not_claim_full_coverage(self):
        baseline=self.baseline(policy="range");self.evidence(points=(),ranges=[("0","40"),("60","100")])
        r=create_review(self.ws,"LAB_A",[self.ext["id"]],baseline_id=baseline)
        self.assertEqual(r["axes"]["coverage"],"failed")

    def test_out_of_limit_result_has_confirmed_evidence_location(self):
        baseline=self.baseline();self.evidence(error="2")
        r=create_review(self.ws,"LAB_A",[self.ext["id"]],baseline_id=baseline)
        self.assertEqual(r["axes"]["fitness"],"failed")
        p=get_review(self.ws,"LAB_A",r["review_id"])["payload"]
        self.assertTrue(any(x["code"]=="RESULT_OUTSIDE_REQUIREMENT" and x["certainty"]=="confirmed" and x["location"]==self.loc for x in p["findings"]))

    def test_incompatible_result_units_require_review(self):
        baseline=self.baseline();self.evidence(unit="mm")
        r=create_review(self.ws,"LAB_A",[self.ext["id"]],baseline_id=baseline)
        self.assertEqual(r["axes"]["fitness"],"pending")

    def test_device_configuration_change_prevents_current_baseline_review(self):
        baseline=self.baseline();self.evidence()
        e=get_equipment(self.ws,"LAB_A",self.equipment["id"])
        update_equipment(self.ws,"LAB_A",e["id"],{"expected_config_hash":e["config_hash"],"configuration":{"sensor":"new"}})
        self.assert_error("BASELINE_NOT_CURRENT",create_review,self.ws,"LAB_A",[self.ext["id"]],baseline_id=baseline)

    def test_review_resolution_is_append_only_and_preserves_calculations(self):
        r=create_review(self.ws,"LAB_A",[self.ext["id"]]);before=get_review(self.ws,"LAB_A",r["review_id"])
        issue=before["payload"]["findings"][0]
        record={"review_id":r["review_id"],"expected_review_hash":before["payload_hash"],"finding_id":issue["id"],"action":"dismissed","reviewer":"TEST_ONLY_OPERATOR","reviewed_at":utc_now(),"reason":"Synthetic resolution test"}
        record_resolution(self.ws,"LAB_A",record)
        after=get_review(self.ws,"LAB_A",r["review_id"])
        self.assertEqual(after["payload"],before["payload"]);self.assertEqual(after["resolutions"][issue["id"]]["action"],"dismissed")

    def test_old_certificate_identity_record_does_not_silently_apply_to_new_configuration(self):
        self.baseline();self.evidence()
        e=get_equipment(self.ws,"LAB_A",self.equipment["id"])
        update_equipment(self.ws,"LAB_A",e["id"],{"expected_config_hash":e["config_hash"],"configuration":{"sensor":"new"}})
        current=self.baseline()
        review=create_review(self.ws,"LAB_A",[self.ext["id"]],baseline_id=current)
        self.assertEqual(review["axes"]["coverage"],"pending")
        self.assertEqual(review["axes"]["fitness"],"pending")

    def test_supplemental_findings_are_anchored_candidates_and_cannot_grant_approval(self):
        record={"extraction_id":self.ext["id"],"expected_extraction_hash":self.ext["payload_hash"],"origin":"agent_candidate","author":"TEST_AGENT",
                "findings":[{"code":"TABLE_UNIT","title":"TEST ONLY unit observation","message":"Synthetic semantic finding","basis":"TEST ONLY basis","suggestion":"Review the original","location":self.loc}]}
        record_candidate_findings(self.ws,"LAB_A",record)
        r=create_review(self.ws,"LAB_A",[self.ext["id"]]);payload=get_review(self.ws,"LAB_A",r["review_id"])["payload"]
        issue=next(x for x in payload["findings"] if x["code"]=="SUPPLEMENT:TABLE_UNIT")
        self.assertEqual(issue["certainty"],"pending");self.assertEqual(issue["location"],self.loc)
        record["findings"][0]["certainty"]="confirmed"
        self.assert_error("CONTRACT_INVALID",record_candidate_findings,self.ws,"LAB_A",record)

    def test_revised_observation_preserves_history_and_uses_latest_record(self):
        baseline=self.baseline();self.evidence()
        before=create_review(self.ws,"LAB_A",[self.ext["id"]],baseline_id=baseline)
        self.evidence(error="2")
        after=create_review(self.ws,"LAB_A",[self.ext["id"]],baseline_id=baseline,previous_review_id=before["review_id"])
        self.assertEqual(before["axes"]["fitness"],"passed");self.assertEqual(after["axes"]["fitness"],"failed")
        self.assertEqual(get_review(self.ws,"LAB_A",before["review_id"])["payload"]["axes"]["fitness"],"passed")

    def test_export_preserves_original_bytes_and_adds_pdf_annotations(self):
        original=file_hash(self.pdf);r=create_review(self.ws,"LAB_A",[self.ext["id"]])
        result=export_review(self.ws,"LAB_A",r["review_id"])
        self.assertEqual(file_hash(self.pdf),original)
        output=Path(result["directory"])/result["manifest"]["pdfs"][0]["file"]
        with fitz.open(output) as doc:
            self.assertGreater(sum(len(list(p.annots() or [])) for p in doc),0)
            self.assertTrue(any("待核实" in a.info.get("content","") for p in doc for a in (p.annots() or [])))

    def test_rotated_coordinate_annotation_round_trip(self):
        cert=add_certificate(self.ws,"LAB_A",file=self.make_pdf("rotated.pdf",rotation=90),ocr=False)
        r=create_review(self.ws,"LAB_A",[cert["extraction_id"]]);out=export_review(self.ws,"LAB_A",r["review_id"],annotation_style='standard')
        pdf=Path(out["directory"])/out["manifest"]["pdfs"][0]["file"]
        payload=get_review(self.ws,"LAB_A",r["review_id"])["payload"]
        expected=next(f for f in payload["findings"] if f["location"].get("kind")!="document")
        with fitz.open(pdf) as doc:
            page=doc[expected["location"]["page"]-1]
            annotation=next(a for a in page.annots() if a.type[0]==fitz.PDF_ANNOT_SQUARE and a.info["subject"]==expected["id"])
            actual=annotation.rect*page.rotation_matrix; box=expected["location"]["bbox"]
            # Square annotation rectangles include half of the 1pt border.
            self.assertAlmostEqual(actual.x0/page.rect.width,box[0],delta=1/page.rect.width)
            self.assertAlmostEqual(actual.y0/page.rect.height,box[1],delta=1/page.rect.height)

    @unittest.skipUnless(shutil.which("tesseract"),"Local OCR not installed")
    def test_scanned_page_uses_local_ocr_and_remains_unverified(self):
        cert=add_certificate(self.ws,"LAB_A",file=self.make_pdf("scan.pdf",scanned=True),ocr_engine="tesseract")
        ext=get_extraction(self.ws,"LAB_A",cert["extraction_id"])
        self.assertEqual(ext["payload"]["pages"][0]["method"],"ocr")
        self.assertTrue(ext["payload"]["pages"][0]["lines"])
        self.assertFalse(certificate_context(self.ws,"LAB_A",ext["id"])["kind_verified"])

    def test_backup_restore_includes_certificate_and_review_records(self):
        create_review(self.ws,"LAB_A",[self.ext["id"]])
        backup=self.root/"backup.zip";backup_laboratory(self.ws,"LAB_A",backup)
        other=Workspace(self.root/"restored")
        r=restore_laboratory(other,backup)
        self.assertEqual(r["integrity"]["counts"]["certificate_reviews"],1)
        self.assertEqual(get_extraction(other,"LAB_A",self.ext["id"])["payload_hash"],self.ext["payload_hash"])

    def test_institution_range_is_bound_to_activity_date(self):
        baseline=self.baseline();self.evidence()
        file=self.root/"scope.md";file.write_text("TEST ONLY INSTITUTION SCOPE")
        src=import_source(self.ws,file,kind="institution_scope",lab_id="LAB_A")
        record={"source_id":src["id"],"source_hash":src["sha256"],"institution":"TEST ONLY ISSUER","scheme":"cnas","valid_from":"2026-10-02","valid_until":"2027-10-01","parameters":[{"parameter":"Force error","unit":"N","minimum":"0","maximum":"100"}],"reviewer":"TEST_ONLY_OPERATOR","reviewed_at":utc_now(),"anchor":"test paragraph"}
        record_institution(self.ws,"LAB_A",record)
        r=create_review(self.ws,"LAB_A",[self.ext["id"]],baseline_id=baseline)
        self.assertEqual(get_review(self.ws,"LAB_A",r["review_id"])["payload"]["institution_checks"][0]["status"],"pending")
        record["valid_from"]="2026-09-01";record_institution(self.ws,"LAB_A",record)
        r=create_review(self.ws,"LAB_A",[self.ext["id"]],baseline_id=baseline)
        self.assertEqual(get_review(self.ws,"LAB_A",r["review_id"])["payload"]["institution_checks"][0]["status"],"passed")
        self.assertEqual(get_review(self.ws,"LAB_A",r["review_id"])["payload"]["institution_checks"][0]["cmc_status"],"pending")

    def test_loopback_workbench_requires_session_for_writes(self):
        server=make_server(self.ws,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=10)
            client.request("GET","/");response=client.getresponse();self.assertEqual(response.status,200);self.assertIn(b"LabCal",response.read())
            body=json.dumps({"id":"LAB_WEB","name":"TEST ONLY WEB"})
            client.request("POST","/api/laboratories",body,{"Content-Type":"application/json"});response=client.getresponse();self.assertEqual(response.status,400);response.read()
            headers={"Content-Type":"application/json","X-Labcal-Token":server.token}
            client.request("POST","/api/laboratories",body,headers);response=client.getresponse();self.assertEqual(response.status,200);response.read()
            client.request("GET","/api/extraction?lab=LAB_B&id="+self.ext["id"]);response=client.getresponse();self.assertEqual(response.status,400);self.assertEqual(json.loads(response.read())["error"]["code"],"CAMPAIGN_REQUIRED")
            client.close()
        finally: server.shutdown();server.server_close();thread.join(timeout=5)


if __name__ == "__main__": unittest.main()
