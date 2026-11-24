"""Automatic workflow invariants, isolated synthetic labs and source fixtures only."""
import base64
import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from labcal.adapters.storage import Workspace
from labcal.adapters.recognition import recognized_fields
from labcal.adapters.pdf import extract_pdf
from labcal.adapters.pdf_process import extract_pdf_isolated, render_page_isolated
from labcal.adapters.server import make_server
from labcal.application.campaigns import create_campaign, attach_item, campaign_directory, update_campaign
from labcal.application.certificates import add_certificate, get_extraction, get_review
from labcal.application.laboratories import create_laboratory
from labcal.application.report_intake import ReportIntake, get_job, list_jobs
from labcal.application.report_workspace import (associate_report, device_catalog, intake_store, latest_association,
                                               report_detail, review_report, review_related)
from labcal.application.sources import verify_source_integrity
from labcal.application.workbooks import import_workbook, get_workbook
from labcal.application.maintenance import backup_laboratory, restore_laboratory, doctor
from labcal.domain.recognition import classify_devices, match_identity
from labcal.errors import LabcalError
from labcal.util import content_hash, file_hash, canonical_json, utc_now
from test_workbook_import import workbook
try:
    import fitz
except ImportError:
    fitz = None


def fields(**kwargs): return {key: [{'value': value}] for key, value in kwargs.items()}
def device(key='d1', number='001', model='MOD-1', name='Synthetic instrument'):
    return {'id':key,'name':name,'identity':{'equipment_name':name,'equipment_number':number,'model':model,'manufacturer':None}}


