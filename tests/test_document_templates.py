import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from labcal.adapters.storage import Workspace
from labcal.application.laboratories import create_laboratory
from labcal.application.campaigns import create_campaign
from labcal.application.document_templates import register, get_template, prepare_confirmation
from labcal.util import file_hash
from labcal.errors import LabcalError


def publication_review(record):
    from labcal.domain.document_template_contracts import REVIEW_QUOTES
    return {'rows': [dict(row_number=i, correction='not_evaluated',
                         **{key: row[column] for key, column in REVIEW_QUOTES.items()})
                     for i, row in enumerate(record['rows'], 1)],
            'selections': {key: {'choice': choice, 'basis': 'TEST ONLY missing evidence',
                                'document_texts': [text]}
                           for key, choice, text in [
                               ('result', 'undetermined', '结果：□满足 □不满足'),
                               ('correction', 'undetermined', '修正：□需要 □不需要'),
                               ('period', 'unspecified', '周期：□维持 □调整')]}}


def technical_review(record):
    return dict(version=2, use_evidence='TEST ONLY direct use statement', decision_evidence='TEST ONLY rule selection',
        rules=[dict(row_number=i, source_reference='TEST ONLY source', requirement_kind='characteristic',
                    compared_quantity='reported_result', decision_mode='report_only', decision_group=f'TEST-{i}',
                    limit_basis='no_independent_limit', requirement_quote=r['method_judgment'],
                    comparison_quote=r['method_judgment'], uncertainty_quote=r['uncertainty_evaluation'])
               for i, r in enumerate(record['rows'], 1)])


