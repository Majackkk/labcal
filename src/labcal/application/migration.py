from ..adapters.layout import output_directory
import json
from pathlib import Path
import re

from ..adapters.legacy import candidate_from_markdown, compare_parameter_documents
from ..errors import LabcalError
from ..util import file_hash, write_json, ensure_inside, utc_now
from .laboratories import create_laboratory, add_equipment
from .workspace_catalog import classify_storage
from .sources import import_source, source_gaps
from .plans import create_plan, assess_plan
from ..adapters.export import export_plan, RENDER_REVISION


CORE_CODES = ["CNAS-CL01:2018", "CNAS-CL01-G001:2024", "CNAS-CL01-G002:2021",
              "CNAS-CL01-G003:2021", "CNAS-CL01-A025:2022", "CNAS-R01:2023", "CNAS-GL015:2022"]


def migrate_legacy(workspace, source_root, lab_id="LAB_LEGACY", sample_lab="LAB_CERT_SAMPLES"):
    root = Path(source_root).resolve()
    support = root / "04_通用体系支撑与公用标准规程库"
    if not (support / "source_registry.json").is_file():
        raise LabcalError("LEGACY_ROOT_INVALID", "未找到原校准模块的资料登记册。")
    workspace.initialize()
    create_laboratory(workspace, lab_id, "旧项目迁移候选资料")
    create_laboratory(workspace, sample_lab, "现有证书样本（归属待确认）")
    classify_storage(workspace, lab_id, "migration_archive", "历史迁移候选资料容器，不作为实际实验室。")
    classify_storage(workspace, sample_lab, "sample_archive", "待归属证书样本容器，不作为实际实验室。")
    registry = json.loads((support / "source_registry.json").read_text(encoding="utf-8"))
    mapping, records, errors = {}, [], []
    for scope in registry["scopes"]:
        for record in scope["sources"]:
            path = ensure_inside(root / record["original_path"], root)
            if not path.is_file():
                errors.append({"code": "LEGACY_SOURCE_MISSING", "path": record["original_path"]})
                continue
            kind = record["source_class"]
            shared = kind == "cnas_document"
            result = import_source(workspace, path, kind=kind, code=record.get("source_code"),
                                   lab_id=None if shared else lab_id, shareable=shared,
                                   metadata={"legacy_scope": scope["scope"], "legacy_registry_hash": record.get("sha256"),
                                             "origin": "legacy_copy", "technical_review": "pending"})
            mapping[record["original_path"]] = result["id"]
            records.append({"path": record["original_path"], "source_id": result["id"], "scope": "reference" if shared else lab_id,
                            "registry_hash_matches": file_hash(path) == record.get("sha256")})
    directories = []
    project_root = root / "03_三级SOP与四级表单_8大检测项目包"
    for project in sorted(project_root.iterdir()):
        directory = project / "07_仪器设备校准与计量确认_CAL"
        if project.is_dir() and directory.is_dir():
            directories.append(directory)
    directories.append(support / "通用测量量具与公用规程")
    plans = []
    for directory in directories:
        for path in sorted(directory.glob("CAL-EQ-*.md")):
            text = path.read_text(encoding="utf-8")
            title = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
            code = path.name.split("_", 1)[0]
            equipment = add_equipment(workspace, lab_id, "MIG-" + code, title,
                                      configuration={"provisional_identity": True, "legacy_document": path.relative_to(root).as_posix(), "actual_configuration_confirmed": False})
            imported = import_source(workspace, path, kind="legacy_plan", lab_id=lab_id)
            payload = candidate_from_markdown(text, title=title, equipment_id=equipment["id"], source_id=imported["id"])
            companions = []
            for prefix, kind in [(code.replace("CAL-EQ-", "CAL-TABLE-"), "legacy_table"),
                                 (code.replace("CAL-EQ-", "FORM-CAL-"), "legacy_form")]:
                for companion in sorted(directory.glob(prefix + "_*.md")):
                    item = import_source(workspace, companion, kind=kind, lab_id=lab_id)
                    companions.append({"path": companion.relative_to(root).as_posix(), "source_id": item["id"], "kind": kind})
                    payload["source_ids"].append(item["id"])
                    if kind == "legacy_table":
                        payload["open_items"].extend(compare_parameter_documents(text, companion.read_text(encoding="utf-8")))
            plan = create_plan(workspace, lab_id, payload)
            readiness = assess_plan(workspace, lab_id, plan["plan_id"], plan["revision"])
            output = None
            if plan["reused"]:
                with workspace.lab_store(lab_id).connect() as db:
                    previous_runs = list(db.execute("SELECT manifest FROM runs WHERE kind='plan_export' ORDER BY created_at DESC"))
                for previous in previous_runs:
                    manifest = json.loads(previous[0])
                    if manifest.get("plan_hash") == plan["payload_hash"] and manifest.get("render_revision") == RENDER_REVISION:
                        directory_out = output_directory(workspace.lab_store(lab_id), manifest["run_id"], manifest["kind"], existing=True)
                        if all((directory_out / item["name"]).is_file() and file_hash(directory_out / item["name"]) == item["sha256"] for item in manifest["artifacts"]):
                            output = {"run_id": manifest["run_id"], "directory": str(directory_out), "artifacts": manifest["artifacts"], "reused": True}
                            break
            if output is None:
                output = export_plan(workspace, lab_id, plan["plan_id"], plan["revision"])
            plans.append({"legacy_code": code, "equipment_id": equipment["id"], **plan,
                          "requirement_count": len(payload["requirements"]), "open_items": payload["open_items"],
                          "companions": companions, "readiness": readiness, "export": output})
    certificates = []
    for path in sorted(workspace.root.glob("*.pdf")):
        item = import_source(workspace, path, kind="calibration_certificate", lab_id=sample_lab,
                             metadata={"document_type_status": "pending", "laboratory_assignment": "pending"})
        certificates.append({"filename": path.name, "source_id": item["id"]})
    relationships = json.loads((support / "relationship_registry.json").read_text(encoding="utf-8"))
    expected = sorted({value.replace("_", " ") for item in relationships["equipment_sets"] for value in item.get("calibration_source_codes", [])})
    report = {"schema_version": "1.0", "created_at": utc_now(), "source_root": str(root),
              "laboratory": lab_id, "sample_laboratory": sample_lab, "original_records": records,
              "registered_record_count": len(records), "registered_unique_hash_count": len({record["sha256"] for scope in registry["scopes"] for record in scope["sources"]}),
              "plans": plans, "certificates": certificates, "expected_metrology_codes": expected,
              "metrology_gaps": source_gaps(workspace, lab_id, expected), "errors": errors,
              "confirmation_policy": "User explicitly stated no confirmed plans; all legacy plans remain candidates."}
    output = workspace.root / "docs/migration/legacy-import-report.json"
    write_json(output, report, overwrite=True)
    return {"report": str(output), "registered_records": len(records), "candidate_plans": len(plans),
            "certificates": len(certificates), "missing_metrology_originals": sum(x["status"] == "original_missing" for x in report["metrology_gaps"]),
            "confirmed_plans": 0, "errors": errors}


