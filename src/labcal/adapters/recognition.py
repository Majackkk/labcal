"""Labelled PDF field candidates with layout bounds and original-page evidence."""
import re
from .pdf import LABELS, location
from ..domain.recognition import normalized

IDENTITY_LABELS = {k: LABELS[k] for k in ("instrument_name", "model", "instrument_number")}
IDENTITY_LABELS["manufacturer"] = ["制造单位", "制造厂家", "生产厂家", "制造厂", "制造商", "厂家", "Manufacturer"]
PRIMARY = {**IDENTITY_LABELS, **{k: LABELS[k] for k in ("certificate_number", "client", "activity_date", "issue_date")}}
PRIMARY["issuer"] = ["出证机构", "校准单位", "检定单位", "Issuing organization"]
PRIMARY["client"] = [*LABELS["client"], "客户单位", "Customer"]
PRIMARY["verification_conclusion"] = LABELS["verification_conclusion"]
PRIMARY["verification_valid_until"] = LABELS["verification_valid_until"]
STANDARDS_SECTION = re.compile(r"^(?:\d+[.、]\s*)?(?:(?:校准|检定)?(?:所用|使用的)?(?:计量标准器|测量标准|标准器|主要计量标准|计量标准装置)|(?:reference|measurement|calibration)\s+standards|standards\s*(?:used)?\s*[:：]?$)", re.I)


def label_pattern(alias):
    return r"\s*".join(re.escape(c) for c in alias) if not alias.isascii() else re.escape(alias).replace(r"\ ", r"\s*")


def recognized_fields(payload):
    fields = {k: list(v) for k, v in payload["fields"].items() if k not in PRIMARY}
    aliases = {**LABELS, **IDENTITY_LABELS, **PRIMARY}
    all_labels = sorted({alias for values in aliases.values() for alias in values}, key=len, reverse=True)
    stops = re.compile("|".join(label_pattern(a) for a in all_labels), re.I)
    delimited_stops = re.compile("(?:"+stops.pattern+")\\s*[:：]", re.I)
    def is_label_line(text):
        return bool(delimited_stops.match(text.strip()) or re.fullmatch("(?:"+stops.pattern+")\\s*[:：.]?",text.strip(),re.I))
    for field, values in PRIMARY.items():
        if field.startswith("verification_") and payload.get("document_kind") == "calibration": continue
        found = []
        for page in payload["pages"]:
            boundary = min((l["bbox"][1] for l in page["lines"] if STANDARDS_SECTION.search(l["text"].strip())), default=2)
            for line in page["lines"]:
                if (field in IDENTITY_LABELS or field.startswith("verification_")) and line["bbox"][1] >= boundary: continue
                # Label at the beginning of a line; a mention inside a narrative
                # sentence or a scope reference is not an equipment identity field.
                match = next((m for alias in values if (m := re.match(r"^\s*(?:\d+[.、]\s*)?"+label_pattern(alias), line["text"], re.I))), None)
                if not match: continue
                if field in {"activity_date", "issue_date"} and found and match.group().isascii(): continue
                value = line["text"][match.end():].strip(" \t:：.")
                end = delimited_stops.search(value)
                if end: value = value[:end.start()].strip(" \t:：;；")
                fragments = [line]
                box = line["bbox"]
                # Bilingual labels often occupy two lines while their value is
                # vertically centred across both. Never consume the following row.
                neighbours = [other for other in page["lines"] if other is not line and other["bbox"][0] >= box[2] - .005
                    and abs((other["bbox"][1]+other["bbox"][3]-box[1]-box[3])/2) < max(.023, (box[3]-box[1])*.8)
                    and not is_label_line(other["text"])]
                if not value and neighbours:
                    anchor = min(neighbours, key=lambda l: (abs(l["bbox"][1]-box[1]), l["bbox"][0]))
                    value = anchor["text"].strip(); fragments.append(anchor)
                if field in {"activity_date", "issue_date"} and value:
                    # Join split year/month/day cells on this baseline, to the right.
                    parts = [n for n in neighbours if re.fullmatch(r"[\d\s年月日./-]+", n["text"])]
                    joined = "".join(n["text"] for n in sorted(parts, key=lambda n:n["bbox"][0]))
                    value = joined if len(fragments) > 1 else value + joined
                    fragments.extend(n for n in parts if n not in fragments)
                    date = re.fullmatch(r"\s*(\d{4})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?\s*", value)
                    if date: value = f"{date[1]}-{int(date[2]):02d}-{int(date[3]):02d}"
                if value and normalized(value) not in {normalized(c["value"]) for c in found}:
                    loc = location(line)
                    loc["bbox"] = [min(f["bbox"][0] for f in fragments), min(f["bbox"][1] for f in fragments),
                                   max(f["bbox"][2] for f in fragments), max(f["bbox"][3] for f in fragments)]
                    loc["line_ids"] = [f["id"] for f in fragments]; loc["quote"] = " ".join(f["text"] for f in fragments)
                    found.append({"value": value, "location": loc, "state": "candidate"})
            # Equipment particulars come from the first page containing this field.
            # Later standards tables keep their own raw extraction, never this role.
            if found: break
        if found: fields[field] = found
    if "issuer" not in fields:
        # The first text line may be a document title. Only suggest an institution
        # from the header before client/device particulars; otherwise leave blank.
        for line in payload["pages"][0]["lines"][:12]:
            if re.match(r"\s*(?:委\s*托|送\s*检|客\s*户|器\s*具|仪\s*器|client|customer|instrument)", line["text"], re.I): break
            if re.search(r"公司|研究院|计量院|检测中心|检定所|实验室|institute|laborator|co[.,]|limited|\bltd\b", line["text"], re.I):
                fields["issuer"] = [{"value": line["text"], "location": location(line), "state": "candidate"}]; break
    return fields
