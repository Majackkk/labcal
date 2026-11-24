"""Plan-free scope, evidence, immutable outputs and deterministic calculations."""
import json
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from labcal.adapters.storage import Workspace
from labcal.application.laboratories import create_laboratory
from labcal.application.campaigns import create_campaign,attach_item
from labcal.application.certificates import add_certificate,get_extraction
from labcal.application.self_diagnosis import prepare,load_task,page,finish
from labcal.domain.self_diagnosis import recalculate,candidate_fields
from labcal.application.storage_audit import audit_storage
from labcal.util import file_hash,content_hash
from labcal.errors import LabcalError
try:import fitz
except ImportError:fitz=None

@unittest.skipUnless(fitz,'optional PDF dependency')
class SelfDiagnosisTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.ws=Workspace(self.temp.name);self.ws.initialize()
  create_laboratory(self.ws,'LAB_TEST','TEST ONLY',test_only=True)
  self.c=create_campaign(self.ws,'LAB_TEST','TEST ONLY',year=2030)
  self.file=Path(self.temp.name)/'synthetic.pdf';d=fitz.open();p=d.new_page();p.insert_text((50,80),'Certificate TEST-001\nResult 1.0 V');d.save(self.file);d.close()
  self.ext=add_certificate(self.ws,'LAB_TEST',file=self.file,ocr=False)['extraction_id']
  attach_item(self.ws,'LAB_TEST',self.c['id'],'extraction',self.ext)
  from labcal.adapters.report_archive import preserve_report
  from labcal.application.campaigns import campaign_directory
  from labcal.application.sources import verify_source_integrity
  ext=get_extraction(self.ws,'LAB_TEST',self.ext)
  preserve_report(campaign_directory(self.ws,'LAB_TEST',self.c),verify_source_integrity(self.ws,'LAB_TEST',ext['payload']['source_id']))
  self.task=prepare(self.ws,'LAB_TEST',self.c['id'],[self.ext])
 def record(self,state='candidate_concern'):
  return {'task_hash':self.task['task_hash'],'author':'TEST ONLY agent','checks':[{'extraction_id':self.ext,'group':'numeric','item':'TEST computation','state':state,
    'reason':'TEST ONLY','basis':'TEST ONLY explicit fixture','suggestion':'TEST ONLY','evidence':[]}]}
 def finish(self,r,**kwargs):return finish(self.ws,'LAB_TEST',self.c['id'],self.task['task_id'],r,**kwargs)
 def test_plan_free_prepare_reads_no_plan_services_and_preserves_source(self):
  before=file_hash(self.file)
  with patch('labcal.application.report_workspace.device_catalog',side_effect=AssertionError('must not read plan')):
   prepared=prepare(self.ws,'LAB_TEST',self.c['id'],[self.ext])
  task=load_task(self.ws,'LAB_TEST',self.c['id'],prepared['task_id'])[1]['payload']
  self.assertNotIn('baseline',task);self.assertEqual(file_hash(self.file),before)
  self.assertTrue(any(c['state']=='not_checked' for c in task['documents'][0]['automatic_checks']))
 def test_foreign_scope_tampered_task_and_duplicate_inputs_rejected(self):
  other=create_campaign(self.ws,'LAB_TEST','OTHER',year=2031)
  with self.assertRaises(LabcalError):load_task(self.ws,'LAB_TEST',other['id'],self.task['task_id'])
  with self.assertRaises(LabcalError):prepare(self.ws,'LAB_TEST',other['id'],[self.ext])
  with self.assertRaises(LabcalError):prepare(self.ws,'LAB_TEST',self.c['id'],[self.ext,self.ext])
  file=Path(self.task['directory'])/'task.json';value=json.loads(file.read_text());value['payload']['engine']='changed';file.write_text(json.dumps(value))
  with self.assertRaises(LabcalError):load_task(self.ws,'LAB_TEST',self.c['id'],self.task['task_id'])
 def test_missing_evidence_false_quote_and_ocr_native_claim_rejected(self):
  with self.assertRaises(LabcalError):self.finish(self.record('confirmed_error'))
  record=self.record('confirmed_error');record['checks'][0]['evidence']=[{'page':1,'bbox':[.05,.05,.9,.3],'quote':'not in source','origin':'native_text'}]
  with self.assertRaises(LabcalError):self.finish(record)
  record['checks'][0]['evidence'][0].update(origin='visual_page_read',image_sha256='0'*64)
  with self.assertRaises(LabcalError):self.finish(record)
 def test_visual_output_is_append_only_and_plan_tables_unchanged(self):
  with self.ws.lab_store('LAB_TEST').connect() as db:
   before={t:[dict(r) for r in db.execute('SELECT * FROM '+t)] for t in ['plans','equipment','confirmations','baselines','certificate_evidence']}
  rendered=page(self.ws,'LAB_TEST',self.c['id'],self.task['task_id'],self.ext,1)
  record=self.record('no_anomaly_detected');record['checks'][0]['evidence']=[{'page':1,'bbox':[.05,.05,.9,.3],'quote':'Result 1.0 V','origin':'visual_page_read','image_sha256':rendered['image_sha256']}]
  a=self.finish(record);b=self.finish(record);self.assertNotEqual(a['run_id'],b['run_id'])
  output=Path(a['directory']);report=json.loads((output/'diagnosis.json').read_text());self.assertEqual(len([c for c in report['checks'] if c['state']=='not_checked']),3)
  self.assertFalse(report['technical_approval'])
  self.assertEqual(audit_storage(self.ws,'LAB_TEST',self.c['id'])['status'],'passed')
  with self.ws.lab_store('LAB_TEST').connect() as db:
   after={t:[dict(r) for r in db.execute('SELECT * FROM '+t)] for t in before}
  self.assertEqual(before,after)
  (output/'diagnosis.md').write_text('tampered');self.assertEqual(audit_storage(self.ws,'LAB_TEST',self.c['id'])['status'],'errors')
 def test_located_issue_uses_existing_pdf_adapter(self):
  record=self.record();ext=get_extraction(self.ws,'LAB_TEST',self.ext)['payload'];line=ext['pages'][0]['lines'][0]
  record['checks'][0]['evidence']=[{'page':1,'bbox':line['bbox'],'quote':line['text'],'origin':'native_text',
    'markup':{'action':'clarify','condition':'TEST ONLY check original definition'}}]
  with patch('labcal.application.self_diagnosis.annotate_pdf_isolated') as annotate:
   def fake(source,target,marks,**kwargs):
    Path(target).write_bytes(b'TEST ONLY rendered copy');self.assertEqual(kwargs['style'],'callouts')
    self.assertEqual(marks[0]['location']['markup'],record['checks'][0]['evidence'][0]['markup'])
    self.assertEqual(marks[0]['certainty'],'candidate')
   annotate.side_effect=fake;self.finish(record);self.assertEqual(annotate.call_count,1)
 def test_filing_note_does_not_mark_original(self):
  record=self.record();record['checks'][0]['group']='filing'
  with patch('labcal.application.self_diagnosis.annotate_pdf_isolated') as annotate:self.finish(record);annotate.assert_not_called()

 def test_real_finish_exports_b3_and_preserves_candidate_and_source(self):
  before=file_hash(self.file)
  with fitz.open(self.file) as d:
   p=d[0];r=p.search_for('1.0')[0];w,h=p.rect.width,p.rect.height
  record=self.record();record['checks'][0]['evidence']=[{'page':1,
   'bbox':[r.x0/w,r.y0/h,r.x1/w,r.y1/h],'quote':'1.0','origin':'native_text',
   'markup':{'action':'replace','condition':'TEST ONLY verify before replacement'}}]
  run=self.finish(record);folder=Path(run['directory'])
  manifest=json.loads((folder/'manifest.json').read_text())
  self.assertEqual(manifest['annotated_pdfs'][0]['page_mapping']['renderer'],'b3-uncertainty-basis-4')
  output=folder/manifest['annotated_pdfs'][0]['file']
  with fitz.open(output) as d:
   self.assertIn('待核实',d[0].get_text())
   self.assertEqual(sum(a.type[0]==fitz.PDF_ANNOT_LINE for a in d[0].annots()),1)
  report=json.loads((folder/'diagnosis.json').read_text())
  self.assertEqual(report['checks'][0]['state'],'candidate_concern')
  self.assertFalse(report['technical_approval']);self.assertEqual(file_hash(self.file),before)
  self.assertEqual(audit_storage(self.ws,'LAB_TEST',self.c['id'])['status'],'passed')

