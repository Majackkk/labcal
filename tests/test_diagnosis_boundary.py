"""The plan-authoring lane and report lane must not mutate one another's facts."""
import copy
import csv
import http.client
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import test_report_workflow as wf
import test_certificates as cf
from labcal.application.campaigns import attach_item, create_campaign
from labcal.application.certificate_reviews import create_review
from labcal.application.certificates import get_extraction, get_review
from labcal.application.intelligence import create_packet, import_result
from labcal.application.report_corrections import correct_report_field, report_fields
from labcal.application.report_workspace import report_detail, review_report, intake_store
from labcal.application.laboratories import add_equipment
from labcal.application.workbooks import draft_from_rows, get_workbook
from labcal.adapters.certificate_export import export_review
from labcal.adapters.diagnosis_export import write_xlsx
from labcal.adapters.pdf import extract_pdf, _supplement_header
from labcal.adapters.server import make_server
from labcal.errors import LabcalError
from labcal.util import content_hash, file_hash


@unittest.skipUnless(wf.fitz,'Optional PDF dependency')
class DiagnosisBoundaryTests(unittest.TestCase):
    setUp=wf.WorkflowTests.setUp
    tearDown=wf.WorkflowTests.tearDown
    pdf=wf.WorkflowTests.pdf
    extraction=wf.WorkflowTests.extraction

    def plans_digest(self):
        with intake_store(self.ws,'LAB_A').connect() as db:
            return {t:content_hash([dict(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY rowid')])
                for t in ['equipment','plans','plan_revisions','evidence_reviews','confirmations','baselines','workbook_imports']}

    def change(self,key,field='client',value='TEST ONLY CORRECTED'):
        ext=get_extraction(self.ws,'LAB_A',key)
        fields=report_fields(self.ws,'LAB_A',self.c['id'],ext)
        return {'field':field,'value':value,'reason':'TEST ONLY actual test interaction',
            'location':{'page':1,'bbox':[.1,.1,.8,.2],'kind':'visual'},'expected_fields_hash':fields['fields_hash']}

    def test_correcting_report_preserves_plan_facts_original_and_historical_diagnosis(self):
        key=self.extraction();old=review_report(self.ws,'LAB_A',self.c['id'],key)
        detail=report_detail(self.ws,'LAB_A',self.c['id'],key);frozen=copy.deepcopy(detail['diagnosis'])
        original=file_hash(self.file);plans=self.plans_digest();ext=get_extraction(self.ws,'LAB_A',key)
        correct_report_field(self.ws,'LAB_A',self.c['id'],key,self.change(key))
        stale=report_detail(self.ws,'LAB_A',self.c['id'],key)
        self.assertTrue(stale['diagnosis_stale']);self.assertEqual(stale['diagnosis'],frozen)
        with self.assertRaises(LabcalError) as error:export_review(self.ws,'LAB_A',old['review_id'],campaign_id=self.c['id'])
        self.assertEqual(error.exception.code,'DIAGNOSIS_REFRESH_REQUIRED')
        new=review_report(self.ws,'LAB_A',self.c['id'],key);fresh=report_detail(self.ws,'LAB_A',self.c['id'],key)
        self.assertFalse(fresh['diagnosis_stale']);self.assertEqual(fresh['fields']['client'][0]['value'],'TEST ONLY CORRECTED')
        self.assertFalse(any(f['code']=='FIELD_CHECK:client' for f in fresh['review']['payload']['findings']))
        self.assertEqual(get_extraction(self.ws,'LAB_A',key),ext);self.assertEqual(file_hash(self.file),original)
        self.assertEqual(self.plans_digest(),plans);self.assertNotEqual(new['review_id'],old['review_id'])
        self.assertEqual(get_review(self.ws,'LAB_A',old['review_id'])['payload']['report_views'][key]['payload'],frozen)

    def test_legacy_database_with_multiple_reviews_upgrades_without_a_live_reader_lock(self):
        key=self.extraction()
        for _ in range(2):
            result=create_review(self.ws,'LAB_A',[key])
            attach_item(self.ws,'LAB_A',self.c['id'],'review',result['review_id'])
        with intake_store(self.ws,'LAB_A').connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='report_corrections'").fetchone())
        detail=report_detail(self.ws,'LAB_A',self.c['id'],key)
        self.assertTrue(detail['diagnosis_stale'])
        self.assertIsNone(detail['diagnosis'])

    def test_http_correction_refreshes_report_without_plan_writes(self):
        key=self.extraction();review_report(self.ws,'LAB_A',self.c['id'],key);plans=self.plans_digest()
        server=make_server(self.ws,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            body={'lab_id':'LAB_A','campaign_id':self.c['id'],'extraction_id':key,'change':self.change(key)}
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request('POST','/api/reports/correct-field',json.dumps(body),{'Content-Type':'application/json','X-Labcal-Token':server.token})
            response=conn.getresponse();data=json.loads(response.read());conn.close()
            self.assertEqual(response.status,200,data);self.assertFalse(data['result']['technical_approval'])
            self.assertTrue(report_detail(self.ws,'LAB_A',self.c['id'],key)['diagnosis_stale'])
            review_report(self.ws,'LAB_A',self.c['id'],key)
            self.assertEqual(report_detail(self.ws,'LAB_A',self.c['id'],key)['fields']['client'][0]['value'],'TEST ONLY CORRECTED')
            self.assertEqual(self.plans_digest(),plans)
        finally:server.shutdown();server.server_close();server.intake.close();thread.join()

    def test_corrections_are_scoped_and_cannot_write_limits_or_approval(self):
        key=self.extraction();change=self.change(key)
        for field in ['mpe','requirements','confirmed_by','technical_approval']:
            with self.assertRaises(LabcalError):correct_report_field(self.ws,'LAB_A',self.c['id'],key,{**change,'field':field})
        for lab,campaign in [('LAB_B',self.c['id']),('LAB_A',self.other['id'])]:
            with self.assertRaises(LabcalError):correct_report_field(self.ws,lab,campaign,key,change)
        correct_report_field(self.ws,'LAB_A',self.c['id'],key,change)
        with self.assertRaises(LabcalError):correct_report_field(self.ws,'LAB_A',self.c['id'],key,change)
        attach_item(self.ws,'LAB_A',self.other['id'],'extraction',key)
        other=report_fields(self.ws,'LAB_A',self.other['id'],get_extraction(self.ws,'LAB_A',key))
        self.assertNotEqual(other['fields'].get('client',[{}])[0].get('value'),change['value'])

    def test_unmatched_report_is_uncompared_and_preconditions_are_separate(self):
        key=self.extraction(self.pdf('unmatched.pdf',number='X',model='Y',title='Unmatched device'))
        import test_point_matrix as points
        from labcal.domain.point_matrix import certificate_points
        parsed,_=certificate_points(points.force_table())
        for row in parsed:row['anchor']['extraction_id']=key
        with patch('labcal.application.point_comparison.certificate_points',return_value=(parsed,[])):
            review_report(self.ws,'LAB_A',self.c['id'],key)
            detail=report_detail(self.ws,'LAB_A',self.c['id'],key)
        self.assertEqual(detail['point_matrix']['basis']['mode'],'unmatched')
        self.assertEqual(len(detail['point_matrix']['rows']),3)
        self.assertTrue(all(r['signal']=='uncompared' for r in detail['point_matrix']['rows']))
        self.assertIn('DEVICE_UNMATCHED',{p['code'] for p in detail['diagnosis']['preconditions']})
        baseline=next(f for f in detail['review']['payload']['findings'] if f['code']=='BASELINE_NOT_BOUND')
        self.assertEqual(baseline['category'],'precondition')

    def test_plan_draft_does_not_rewrite_report_and_invalidates_only_derived_context(self):
        key=self.extraction();result=review_report(self.ws,'LAB_A',self.c['id'],key)
        before=get_review(self.ws,'LAB_A',result['review_id']);ext=get_extraction(self.ws,'LAB_A',key)
        equipment=add_equipment(self.ws,'LAB_A','TEST-PLAN-ONLY','Synthetic instrument',model='MOD-1')
        book=get_workbook(self.ws,'LAB_A',self.wb['workbook_id'])
        draft_from_rows(self.ws,'LAB_A',self.c['id'],self.wb['workbook_id'],[book['candidate_view'][0]['id']],equipment['id'])
        self.assertEqual(get_review(self.ws,'LAB_A',result['review_id']),before)
        self.assertEqual(get_extraction(self.ws,'LAB_A',key),ext)
        self.assertTrue(report_detail(self.ws,'LAB_A',self.c['id'],key)['diagnosis_stale'])

    def test_model_packet_binds_report_corrections_without_confirming_them(self):
        key=self.extraction();packet=create_packet(self.ws,'LAB_A',self.c['id'],[key])
        correct_report_field(self.ws,'LAB_A',self.c['id'],key,self.change(key))
        proposed=copy.deepcopy(packet['template']);proposed['author']='TEST MODEL'
        with self.assertRaises(LabcalError) as error:import_result(self.ws,'LAB_A',self.c['id'],proposed)
        self.assertEqual(error.exception.code,'INTELLIGENCE_CONTEXT_CONFLICT')

    def test_full_export_uses_the_fixed_view_and_includes_all_point_rows(self):
        key=self.extraction();result=review_report(self.ws,'LAB_A',self.c['id'],key)
        detail=report_detail(self.ws,'LAB_A',self.c['id'],key)
        out=export_review(self.ws,'LAB_A',result['review_id'],campaign_id=self.c['id']);folder=Path(out['directory'])
        data=json.loads((folder/'diagnosis.json').read_text())
        self.assertEqual(data['views'][key],detail['diagnosis'])
        self.assertEqual(out['manifest']['diagnosis_views'][key],content_hash(detail['diagnosis']))
        with (folder/'comparison.csv').open(encoding='utf-8-sig') as f: rows=list(csv.reader(f))
        self.assertEqual(len(rows)-1,len(detail['point_matrix']['rows']))
        with zipfile.ZipFile(folder/'comparison.xlsx') as z:
            for name in z.namelist():
                if name.endswith('.xml'):ET.fromstring(z.read(name))
            self.assertNotIn(b'<f>',z.read('xl/worksheets/sheet2.xml'))
        self.assertNotIn('方案基准待绑定',(folder/'issues.md').read_text())

    def test_header_ocr_preserves_native_lines_and_only_adds_uncovered_image_text(self):
        class Page:
            rotation=0
            rect=wf.fitz.Rect(0,0,600,800)
            def get_image_info(self): return [{'bbox':(10,10,400,100)}]
        native=[{'text':'CALIBRATION CERTIFICATE','bbox':[.1,.15,.8,.2]}]
        candidates=[{'text':'TEST ONLY LAB','bbox':[.1,.03,.8,.08]}, {'text':'duplicate title','bbox':[.1,.15,.8,.2]}]
        with patch('labcal.adapters.pdf._ocr_lines',return_value=(candidates,None)) as ocr:
            new,error=_supplement_header(Page(),native,'eng')
        self.assertEqual([r['text'] for r in new],['TEST ONLY LAB']);self.assertEqual(len(native),1)
        self.assertEqual(ocr.call_args.kwargs['clip'].height,200)

    def test_xlsx_stores_source_formula_prefix_as_text(self):
        path=self.root/'safe.xlsx';write_xlsx(path,[('Test',[['Input'],['=HYPERLINK("bad")'],['-1.07'],['raw\x00text']],[35])])
        with zipfile.ZipFile(path) as z:
            text=z.read('xl/worksheets/sheet1.xml');ET.fromstring(text)
            self.assertNotIn(b'<f>',text);self.assertIn(b'-1.07',text);self.assertIn(b'inlineStr',text)

    def test_internal_number_page_and_date_checks_remain_candidate_findings(self):
        from labcal.domain.report_integrity import check_integrity
        line=lambda text,n,p:{'text':text,'id':str(n),'page':p,'bbox':[.1,.1,.8,.2]}
        loc={'page':1,'bbox':[.1,.1,.8,.2],'kind':'text'}
        context={'extraction_id':'EXT-TEST','payload':{'pages':[
            {'page':1,'lines':[line('Certificate No: TEST-AAA',1,1),line('Page 1 of 3',2,1)]},
            {'page':2,'lines':[line('Certificate No: TEST-BBB',3,2)]}], 'fields':{}},'fields':{},
            'candidate_fields':{'activity_date':[{'value':'2026-10-02','location':loc}],'issue_date':[{'value':'2026-10-01','location':loc}]}}
        checks,issues=check_integrity(context)
        self.assertEqual({f['code'] for f in issues},{'PRINTED_PAGE_SEQUENCE','CERTIFICATE_NUMBER_VARIANTS','CANDIDATE_DATE_ORDER'})
        self.assertTrue(all(f['certainty']=='pending' for f in issues))
        self.assertTrue(any(c.get('candidate_signal')=='inconsistent' for c in checks))


@unittest.skipUnless(cf.fitz,'Optional PDF dependency')
class PointJudgementTests(unittest.TestCase):
    def test_per_point_conclusions_require_fixed_baseline_and_verified_observation(self):
        case=cf.CertificateTests();case.setUp()
        try:
            baseline=case.baseline();campaign=create_campaign(case.ws,'LAB_A','TEST ONLY fixed decisions',year=2026)
            attach_item(case.ws,'LAB_A',campaign['id'],'extraction',case.cert['extraction_id'])
            attach_item(case.ws,'LAB_A',campaign['id'],'baseline',baseline)
            for error,expected in [('0.2','passed'),('2.0','failed')]:
                case.evidence(error=error)
                result=create_review(case.ws,'LAB_A',[case.cert['extraction_id']],baseline_id=baseline,campaign_id=campaign['id'])
                attach_item(case.ws,'LAB_A',campaign['id'],'review',result['review_id'])
                detail=report_detail(case.ws,'LAB_A',campaign['id'],case.cert['extraction_id'])
                points=[r for r in detail['point_matrix']['rows'] if r['plan']]
                self.assertEqual(len(points),3)
                self.assertEqual({r['status'] for r in points},{expected})
                self.assertTrue(all(r['decision_evidence'] for r in points))
            self.assertFalse(detail['diagnosis']['technical_approval'])
        finally:case.tearDown()
