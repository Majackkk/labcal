"""Derived annotated PDFs and matching human/structured reports."""
from .layout import output_directory
import copy
import html
import json
from pathlib import Path

from .. import __version__
from ..application.certificates import certificate_store, get_review, get_extraction
from ..application.sources import verify_source_integrity
from ..util import canonical_json, content_hash, file_hash, identifier, utc_now, write_json
from ..errors import LabcalError
from .pdf_process import annotate_pdf_isolated as annotate_pdf
from ..application.report_diagnosis import finding_category

STATUS = {"passed": "符合", "failed": "不符合", "pending": "待核实", "not_applicable": "不适用"}


def export_review(workspace, lab_id, review_id, *, annotation_style="callouts", campaign_id=None):
    if annotation_style not in {"standard", "callouts"}: raise LabcalError("ANNOTATION_STYLE_INVALID", "批注方式应为standard或callouts。")
    review = get_review(workspace, lab_id, review_id)
    payload = copy.deepcopy(review["payload"])
    views={key:item["payload"] for key,item in payload.get("report_views",{}).items()}
    for key,item in payload.get("report_views",{}).items():
        if content_hash(item["payload"])!=item["payload_hash"]: raise LabcalError("DIAGNOSIS_SNAPSHOT_INTEGRITY", "诊断快照摘要不一致。")
    if campaign_id:
        from ..application.report_workspace import report_detail
        for snapshot in payload["certificate_snapshots"]:
            detail=report_detail(workspace,lab_id,campaign_id,snapshot["extraction_id"],review_id=review_id)
            if detail["diagnosis_stale"] or not detail["diagnosis"]:
                raise LabcalError("DIAGNOSIS_REFRESH_REQUIRED", "资料或方案已更新，请先开始诊断，再导出完整诊断包。")
    for finding in payload["findings"]:
        resolution = review["resolutions"].get(finding["id"])
        if resolution:
            finding["resolution"] = resolution
            if resolution["action"] == "confirmed": finding["certainty"] = "confirmed"
    store = certificate_store(workspace, lab_id)
    run_id = identifier("RUN")
    output = output_directory(store, run_id, "certificate_review_export")
    output.mkdir(parents=True)
    pdfs = []
    for snapshot in payload["certificate_snapshots"]:
        ext = get_extraction(workspace, lab_id, snapshot["extraction_id"])
        source = verify_source_integrity(workspace, lab_id, snapshot["source_id"])
        filename = snapshot["certificate_id"] + "-annotated.pdf"
        findings = [f for f in payload["findings"] if f["extraction_id"] == snapshot["extraction_id"] and
            (not views or finding_category(f)=="content_issue" and f["location"].get("kind")!="document")]
        findings = [f for f in findings if f.get('resolution', {}).get('action') not in {'dismissed', 'resolved'}]
        if not findings:
            continue
        for finding in findings:
            if 'dimension' not in finding.get('location', {}).get('markup', {}) and finding_category(finding) == 'content_issue':
                finding['dimension'] = 'certificate_self'
        page_mapping = annotate_pdf(source["absolute_path"], output / filename, findings, style=annotation_style)
        pdfs.append({"file": filename, "original_name": source["name"], "source_hash": source["sha256"],
                     "digital_signatures_present_in_original": ext["payload"]["digital_signatures_present"],
                     "role": "review_copy_original_signature_not_revalidated"})
        if page_mapping: pdfs[-1]["page_mapping"] = page_mapping
    # Resolutions preserve the original calculation. A rerun is needed for new conclusions.
    payload["resolution_note"] = "复核处理记录不改写原始计算；补充证据后重新审查生成新快照。"
    write_json(output / "review.json", payload)
    write_json(output / "resolutions.json", review["resolutions"])
    if views:
        from .diagnosis_export import export_diagnosis_views
        export_diagnosis_views(output,views)
    rows = []
    markdown = ["# 证书审查记录", "", "本记录供实验室复核，不构成自动技术批准。", "",
                " | ".join(f"{label}：{STATUS[payload['axes'][axis]]}" for axis, label in [("document", "证书资料"), ("coverage", "方案覆盖"), ("fitness", "用途判定")]),
                "", f"基准：{payload['baseline_id'] or '未绑定，仅基础审查'}。", "", payload["resolution_note"], ""]
    for finding in payload["findings"]:
        if views and finding_category(finding)!="content_issue": continue
        state = finding.get("resolution", {}).get("action") or ("已证实" if finding["certainty"] == "confirmed" else "待核实")
        page = finding["location"]["page"]
        markdown.extend([f"## {finding['id']} {finding['title']}", "", f"位置：第{page}页；状态：{state}。",
                         finding["message"], "", "依据：" + finding["basis"], "", "建议：" + finding["suggestion"], ""])
        rows.append("<tr>" + "".join("<td>" + html.escape(str(x)) + "</td>" for x in [finding["id"], finding["title"], f"第{page}页", state, finding["message"], finding["basis"], finding["suggestion"]]) + "</tr>")
    (output / "issues.md").write_text("\n".join(markdown), encoding="utf-8")
    links = "".join(f'<li><a href="{html.escape(p["file"])}">{html.escape(p["original_name"])} · 批注副本</a></li>' for p in pdfs)
    axis_labels = "".join(f'<div class="axis"><small>{label}</small><strong>{STATUS[payload["axes"][axis]]}</strong></div>' for axis, label in [("document", "证书资料"), ("coverage", "方案覆盖"), ("fitness", "用途判定")])
    report = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>证书审查记录</title>