class DecimalTests(unittest.TestCase):
 def test_exact_difference_relative_and_rounding_interval(self):
  spec={'formula':'indication_minus_reference','indication':'1.2','reference':'1.1','reported':'0.10','resolution':'0.01','unit':'V'}
  self.assertTrue(recalculate(spec)['rounding_compatible']);self.assertEqual(recalculate(spec)['expected'],'0.1')
  self.assertFalse(recalculate({**spec,'reported':'0.12'})['rounding_compatible'])
  self.assertEqual(recalculate({**spec,'formula':'relative_to_reference_percent','indication':'101','reference':'100','reported':'1','unit':'%'})['expected'],'1.00')
  with self.assertRaises(LabcalError):recalculate({**spec,'indication':'NaN'})
  with self.assertRaises(LabcalError):recalculate({**spec,'formula':'__import__'})
 def test_model_and_dual_ids_remain_separate(self):
  lines=[{'text':v,'bbox':[.1,.1,.9,.2]} for v in ['型号/规格：','MOD-1','器具编号：','SERIAL-1','管理编号：','ASSET-1']]
  fields=candidate_fields({'pages':[{'lines':lines}]})
  self.assertEqual(fields['model']['value'],'MOD-1');self.assertEqual(fields['serial_number']['value'],'SERIAL-1');self.assertEqual(fields['asset_number']['value'],'ASSET-1')
