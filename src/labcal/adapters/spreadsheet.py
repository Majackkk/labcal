"""Read-only OOXML adapter. Keeps lexical numbers, formula text and cached values."""
from pathlib import PurePosixPath
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

from ..errors import LabcalError
from ..util import normalize_text

PARSER_VERSION = "xlsx-stdlib/2.1"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
MAX_UNCOMPRESSED = 60 * 1024 * 1024


def column_number(name):
    result = 0
    for ch in name: result = result * 26 + ord(ch) - 64
    return result


def column_name(number):
    result = ""
    while number:
        number, part = divmod(number - 1, 26)
        result = chr(65 + part) + result
    return result


def read_workbook(path):
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or sum(x.file_size for x in archive.infolist()) > MAX_UNCOMPRESSED:
                raise LabcalError("WORKBOOK_TOO_LARGE", "工作簿解压体积过大或包含重复成员。")
            def xml(name):
                data = archive.read(name)
                if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
                    raise LabcalError("WORKBOOK_XML_UNSUPPORTED", "工作簿包含不支持的XML声明。")
                return ET.fromstring(data)
            book = xml("xl/workbook.xml")
            relationships = {r.attrib["Id"]: r for r in xml("xl/_rels/workbook.xml.rels")}
            strings = []
            if "xl/sharedStrings.xml" in names:
                strings = ["".join(si.itertext()) if not si.findall(".//s:t", NS) else "".join(t.text or "" for t in si.findall(".//s:t", NS))
                           for si in xml("xl/sharedStrings.xml").findall("s:si", NS)]
            sheets, cell_count = [], 0
            for sheet in book.findall("s:sheets/s:sheet", NS):
                rel = relationships[sheet.attrib[REL]]
                if rel.attrib.get("TargetMode") == "External":
                    raise LabcalError("WORKBOOK_EXTERNAL_SHEET", "不读取工作簿外部工作表。")
                target = rel.attrib["Target"]
                member = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target))
                if not member.startswith("xl/") or ".." in PurePosixPath(member).parts:
                    raise LabcalError("WORKBOOK_PATH_INVALID", "工作表引用超出工作簿范围。")
                root = xml(member); cells = []
                for node in root.findall("s:sheetData/s:row/s:c", NS):
                    address = node.attrib.get("r", "")
                    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", address)
                    if not match: raise LabcalError("WORKBOOK_CELL_INVALID", "工作簿单元格地址无效。")
                    col, row = column_number(match[1]), int(match[2])
                    if col > 16384 or row > 1048576: raise LabcalError("WORKBOOK_CELL_INVALID", "单元格地址超出范围。")
                    kind = node.attrib.get("t", "n"); value = node.find("s:v", NS)
                    value = value.text or "" if value is not None else ""
                    if kind == "s": value = strings[int(value)]
                    elif kind == "inlineStr": value = "".join(t.text or "" for t in node.findall("s:is//s:t", NS))
                    formula = node.find("s:f", NS)
                    item = {"address": address, "row": row, "column": col, "type": kind, "value": value}
                    if formula is not None:
                        item["formula"] = formula.text or ""
                        item["formula_attributes"] = dict(formula.attrib)
                        item["cached_value"] = value
                    if value != "" or formula is not None: cells.append(item)
                    cell_count += 1
                    if cell_count > 200000: raise LabcalError("WORKBOOK_TOO_LARGE", "单次接入最多20万个单元格。")
                sheets.append({"name": sheet.attrib["name"], "cells": cells,
                    "merges": [m.attrib["ref"] for m in root.findall("s:mergeCells/s:mergeCell", NS)],
                    "max_row": max((c["row"] for c in cells), default=0), "max_column": max((c["column"] for c in cells), default=0)})
            return sheets
    except LabcalError:
        raise
    except (zipfile.BadZipFile, KeyError, IndexError, ValueError, ET.ParseError) as exc:
        raise LabcalError("WORKBOOK_UNREADABLE", "无法读取有效的XLSX工作簿。", {"reason": str(exc)}) from exc


