"""Portable diagnosis outputs from a frozen view, using Python's standard library.

The desktop export must work without Codex or a spreadsheet runtime. Cells are
inline strings to preserve decimal text and prevent formulas from source files.
"""
import csv
import html
import json
from pathlib import Path
import math
import re
from xml.sax.saxutils import escape
import zipfile

from ..application.report_diagnosis import STATE_LABELS
from ..util import write_json

STATUS={"passed":"符合","failed":"不符合","pending":"待核对","not_applicable":"不适用"}
SIGNAL={"paired":"点位已对应","condition_pending":"工况待核对","ambiguous":"部件待对应","plan_only":"方案点未找到",
    "report_only":"方案外识别点","uncompared":"尚未建立对照","unresolved":"点位待拆分","other_route":"其他实施方式"}
HEADERS=["报告","参数","部件/工况","方案点位","方案MPE","方案U要求","证书点位","实测值","误差/特性值","重复性",
    "证书MPE","证书U","对应状态","技术结论","判定原因","原页","方案原文位置","原页位置","判定记录"]


def point(value): return (value.get("point","")+" "+value.get("unit","")).strip()


def comparison_rows(views):
    rows=[]
    for view in views.values():
        for row in view["point_matrix"]["rows"]:
            p=row["plan"] or {}; o=row["certificate"] or {}; loc=o.get("anchor",{}).get("location",{})
            conclusion=STATUS[row["status"]] if row["status"]!="pending" else STATE_LABELS[row["decision_state"]]
            rows.append([view["report"]["file_name"],p.get("parameter") or o.get("parameter"),
                " / ".join(filter(None,[p.get("component"),p.get("condition"),o.get("condition")])),point(p),p.get("mpe"),p.get("uncertainty"),
                point(o),o.get("measured"),o.get("error"),o.get("repeatability"),o.get("mpe"),o.get("uncertainty"),
                SIGNAL[row["signal"]],conclusion,row["decision_reason"],str(loc.get("page","")),p.get("source"),
                json.dumps(loc,ensure_ascii=False) if loc else "",json.dumps(row["decision_evidence"],ensure_ascii=False)])
    return [["-" if v is None or v=="" else str(v) for v in r] for r in rows]


def _cell_name(index):
    result=""
    while index: index,rem=divmod(index-1,26);result=chr(65+rem)+result
    return result


def write_xlsx(path, sheets):
    """Small OOXML output adapter; no formulas, macros, links or editable limits."""
    ns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel="http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as z:
        overrides=''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(sheets)+1))
        z.writestr('[Content_Types].xml','<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+overrides+'</Types>')
        z.writestr('_rels/.rels',f'<Relationships xmlns="{rel}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        names=''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i,(name,rows,widths) in enumerate(sheets,1))
        z.writestr('xl/workbook.xml',f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{names}</sheets></workbook>')
        relations=''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1))
        z.writestr('xl/_rels/workbook.xml.rels',f'<Relationships xmlns="{rel}">{relations}<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml',f'<styleSheet xmlns="{ns}"><fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Microsoft YaHei"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF285A9B"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        for si,(_,rows,widths) in enumerate(sheets,1):
            cells=[]
            for ri,row in enumerate(rows,1):
                clean=lambda value:re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '\ufffd', str(value))
                content=''.join(f'<c r="{_cell_name(ci)}{ri}" t="inlineStr" s="{1 if ri==1 else 0}"><is><t xml:space="preserve">{escape(clean(value))}</t></is></c>' for ci,value in enumerate(row,1))
                lines=max(sum(max(1,math.ceil(sum(2 if ord(c)>255 else 1 for c in part)/max(8,widths[ci]-2))) for part in str(value).splitlines()) or 1 for ci,value in enumerate(row))
                height=30 if ri==1 else max(36,min(409,lines*15+8))
                cells.append(f'<row r="{ri}" ht="{height}" customHeight="1">{content}</row>')
            end=f'{_cell_name(max(len(r) for r in rows))}{len(rows)}'
            cols=''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i,w in enumerate(widths,1))
            z.writestr(f'xl/worksheets/sheet{si}.xml',f'<worksheet xmlns="{ns}"><dimension ref="A1:{end}"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="20"/><cols>{cols}</cols><sheetData>{"".join(cells)}</sheetData><autoFilter ref="A1:{end}"/><pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/><pageSetup orientation="landscape" paperSize="9"/></worksheet>')


