"""Presentation intent for review copies, never a replacement measurement value."""
from ..errors import LabcalError
from .assessment import DIMENSION

TEXT = {'type': 'string', 'minLength': 1}
UNCERTAINTY_BASIS = {
    'type': 'object', 'required': ['references', 'application', 'role'],
    'properties': {
        'role': {'enum': ['requirement', 'guidance', 'supplementary_check']},
        'application': TEXT,
        'references': {'type': 'array', 'minItems': 1, 'items': {
            'type': 'object', 'required': ['document', 'clause', 'quote', 'source', 'checked_on', 'status'],
            'properties': {**{k: TEXT for k in ['document', 'clause', 'quote', 'source', 'checked_on']},
                           'status': {'const': 'valid'}}, 'additionalProperties': False}},
    }, 'additionalProperties': False,
}

MARKUP = {
    'type': 'object', 'required': ['action'],
    'properties': {
        'action': {'enum': ['replace', 'delete', 'insert', 'clarify', 'flag']},
        'condition': {'type': 'string', 'minLength': 1},
        'dimension': DIMENSION,
        'topic': {'enum': ['general', 'uncertainty']},
        'uncertainty_basis': UNCERTAINTY_BASIS,
    }, 'additionalProperties': False,
}


def validate_markup_location(location):
    """A proposed deletion needs a quoted, specific original-page target."""
    markup = location.get('markup', {'action': 'flag'})
    if (not isinstance(markup, dict) or set(markup) - set(MARKUP['properties'])
            or markup.get('action') not in MARKUP['properties']['action']['enum']
            or 'dimension' in markup and markup['dimension'] not in DIMENSION['enum']
            or 'condition' in markup and (not isinstance(markup['condition'], str) or not markup['condition'].strip())):
        raise LabcalError('MARKUP_INVALID', '批改操作或核实要求无效。')
    if 'topic' in markup and markup['topic'] not in {'general', 'uncertainty'}:
        raise LabcalError('MARKUP_INVALID', '批注主题无效。')
    if markup.get('topic') == 'uncertainty' or 'uncertainty_basis' in markup:
        from .contracts import _validate
        from datetime import date
        basis = markup.get('uncertainty_basis')
        if markup.get('topic') != 'uncertainty' or not basis:
            raise LabcalError('UNCERTAINTY_BASIS_REQUIRED', '不确定度批注须列明文件版本、条款、原文、来源、有效性核查日期和本次应用。')
        errors = []
        _validate(basis, UNCERTAINTY_BASIS, '$.markup.uncertainty_basis', errors)
        if errors:
            raise LabcalError('UNCERTAINTY_BASIS_INVALID', '不确定度条款依据不完整或文件状态未核实有效。', {'errors': errors})
        for ref in basis['references']:
            if any(not ref[k].strip() for k in ['document', 'clause', 'quote', 'source', 'checked_on']) or not basis['application'].strip():
                raise LabcalError('UNCERTAINTY_BASIS_REQUIRED', '不确定度依据及本次应用不得为空白。')
            try:
                date.fromisoformat(ref['checked_on'])
            except ValueError as exc:
                raise LabcalError('UNCERTAINTY_BASIS_DATE', '有效性核查日期须为有效的YYYY-MM-DD日期。') from exc
    if markup.get('action') in {'replace', 'delete'}:
        if location.get('kind') in {'document', 'section'} or not str(location.get('quote', '')).strip():
            raise LabcalError('MARKUP_TARGET_REQUIRED', '斜线批改需明确具体原文及其区域；文件级或章节级提示不能作为删除对象。')


def uncertainty_basis_text(location):
    """One projection for both visible B3 cards and full PDF annotation notes."""
    basis = location.get('markup', {}).get('uncertainty_basis')
    if not basis:
        return ''
    roles = {'requirement': '适用要求', 'guidance': '技术参考', 'supplementary_check': '补充核查'}
    lines = ['不确定度依据（' + roles[basis['role']] + '）']
    for ref in basis['references']:
        lines.extend([ref['document'] + '｜' + ref['clause'], '原文：“' + ref['quote'] + '”',
                      '来源：' + ref['source'], '有效性核查：' + ref['checked_on'] + '，有效'])
    lines.append('本次应用：' + basis['application'])
    return '\n'.join(lines)
