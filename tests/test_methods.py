"""Cross-lab reuse, original evidence, formula boundaries and export isolation."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from labcal.adapters.storage import Workspace
from labcal.application.laboratories import create_laboratory
from labcal.application.campaigns import create_campaign, attach_item
from labcal.application.sources import import_source
from labcal.application.certificates import add_certificate, get_extraction
from labcal.application.methods import import_method, get_method, method_page, import_use, get_use, list_uses, search_methods, import_adoption, list_adoptions
from labcal.application.method_diagnosis import prepare, finish, load_task
from labcal.application import self_diagnosis
from labcal.application.laboratory_summary import export_summary, collect
from labcal.application.storage_audit import audit_storage
from labcal.domain.method_requirements import compare, expression_value
from labcal.errors import LabcalError
from labcal.util import content_hash, file_hash
try: import fitz
except ImportError: fitz = None


def requirement():
    return {'id': 'R1', 'equipment_category': 'displacement', 'parameter': 'deflection error',
            'requirement_type': 'mpe', 'origin': 'explicit', 'statement': 'TEST ONLY abs error <= 2 um',
            'range_text': 'TEST ONLY 0-1 mm', 'condition': 'TEST ONLY', 'state': 'page_checked', 'evidence': [],
            'criterion': {'operator': 'abs_le', 'unit': 'um', 'value': '2', 'relative_basis': 'not_applicable'}}


@unittest.skipUnless(fitz, 'optional PDF dependency')
class MethodIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.ws = Workspace(self.temp.name); self.ws.initialize()
        for lab in ['LAB_TEST', 'LAB_OTHER']: create_laboratory(self.ws, lab, 'TEST ONLY', test_only=True)
        self.c = create_campaign(self.ws, 'LAB_TEST', 'TEST ONLY', year=2030)
        self.file = Path(self.temp.name) / 'synthetic.pdf'
        d = fitz.open(); p = d.new_page(); p.insert_text((50,80), 'TEST STANDARD\nError 0.001 mm'); d.save(self.file); d.close()
        self.source = import_source(self.ws, self.file, kind='test_method_standard', shareable=True)['id']
        page = method_page(self.ws, self.source, 1)
        self.req = requirement(); self.req['evidence'] = [{'page': 1, 'clause': 'TEST 1', 'quote': 'TEST STANDARD', 'image_sha256': page['image_sha256']}]
        self.package = {'standard_code': 'TEST', 'edition': '2030', 'title': 'TEST ONLY', 'source_id': self.source,
            'author': 'TEST agent', 'coverage': 'TEST ONLY partial', 'requirements': [self.req], 'gaps': []}
        self.method = import_method(self.ws, self.package)
        self.use_record = {'equipment_key': 'TEST-DEVICE', 'equipment_name': 'TEST ONLY', 'method_id': self.method['id'],
            'purpose': 'TEST ONLY', 'use_range': '0-1 mm', 'conditions': {}, 'state': 'user_adopted', 'requirement_ids': ['R1'],
            'user_statement': 'TEST ONLY simulated user adoption', 'evidence_reference': 'TEST ONLY fixture'}
        self.use = import_use(self.ws, 'LAB_TEST', self.use_record)
        self.ext = add_certificate(self.ws, 'LAB_TEST', file=self.file, ocr=False)['extraction_id']
        attach_item(self.ws, 'LAB_TEST', self.c['id'], 'extraction', self.ext)
        from labcal.adapters.report_archive import preserve_report
        from labcal.application.campaigns import campaign_directory
        from labcal.application.sources import verify_source_integrity
        source_id = get_extraction(self.ws, 'LAB_TEST', self.ext)['payload']['source_id']
        preserve_report(campaign_directory(self.ws, 'LAB_TEST', self.c), verify_source_integrity(self.ws, 'LAB_TEST', source_id))
        self.task = prepare(self.ws, 'LAB_TEST', self.c['id'], [self.ext], [self.use['id']])
        payload = get_extraction(self.ws, 'LAB_TEST', self.ext)['payload']; line = payload['pages'][0]['lines'][0]
        self.evidence = [{'page':1, 'bbox':line['bbox'], 'quote':line['text'], 'origin':'native_text'}]
        self.self_record = {'task_hash': self_diagnosis.load_task(self.ws, 'LAB_TEST', self.c['id'], self.task['self_task_id'])[1]['payload_hash'],
            'author': 'TEST agent', 'checks': [{'extraction_id': self.ext, 'group': group, 'item': group, 'state': 'no_anomaly_detected',
                'reason': 'TEST ONLY', 'basis': 'TEST ONLY', 'suggestion': 'TEST ONLY', 'evidence': self.evidence}
                for group in ['identity', 'numeric', 'technical', 'conclusion']]}
        self.inner = self_diagnosis.finish(self.ws, 'LAB_TEST', self.c['id'], self.task['self_task_id'], self.self_record, annotate=False)

    def observation(self, value='0.001'):
        return {'use_id': self.use['id'], 'requirement_id': 'R1', 'extraction_id': self.ext, 'point': '0.5 mm',
            'condition': 'TEST ONLY', 'match': 'matched', 'scope': 'TEST ONLY one point', 'reason': 'TEST ONLY identity/condition association',
            'evidence': self.evidence, 'rule': 'direct', 'rule_basis': 'TEST ONLY agreement', 'value': {'value': value, 'unit': 'mm'}}

    def finish(self, observations=None, **extra):
        record = {'task_hash': self.task['task_hash'], 'author': 'TEST agent', 'self_run_id': self.inner['run_id'],
                  'observations': observations if observations is not None else [self.observation()]}
        record.update(extra)
        if not record.get('self_run_id'): record.pop('self_run_id', None)
        result = finish(self.ws, 'LAB_TEST', self.c['id'], self.task['task_id'], record, annotate=False)
        return result, json.loads((Path(result['directory']) / 'diagnosis.json').read_text())

    def test_shared_method_reuse_and_candidate_is_not_adoption(self):
        self.assertEqual(import_method(self.ws, self.package)['id'], self.method['id'])
        u = import_use(self.ws, 'LAB_OTHER', {**self.use_record, 'state': 'candidate', 'user_statement': '', 'evidence_reference': ''})
        self.assertEqual(u['payload']['method_hash'], self.method['payload_hash'])
        self.assertEqual(compare(self.req, u['payload'], self.observation())['state'], 'insufficient_evidence')
        with self.assertRaises(LabcalError): get_use(self.ws, 'LAB_OTHER', self.use['id'])
        with self.assertRaises(LabcalError): import_use(self.ws, 'LAB_OTHER', {**self.use_record, 'evidence_reference': ''})
        self.assertEqual(search_methods(self.ws, standard='TEST', parameter='deflection')[0]['id'], self.method['id'])
        self.assertEqual(search_methods(self.ws, edition='2029'), [])

    def test_method_adoption_does_not_confirm_device_conditions(self):
        record = {'source_id':self.source,'standard_code':'TEST','edition':'2030',
                  'user_statement':'TEST ONLY direct message','evidence_reference':'TEST ONLY fixture'}
        adopted = import_adoption(self.ws, 'LAB_TEST', record)
        self.assertFalse(adopted['payload']['equipment_configuration_confirmed'])
        self.assertEqual(list_adoptions(self.ws, 'LAB_OTHER'), [])
        candidate = import_use(self.ws, 'LAB_TEST', {**self.use_record, 'state':'candidate', 'adoption_id':adopted['id']})
        self.assertEqual(compare(self.req, candidate['payload'], self.observation())['state'], 'insufficient_evidence')
        with self.assertRaises(LabcalError): import_use(self.ws, 'LAB_OTHER', {**self.use_record, 'adoption_id':adopted['id']})

    def test_uncertainty_request_checks_applicable_cmc_and_institution_source(self):
        f = Path(self.temp.name) / 'scope.pdf'
        d=fitz.open();p=d.new_page();p.insert_text((50,80),'TEST ONLY CMC 1.8 um, k=2');d.save(f);d.close()
        source=import_source(self.ws,f,kind='institution_scope',lab_id='LAB_TEST')['id']
        request={'value':'3','unit':'um','k':'2','scope_source_id':source,'scope_page':1,
            'scope_quote':'TEST ONLY CMC 1.8 um, k=2','scope_applicability':'TEST ONLY matching range and valid date',
            'cmc':{'value':'1.8','unit':'um'},'scope_k':'2'}
        communication={'use_id':self.use['id'],'extraction_id':self.ext,'items':['TEST ONLY proposed U <= 3 um'], 'uncertainty_request':request}
        _, result=self.finish(communications=[communication]);self.assertEqual(result['communications'][0]['scope_source_hash'],file_hash(f))
        communication['uncertainty_request']={**request,'value':'1'}
        with self.assertRaises(LabcalError):self.finish(communications=[communication])
        communication['uncertainty_request']={**request,'scope_source_id':self.source}
        with self.assertRaises(LabcalError):self.finish(communications=[communication])

    def test_immutable_version_and_user_use_revision(self):
        newer = import_method(self.ws, {**self.package, 'edition': '2031'})
        updated = import_use(self.ws, 'LAB_TEST', {**self.use_record, 'method_id': newer['id'], 'supersedes': self.use['id']})
        self.assertEqual([u['id'] for u in list_uses(self.ws, 'LAB_TEST')], [updated['id']])
        self.assertEqual(len(list_uses(self.ws, 'LAB_TEST', include_history=True)), 2)
        self.assertEqual(get_method(self.ws, self.method['id'])['payload']['edition'], '2030')
        # Existing fixed task remains readable, while new tasks reject superseded uses.
        load_task(self.ws, 'LAB_TEST', self.c['id'], self.task['task_id'])
        with self.assertRaises(LabcalError): prepare(self.ws, 'LAB_TEST', self.c['id'], [self.ext], [self.use['id']])

    def test_checked_requirement_needs_original_page_and_rejects_code(self):
        p = copy.deepcopy(self.package); p['requirements'][0]['evidence'][0]['image_sha256'] = 'bad'
        with self.assertRaises(LabcalError): import_method(self.ws, p)
        p = copy.deepcopy(self.package); p['requirements'][0]['criterion'].pop('value')
        p['requirements'][0]['criterion']['expression'] = '__import__("os").system("id")'
        with self.assertRaises(LabcalError): import_method(self.ws, p)

    def test_exact_unit_comparison_boundary_and_guard_band(self):
        _, result = self.finish(); self.assertEqual(result['rows'][0]['decision']['state'], 'meets_requirement')
        _, result = self.finish([self.observation('0.002')]); self.assertEqual(result['rows'][0]['decision']['state'], 'meets_requirement')
        _, result = self.finish([self.observation('0.002001')]); self.assertEqual(result['rows'][0]['decision']['state'], 'does_not_meet')
        obs = {**self.observation('0.0015'), 'rule': 'guard_band', 'uncertainty': {'value':'1', 'unit':'um'}}
        _, result = self.finish([obs]); self.assertEqual(result['rows'][0]['decision']['state'], 'insufficient_evidence')
        self.assertIn('不据此断言', result['rows'][0]['decision']['reason'])

    def test_evidence_scope_and_internal_stage_are_required(self):
        bad = self.observation(); bad['evidence'] = [{**self.evidence[0], 'quote': 'wrong'}]
        with self.assertRaises(LabcalError): self.finish([bad])
        with self.assertRaises(LabcalError): self.finish([self.observation(), self.observation()])
        _, result = self.finish(self_run_id='')
        self.assertEqual(result['rows'][0]['decision']['state'], 'not_checked')
        partial = {**self.self_record, 'checks': self.self_record['checks'][:1]}
        run = self_diagnosis.finish(self.ws, 'LAB_TEST', self.c['id'], self.task['self_task_id'], partial, annotate=False)
        _, result = self.finish(self_run_id=run['run_id'])
        self.assertEqual(result['rows'][0]['decision']['state'], 'insufficient_evidence')
        with self.assertRaises(LabcalError): prepare(self.ws, 'LAB_TEST', self.c['id'], [self.ext, self.ext], [self.use['id']])

    def test_idempotency_latest_rows_and_protect_excel_manual_changes(self):
        first, _ = self.finish(); same, _ = self.finish(); self.assertEqual(first['run_id'], same['run_id'])
        self.finish([self.observation('0.004')])
        data = collect(self.ws, 'LAB_TEST'); self.assertEqual(len(data['method_rows']), 1)
        self.assertEqual(data['method_rows'][0]['decision']['state'], 'does_not_meet')
        output = export_summary(self.ws, 'LAB_TEST'); again = export_summary(self.ws, 'LAB_TEST')
        self.assertTrue(again['reused']); self.assertEqual(output['run_id'], again['run_id'])
        with zipfile.ZipFile(output['path']) as z:
            self.assertIn('方法要求对照', z.read('xl/workbook.xml').decode())
            self.assertNotIn('<f>', z.read('xl/worksheets/sheet1.xml').decode())
        Path(output['path']).write_bytes(b'USER EDITS')
        with self.assertRaises(LabcalError): export_summary(self.ws, 'LAB_TEST')
        self.assertEqual(Path(output['path']).read_bytes(), b'USER EDITS')

    def test_new_self_diagnosis_invalidates_old_method_projection_only(self):
        run, result = self.finish()
        self.assertEqual(result['rows'][0]['decision']['state'], 'meets_requirement')
        partial = {**self.self_record, 'checks': self.self_record['checks'][:1]}
        self_diagnosis.finish(self.ws, 'LAB_TEST', self.c['id'], self.task['self_task_id'], partial, annotate=False)
        self.assertEqual(collect(self.ws, 'LAB_TEST')['method_rows'][0]['decision']['state'], 'not_checked')
        frozen = json.loads((Path(run['directory']) / 'diagnosis.json').read_text())
        self.assertEqual(frozen['rows'][0]['decision']['state'], 'meets_requirement')

    def test_outputs_do_not_change_plan_lane_and_audit_detects_tamper(self):
        store = self.ws.lab_store('LAB_TEST')
        tables = ['equipment','plans','plan_revisions','confirmations','baselines','certificate_evidence']
        with store.connect() as db: before = {t: [dict(r) for r in db.execute('SELECT * FROM ' + t)] for t in tables}
        source_hash = file_hash(self.file); result, _ = self.finish()
        export_summary(self.ws, 'LAB_TEST')
        with store.connect() as db: after = {t: [dict(r) for r in db.execute('SELECT * FROM ' + t)] for t in tables}
        self.assertEqual(before, after); self.assertEqual(file_hash(self.file), source_hash)
        # Archive projection warnings are expected in this deliberately minimal fixture.
        audit = audit_storage(self.ws, 'LAB_TEST')
        self.assertFalse(any(i['code'] in {'METHOD_RECORD_CHANGED','FILE_INTEGRITY_FAILED'} for i in audit['issues']))
        (Path(result['directory']) / 'diagnosis.md').write_text('tampered')
        self.assertTrue(any(i['code'] == 'FILE_INTEGRITY_FAILED' for i in audit_storage(self.ws, 'LAB_TEST')['issues']))


class FormulaTests(unittest.TestCase):
    def test_page_assessment_is_scoped_and_cannot_override_numeric_rule(self):
        req = requirement(); req['criterion'] = {'operator':'none','unit':'mm','relative_basis':'not_applicable'}
        use = {'state':'user_adopted','conditions':{}}
        obs = {'match':'matched','evidence':[{}],'rule':'none','assessment':{
            'origin':'agent_page_assessment','state':'meets_requirement','reason':'TEST ONLY scoped comparison', 'basis':'TEST ONLY original clause'}}
        self.assertEqual(compare(req,use,obs)['origin'], 'agent_page_assessment')
        self.assertEqual(compare(req,{**use,'state':'candidate'},obs)['state'],'insufficient_evidence')
        self.assertEqual(compare(req,use,{**obs,'evidence':[]})['state'],'insufficient_evidence')
        with self.assertRaises(LabcalError): compare(requirement(),use,obs)

    def test_conditional_formula_and_dimensions(self):
        values = {'ratio': {'value':'0.01','unit':'1'}, 'strain': {'value':'0.0025','unit':'1'},
                  'span': {'value':'64','unit':'mm'}, 'thickness': {'value':'4','unit':'mm'}}
        answer = expression_value('ratio * strain * span**2 / (6 * thickness)', values, 'um')
        self.assertTrue(abs(answer - 4) < 1)
        self.assertEqual(str(expression_value('span / thickness', values, '1')), '16')
        for expr, unit in [('span + strain', 'mm'), ('span / 0', 'mm'), ('span**100', 'mm'), ('span.real', 'mm')]:
            with self.assertRaises(LabcalError): expression_value(expr, values, unit)

    def test_relative_basis_and_recommendation_not_automatically_mandatory(self):
        req = requirement(); req['criterion'].update(unit='%', value='1', relative_basis='reading')
        use = {'state':'user_adopted','conditions':{}}
        obs = {'match':'matched','evidence':[{}],'rule':'direct','value':{'value':'0.1','unit':'%'}, 'relative_basis':'full_scale'}
        self.assertEqual(compare(req, use, obs)['state'], 'insufficient_evidence')
        req['origin'] = 'recommendation'; self.assertEqual(compare(req, use, obs)['state'], 'insufficient_evidence')