class IdentityTests(unittest.TestCase):
    def test_unique_number_matches_without_implying_technical_approval(self):
        result=match_identity(fields(instrument_number='001',model='MOD-1'),[device()])
        self.assertEqual(result['state'],'matched'); self.assertFalse(result['technical_approval'])

    def test_name_only_association_stays_provisional(self):
        result=match_identity(fields(instrument_name='Synthetic instrument'),[device(number=None,model=None)])
        self.assertEqual(result['state'],'suggested')

    def test_number_conflict_prevents_model_and_name_from_selecting_wrong_device(self):
        result=match_identity(fields(instrument_name='Synthetic instrument',model='MOD-1',instrument_number='002'),[device()])
        self.assertEqual(result['state'],'conflict');self.assertIsNone(result['device_id'])

    def test_duplicate_numbers_are_ambiguous(self):
        result=match_identity(fields(instrument_number='001'),[device(),device('d2')])
        self.assertEqual(result['state'],'ambiguous');self.assertIsNone(result['device_id'])

    def test_conflicting_report_identity_values_never_auto_select(self):
        value=fields(instrument_number='001');value['instrument_number'].append({'value':'002'})
        self.assertEqual(match_identity(value,[device()])['state'],'conflict')

    def test_leading_zero_and_no_input_are_not_ignored(self):
        self.assertNotEqual(match_identity(fields(instrument_number='1'),[device()])['state'],'matched')
        self.assertEqual(match_identity({},[device()])['state'],'unmatched')

    def test_classification_groups_rows_but_separates_distinct_identifiers(self):
        rows=[{'id':str(i),'sheet':'s','group':'g','anchor':f'A{i}', 'fields':{'equipment_name':'X','model':None,'manufacturer':None,'equipment_number':n}} for i,n in enumerate(['1','1','2'])]
        result=classify_devices('WB-X','f'*64,rows)
        self.assertEqual([len(d['rows']) for d in result],[2,1]);self.assertTrue(all(d['equipment_id'] is None for d in result))

    def test_standard_models_from_later_pages_do_not_become_device_models(self):
        def line(text,n,page=1): return {'text':text,'id':str(n),'page':page,'bbox':[.1,.1+n*.04,.8,.12+n*.04]}
        p={'fields':{},'pages':[{'lines':[line('Instrument name: Synthetic instrument',1),line('Model/Type: MOD-1',2),line('Serial No: 001',3)]},
                              {'lines':[line('Model/Type: STANDARD-MODEL',1,2)]}]}
        result=recognized_fields(p)
        self.assertEqual([v['value'] for v in result['model']],['MOD-1'])
        self.assertEqual(result['instrument_number'][0]['value'],'001')

    def test_split_bilingual_identity_uses_value_region_and_skips_english_label(self):
        p={'fields':{},'pages':[{'lines':[
            {'id':'a','page':1,'text':'仪 器 编 号：','bbox':[.08,.48,.21,.498]},
            {'id':'b','page':1,'text':'No.','bbox':[.08,.499,.11,.511]},
            {'id':'c','page':1,'text':'0007','bbox':[.50,.498,.67,.513]}]}]}
        value=recognized_fields(p)['instrument_number'][0]
        self.assertEqual(value['value'],'0007');self.assertIn('c',value['location']['line_ids'])

    def test_section_decoration_only_supports_provisional_name_matching(self):
        d=device(name='【设备组 · TEST】模拟仪器 MODEL-42 (共 8 项参数)',number=None,model=None)
        self.assertEqual(match_identity(fields(instrument_name='模拟仪器'),[d])['state'],'suggested')
        self.assertIsNone(d['identity']['model'])

    def test_same_page_standard_section_is_excluded_from_device_identity(self):
        lines=[{'id':str(n),'page':1,'text':text,'bbox':[.1,n*.1,.8,n*.1+.02]} for n,text in enumerate(['Model/Type: DEVICE-X','Standards used:','Model/Type: STANDARD-Y'],1)]
        result=recognized_fields({'fields':{},'pages':[{'lines':lines}]})
        self.assertEqual([c['value'] for c in result['model']],['DEVICE-X'])

    def test_document_title_does_not_become_an_issuer(self):
        lines=[{'id':'p1','page':1,'text':'CALIBRATION CERTIFICATE','bbox':[.1,.1,.8,.15]}]
        result=recognized_fields({'fields':{'issuer':[{'value':'CALIBRATION CERTIFICATE'}]},'pages':[{'lines':lines}]})
        self.assertNotIn('issuer',result)

    def test_customer_company_below_customer_label_is_not_an_issuer(self):
        p={'fields':{},'pages':[{'lines':[
            {'id':'title','page':1,'text':'CALIBRATION CERTIFICATE','bbox':[.1,.1,.8,.15]},
            {'id':'label','page':1,'text':'客户单位：','bbox':[.1,.2,.25,.23]},
            {'id':'value','page':1,'text':'TEST CUSTOMER LIMITED','bbox':[.4,.2,.8,.23]}]}]}
        result=recognized_fields(p)
        self.assertEqual(result['client'][0]['value'],'TEST CUSTOMER LIMITED')
        self.assertNotIn('issuer',result)

    def test_split_date_sorts_year_month_day_even_when_month_is_closest_to_label(self):
        def item(key,text,x,y):return {'id':key,'page':1,'text':text,'bbox':[x,y,x+.08,y+.018]}
        p={'fields':{},'pages':[{'lines':[item('label','校准日期：',.1,.2),item('year','2030年',.4,.203),item('month','07月',.55,.2),item('day','13日',.7,.203)]}]}
        self.assertEqual(recognized_fields(p)['activity_date'][0]['value'],'2030-07-13')

    def test_calibration_does_not_treat_standard_validity_as_verification_validity(self):
        lines=[{'id':'p1','page':1,'text':'有效期至：2027-01-01','bbox':[.1,.1,.8,.15]}]
        result=recognized_fields({'document_kind':'calibration','fields':{'verification_valid_until':[{'value':'2027-01-01'}]},'pages':[{'lines':lines}]})
        self.assertNotIn('verification_valid_until',result)