ALIASES = {
    "group": ["所属项目/设备模块", "设备模块", "设备名称", "所属项目", "设备组", "instrument"],
    "sequence": ["序号", "编号", "no"],
    "equipment_name": ["设备名称", "仪器名称", "仪器设备名称", "器具名称", "instrumentname", "equipmentname"],
    "manufacturer": ["厂家", "制造厂", "制造厂家", "生产厂家", "制造商", "生产厂商", "manufacturer"],
    "model": ["设备型号", "仪器型号", "型号", "型号规格", "规格型号", "model"],
    "equipment_number": ["设备编号", "仪器编号", "器具编号", "出厂编号", "出厂序列号", "资产编号", "设备序列号", "serialnumber"],
    "parameter": ["测量系统类别/参数名称", "参数名称", "校准参数", "检定参数", "校准/检定参数", "校准／检定参数", "校准/检定项目", "校准项目", "检定项目", "校准检定参数", "测量参数", "参数", "parameter"],
    "conditions": ["送校定制加密校准点/参数条件", "校准点", "参数条件", "校准范围", "范围/点位"],
    "limit": ["实验室允许误差mpe要求", "允许误差mpe要求", "允许误差(mpe)", "最大允许误差", "允许误差要求", "允许误差", "mpe", "技术要求", "限值"],
    "uncertainty": ["不确定度要求", "测量不确定度要求", "扩展不确定度要求", "不确定度", "测量不确定度", "uncertaintyrequirement", "uncertainty"],
    "method_claim": ["【检测方法依据】条款号与强制原文摘录", "检测方法依据", "方法依据"],
    "metrology_claim": ["【规程限制依据】检定规程/校准规范条款号与强制原文摘录", "规程限制依据", "计量依据"],
}

DISPLAY_ROLES = {"equipment_name": "equipment_name", "manufacturer": "manufacturer", "model": "model",
                 "equipment_number": "equipment_number", "parameter": "parameter", "mpe": "limit", "uncertainty": "uncertainty"}
IDENTITY_ROLES = ("equipment_name", "manufacturer", "model", "equipment_number")
MISSING_VALUES = {"", "-", "—", "--", "/", "未提供", "未明确", "待定"}


def explicit_identity(row, sheet_name):
    """Labelled metadata only; a title's model-looking suffix is not an identity fact."""
    result = {}
    label_roles = {alias: role for role in IDENTITY_ROLES for alias in ALIASES[role] if alias.isascii() is False}
    labels = sorted(label_roles, key=len, reverse=True)
    pattern = re.compile(r"(" + "|".join(re.escape(k) for k in labels) + r")\s*[:：]\s*(.*?)(?=(?:" + "|".join(re.escape(k) for k in labels) + r")\s*[:：]|$)", re.S)
    for col, cell in sorted(row.items()):
        if "formula" in cell: continue
        text = cell["value"].strip()
        for role in IDENTITY_ROLES:
            if normalize_text(text.rstrip(":：")) in {normalize_text(a) for a in ALIASES[role]}:
                adjacent = row.get(col + 1)
                if adjacent and "formula" not in adjacent and adjacent["value"].strip() not in MISSING_VALUES:
                    result[role] = (adjacent["value"].strip(), {"sheet": sheet_name, "cell": adjacent["address"], "origin": "label"})
        for match in pattern.finditer(text):
            value = match[2].strip(" \t\r\n;；，")
            if value not in MISSING_VALUES:
                result[label_roles[match[1]]] = (value, {"sheet": sheet_name, "cell": cell["address"], "origin": "label"})
    return result


def cell_at(rows, merges, number, col):
    cell = rows.get(number, {}).get(col)
    if cell is not None: return cell
    for merge in merges:
        match = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", merge)
        if match and int(match[2]) <= number <= int(match[4]) and column_number(match[1]) == col:
            return rows.get(int(match[2]), {}).get(column_number(match[1]))
    return None


def uncertainty_fragment(text):
    """Keep explicit uncertainty requirements, including their k context, as source text."""
    for line in str(text).splitlines():
        match = re.search(r"(?:扩展|测量|相对)?不确定度[^\n]*(?:[≤≥<>＝=]|要求|应|须)[^\n]*|\bU\s*(?:rel\s*)?[≤≥<>=＝][^\n]*", line, re.I)
        if match and re.search(r"\d", match.group(0)): return match.group(0).strip()
    return None


