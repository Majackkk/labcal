"""Synthetic metrology boundary and publication checks; no real lab writes."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from labcal.domain.confirmation_uncertainty import evaluate_point, uncertainty_issues
from labcal.domain.contracts import validate_contract
from labcal.errors import LabcalError


def point(error='0.2', u='0.4', mpe='1', label='TEST point'):
    return dict(label=label, error=error, expanded_uncertainty=u, mpev=mpe, unit='TEST unit')


def record():
    text = 'TEST JJF1094 scope and k=2 equivalence. TEST 0.4/1>1/3; 0.2+0.4=0.6≤1. TEST conforms.'
    return dict(rows=[dict(uncertainty_evaluation=text)], technical_review=dict(rules=[dict(
        row_number=1, decision_group='TEST use', requirement_kind='maximum_error',
        compared_quantity='indication_error', requirement_interpretation='measurement_accuracy',
        state='meets_requirement', decision_mode='specification_check',
        reasoning=dict(uncertainty_role='decision_component', uncertainty_review=dict(
            route='jjf1094_general', application='decision', coverage_basis='k2_equivalent',
            basis_quote='TEST JJF1094 scope and k=2 equivalence.',
            calculation_quote='TEST 0.4/1>1/3; 0.2+0.4=0.6≤1.', judgment_quote='TEST conforms.',
            outcome='meets_requirement', points=[point()])))]))


class UncertaintyTests(unittest.TestCase):
    def test_one_third_uses_exact_comparison_and_includes_equality(self):
        for error, u, mpe, expected, clause in [
            ('1', '0.3', '1', 'meets_requirement', '5.3.1.4'),
            ('-1.001', '0.3', '1', 'does_not_meet', '5.3.1.4'),
            ('3', '1', '3', 'meets_requirement', '5.3.1.4'),
            ('0.6', '0.4', '1', 'meets_requirement', '5.3.1.6 a)'),
            ('0.60001', '0.4', '1', 'insufficient_evidence', '5.3.1.6 c)'),
            ('-1.1', '0.4', '1', 'insufficient_evidence', '5.3.1.6 c)'),
            ('1.39999', '0.4', '1', 'insufficient_evidence', '5.3.1.6 c)'),
            ('-1.4', '0.4', '1', 'does_not_meet', '5.3.1.6 b)'),
        ]:
            with self.subTest(error=error, u=u, mpe=mpe):
                result = evaluate_point(point(error, u, mpe), 'jjf1094_general')
                self.assertEqual((result['state'], result['clause']), (expected, clause))

    def test_u_at_or_above_mpe_does_not_automatically_mean_instrument_failure(self):
        for error, u, expected in [('0', '1', 'meets_requirement'), ('0.1', '1', 'insufficient_evidence'),
                                  ('0', '1.1', 'insufficient_evidence'), ('2.1', '1.1', 'does_not_meet')]:
            self.assertEqual(evaluate_point(point(error, u), 'jjf1094_general')['state'], expected)

    def test_simple_acceptance_and_supported_verification_do_not_silently_add_u(self):
        for route in ('simple_acceptance', 'verification_regulation'):
            self.assertEqual(evaluate_point(point('0.9', '0.4'), route)['state'], 'meets_requirement')
            self.assertEqual(evaluate_point(point('1.01', '0.4'), route)['state'], 'does_not_meet')
        self.assertEqual(evaluate_point(point('0.9', None), 'simple_acceptance')['state'], 'meets_requirement')

    def test_missing_invalid_or_wrong_sign_numbers_are_not_filled_or_rounded(self):
        for p in [point(u=None), point(u='-0.1'), point(mpe='0'), point(mpe='-1'), point(u='NaN'),
                  point(error='Infinity'), point(u='/'), point(error='1e9999')]:
            with self.subTest(point=p), self.assertRaises((ValueError, LabcalError)):
                evaluate_point(p, 'jjf1094_general')

    def test_required_quotes_must_be_visible_in_final_u_column(self):
        r = record(); self.assertEqual(uncertainty_issues(r, required=True), [])
        for field in ('basis_quote', 'calculation_quote', 'judgment_quote'):
            bad = copy.deepcopy(r)
            bad['technical_review']['rules'][0]['reasoning']['uncertainty_review'][field] = 'TEST absent'
            self.assertIn('CONFIRMATION_U_QUOTE', {x['code'] for x in uncertainty_issues(bad)})

    def test_legacy_records_remain_readable_but_new_work_requires_u_review(self):
        r = record(); r['technical_review']['rules'][0]['reasoning'].pop('uncertainty_review')
        self.assertEqual(uncertainty_issues(r), [])
        self.assertEqual(uncertainty_issues(r, required=True)[0]['code'], 'CONFIRMATION_U_REVIEW_REQUIRED')

    def test_wrong_antigravity_failure_threshold_is_blocked(self):
        r = record(); rule = r['technical_review']['rules'][0]; a = rule['reasoning']['uncertainty_review']
        a['points'] = [point('1.1')]; a['outcome'] = 'does_not_meet'; rule['state'] = 'does_not_meet'
        codes = {x['code'] for x in uncertainty_issues(r)}
        self.assertTrue({'CONFIRMATION_U_CALCULATION', 'CONFIRMATION_U_CONCLUSION'} <= codes)
        a['outcome'] = rule['state'] = 'insufficient_evidence'
        self.assertEqual(uncertainty_issues(r), [])

    def test_separate_evaluation_does_not_override_selected_simple_acceptance(self):
        r = record(); rule = r['technical_review']['rules'][0]; a = rule['reasoning']['uncertainty_review']
        rule['decision_mode'] = 'direct_comparison'; rule['reasoning']['uncertainty_role'] = 'separate_evaluation'
        a.update(application='supplementary', outcome='insufficient_evidence', points=[point('0.9')])
        self.assertEqual(uncertainty_issues(r), [])
        a['application'] = 'decision'
        self.assertIn('CONFIRMATION_U_CONCLUSION', {x['code'] for x in uncertainty_issues(r)})

    def test_each_use_is_evaluated_against_its_own_criterion(self):
        r = record(); first = r['technical_review']['rules'][0]
        first['reasoning']['uncertainty_review']['points'] = [point('3', '5', '10')]
        second = copy.deepcopy(first); second['decision_group'] = 'TEST tighter use'
        second['reasoning']['uncertainty_review']['points'] = [point('3', '5', '4.3')]
        r['technical_review']['rules'].append(second)
        self.assertEqual(uncertainty_issues(r)[0]['rule_number'], 2)
        second['state'] = second['reasoning']['uncertainty_review']['outcome'] = 'insufficient_evidence'
        self.assertEqual(uncertainty_issues(r), [])

    def test_different_point_limits_are_paired_not_combined_into_a_false_worst_case(self):
        r = record(); a = r['technical_review']['rules'][0]['reasoning']['uncertainty_review']
        a['points'] = [point('0.05', '0.04', '.1', 'TEST A'), point('0.2', '0.4', '1', 'TEST B')]
        self.assertEqual(uncertainty_issues(r), [])

    def test_resolution_sd_and_unverified_coverage_do_not_enter_jjf_algorithm(self):
        r = record(); rule = r['technical_review']['rules'][0]
        for changes in [dict(requirement_kind='resolution'), dict(compared_quantity='repeatability_sd'),
                        dict(requirement_interpretation='recording_precision')]:
            bad = copy.deepcopy(r); bad['technical_review']['rules'][0].update(changes)
            self.assertIn('CONFIRMATION_U_QUANTITY', {x['code'] for x in uncertainty_issues(bad)})
        rule['reasoning']['uncertainty_review'].pop('coverage_basis')
        self.assertIn('CONFIRMATION_U_COVERAGE', {x['code'] for x in uncertainty_issues(r)})

    def test_neutral_u_evaluation_records_why_no_numeric_comparison_is_made(self):
        r = record(); a = r['technical_review']['rules'][0]['reasoning']['uncertainty_review']
        a.update(route='qualitative', application='supplementary', outcome='reported_characteristic', points=[])
        self.assertEqual(uncertainty_issues(r), [])
        a['points'] = [point()]
        self.assertIn('CONFIRMATION_U_MANUAL_ROUTE', {x['code'] for x in uncertainty_issues(r)})

    def test_schema_preserves_decimal_text_and_disallows_undocumented_extra_fields(self):
        from test_confirmation_reasoning import fixture
        from test_document_templates import publication_review
        r = fixture(); r.update(author='TEST', scope='TEST', evidence_references=['TEST'])
        r['rows'] = [dict(function='TEST', purpose='TEST', state='meets_requirement', parameter_results='TEST',
                          method_judgment='TEST', uncertainty_evaluation='TEST', traceability='TEST', application='TEST')]
        r['publication_review'] = publication_review(r)
        r['technical_review'].update(version=2, use_evidence='TEST', decision_evidence='TEST')
        a = record()['technical_review']['rules'][0]['reasoning']['uncertainty_review']
        r['technical_review']['rules'][0].update(uncertainty_quote='TEST', comparison_quote='TEST')
        r['technical_review']['rules'][0]['reasoning']['uncertainty_review'] = a
        validate_contract('confirmation-record.v1', r)
        a['points'][0]['error'] = .2
        with self.assertRaises(LabcalError): validate_contract('confirmation-record.v1', r)
