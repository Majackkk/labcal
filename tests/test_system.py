"""Behavioral checks use only temporary, explicitly simulated laboratories."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from labcal.adapters.storage import Workspace
from labcal.adapters.legacy import candidate_from_markdown, compare_parameter_documents
from labcal.adapters.export import export_plan
from labcal.application.laboratories import create_laboratory, add_equipment, get_equipment, update_equipment
from labcal.application.sources import import_source, verify_source_integrity, source_gaps
from labcal.application.plans import (create_plan, get_plan, assess_plan, confirmation_template, confirm_plan,
                                     verify_evidence, compile_baseline, get_baseline, diff_plans)
from labcal.application.maintenance import doctor, backup_laboratory, restore_laboratory
from labcal.domain.rules import convert, evaluate_rule, number
from labcal.errors import LabcalError
from labcal.util import content_hash, utc_now


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root / "workspace")
        self.workspace.initialize()
        create_laboratory(self.workspace, "LAB_A", "模拟材料实验室", test_only=True)
        create_laboratory(self.workspace, "LAB_B", "模拟分析实验室", test_only=True)
        self.equipment = add_equipment(self.workspace, "LAB_A", "EQ-001", "模拟测力系统", configuration={"sensor": "sensor-a"})
        self.source_file = self.root / "requirement.md"
        self.source_file.write_text("模拟实验室要求：范围0至100N，点位20、50、100N。此文件只用于测试。\n", encoding="utf-8")
        self.source = import_source(self.workspace, self.source_file, kind="user_plan", lab_id="LAB_A")

    def tearDown(self):
        self.temp.cleanup()

    def requirement(self, name="R1", *, route="external_calibration", claim="lab_requirement", with_rule=True):
        result = {"id": name, "parameter": "试验力示值误差", "measurand": "force", "unit": "N",
                  "delivery_route": route, "coverage_policy": "explicit_points", "calibration_range": {"minimum": "0", "maximum": "100"},
                  "points": ["20", "50", "100"], "report_fields": ["indication_error", "uncertainty"],
                  "evidence": [{"source_id": self.source["id"], "claim_kind": claim, "anchor": "line:1"}]}
        if with_rule:
            result.update({"limit": {"lower": "-1", "upper": "1", "unit": "N"},
                           "decision_rule": {"type": "interval", "input": "error"}})
        return result

    def payload(self, requirements=None):
        return {"equipment_id": self.equipment["id"], "title": "模拟校准方案", "origin": "user_plan",
                "requirements": requirements or [self.requirement()], "source_ids": [self.source["id"]]}

    def make_plan(self, requirements=None):
        return create_plan(self.workspace, "LAB_A", self.payload(requirements))

    def simulated_confirmation(self, plan):
        record = confirmation_template(self.workspace, "LAB_A", plan["plan_id"], plan["revision"])
        record.update({"confirmed_by": "TEST_ONLY_OPERATOR", "confirmed_at": utc_now(),
                       "evidence_kind": "operator_command", "user_statement": "仅确认隔离测试数据，不构成真实业务批准。"})
        return record

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(LabcalError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_same_asset_code_is_isolated_between_laboratories(self):
        other = add_equipment(self.workspace, "LAB_B", "EQ-001", "模拟天平", model="ANALYTICAL")
        self.assertNotEqual(other["id"], self.equipment["id"])
        self.assert_error("EQUIPMENT_NOT_FOUND", get_equipment, self.workspace, "LAB_B", self.equipment["id"])

    def test_sources_do_not_cross_laboratories(self):
        self.assert_error("SOURCE_NOT_FOUND", verify_source_integrity, self.workspace, "LAB_B", self.source["id"])

    def test_source_object_path_cannot_escape_lab_storage(self):
        with self.workspace.lab_store("LAB_A").connect() as db:
            db.execute("UPDATE sources SET object_path='../../outside.md' WHERE id=?", (self.source["id"],))
        self.assert_error("PATH_OUTSIDE_SCOPE", verify_source_integrity, self.workspace, "LAB_A", self.source["id"])

    def test_lab_path_traversal_is_rejected(self):
        self.assert_error("INVALID_LAB_ID", create_laboratory, self.workspace, "../LAB_A", "invalid")

    def test_private_source_cannot_enter_shared_catalogue(self):
        self.assert_error("SHARING_SCOPE_REQUIRED", import_source, self.workspace, self.source_file, kind="user_plan", shareable=True)

    def test_source_import_is_idempotent_and_remains_pending(self):
        second = import_source(self.workspace, self.source_file, kind="user_plan", lab_id="LAB_A")
        self.assertTrue(second["reused"])
        self.assertEqual(second["id"], self.source["id"])
        self.assertEqual(verify_source_integrity(self.workspace, "LAB_A", self.source["id"])["metadata"]["technical_review"], "pending")

    def test_legacy_code_without_year_is_available_but_pending(self):
        import_source(self.workspace, self.source_file, kind="cnas_document", name="CNAS-CL01-G002_2021.md", code="CNAS-CL01-G002", shareable=True)
        gap = source_gaps(self.workspace, "LAB_A", ["CNAS-CL01-G002:2021"])[0]
        self.assertEqual(gap["status"], "available_pending_review")

    def test_unconfirmed_plan_cannot_compile_baseline(self):
        plan = self.make_plan()
        self.assertTrue(assess_plan(self.workspace, "LAB_A", plan["plan_id"])["coverage_ready"])
        self.assert_error("BASELINE_NOT_READY", compile_baseline, self.workspace, "LAB_A", plan["plan_id"])

    def test_unknown_approval_field_is_not_accepted(self):
        payload = self.payload(); payload["approved"] = True
        self.assert_error("CONTRACT_INVALID", create_plan, self.workspace, "LAB_A", payload)

    def test_cross_lab_plan_payload_is_rejected(self):
        payload = self.payload(); payload["lab_id"] = "LAB_B"
        self.assert_error("LAB_SCOPE_MISMATCH", create_plan, self.workspace, "LAB_A", payload)

    def test_duplicate_requirement_ids_are_rejected(self):
        self.assert_error("DUPLICATE_REQUIREMENT_ID", create_plan, self.workspace, "LAB_A", self.payload([self.requirement(), self.requirement()]))

    def test_point_outside_range_prevents_confirmation(self):
        requirement = self.requirement(); requirement["points"] = ["200"]
        plan = self.make_plan([requirement])
        self.assertIn("POINT_OUTSIDE_RANGE", assess_plan(self.workspace, "LAB_A", plan["plan_id"])["requirements"][0]["coverage_issues"])
        self.assert_error("PLAN_NOT_READY_FOR_CONFIRMATION", confirm_plan, self.workspace, "LAB_A", self.simulated_confirmation(plan))

    def test_range_with_different_equivalent_unit_is_normalized(self):
        requirement = self.requirement()
        requirement["calibration_range"] = {"minimum": "0", "maximum": "0.1", "unit": "kN"}
        plan = self.make_plan([requirement])
        self.assertTrue(assess_plan(self.workspace, "LAB_A", plan["plan_id"])["coverage_ready"])

    def test_uncertainty_requirement_with_wrong_dimension_is_not_decision_ready(self):
        requirement = self.requirement(); requirement["uncertainty"] = {"maximum": "0.1", "unit": "mm"}
        plan = self.make_plan([requirement])
        self.assertFalse(assess_plan(self.workspace, "LAB_A", plan["plan_id"])["decision_ready"])

    def test_relative_uncertainty_requires_explicit_basis(self):
        requirement = self.requirement(); requirement["uncertainty"] = {"maximum": "0.1", "unit": "%"}
        plan = self.make_plan([requirement])
        self.assertFalse(assess_plan(self.workspace, "LAB_A", plan["plan_id"])["decision_ready"])

    def test_confirmed_coverage_can_exist_without_automatic_decision(self):
        plan = self.make_plan([self.requirement(with_rule=False)])
        confirm_plan(self.workspace, "LAB_A", self.simulated_confirmation(plan))
        baseline = compile_baseline(self.workspace, "LAB_A", plan["plan_id"])
        self.assertTrue(baseline["payload"]["readiness"]["coverage_ready"])
        self.assertFalse(baseline["payload"]["readiness"]["decision_ready"])

    def test_internal_checks_are_not_in_external_certificate_group(self):
        plan = self.make_plan([self.requirement("EXTERNAL"), self.requirement("INTERNAL", route="pre_use_check")])
        confirm_plan(self.workspace, "LAB_A", self.simulated_confirmation(plan))
        baseline = compile_baseline(self.workspace, "LAB_A", plan["plan_id"])
        self.assertEqual(baseline["payload"]["external_certificate_groups"], {"default": ["EXTERNAL"]})
        self.assertEqual(len(baseline["payload"]["requirements"]), 2)

    def test_confirmation_hash_must_match_exact_revision(self):
        plan = self.make_plan(); record = self.simulated_confirmation(plan); record["payload_hash"] = "wrong"
        self.assert_error("CONFIRMATION_VERSION_CONFLICT", confirm_plan, self.workspace, "LAB_A", record)

    def test_empty_user_confirmation_is_rejected(self):
        plan = self.make_plan(); record = confirmation_template(self.workspace, "LAB_A", plan["plan_id"])
        self.assert_error("CONTRACT_INVALID", confirm_plan, self.workspace, "LAB_A", record)

    def test_confirmation_and_baseline_are_idempotent(self):
        plan = self.make_plan(); record = self.simulated_confirmation(plan)
        first = confirm_plan(self.workspace, "LAB_A", record)
        second = confirm_plan(self.workspace, "LAB_A", record)
        self.assertEqual(first["confirmation_id"], second["confirmation_id"])
        a = compile_baseline(self.workspace, "LAB_A", plan["plan_id"])
        b = compile_baseline(self.workspace, "LAB_A", plan["plan_id"])
        self.assertEqual(a["payload_hash"], b["payload_hash"])
        with self.workspace.lab_store("LAB_A").connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0], 1)

    def test_new_revision_does_not_change_historical_baseline(self):
        first = self.make_plan(); confirm_plan(self.workspace, "LAB_A", self.simulated_confirmation(first))
        baseline = compile_baseline(self.workspace, "LAB_A", first["plan_id"])
        payload = get_plan(self.workspace, "LAB_A", first["plan_id"])["payload"]
        payload["requirements"][0]["points"] = ["10", "20", "50", "100"]
        second = create_plan(self.workspace, "LAB_A", payload, plan_id=first["plan_id"])
        self.assertEqual(second["revision"], 2)
        confirm_plan(self.workspace, "LAB_A", self.simulated_confirmation(second))
        self.assertEqual(get_baseline(self.workspace, "LAB_A", baseline["baseline_id"])["payload_hash"], baseline["payload_hash"])
        self.assertFalse(assess_plan(self.workspace, "LAB_A", first["plan_id"], 1)["automatic_baseline_available"])
        self.assertEqual(diff_plans(self.workspace, "LAB_A", first["plan_id"], 1, 2)["changed"], ["R1"])

    def test_reverting_plan_content_creates_new_revision(self):
        first = self.make_plan(); old = get_plan(self.workspace, "LAB_A", first["plan_id"])["payload"]
        changed = copy.deepcopy(old); changed["requirements"][0]["points"] = ["50", "100"]
        create_plan(self.workspace, "LAB_A", changed, plan_id=first["plan_id"])
        third = create_plan(self.workspace, "LAB_A", old, plan_id=first["plan_id"])
        self.assertEqual(third["revision"], 3)

    def test_configuration_change_invalidates_current_baseline_use(self):
        plan = self.make_plan(); confirm_plan(self.workspace, "LAB_A", self.simulated_confirmation(plan))
        equipment = get_equipment(self.workspace, "LAB_A", self.equipment["id"])
        update_equipment(self.workspace, "LAB_A", self.equipment["id"], {"expected_config_hash": equipment["config_hash"], "configuration": {"sensor": "sensor-b"}})
        state = assess_plan(self.workspace, "LAB_A", plan["plan_id"])
        self.assertFalse(state["configuration_current"])
        self.assert_error("BASELINE_NOT_READY", compile_baseline, self.workspace, "LAB_A", plan["plan_id"])

    def test_noop_configuration_update_does_not_invalidate_confirmation(self):
        plan = self.make_plan(); confirm_plan(self.workspace, "LAB_A", self.simulated_confirmation(plan))
        equipment = get_equipment(self.workspace, "LAB_A", self.equipment["id"])
        update_equipment(self.workspace, "LAB_A", self.equipment["id"], {"expected_config_hash": equipment["config_hash"], "configuration": equipment["configuration"]})
        self.assertTrue(assess_plan(self.workspace, "LAB_A", plan["plan_id"])["automatic_baseline_available"])

    def test_modified_source_bytes_prevent_automatic_use(self):
        plan = self.make_plan()
        source = verify_source_integrity(self.workspace, "LAB_A", self.source["id"])
        Path(source["absolute_path"]).write_text("changed", encoding="utf-8")
        self.assertFalse(assess_plan(self.workspace, "LAB_A", plan["plan_id"])["coverage_ready"])
        self.assertEqual(doctor(self.workspace, "LAB_A")["status"], "errors")

    def test_normative_claim_needs_parameter_review(self):
        original = self.root / "simulated-regulation.md"
        original.write_text("隔离测试的模拟规范文件，不是真实规范原件。", encoding="utf-8")
        normative_source = import_source(self.workspace, original, kind="metrology_regulation", lab_id="LAB_A")
        requirement = self.requirement(claim="normative")
        requirement["evidence"][0]["source_id"] = normative_source["id"]
        requirement["evidence"][0]["fields"] = ["parameter", "unit", "limit"]
        plan = self.make_plan([requirement])
        self.assertFalse(assess_plan(self.workspace, "LAB_A", plan["plan_id"])["coverage_ready"])
        record = {"requirement_id": "R1", "source_id": normative_source["id"], "reviewer": "TEST_ONLY_REVIEWER",
                  "source_quote": "模拟规范只用于测试。", "checked_fields": ["parameter", "unit", "limit"],
                  "applicability_note": "隔离测试，不构成真实审核。", "expected_plan_hash": plan["payload_hash"],
                  "source_hash": normative_source["sha256"], "reviewed_at": utc_now(), "anchor": "line:1", "page": None,
                  "version_checked": True, "applicability_checked": True}
        verify_evidence(self.workspace, "LAB_A", plan["plan_id"], 1, record)
        self.assertTrue(assess_plan(self.workspace, "LAB_A", plan["plan_id"])["coverage_ready"])

    def test_user_plan_cannot_be_presented_as_normative_original(self):
        plan = self.make_plan([self.requirement(claim="normative")])
        issues = assess_plan(self.workspace, "LAB_A", plan["plan_id"])["requirements"][0]["coverage_issues"]
        self.assertIn("SOURCE_ROLE_MISMATCH", issues)

    def test_unimplemented_laboratory_profile_does_not_claim_decision_readiness(self):
        create_laboratory(self.workspace, "LAB_MED", "模拟其他体系", laboratory_type="medical", profile="cnas_cl02", test_only=True)
        equipment = add_equipment(self.workspace, "LAB_MED", "EQ-001", "模拟设备")
        source = import_source(self.workspace, self.source_file, kind="user_plan", lab_id="LAB_MED")
        requirement = self.requirement(); requirement["evidence"][0]["source_id"] = source["id"]
        plan = create_plan(self.workspace, "LAB_MED", {"title": "模拟其他体系方案", "equipment_id": equipment["id"], "requirements": [requirement], "source_ids": [source["id"]]})
        state = assess_plan(self.workspace, "LAB_MED", plan["plan_id"])
        self.assertTrue(state["coverage_ready"])
        self.assertFalse(state["decision_ready"])
        self.assertFalse(state["profile_supported"])

    def test_decision_only_open_item_preserves_coverage(self):
        payload = self.payload()
        payload["open_items"] = [{"code": "UNCERTAINTY_POLICY_PENDING", "affects": ["decision"], "requirement_ids": ["R1"]}]
        plan = create_plan(self.workspace, "LAB_A", payload)
        state = assess_plan(self.workspace, "LAB_A", plan["plan_id"])
        self.assertTrue(state["coverage_ready"])
        self.assertFalse(state["decision_ready"])

    def test_unresolved_configuration_conflict_blocks_confirmation(self):
        payload = self.payload()
        payload["open_items"] = [{"code": "COMPONENT_CONFLICT", "affects": ["coverage"]}]
        plan = create_plan(self.workspace, "LAB_A", payload)
        self.assert_error("PLAN_NOT_READY_FOR_CONFIRMATION", confirm_plan, self.workspace, "LAB_A", self.simulated_confirmation(plan))

    def test_evidence_review_without_version_check_remains_unusable(self):
        plan = self.make_plan()
        record = {"requirement_id": "R1", "source_id": self.source["id"], "reviewer": "TEST_ONLY_REVIEWER", "source_quote": "测试原文", "checked_fields": ["unit"], "applicability_note": "测试", "expected_plan_hash": plan["payload_hash"], "source_hash": self.source["sha256"], "reviewed_at": utc_now()}
        self.assert_error("EVIDENCE_REVIEW_INCOMPLETE", verify_evidence, self.workspace, "LAB_A", plan["plan_id"], 1, record)

    def test_legacy_import_preserves_values_without_approving_them(self):
        text = "| 序号 | 测量系统类别 | 校准点 | MPE |\n| --- | --- | --- | --- |\n| 01 | 试验力 (30kN) | 1,2 | ±1% |\n"
        payload = candidate_from_markdown(text, title="旧方案", equipment_id=self.equipment["id"], source_id=self.source["id"])
        plan = create_plan(self.workspace, "LAB_A", payload)
        state = assess_plan(self.workspace, "LAB_A", plan["plan_id"])
        self.assertFalse(state["coverage_ready"])
        self.assertEqual(get_plan(self.workspace, "LAB_A", plan["plan_id"])["payload"]["requirements"][0]["raw"]["columns"]["MPE"], "±1%")
        changed = text.replace("01 | 试验力 (30kN)", "01A | 试验力 (500N)")
        self.assertEqual(compare_parameter_documents(text, changed)[0]["code"], "LEGACY_PARAMETER_MISMATCH")

    def test_exports_derive_from_same_requirements(self):
        plan = self.make_plan(); output = export_plan(self.workspace, "LAB_A", plan["plan_id"])
        directory = Path(output["directory"])
        stored = json.loads((directory / "plan.json").read_text())
        self.assertEqual(stored["requirements"], get_plan(self.workspace, "LAB_A", plan["plan_id"])["payload"]["requirements"])
        self.assertIn("候选稿，未确认", (directory / "calibration-plan.md").read_text())
        self.assertIn("未执行", (directory / "confirmation-record.md").read_text())

    def test_future_modules_are_disabled(self):
        lab = next(x for x in self.workspace.labs() if x["id"] == "LAB_A")
        self.assertTrue(lab["features"]["certificate_acceptance"])
        self.assertTrue(lab["features"]["pdf_annotations"])
        for key in ["interval_management", "historical_trend", "abnormal_impact"]:
            self.assertFalse(lab["features"][key])

    def test_different_laboratory_balance_workflow(self):
        equipment = add_equipment(self.workspace, "LAB_B", "EQ-001", "模拟分析天平", configuration={"maximum_g": "200"})
        original = self.root / "balance-policy.md"
        original.write_text("模拟称量要求，仅用于测试。", encoding="utf-8")
        source = import_source(self.workspace, original, kind="user_plan", lab_id="LAB_B")
        requirement = {"id": "MASS", "parameter": "称量示值误差", "unit": "g", "delivery_route": "external_calibration",
                       "coverage_policy": "explicit_points", "points": ["10", "100", "200"],
                       "calibration_range": {"minimum": "0", "maximum": "200"}, "report_fields": ["indication_error", "uncertainty"],
                       "limit": {"lower": "-0.0002", "upper": "0.0002", "unit": "g"},
                       "decision_rule": {"type": "interval", "input": "error"},
                       "evidence": [{"source_id": source["id"], "claim_kind": "lab_requirement"}]}
        plan = create_plan(self.workspace, "LAB_B", {"equipment_id": equipment["id"], "title": "模拟称量方案", "origin": "user_plan", "source_ids": [source["id"]], "requirements": [requirement]})
        record = confirmation_template(self.workspace, "LAB_B", plan["plan_id"])
        record.update({"confirmed_by": "TEST_ONLY_OPERATOR", "confirmed_at": utc_now(), "user_statement": "仅确认模拟称量测试数据。", "evidence_kind": "operator_command"})
        confirm_plan(self.workspace, "LAB_B", record)
        baseline = compile_baseline(self.workspace, "LAB_B", plan["plan_id"])
        self.assertTrue(baseline["payload"]["readiness"]["decision_ready"])
        self.assertTrue(evaluate_rule(requirement, {"error": {"value": "0.1", "unit": "mg"}})["conforms"])
        self.assert_error("BASELINE_NOT_FOUND", get_baseline, self.workspace, "LAB_A", baseline["baseline_id"])

    def test_backup_restores_history_and_originals(self):
        plan = self.make_plan(); confirm_plan(self.workspace, "LAB_A", self.simulated_confirmation(plan))
        baseline = compile_baseline(self.workspace, "LAB_A", plan["plan_id"])
        archive = self.root / "backup.zip"
        backup_laboratory(self.workspace, "LAB_A", archive)
        restored = Workspace(self.root / "restored")
        result = restore_laboratory(restored, archive)
        self.assertEqual(result["integrity"]["status"], "structural_checks_passed")
        self.assertEqual(get_baseline(restored, "LAB_A", baseline["baseline_id"])["payload_hash"], baseline["payload_hash"])
        verify_source_integrity(restored, "LAB_A", self.source["id"])

    def test_restore_rejects_path_traversal(self):
        archive = self.root / "malicious.zip"
        with zipfile.ZipFile(archive, "w") as file:
            file.writestr("../outside.txt", "invalid")
        self.assert_error("BACKUP_PATH_INVALID", restore_laboratory, Workspace(self.root / "invalid-restore"), archive)


class MeasurementRulesTests(unittest.TestCase):
    def test_exact_decimal_unit_conversion(self):
        self.assertEqual(str(convert("0.1", "MPa", "kPa")), "100.0")
        self.assertEqual(convert("1", "kN", "N"), number("1000"))

    def test_incompatible_quantities_are_rejected(self):
        with self.assertRaises(LabcalError): convert("1", "N", "mm")

    def test_float_and_nonfinite_input_are_rejected(self):
        for value in [0.1, "NaN", "Infinity", True]:
            with self.assertRaises(LabcalError): number(value)

    def test_guard_band_is_explicit_and_uses_matching_units(self):
        requirement = {"limit": {"lower": "-1", "upper": "1", "unit": "N"}, "decision_rule": {"type": "guard_band", "input": "error", "uncertainty_input": "U"}}
        result = evaluate_rule(requirement, {"error": {"value": "0.8", "unit": "N"}, "U": {"value": "0.0003", "unit": "kN"}})
        self.assertFalse(result["conforms"])

    def test_percent_basis_cannot_be_silently_changed(self):
        requirement = {"relative_basis": "reading", "limit": {"lower": "-1", "upper": "1", "unit": "%"}, "decision_rule": {"type": "interval", "input": "error"}}
        with self.assertRaises(LabcalError): evaluate_rule(requirement, {"error": {"value": "0.5", "unit": "%", "relative_basis": "full_scale"}})

    def test_exclusive_limit_does_not_accept_boundary(self):
        requirement = {"limit": {"upper": "1", "unit": "N", "upper_inclusive": False}, "decision_rule": {"type": "upper", "input": "value"}}
        self.assertFalse(evaluate_rule(requirement, {"value": {"value": "1", "unit": "N"}})["conforms"])

    def test_unimplemented_rule_metadata_does_not_get_ignored(self):
        requirement = {"limit": {"upper": "1", "unit": "N"}, "decision_rule": {"type": "upper", "input": "value", "custom_formula": "x+1"}}
        with self.assertRaises(LabcalError): evaluate_rule(requirement, {"value": {"value": "0", "unit": "N"}})


if __name__ == "__main__":
    unittest.main()

