"""Two isolated laboratories share standards, never uses, configuration or choices."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from labcal.adapters.storage import Workspace
from labcal.application.laboratories import create_laboratory, add_equipment
from labcal.application.campaigns import create_campaign
from labcal.application.sources import import_source
from labcal.application.methods import import_method, import_use, import_adoption
from labcal.application.business_context import (
    assemble_context, import_note, list_notes, validate_context_coverage, context_signature)
from labcal.errors import LabcalError
from labcal.util import content_hash, read_json


class ContextFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.w = Workspace(self.tmp.name); self.w.initialize()
        for lab in ['LAB_A', 'LAB_B']:
            create_laboratory(self.w, lab, 'TEST ONLY ' + lab, test_only=True)
        source = Path(self.tmp.name) / 'TEST-standard.txt'; source.write_text('TEST ONLY synthetic method')
        self.source = import_source(self.w, source, kind='test_method_standard', shareable=True,
                                    metadata={'pages': 1})['id']
        req = dict(id='R', equipment_category='TEST', parameter='TEST', requirement_type='mpe',
                   origin='derived', statement='TEST ONLY', range_text='', condition='', state='candidate', evidence=[],
                   criterion=dict(operator='abs_le', unit='mm', expression='length', relative_basis='not_applicable',
                                  inputs={'length': {'unit': 'mm', 'positive': True}}))
        self.method = import_method(self.w, dict(standard_code='TEST-STD', edition='2030', title='TEST ONLY',
            source_id=self.source, author='TEST ONLY', coverage='TEST ONLY candidate', requirements=[req], gaps=['TEST ONLY']))
        self.use = dict(equipment_key='asset:SAME', equipment_name='TEST ONLY', method_id=self.method['id'],
                        purpose='TEST measurement', use_range='TEST range', conditions={}, state='user_adopted',
                        requirement_ids=['R'], user_statement='TEST direct user statement', evidence_reference='TEST ONLY')

    def note(self, **extra):
        return dict(kind='decision_rule', key='TEST-rule', equipment_key='asset:SAME', scope='TEST purpose only',
                    statement='TEST direct comparison; U separately evaluated', state='recorded',
                    evidence_reference='TEST direct user choice', **extra)


class BusinessContextTests(ContextFixture, unittest.TestCase):

    def test_isolation_same_asset_shared_method_and_different_conditions(self):
        a = import_use(self.w, 'LAB_A', {**self.use, 'conditions': {'length': {'value': '4', 'unit': 'mm'}}})
        b = import_use(self.w, 'LAB_B', {**self.use, 'conditions': {'length': {'value': '8', 'unit': 'mm'}}})
        add_equipment(self.w, 'LAB_A', 'SAME', 'TEST device', configuration={'TEST': 'A'})
        add_equipment(self.w, 'LAB_B', 'SAME', 'TEST device', configuration={'TEST': 'B'})
        import_note(self.w, 'LAB_A', self.note())
        ca = assemble_context(self.w, 'LAB_A', 'asset:SAME'); cb = assemble_context(self.w, 'LAB_B', 'asset:SAME')
        self.assertEqual([u['id'] for u in ca['uses']], [a['id']])
        self.assertEqual([u['id'] for u in cb['uses']], [b['id']])
        self.assertEqual(ca['equipment'][0]['configuration'], {'TEST': 'A'})
        self.assertEqual(cb['notes'], [])
        self.assertEqual(ca['methods'], cb['methods'])

    def test_roles_are_explicit_related_uses_and_candidates_stay_separate(self):
        import_use(self.w, 'LAB_A', {**self.use, 'measurement': dict(role='force', quantity='force',
                   direction='compression', related_equipment_keys=['asset:GAUGE'])})
        import_use(self.w, 'LAB_A', {**self.use, 'equipment_key': 'asset:GAUGE',
                   'measurement': dict(role='deflection', quantity='length')})
        import_use(self.w, 'LAB_A', {**self.use, 'purpose': 'TEST candidate', 'state': 'candidate'})
        ctx = assemble_context(self.w, 'LAB_A', 'asset:SAME')
        self.assertEqual(len(ctx['uses']), 1); self.assertEqual(len(ctx['related_uses']), 1)
        self.assertEqual(len(ctx['candidates']), 1)
        self.assertEqual(ctx['uses'][0]['payload']['measurement']['direction'], 'compression')
        self.assertEqual(ctx['questions'][0]['fields'], ['length'])
        self.assertEqual(assemble_context(self.w, 'LAB_B', 'asset:SAME')['uses'], [])

    def test_notes_version_conflict_history_and_candidate_cannot_replace_fact(self):
        first = import_note(self.w, 'LAB_A', self.note())
        self.assertEqual(first, import_note(self.w, 'LAB_A', self.note()))
        changed = {**self.note(), 'statement': 'TEST changed choice'}
        with self.assertRaises(LabcalError): import_note(self.w, 'LAB_A', changed)
        candidate = {**changed, 'supersedes': first['id'], 'state': 'candidate'}
        with self.assertRaises(LabcalError): import_note(self.w, 'LAB_A', candidate)
        second = import_note(self.w, 'LAB_A', {**changed, 'supersedes': first['id']})
        self.assertEqual([n['id'] for n in list_notes(self.w, 'LAB_A')], [second['id']])
        self.assertEqual(len(list_notes(self.w, 'LAB_A', history=True)), 2)
        with self.assertRaises(LabcalError): import_note(self.w, 'LAB_B', {**changed, 'supersedes': first['id']})

    def test_known_use_cannot_be_silently_omitted_or_assigned_to_wrong_standard(self):
        use = import_use(self.w, 'LAB_A', self.use)
        ctx = assemble_context(self.w, 'LAB_A', 'asset:SAME')
        rec = {'coverage': {'methods': [{'source_id': self.source, 'items': [{'use_ids': []}]}]}}
        with self.assertRaises(LabcalError): validate_context_coverage(ctx, rec)
        rec['coverage']['methods'][0]['items'][0]['use_ids'] = [use['id']]
        validate_context_coverage(ctx, rec)
        rec['coverage']['methods'][0]['source_id'] = 'REF-OTHER'
        with self.assertRaises(LabcalError): validate_context_coverage(ctx, rec)

    def test_prepare_freezes_actual_context_without_reading_historical_results(self):
        from labcal.application.document_templates import register, prepare_confirmation
        import_use(self.w, 'LAB_A', self.use)
        campaign = create_campaign(self.w, 'LAB_A', 'TEST ONLY', year=2030)
        doc = Path(self.tmp.name) / 'TEST.docx'
        with ZipFile(doc, 'w') as z:
            z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
        tpl = register(self.w, 'LAB_A', campaign['id'], doc, 'calibration-confirmation', 'TEST ONLY', 'TEST user selection')
        with patch('labcal.application.document_templates.require_item'), patch(
                'labcal.application.document_templates.get_extraction', return_value={
                    'payload_hash': 'TEST', 'payload': {'source_id': tpl['payload']['source_id']}}), patch(
                'labcal.application.document_templates.verify_confirmation', side_effect=AssertionError('old result')):
            task = prepare_confirmation(self.w, 'LAB_A', campaign['id'], 'EXT-TEST', 'TEST device',
                                        fresh_review=True, equipment_key='asset:SAME')
        fixed = read_json(Path(task['directory']) / 'business-context.json')
        self.assertEqual(content_hash(fixed), task['task']['context_hash'])
        self.assertEqual(task['task']['equipment_key'], 'asset:SAME')
        self.assertEqual(len(fixed['uses']), 1)
        self.assertEqual(len(task['task']['coverage_scope']), 1)

    def test_signature_ignores_unrelated_standard_but_sees_related_device_changes(self):
        import_use(self.w, 'LAB_A', self.use)
        ctx = assemble_context(self.w, 'LAB_A', 'asset:SAME')
        changed = copy.deepcopy(ctx)
        changed['adoptions'].append(dict(id='TEST-unrelated', record_hash='TEST', payload={'source_id': 'REF-OTHER'}))
        self.assertEqual(context_signature(ctx), context_signature(changed))
        changed['equipment'].append(dict(id='TEST-related', config_hash='TEST'))
        self.assertNotEqual(context_signature(ctx), context_signature(changed))

    def test_context_management_does_not_write_plan_or_confirmation_lane(self):
        from labcal.application.laboratories import add_equipment
        from labcal.application.plans import create_plan, get_plan
        equipment = add_equipment(self.w, 'LAB_A', 'SAME', 'TEST device')
        plan = create_plan(self.w, 'LAB_A', dict(equipment_id=equipment['id'], title='TEST candidate',
            origin='user_plan', source_ids=[self.source], requirements=[dict(id='TEST-P', parameter='TEST', unit='mm',
                delivery_route='external_calibration', coverage_policy='explicit_points', points=['1'],
                report_fields=['indication_error'], evidence=[dict(source_id=self.source, claim_kind='normative', page=1)])]))
        before = get_plan(self.w, 'LAB_A', plan['plan_id'])
        import_use(self.w, 'LAB_A', self.use)
        import_note(self.w, 'LAB_A', self.note())
        assemble_context(self.w, 'LAB_A', 'asset:SAME')
        self.assertEqual(get_plan(self.w, 'LAB_A', plan['plan_id']), before)
        with self.w.lab_store('LAB_A').connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM confirmations').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM baselines').fetchone()[0], 0)
