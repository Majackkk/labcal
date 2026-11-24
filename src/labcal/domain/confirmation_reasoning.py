"""Check declared technical reasoning, without inferring criteria from prose.

Evidence truth and clause meaning still require original-page review. This module
only rejects inconsistent claims; it never supplies an MPE or a use restriction.
"""


def reasoning_issues(record):
    issues = []

    def issue(code, message, rule_number=None):
        issues.append(dict(code=code, message=message, rule_number=rule_number))

    review = record.get('technical_review', {})
    rules = review.get('rules', [])
    if not rules:
        issue('CONFIRMATION_REASONING_REQUIRED', '尚无逐项技术判断记录，不能直接重新出具正式表。')
    groups, meanings = {}, {}
    for number, rule in enumerate(rules, 1):
        analysis = rule.get('reasoning')
        if not analysis:
            issue('CONFIRMATION_REASONING_REQUIRED', '先核清要求含义、比较量、规则依据及U的作用，再生成正式表。', number)
            continue
        if not all(k in rule for k in ('dimension', 'state', 'judgment_quote')):
            issue('CONFIRMATION_REASONING_REQUIRED', '判断须先区分器具要求与检测用途，并给出对应结论。', number)
            continue
        status = analysis['requirement_status']
        interpretation = analysis['interpretation_status']
        state, mode = rule['state'], rule['decision_mode']
        decisive = state in {'meets_requirement', 'does_not_meet'}
        if status == 'unresolved' or interpretation == 'unresolved':
            issue('CONFIRMATION_BASIS_UNRESOLVED', '要求依据或关键术语尚未核清；继续技术分析，不能靠限定使用完成正式表。', number)
        for field in ['interpretation_evidence', 'quantity_basis', 'decision_evidence']:
            if not analysis[field].strip():
                issue('CONFIRMATION_REASONING_EVIDENCE', '技术分析必须保留具体来源、解释或对应关系。', number)
        if rule['requirement_kind'] == 'weighing_precision' and interpretation != 'resolved':
            issue('CONFIRMATION_TERM_INTERPRETATION', '“精确到”的含义须结合原文上下文明确解释，不能按无歧义数值处理。', number)
        if decisive and (status != 'established' or analysis['quantity_match'] == 'not_comparable'):
            issue('CONFIRMATION_REASONING_CONCLUSION', '尚无明确要求或实测量不可比较，不能形成符合/不符合结论。', number)
        if status == 'no_independent_limit' and (mode != 'report_only' or state not in {'reported_characteristic', 'not_assigned'}):
            issue('CONFIRMATION_NEUTRAL_CHARACTERISTIC', '确无独立判限时中性记录，不能把特性记录写成合格或证据不足。', number)
        if status == 'established' and rule['limit_basis'] == 'no_independent_limit':
            issue('CONFIRMATION_REASONING_BASIS', '存在适用要求时不能声明没有独立判限。', number)
        role = analysis['uncertainty_role']
        if mode == 'guard_band' and role != 'decision_component':
            issue('CONFIRMATION_UNCERTAINTY_ROLE', '采用保护带时，U应实际参与判定。', number)
        if mode == 'direct_comparison' and role != 'separate_evaluation':
            issue('CONFIRMATION_UNCERTAINTY_ROLE', '直接比较误差与允差时，U另列评价；不能暗中切换成保护带判定。', number)
        group = rule['decision_group']
        signature = (rule['requirement_kind'], rule['compared_quantity'], mode, role)
        if group in groups and groups[group] != signature:
            issue('CONFIRMATION_REASONING_GROUP', '同组点位的要求类型、比较量、规则和U作用必须一致。', number)
        groups[group] = signature
        key = (rule['source_reference'], rule['requirement_quote'])
        meaning = rule.get('requirement_interpretation')
        if key in meanings and meanings[key] != meaning:
            issue('CONFIRMATION_INTERPRETATION_CONFLICT', '同一来源同一要求在本次确认中采用了不同解释。', number)
        meanings[key] = meaning

    choice = record['publication_review']['selections']['result']['choice']
    use_rules = [r for r in rules if r.get('dimension') == 'intended_use']
    use_states = {r['state'] for r in use_rules}
    if choice == 'meets_declared_uses' and (not use_rules or 'meets_requirement' not in use_states
            or use_states & {'does_not_meet', 'insufficient_evidence'}):
        issue('CONFIRMATION_USE_CONCLUSION', '全部声明用途满足须有用途符合依据，且不能包含不满足或证据不足的用途分项。')
    if choice == 'does_not_meet' and 'does_not_meet' not in use_states:
        issue('CONFIRMATION_USE_CONCLUSION', '器具分项或证据缺口不能直接成为检测用途不满足的结论。')
    restrictions = review.get('use_restrictions', [])
    if choice == 'limited_use':
        if not restrictions or not {'meets_requirement', 'does_not_meet'} <= use_states:
            issue('CONFIRMATION_RESTRICTION_REQUIRED', '限定使用须同时有适用用途和具体不满足项；证据不足、仅评价已校点均不自动产生使用限制。')
    elif restrictions:
        issue('CONFIRMATION_RESTRICTION_CONFLICT', '存在设备使用限制时，总体结果须与限制一致。')
    for restriction in restrictions:
        if any(not restriction[k].strip() for k in ['scope', 'reason']):
            issue('CONFIRMATION_RESTRICTION_REQUIRED', '使用限制须写明受影响的具体用途/范围和依据。')
        linked = []
        for number in restriction['rule_numbers']:
            if number > len(rules):
                issue('CONFIRMATION_RESTRICTION_LINK', '使用限制引用了不存在的判断。')
                continue
            rule = rules[number-1]
            linked.append(rule)
            if rule['dimension'] != 'intended_use' or rule['state'] != 'does_not_meet':
                issue('CONFIRMATION_RESTRICTION_LINK', '使用限制必须关联具体用途不满足项，不能关联器具合格或证据不足项。', number)
        quotes = restriction['application_quotes']
        applications = [record['rows'][r['row_number']-1]['application'] for r in linked]
        if (any(not q.strip() or not any(q in a for a in applications) for q in quotes)
                or any(not any(q.strip() and q in a for q in quotes) for a in applications)):
            issue('CONFIRMATION_RESTRICTION_TEXT', '限制须逐项引用正式应用栏中可执行的范围/操作条件。')
    return issues
