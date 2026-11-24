"""A per-import folder with immutable copies, separate from editable business facts."""
import json
from pathlib import Path
import re
import shutil

from ..errors import LabcalError
from ..util import ensure_inside, file_hash, content_hash, write_json


def import_directory(campaign_directory, source_name, workbook_id):
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', Path(source_name).stem).strip(' .')[:60] or '方案'
    return ensure_inside(campaign_directory / "校准方案" / (stem + "__" + workbook_id), campaign_directory)


def preserve_import(directory, source, item, *, lab_id, campaign_id):
    directory.mkdir(parents=True, exist_ok=True)
    original = ensure_inside(directory / "原件.xlsx", directory)
    if not original.exists():
        try:
            with original.open("xb") as output, Path(source["absolute_path"]).open("rb") as stream:
                shutil.copyfileobj(stream, output)
        except FileExistsError:
            pass
    if file_hash(original) != source["sha256"]:
        raise LabcalError("WORKBOOK_ARCHIVE_CHANGED", "方案文件夹中的原件副本摘要不一致，未覆盖该文件。")
    original.chmod(0o444)
    manifest = {"schema_version": "1.0", "lab_id": lab_id, "campaign_id": campaign_id,
        "workbook_id": item["id"], "workbook_hash": item["payload_hash"], "source_id": source["id"],
        "source_hash": source["sha256"], "source_name": source["name"], "lab_relative_object_path": source["object_path"],
        "original_file": "原件.xlsx", "extraction_file": "提取记录.json", "state": "candidate"}
    for name, value in (("提取记录.json", item["payload"]), ("索引.json", manifest)):
        file = ensure_inside(directory / name, directory)
        try:
            write_json(file, value)
        except LabcalError as exc:
            if exc.code != "OUTPUT_EXISTS": raise
            if content_hash(json.loads(file.read_text(encoding="utf-8"))) != content_hash(value):
                raise LabcalError("WORKBOOK_ARCHIVE_CHANGED", "方案文件夹中的固定提取记录已变化，未覆盖。")
        file.chmod(0o444)
    (directory / "README.md").write_text(
        f"# {source['name']}\n\n- [导入原件副本](原件.xlsx)\n- [固定提取记录](提取记录.json)\n- [归属与摘要](索引.json)\n\n"
        "原件副本和提取记录只读留存。方案内容保持候选；请通过工作台整理和确认，不直接修改此文件夹中的参数。"
        "实验室对象库留存原始字节，数据库管理在用事实。\n", encoding="utf-8")
    return {"directory": str(directory), "original_file": str(original), "manifest": manifest}
