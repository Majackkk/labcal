"""B3 review copies: diagonal edit marks, origin dots and field-based side cards.

Coordinates are always those of the displayed source page. PDF dependencies stay
inside this adapter; inputs carry edit intent, not replacement measurement facts.
"""
from pathlib import Path
import math
from functools import lru_cache

from ..domain.report_markup import validate_markup_location, uncertainty_basis_text
from ..domain.assessment import DIMENSIONS, assessment_style, rgb
from ..errors import LabcalError

RENDERER_VERSION = 'b3-uncertainty-basis-4'
GUTTER = 330
FONT_FILE = next((str(p) for p in map(Path, (
    '/System/Library/Fonts/STHeiti Light.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
    'C:/Windows/Fonts/msyh.ttc',
)) if p.is_file()), None)
FONT = 'review_b3' if FONT_FILE else 'china-s'
SIZE = 10
LEADING = 14
LABEL_WIDTH = 76
DOT_RADIUS = 2.5
DOT_GAP = 4
RED = (.76, .13, .19)
BLUE = (.15, .35, .57)
AMBER = (.60, .35, .05)
PURPLE = (.43, .27, .57)
INK = (.14, .18, .23)
GRAY = (.41, .46, .51)
BORDER = (.79, .83, .87)
ACTIONS = {'replace': '建议替换', 'delete': '建议删除', 'insert': '建议补充',
           'clarify': '建议澄清', 'flag': '定位核实'}


@lru_cache(maxsize=1)
def _font(fitz):
    return fitz.Font(fontfile=FONT_FILE) if FONT_FILE else fitz.Font('china-s')


def _wrap(text, width, fitz, size=SIZE):
    lines = []
    for paragraph in str(text).split('\n'):
        line = ''; length = 0
        for character in paragraph:
            extra = _font(fitz).text_length(character, fontsize=size)
            if line and length + extra > width:
                lines.append(line); line = ''; length = 0
            line += character; length += extra
        lines.append(line)
    return lines


def _text(page, x, y, lines, *, color=INK, size=SIZE):
    for line in lines:
        page.insert_text((x, y + size), line, fontname=FONT, fontsize=size, color=color)
        y += LEADING


