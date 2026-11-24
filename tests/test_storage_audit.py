"""Storage projections are checked against fixed facts in temporary test labs."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from labcal.adapters.storage import Workspace
from labcal.application.laboratories import create_laboratory
from labcal.application.campaigns import create_campaign, campaign_directory, attach_item, update_campaign
from labcal.application.workbooks import import_workbook
from labcal.application.storage_audit import audit_storage
from labcal.application.sources import verify_source_integrity
from labcal.application.certificates import add_certificate
from labcal.application.console import console_state
from labcal.util import write_json, file_hash
from labcal.errors import LabcalError
from test_campaigns import sample_workbook


class StorageAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.ws = Workspace(Path(self.temp.name)); self.ws.initialize()
        create_laboratory(self.ws, 'LAB_TEST', 'TEST ONLY', test_only=True)
        self.campaign = create_campaign(self.ws, 'LAB_TEST', 'Synthetic cycle', year=2030)
        self.path = Path(self.temp.name) / 'test.xlsx'; sample_workbook(self.path)
        self.book = import_workbook(self.ws, 'LAB_TEST', self.campaign['id'], self.path)

    def test_readonly_audit_passes_and_detects_archive_damage_without_repairing_it(self):
        database = self.ws.lab_store('LAB_TEST').path
        before = database.read_bytes()
        result = audit_storage(self.ws, 'LAB_TEST', self.campaign['id'])
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(database.read_bytes(), before)
        archive = Path(self.book['import_directory']) / '原件.xlsx'
        archive.chmod(0o644); archive.write_bytes(b'broken synthetic copy')
        failed = audit_storage(self.ws, 'LAB_TEST', self.campaign['id'])
        self.assertTrue(any(i['code'] == 'FILE_INTEGRITY_FAILED' for i in failed['issues']))
        self.assertEqual(archive.read_bytes(), b'broken synthetic copy')
        verify_source_integrity(self.ws, 'LAB_TEST', self.book['source_id'])

    def test_changed_year_preserves_archive_and_stale_projection_is_distinct_from_original_damage(self):
        updated = update_campaign(self.ws, 'LAB_TEST', self.campaign['id'], {'year':2031, 'expected_record_hash':self.campaign['record_hash']})
        folder = campaign_directory(self.ws, 'LAB_TEST', updated)
        self.assertEqual(audit_storage(self.ws, 'LAB_TEST', updated['id'])['status'], 'passed')
        index = folder / 'index.json'; data = json.loads(index.read_text()); data['items'] = []
        index.write_text(json.dumps(data))
        result = audit_storage(self.ws, 'LAB_TEST', updated['id'])
        self.assertEqual(result['status'], 'warnings')
        self.assertTrue(any(i['code'] == 'PROJECTION_STALE' for i in result['issues']))
        self.assertFalse(any(i['code'] == 'FILE_INTEGRITY_FAILED' for i in result['issues']))

    def test_export_tampering_is_detected_against_database_manifest(self):
        store = self.ws.lab_store('LAB_TEST'); output = store.directory / 'exports' / 'RUN-TEST'
        output.mkdir(parents=True); (output / 'report.html').write_text('synthetic original export')
        manifest = {'kind':'plan_export','artifacts':[{'name':'report.html','sha256':file_hash(output / 'report.html')}]}
        write_json(output / 'manifest.json', manifest)
        with store.connect() as db:
            db.execute('INSERT INTO runs VALUES (?,?,?,?)', ('RUN-TEST','plan_export',json.dumps(manifest),'2030-01-01'))
        self.assertEqual(audit_storage(self.ws, 'LAB_TEST')['status'], 'passed')
        (output / 'report.html').write_text('tampered')
        result = audit_storage(self.ws, 'LAB_TEST')
        self.assertTrue(any(i['code'] == 'FILE_INTEGRITY_FAILED' for i in result['issues']))

    def test_atomic_projection_failure_keeps_previous_complete_file(self):
        path = Path(self.temp.name) / 'projection.json'; write_json(path, {'old':True})
        with patch('labcal.util.os.replace', side_effect=OSError('simulated filesystem failure')):
            with self.assertRaises(OSError): write_json(path, {'new':True}, overwrite=True)
        self.assertEqual(json.loads(path.read_text()), {'old':True})
        self.assertEqual(list(path.parent.glob('.labcal-write-*')), [])
        with self.assertRaises(LabcalError): write_json(path, {'overwrite':False})

    def test_report_versions_do_not_multiply_list_rows_or_leak_to_another_year(self):
        file = Path(self.temp.name) / 'synthetic.pdf'; file.write_bytes(b'%PDF-SYNTHETIC TEST ONLY')
        def reader(version):
            def read(path, source_id, source_hash, **kwargs):
                return {'schema_version':'1.0','source_id':source_id,'source_hash':source_hash,'document_kind':'unknown',
                    'pages':[{'page':1,'lines':[],'tables':[],'text':''}], 'fields':{},'parser_version':version,'warnings':[]}
            return read
        old = add_certificate(self.ws,'LAB_TEST',file=file,pdf_reader=reader('TEST-1'))
        new = add_certificate(self.ws,'LAB_TEST',file=file,pdf_reader=reader('TEST-2'))
        other = create_campaign(self.ws, 'LAB_TEST', 'Other cycle', year=2031)
        for result in [old,new]: attach_item(self.ws,'LAB_TEST',self.campaign['id'],'extraction',result['extraction_id'])
        attach_item(self.ws,'LAB_TEST',other['id'],'extraction',old['extraction_id'])
        current = console_state(self.ws,'LAB_TEST',self.campaign['id'])
        self.assertEqual(current['report_count'],1); self.assertEqual(current['certificate_matches'],1)
        self.assertEqual(current['certificates'][0]['extraction_id'],new['extraction_id'])
        self.assertEqual(current['certificates'][0]['version_count'],2)
        self.assertEqual(current['totals']['extraction'],2)
        self.assertEqual(console_state(self.ws,'LAB_TEST',other['id'])['certificates'][0]['extraction_id'],old['extraction_id'])


if __name__ == '__main__': unittest.main()
