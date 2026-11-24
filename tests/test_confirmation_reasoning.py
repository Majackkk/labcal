"""Regression scenarios for metrology reasoning; all data are synthetic."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from labcal.domain.confirmation_reasoning import reasoning_issues


def fixture():
    # Under a chosen direct-comparison rule, crossing a guard band is not a failure.
    rule = dict(row_number=1, dimension='intended_use', state='meets_requirement',
                source_reference='TEST method clause 1', requirement_quote='TEST error limit ±1 unit',
                requirement_kind='maximum_error', compared_quantity='indication_error',
                decision_mode='direct_comparison', decision_group='TEST USE',
                limit_basis='method_requirement', judgment_quote='TEST conforms',
                requirement_interpretation='measurement_accuracy',
                reasoning=dict(requirement_status='established', interpretation_status='unambiguous',
                    interpretation_evidence='TEST explicit signed indication-error limit',
                    quantity_match='direct', quantity_basis='TEST E=0.8 unit versus ±1 unit, same measurand',
                    decision_evidence='TEST user chose direct comparison, U=0.4 unit evaluated separately',
                    uncertainty_role='separate_evaluation'))
    return dict(rows=[dict(application='TEST USE may be used without correction')],
                technical_review=dict(rules=[rule]),
                publication_review=dict(selections=dict(result=dict(choice='meets_declared_uses'))))


class ReasoningTests(unittest.TestCase):
    def codes(self, record):
        return {x['code'] for x in reasoning_issues(record)}

    def test_direct_comparison_does_not_silently_become_guard_band(self):
        record = fixture()
        self.assertEqual(reasoning_issues(record), [])
        record['technical_review']['rules'][0]['reasoning']['uncertainty_role'] = 'decision_component'
        self.assertIn('CONFIRMATION_UNCERTAINTY_ROLE', self.codes(record))

    def test_guard_band_needs_uncertainty_in_decision(self):
        record = fixture(); rule = record['technical_review']['rules'][0]
        rule['decision_mode'] = 'guard_band'
        self.assertIn('CONFIRMATION_UNCERTAINTY_ROLE', self.codes(record))

    def test_ambiguous_precision_cannot_be_finalized_even_as_limited_use(self):
        record = fixture(); rule = record['technical_review']['rules'][0]
        rule.update(requirement_kind='weighing_precision', state='insufficient_evidence')
        rule['reasoning'].update(requirement_status='unresolved', interpretation_status='unresolved')
        record['publication_review']['selections']['result']['choice'] = 'limited_use'
        self.assertTrue({'CONFIRMATION_BASIS_UNRESOLVED', 'CONFIRMATION_RESTRICTION_REQUIRED'} <= self.codes(record))

    def test_precision_word_needs_a_resolved_interpretation(self):
        record = fixture(); record['technical_review']['rules'][0]['requirement_kind'] = 'weighing_precision'
        self.assertIn('CONFIRMATION_TERM_INTERPRETATION', self.codes(record))

    def test_incomparable_quantities_do_not_produce_pass_or_fail(self):
        for state in ['meets_requirement', 'does_not_meet']:
            record = fixture(); rule = record['technical_review']['rules'][0]
            rule['state'] = state; rule['reasoning']['quantity_match'] = 'not_comparable'
            self.assertIn('CONFIRMATION_REASONING_CONCLUSION', self.codes(record))

    def test_missing_data_is_undetermined_not_a_use_restriction(self):
        record = fixture(); record['technical_review']['rules'][0]['state'] = 'insufficient_evidence'
        record['publication_review']['selections']['result']['choice'] = 'undetermined'
        self.assertEqual(reasoning_issues(record), [])
        for choice in ['limited_use', 'does_not_meet', 'meets_declared_uses']:
            record['publication_review']['selections']['result']['choice'] = choice
            self.assertTrue(reasoning_issues(record))

    def test_instrument_failure_does_not_automatically_prove_use_failure(self):
        record = fixture(); rule = record['technical_review']['rules'][0]
        rule.update(dimension='instrument_requirement', state='does_not_meet')
        record['publication_review']['selections']['result']['choice'] = 'does_not_meet'
        self.assertIn('CONFIRMATION_USE_CONCLUSION', self.codes(record))

    def test_neutral_characteristic_is_neither_pass_nor_missing_evidence(self):
        record = fixture(); rule = record['technical_review']['rules'][0]
        rule.update(state='reported_characteristic', decision_mode='report_only',
                    requirement_kind='characteristic', compared_quantity='repeatability_sd',
                    limit_basis='no_independent_limit')
        rule['reasoning'].update(requirement_status='no_independent_limit', uncertainty_role='not_applicable')
        record['publication_review']['selections']['result']['choice'] = 'undetermined'
        self.assertEqual(reasoning_issues(record), [])
        rule['state'] = 'insufficient_evidence'
        self.assertIn('CONFIRMATION_NEUTRAL_CHARACTERISTIC', self.codes(record))

    def test_restriction_requires_failed_use_and_visible_operating_boundary(self):
        record = fixture(); rules = record['technical_review']['rules']
        record['rows'].append(dict(application='TEST USE B must not be used above 10 units'))
        rules.append(copy.deepcopy(rules[0]))
        rules[1].update(row_number=2, state='does_not_meet', decision_group='TEST USE B')
        record['publication_review']['selections']['result']['choice'] = 'limited_use'
        restriction = dict(rule_numbers=[2], scope='TEST USE B above 10 units', reason='TEST measured error exceeds method limit',
                           application_quotes=['TEST USE B must not be used above 10 units'])
        record['technical_review']['use_restrictions'] = [restriction]
        self.assertEqual(reasoning_issues(record), [])
        rules[1]['state'] = 'insufficient_evidence'
        self.assertIn('CONFIRMATION_RESTRICTION_LINK', self.codes(record))
        rules[1]['state'] = 'does_not_meet'; restriction['application_quotes'] = ['not in form']
        self.assertIn('CONFIRMATION_RESTRICTION_TEXT', self.codes(record))

    def test_same_point_group_cannot_switch_quantity_or_u_treatment(self):
        record = fixture(); rules = record['technical_review']['rules']; rules.append(copy.deepcopy(rules[0]))
        rules[1]['compared_quantity'] = 'resolution'
        self.assertIn('CONFIRMATION_REASONING_GROUP', self.codes(record))

    def test_same_clause_cannot_alternate_between_accuracy_and_resolution(self):
        record = fixture(); rules = record['technical_review']['rules']; rules.append(copy.deepcopy(rules[0]))
        rules[1]['requirement_interpretation'] = 'division_or_resolution'
        self.assertIn('CONFIRMATION_INTERPRETATION_CONFLICT', self.codes(record))

    def test_legacy_record_is_readable_but_not_automatically_ready_for_reissue(self):
        record = fixture(); record['technical_review']['rules'][0].pop('reasoning')
        self.assertIn('CONFIRMATION_REASONING_REQUIRED', self.codes(record))
