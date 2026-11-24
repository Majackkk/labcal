"""Colour an existing five-column Word copy without changing its cell text."""
from pathlib import Path
from xml.dom import minidom
from zipfile import ZipFile

from ..domain.assessment import DIMENSIONS, rule_dimension
from ..errors import LabcalError

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
MARKER = 'LabcalAssessmentLegend'


def self_summary(checks):
    from collections import Counter
    counts = Counter(c['state'] for c in checks if c['group'] != 'filing')
    names = {'no_anomaly_detected': '已检查范围未发现异常', 'confirmed_error': '发现错误',
             'candidate_concern': '存在疑点', 'unable_to_check': '无法判断',
             'not_checked': '未检查', 'not_applicable': '不适用'}
    return '证书自身：' + '；'.join(f'{name}{counts[key]}项' for key, name in names.items() if counts[key]) + '。'


def elements(node, tag):
    return list(node.getElementsByTagNameNS(W, tag))


def text(node):
    return ''.join(t.firstChild.data if t.firstChild else '' for t in elements(node, 't'))


def colour_run(run, colour, doc):
    prop = next((c for c in run.childNodes if c.nodeType == c.ELEMENT_NODE and c.localName == 'rPr'), None)
    if prop is None:
        prop = doc.createElementNS(W, 'w:rPr'); run.insertBefore(prop, run.firstChild)
    for old in elements(prop, 'color'): old.parentNode.removeChild(old)
    c = doc.createElementNS(W, 'w:color'); c.setAttributeNS(W, 'w:val', colour); prop.appendChild(c)


def compact_legend(prop, doc):
    for tag, attributes in [('spacing', {'before': '0', 'after': '40', 'line': '240', 'lineRule': 'exact'}),
                             ('snapToGrid', {'val': '0'})]:
        item = doc.createElementNS(W, 'w:' + tag)
        for key, value in attributes.items():
            item.setAttributeNS(W, 'w:' + key, value)
        prop.appendChild(item)


def paint(paragraph, base, overrides, doc):
    """Split only text runs; preserve original typography, breaks and bookmarks."""
    value = text(paragraph); colours = [base] * len(value)
    hits = [set() for _ in value]
    for quote, colour in overrides:
        for part in quote.split('\n'):
            if not part.strip(): continue
            start = 0
            while (start := value.find(part, start)) >= 0:
                for n in range(start, start+len(part)): hits[n].add(colour)
                start += len(part)
    for n, choices in enumerate(hits):
        if choices: colours[n] = next(iter(choices)) if len(choices) == 1 else '24303D'
    position = 0
    for run in elements(paragraph, 'r'):
        parent = run.parentNode
        props = [c for c in run.childNodes if c.nodeType == c.ELEMENT_NODE and c.localName == 'rPr']
        for child in list(run.childNodes):
            if child in props: continue
            pieces = []
            if child.nodeType == child.ELEMENT_NODE and child.localName == 't':
                content = ''.join(n.data for n in child.childNodes if n.nodeType == n.TEXT_NODE)
                start = 0
                while start < len(content):
                    colour = colours[position+start]; end = start+1
                    while end < len(content) and colours[position+end] == colour: end += 1
                    t = child.cloneNode(False); t.setAttribute('xml:space', 'preserve'); t.appendChild(doc.createTextNode(content[start:end]))
                    pieces.append((t, colour)); start = end
                position += len(content)
            else:
                pieces.append((child.cloneNode(True), base))
            for piece, colour in pieces:
                replacement = doc.createElementNS(W, 'w:r')
                for prop in props: replacement.appendChild(prop.cloneNode(True))
                replacement.appendChild(piece); colour_run(replacement, colour, doc)
                parent.insertBefore(replacement, run)
        parent.removeChild(run)


