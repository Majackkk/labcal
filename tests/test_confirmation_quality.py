"""Three-layer publication: real self-run linkage, inventory, output and isolation."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import test_self_diagnosis as sd
from test_document_templates import write_form, publication_review
from labcal.application.confirmation_quality import validate_quality, load_self_review
from labcal.application.certificates import get_extraction
from labcal.adapters.confirmation_style import format_confirmation, validate_colours
from labcal.application.document_templates import inspect_docx
from labcal.domain.contracts import validate_contract
from labcal.errors import LabcalError
from labcal.util import file_hash, content_hash

@unittest.skipUnless(sd.fitz, 'optional PDF dependency')
class QualityTests(unittest.TestCase):
    setUp = sd.SelfDiagnosisTests.setUp
    def fixture(self, concern=False):
        ext = get_extraction(self.ws, 'LAB_TEST', self.ext)
        line = ext['payload']['pages'][0]['lines'][0]
        evidence = dict(page=1,bbox=line['bbox'],quote=line['text'],origin='native_text')
        rec = {'task_hash':self.task['task_hash'],'author':'TEST ONLY','checks':[
            dict(extraction_id=self.ext,group=g,item='TEST',state='no_anomaly_detected',reason='TEST',basis='TEST',suggestion='TEST',evidence=[evidence])
            for g in ('identity','numeric','technical','conclusion')]}
        if concern:rec['checks'][1]['state']='candidate_concern'
        run=sd.finish(self.ws,'LAB_TEST',self.c['id'],self.task['task_id'],rec,annotate=False)
        task={'lab_id':'LAB_TEST','campaign_id':self.c['id'],'extraction_id':self.ext,'extraction_hash':ext['payload_hash'],'source_hash':ext['payload']['source_hash']}
        row=dict(function='TEST function',purpose='TEST use',state='meets_requirement',parameter_results='TEST function / TEST use / TEST source result',
            method_judgment='TEST instrument requirement. TEST instrument conforms. TEST use requirement. TEST use conforms.',
            uncertainty_evaluation='TEST U and scope',traceability='TEST trace',application='TEST no correction')
        record=dict(author='TEST',scope='TEST',evidence_references=['TEST'],rows=[row])
        record['publication_review']=publication_review(record)
        record['publication_review']['selections']['result'].update(choice='meets_declared_uses', document_texts=['结果：☑满足 □不满足'])
        record['technical_review']=dict(version=2,use_evidence='TEST',decision_evidence='TEST',self_run_id=run['run_id'],
            certificate_items=[dict(label='TEST certificate item',evidence=evidence,row_numbers=[1])],rules=[])
        for dimension,basis,label in [('instrument_requirement','instrument_specification','instrument'),('intended_use','method_requirement','use')]:
            record['technical_review']['rules'].append(dict(row_number=1,dimension=dimension,state='meets_requirement',
                source_reference='TEST',configuration_evidence='TEST config',requirement_kind='maximum_error',compared_quantity='indication_error',
                decision_mode='direct_comparison',decision_group=dimension,limit_basis=basis,requirement_quote=f'TEST {label} requirement.',
                comparison_quote=f'TEST {label} conforms.',judgment_quote=f'TEST {label} conforms.',uncertainty_quote='TEST U and scope'))
            record['technical_review']['rules'][-1].update(
                basis_role='technical_reference' if label == 'instrument' else 'acceptance_requirement',
                basis_scope_quote=f'TEST {label} requirement.')
            record['technical_review']['rules'][-1]['reasoning'] = dict(
                requirement_status='established', interpretation_status='unambiguous',
                interpretation_evidence='TEST explicit error requirement, not display division',
                quantity_match='direct', quantity_basis='TEST indication error compared with indication-error limit',
                decision_evidence='TEST direct comparison chosen for this use', uncertainty_role='separate_evaluation')
        return task,record,ext['payload']
    def test_real_run_inventory_and_colour_leave_original_and_plan_facts_unchanged(self):
        task,record,ext=self.fixture(); before=file_hash(self.file)
        with self.ws.lab_store('LAB_TEST').connect() as db:
            facts={t:content_hash([dict(r) for r in db.execute('SELECT * FROM '+t)]) for t in ['plans','plan_revisions','confirmations','baselines','equipment']}
        validate_contract('confirmation-record.v1',record);checks=validate_quality(self.ws,task,record,ext)
        original=Path(self.temp.name)/'original.docx';out=Path(self.temp.name)/'colour.docx';again=Path(self.temp.name)/'again.docx'
        write_form(original,record);digest=file_hash(original)
        format_confirmation(original,out,record,0,self_checks=checks)
        self.assertEqual(inspect_docx(original),inspect_docx(out));validate_colours(out,record,0)
        format_confirmation(out,again,record,0,self_checks=checks)
        self.assertEqual(inspect_docx(out),inspect_docx(again));self.assertEqual(file_hash(original),digest)
        with self.assertRaises(LabcalError):format_confirmation(original,out,record,0)
        self.assertEqual(file_hash(self.file),before)
        with self.ws.lab_store('LAB_TEST').connect() as db:
            self.assertEqual(facts,{t:content_hash([dict(r) for r in db.execute('SELECT * FROM '+t)]) for t in facts})

    def test_new_prepare_format_finish_runs_the_quality_gate(self):
        from labcal.application.document_templates import register, prepare_confirmation, finish_confirmation
        task,record,_=self.fixture()
        from labcal.application.sources import import_source
        reference=import_source(self.ws,self.file,kind='test_method_standard',shareable=True)
        sid=reference['id']
        rendered=sd.page(self.ws,'LAB_TEST',self.c['id'],self.task['task_id'],self.ext,1)
        image=self.ws.reference_store().directory/'processing'/'methods'/sid/'pages'/'P1.png'
        image.parent.mkdir(parents=True);image.write_bytes(Path(rendered['path']).read_bytes())
        record['coverage']=dict(claim='complete_for_declared_scope',scope_note='TEST ONLY',methods=[
            dict(source_id=sid,search_note='TEST ONLY',items=[dict(task='TEST use',disposition='assessed',reason='TEST ONLY',row_numbers=[1],
                evidence=[dict(page=1,clause='TEST',quote='Certificate TEST-001',image_sha256=file_hash(image))])])])
        record['technical_review']['rules'][1].update(purpose='TEST use',acceptance_basis='method_explicit',
            applicability_basis='TEST ONLY explicit fixture requirement',requirement_interpretation='measurement_accuracy',
            method_links=[dict(source_id=sid,task='TEST use')])
        # Task-local numeric evidence, also visible in the final U column.
        u_text='TEST chosen simple acceptance; U separately evaluated. TEST |E|=0.2≤1, U=0.4. TEST comparison conforms.'
        record['rows'][0]['uncertainty_evaluation']=u_text
        record['publication_review']['rows'][0]['uncertainty_reasoning']=u_text
        for rule in record['technical_review']['rules']:
            rule['uncertainty_quote']=u_text
            rule['reasoning']['uncertainty_review']=dict(route='simple_acceptance', application='decision',
                basis_quote='TEST chosen simple acceptance; U separately evaluated.',
                calculation_quote='TEST |E|=0.2≤1, U=0.4.', judgment_quote='TEST comparison conforms.',
                outcome='meets_requirement', points=[dict(label='TEST point', error='0.2', expanded_uncertainty='0.4',mpev='1',unit='TEST unit')])
        original=Path(self.temp.name)/'template.docx';write_form(original,record)
        register(self.ws,'LAB_TEST',self.c['id'],original,'calibration-confirmation','TEST ONLY','TEST ONLY user template')
        prepared=prepare_confirmation(self.ws,'LAB_TEST',self.c['id'],self.ext,'TEST ONLY instrument',context_sources=[sid])
        self.assertEqual(prepared['task']['quality_version'],1)
        self.assertEqual(prepared['task']['use_basis_version'],1)
        self.assertEqual(prepared['task']['basis_role_version'],1)
        self.assertEqual(prepared['task']['reasoning_version'],1)
        self.assertEqual(prepared['task']['uncertainty_review_version'],1)
        from labcal.application.document_templates import analyze_confirmation
        def facts():
            with self.ws.lab_store('LAB_TEST').connect() as db:
                tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                return {t: [tuple(r) for r in db.execute('SELECT * FROM "'+t+'"')] for t in tables}
        before_analysis = facts(); source_hash = file_hash(self.file)
        analysis = analyze_confirmation(self.ws,'LAB_TEST',self.c['id'],prepared['task']['id'],record)
        self.assertTrue(analysis['ready_for_document']); self.assertFalse(analysis['semantic_verification'])
        bad_role = copy.deepcopy(record)
        del bad_role['technical_review']['rules'][0]['basis_role']
        role_analysis = analyze_confirmation(self.ws,'LAB_TEST',self.c['id'],prepared['task']['id'],bad_role)
        self.assertFalse(role_analysis['ready_for_document'])
        self.assertEqual(role_analysis['issues'][0]['code'], 'CONFIRMATION_BASIS_ROLE')
        with self.assertRaises(LabcalError) as role_error:
            finish_confirmation(self.ws,'LAB_TEST',self.c['id'],prepared['task']['id'],bad_role,Path(self.temp.name)/'missing.docx',0)
        self.assertEqual(role_error.exception.code, 'CONFIRMATION_BASIS_ROLE')
        bad = copy.deepcopy(record)
        bad['technical_review']['rules'][1]['reasoning']['interpretation_status'] = 'unresolved'
        blocked = analyze_confirmation(self.ws,'LAB_TEST',self.c['id'],prepared['task']['id'],bad)
        self.assertFalse(blocked['ready_for_document'])
        self.assertIn('CONFIRMATION_BASIS_UNRESOLVED', [i['code'] for i in blocked['issues']])
        bad_u=copy.deepcopy(record)
        bad_u['technical_review']['rules'][1]['reasoning']['uncertainty_review']['points'][0]['error']='1.1'
        blocked_u=analyze_confirmation(self.ws,'LAB_TEST',self.c['id'],prepared['task']['id'],bad_u)
        self.assertFalse(blocked_u['ready_for_document'])
        self.assertIn('CONFIRMATION_U_CALCULATION',[i['code'] for i in blocked_u['issues']])
        with self.assertRaises(LabcalError) as u_error:
            finish_confirmation(self.ws,'LAB_TEST',self.c['id'],prepared['task']['id'],bad_u,Path(self.temp.name)/'missing.docx',0)
        self.assertEqual(u_error.exception.code,'CONFIRMATION_UNCERTAINTY_BLOCKED')
        # Both public exits reject unresolved analysis before reading/writing a Word file.
        with self.assertRaises(LabcalError):
            finish_confirmation(self.ws,'LAB_TEST',self.c['id'],prepared['task']['id'],bad,Path(self.temp.name)/'missing.docx',0)
        from labcal.cli import execute, parser
        from labcal.util import write_json
        bad_path = Path(self.temp.name)/'bad-record.json'; write_json(bad_path,bad)
        args = parser().parse_args(['--workspace',self.temp.name,'confirmation','format','--lab','LAB_TEST','--campaign',self.c['id'],
            '--task',prepared['task']['id'],'--record',str(bad_path),'--document','missing.docx',
            '--output',str(Path(self.temp.name)/'should-not-exist.docx'),'--appendix-table','0'])
        with self.assertRaises(LabcalError): execute(args)
        self.assertFalse((Path(self.temp.name)/'should-not-exist.docx').exists())
        self.assertEqual(facts(), before_analysis)
        self.assertEqual(file_hash(self.file), source_hash)
        out=Path(self.temp.name)/'filled-colour.docx'
        _,checks=load_self_review(self.ws,task,record['technical_review']['self_run_id'])
        format_confirmation(original,out,record,0,self_checks=checks)
        result=finish_confirmation(self.ws,'LAB_TEST',self.c['id'],prepared['task']['id'],record,out,0)
        self.assertFalse(result['technical_approval']);self.assertTrue(finish_confirmation(self.ws,'LAB_TEST',self.c['id'],prepared['task']['id'],record,out,0)['reused'])
    def test_missing_inventory_false_quote_and_unmapped_rows_blocked(self):
        task,record,ext=self.fixture()
        for mutate,code in [
            (lambda r:r['technical_review'].pop('certificate_items'),'CONFIRMATION_QUALITY_REQUIRED'),
            (lambda r:r['technical_review']['certificate_items'][0]['row_numbers'].append(2),'CONFIRMATION_ITEM_ROWS'),
            (lambda r:r['technical_review']['certificate_items'][0]['evidence'].update(quote='NOT IN SOURCE'),'SELF_QUOTE_MISMATCH'),
            (lambda r:r['technical_review']['certificate_items'].append(copy.deepcopy(r['technical_review']['certificate_items'][0])),'CONFIRMATION_ITEM_DUPLICATE')]:
            bad=copy.deepcopy(record);mutate(bad)
            with self.assertRaises(LabcalError) as error:validate_quality(self.ws,task,bad,ext)
            self.assertEqual(error.exception.code,code)
    def test_scope_uncertainty_and_layer_contradictions_not_hidden(self):
        task,record,ext=self.fixture(concern=True)
        with self.assertRaises(LabcalError) as error:validate_quality(self.ws,task,record,ext)
        self.assertEqual(error.exception.code,'CONFIRMATION_SELF_UNRESOLVED')
        record['publication_review']['selections']['result']['choice']='limited_use'
        validate_quality(self.ws,task,record,ext)
        record['technical_review']['rules'][1]['state']='does_not_meet'
        with self.assertRaises(LabcalError) as error:validate_quality(self.ws,task,record,ext)
        self.assertEqual(error.exception.code,'CONFIRMATION_LAYER_STATE')
        task['campaign_id']='OTHER'
        with self.assertRaises(LabcalError):load_self_review(self.ws,task,record['technical_review']['self_run_id'])
    def test_instrument_pass_cannot_replace_use_pass(self):
        task,record,ext=self.fixture();record['technical_review']['rules'].pop()
        with self.assertRaises(LabcalError) as error:validate_quality(self.ws,task,record,ext)
        self.assertEqual(error.exception.code,'CONFIRMATION_LAYER_STATE')
    def test_wrong_colour_or_missing_legend_blocked(self):
        _,record,_=self.fixture();path=Path(self.temp.name)/'plain.docx';write_form(path,record)
        with self.assertRaises(LabcalError):validate_colours(path,record,0)

    def test_method_acceptance_cannot_be_replaced_by_grade_or_resolution(self):
        from labcal.application.confirmation_quality import validate_method_basis
        task,record,_=self.fixture()
        task['coverage_scope']=[dict(source_id='REF-TEST')]
        record['coverage']=dict(methods=[dict(source_id='REF-TEST',items=[dict(task='TEST use',row_numbers=[1],disposition='assessed')])])
        rule=record['technical_review']['rules'][1]
        rule.update(purpose='TEST use',acceptance_basis='method_explicit',applicability_basis='TEST original clause and use',
            requirement_interpretation='measurement_accuracy',method_links=[dict(source_id='REF-TEST',task='TEST use')])
        validate_method_basis(task,record)
        bad=copy.deepcopy(record);bad['technical_review']['rules'][1]['limit_basis']='instrument_specification'
        with self.assertRaises(LabcalError):validate_method_basis(task,bad)
        bad=copy.deepcopy(record);bad['technical_review']['rules'][1]['compared_quantity']='resolution'
        with self.assertRaises(LabcalError):validate_method_basis(task,bad)
        bad=copy.deepcopy(record);bad['technical_review']['rules'][1]['acceptance_basis']='laboratory_established'
        with self.assertRaises(LabcalError):validate_method_basis(task,bad)
        bad=copy.deepcopy(record);bad['technical_review']['rules'][1]['method_links'][0]['source_id']='REF-OTHER'
        with self.assertRaises(LabcalError):validate_method_basis(task,bad)

    def test_neutral_raw_record_gap_is_scoped_not_converted_to_failure(self):
        task,record,ext=self.fixture()
        record['rows'].append(dict(record['rows'][0],state='reported_characteristic'))
        record['technical_review']['certificate_items'][0]['row_numbers'].append(2)
        gap=dict(id='TEST-GAP',group='numeric',state='unable_to_check')
        with patch('labcal.application.confirmation_quality.load_self_review',return_value=(Path(self.task['directory']),[gap])):
            with self.assertRaises(LabcalError):validate_quality(self.ws,task,record,ext)
            record['technical_review']['self_issue_dispositions']=[dict(check_id='TEST-GAP',row_numbers=[2],reason='TEST neutral raw data only')]
            validate_quality(self.ws,task,record,ext)
            record['technical_review']['self_issue_dispositions'][0]['row_numbers']=[1]
            with self.assertRaises(LabcalError):validate_quality(self.ws,task,record,ext)
