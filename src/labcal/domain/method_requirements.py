"""Bounded Decimal expressions and narrowly scoped method comparisons. No eval."""
import ast
from decimal import Decimal, DecimalException, localcontext

from .rules import number, convert, UNITS
from ..errors import LabcalError

ENGINE = 'method-comparison-1.3'


def decimal(value):
    if not isinstance(value, (str, int)) or len(str(value)) > 80:
        raise LabcalError('METHOD_NUMBER_INVALID', '数值需要有限长度的十进制文本。')
    result = number(value)
    if result and not -30 <= result.adjusted() <= 30:
        raise LabcalError('METHOD_NUMBER_INVALID', '数值超出当前计算支持范围。')
    return result


def expression_value(expression, values, output_unit):
    """SI dimensions are tracked; only + - * / and small integer powers allowed."""
    if len(expression) > 500:
        raise LabcalError('METHOD_EXPRESSION_INVALID', '公式过长。')
    def quantity(value, unit):
        if unit == '1': return decimal(value), {}
        if unit not in UNITS: raise LabcalError('UNIT_INCOMPATIBLE', '公式单位尚不支持。')
        dim, scale = UNITS[unit]
        return decimal(value) * Decimal(scale), {dim: 1}
    def combine(a, b, sign):
        dims = dict(a)
        for k, v in b.items(): dims[k] = dims.get(k, 0) + sign * v
        return {k: v for k, v in dims.items() if v}
    def walk(node):
        if isinstance(node, ast.Name):
            if node.id not in values: raise LabcalError('METHOD_INPUT_MISSING', '公式缺少条件：' + node.id)
            return quantity(values[node.id]['value'], values[node.id]['unit'])
        if isinstance(node, ast.Constant) and type(node.value) is int: return decimal(node.value), {}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            val, dim = walk(node.operand)
            return (-val if isinstance(node.op, ast.USub) else val), dim
        if isinstance(node, ast.BinOp):
            a, ad = walk(node.left); b, bd = walk(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)) and ad == bd:
                return (a + b if isinstance(node.op, ast.Add) else a - b), ad
            if isinstance(node.op, ast.Mult): return a * b, combine(ad, bd, 1)
            if isinstance(node.op, ast.Div): return a / b, combine(ad, bd, -1)
            if isinstance(node.op, ast.Pow) and not bd and b == int(b) and -4 <= b <= 4:
                return a ** int(b), {k: v * int(b) for k, v in ad.items() if v * int(b)}
        raise LabcalError('METHOD_EXPRESSION_INVALID', '公式包含不支持的运算或不兼容量纲。')
    try:
        tree = ast.parse(expression, mode='eval')
        if len(list(ast.walk(tree))) > 100: raise ValueError()
        with localcontext() as ctx:
            ctx.prec = 34
            value, dim = walk(tree.body); scale, target_dim = quantity('1', output_unit)
            if dim != target_dim: raise LabcalError('UNIT_INCOMPATIBLE', '公式输出量纲与要求单位不一致。')
            return decimal(str(value / scale))
    except (SyntaxError, ValueError, DecimalException, OverflowError, RecursionError) as exc:
        raise LabcalError('METHOD_EXPRESSION_INVALID', '公式或条件不能可靠计算。') from exc


def limit_value(requirement, conditions):
    c = requirement['criterion']
    if c['operator'] == 'none': return None
    if 'expression' not in c:
        return c['value'] if c['operator'] == 'eq' else decimal(c['value'])
    values = dict(c.get('constants', {}))
    for key, spec in c.get('inputs', {}).items():
        if key not in conditions: raise LabcalError('METHOD_INPUT_MISSING', '缺少实际条件：' + key)
        datum = conditions[key]
        decimal(datum['value'])
        if spec.get('positive', False) and decimal(datum['value']) <= 0:
            raise LabcalError('METHOD_INPUT_INVALID', '条件必须大于零：' + key)
        values[key] = {'value': str(convert(datum['value'], datum['unit'], spec['unit'])), 'unit': spec['unit']}
    return expression_value(c['expression'], values, c['unit'])


def compare(requirement, use, observation):
    result = {'state': 'insufficient_evidence', 'reason': '', 'engine': ENGINE}
    def pending(reason): return {**result, 'reason': reason}
    if use['state'] != 'user_adopted': return pending('设备用途关联或实际使用条件尚待明确；标准采用记录单独保存。')
    if requirement['state'] != 'page_checked': return pending('方法要求尚未完成原页核对。')
    if requirement['origin'] == 'recommendation': return pending('本项为建议，不能作为标准强制要求判定。')
    if observation['match'] != 'matched': return pending('缺少对应校准项目或存在参数、工况、点位对应歧义。')
    if not observation['evidence']: return pending('缺少证书原页证据。')
    c = requirement['criterion']
    if 'assessment' in observation:
        if c['operator'] != 'none' or observation['rule'] != 'none':
            raise LabcalError('METHOD_ASSESSMENT_NOT_APPLICABLE', '原页专项分析不能覆盖已定义的确定性数值规则。')
        assessment = observation['assessment']
        return {**result, 'state': assessment['state'], 'reason': assessment['reason'],
                'origin': 'agent_page_assessment', 'assessment_basis': assessment['basis']}
    if c['operator'] == 'none' or observation['rule'] == 'none': return pending('需要专项分析或明确适用判定规则。')
    if c['unit'] == '%' and c['relative_basis'] != observation.get('relative_basis'):
        return pending('相对误差的计算基准尚未一致。')
    if 'value' not in observation: return pending('缺少对应结果数值。')
    try:
        limit = limit_value(requirement, use['conditions'])
        result['limit'] = str(limit); result['unit'] = c['unit']
        datum = observation['value']
        if c['operator'] == 'eq':
            if observation['rule'] != 'direct' or datum['unit'] != c['unit']:
                return pending('等级或定性要求需要同一标度的直接比较。')
            passed = datum['value'] == limit
        else:
            v = convert(str(decimal(datum['value'])), datum['unit'], c['unit'])
            limit = decimal(str(limit))
            if c['operator'] == 'abs_le' and limit < 0:
                raise LabcalError('METHOD_LIMIT_INVALID', '绝对误差限值不能为负数。')
            result['normalized_value'] = str(v)
            direct = {'abs_le': abs(v) <= limit, 'le': v <= limit, 'ge': v >= limit}[c['operator']]
            passed = direct
            if observation['rule'] == 'guard_band':
                if 'uncertainty' not in observation: return pending('保护带规则缺少适用于该结果的不确定度。')
                u = convert(str(decimal(observation['uncertainty']['value'])), observation['uncertainty']['unit'], c['unit'])
                if u < 0: raise LabcalError('UNCERTAINTY_NEGATIVE', '不确定度不能为负数。')
                result['normalized_uncertainty'] = str(u)
                passed = {'abs_le': abs(v) + u <= limit, 'le': v + u <= limit, 'ge': v - u >= limit}[c['operator']]
                if not passed:
                    return {**result, 'reason': '所选保护带规则未能确认符合；不据此断言设备超差。'}
        return {**result, 'state': 'meets_requirement' if passed else 'does_not_meet',
                'reason': '仅对本条证据及所声明范围按所选规则评价。'}
    except LabcalError as exc:
        return {**result, 'reason': exc.message, 'gap_code': exc.code}
