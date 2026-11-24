import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1] / "labcal"


class CliTests(unittest.TestCase):
    def test_import_confirm_baseline_export_through_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def run(*args, expected=0):
                result = subprocess.run([sys.executable, str(APP), "--workspace", str(root), *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                return json.loads(result.stdout)
            run("init")
            run("lab", "add", "--id", "LAB_CLI", "--name", "隔离命令测试", "--test-only")
            equipment = run("equipment", "add", "--lab", "LAB_CLI", "--asset-code", "EQ-1", "--name", "模拟温度设备")["result"]
            policy = root / "policy.md"; policy.write_text("仅供隔离测试的温度要求。")
            source = run("source", "add", "--lab", "LAB_CLI", "--file", str(policy), "--kind", "user_plan")["result"]
            payload = {"equipment_id": equipment["id"], "title": "模拟温度方案", "requirements": [{"id": "T1", "parameter": "温度偏差", "unit": "℃", "delivery_route": "external_calibration", "coverage_policy": "explicit_points", "points": ["100"], "report_fields": ["indication_error"], "evidence": [{"source_id": source["id"], "claim_kind": "lab_requirement"}]}]}
            path = root / "input.json"; path.write_text(json.dumps(payload, ensure_ascii=False))
            plan = run("plan", "import", "--lab", "LAB_CLI", "--file", str(path))["result"]
            denied = run("baseline", "build", "--lab", "LAB_CLI", "--id", plan["plan_id"], expected=2)
            self.assertEqual(denied["error"]["code"], "BASELINE_NOT_READY")
            confirmation = root / "confirmation.json"
            run("plan", "confirmation-template", "--lab", "LAB_CLI", "--id", plan["plan_id"], "--output", str(confirmation))
            record = json.loads(confirmation.read_text())
            record.update({"confirmed_by": "TEST_ONLY_OPERATOR", "confirmed_at": "2026-10-14T10:00:00+08:00", "user_statement": "本记录仅用于命令行集成测试。", "evidence_kind": "operator_command"})
            confirmation.write_text(json.dumps(record, ensure_ascii=False))
            run("plan", "confirm", "--lab", "LAB_CLI", "--record", str(confirmation))
            baseline = run("baseline", "build", "--lab", "LAB_CLI", "--id", plan["plan_id"])["result"]
            self.assertFalse(baseline["payload"]["readiness"]["decision_ready"])
            output = run("plan", "export", "--lab", "LAB_CLI", "--id", plan["plan_id"])["result"]
            self.assertTrue((Path(output["directory"]) / "calibration-plan.html").is_file())
            self.assertEqual(run("doctor", "--lab", "LAB_CLI")["result"]["status"], "structural_checks_passed")


if __name__ == "__main__":
    unittest.main()

