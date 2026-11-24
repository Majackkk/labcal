"""Shared assessment labels and colours; independent of limits and pass/fail."""
from ..errors import LabcalError

DIMENSIONS = {
    'certificate_self': {'label': '证书自身', 'colour': '2363A3'},
    'instrument_requirement': {'label': '器具技术要求', 'colour': '7550A0'},
    'intended_use': {'label': '检测用途', 'colour': 'A85512'},
}
DIMENSION = {'enum': list(DIMENSIONS)}


def assessment_style(dimension):
    if dimension not in DIMENSIONS:
        raise LabcalError('ASSESSMENT_DIMENSION_INVALID', '请明确证书自身、器具技术要求或检测用途维度。')
    return DIMENSIONS[dimension]


def rgb(dimension):
    value = assessment_style(dimension)['colour']
    return tuple(int(value[n:n+2], 16)/255 for n in (0, 2, 4))


def rule_dimension(rule):
    """Only legacy records infer from explicit basis; new records declare it."""
    return rule.get('dimension') or ('instrument_requirement' if rule['limit_basis'] == 'instrument_specification' else 'intended_use')
