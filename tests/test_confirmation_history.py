"""Summary identity follows fixed certificates, never equipment display names."""
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from labcal.application.laboratory_summary import current_confirmations


class ConfirmationHistoryTests(unittest.TestCase):
    def record(self, task, extraction, label='TEST device', campaign='TEST campaign'):
        return dict(task_id=task, extraction_id=extraction, equipment_label=label,
                    campaign_id=campaign, payload={'TEST': task})

    def test_new_extraction_for_same_recorded_device_supersedes_old_confirmation(self):
        records = [self.record('OLD', 'EXT-A'), self.record('NEW', 'EXT-B')]
        before = copy.deepcopy(records)
        self.assertEqual(current_confirmations(records, {'EXT-A': 'CERT-1', 'EXT-B': 'CERT-1'}), [records[1]])
        self.assertEqual(records, before)

    def test_shared_certificate_does_not_merge_distinct_or_unresolved_labels(self):
        records = [self.record('ONE', 'EXT-A', 'TEST device 1'),
                   self.record('TWO', 'EXT-A', 'TEST device 2')]
        self.assertEqual(current_confirmations(records, {'EXT-A': 'CERT-1'}), records)

    def test_same_label_does_not_hide_other_certificate(self):
        records = [self.record('ONE', 'EXT-A'), self.record('TWO', 'EXT-B')]
        self.assertEqual(current_confirmations(records, {'EXT-A': 'CERT-1', 'EXT-B': 'CERT-2'}), records)

    def test_batches_keep_their_own_fixed_results(self):
        records = [self.record('ONE', 'EXT-A'), self.record('TWO', 'EXT-A', campaign='TEST other batch')]
        self.assertEqual(current_confirmations(records, {'EXT-A': 'CERT-1'}), records)

    def test_legacy_missing_parent_uses_extraction_identity_not_label(self):
        records = [self.record('ONE', 'EXT-A'), self.record('TWO', 'EXT-B'), self.record('NEW', 'EXT-A')]
        self.assertEqual(current_confirmations(records, {}), [records[2], records[1]])

    def test_compatibility_imports_use_the_same_domain_rules(self):
        from labcal.domain import confirmation_basis as domain
        from labcal.application import confirmation_quality as application
        self.assertIs(application.validate_method_basis, domain.validate_method_basis)
        self.assertIs(application.validate_basis_roles, domain.validate_basis_roles)

    def test_explicit_replacement_keeps_history_and_requires_same_identity_and_batch(self):
        old = self.record('OLD', 'EXT-A')
        new = self.record('NEW', 'EXT-B')
        old['payload'] = {'task': {'equipment_key': 'TEST-ID'}}
        new['payload'] = {'task': {'equipment_key': 'TEST-ID', 'replaces_task': 'OLD',
                                   'replacement_reason': 'TEST explicit corrected certificate'}}
        records = [old, new]; before = copy.deepcopy(records)
        self.assertEqual(current_confirmations(records, {}), [new])
        self.assertEqual(records, before)
        new['payload']['task']['equipment_key'] = 'OTHER-ID'
        self.assertEqual(current_confirmations(records, {}), records)
        new['payload']['task']['equipment_key'] = 'TEST-ID'; new['campaign_id'] = 'OTHER-YEAR'
        self.assertEqual(current_confirmations(records, {}), records)
