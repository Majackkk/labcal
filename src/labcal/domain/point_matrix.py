"""Read-only point projections. Parsed cells are candidates, never acceptance facts.

Only explicit point lists are expanded. Ranges, percentages of capacity and unclear
prose stay unresolved. Matching needs parameter family, compatible units and an
exact Decimal value; component/condition ambiguity remains visible.
"""
from decimal import Decimal, InvalidOperation
import json
import re

VERSION = "point-matrix-1.0"
NUMBER = r"[-+−]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
UNIT = r"(?:mm/min|mm/s|m/s|kN|mN|N|μm|µm|um|mm|cm|m|MPa|kPa|Pa|bar|mbar|mV|V|mA|A|kHz|Hz|℃|°C|%RH|%|kg|mg|g|J|s|min)"
UNITS = {"N": ("force", "1"), "kN": ("force", "1000"), "mN": ("force", ".001"),
         "mm": ("length", "1"), "μm": ("length", ".001"), "µm": ("length", ".001"), "um": ("length", ".001"),
         "cm": ("length", "10"), "m": ("length", "1000"),
         "mm/min": ("speed", "1"), "mm/s": ("speed", "60"), "m/s": ("speed", "60000"),
         "Pa": ("pressure", "1"), "kPa": ("pressure", "1000"), "MPa": ("pressure", "1000000"), "bar": ("pressure", "100000"), "mbar": ("pressure", "100"),
         "℃": ("temperature", "1"), "°C": ("temperature", "1")}


def compact(text):
    return re.sub(r"\s+", "", str(text or "")).replace("−", "-")


