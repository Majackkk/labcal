"""Immutable report originals and association snapshots under the owning campaign."""
import json
from pathlib import Path
import shutil
import os
import tempfile

from ..errors import LabcalError
from ..util import canonical_json, file_hash, ensure_inside


def fixed_json(path, value):
    data = (canonical_json(value) + "\n").encode("utf-8")
    descriptor, staged = tempfile.mkstemp(prefix=".archive-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output: output.write(data)
        try: os.link(staged, path)
        except FileExistsError:
            try: same = json.loads(path.read_text(encoding="utf-8")) == value
            except (ValueError, UnicodeError): same = False
            if not same:
                raise LabcalError("REPORT_ARCHIVE_CHANGED", "报告归档快照已变化，未覆盖。")
    finally: Path(staged).unlink(missing_ok=True)
    path.chmod(0o444)


def preserve_report(campaign_directory, source):
    directory = ensure_inside(campaign_directory / "证书报告" / "原件档案" / source["id"], campaign_directory)
    directory.mkdir(parents=True, exist_ok=True)
    file = directory / "原件.pdf"
    descriptor, staged = tempfile.mkstemp(prefix=".original-", dir=directory)
    os.close(descriptor)
    try:
        shutil.copyfile(source["absolute_path"], staged)
        if file_hash(staged) != source["sha256"]:
            raise LabcalError("REPORT_ARCHIVE_CHANGED", "报告原件在归档期间发生变化。")
        try: os.link(staged, file)
        except FileExistsError: pass
    finally: Path(staged).unlink(missing_ok=True)
    if file_hash(file) != source["sha256"]:
        raise LabcalError("REPORT_ARCHIVE_CHANGED", "报告归档原件摘要不一致，未覆盖。")
    file.chmod(0o444)
    fixed_json(directory / "原件索引.json", {"source_id": source["id"], "source_hash": source["sha256"],
        "source_name": source["name"], "lab_relative_object_path": source["object_path"], "original_file": "原件.pdf"})
    return directory


def preserve_association(campaign_directory, association, source):
    item = association["payload"]; match = item["match"]
    category = "设备关联" if match["state"] in {"matched", "operator_linked"} else "待确认关联" if match["state"] == "suggested" else "待匹配"
    target = match["device_id"] or "未确定设备"
    directory = ensure_inside(campaign_directory / "证书报告" / category / target / source["id"], campaign_directory)
    directory.mkdir(parents=True, exist_ok=True)
    fixed_json(directory / (association["id"] + ".json"), {k: association[k] for k in ("id", "payload", "payload_hash")})
    # A fixed relative reference, not a second editable copy of the report/limits.
    fixed_json(directory / "原件入口.json", {"source_id": source["id"], "source_hash": source["sha256"],
        "campaign_relative_original": "证书报告/原件档案/" + source["id"] + "/原件.pdf"})
    return directory
