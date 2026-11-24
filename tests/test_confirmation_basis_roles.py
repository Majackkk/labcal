"""Binding requirements and reference-only checks must not be interchangeable."""
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from labcal.application.confirmation_quality import validate_basis_roles
from labcal.errors import LabcalError


class BasisRolesTests(unittest.TestCase):
    def fixture(self):
        return {
            'rows': [dict(method_judgment='TEST method criterion. TEST reference only. TEST impact evaluated.',
                          uncertainty_evaluation='TEST U', application='TEST use')],
            'publication_review': {'selections': {'result': {'choice': 'meets_declared_uses'}}},
            'technical_review': {'rules': [
                dict(row_number=1, dimension='intended_use', limit_basis='method_requirement',
                     basis_role='acceptance_requirement', basis_scope_quote='TEST method criterion.',
                     state='meets_requirement', decision_mode='direct_comparison'),
                dict(row_number=1, dimension='instrument_requirement', limit_basis='instrument_specification',
                     basis_role='technical_reference', basis_scope_quote='TEST reference only.',
                     state='meets_requirement', decision_mode='direct_comparison')]} }

    def test_requires_visible_roles_but_keeps_legacy_readable(self):
        rec = self.fixture(); validate_basis_roles(rec, required=True)
        del rec['technical_review']['rules'][0]['basis_role']
        validate_basis_roles(rec)
        with self.assertRaises(LabcalError): validate_basis_roles(rec, required=True)
        rec = self.fixture(); rec['technical_review']['rules'][0]['basis_scope_quote'] = 'not in form'
        with self.assertRaises(LabcalError): validate_basis_roles(rec, required=True)

    def test_instrument_pass_does_not_cancel_failed_method(self):
        rec = self.fixture(); rec['technical_review']['rules'][0]['state'] = 'does_not_meet'
        with self.assertRaises(LabcalError) as ctx: validate_basis_roles(rec, required=True)
        self.assertEqual(ctx.exception.code, 'CONFIRMATION_ACCEPTANCE_UNMET')

    def test_reference_failure_needs_impact_not_automatic_use_failure(self):
        rec = self.fixture(); ref = rec['technical_review']['rules'][1]
        ref['state'] = 'does_not_meet'
        with self.assertRaises(LabcalError): validate_basis_roles(rec, required=True)
        ref['reference_impact_quote'] = 'TEST impact evaluated.'
        validate_basis_roles(rec, required=True)
        # A visible impact explanation is only a declaration, not proof of its truth.
        ref['reference_impact_quote'] = 'hidden explanation'
        with self.assertRaises(LabcalError): validate_basis_roles(rec, required=True)

    def test_binding_instrument_requirement_cannot_be_waived(self):
        rec = self.fixture(); ref = rec['technical_review']['rules'][1]
        ref['basis_role'] = 'acceptance_requirement'
        with self.assertRaises(LabcalError): validate_basis_roles(rec, required=True)
        ref['applicability_basis'] = 'TEST documented mandatory application'
        validate_basis_roles(rec, required=True)
        for state in ('does_not_meet', 'insufficient_evidence'):
            ref['state'] = state
            with self.assertRaises(LabcalError): validate_basis_roles(rec, required=True)

    def test_use_cannot_be_labelled_reference(self):
        rec = self.fixture(); rec['technical_review']['rules'][0]['basis_role'] = 'technical_reference'
        with self.assertRaises(LabcalError): validate_basis_roles(rec, required=True)

    def test_neutral_characteristics_cannot_be_independent_pass(self):
        rec = self.fixture(); ref = rec['technical_review']['rules'][1]
        ref.update(basis_role='characteristic_record', decision_mode='report_only')
        with self.assertRaises(LabcalError): validate_basis_roles(rec, required=True)
        ref['state'] = 'reported_characteristic'
        validate_basis_roles(rec, required=True)