<style>body{{font:14px/1.65 "Helvetica Neue",Helvetica,Arial,"PingFang SC",sans-serif;color:#111;background:#fff;margin:0;padding:40px}}main{{max-width:1180px;margin:auto}}h1{{font-size:42px;letter-spacing:-1.4px;margin:0 0 12px;border-top:6px solid #e63329;padding-top:22px}}.axes{{display:grid;grid-template-columns:repeat(3,1fr);gap:24px;margin:28px 0;border-top:2px solid #111}}.axis{{padding:14px 0;border-bottom:1px solid #bbb}}small,strong{{display:block}}strong{{font-size:22px}}table{{width:100%;border-collapse:collapse;background:white;border-top:2px solid #111}}td,th{{padding:12px 9px;border-bottom:1px solid #ccc;vertical-align:top;text-align:left}}th{{background:#f3f3ef;font-size:12px}}td{{font-size:12px}}a{{color:#111;text-underline-offset:3px}}p{{color:#666}}@media(max-width:700px){{body{{padding:20px}}h1{{font-size:30px}}table{{display:block;overflow:auto}}}}</style>
<main><h1>证书审查记录</h1><p>任务 {html.escape(review_id)} · 供实验室复核，未自动技术批准</p><div class="axes">{axis_labels}</div>
<p>基准：{html.escape(payload['baseline_id'] or '未绑定，仅基础审查')}。{html.escape(payload['resolution_note'])}</p><ul>{links}</ul>
<table><thead><tr><th>编号</th><th>问题</th><th>位置</th><th>状态</th><th>说明</th><th>依据</th><th>建议</th></tr></thead><tbody>{''.join(rows)}</tbody></table></main></html>'''
    (output / "review.html").write_text(report, encoding="utf-8")
    artifacts = [{"name": p.name, "sha256": file_hash(p)} for p in sorted(output.iterdir()) if p.is_file()]
    manifest = {"schema_version": "1.0", "run_id": run_id, "kind": "certificate_review_export", "lab_id": lab_id,
                "review_id": review_id, "review_hash": review["payload_hash"], "resolution_hash": content_hash(review["resolutions"]),
                "application_version": __version__, "annotation_style": annotation_style, "created_at": utc_now(), "pdfs": pdfs, "artifacts": artifacts}
    if views: manifest["diagnosis_views"]={key:content_hash(value) for key,value in views.items()}
    write_json(output / "manifest.json", manifest)
    with store.connect() as db:
        db.execute("INSERT INTO runs VALUES (?,?,?,?)", (run_id, manifest["kind"], canonical_json(manifest), utc_now()))
        store.event(db, "certificate_review_exported", run_id, {"review_id": review_id, "manifest_hash": content_hash(manifest)})
    return {"run_id": run_id, "directory": str(output), "manifest": manifest}
