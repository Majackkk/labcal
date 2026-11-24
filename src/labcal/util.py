from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
import unicodedata

from .errors import LabcalError


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def normalize_text(text):
    return re.sub(r"\s+", "", "".join(c for c in str(text) if not unicodedata.category(c).startswith("C"))).casefold()


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value):
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_hash(path):
    h = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def identifier(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def check_lab_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,63}", value):
        raise LabcalError("INVALID_LAB_ID", "实验室标识只能包含大写字母、数字、下划线或连字符。")
    return value


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LabcalError("INVALID_JSON_FILE", "无法读取有效的 JSON 文件。", {"path": str(path), "reason": str(exc)}) from exc


def atomic_write_text(path, text, *, overwrite=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged = tempfile.mkstemp(prefix=".labcal-write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        if overwrite: os.replace(staged, path)
        else: os.link(staged, path)
    except FileExistsError as exc:
        raise LabcalError("OUTPUT_EXISTS", "输出已存在，请选用新文件或新任务。", {"path": str(path)}) from exc
    finally:
        Path(staged).unlink(missing_ok=True)


def write_json(path, value, *, overwrite=False):
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", overwrite=overwrite)


def ensure_inside(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise LabcalError("PATH_OUTSIDE_SCOPE", "路径超出当前任务允许的数据范围。") from exc
    return path

