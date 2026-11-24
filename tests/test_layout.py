"""Filesystem-only reorganization preserves immutable facts and portable backups."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from labcal.adapters.storage import Workspace
from labcal.adapters.layout import output_directory, upload_directory, processing_directory
from labcal.application.layout_migration import organize_layout
from labcal.application.laboratories import create_laboratory
from labcal.application.maintenance import backup_laboratory, restore_laboratory
from labcal.util import file_hash, write_json
from labcal.errors import LabcalError

class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.ws=Workspace(self.temp.name); self.ws.initialize()
        create_laboratory(self.ws,'LAB_TEST','TEST ONLY',test_only=True)
        self.store=self.ws.lab_store('LAB_TEST')

    def legacy_export(self):
        folder=self.store.directory/'exports'/'RUN-TEST'; folder.mkdir(parents=True)
        (folder/'result.txt').write_text('fixed artifact')
        manifest={'kind':'plan_export','run_id':'RUN-TEST','artifacts':[{'name':'result.txt','sha256':file_hash(folder/'result.txt')}]}
        write_json(folder/'manifest.json',manifest)
        with self.store.connect() as db:
            db.execute('INSERT INTO runs VALUES (?,?,?,?)',('RUN-TEST','plan_export',json.dumps(manifest),'2030-01-01'))
        return folder

    def test_migrate_preserves_database_and_all_artifacts_and_is_idempotent(self):
        folder=self.legacy_export(); digest=file_hash(folder/'manifest.json'); before=file_hash(self.store.path)
        preview=organize_layout(self.ws,'LAB_TEST'); self.assertFalse(preview['applied']); self.assertTrue(folder.exists())
        result=organize_layout(self.ws,'LAB_TEST',apply=True)
        target=output_directory(self.store,'RUN-TEST','plan_export',existing=True)
        self.assertEqual(target,self.store.directory/'outputs/calibration/RUN-TEST')
        self.assertEqual(file_hash(target/'manifest.json'),digest)
        self.assertEqual(file_hash(self.store.path),before)
        self.assertFalse(folder.exists()); self.assertTrue(result['applied'])
        self.assertEqual(organize_layout(self.ws,'LAB_TEST',apply=True)['moves'],[])

    def test_scope_routes_and_duplicates_rejected(self):
        folder=self.legacy_export()
        self.assertEqual(output_directory(self.store,'RUN-TEST','plan_export'),folder)
        duplicate=self.store.directory/'outputs/calibration/RUN-TEST'; duplicate.mkdir()
        with self.assertRaises(LabcalError): output_directory(self.store,'RUN-TEST','plan_export')
        with self.assertRaises(LabcalError): upload_directory(self.store,'../LAB_OTHER','calibration','UPLOAD-1')
        with self.assertRaises(LabcalError): processing_directory(self.store,'../other','RUN-1')
        other=self.ws.lab_store('LAB_TEST')
        self.assertNotEqual(upload_directory(other,'CAM-A','calibration','UPLOAD-1'),upload_directory(other,'CAM-B','calibration','UPLOAD-1'))
        self.assertNotEqual(output_directory(other,'RUN-A','plan_export'),output_directory(other,'RUN-A','certificate_review_export'))

    def test_conflict_refuses_all_moves(self):
        old=self.legacy_export(); (self.store.directory/'outputs/calibration/RUN-TEST').mkdir()
        with self.assertRaises(LabcalError): organize_layout(self.ws,'LAB_TEST',apply=True)
        self.assertTrue(old.exists())

    def test_backup_restore_includes_new_lanes_and_legacy_uploads(self):
        self.legacy_export(); organize_layout(self.ws,'LAB_TEST',apply=True)
        legacy=self.store.directory/'inbox'/'UPLOAD-OLD'; legacy.mkdir(parents=True); (legacy/'source.txt').write_text('historical input')
        upload=upload_directory(self.store,'CAM-A','certificates','UPLOAD-NEW'); upload.mkdir(parents=True); (upload/'source.txt').write_text('new input')
        archive=Path(self.temp.name)/'backup.zip'; backup_laboratory(self.ws,'LAB_TEST',archive)
        restored=Workspace(Path(self.temp.name)/'restored'); restore_laboratory(restored,archive)
        target=restored.lab_store('LAB_TEST')
        self.assertEqual((output_directory(target,'RUN-TEST','plan_export',existing=True)/'result.txt').read_text(),'fixed artifact')
        self.assertEqual((target.directory/'inbox/UPLOAD-OLD/source.txt').read_text(),'historical input')
        self.assertEqual((upload_directory(target,'CAM-A','certificates','UPLOAD-NEW')/'source.txt').read_text(),'new input')

    def test_failed_move_rolls_back_completed_moves(self):
        from unittest.mock import patch
        old=self.legacy_export()
        prior=self.store.directory/'runs'/'RUN-TEST'; prior.mkdir(parents=True); (prior/'manifest.json').write_text('{}')
        rename=Path.rename; calls=[]
        def failing(path,target):
            calls.append(str(path))
            if len(calls)==2: raise OSError('simulated move failure')
            return rename(path,target)
        with patch.object(Path,'rename',failing):
            with self.assertRaises(OSError): organize_layout(self.ws,'LAB_TEST',apply=True)
        self.assertTrue(old.exists()); self.assertTrue(prior.exists())
        self.assertFalse((self.store.directory/'outputs/calibration/RUN-TEST').exists())
        journal=next((self.store.directory/'processing/maintenance').glob('LAYOUT-*/migration.json'))
        self.assertTrue(json.loads(journal.read_text())['rolled_back'])
