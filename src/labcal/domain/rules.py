from decimal import Decimal, InvalidOperation

from ..errors import LabcalError

RULES_VERSION = "measurement-rules-1.0"
# Scales are decimal strings, never binary float factors.
UNITS = {
    "N": ("force", "1"), "kN": ("force", "1000"),
    "m": ("length", "1"), "mm": ("length", "0.001"),
    "um": ("length", "0.000001"), "µm": ("length", "0.000001"), "μm": ("length", "0.000001"),
    "Pa": ("pressure", "1"), "kPa": ("pressure", "1000"), "MPa": ("pressure", "1000000"),
    "s": ("time", "1"), "min": ("time", "60"), "h": ("time", "3600"),
    "g": ("mass", "0.001"), "mg": ("mass", "0.000001"), "kg": ("mass", "1"),
    "°C": ("temperature_celsius", "1"), "℃": ("temperature_celsius", "1"),
    "%": ("relative_percent", "1"), "%RH": ("relative_humidity", "1"),
    "mm/min": ("speed", "1"), "mm/s": ("speed", "60"),
    "J/g": ("specific_energy", "1000"), "J/kg": ("specific_energy", "1"),
}


def number(value):
    if isinstance(value, (float, bool)):
        raise LabcalError("DECIMAL_REQUIRED", "计量数值请使用十进制字符串或整数，不能使用浮点数。")
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise LabcalError("NUMBER_INVALID", "计量数值无法解析。", {"value": str(value)}) from exc
    if not result.is_finite():
        raise LabcalError("NUMBER_INVALID", "计量数值必须为有限数。")
    return result


def convert(value, from_unit, to_unit):
    result = number(value)
    if from_unit == to_unit and from_unit:
        return result
    source, target = UNITS.get(from_unit), UNITS.get(to_unit)
    if not source or not target or source[0] != target[0]:
        raise LabcalError("UNIT_INCOMPATIBLE", "单位不能按当前量纲直接换算。", {"from": from_unit, "to": to_unit})
    return result * Decimal(source[1]) / Decimal(target[1])


def rule_problems(requirement):
    rule, limit = requirement.get("decision_rule"), requirement.get("limit")
    problems = []
    if not isinstance(rule, dict) or not isinstance(limit, dict):
        return ["DECISION_UNDEFINED"]
    kind = rule.get("type")
    if set(rule) - {"type", "input", "uncertainty_input"}:
        problems.append("DECISION_FIELDS_UNSUPPORTED")
    if set(limit) - {"kind", "unit", "lower", "upper", "expected", "lower_inclusive", "upper_inclusive"}:
        problems.append("LIMIT_FIELDS_UNSUPPORTED")
    if kind not in {"interval", "upper", "lower", "guard_band", "qualitative"}:
        return ["DECISION_UNSUPPORTED"]
    if not rule.get("input"):
        problems.append("DECISION_INPUT_MISSING")
    if kind == "qualitative":
        if not isinstance(limit.get("expected"), (str, bool)):
            problems.append("QUALITATIVE_EXPECTATION_MISSING")
        return problems
    if not limit.get("unit"):
        problems.append("LIMIT_UNIT_MISSING")
    if limit.get("unit") == "%" and requirement.get("relative_basis") in {None, "unresolved", "not_applicable"}:
        problems.append("RELATIVE_BASIS_UNDEFINED")
    required = {"interval": ["lower", "upper"], "upper": ["upper"], "lower": ["lower"], "guard_band": ["lower", "upper"]}[kind]
    try:
        for field in required:
            number(limit[field])
        if "lower" in limit and "upper" in limit and number(limit["lower"]) > number(limit["upper"]):
            problems.append("LIMIT_REVERSED")
    except (KeyError, LabcalError):
        problems.append("LIMIT_INVALID")
    if kind == "guard_band" and not rule.get("uncertainty_input"):
        problems.append("UNCERTAINTY_INPUT_MISSING")
    return problems


def evaluate_rule(requirement, inputs):
    problems = rule_problems(requirement)
    if problems:
        raise LabcalError("RULE_NOT_READY", "此参数的判定规则尚不完整。", {"problems": problems})
    rule, limit = requirement["decision_rule"], requirement["limit"]
    if rule["input"] not in inputs:
        raise LabcalError("RESULT_INPUT_MISSING", "缺少规则要求的测量输入。")
    datum = inputs[rule["input"]]
    if rule["type"] == "qualitative":
        value = datum.get("value") if isinstance(datum, dict) else datum
        return {"conforms": type(value) is type(limit["expected"]) and value == limit["expected"], "rule_version": RULES_VERSION}
    if not isinstance(datum, dict) or not datum.get("unit"):
        raise LabcalError("RESULT_UNIT_MISSING", "数值输入必须明确单位。")
    if limit["unit"] == "%" and datum.get("relative_basis") != requirement.get("relative_basis"):
        raise LabcalError("RELATIVE_BASIS_MISMATCH", "测量输入与限值的百分比基准不同。")
    value = convert(datum.get("value"), datum["unit"], limit["unit"])
    lower = number(limit["lower"]) if "lower" in limit and rule["type"] != "upper" else None
    upper = number(limit["upper"]) if "upper" in limit and rule["type"] != "lower" else None
    interval_low = interval_high = value
    if rule["type"] == "guard_band":
        uncertainty = inputs.get(rule["uncertainty_input"])
        if not isinstance(uncertainty, dict) or not uncertainty.get("unit"):
            raise LabcalError("UNCERTAINTY_INPUT_MISSING", "保护带规则需要带单位的不确定度输入。")
        if limit["unit"] == "%" and uncertainty.get("relative_basis") != requirement.get("relative_basis"):
            raise LabcalError("RELATIVE_BASIS_MISMATCH", "不确定度与结果的百分比基准不同。")
        u = convert(uncertainty.get("value"), uncertainty["unit"], limit["unit"])
        if u < 0:
            raise LabcalError("UNCERTAINTY_NEGATIVE", "不确定度不能为负数。")
        interval_low, interval_high = value - u, value + u
    lower_ok = lower is None or (interval_low >= lower if limit.get("lower_inclusive", True) else interval_low > lower)
    upper_ok = upper is None or (interval_high <= upper if limit.get("upper_inclusive", True) else interval_high < upper)
    conforms = lower_ok and upper_ok
    return {"conforms": conforms, "normalized_value": str(value), "unit": limit["unit"],
            "comparison_interval": [str(interval_low), str(interval_high)], "rule_version": RULES_VERSION}

