"""Launcher lifecycle and safe desktop-entry generation, using isolated paths."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from labcal.application.desktop import open_workbench,install_desktop_entry,health_identity
from labcal.adapters.storage import Workspace
from labcal.errors import LabcalError

class DesktopLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='labcal launch spaces ');self.addCleanup(self.tmp.cleanup)
        self.workspace=Workspace(Path(self.tmp.name));self.workspace.initialize()

    def test_reuses_only_verified_service_without_launching_another_process(self):
        with patch('labcal.application.desktop.probe',return_value=True),patch('labcal.application.desktop.subprocess.Popen') as spawn:
            result=open_workbench(self.workspace,open_browser=False)
        spawn.assert_not_called();self.assertTrue(result['reused']);self.assertEqual(result['status'],'ready')

    def test_starts_detached_with_stable_interpreter_and_workspace(self):
        process=Mock(pid=123);process.poll.return_value=None
        with patch('labcal.application.desktop.probe',side_effect=[False,True]),patch('labcal.application.desktop.socket.socket') as socket,patch('labcal.application.desktop.subprocess.Popen',return_value=process) as spawn:
            socket.return_value.__enter__.return_value.connect_ex.return_value=1
            result=open_workbench(self.workspace,open_browser=False)
        args,kwargs=spawn.call_args
        self.assertTrue(kwargs['start_new_session']);self.assertEqual(kwargs['cwd'],self.workspace.root)
        self.assertIn(str(self.workspace.root),args[0]);self.assertFalse(result['reused'])

    def test_occupied_foreign_port_is_not_killed_or_replaced(self):
        with patch('labcal.application.desktop.probe',return_value=False),patch('labcal.application.desktop.socket.socket') as socket,patch('labcal.application.desktop.subprocess.Popen') as spawn:
            socket.return_value.__enter__.return_value.connect_ex.return_value=0
            with self.assertRaises(LabcalError) as error:open_workbench(self.workspace,open_browser=False)
        self.assertEqual(error.exception.code,'WORKBENCH_PORT_IN_USE');spawn.assert_not_called()

    def test_failed_process_reports_log_instead_of_opening_dead_url(self):
        process=Mock();process.poll.return_value=1
        with patch('labcal.application.desktop.probe',return_value=False),patch('labcal.application.desktop.socket.socket') as socket,patch('labcal.application.desktop.subprocess.Popen',return_value=process),patch('labcal.application.desktop.webbrowser.open') as browser:
            socket.return_value.__enter__.return_value.connect_ex.return_value=1
            with self.assertRaises(LabcalError) as error:open_workbench(self.workspace)
        self.assertEqual(error.exception.code,'WORKBENCH_START_FAILED');browser.assert_not_called()

    def test_entry_handles_spaces_and_preserves_unrelated_existing_file(self):
        target=Path(self.tmp.name)/'desktop'
        result=install_desktop_entry(self.workspace,target);entry=Path(result['entry'])
        text=entry.read_text();self.assertIn('"$labcal_project/labcal"',text);self.assertTrue(entry.stat().st_mode&0o100)
        self.assertEqual(install_desktop_entry(self.workspace,target)['entry'],str(entry))
        entry.write_text('unrelated user file')
        with self.assertRaises(LabcalError):install_desktop_entry(self.workspace,target)
        self.assertEqual(entry.read_text(),'unrelated user file')

    def test_health_identity_distinguishes_workspaces(self):
        other=Workspace(Path(self.tmp.name)/'other')
        self.assertNotEqual(health_identity(self.workspace)['workspace_id'],health_identity(other)['workspace_id'])
