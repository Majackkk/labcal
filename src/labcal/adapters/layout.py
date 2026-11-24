"""Versioned filesystem routing. Database facts and immutable objects stay in place."""
import re
from ..errors import LabcalError
from ..util import ensure_inside

LAYOUT_VERSION = 2
LANES = {"plan_export": "calibration", "certificate_review_export": "certificates", "certificate_self_diagnosis": "certificates",
         "method_diagnosis": "certificates", "laboratory_summary": "summaries"}


def segment(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise LabcalError("LAYOUT_ID_INVALID", "存储标识必须是安全的单级目录名。")
    return value


def template_directory(store, role, source_id):
    return ensure_inside(store.directory / 'templates' / segment(role) / segment(source_id), store.directory)


def output_directory(store, run_id, kind=None, *, existing=False):
    """New writes are lane-scoped; old exports remain readable without rewriting manifests."""
    run_id = segment(run_id)
    candidates = [store.directory / "outputs" / lane / run_id for lane in sorted(set(LANES.values()))]
    candidates.append(store.directory / "exports" / run_id)
    found = [ensure_inside(p, store.directory) for p in candidates if p.exists()]
    if len(found) > 1:
        raise LabcalError("LAYOUT_AMBIGUOUS", "同一输出任务存在多个目录，请先核对，不自动选择。")
    if found:
        if kind in LANES and found[0].parent.name in LANES.values() and found[0].parent.name != LANES[kind]:
            raise LabcalError("LAYOUT_LANE_CONFLICT", "输出目录与任务线路不一致。")
        return found[0]
    if kind not in LANES:
        if existing: raise LabcalError("EXPORT_NOT_FOUND", "输出目录不存在。")
        raise LabcalError("LAYOUT_KIND_INVALID", "输出任务未指定有效线路。")
    return ensure_inside(store.directory / "outputs" / LANES[kind] / run_id, store.directory)


def processing_directory(store, lane, task_id):
    if lane not in {"calibration", "certificates", "maintenance", "legacy", "methods"}:
        raise LabcalError("LAYOUT_LANE_INVALID", "处理目录未指定有效线路。")
    return ensure_inside(store.directory / "processing" / lane / segment(task_id), store.directory)


def upload_directory(store, campaign_id, lane, upload_id):
    if lane not in {"calibration", "certificates", "institution"}:
        raise LabcalError("LAYOUT_LANE_INVALID", "输入目录未指定有效线路。")
    return ensure_inside(store.directory / "inputs" / segment(campaign_id) / lane / segment(upload_id), store.directory)


def initialize_layout(store):
    for path in ["inputs", "processing/calibration", "processing/certificates", "processing/maintenance",
                 "outputs/calibration", "outputs/certificates", "outputs/summaries", "processing/methods"]:
        ensure_inside(store.directory / path, store.directory).mkdir(parents=True, exist_ok=True)
