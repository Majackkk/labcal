from .layout import output_directory, processing_directory
import html
import json
from pathlib import Path
from string import Template
import sys

from .. import __version__
from ..application.plans import get_plan, assess_plan
from ..errors import LabcalError
from ..util import canonical_json, content_hash, file_hash, identifier, utc_now, write_json

ROUTE_LABELS = {"external_calibration": "委外校准", "legal_verification": "检定", "internal_calibration": "内部校准", "functional_check": "功能核查", "pre_use_check": "试验前检查", "unresolved": "待明确"}
REPORT_LABELS = {"measured_value": "实测值", "indication_error": "示值误差", "repeatability": "重复性", "uncertainty": "测量不确定度", "coverage_factor": "包含因子", "coverage_probability": "包含概率", "qualitative_result": "定性结果"}
RULE_LABELS = {"interval": "区间比较", "upper": "上限比较", "lower": "下限比较", "guard_band": "明确保护带规则", "qualitative": "定性比较"}
RENDER_REVISION = "document-layout-1.1-swiss"


def raw_field(requirement, words):
    columns = requirement.get("raw", {}).get("columns", {})
    for key, value in columns.items():
        if any(word in key for word in words):
            return "原文待核验：" + str(value).replace("<br>", "；").replace("<br/>", "；")
    return "待明确"


def scope_text(requirement):
    interval = requirement.get("calibration_range")
    parts = []
    if interval:
        parts.append(f"范围：{interval.get('minimum', '?')}～{interval.get('maximum', '?')} {interval.get('unit', requirement['unit'])}")
    if requirement["points"]:
        parts.append("点位：" + "、".join(requirement["points"]) + " " + requirement["unit"])
    if requirement.get("conditions"):
        parts.append("条件：" + cell(requirement["conditions"]))
    return "；".join(parts) if parts else raw_field(requirement, ["校准点", "参数条件", "范围/点"])


def limit_text(requirement):
    limit = requirement.get("limit")
    if not limit:
        return raw_field(requirement, ["允许误差", "MPE"])
    if "expected" in limit:
        return "要求：" + str(limit["expected"])
    parts = []
    if "lower" in limit:
        parts.append(("≥" if limit.get("lower_inclusive", True) else ">") + str(limit["lower"]))
    if "upper" in limit:
        parts.append(("≤" if limit.get("upper_inclusive", True) else "<") + str(limit["upper"]))
    return "，".join(parts) + " " + limit.get("unit", "")


def display_values(requirement):
    rule = requirement.get("decision_rule") or {}
    return [requirement["id"], requirement["parameter"], requirement["unit"] or "待明确",
            ROUTE_LABELS[requirement["delivery_route"]], scope_text(requirement),
            "、".join(REPORT_LABELS.get(field, field) for field in requirement["report_fields"]) or "待明确",
            limit_text(requirement), RULE_LABELS.get(rule.get("type"), "待明确")]


def cell(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value if value is not None else "待明确").replace("|", "\\|").replace("\n", " ")


