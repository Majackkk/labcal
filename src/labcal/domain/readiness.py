from ..errors import LabcalError
from .rules import number, convert, rule_problems


def requirement_hash(requirement):
    from ..util import content_hash
    return content_hash(requirement)


def assess(payload, inspect_evidence):
    results, ids = [], set()
    for item in payload["requirements"]:
        coverage, decision = [], []
        if item["id"] in ids:
            coverage.append("DUPLICATE_REQUIREMENT_ID")
        ids.add(item["id"])
        route = item["delivery_route"]
        if route == "unresolved":
            coverage.append("DELIVERY_ROUTE_UNDEFINED")
        if not item["unit"] and route not in {"functional_check", "pre_use_check"}:
            coverage.append("UNIT_UNDEFINED")
        if item["coverage_policy"] == "unresolved":
            coverage.append("COVERAGE_UNDEFINED")
        if not item["report_fields"]:
            coverage.append("REPORT_FIELDS_UNDEFINED")
        if item["coverage_policy"] in {"explicit_points", "representative_points"} and not item["points"]:
            coverage.append("POINTS_MISSING")
        interval = item.get("calibration_range")
        try:
            if interval:
                lower, upper = number(interval["minimum"]), number(interval["maximum"])
                range_unit = interval.get("unit", item["unit"])
                lower = convert(str(lower), range_unit, item["unit"])
                upper = convert(str(upper), range_unit, item["unit"])
                if lower > upper:
                    coverage.append("RANGE_REVERSED")
                for point in item["points"]:
                    if not lower <= number(point) <= upper:
                        coverage.append("POINT_OUTSIDE_RANGE")
            elif item["coverage_policy"] == "range":
                coverage.append("RANGE_MISSING")
            for point in item["points"]:
                number(point)
        except (KeyError, LabcalError):
            coverage.append("RANGE_OR_POINTS_INVALID")
        if not item["evidence"]:
            coverage.append("EVIDENCE_MISSING")
        for evidence in item["evidence"]:
            coverage.extend(inspect_evidence(item, evidence))
        decision.extend(rule_problems(item))
        uncertainty = item.get("uncertainty")
        if uncertainty:
            try:
                if "maximum" not in uncertainty or not uncertainty.get("unit") or number(uncertainty["maximum"]) < 0:
                    decision.append("UNCERTAINTY_REQUIREMENT_INVALID")
                elif uncertainty["unit"] == "%" and uncertainty.get("relative_basis") not in {"reading", "reference_value", "full_scale"}:
                    decision.append("UNCERTAINTY_RELATIVE_BASIS_UNDEFINED")
                elif uncertainty["unit"] != "%":
                    convert(uncertainty["maximum"], uncertainty["unit"], item["unit"])
            except LabcalError:
                decision.append("UNCERTAINTY_REQUIREMENT_INVALID")
        results.append({"requirement_id": item["id"], "coverage_ready": not coverage,
                        "decision_ready": not coverage and not decision,
                        "coverage_issues": sorted(set(coverage)), "decision_issues": sorted(set(decision))})
    mandatory = [r for r, item in zip(results, payload["requirements"]) if item.get("mandatory", True)]
    return {"coverage_ready": bool(mandatory) and all(x["coverage_ready"] for x in mandatory),
            "decision_ready": bool(mandatory) and all(x["decision_ready"] for x in mandatory),
            "requirements": results}