def import_official_downloads(workspace, directory):
    directory = Path(directory)
    acquisition = json.loads((directory / "acquisition.json").read_text(encoding="utf-8"))
    codes = {
        "CNAS-CL01-2018-2019-revision.pdf": "CNAS-CL01:2018",
        "CNAS-CL01-G001-2024.pdf": "CNAS-CL01-G001:2024",
        "CNAS-CL01-G003-2021-2023-revision.pdf": "CNAS-CL01-G003:2021",
        "CNAS-CL01-A025-2022.pdf": "CNAS-CL01-A025:2022",
        "CNAS-R01-2023.pdf": "CNAS-R01:2023",
    }
    results = []
    for record in acquisition:
        if record["status"] != "DOWNLOADED":
            results.append(record)
            continue
        metadata = {"publisher": "中国合格评定国家认可委员会", "acquired_from": record["url"], "version_status": "pending", "applicability_status": "pending"}
        if record["name"] == "CNAS-CL01-2018-2019-revision.pdf" and record.get("pages", 0) < 4:
            metadata.update({"content_completeness": "incomplete", "completeness_note": "公开文件仅封面／前言，需补齐准则正文。"})
        item = import_source(workspace, directory / record["name"], kind="cnas_document", code=codes[record["name"]], shareable=True, metadata=metadata)
        results.append({"code": codes[record["name"]], "source_id": item["id"], "pages": record.get("pages"), "content_completeness": metadata.get("content_completeness", "pending")})
    report = workspace.root / "docs/migration/official-source-acquisition.json"
    write_json(report, results, overwrite=True)
    return {"report": str(report), "sources": results}

