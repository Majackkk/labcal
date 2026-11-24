"""Conservative migration: retain original rows; do not infer approved numbers."""
import re


def plain(value):
    value = re.sub(r"<br\s*/?>", " / ", value, flags=re.I)
    return re.sub(r"[*`_]", "", value).strip()


def split_row(line):
    cells, current, escaped, in_code = [], [], False, False
    for char in line.strip().strip("|"):
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "`":
            in_code = not in_code
            current.append(char)
        elif char == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    return cells


def parameter_rows(text):
    lines, result, headers, parameter_index = text.splitlines(), [], None, None
    for index, line in enumerate(lines):
        if not line.strip().startswith("|"):
            headers, parameter_index = None, None
            continue
        cells = split_row(line)
        if all(re.fullmatch(r"\s*:?-+:?\s*", cell) for cell in cells):
            continue
        candidates = [i for i, cell in enumerate(cells) if plain(cell) in {"测量系统类别", "测量参数", "参数", "校准参数"}]
        if candidates and any(any(word in cell for word in ["校准点", "范围/点", "参数条件", "允许误差", "MPE"]) for cell in cells):
            headers, parameter_index = cells, candidates[0]
            continue
        if headers is not None and len(cells) == len(headers) and plain(cells[parameter_index]):
            result.append({"line": index + 1, "parameter": plain(cells[parameter_index]),
                           "row_id": plain(cells[0]), "columns": dict(zip(headers, cells))})
    return result


def candidate_from_markdown(text, *, title, equipment_id, source_id):
    rows = parameter_rows(text)
    if not rows:
        rows = [{"line": 1, "parameter": "原方案待结构化", "row_id": "1", "columns": {"全文": text}}]
    requirements = []
    for index, row in enumerate(rows, 1):
        requirements.append({"id": f"R{index:03d}", "parameter": row["parameter"], "unit": "",
                             "delivery_route": "unresolved", "coverage_policy": "unresolved",
                             "points": [], "report_fields": [],
                             "evidence": [{"source_id": source_id, "claim_kind": "legacy", "anchor": f"line:{row['line']}"}],
                             "raw": row})
    return {"title": title, "equipment_id": equipment_id, "origin": "legacy_markdown",
            "requirements": requirements, "source_ids": [source_id],
            "open_items": [{"code": "LEGACY_NORMALIZATION_REQUIRED", "message": "旧方案参数尚需原件和实际配置核对，不能直接建立用户确认。"}]}


def compare_parameter_documents(plan_text, table_text):
    plan, table = parameter_rows(plan_text), parameter_rows(table_text)
    grouped = {}
    for row in table:
        key = re.sub(r"[^0-9]", "", row["row_id"]).lstrip("0") or row["row_id"]
        grouped.setdefault(key, []).append(row)
    result = []
    for row in plan:
        key = re.sub(r"[^0-9]", "", row["row_id"]).lstrip("0") or row["row_id"]
        other = grouped.get(key, [])
        if other and {x["parameter"] for x in other} != {row["parameter"]}:
            result.append({"code": "LEGACY_PARAMETER_MISMATCH", "row_id": row["row_id"],
                           "plan_parameter": row["parameter"], "table_parameters": [x["parameter"] for x in other],
                           "message": "方案与速查表的参数对象表述不同，需要核对，未自动采纳任何一方。"})
    return result