def requirement_table(payload):
    lines = ["| 编号 | 参数及对象 | 单位 | 实施方式 | 范围／点位 | 结果报告要求 | 限值 | 判定规则 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in payload["requirements"]:
        values = display_values(row)
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    return "\n".join(lines)


def render_plan(plan, readiness):
    payload = plan["payload"]
    state = "已确认" if readiness["user_confirmed"] else "候选稿，未确认"
    lines = [f"# {payload['title']}", "", f"状态：{state}；方案版本：{plan['revision']}。",
             "", requirement_table(payload), "", "## 证据与待处理事项", ""]
    for row in payload["requirements"]:
        lines.append(f"- {row['id']}：{cell(row['evidence'])}")
        if row.get("raw"):
            lines.append(f"  原方案内容：{cell(row['raw'])}")
    for issue in payload.get("open_items", []):
        lines.append(f"- {cell(issue)}")
    lines.extend(["", f"项目覆盖就绪：{'是' if readiness['coverage_ready'] else '否'}。",
                  f"结果判定就绪：{'是' if readiness['decision_ready'] else '否'}。",
                  "", "用户确认具体版本和实际配置后方可建立正式验收基准。"])
    return "\n".join(lines) + "\n"


def render_html(plan, readiness, workspace_root):
    payload = plan["payload"]
    rows = []
    for requirement in payload["requirements"]:
        values = display_values(requirement)
        rows.append("<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in values) + "</tr>")
    headers = ["编号", "参数及对象", "单位", "实施方式", "范围／点位", "报告要求", "限值", "判定规则"]
    issues = "".join("<li>" + html.escape(str(issue.get("message") or issue.get("code", "待处理事项"))) + "</li>" for issue in payload.get("open_items", []))
    if not issues:
        issues = "<li>具体原件与实际配置仍按方案状态核对。</li>"
    raw_sections = []
    for requirement in payload["requirements"]:
        columns = requirement.get("raw", {}).get("columns", {})
        if columns:
            content = "".join("<tr><th>" + html.escape(str(key)) + "</th><td>" + html.escape(str(value)) + "</td></tr>" for key, value in columns.items())
            raw_sections.append("<details><summary>" + html.escape(requirement["id"] + " " + requirement["parameter"]) + " 原方案字段</summary><table>" + content + "</table></details>")
    title = html.escape(payload["title"])
    state = "已确认" if readiness["user_confirmed"] else "候选稿，未确认"
    candidates = [Path(workspace_root) / "templates/calibration-plan/document.html",
                  Path(__file__).resolve().parents[3] / "templates/calibration-plan/document.html",
                  Path(sys.prefix) / "share/labcal/templates/document.html"]
    template_path = next((path for path in candidates if path.is_file()), None)
    if template_path is None:
        raise LabcalError("TEMPLATE_NOT_FOUND", "未找到方案呈现模板。")
    summary = f"项目覆盖：{'已就绪' if readiness['coverage_ready'] else '待补齐'}；结果判定：{'已就绪' if readiness['decision_ready'] else '待明确'}。"
    return Template(template_path.read_text(encoding="utf-8")).substitute(title=title, state=state, revision=plan["revision"], summary=summary,
            headers="".join("<th>"+x+"</th>" for x in headers), rows="".join(rows), issues=issues, raw_sections="".join(raw_sections))


def export_plan(workspace, lab_id, plan_id, revision=None):
    plan = get_plan(workspace, lab_id, plan_id, revision)
    readiness = assess_plan(workspace, lab_id, plan_id, plan["revision"])
    store = workspace.lab_store(lab_id)
    run_id = identifier("RUN")
    output = output_directory(store, run_id, "plan_export")
    output.mkdir(parents=True, exist_ok=False)
    files = {
        "calibration-plan.md": render_plan(plan, readiness),
        "requirements.md": "# 校准要求速查表\n\n" + requirement_table(plan["payload"]) + "\n",
        "confirmation-record.md": "# 逐参数计量确认记录骨架\n\n本表未填写实际证书结果，不构成设备放行。\n\n| 参数标识 | 参数 | 证书结果 | 不确定度 | 判定规则 | 结论 | 核对人及日期 |\n| --- | --- | --- | --- | --- | --- | --- |\n" + "\n".join(f"| {cell(r['id'])} | {cell(r['parameter'])} | 待填写 | 待填写 | {cell(r.get('decision_rule'))} | 未执行 | 待填写 |" for r in plan["payload"]["requirements"]) + "\n",
        "calibration-plan.html": render_html(plan, readiness, workspace.root),
    }
    for name, text in files.items():
        (output / name).write_text(text, encoding="utf-8")
    write_json(output / "plan.json", plan["payload"])
    write_json(output / "readiness.json", readiness)
    manifest = {"schema_version": "1.0", "run_id": run_id, "kind": "plan_export", "lab_id": lab_id,
                "plan_id": plan_id, "revision": plan["revision"], "plan_hash": plan["payload_hash"],
                "configuration_hash": plan["payload"]["equipment_config_hash"], "application_version": __version__,
                "render_revision": RENDER_REVISION, "created_at": utc_now(), "artifacts": [{"name": p.name, "sha256": file_hash(p)} for p in sorted(output.iterdir())]}
    write_json(output / "manifest.json", manifest)
    run_dir = processing_directory(store, "calibration", run_id)
    write_json(run_dir / "manifest.json", manifest)
    with store.connect() as db:
        db.execute("INSERT INTO runs VALUES (?,?,?,?)", (run_id, "plan_export", canonical_json(manifest), utc_now()))
        store.event(db, "plan_exported", run_id, {"manifest_hash": content_hash(manifest), "plan_id": plan_id, "revision": plan["revision"]})
    return {"run_id": run_id, "directory": str(output), "artifacts": manifest["artifacts"]}