def extract_candidates(sheets, mapping=None):
    """Headers propose mappings; ambiguous or unknown sheets remain available raw."""
    mappings, candidates = {}, []
    for sheet in sheets:
        rows = {}
        for cell in sheet["cells"]: rows.setdefault(cell["row"], {})[cell["column"]] = cell
        selected = (mapping or {}).get(sheet["name"])
        if selected:
            if not isinstance(selected, dict) or not isinstance(selected.get("header_row"), int) or isinstance(selected["header_row"], bool):
                raise LabcalError("WORKBOOK_MAPPING_INVALID", "列映射需包含表头行号和列号。")
            selected = dict(selected)
            for role, col in selected.items():
                if role != "header_row" and (role not in ALIASES or not isinstance(col, int) or isinstance(col, bool) or col < 1 or col > sheet["max_column"]):
                    raise LabcalError("WORKBOOK_MAPPING_INVALID", "列映射包含不支持的字段或列号。")
            if selected["header_row"] not in rows or "parameter" not in selected:
                raise LabcalError("WORKBOOK_MAPPING_INVALID", "列映射需指定有效表头行与参数名称列。")
        else:
            for number, row in sorted(rows.items()):
                if number > 30: break
                proposed = {"header_row": number}
                for role, aliases in ALIASES.items():
                    matches = [col for col, c in row.items() if "formula" not in c and normalize_text(c["value"]) in {normalize_text(a) for a in aliases}]
                    if len(matches) == 1: proposed[role] = matches[0]
                if "parameter" in proposed:
                    selected = proposed; break
        if not selected: continue
        mappings[sheet["name"]] = selected
        section, section_cell, metadata = "", None, {}
        for number, row in sorted(rows.items()):
            if number < selected["header_row"]: metadata.update(explicit_identity(row, sheet["name"]))
        for number, row in sorted(rows.items()):
            if number <= selected["header_row"]: continue
            # A merged section is preserved separately; never materialised as a device.
            texts = [c["value"].strip() for c in row.values() if c["value"].strip()]
            if len(texts) == 1 and any(re.fullmatch(r"A"+str(number)+r":[A-Z]+"+str(number), merge) for merge in sheet["merges"]):
                section = texts[0]; section_cell = next(c for c in row.values() if c["value"].strip())["address"]
                metadata = explicit_identity(row, sheet["name"]); continue
            labelled = explicit_identity(row, sheet["name"])
            if labelled and len(texts) < 3:
                metadata.update(labelled); continue
            parameter_cell = row.get(selected["parameter"])
            if parameter_cell and "formula" in parameter_cell: continue
            if all("formula" in cell for cell in row.values()): continue
            if not parameter_cell and not any(role in selected and cell_at(rows, sheet["merges"], number, selected[role]) for role in (*IDENTITY_ROLES, "limit", "uncertainty")):
                continue
            values = {role: (cell_at(rows, sheet["merges"], number, col) or {}).get("value", "") for role, col in selected.items() if role != "header_row"}
            group = section or values.get("group", "").strip() or "未分组"
            fields, sources = {}, {}
            for field, role in DISPLAY_ROLES.items():
                cell = cell_at(rows, sheet["merges"], number, selected[role]) if role in selected else None
                value = cell["value"].strip() if cell else ""
                fields[field] = None if value in MISSING_VALUES else value
                if fields[field] is not None:
                    sources[field] = {"sheet": sheet["name"], "cell": cell["address"], "origin": "column"}
                elif role in metadata:
                    fields[field], sources[field] = metadata[role]
            if not fields["equipment_name"]:
                cell = cell_at(rows, sheet["merges"], number, selected["group"]) if "group" in selected else None
                fallback = section or (cell["value"].strip() if cell else "")
                if fallback and fallback not in MISSING_VALUES:
                    fields["equipment_name"] = fallback
                    sources["equipment_name"] = {"sheet": sheet["name"], "cell": section_cell if section else cell["address"], "origin": "section" if section else "column"}
            if not fields["uncertainty"]:
                for role in ("limit", "conditions", "metrology_claim", "method_claim"):
                    fragment = uncertainty_fragment(values.get(role, ""))
                    if fragment:
                        cell = cell_at(rows, sheet["merges"], number, selected[role])
                        fields["uncertainty"] = fragment
                        sources["uncertainty"] = {"sheet": sheet["name"], "cell": cell["address"], "origin": "inline"}
                        break
            candidates.append({"id": f"{sheet['name']}:{number}", "sheet": sheet["name"], "row": number,
                "group": group, "parameter": values["parameter"].strip() or "校准/检定参数待明确", "values": values,
                "fields": fields, "field_sources": sources,
                "anchor": f"'{sheet['name']}'!A{number}:{column_name(sheet['max_column'])}{number}",
                "formula_cells": [c["address"] for c in row.values() if "formula" in c], "state": "candidate"})
    return mappings, candidates