def numeric(text):
    value = compact(text)
    if "," in value and not re.fullmatch(r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", value): return None
    value = value.replace(",", "")
    if not re.fullmatch(NUMBER, value): return None
    try: return Decimal(value)
    except InvalidOperation: return None


def family(text):
    text = compact(text)
    # Specific quantities precede broad ones: extensometer deformation is not crosshead displacement.
    for key, pattern in [("humidity_upper_deviation", r"湿度上偏差"), ("humidity_lower_deviation", r"湿度下偏差"),
                         ("temperature_upper_deviation", r"温度上偏差"), ("temperature_lower_deviation", r"温度下偏差"),
                         ("humidity_uniformity", r"湿度均匀"), ("humidity_fluctuation", r"湿度波动"), ("humidity_deviation", r"湿度.*(?:偏差|误差)"),
                         ("temperature_uniformity", r"温度均匀"), ("temperature_fluctuation", r"温度波动"),
                         ("temperature_deviation", r"温度.*(?:偏差|误差)"),
                         ("extensometer", r"引伸计|微变形"), ("coaxiality", r"同轴度"),
                         ("radius", r"半径"), ("span", r"跨距"), ("speed", r"速度"),
                         ("displacement", r"位移"), ("force", r"试验力|力值|负荷|拉力"), ("pressure", r"压力"),
                         ("temperature", r"温度"), ("humidity", r"湿度"), ("energy", r"能量")]:
        if re.search(pattern, text): return key
    return text


def point_key(value, unit):
    n = numeric(value)
    if n is None or not unit: return None
    dimension, factor = UNITS.get(unit, (unit, "1"))
    return dimension, n * Decimal(factor)


def display_requirement(value):
    if not isinstance(value, dict): return str(value or "")
    labels = {"lower":"下限", "upper":"上限", "unit":"单位", "maximum":"最大值", "expected":"期望结果",
              "lower_inclusive":"包含下限", "upper_inclusive":"包含上限", "kind":"类型"}
    return "；".join(f"{labels.get(k,k)}：{v}" for k,v in value.items())


def plan_points(requirements):
    result = []
    for req in requirements:
        raw = req.get("raw", {}); fields = req.get("fields", raw.get("fields", {}))
        conditions = str(raw.get("values", {}).get("conditions") or "")
        limit = display_requirement(fields.get("mpe") or req.get("limit"))
        uncertainty = display_requirement(fields.get("uncertainty") or req.get("uncertainty"))
        structured_condition = " / ".join(str(req[k]) for k in ("channel", "mode") if req.get(k))
        if req.get("conditions"): structured_condition += " " + json.dumps(req["conditions"], ensure_ascii=False)
        base = {"requirement_id": req["id"], "parameter": req["parameter"], "family": family(req["parameter"]),
                "component": str(req.get("component") or ""), "condition": structured_condition.strip(), "point": "", "unit": req.get("unit", ""),
                "mpe": limit, "uncertainty": str(uncertainty), "source": req.get("plan_anchor") or raw.get("anchor", ""),
                "raw_conditions": conditions, "parse_state": "explicit", "note": "",
                "delivery_route": req.get("delivery_route", "unresolved")}
        emitted = []
        if req.get("points"):
            for point in req["points"]:
                emitted.append({**base, "point": str(point)})
        else:
            context = structured_condition; notes = []; unresolved = []
            for segment in re.split(r"[\n；;]", conditions):
                s = segment.strip()
                if not s: continue
                # Notes and range statements are never mined for apparent point numbers.
                if s.startswith(("*", "（", "(")) or re.search(r"重点|根据", s):
                    notes.append(s); continue
                if re.search(r"各摆锤|[~～至]|\d\s*[-—]\s*\d", s): unresolved.append(s); continue
                if re.search(r"工况下[：:]?$", s): context = s.rstrip("：:"); continue
                component = base["component"]; condition = context
                match = re.search(r"(?:校准点|测量点|检定点|点位)(?:为|是)?[：:]?\s*(.*)", s)
                if match:
                    prefix = re.sub(r"^\d+[、.]\s*", "", s[:match.start()]).rstrip("：: ")
                    component = prefix or component; s = match.group(1)
                elif ":" in s or "：" in s:
                    prefix, tail = re.split(r"[：:]", s, maxsplit=1)
                    if re.search(r"标称|推荐|名义", prefix): s = re.sub(r"^R\s*", "", tail.strip()); condition = prefix
                # Repeated units are accepted only when the entire segment is an explicit list.
                if re.fullmatch(NUMBER+r"\s*"+UNIT+r"(?:\s*[,，、]\s*"+NUMBER+r"\s*"+UNIT+r")+", s):
                    for value, unit in re.findall("("+NUMBER+r")\s*("+UNIT+")", s):
                        emitted.append({**base, "component":component, "condition":condition, "point":value, "unit":unit})
                    continue
                # Accept only a leading numeric list followed by an explicit unit.
                m = re.match(r"^\s*(" + NUMBER + r"(?:\s*[,，、]\s*" + NUMBER + r")*)\s*(" + UNIT + r")(?![A-Za-z/])", s)
                if not m: unresolved.append(segment.strip()); continue
                tail = s[m.end():].strip()
                if re.search(NUMBER + r"\s*" + UNIT, tail) and not tail.startswith(("(", "（")):
                    unresolved.append(segment.strip()); continue
                condition = "；".join(x for x in (condition, tail) if x)
                for point in re.split(r"\s*[,，、]\s*", m.group(1)):
                    emitted.append({**base, "component": component, "condition": condition, "point": point, "unit": m.group(2)})
            for row in emitted: row["note"] = "；".join(notes)
            if emitted and unresolved:
                emitted.append({**base, "parse_state":"unresolved", "condition":"；".join(unresolved), "note":"部分原文尚未可靠拆分，不能按其余已识别点代表全部要求。"})
        if not emitted:
            emitted = [{**base, "parse_state": "unresolved", "condition": conditions or str(req.get("calibration_range") or ""),
                        "note": "未明确离散点位；保留范围、工况或原文，待确定。"}]
        for i, row in enumerate(emitted): result.append({"id": f"{req['id']}:{i+1}", **row})
    return result


def _unit(text):
    m = re.search(r"(?<![A-Za-z])" + UNIT + r"(?![A-Za-z/])", str(text or ""))
    return m.group() if m else ""


def _header_role(text):
    text = compact(text).lower()
    for name, pattern in [("upper_deviation",r"上偏差"),("lower_deviation",r"下偏差"),("uniformity",r"均匀度"),("fluctuation",r"波动度")]:
        if re.search(pattern,text): return name
    if re.search(r"不确定|uncertainty", text): return "uncertainty"
    if re.search(r"允许|mpe|tolerance", text): return "repeatability_mpe" if re.search(r"重复|repeatability", text) else "mpe"
    if re.search(r"重复|repeatability", text): return "repeatability"
    if re.search(r"误差|error", text): return "error"
    if re.search(r"校准点|检定点|测量点|标准值|设定温度|设定湿度|仪器示值|calibrationpoint|instrumentindication", text): return "point"
    if re.search(r"实测|测得|示值|measured|indication", text): return "measured"
    if re.search(r"项目|通道|部件|工况|project", text): return "condition"
    return ""


def certificate_points(extraction):
    """Parse stored table cells without mutating the fixed extraction or inventing cells."""
    result, warnings = [], []
    for page in extraction["payload"]["pages"]:
        for ti, table in enumerate(page.get("tables", [])):
            rows = table.get("rows", [])
            if len(rows) < 2: continue
            header_index = next((i for i, r in enumerate(rows[:3]) if any(_header_role(c) in {"point", "error"} for c in r)
                                 and any(_header_role(c) in {"error", "measured", "upper_deviation", "lower_deviation", "uniformity", "fluctuation"} for c in r)), None)
            if header_index is None: continue
            headers = rows[header_index]; roles = [_header_role(c) for c in headers]
            # A standard/nominal value is the point when an instrument indication also exists.
            nominal = [i for i,c in enumerate(headers) if re.search(r"标准值|校准点|检定点|测量点|设定温度|设定湿度|calibration\s*point", str(c), re.I)]
            if len(nominal) == 1:
                for i,c in enumerate(headers):
                    if i != nominal[0] and roles[i] == "point" and re.search(r"仪器示值|instrument\s*indication", str(c), re.I): roles[i] = "measured"
            if any(roles.count(role) > 1 for role in set(roles) if role):
                warnings.append(f"第 {page['page']} 页结果表含重复结果或通道列，尚不能可靠逐列对应；请查看原表。")
                continue
            characteristics = {"upper_deviation":"上偏差", "lower_deviation":"下偏差", "uniformity":"均匀度", "fluctuation":"波动度"}
            if not set(roles) & ({"error", "measured"} | characteristics.keys()): continue
            bbox = table["bbox"]
            headings = [l for l in page.get("lines", []) if l["bbox"][3] <= bbox[1]+.002
                        and bbox[1]-l["bbox"][3] < .09 and re.search(r"[\u4e00-\u9fff]", l["text"])
                        and re.search(r"校准|检定|测量|结果", l["text"])]
            heading = max(headings, key=lambda l:l["bbox"][1])["text"] if headings else ""
            title = re.sub(r"^\d+[.、]\s*", "", heading).strip("：: ")
            if not title or title in {"校准结果", "检定结果", "测量结果"}:
                title = next((str(c).split("\n")[0] for c, role in zip(headers, roles) if role == "error"), "未识别参数")
            units = [_unit(c) for c in headers]; condition = ""; prev_y = bbox[1]; table_results = []
            shared_u = ""
            for ri, cells in enumerate(rows[header_index+1:], header_index+1):
                cells = [str(c or "").strip() for c in cells]
                if any(re.search(r"不确定度|Uncertainty", c) for c in cells):
                    shared_u = " ".join(c for c in cells if re.search(r"[=＝].*\d|\d.*%", c)); continue
                values = {role:cells[i] for i, role in enumerate(roles) if role and i < len(cells)}
                numeric_role = "point" if "point" in roles else "error"
                if numeric(values.get(numeric_role, "")) is None:
                    # Unit/header rows may establish merged condition, but never yield results.
                    for i, c in enumerate(cells):
                        if i < len(units) and _unit(c) and not re.search(r"\d", c): units[i] = _unit(c)
                    if values.get("condition"): condition = values["condition"]
                    continue
                if values.get("condition"): condition = values["condition"]
                formatted = {}
                for i, role in enumerate(roles):
                    if not role or i >= len(cells): continue
                    formatted[role] = cells[i] + (" " + units[i] if cells[i] and units[i] and not _unit(cells[i]) else "")
                    if role == "uncertainty" and re.fullmatch(r"U\s*=", cells[i]) and i+1 < len(cells) and i+1 < len(roles) and not roles[i+1] and re.search(r"\d", cells[i+1]):
                        formatted[role] = cells[i] + " " + cells[i+1]
                candidates = [l for l in page.get("lines", []) if bbox[0] <= l["bbox"][0] <= bbox[2]
                              and prev_y < (l["bbox"][1]+l["bbox"][3])/2 <= bbox[3]
                              and compact(l["text"]) == compact(values[numeric_role])]
                # Choose the leftmost column occurrence on the earliest matching row.
                chosen = min(candidates, key=lambda l:(l["bbox"][1], l["bbox"][0])) if candidates else None
                anchor_box = bbox; line_ids = []; precision = "table"
                if chosen:
                    y = (chosen["bbox"][1]+chosen["bbox"][3])/2; prev_y = y+.001
                    lines = [l for l in page["lines"] if bbox[0]-.002 <= l["bbox"][0] <= bbox[2]
                             and abs((l["bbox"][1]+l["bbox"][3])/2-y)<.006]
                    anchor_box = [bbox[0], min(l["bbox"][1] for l in lines), bbox[2], max(l["bbox"][3] for l in lines)]
                    line_ids = [l["id"] for l in lines]; precision = "row"
                record = {"id": f"{extraction['id']}:{page['page']}:{ti}:{ri}", "parameter": title, "family": family(title),
                          "point": values.get("point", ""), "unit": units[roles.index("point")] if "point" in roles else "",
                          "condition": condition, "measured": formatted.get("measured", ""), "error": formatted.get("error", ""),
                          "repeatability": formatted.get("repeatability", ""), "mpe": formatted.get("mpe", ""),
                          "repeatability_mpe": formatted.get("repeatability_mpe", ""), "uncertainty": formatted.get("uncertainty", ""),
                          "uncertainty_scope": "cell", "anchor_precision": precision, "state": "candidate",
                          "anchor": {"extraction_id": extraction["id"], "location": {"page": page["page"], "bbox": anchor_box,
                                     "line_ids": line_ids, "quote": " | ".join(cells), "kind": "text" if line_ids else "section"}}}
                metrics = [role for role in roles if role in characteristics]
                if metrics:
                    for metric in metrics:
                        if values.get(metric):
                            prefix = "温度" if record["unit"] in {"℃", "°C"} else "湿度" if record["unit"] == "%RH" else title
                            label = prefix + characteristics[metric]
                            table_results.append({**record, "id":record["id"]+":"+metric, "parameter":label, "family":family(label), "error":formatted[metric]})
                else: table_results.append(record)
            if shared_u:
                for r in table_results:
                    if not r["uncertainty"]: r.update(uncertainty=shared_u, uncertainty_scope="table")
            result.extend(table_results)
    if not result: warnings.append("未识别到可结构化的测量结果表；请回看原页，不能据此认定证书缺项。")
    return result, warnings


def align_points(plans, observations):
    rows = []; used = set()
    # One result cannot uniquely cover distinct plan components/conditions at the same point.
    plan_scopes = {}
    for p in plans:
        key = point_key(p["point"], p["unit"])
        if key is not None:
            plan_scopes.setdefault((p["family"], key), set()).add((p["parameter"], p["component"], p["condition"]))
    for p in plans:
        key = point_key(p["point"], p["unit"])
        candidates = [o for o in observations if key is not None and o["family"] == p["family"] and point_key(o["point"], o["unit"]) == key]
        exact = [o for o in candidates if compact(p["component"]) and compact(p["component"]) == compact(o["condition"])]
        if exact: candidates = exact
        for o in candidates or [None]:
            if o: used.add(o["id"])
            if p["delivery_route"] in {"internal_calibration", "functional_check", "pre_use_check"}:
                signal, reason = "other_route", "方案规定其他实施方式，当前证书不承担此项目的覆盖。"
            elif p["parse_state"] == "unresolved": signal, reason = "unresolved", p["note"]
            elif not o: signal, reason = "plan_only", "当前识别结果中未找到同参数、同点位；需回看原页，尚不能认定缺项。"
            elif (p["component"] and compact(p["component"]) != compact(o["condition"])) or len(candidates)>1 or (not exact and len(plan_scopes.get((p["family"], key), ()))>1):
                signal, reason = "ambiguous", "点位相同，但部件或工况尚不能唯一对应；所列结果仅为候选。"
            elif p["condition"] or o["condition"]:
                signal, reason = "condition_pending", "参数与点位已对应；方案工况与证书适用条件仍需核对。"
            else: signal, reason = "paired", "参数与点位已对应；数值、判据与不确定度仍按原页及确认方案核对。"
            alerts = []
            # Highlight competing/different stated limits, without choosing a decision rule.
            primary = "\n".join(line for line in p["mpe"].splitlines() if not re.search(r"重复|零点", line))
            limits = {numeric(n) for n in re.findall(r"[±≤<]\s*(" + NUMBER + r")\s*%", primary)}
            if len(limits) > 1: alerts.append("方案有多种限值表述")
            if o and len(limits) == 1:
                report_limits = {numeric(n) for n in re.findall(r"[±≤<]\s*(" + NUMBER + r")\s*%", o["mpe"])}
                if report_limits and report_limits != limits: alerts.append("双方标注的 MPE 不同")
            if not p["uncertainty"]: alerts.append("方案未明确不确定度要求")
            rows.append({"id": f"M{len(rows)+1}", "plan": p, "certificate": o, "signal": signal, "reason": reason, "alerts": alerts, "status": "pending"})
    for o in observations:
        if o["id"] not in used:
            rows.append({"id": f"M{len(rows)+1}", "plan": None, "certificate": o, "signal": "report_only",
                         "reason": "证书识别结果中存在此项，当前方案未建立对应点位。", "alerts": [], "status": "pending"})
    return rows