def _styled_xml(raw, record, appendix_table, self_checks=None):
    doc = minidom.parseString(raw)
    tables = elements(doc, 'tbl')
    if not 0 <= appendix_table < len(tables):
        raise LabcalError('CONFIRMATION_TABLE_REQUIRED', '没有对应的确认附页。')
    table = tables[appendix_table]
    for p in list(elements(doc, 'p')):
        if any(s.getAttributeNS(W, 'val') in {MARKER, MARKER+'Self'} for s in elements(p, 'pStyle')):
            p.parentNode.removeChild(p)
    # Legend labels are meanings, never pass/fail states or approval statements.
    p = doc.createElementNS(W, 'w:p'); prop = doc.createElementNS(W, 'w:pPr')
    style = doc.createElementNS(W, 'w:pStyle'); style.setAttributeNS(W, 'w:val', MARKER); prop.appendChild(style)
    compact_legend(prop, doc)
    keep = doc.createElementNS(W, 'w:keepNext'); prop.appendChild(keep); p.appendChild(prop)
    for key, item in DIMENSIONS.items():
        run = doc.createElementNS(W, 'w:r'); colour_run(run, item['colour'], doc)
        t = doc.createElementNS(W, 'w:t'); t.setAttribute('xml:space', 'preserve')
        t.appendChild(doc.createTextNode(item['label'] + '    ')); run.appendChild(t); p.appendChild(run)
    table.parentNode.insertBefore(p, table)
    summary = doc.createElementNS(W, 'w:p'); pr = doc.createElementNS(W, 'w:pPr')
    ps = doc.createElementNS(W, 'w:pStyle'); ps.setAttributeNS(W, 'w:val', MARKER+'Self'); pr.appendChild(ps)
    compact_legend(pr, doc)
    pr.appendChild(doc.createElementNS(W, 'w:keepNext')); summary.appendChild(pr)
    if self_checks is None:
        statement = '证书自身：本副本为排版验证，未重新执行证书诊断。'
    else:
        statement = self_summary(self_checks)
    run = doc.createElementNS(W, 'w:r'); colour_run(run, DIMENSIONS['certificate_self']['colour'], doc)
    t = doc.createElementNS(W, 'w:t'); t.appendChild(doc.createTextNode(statement)); run.appendChild(t); summary.appendChild(run)
    table.parentNode.insertBefore(summary, table)
    rows = [r for r in table.childNodes if r.nodeType == r.ELEMENT_NODE and r.localName == 'tr'][1:]
    for n, row in enumerate(rows, 1):
        cells = [c for c in row.childNodes if c.nodeType == c.ELEMENT_NODE and c.localName == 'tc']
        for paragraph in elements(cells[0], 'p'):
            paint(paragraph, DIMENSIONS['instrument_requirement']['colour'], [], doc)
        overrides = []
        for rule in record.get('technical_review', {}).get('rules', []):
            if rule['row_number'] != n: continue
            colour = DIMENSIONS[rule_dimension(rule)]['colour']
            overrides.extend((rule[key], colour) for key in ('requirement_quote', 'comparison_quote', 'judgment_quote', 'basis_scope_quote', 'reference_impact_quote') if rule.get(key))
        for paragraph in elements(cells[1], 'p'):
            paint(paragraph, DIMENSIONS['intended_use']['colour'], overrides, doc)
        # The same U cell may contain both method-use and instrument-reference
        # evaluations. Colour their explicit quotations without guessing labels.
        u_overrides = []
        for rule in record.get('technical_review', {}).get('rules', []):
            if rule['row_number'] != n or 'basis_role' not in rule:
                continue
            u_review = rule.get('reasoning', {}).get('uncertainty_review', {})
            colour = DIMENSIONS[rule_dimension(rule)]['colour']
            u_overrides.extend((u_review[key], colour) for key in
                              ('basis_quote', 'calculation_quote', 'judgment_quote') if u_review.get(key))
        if u_overrides:
            for paragraph in elements(cells[2], 'p'):
                paint(paragraph, '24303D', u_overrides, doc)
    return doc.toxml(encoding='utf-8')


def format_confirmation(source, output, record, appendix_table, *, self_checks=None):
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve() or output.exists():
        raise LabcalError('OUTPUT_EXISTS', '分色确认表须另存新副本，不覆盖原表或历史输出。')
    with ZipFile(source) as original:
        xml = _styled_xml(original.read('word/document.xml'), record, appendix_table, self_checks)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('xb') as stream, ZipFile(stream, 'w') as target:
            for item in original.infolist():
                target.writestr(item, xml if item.filename == 'word/document.xml' else original.read(item.filename))
    validate_colours(output, record, appendix_table, self_checks=self_checks)
    return {'path': str(output.resolve()), 'style': 'assessment-three-colours-1'}


def validate_colours(path, record, appendix_table, *, self_checks=None):
    with ZipFile(path) as archive: doc = minidom.parseString(archive.read('word/document.xml'))
    legends = [p for p in elements(doc, 'p') if any(s.getAttributeNS(W, 'val') == MARKER for s in elements(p, 'pStyle'))]
    if len(legends) != 1:
        raise LabcalError('CONFIRMATION_COLOURS_REQUIRED', '发布前请使用confirmation format生成带三维色系标识的副本。')
    for item in DIMENSIONS.values():
        if not any(item['label'] in text(r) and any(c.getAttributeNS(W, 'val') == item['colour'] for c in elements(r, 'color')) for r in elements(legends[0], 'r')):
            raise LabcalError('CONFIRMATION_COLOURS_REQUIRED', '三维色系标识缺失或颜色不一致。')
    if self_checks is not None:
        summaries = [text(p) for p in elements(doc, 'p') if any(s.getAttributeNS(W, 'val') == MARKER+'Self' for s in elements(p, 'pStyle'))]
        if summaries != [self_summary(self_checks)]:
            raise LabcalError('CONFIRMATION_SELF_SUMMARY', 'Word证书自身摘要与实际诊断记录不一致。')
    table = elements(doc, 'tbl')[appendix_table]
    values = {c.getAttributeNS(W, 'val') for c in elements(table, 'color')}
    required = {DIMENSIONS['instrument_requirement']['colour']}
    rules = record.get('technical_review', {}).get('rules', [])
    if not rules or any(rule_dimension(r) == 'intended_use' for r in rules):
        required.add(DIMENSIONS['intended_use']['colour'])
    if not required <= values:
        raise LabcalError('CONFIRMATION_COLOURS_REQUIRED', '确认附页的器具要求和用途未按约定分色。')