def write_form(path, record):
    from xml.sax.saxutils import escape
    from labcal.domain.document_template_contracts import ROW_FIELDS
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>'
    for values in [['HEADER'] * 5] + [[r[k] for k in ROW_FIELDS] for r in record['rows']]:
        xml += '<w:tr>'+''.join('<w:tc><w:p><w:r><w:t>'+escape(v)+'</w:t></w:r></w:p></w:tc>' for v in values)+'</w:tr>'
    xml += '</w:tbl>'
    for item in record['publication_review']['selections'].values():
        for value in item['document_texts']:
            xml += '<w:p><w:r><w:t>'+escape(value)+'</w:t></w:r></w:p>'
    xml += '</w:body></w:document>'
    with ZipFile(path, 'w') as z: z.writestr('word/document.xml', xml)


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.w = Workspace(self.tmp.name); self.w.initialize()
        create_laboratory(self.w, 'LAB_TEST', 'TEST ONLY', test_only=True)
        create_laboratory(self.w, 'LAB_OTHER', 'TEST ONLY OTHER', test_only=True)
        self.c = create_campaign(self.w, 'LAB_TEST', 'TEST ONLY', year=2030)
        self.file = Path(self.tmp.name) / 'blank.docx'
        with ZipFile(self.file, 'w') as z:
            z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>TEST ONLY FIELD</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')

    def register(self, reason='TEST ONLY user selected template'):
        return register(self.w, 'LAB_TEST', self.c['id'], self.file, 'calibration-confirmation', 'TEST ONLY', reason)

    def test_original_preserved_and_repeat_registration_reused(self):
        digest = file_hash(self.file); first = self.register(); again = self.register()
        self.assertEqual(first, again); self.assertEqual(file_hash(self.file), digest)
        self.assertEqual(file_hash(first['path']), digest)
        self.assertFalse(first['payload']['technical_approval'])
        self.assertEqual(first['payload']['inventory']['tables'][0]['rows'][0][0], 'TEST ONLY FIELD')

    def test_other_lab_cannot_resolve_and_modified_archive_rejected(self):
        first = self.register()
        with self.assertRaises(LabcalError): get_template(self.w, 'LAB_OTHER', 'calibration-confirmation')
        Path(first['path']).write_bytes(b'changed')
        with self.assertRaises(LabcalError): get_template(self.w, 'LAB_TEST', 'calibration-confirmation')

    def test_new_selection_preserves_old_version_and_does_not_confirm_equipment(self):
        first = self.register(); second = self.register('TEST ONLY replacement selection')
        self.assertNotEqual(first['payload']['id'], second['payload']['id'])
        old = get_template(self.w, 'LAB_TEST', 'calibration-confirmation', template_id=first['payload']['id'])
        self.assertEqual(first, old)
        with self.w.lab_store('LAB_TEST').connect() as db:
            for table in ['equipment','confirmations','baselines']:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0], 0)

    def test_invalid_template_and_foreign_campaign_rejected(self):
        bad = Path(self.tmp.name) / 'bad.docx'; bad.write_bytes(b'not docx')
        with self.assertRaises(LabcalError): register(self.w, 'LAB_TEST', self.c['id'], bad, 'calibration-confirmation', 'TEST ONLY', 'TEST ONLY')
        other = create_campaign(self.w, 'LAB_OTHER', 'TEST ONLY', year=2030)
        with self.assertRaises(LabcalError): register(self.w, 'LAB_TEST', other['id'], self.file, 'calibration-confirmation', 'TEST ONLY', 'TEST ONLY')

    def test_prepare_requires_scoped_certificate_and_keeps_template_fixed(self):
        original = self.register()
        with self.assertRaises(LabcalError): prepare_confirmation(self.w, 'LAB_TEST', self.c['id'], 'EXT-MISSING', 'TEST ONLY')
        with patch('labcal.application.document_templates.require_item'), \
             patch('labcal.application.document_templates.get_extraction', return_value={'payload_hash':'TEST-EXT-HASH','payload':{'source_id':original['payload']['source_id']}}):
            task = prepare_confirmation(self.w, 'LAB_TEST', self.c['id'], 'EXT-TEST', 'TEST ONLY')
        self.assertEqual(task['task']['template_id'], original['payload']['id'])
        self.assertEqual(task['task']['state'], 'awaiting_evidence_and_form_filling')
        self.assertFalse(task['task']['technical_approval'])
        self.assertFalse(list(Path(task['directory']).glob('*.docx')))

    def test_fresh_prepare_does_not_read_historical_conclusions(self):
        original = self.register()
        store = self.w.lab_store('LAB_TEST')
        with store.connect() as db:
            db.execute("INSERT INTO meta(key,value) VALUES (?,?)", (
                'equipment_confirmation:TEST-OLD',
                json.dumps({'equipment_label': 'TEST ONLY', 'id': 'TEST-OLD'})))
        with patch('labcal.application.document_templates.require_item'), \
             patch('labcal.application.document_templates.get_extraction', return_value={
                 'payload_hash': 'TEST-EXT-HASH',
                 'payload': {'source_id': original['payload']['source_id']}}), \
             patch('labcal.application.document_templates.verify_confirmation',
                   side_effect=AssertionError('Historical conclusions must not be read')) as verify:
            prepared = prepare_confirmation(self.w, 'LAB_TEST', self.c['id'],
                                            'EXT-TEST', 'TEST ONLY', fresh_review=True)
            verify.assert_not_called()
            self.assertEqual(prepared['task']['input_policy'],
                             'originals_and_direct_user_evidence')
            self.assertEqual(prepared['task']['coverage_scope'], [])
            # The normal path retains explicit reference-context inheritance.
            with self.assertRaisesRegex(AssertionError, 'Historical conclusions'):
                prepare_confirmation(self.w, 'LAB_TEST', self.c['id'],
                                     'EXT-TEST', 'TEST ONLY')
        with store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM meta WHERE key=?",
                ('equipment_confirmation:TEST-OLD',)).fetchone()[0], 1)

    def test_finish_shared_record_is_immutable_and_scoped(self):
        from labcal.application.document_templates import finish_confirmation, verify_confirmation
        from labcal.util import read_json
        original = self.register()
        with patch('labcal.application.document_templates.require_item'), patch('labcal.application.document_templates.get_extraction', return_value={'payload_hash':'TEST-EXT-HASH','payload':{'source_id':original['payload']['source_id']}}):
            prepared = prepare_confirmation(self.w, 'LAB_TEST', self.c['id'], 'EXT-TEST', 'TEST ONLY')
            record = {'coverage':{'claim':'scoped','scope_note':'TEST ONLY','methods':[]}, 'author':'TEST agent', 'scope':'TEST ONLY', 'evidence_references':['TEST ONLY'], 'rows':[
                dict(function='TEST function', purpose='TEST task', state='insufficient_evidence',
                     parameter_results='TEST function / TEST task / TEST result', method_judgment='TEST judgment', uncertainty_evaluation='TEST U',
                     traceability='TEST trace', application='TEST use')]}
            record['publication_review'] = publication_review(record)
            record['technical_review'] = technical_review(record)
            # Legacy registration mechanics are exercised here; quality gate has
            # separate tests with real page evidence and completed self runs.
            from labcal.util import content_hash, write_json
            fixed_path = Path(prepared['directory'])/'confirmation-task.json'
            old_task = read_json(fixed_path)['payload']; old_task.pop('quality_version'); old_task.pop('use_basis_version'); old_task.pop('reasoning_version'); old_task.pop('uncertainty_review_version')
            write_json(fixed_path, {'payload': old_task, 'payload_hash': content_hash(old_task)}, overwrite=True)
            document = Path(self.tmp.name) / 'filled.docx'
            write_form(document, record)
            args = (self.w,'LAB_TEST',self.c['id'],prepared['task']['id'],record,document,0)
            result = finish_confirmation(*args)
            self.assertFalse(result['technical_approval'])
            self.assertTrue(finish_confirmation(*args)['reused'])
            with self.assertRaises(LabcalError):
                finish_confirmation(self.w,'LAB_OTHER',self.c['id'],prepared['task']['id'],record,document,0)
            record['rows'][0]['application'] = 'different'
            with self.assertRaises(LabcalError): finish_confirmation(*args)
            target = self.w.lab_store('LAB_TEST').directory/result['document_path']
            target.write_bytes(b'changed')
            with self.assertRaises(LabcalError): verify_confirmation(self.w.lab_store('LAB_TEST'),result)
            self.assertEqual(read_json(Path(prepared['directory'])/'confirmation-task.json')['payload']['state'], 'awaiting_evidence_and_form_filling')

    def test_coverage_blocks_omitted_method_and_unresolved_complete_claim(self):
        from labcal.application.document_templates import validate_coverage
        import copy
        sid='REF-TEST-COVERAGE'
        image=self.w.reference_store().directory/'processing'/'methods'/sid/'pages'/'P1.png'
        image.parent.mkdir(parents=True);image.write_bytes(b'TEST ONLY page image')
        source={'id':sid,'sha256':'TEST HASH','metadata':{'pages':1}}
        scope={'source_id':sid,'source_hash':'TEST HASH','standard':'TEST ONLY','relationship':'laboratory_adopted'}
        task={'lab_id':'LAB_TEST','coverage_scope':[scope]}
        rec={'rows':[{'state':'meets_requirement'}], 'coverage':{'claim':'complete_for_declared_scope','scope_note':'TEST ONLY','methods':[]}}
        with patch('labcal.application.document_templates.verify_source_integrity',return_value=source):
            with self.assertRaises(LabcalError) as error:validate_coverage(self.w,task,rec)
            self.assertEqual(error.exception.code,'CONFIRMATION_COVERAGE_MISSING')
            method={'source_id':sid,'search_note':'TEST ONLY all relevant sections','items':[
                {'task':'TEST dimension','disposition':'assessed','reason':'TEST ONLY','row_numbers':[1],
                 'evidence':[{'page':1,'clause':'TEST','quote':'TEST ONLY','image_sha256':file_hash(image)}]}]}
            rec['coverage']['methods']=[method]
            validate_coverage(self.w,task,rec)
            bad=copy.deepcopy(rec);bad['coverage']['methods'][0]['items'][0]['disposition']='insufficient_evidence'
            with self.assertRaises(LabcalError) as error:validate_coverage(self.w,task,bad)
            self.assertEqual(error.exception.code,'CONFIRMATION_COVERAGE_UNRESOLVED')
            bad['coverage']['claim']='scoped';validate_coverage(self.w,task,bad)
            omitted=copy.deepcopy(task);omitted['coverage_scope'].append({**scope,'source_id':'REF-OTHER-TEST'})
            with self.assertRaises(LabcalError):validate_coverage(self.w,omitted,rec)
            method['items'][0]['row_numbers']=[2]
            with self.assertRaises(LabcalError):validate_coverage(self.w,task,rec)
        with self.assertRaises(LabcalError):validate_coverage(self.w,{},rec)

    def test_publication_review_blocks_missing_reasoning_and_unchecked_choices(self):
        import copy
        from labcal.application.document_templates import validate_publication_review
        from labcal.domain.contracts import validate_contract
        record = {'author': 'TEST ONLY', 'scope': 'TEST ONLY', 'evidence_references': ['TEST'],
                  'rows': [dict(function='外量', purpose='长度', state='meets_requirement',
                    parameter_results='外量：长度；TEST 示值误差在证书允差内。',
                    method_judgment='TEST 方法长度要求及比较；TEST 采用已说明判定规则。',
                    uncertainty_evaluation='TEST U适用范围及比较。',
                    traceability='TEST certificate', application='TEST 使用基准及无需修正。')]}
        review = publication_review(record)
        review['rows'][0]['correction'] = 'not_required'
        review['selections']['result'].update(choice='meets_declared_uses', document_texts=['结果：☑满足 □不满足'])
        review['selections']['correction'].update(choice='not_required', document_texts=['修正：□需要 ☑不需要'])
        record['publication_review'] = review
        doc = Path(self.tmp.name) / 'review.docx'; write_form(doc, record)
        validate_contract('confirmation-record.v1', record)
        validate_publication_review(record, doc, 0)
        cases = []
        bad = copy.deepcopy(record); del bad['publication_review']; cases.append((bad, 'CONFIRMATION_REVIEW_REQUIRED'))
        bad = copy.deepcopy(record); bad['publication_review']['rows'] *= 2; cases.append((bad, 'CONFIRMATION_REVIEW_ROWS'))
        bad = copy.deepcopy(record); bad['publication_review']['rows'][0]['decision_rule'] = '未出现在正式表里的分析'; cases.append((bad, 'CONFIRMATION_REASONING_QUOTE'))
        bad = copy.deepcopy(record); bad['rows'][0]['purpose'] = '没有显示的任务'; cases.append((bad, 'CONFIRMATION_USE_TITLE'))
        bad = copy.deepcopy(record); bad['rows'][0]['state'] = 'insufficient_evidence'; cases.append((bad, 'CONFIRMATION_SELECTION_CONFLICT'))
        bad = copy.deepcopy(record); bad['publication_review']['rows'][0]['correction'] = 'required'; cases.append((bad, 'CONFIRMATION_SELECTION_CONFLICT'))
        bad = copy.deepcopy(record); bad['publication_review']['selections']['correction']['choice'] = 'undetermined'; cases.append((bad, 'CONFIRMATION_SELECTION_CONFLICT'))
        for bad, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(LabcalError) as error: validate_publication_review(bad, doc, 0)
                self.assertEqual(error.exception.code, code)
        for text in ['结果：□满足 □不满足', '结果：☑满足 ☑不满足']:
            bad = copy.deepcopy(record)
            bad['publication_review']['selections']['result']['document_texts'] = [text]
            write_form(doc, bad)
            with self.assertRaises(LabcalError) as error: validate_publication_review(bad, doc, 0)
            self.assertEqual(error.exception.code, 'CONFIRMATION_SELECTION_MARK')

    def test_review_does_not_rewrite_historical_payloads(self):
        from labcal.application.document_templates import verify_confirmation
        from labcal.util import content_hash, write_json
        store = self.w.lab_store('LAB_TEST')
        folder = store.directory / 'outputs' / 'certificates' / 'CONF-TEST-OLD'; folder.mkdir(parents=True)
        doc = folder / 'old.docx'; doc.write_bytes(self.file.read_bytes())
        result = folder / 'confirmation-result.json'
        payload = {'record': {'rows': [], 'scope': 'TEST legacy without publication review'}}
        write_json(result, payload)
        metadata = {'document_path': str(doc.relative_to(store.directory)), 'document_hash': file_hash(doc),
                    'result_path': str(result.relative_to(store.directory)), 'result_hash': file_hash(result),
                    'record_hash': content_hash(payload)}
        self.assertEqual(verify_confirmation(store, metadata), payload)
        self.assertNotIn('publication_review', json.loads(result.read_text())['record'])

    def test_technical_review_rejects_metrological_category_errors(self):
        import copy
        from labcal.application.document_templates import validate_technical_review
        r = dict(state='meets_requirement', method_judgment='TEST resolution, error and rule', uncertainty_evaluation='TEST U')
        record = {'rows': [r]}
        record['technical_review'] = technical_review(record)
        rule = record['technical_review']['rules'][0]
        rule.update(requirement_kind='maximum_error', compared_quantity='indication_error',
                    decision_mode='direct_comparison', limit_basis='method_requirement')
        validate_technical_review(record, required=True)
        cases = [({'requirement_kind':'resolution'}, 'CONFIRMATION_QUANTITY_MISMATCH'),
                 ({'requirement_kind':'weighing_precision'}, 'CONFIRMATION_INTERPRETATION_REQUIRED'),
                 ({'requirement_kind':'repeatability_range', 'compared_quantity':'repeatability_sd'}, 'CONFIRMATION_QUANTITY_MISMATCH'),
                 ({'limit_basis':'instrument_specification'}, 'CONFIRMATION_CONFIGURATION_REQUIRED'),
                 ({'limit_basis':'no_independent_limit'}, 'CONFIRMATION_CHARACTERISTIC_CONFLICT'),
                 ({'comparison_quote':'unsupported'}, 'CONFIRMATION_TECHNICAL_QUOTE')]
        for changes, code in cases:
            with self.subTest(changes=changes):
                bad=copy.deepcopy(record);bad['technical_review']['rules'][0].update(changes)
                with self.assertRaises(LabcalError) as e: validate_technical_review(bad, required=True)
                self.assertEqual(e.exception.code, code)
        bad=copy.deepcopy(record);bad['technical_review']['rules'].append({**rule, 'decision_mode':'guard_band'})
        with self.assertRaises(LabcalError) as e: validate_technical_review(bad, required=True)
        self.assertEqual(e.exception.code, 'CONFIRMATION_RULE_INCONSISTENT')
        validate_technical_review({'rows':[r]}, required=False)
        with self.assertRaises(LabcalError): validate_technical_review({'rows':[r]}, required=True)

    def test_recorded_characteristic_is_neutral_not_a_pass_or_missing_evidence(self):
        from labcal.application.document_templates import validate_publication_review, validate_technical_review
        from labcal.domain.contracts import validate_contract
        row=dict(function='TEST function', purpose='TEST purpose', state='reported_characteristic',
                 parameter_results='TEST function TEST purpose', method_judgment='TEST no independent limit',
                 uncertainty_evaluation='TEST U not separately reported', traceability='TEST trace', application='TEST application')
        record=dict(author='TEST',scope='TEST',evidence_references=['TEST'],rows=[row])
        record['publication_review']=publication_review(record);record['technical_review']=technical_review(record)
        validate_contract('confirmation-record.v1',record);validate_technical_review(record,required=True)
        doc=Path(self.tmp.name)/'neutral.docx';write_form(doc,record)
        validate_publication_review(record,doc,0)
        record['publication_review']['selections']['result'].update(choice='meets_declared_uses',document_texts=['结果：☑满足'])
        write_form(doc,record)
        with self.assertRaises(LabcalError):validate_publication_review(record,doc,0)
