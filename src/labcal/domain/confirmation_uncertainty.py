"""Check declared U evaluations inside the existing confirmation record.

Numbers are task-local calculation inputs, never new acceptance criteria. Source
meaning, quantity correspondence and applicability still need original review.
"""
from decimal import localcontext

from .method_requirements import decimal
from ..errors import LabcalError


NUMERIC_ROUTES = {'jjf1094_general', 'verification_regulation', 'simple_acceptance'}


def evaluate_point(point, route):
    """Same-unit, symmetric indication-error limits only; no guessed U or MPE."""
    if route not in NUMERIC_ROUTES:
        raise ValueError('This route requires documented manual evaluation.')
    with localcontext() as ctx:
        ctx.prec = 200
        error, mpe = abs(decimal(point['error'])), decimal(point['mpev'])
        value = point.get('expanded_uncertainty')
        u = decimal(value) if value is not None else None
        if mpe <= 0 or (u is not None and u < 0):
            raise ValueError('MPEV must be positive and U nonnegative.')
        if route != 'jjf1094_general':
            state = 'meets_requirement' if error <= mpe else 'does_not_meet'
            return dict(state=state, clause='5.3.1.5' if route == 'verification_regulation' else '6.2.1')
        if u is None:
            raise ValueError('U95 is missing, not zero.')
        # Multiplication avoids rounding the recurring decimal 1/3.
        if 3 * u <= mpe:
            return dict(state='meets_requirement' if error <= mpe else 'does_not_meet',
                        clause='5.3.1.4', ratio_relation='le_one_third')
        if error + u <= mpe:
            state, clause = 'meets_requirement', '5.3.1.6 a)'
        elif error >= mpe + u:
            state, clause = 'does_not_meet', '5.3.1.6 b)'
        else:
            state, clause = 'insufficient_evidence', '5.3.1.6 c)'
        return dict(state=state, clause=clause, ratio_relation='gt_one_third',
                    lower=str(mpe-u), upper=str(mpe+u), error_plus_u=str(error+u))


def uncertainty_issues(record, *, required=False):
    issues = []

    def issue(code, message, number):
        issues.append(dict(code=code, message=message, rule_number=number))

    rules = record.get('technical_review', {}).get('rules', [])
    groups = {}
    for number, rule in enumerate(rules, 1):
        review = rule.get('reasoning', {}).get('uncertainty_review')
        if review is None:
            if required:
                issue('CONFIRMATION_U_REVIEW_REQUIRED', 'U评价须明确依据、比较或不计算的理由、判断，并摘录正式U栏。', number)
            continue
        row = record['rows'][rule['row_number']-1]
        for key in ('basis_quote', 'calculation_quote', 'judgment_quote'):
            text = review[key]
            if not text.strip() or text not in row['uncertainty_evaluation']:
                issue('CONFIRMATION_U_QUOTE', 'U的依据、计算和判断必须在正式U栏中可见，不能只存内部记录。', number)
        route, application = review['route'], review['application']
        signature = (route, application, review.get('coverage_basis'))
        group = rule['decision_group']
        if group in groups and groups[group] != signature:
            issue('CONFIRMATION_U_GROUP', '同组点位的U路径及正式/补充评价性质须一致，不能选择性换规则。', number)
        groups[group] = signature
        points = review['points']
        if route not in NUMERIC_ROUTES:
            if points:
                issue('CONFIRMATION_U_MANUAL_ROUTE', '其他规则或定性评价不套用对称示值误差算法；保留对应计算及适用理由。', number)
            continue
        if (rule['requirement_kind'] != 'maximum_error'
                or rule['compared_quantity'] != 'indication_error'
                or rule.get('requirement_interpretation') in {'division_or_resolution', 'recording_precision'}):
            issue('CONFIRMATION_U_QUANTITY', '数值路径只处理有明确对称MPE的示值误差，不把分辨力、记录精度或标准偏差当成MPE。', number)
        if not points:
            issue('CONFIRMATION_U_POINTS_REQUIRED', '数值U评价须保留逐点计算输入，不能只给文件名或结论。', number)
            continue
        if route == 'jjf1094_general' and review.get('coverage_basis') not in {'u95', 'k2_equivalent'}:
            issue('CONFIRMATION_U_COVERAGE', 'JJF路径须核实为U95或有依据可替代的k=2扩展不确定度，不能使用标准不确定度或CMC。', number)
        if application == 'decision':
            expected_mode = 'specification_check' if route == 'jjf1094_general' else 'direct_comparison'
            if rule['decision_mode'] != expected_mode:
                issue('CONFIRMATION_U_RULE_MODE', '正式判定路径须与既有规则一致；JJF一般路径按其条件分支评价，不能冒充简单接受。', number)
            if route == 'jjf1094_general' and rule['reasoning']['uncertainty_role'] != 'decision_component':
                issue('CONFIRMATION_U_RULE_MODE', 'JJF正式路径中的U参与分支选择与必要的区间判定。', number)
        states, labels = [], set()
        for point in points:
            label = point['label']
            if not label.strip() or label in labels or not point['unit'].strip():
                issue('CONFIRMATION_U_POINT_IDENTITY', '计算输入须按参数、工况和点位唯一标识，并使用同一单位。', number)
            labels.add(label)
            try:
                states.append(evaluate_point(point, route)['state'])
            except (LabcalError, ValueError, ArithmeticError):
                issue('CONFIRMATION_U_NUMBER', '误差、U和MPEV须为有效十进制数；MPEV>0，U≥0，缺少U不当作0。', number)
        if len(states) != len(points):
            continue
        expected = ('does_not_meet' if 'does_not_meet' in states else
                    'insufficient_evidence' if 'insufficient_evidence' in states else 'meets_requirement')
        if review['outcome'] != expected:
            issue('CONFIRMATION_U_CALCULATION', 'U评价结论与逐点复算不一致；JJF待定区延伸至MPEV+U，不能在MPEV处提前判不合格。', number)
        if application == 'decision' and rule['state'] != expected:
            issue('CONFIRMATION_U_CONCLUSION', '正式判定与U路径复算结论不一致，不能据此发布用途合格或不合格。', number)
    return issues