def _card_parts(finding, width, fitz, capacity):
    """Split exceptionally long cards without truncating advice or reasons."""
    loc = finding['location']; markup = loc.get('markup', {})
    action = markup.get('action', 'flag')
    confirmed = finding['certainty'] == 'confirmed' or finding.get('resolution', {}).get('action') == 'confirmed'
    dimension = finding.get('dimension') or markup.get('dimension')
    # Legacy plan comparisons must not be presented as a method-use judgement.
    color = rgb(dimension) if dimension else GRAY
    condition = markup.get('condition') or ('按真实记录处理，保留处理依据。' if confirmed else
                                           '先核对原文、定义及原始记录，再决定是否更改。')
    if loc.get('kind') == 'document':
        condition += '\n文件级提示，圆点不代表具体错误单元格。'
    elif action == 'flag':
        condition += '\n仅定位原文区域，不表示该区域全部内容有误。'
    label = assessment_style(dimension)['label'] if dimension else '未分类／方案对照'
    heading = f"{label} · {finding['id']} · {ACTIONS[action]} · " + ('已确认问题' if confirmed else '待核实')
    titles = _wrap(heading + '\n' + finding['title'], width - 24, fitz, 10)
    if len(titles) > 5:
        titles = titles[:4] + ['（完整标题见批注全文）']
    head_height = len(titles) * LEADING + 16
    reason = finding['message'] + '\n依据：' + finding['basis']
    if uncertainty_basis_text(loc):
        reason += '\n' + uncertainty_basis_text(loc)
    rows = [('修改建议', finding['suggestion'], RED if action in {'replace', 'delete'} else BLUE), ('核实要求', condition, AMBER),
            ('原因', reason, PURPLE)]
    parts = []; part = []; used = head_height
    text_width = width - LABEL_WIDTH - 22
    max_lines = max(1, int((capacity - head_height - 18) // LEADING))
    for label, value, field_color in rows:
        lines = _wrap(value, text_width, fitz)
        for offset in range(0, len(lines), max_lines):
            chunk = lines[offset:offset + max_lines]
            row = {'label': label + ('（续）' if offset else ''), 'lines': chunk,
                   'color': field_color, 'height': max(36, len(chunk) * LEADING + 18)}
            if part and used + row['height'] > capacity:
                parts.append(part); part = []; used = head_height
            part.append(row); used += row['height']
    if part: parts.append(part)
    return [{'finding': finding, 'rows': rows, 'titles': titles, 'head_height': head_height,
             'height': head_height + sum(r['height'] for r in rows), 'color': color,
             'confirmed': confirmed, 'action': action, 'part': n + 1, 'parts': len(parts)}
            for n, rows in enumerate(parts)]


def _draw_card(page, rect, card, fitz):
    page.draw_rect(rect, color=BORDER, fill=(1, 1, 1), width=.65)
    y = rect.y0 + card['head_height']
    page.draw_rect(fitz.Rect(rect.x0, rect.y0, rect.x1, y), color=None, fill=card['color'])
    _text(page, rect.x0 + 12, rect.y0 + 8, card['titles'], color=(1, 1, 1))
    for row in card['rows']:
        page.draw_rect(fitz.Rect(rect.x0, y, rect.x0 + LABEL_WIDTH, y + row['height']),
                       color=None, fill=(.95, .96, .975))
        page.draw_line((rect.x0, y), (rect.x1, y), color=BORDER, width=.5)
        _text(page, rect.x0 + 10, y + 9, [row['label']], color=row['color'], size=9)
        _text(page, rect.x0 + LABEL_WIDTH + 10, y + 9, row['lines'])
        y += row['height']


def _diagonal_targets(page, rect, quote, fitz):
    """Prefer exact native text; scanned targets use the visually checked rectangle."""
    matches = page.search_for(quote, clip=rect + (-2, -2, 2, 2)) if quote.strip() else []
    if matches:
        return matches
    if page.get_text('text', clip=rect).strip():
        raise LabcalError('MARKUP_TEXT_MISMATCH', '拟批改原文与定位区域的PDF文字不一致，请核对原页后再标记。')
    # Never put a long deletion slash through an entire table or paragraph.
    if rect.height > 32 or rect.width > 180:
        raise LabcalError('MARKUP_TARGET_TOO_BROAD', '拟替换／删除需定位具体文字或单元格；不能用整块表格画斜线。')
    return [rect]


def _leader_origin(page, target, width, height, protected, reserved, fitz):
    """Put the entire dot outside source text, with a visible safety gap.

Native text and known scan targets are protected geometrically. A small raster
check also avoids grid lines or nearby glyphs on scanned pages. When crowded,
the added gutter provides a safe fallback instead of painting over the report.
"""
    radius = DOT_RADIUS
    offset = radius + DOT_GAP + 1
    cx, cy = (target.x0 + target.x1)/2, (target.y0 + target.y1)/2
    candidates = [(target.x1 + offset, cy), (target.x1 + offset, target.y1 + offset),
                  (cx, target.y1 + offset), (target.x1 + offset, target.y0 - offset),
                  (cx, target.y0 - offset), (target.x0 - offset, cy)]
    obstacles = [fitz.Rect(w[:4]) for w in page.get_text('words', clip=fitz.Rect(0, 0, width, height))]
    obstacles += protected + reserved
    for x, y in candidates:
        footprint = fitz.Rect(x-radius-.5, y-radius-.5, x+radius+.5, y+radius+.5)
        if footprint.x0 < 1 or footprint.y0 < 1 or footprint.x1 > width-1 or footprint.y1 > height-1:
            continue
        if any(footprint.intersects(r + (-DOT_GAP, -DOT_GAP, DOT_GAP, DOT_GAP)) for r in obstacles):
            continue
        pixels = page.get_pixmap(clip=footprint, matrix=fitz.Matrix(1.5, 1.5), colorspace=fitz.csGRAY, annots=False)
        if pixels.samples and min(pixels.samples) < 190:
            continue
        reserved.append(footprint)
        return x, y
    # No free nearby space: stay outside the original page, not on its data.
    x = width + 7
    for dy in (0, 10, -10, 20, -20, 30, -30):
        y = min(height-radius-1, max(radius+1, cy+dy))
        footprint = fitz.Rect(x-radius, y-radius, x+radius, y+radius)
        if not any(footprint.intersects(r + (-1, -1, 1, 1)) for r in reserved):
            reserved.append(footprint)
            return x, y
    reserved.append(fitz.Rect(x-radius, cy-radius, x+radius, cy+radius))
    return x, cy


def annotate_callouts(path, output, findings):
    from .pdf import fitz_module
    fitz = fitz_module(); output = Path(output)
    if output.exists() or output.resolve() == Path(path).resolve():
        raise LabcalError('OUTPUT_EXISTS', '批注副本不能覆盖原件或已有输出。')
    with fitz.open(path) as source, fitz.open() as target:
        active = [f for f in findings if f.get('resolution', {}).get('action') not in {'dismissed', 'resolved'}]
        for finding in active:
            loc = finding['location']; box = loc['bbox']
            validate_markup_location(loc)
            if (isinstance(loc['page'], bool) or not isinstance(loc['page'], int)
                    or not 1 <= loc['page'] <= len(source) or len(box) != 4
                    or not all(not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1 for v in box)
                    or box[0] >= box[2] or box[1] >= box[3]):
                raise LabcalError('LOCATION_INVALID', '批注位置必须是原页上的有效比例坐标。')
        # Retain visible original stamps/widgets without touching the signed source.
        source.bake(annots=True, widgets=True)
        page_map = []
        for index, original in enumerate(source):
            width, height = original.rect.width, original.rect.height
            page_height = max(360, height); top = 86; bottom = page_height - 24
            cards = []
            for finding in active:
                if finding['location']['page'] != index + 1: continue
                box = finding['location']['bbox']
                for card in _card_parts(finding, GUTTER - 28, fitz, bottom - top):
                    card['y'] = (box[1] + box[3]) / 2 * height
                    cards.append(card)
            cards.sort(key=lambda c: c['y'])
            groups = []; group = []; used = 0
            for card in cards:
                if group and used + card['height'] + 14 > bottom - top:
                    groups.append(group); group = []; used = 0
                group.append(card); used += card['height'] + 14
            if group: groups.append(group)
            if not groups: groups = [[]]
            for continuation, group in enumerate(groups):
                page = target.new_page(width=width + GUTTER, height=page_height)
                page.insert_font(fontname=FONT, fontfile=FONT_FILE)
                page_map.append({'review_page': len(target), 'source_page': index + 1, 'continuation': continuation + 1})
                page.show_pdf_page(fitz.Rect(0, 0, width, height), source, index)
                page.draw_rect(fitz.Rect(width, 0, width + GUTTER, page_height), color=BORDER, fill=(.965, .975, .985), width=.6)
                page.insert_text((width + 14, 22), '报告批改 · B3', fontname=FONT, fontsize=13, color=INK)
                page.insert_text((width + 14, 39), f'原件第 {index+1}/{len(source)} 页 · 标注 {continuation+1}/{len(groups)}', fontname=FONT, fontsize=8, color=GRAY)
                page.insert_text((width + 14, 55), '斜线：拟替换／删除   圆点：原文定位', fontname=FONT, fontsize=8, color=GRAY)
                for offset, (dimension, style) in enumerate(DIMENSIONS.items()):
                    page.insert_text((width+14+offset*100, 72), style['label'], fontname=FONT, fontsize=8, color=rgb(dimension))
                cursor = top
                protected = [fitz.Rect(f['location']['bbox'][0]*width, f['location']['bbox'][1]*height,
                                       f['location']['bbox'][2]*width, f['location']['bbox'][3]*height)
                             for f in active if f['location']['page'] == index+1]
                reserved = []
                for number, card in enumerate(group):
                    finding = card['finding']; loc = finding['location']; box = loc['bbox']; color = card['color']
                    remaining = sum(c['height'] + 14 for c in group[number:]) - 14
                    y = max(cursor, min(card['y'] - card['height']/2, bottom - remaining))
                    rect = fitz.Rect(width + 14, y, width + GUTTER - 14, y + card['height'])
                    displayed = fitz.Rect(box[0]*width, box[1]*height, box[2]*width, box[3]*height)
                    content = '\n'.join([finding['title'], '状态：' + ('已确认问题' if card['confirmed'] else '待核实'),
                        '修改建议：' + finding['suggestion'], '核实要求：' + loc.get('markup', {}).get('condition', '按真实记录核对处理。'),
                        '原因：' + finding['message'], '依据：' + finding['basis'], uncertainty_basis_text(loc),
                        '维度：' + (assessment_style(finding.get('dimension') or loc.get('markup', {}).get('dimension'))['label']
                                  if finding.get('dimension') or loc.get('markup', {}).get('dimension') else '未分类／方案对照')])
                    if card['action'] in {'replace', 'delete'}:
                        for mark_rect in _diagonal_targets(page, displayed, loc['quote'], fitz):
                            mark = page.add_line_annot((mark_rect.x0, mark_rect.y1), (mark_rect.x1, mark_rect.y0))
                            mark.set_colors(stroke=color); mark.set_border(width=1.25)
                            mark.set_info(title='智能体批改建议', subject=finding['id'], content=content); mark.update()
                    x0, y0 = _leader_origin(page, displayed, width, height, protected, reserved, fitz)
                    # Open polylines are essential: no closing diagonal across source text.
                    lane = width + 5 + (number % 3)*2
                    knee = min(width + 10, x0 + 5)
                    exit_y = max(y0, min(height - 3, displayed.y1 + 4))
                    shape = page.new_shape()
                    shape.draw_polyline([(x0, y0), (knee, y0), (knee, exit_y),
                                         (lane, exit_y), (lane, y+20), (rect.x0, y+20)])
                    shape.finish(color=color, width=.85, closePath=False); shape.commit()
                    page.draw_circle((x0, y0), DOT_RADIUS, color=color, fill=color, width=.5)
                    _draw_card(page, rect, card, fitz)
                    if card['parts'] > 1:
                        page.insert_text((width+14, page_height-17), f"{finding['id']} 分段 {card['part']}/{card['parts']}", fontname=FONT, fontsize=7, color=GRAY)
                    note = page.add_text_annot((width + GUTTER - 24, y + 4), content)
                    note.set_info(title='智能体批改建议', subject=finding['id']); note.set_colors(stroke=color)
                    note.set_flags(note.flags | fitz.PDF_ANNOT_IS_NO_VIEW); note.update()
                    cursor = y + card['height'] + 14
                if not group:
                    _text(page, width+14, top, ['本页无定位批改项。', '请结合完整诊断记录阅读。'], color=GRAY)
                page.insert_text((width+14, page_height-7), '批改建议副本 · 原件及其签署证据另行留存', fontname=FONT, fontsize=7, color=GRAY)
        target.set_metadata({'title': '报告批改建议副本 · B3', 'subject': RENDERER_VERSION + '; original signature not revalidated', 'keywords': 'labcal_review_copy,b3'})
        target.save(output, garbage=3, deflate=True)
        return {'renderer': RENDERER_VERSION, 'source_pages': len(source), 'review_pages': len(target), 'page_map': page_map}
