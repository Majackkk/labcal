"""Fixed multi-artifact delivery and scoped changes with no altered technical results."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from test_business_context import ContextFixture
from test_document_templates import write_form, publication_review
from labcal.application.business_context import assemble_context, import_note
from labcal.application.methods import import_use
from labcal.application.confirmation_delivery import annotation_inputs, write_delivery, verify_delivery
from labcal.application.confirmation_updates import context_changes, impact, import_followup, list_followups
from labcal.application.document_templates import finish_confirmation, verify_confirmation
from labcal.application.campaigns import create_campaign
from labcal.errors import LabcalError
from labcal.util import write_json, read_json, file_hash, content_hash


class ConfirmationUpdatesTests(ContextFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.c = create_campaign(self.w, 'LAB_A', 'TEST ONLY', year=2030)
        self.task = dict(id='CONF-TEST', lab_id='LAB_A', campaign_id=self.c['id'], equipment_key='asset:SAME',
                         equipment_label='TEST device', extraction_id='EXT-TEST', source_hash='TEST-source')
        self.folder = self.w.lab_store('LAB_A').directory / 'processing' / 'certificates' / self.task['id']
        self.folder.mkdir(parents=True)
        self.record = dict(author='TEST ONLY', scope='TEST ONLY', evidence_references=['TEST ONLY'], rows=[
            dict(function='TEST', purpose='TEST', state='insufficient_evidence',
                 parameter_results='TEST', method_judgment='TEST', uncertainty_evaluation='TEST',
                 traceability='TEST', application='TEST')])
        self.record['publication_review'] = publication_review(self.record)
        self.doc = self.folder / 'TEST.docx'; write_form(self.doc, self.record)
        self.pdf = self.folder / 'TEST-B3.pdf'; self.pdf.write_bytes(b'%PDF-TEST ONLY synthetic artifact')
        self.annotation = self.folder / 'annotation-manifest.json'
        write_json(self.annotation, dict(confirmation_task=self.task['id'], source_hash=self.task['source_hash'],
                   output=self.pdf.name, output_hash=file_hash(self.pdf), document_hash=file_hash(self.doc)))

    def finish(self, **extra):
        with patch('labcal.application.document_templates.review_confirmation', return_value=(self.task, None)):
            return finish_confirmation(self.w, 'LAB_A', self.c['id'], self.task['id'], self.record, self.doc, 0, **extra)

    def test_delivery_is_complete_immutable_and_pdf_tampering_is_detected(self):
        result = self.finish(annotations=self.pdf, annotation_manifest=self.annotation)
        store = self.w.lab_store('LAB_A')
        manifest = verify_delivery(store, result)
        self.assertEqual({a['role'] for a in manifest['artifacts']},
                         {'document', 'result', 'annotations', 'annotation_manifest'})
        self.assertTrue(self.finish(annotations=self.pdf, annotation_manifest=self.annotation)['reused'])
        self.assertEqual(verify_confirmation(store, result)['record'], self.record)
        target = (store.directory / result['delivery_path']).parent / self.pdf.name
        target.write_bytes(b'%PDF-CHANGED')
        with self.assertRaises(LabcalError): verify_confirmation(store, result)
        self.assertTrue(self.pdf.read_bytes().endswith(b'synthetic artifact'))

    def test_no_problem_does_not_need_empty_pdf_and_published_annotations_cannot_be_added(self):
        result = self.finish()
        manifest = verify_delivery(self.w.lab_store('LAB_A'), result)
        self.assertEqual({a['role'] for a in manifest['artifacts']}, {'document', 'result'})
        with self.assertRaises(LabcalError): self.finish(annotations=self.pdf, annotation_manifest=self.annotation)

    def test_wrong_task_source_document_or_pdf_is_rejected_before_registration(self):
        original = read_json(self.annotation)
        for field in ['confirmation_task', 'source_hash', 'document_hash', 'output_hash']:
            write_json(self.annotation, {**original, field: 'WRONG'}, overwrite=True)
            with self.subTest(field=field), self.assertRaises(LabcalError):
                self.finish(annotations=self.pdf, annotation_manifest=self.annotation)
        self.assertFalse((self.w.lab_store('LAB_A').directory / 'outputs' / 'certificates' / self.task['id']).exists())

    def test_changed_context_identifies_only_relevant_lab_and_excludes_candidates(self):
        first = import_use(self.w, 'LAB_A', self.use)
        before = assemble_context(self.w, 'LAB_A', 'asset:SAME')
        import_use(self.w, 'LAB_B', {**self.use, 'purpose': 'TEST other lab'})
        import_use(self.w, 'LAB_A', {**self.use, 'purpose': 'TEST candidate', 'state': 'candidate'})
        self.assertEqual(context_changes(before, assemble_context(self.w, 'LAB_A', 'asset:SAME')), [])
        import_use(self.w, 'LAB_A', {**self.use, 'purpose': 'TEST changed purpose', 'supersedes': first['id']})
        changes = context_changes(before, assemble_context(self.w, 'LAB_A', 'asset:SAME'))
        self.assertEqual([c['kind'] for c in changes], ['uses'])

    def test_empty_new_laboratory_needs_no_certificate_or_template_to_check_updates(self):
        self.assertEqual(impact(self.w, 'LAB_B'), [])

    def test_followup_export_links_to_fixed_delivery_without_rewriting_results(self):
        from labcal.application.laboratory_summary import export_summary
        from zipfile import ZipFile
        result = self.finish(annotations=self.pdf, annotation_manifest=self.annotation)
        import_followup(self.w, 'LAB_A', self.task['id'], dict(issue_id='TEST-01', category='correction',
            state='response_received', statement='TEST response', evidence_reference='TEST reply'))
        summary = export_summary(self.w, 'LAB_A')
        with ZipFile(summary['path']) as z:
            self.assertIn('变更与跟进', z.read('xl/workbook.xml').decode())
            all_text = ''.join(z.read(p).decode() for p in z.namelist() if p.startswith('xl/worksheets/'))
            self.assertIn('已收到回复', all_text)
            self.assertIn('delivery-manifest.json', all_text)
        self.assertEqual(file_hash(self.w.lab_store('LAB_A').directory/result['result_path']), result['result_hash'])

    def test_archived_context_survives_processing_cleanup_and_change_is_reminder(self):
        use = import_use(self.w, 'LAB_A', self.use)
        snapshot = assemble_context(self.w, 'LAB_A', 'asset:SAME')
        write_json(self.folder / 'business-context.json', snapshot)
        self.task['context_hash'] = content_hash(snapshot)
        result = self.finish()
        (self.folder / 'business-context.json').unlink()
        before_hash = result['result_hash']
        self.assertEqual(impact(self.w, 'LAB_A', task_id=self.task['id'])[0]['status'], 'unchanged')
        import_use(self.w, 'LAB_A', {**self.use, 'purpose': 'TEST added purpose'})
        report = impact(self.w, 'LAB_A', task_id=self.task['id'])[0]
        self.assertEqual(report['status'], 'review_required')
        self.assertEqual(verify_confirmation(self.w.lab_store('LAB_A'), result)['record'], self.record)
        self.assertEqual(file_hash(self.w.lab_store('LAB_A').directory / result['result_path']), before_hash)
        with self.assertRaises(LabcalError): impact(self.w, 'LAB_B', task_id=self.task['id'])

    def test_followups_are_versioned_scoped_and_reply_never_changes_original_judgment(self):
        result = self.finish()
        record = dict(issue_id='TEST-01', category='clarification', state='open',
                      statement='TEST request', evidence_reference='TEST evidence')
        first = import_followup(self.w, 'LAB_A', self.task['id'], record)
        with self.assertRaises(LabcalError): import_followup(self.w, 'LAB_B', self.task['id'], record)
        reply = {**record, 'state': 'response_received', 'statement': 'TEST received reply', 'supersedes': first['id']}
        second = import_followup(self.w, 'LAB_A', self.task['id'], reply)
        self.assertEqual(len(list_followups(self.w, 'LAB_A', history=True)), 2)
        self.assertEqual(list_followups(self.w, 'LAB_A')[0]['id'], second['id'])
        self.assertEqual(impact(self.w, 'LAB_A')[0]['status'], 'review_required')
        with self.assertRaises(LabcalError):
            import_followup(self.w, 'LAB_A', self.task['id'], {**reply, 'state': 'addressed', 'supersedes': second['id']})
        self.assertEqual(verify_confirmation(self.w.lab_store('LAB_A'), result)['record'], self.record)