@unittest.skipUnless(fitz,'PDF optional dependency')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.ws=Workspace(self.root/'workspace');self.ws.initialize()
        for lab in ['LAB_A','LAB_B']:create_laboratory(self.ws,lab,'TEST ONLY '+lab,test_only=True)
        self.c=create_campaign(self.ws,'LAB_A','Test annual',year=2026)
        self.other=create_campaign(self.ws,'LAB_A','Other year',year=2027)
        self.book=self.root/'plan.xlsx'
        workbook(self.book,[['设备名称','厂家','设备型号','设备编号','校准参数','允许误差','不确定度要求'],
            ['Synthetic instrument',None,'MOD-1','001','Force',None,None],
            ['Other instrument',None,'MOD-2','002','Temperature',None,None]])
        self.wb=import_workbook(self.ws,'LAB_A',self.c['id'],self.book)
        self.file=self.pdf('report.pdf')

    def tearDown(self): self.temp.cleanup()

    def pdf(self,name,number='001',model='MOD-1',title='Synthetic instrument'):
        file=self.root/name
        with fitz.open() as d:
            for page_no in range(2):
                p=d.new_page()
                lines=['CALIBRATION CERTIFICATE','Certificate No: '+name,'Instrument name: '+title,'Model/Type: '+model,'Serial No: '+number,'Calibration results: TEST ONLY','Uncertainty: 0.1 N'] if page_no==0 else ['Standards','Model/Type: STANDARD-X','TEST DATA ONLY']
                for i,text in enumerate(lines):p.insert_text((40,60+i*30),text)
            d.save(file)
        return file

    def extraction(self,file=None):
        r=add_certificate(self.ws,'LAB_A',file=file or self.file,ocr=False)
        attach_item(self.ws,'LAB_A',self.c['id'],'extraction',r['extraction_id'])
        p=get_extraction(self.ws,'LAB_A',r['extraction_id'])['payload']
        attach_item(self.ws,'LAB_A',self.c['id'],'source',p['source_id'])
        return r['extraction_id']

    def test_auto_catalog_and_match_create_no_real_equipment_or_confirmation(self):
        devices=device_catalog(self.ws,'LAB_A',self.c['id'])['devices'];self.assertEqual(len(devices),2)
        key=self.extraction();a=associate_report(self.ws,'LAB_A',self.c['id'],key)
        self.assertEqual(a['payload']['match']['state'],'matched')
        with intake_store(self.ws,'LAB_A').connect() as db:
            for table in ['equipment','confirmations','baselines']:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)

    def test_same_report_different_batch_uses_separate_plan_context(self):
        key=self.extraction()
        with self.assertRaises(LabcalError):report_detail(self.ws,'LAB_A',self.other['id'],key)
        attach_item(self.ws,'LAB_A',self.other['id'],'extraction',key)
        a=associate_report(self.ws,'LAB_A',self.other['id'],key)
        self.assertEqual(a['payload']['match']['state'],'unmatched')
        with self.assertRaises(LabcalError):report_detail(self.ws,'LAB_B',self.c['id'],key)

    def test_repeated_association_reuses_snapshot_but_choice_remains_explicit(self):
        key=self.extraction();a=associate_report(self.ws,'LAB_A',self.c['id'],key)
        self.assertEqual(a['id'],associate_report(self.ws,'LAB_A',self.c['id'],key)['id'])
        target=device_catalog(self.ws,'LAB_A',self.c['id'])['devices'][1]['id']
        choice={'expected_association_id':a['id'],'device_id':target,'operator':'TEST_ONLY','statement':'Simulated document correspondence'}
        b=associate_report(self.ws,'LAB_A',self.c['id'],key,choice=choice)
        self.assertEqual(b['payload']['match']['state'],'operator_linked')
        self.assertEqual(associate_report(self.ws,'LAB_A',self.c['id'],key)['id'],b['id'])
        with self.assertRaises(LabcalError):associate_report(self.ws,'LAB_A',self.c['id'],key,choice=choice)

    def test_choice_cannot_invent_device_or_actor(self):
        key=self.extraction();a=associate_report(self.ws,'LAB_A',self.c['id'],key)
        for target,actor in [('foreign-device','TEST_ONLY'),(a['payload']['match']['device_id'],' ')]:
            with self.assertRaises(LabcalError):associate_report(self.ws,'LAB_A',self.c['id'],key,choice={'expected_association_id':a['id'],'device_id':target,'operator':actor,'statement':'TEST ONLY'})

    def test_unconfirmed_plan_keeps_eight_diagnostic_axes_pending(self):
        key=self.extraction();r=review_report(self.ws,'LAB_A',self.c['id'],key)
        detail=report_detail(self.ws,'LAB_A',self.c['id'],key)
        self.assertIsNone(get_review(self.ws,'LAB_A',r['review_id'])['payload']['baseline_id'])
        self.assertEqual({d['key'] for d in detail['diagnostics']}, {'information','traceability','method','coverage','results','uncertainty','integrity','numeric'})
        self.assertEqual(next(d for d in detail['diagnostics'] if d['key']=='results')['state'],'not_started')
        self.assertTrue(all(d['status']=='pending' for d in detail['diagnostics']))
        self.assertFalse(detail['model_service_connected'])

    def test_historical_diagnostics_bind_selected_review_and_reject_other_report(self):
        a=self.extraction();old=review_report(self.ws,'LAB_A',self.c['id'],a)
        review_report(self.ws,'LAB_A',self.c['id'],a)
        detail=report_detail(self.ws,'LAB_A',self.c['id'],a,review_id=old['review_id'])
        self.assertEqual(detail['review']['id'],old['review_id'])
        b=self.extraction(self.pdf('another.pdf'))
        other=review_report(self.ws,'LAB_A',self.c['id'],b)
        with self.assertRaises(LabcalError):report_detail(self.ws,'LAB_A',self.c['id'],a,review_id=other['review_id'])

    def test_reprocessing_detects_corrupt_archive_without_overwriting_it(self):
        key=self.extraction();associate_report(self.ws,'LAB_A',self.c['id'],key)
        file=Path(report_detail(self.ws,'LAB_A',self.c['id'],key)['archive_directory'])/'原件.pdf'
        file.chmod(0o644);file.write_bytes(b'changed archived copy')
        with self.assertRaises(LabcalError):associate_report(self.ws,'LAB_A',self.c['id'],key)
        self.assertEqual(file.read_bytes(),b'changed archived copy')

    def test_candidate_comparison_has_two_sides_and_does_not_grant_conformity(self):
        key=self.extraction();review_report(self.ws,'LAB_A',self.c['id'],key)
        report=report_detail(self.ws,'LAB_A',self.c['id'],key)['comparison_report']
        self.assertEqual(report['basis']['mode'],'candidate_preview')
        self.assertTrue(all('plan' in r and 'certificate' in r for r in report['rows']))
        self.assertTrue(all(r['status']=='pending' for r in report['rows']))
        self.assertEqual([r['title'] for r in report['rows'] if r['category']=='parameters'],['Force'])
        self.assertEqual(next(r for r in report['rows'] if r['title']=='厂家')['plan']['text'],'-')
        self.assertFalse(report['technical_approval'])
        with intake_store(self.ws,'LAB_A').connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM certificate_evidence').fetchone()[0],0)

    def test_unmatched_report_has_no_fabricated_plan_requirements(self):
        key=self.extraction();attach_item(self.ws,'LAB_A',self.other['id'],'extraction',key)
        report=report_detail(self.ws,'LAB_A',self.other['id'],key)['comparison_report']
        self.assertEqual(report['basis']['mode'],'unmatched')
        self.assertFalse(any(r['category']=='parameters' for r in report['rows']))
        self.assertTrue(all(r['status']=='pending' for r in report['rows']))

    def test_identity_difference_is_a_candidate_difference_not_confirmed_failure(self):
        key=self.extraction();a=associate_report(self.ws,'LAB_A',self.c['id'],key)
        target=next(d for d in device_catalog(self.ws,'LAB_A',self.c['id'])['devices'] if d['identity']['equipment_number']=='002')
        associate_report(self.ws,'LAB_A',self.c['id'],key,choice={'expected_association_id':a['id'],'device_id':target['id'],'operator':'TEST_ONLY','statement':'Simulate a candidate correspondence difference'})
        report=report_detail(self.ws,'LAB_A',self.c['id'],key)['comparison_report']
        row=next(r for r in report['rows'] if r['title']=='设备编号')
        self.assertEqual(row['signal'],'conflict');self.assertEqual(row['status'],'pending')
        self.assertEqual(row['plan']['text'],'002');self.assertEqual(row['certificate']['text'],'001')

    def test_joint_review_only_unions_same_device(self):
        a=self.extraction();b=self.extraction(self.pdf('part2.pdf'));c=self.extraction(self.pdf('other.pdf','002','MOD-2','Other instrument'))
        for key in [a,b,c]:review_report(self.ws,'LAB_A',self.c['id'],key)
        r=review_related(self.ws,'LAB_A',self.c['id'],a);p=get_review(self.ws,'LAB_A',r['review_id'])['payload']
        self.assertEqual({s['extraction_id'] for s in p['certificate_snapshots']},{a,b});self.assertIsNone(p['baseline_id'])

    def test_original_archived_before_extraction_and_progress_reports_real_pages(self):
        from labcal.application.certificates import add_certificate as original
        seen=[]
        def inspect(ws,lab,**kwargs):
            source=verify_source_integrity(ws,lab,kwargs['source_id'])
            directory=campaign_directory(ws,lab,self.c)/'证书报告'/'原件档案'/source['id']
            self.assertEqual(file_hash(directory/'原件.pdf'),file_hash(self.file))
            progress=kwargs['progress']
            def capture(*args):
                seen.append(args);progress(*args)
                if args==('reading_page',1,2):self.assertTrue(render_page_isolated(self.file,1).startswith(b'\x89PNG'))
            kwargs['progress']=capture
            return original(ws,lab,**kwargs)
        manager=ReportIntake(self.ws)
        with patch('labcal.application.report_intake.add_certificate',side_effect=inspect):
            job=manager.submit('LAB_A',self.c['id'],file=self.file);manager.close()
        done=get_job(self.ws,'LAB_A',self.c['id'],job['id'])
        self.assertEqual(done['state'],'completed');self.assertEqual(done['page'],2)
        self.assertEqual(seen,[('reading_page',1,2),('page_read',1,2),('reading_page',2,2),('page_read',2,2)])
        self.assertEqual(file_hash(self.file),file_hash(verify_source_integrity(self.ws,'LAB_A',done['source_id'])['absolute_path']))

    def test_process_boundary_preserves_exact_extraction_payload(self):
        args=(self.file,'SRC-test',file_hash(self.file));seen=[]
        expected=extract_pdf(*args,ocr=False)
        actual=extract_pdf_isolated(*args,ocr=False,progress=lambda *event:seen.append(event))
        self.assertEqual(content_hash(actual),content_hash(expected))
        self.assertEqual(seen[-1],('page_read',2,2))

    def test_one_bad_report_does_not_abort_other_job_and_original_is_retained(self):
        broken=self.root/'broken.pdf';broken.write_bytes(b'%PDF-1.7\nInvalid PDF container')
        manager=ReportIntake(self.ws)
        bad=manager.submit('LAB_A',self.c['id'],file=broken);good=manager.submit('LAB_A',self.c['id'],file=self.file);manager.close()
        failed=get_job(self.ws,'LAB_A',self.c['id'],bad['id'])
        self.assertEqual(failed['state'],'failed');self.assertTrue(failed['source_id'])
        self.assertEqual(get_job(self.ws,'LAB_A',self.c['id'],good['id'])['state'],'completed')
        with self.assertRaises(LabcalError):get_job(self.ws,'LAB_A',self.other['id'],bad['id'])

    def test_simultaneous_identical_imports_keep_one_original_and_extraction(self):
        manager=ReportIntake(self.ws)
        a=manager.submit('LAB_A',self.c['id'],file=self.file);b=manager.submit('LAB_A',self.c['id'],file=self.file);manager.close()
        jobs=[get_job(self.ws,'LAB_A',self.c['id'],j['id']) for j in [a,b]]
        self.assertTrue(all(j['state']=='completed' for j in jobs),str(jobs))
        self.assertEqual(jobs[0]['source_id'],jobs[1]['source_id'])
        self.assertEqual(jobs[0]['result']['extraction_id'],jobs[1]['result']['extraction_id'])

    def test_restart_marks_unfinished_job_interrupted(self):
        job={'id':'JOB-test','lab_id':'LAB_A','campaign_id':self.c['id'],'filename':'test.pdf','state':'running','stage':'reading_page','page':1,'pages':2,'source_id':None,'result':None,'error':None,'updated_at':utc_now()}
        with intake_store(self.ws,'LAB_A').connect() as db:db.execute('INSERT INTO report_jobs VALUES (?,?,?,?)',(job['id'],self.c['id'],canonical_json(job),job['updated_at']))
        manager=ReportIntake(self.ws);manager.close()
        self.assertEqual(get_job(self.ws,'LAB_A',self.c['id'],job['id'])['state'],'interrupted')

    def test_report_folders_move_with_year_without_changing_original(self):
        key=self.extraction();review_report(self.ws,'LAB_A',self.c['id'],key)
        before=Path(report_detail(self.ws,'LAB_A',self.c['id'],key)['archive_directory']);digest=file_hash(before/'原件.pdf')
        update_campaign(self.ws,'LAB_A',self.c['id'],{'year':2028,'expected_record_hash':self.c['record_hash']})
        after=Path(report_detail(self.ws,'LAB_A',self.c['id'],key)['archive_directory'])
        self.assertFalse(before.exists());self.assertEqual(file_hash(after/'原件.pdf'),digest)

    def test_backup_restore_preserves_report_association_and_archive(self):
        key=self.extraction();a=associate_report(self.ws,'LAB_A',self.c['id'],key)
        backup=self.root/'backup.zip';backup_laboratory(self.ws,'LAB_A',backup)
        target=Workspace(self.root/'restored');restore_laboratory(target,backup)
        restored=report_detail(target,'LAB_A',self.c['id'],key)
        self.assertEqual(restored['association']['payload_hash'],a['payload_hash'])
        self.assertEqual(file_hash(Path(restored['archive_directory'])/'原件.pdf'),file_hash(self.file))
        self.assertEqual(doctor(target,'LAB_A')['counts']['report_associations'],1)

    def test_doctor_detects_changed_association_snapshot(self):
        key=self.extraction();a=associate_report(self.ws,'LAB_A',self.c['id'],key)
        with intake_store(self.ws,'LAB_A').connect() as db:
            db.execute('UPDATE report_associations SET payload_hash=? WHERE id=?',('0'*64,a['id']))
        self.assertTrue(any(i['code']=='REPORT_RECORD_INTEGRITY_FAILED' for i in doctor(self.ws,'LAB_A')['issues']))

    def test_http_report_intake_is_scoped_and_returns_background_job(self):
        server=make_server(self.ws,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,body=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
            connection.request(method,path,json.dumps(body) if body else None,{'X-Labcal-Token':server.token,'Content-Type':'application/json'})
            response=connection.getresponse();value=json.loads(response.read());connection.close();return response.status,value
        try:
            status,_=request('GET','/api/report-jobs?lab=LAB_A');self.assertEqual(status,400)
            status,r=request('POST','/api/report-jobs',{'lab_id':'LAB_A','campaign_id':self.c['id'],'filename':'report.pdf','file_base64':base64.b64encode(self.file.read_bytes()).decode()})
            self.assertEqual(status,200);self.assertIn(r['result']['state'],['queued','running'])
            server.intake.close()
            status,r=request('GET',f'/api/report-jobs?lab=LAB_A&campaign={self.c["id"]}')
            self.assertEqual(r['result'][0]['state'],'completed')
            key=r['result'][0]['result']['extraction_id']
            status,_=request('GET',f'/api/report?lab=LAB_A&campaign={self.other["id"]}&id={key}');self.assertEqual(status,400)
        finally:server.shutdown();server.server_close();thread.join()

@unittest.skipUnless(fitz,'PDF optional dependency')
class ConfirmedComparisonTests(unittest.TestCase):
    def test_two_column_conclusions_use_fixed_baseline_and_existing_measured_results(self):
        import test_certificates as fixtures
        from labcal.application.certificate_reviews import create_review
        from labcal.application.plans import get_baseline
        case=fixtures.CertificateTests('test_extraction_is_idempotent_and_locations_are_normalized');case.setUp()
        try:
            baseline=case.baseline();c=create_campaign(case.ws,'LAB_A','TEST ONLY comparison',year=2026)
            attach_item(case.ws,'LAB_A',c['id'],'extraction',case.cert['extraction_id'])
            attach_item(case.ws,'LAB_A',c['id'],'baseline',baseline)
            for error,expected in [('0.2','passed'),('2.0','failed')]:
                with self.subTest(error=error):
                    case.evidence(error=error)
                    r=create_review(case.ws,'LAB_A',[case.cert['extraction_id']],baseline_id=baseline)
                    attach_item(case.ws,'LAB_A',c['id'],'review',r['review_id'])
                    report=report_detail(case.ws,'LAB_A',c['id'],case.cert['extraction_id'],review_id=r['review_id'])['comparison_report']
                    self.assertEqual(report['basis']['mode'],'confirmed_baseline')
                    self.assertEqual(report['basis']['hash'],get_baseline(case.ws,'LAB_A',baseline)['payload_hash'])
                    row=next(r for r in report['rows'] if r['category']=='parameters')
                    self.assertEqual(row['status'],expected);self.assertIn(error,row['certificate']['text'])
                    self.assertEqual({a['extraction_id'] for a in row['certificate']['anchors']},{case.cert['extraction_id']})
        finally:case.tearDown()

if __name__=='__main__':unittest.main()
