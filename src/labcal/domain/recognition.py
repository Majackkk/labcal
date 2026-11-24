"""Deterministic candidate classification and identity matching, without approval."""
import re
import unicodedata

from ..util import content_hash

VERSION = "local-recognition/1.1"


def normalized(value):
    return re.sub(r"[\s:：,，;；()（）]+", "", unicodedata.normalize("NFKC", str(value or ""))).casefold()


def name_clue(value):
    """Remove section decoration for weak name search, never populate model/serial."""
    text = re.sub(r"【[^】]*】", "", str(value or ""))
    text = re.sub(r"[（(][^）)]*(?:共|合计|total|项)[^）)]*[）)]", "", text, flags=re.I)
    text = re.sub(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9_.\-/]*\d[A-Za-z0-9_.\-/]*(?![A-Za-z0-9])", "", text)
    return normalized(text)


def classify_devices(workbook_id, workbook_hash, rows):
    """Keep explicit identities separate; a nameless section remains a candidate group."""
    groups = {}
    for row in rows:
        fields = row["fields"]
        identity = {k: fields.get(k) for k in ("equipment_name", "manufacturer", "model", "equipment_number")}
        # Section/sheet is part of the key: identical names in separate sections are
        # not silently merged into one physical instrument.
        key = content_hash({"sheet": row["sheet"], "group": row["group"], "identity": identity})
        if key not in groups:
            groups[key] = {"id": "DEV-" + content_hash([workbook_id, key])[:24], "kind": "workbook_candidate",
                "workbook_id": workbook_id, "workbook_hash": workbook_hash, "identity": identity,
                "name": identity["equipment_name"] or row["group"], "group": row["group"],
                "rows": [], "anchors": [], "technical_state": "candidate", "equipment_id": None,
                "plan_id": None, "revision": None, "baseline_id": None}
        groups[key]["rows"].append(row["id"])
        groups[key]["anchors"].append(row["anchor"])
    return list(groups.values())


def match_identity(fields, devices):
    """Never choose between duplicate identities, conflicting values or mere scores."""
    aliases = {"equipment_name": "instrument_name", "model": "model", "equipment_number": "instrument_number", "manufacturer": "manufacturer"}
    values = {key: {normalized(c["value"]) for c in fields.get(alias, []) if c.get("value")} for key, alias in aliases.items()}
    inconsistent = [key for key, candidates in values.items() if len(candidates) > 1]
    ranked = []
    for device in devices:
        equal, conflicts, hints = [], [], []
        for key, candidates in values.items():
            expected = normalized(device["identity"].get(key))
            if not expected or not candidates: continue
            if expected in candidates:
                equal.append(key)
            elif key == "equipment_name" and any(expected in v or v in expected or
                    (name_clue(device["identity"].get(key)) and name_clue(v) and
                     (name_clue(device["identity"].get(key)) in name_clue(v) or name_clue(v) in name_clue(device["identity"].get(key)))) for v in candidates):
                hints.append(key)
            else:
                conflicts.append(key)
        strength = 3 if "equipment_number" in equal else 2 if "model" in equal and ("equipment_name" in equal or hints) else 1 if "equipment_name" in equal or hints else 0
        if strength:
            ranked.append({"device_id": device["id"], "name": device["name"], "strength": strength,
                           "equal": equal, "conflicts": conflicts, "hints": hints})
    ranked.sort(key=lambda d: (-d["strength"], d["device_id"]))
    top = [d for d in ranked if d["strength"] == ranked[0]["strength"]] if ranked else []
    state, chosen, reason = "unmatched", None, "未找到可辨认的方案设备身份。"
    if inconsistent:
        state, reason = "conflict", "报告中同一身份字段出现不同候选值，需回看原页。"
    elif len(top) > 1:
        state, reason = "ambiguous", "多个设备具有相同匹配依据，尚不能确定具体设备。"
    elif top:
        item = top[0]
        if item["conflicts"]:
            state, reason = "conflict", "匹配线索与其他身份字段冲突，尚未建立关联。"
        else:
            chosen = item["device_id"]
            if item["strength"] >= 2:
                state, reason = "matched", "身份字段唯一匹配，已自动关联；原页与实际设备对应关系仍待核验。"
            else:
                state, reason = "suggested", "按名称自动归入候选设备；缺少编号或型号交叉证据，具体设备待确认。"
    return {"state": state, "device_id": chosen, "reason": reason, "candidates": ranked,
            "identity_conflicts": inconsistent, "technical_approval": False}