def export_diagnosis_views(output, views):
    output=Path(output);rows=comparison_rows(views)
    write_json(output/'diagnosis.json',{"kind":"fixed_report_diagnosis_export","views":views,"technical_approval":False})
    # CSV values starting with a formula prefix are protected for spreadsheet opening.
    with (output/'comparison.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        csv.writer(stream).writerows([["'"+v if v.startswith(('=','+','-','@')) else v for v in row] for row in [HEADERS]+rows])
    summary=[["报告","诊断维度","状态","说明","下一步"]]
    sources=[["报告","字段","识别值","原页位置","方案基准/版本","诊断输入摘要"]]
    for view in views.values():
        for d in view['diagnostics']: summary.append([view['report']['file_name'],d['title'],STATUS[d['status']] if d['status']!='pending' else d['state_label'],d['reason'],d['next_action']])
        for key,values in view['fields'].items():
            for datum in values: sources.append([view['report']['file_name'],key,str(datum['value']),json.dumps(datum['location'],ensure_ascii=False),view['point_matrix']['basis']['label'],view['input_hash']])
    write_xlsx(output/'comparison.xlsx',[("诊断摘要",summary,[34,25,16,65,65]),("逐点对照",[HEADERS]+rows,[34,24,25,16,35,23,16,20,22,20,23,26,20,16,55,10,30,45,45]),("固定输入",sources,[34,25,55,65,45,68])])
    h=lambda value:html.escape(str(value))
    sections=[]
    for view in views.values():
        notes=''.join(f'<li>{h(p["text"])}</li>' for p in view['preconditions'])
        dimensions=''.join('<tr>'+''.join(f'<td>{h(v)}</td>' for v in row[1:])+'</tr>' for row in summary[1:] if row[0]==view['report']['file_name'])
        sections.append(f'<section><h2>{h(view["report"]["file_name"])}</h2><p>{h(view["point_matrix"]["basis"]["label"])}</p><ul>{notes}</ul><table><thead><tr><th>维度</th><th>状态</th><th>说明</th><th>下一步</th></tr></thead><tbody>{dimensions}</tbody></table></section>')
    # The printable view has essential columns; Excel/JSON retain every field.
    visible=[1,2,3,4,5,6,7,8,11,12,13,14,15]
    table='<table><thead><tr>'+''.join(f'<th>{h(HEADERS[i])}</th>' for i in visible)+'</tr></thead><tbody>'+''.join('<tr>'+''.join(f'<td>{h(row[i])}</td>' for i in visible)+'</tr>' for row in rows)+'</tbody></table>'
    (output/'diagnosis.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>报告完整诊断</title><style>body{font:13px/1.5 Arial,"PingFang SC",sans-serif;margin:24px;color:#20334c}h1{border-bottom:3px solid #285a9b}table{border-collapse:collapse;width:100%;margin:12px 0}th,td{border:1px solid #c4cede;padding:7px;text-align:left;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#e7eef8}section{break-inside:avoid}@media print{body{margin:8mm}table{font-size:9px}}</style><h1>报告完整诊断</h1><p>固定快照输出；原件与方案未改写。基础审查或候选对照不代表设备技术批准。</p>'+''.join(sections)+'<h2>逐点对照</h2>'+table+'</html>',encoding='utf-8')
    (output/'summary.md').write_text('# 报告诊断摘要\n\n固定快照输出；不构成自动技术批准。\n\n'+'\n\n'.join('方案：'+v['point_matrix']['basis']['label']+'\n'+'\n'.join('- '+p['text'] for p in v['preconditions']) for v in views.values())+'\n\n完整逐点结果见配套Excel、CSV及诊断HTML。\n',encoding='utf-8')
