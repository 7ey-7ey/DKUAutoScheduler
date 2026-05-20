import json
import re
from typing import Dict, List, Optional, Tuple


YEAR_LABELS = {1: "Y1", 2: "Y2", 3: "Y3", 4: "Y4/Senior"}
SEPARATOR_RE = re.compile(r"(?i)\s*(?:,|/|\band\b|\bor\b)\s*")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def strip_reserved_shell(description: str) -> str:
    target = normalize_text(description)
    target = re.sub(r"(?i)^reserved\s+for\s+", "", target).strip()
    target = re.sub(r"(?i)^rsvd\s+for\s+", "", target).strip()
    target = re.sub(r"(?i)^students?\s+in\s+", "", target).strip()
    target = re.sub(r"(?i)\bstudents?\b", "", target).strip()
    target = re.sub(r"(?i)\bstu\b\.?", "", target).strip()
    target = re.sub(r"(?i)\bonly\b", "", target).strip()
    target = re.sub(r"(?i)^in\s+", "", target).strip()
    return re.sub(r"\s+", " ", target).strip(" .")


def extract_reserve_caps(search_raw_json: str, detail_raw_json: str = "") -> Tuple[List, str]:
    try:
        search_data = json.loads(search_raw_json or "{}")
    except json.JSONDecodeError:
        search_data = {}
    if not isinstance(search_data, dict):
        search_data = {}
    search_caps = search_data.get("reserve_caps") or []
    if isinstance(search_caps, list) and search_caps:
        return search_caps, "search_raw_json.reserve_caps"

    try:
        detail_data = json.loads(detail_raw_json or "{}")
    except json.JSONDecodeError:
        detail_data = {}
    if not isinstance(detail_data, dict):
        detail_data = {}
    section = detail_data.get("section_info", {}) or {}
    detail_caps = section.get("reserve_caps") or []
    if isinstance(detail_caps, list) and detail_caps:
        return detail_caps, "detail_raw_json.section_info.reserve_caps"
    return [], ""


def split_target_parts(target: str) -> List[str]:
    return [part.strip() for part in SEPARATOR_RE.split(target) if part.strip()]


def parse_student_identity(parts: List[str]) -> Optional[Dict]:
    values = []
    for part in parts:
        lowered = part.lower()
        if lowered == "international":
            values.append("international")
        elif lowered == "chinese":
            values.append("chinese")
        else:
            return None
    labels = {
        "international": "International students",
        "chinese": "Chinese students",
    }
    unique_values = list(dict.fromkeys(values))
    return {
        "type": "student_identity",
        "values": unique_values,
        "label": ", ".join(labels[v] for v in unique_values),
    } if unique_values else None


def parse_class_year(parts: List[str]) -> Optional[Dict]:
    class_years = []
    for part in parts:
        match = re.fullmatch(r"(?i)class\s+of\s+(20\d{2})", part)
        if match:
            class_years.append(int(match.group(1)))
            continue
        match = re.fullmatch(r"(?i)CO\s*(20\d{2})", part)
        if match:
            class_years.append(int(match.group(1)))
            continue
        match = re.fullmatch(r"(?i)CO\s*(\d{2})", part)
        if match:
            class_years.append(2000 + int(match.group(1)))
            continue
        return None
    unique_class_years = sorted(dict.fromkeys(class_years))
    return {
        "type": "class_year",
        "values": unique_class_years,
        "label": ", ".join(f"Class of {year}" for year in unique_class_years),
    } if unique_class_years else None


def parse_year_level(parts: List[str]) -> Optional[Dict]:
    year_levels = set()
    for part in parts:
        match = re.fullmatch(r"(?i)Y\s*([1-4])", part)
        if match:
            year_levels.add(int(match.group(1)))
            continue
        match = re.fullmatch(r"(?i)year\s*([1-4])", part)
        if match:
            year_levels.add(int(match.group(1)))
            continue
        if re.fullmatch(r"(?i)seniors?", part):
            year_levels.add(4)
            continue
        return None
    unique_year_levels = sorted(year_levels)
    return {
        "type": "year_level",
        "values": unique_year_levels,
        "label": ", ".join(YEAR_LABELS.get(y, f"Y{y}") for y in unique_year_levels),
    } if unique_year_levels else None


def parse_reserved_description(description: str) -> dict:
    raw = normalize_text(description)
    target = strip_reserved_shell(raw)
    notes = []
    conditions = []

    if not raw:
        return {
            "status": "failed",
            "raw_description": raw,
            "target_text": target,
            "conditions": [],
            "normalized_targets": [],
            "notes": ["empty description"],
        }

    parts = split_target_parts(target)
    parsed_condition = (
        parse_student_identity(parts)
        or parse_class_year(parts)
        or parse_year_level(parts)
    )
    if parsed_condition:
        conditions.append(parsed_condition)
        status = "parsed"
    else:
        unknown_value = target or raw
        conditions.append({
            "type": "unknown",
            "values": [unknown_value],
            "label": f"Unknown: {unknown_value}",
        })
        status = "partial"
        notes.append("unrecognized reserved seat target; needs human review")

    normalized_targets = [c["label"] for c in conditions]
    return {
        "status": status,
        "raw_description": raw,
        "target_text": target,
        "conditions": conditions,
        "normalized_targets": normalized_targets,
        "notes": notes,
    }


def parse_reserve_caps(caps: List[Dict]) -> List[Dict]:
    results = []
    for cap in caps:
        if not isinstance(cap, dict):
            continue
        capacity = int(cap.get("enrl_cap", 0) or 0)
        enrollment_total = int(cap.get("enrl_tot", 0) or 0)
        parsed_description = parse_reserved_description(cap.get("descr", ""))
        results.append({
            "reserve_cap_number": cap.get("rsrv_cap_nbr", ""),
            "start_date": str(cap.get("start_dt", "") or ""),
            "capacity": capacity,
            "enrollment_total": enrollment_total,
            "available": capacity - enrollment_total,
            "description": parsed_description["raw_description"],
            "description_parse": parsed_description,
        })
    return results


def classify_reserved_seats(parsed_caps: List[Dict]) -> list:
    """Return tags like ['has_class_year', 'has_year_level', 'has_identity', 'has_unknown']"""
    if not parsed_caps:
        return []
    tags = []
    for cap in parsed_caps:
        for cond in cap.get("description_parse", {}).get("conditions", []):
            t = cond.get("type", "")
            if t == "class_year":
                tags.append("has_class_year")
            elif t == "year_level":
                tags.append("has_year_level")
            elif t == "student_identity":
                tags.append("has_identity")
            elif t == "unknown":
                tags.append("has_unknown")
    return list(dict.fromkeys(tags))
