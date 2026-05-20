import os
import re
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple
from zipfile import ZipFile
import xml.etree.ElementTree as ET


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BULLETIN_DOCX_PATH = os.path.abspath(
    os.path.join(BASE_DIR, "..", "参考文件", "(Converted)ug_bulletin_2025-2026.docx")
)

WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
WORD_W = "{%s}" % WORD_NS["w"]
COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,})\s*([0-9]{2,3}[A-Z]?)\b")
KNOWN_COURSE_SUBJECTS = {
    "ARHU", "ARTS", "BEHAVSCI", "BIOL", "CAPSTONE", "CHEM", "CHINESE", "CHSC",
    "COMPDSGN", "COMPSCI", "CULANTH", "CULSOC", "DKU", "EAP", "ECON", "ENVIR",
    "ETHLDR", "GCHINA", "GCULS", "GERMAN", "GLHLTH", "GLOCHALL", "HIST", "HUM",
    "INDSTU", "INFOSCI", "INTGSCI", "JAPANESE", "KOREAN", "LIT", "MATH",
    "MATSCI", "MEDIA", "MEDIART", "MINITERM", "MUSIC", "NEUROSCI", "PHIL",
    "PHYSEDU", "PHYS", "POLECON", "POLSCI", "PPE", "PSYCH", "PUBPOL",
    "RELIG", "RINDSTU", "SOCIOL", "SOSC", "SPANISH", "STATS", "WOC",
}
COURSE_SUBJECT_ALIASES = {
    "EHTLDR": "ETHLDR",
}
REQUIREMENT_CATEGORIES = {
    "divisional foundation courses": "Divisional Foundation Courses",
    "interdisciplinary courses": "Interdisciplinary Courses",
    "interdisciplinary studies courses": "Interdisciplinary Courses",
    "disciplinary courses": "Disciplinary Courses",
    "disciplinary studies courses": "Disciplinary Courses",
    "signature work": "Signature Work",
    "signature work capstone courses": "Signature Work",
    "electives": "Electives",
}
NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

_CACHE: Optional[Tuple[float, Dict[str, Any]]] = None


def normalize_course_code(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().upper())
    match = re.fullmatch(r"([A-Z]+)\s*([0-9][0-9A-Z]*)", text)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return text


def extract_course_codes(text: str) -> List[str]:
    normalized = (
        str(text or "")
        .replace("|", " ")
        .replace("/", " / ")
        .replace("\u6bcf", " ")
        .replace("\u203b", " ")
    )
    codes = []
    for subject, catalog in COURSE_CODE_RE.findall(normalized.upper()):
        subject = COURSE_SUBJECT_ALIASES.get(subject, subject)
        if subject not in KNOWN_COURSE_SUBJECTS:
            continue
        code = normalize_course_code(f"{subject} {catalog}")
        if code and code not in codes:
            codes.append(code)
    return codes


def _paragraph_text(p) -> str:
    return "".join(t.text or "" for t in p.findall(".//w:t", WORD_NS)).strip()


def _paragraph_style(p) -> str:
    ppr = p.find("w:pPr", WORD_NS)
    if ppr is None:
        return ""
    style = ppr.find("w:pStyle", WORD_NS)
    return style.attrib.get(WORD_W + "val", "") if style is not None else ""


def _cell_text(cell) -> str:
    values = []
    for p in cell.findall(".//w:p", WORD_NS):
        text = _paragraph_text(p)
        if text:
            values.append(text)
    return " | ".join(values)


def _table_rows(tbl) -> List[List[str]]:
    rows = []
    for tr in tbl.findall("w:tr", WORD_NS):
        cells = [_cell_text(tc) for tc in tr.findall("w:tc", WORD_NS)]
        if any(cell.strip() for cell in cells):
            rows.append(cells)
    return rows


def _read_docx_body(path: str):
    with ZipFile(path) as docx:
        root = ET.fromstring(docx.read("word/document.xml"))
    return root.find("w:body", WORD_NS)


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", value.lower()).strip("-")
    return text or "track"


def _is_available_only_heading(text: str) -> bool:
    return text.lower().startswith("available only")


def _category_from_text(text: str) -> Optional[str]:
    cleaned = re.sub(r"\s+", " ", text or "").strip().lower()
    return REQUIREMENT_CATEGORIES.get(cleaned)


def _instruction_count(text: str) -> Optional[int]:
    lower = text.lower()
    if "choose" not in lower:
        return None
    digit = re.search(r"\bchoose\s+(\d+)\b", lower)
    if digit:
        return int(digit.group(1))
    word = re.search(r"\bchoose\s+(one|two|three|four|five|six|seven|eight|nine|ten)\b", lower)
    if word:
        return NUMBER_WORDS.get(word.group(1))
    return 1


def _row_title(cells: List[str]) -> str:
    if len(cells) >= 2:
        return cells[1].strip()
    return ""


def _row_credits(cells: List[str]) -> str:
    for cell in reversed(cells):
        match = re.search(r"\b(\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?)\b", cell)
        if match:
            return match.group(1)
    return ""


def _is_header_row(row_text: str) -> bool:
    lower = row_text.lower()
    return "course code" in lower and "course" in lower and "credit" in lower


def _looks_like_course_row(cells: List[str]) -> bool:
    if not cells:
        return False
    first = cells[0].strip()
    if "choose" in first.lower():
        return False
    return bool(extract_course_codes(first))


def _make_group(category: str, instruction: str, kind: str, min_count: int) -> Dict[str, Any]:
    return {
        "key": "",
        "category": category,
        "instruction": instruction,
        "kind": kind,
        "min_count": min_count,
        "course_codes": [],
        "summary": "",
        "courses": [],
    }


def _parse_requirement_groups(rows: List[List[str]], category: str) -> List[Dict[str, Any]]:
    groups: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    recommended = category == "Electives"

    def close_current() -> None:
        nonlocal current
        if current and current["courses"]:
            if current["kind"] == "required":
                current["min_count"] = len(current["courses"])
            groups.append(current)
        current = None

    for cells in rows:
        row_text = " ".join(cells).strip()
        if not row_text or _is_header_row(row_text):
            continue
        codes = extract_course_codes(row_text)
        count = _instruction_count(row_text)
        if count is not None and not _looks_like_course_row(cells):
            close_current()
            kind = "recommended" if recommended else "choice"
            current = _make_group(category, row_text, kind, count)
            if codes:
                current["courses"].append({
                    "codes": codes,
                    "title": _row_title(cells),
                    "credits": _row_credits(cells),
                })
            continue
        if not codes:
            close_current()
            current = _make_group(category, row_text, "recommended" if recommended else "required", 0)
            continue
        if current is None:
            current = _make_group(
                category,
                "Recommended elective options" if recommended else "Complete listed courses",
                "recommended" if recommended else "required",
                0,
            )
        current["courses"].append({
            "codes": codes,
            "title": _row_title(cells),
            "credits": _row_credits(cells),
        })
    close_current()
    return groups


def _track_course_pool(groups: List[Dict[str, Any]]) -> List[str]:
    pool = []
    for group in groups:
        if group.get("kind") == "recommended":
            continue
        for course in group.get("courses", []):
            for code in course.get("codes", []):
                if code not in pool:
                    pool.append(code)
    return sorted(pool)


def _finalize_requirement_groups(groups: List[Dict[str, Any]]) -> None:
    for index, group in enumerate(groups, 1):
        codes = []
        for course in group.get("courses", []):
            for code in course.get("codes", []):
                if code not in codes:
                    codes.append(code)
        group["key"] = f"{_slug(group.get('category', 'requirement'))}-{index}"
        group["course_codes"] = codes
        if group.get("kind") == "choice":
            group["summary"] = f"选 {group.get('min_count', 1)} 门：{', '.join(codes)}"
        elif group.get("kind") == "required":
            group["summary"] = f"都要完成：{', '.join(codes)}"
        else:
            group["summary"] = f"推荐/可选：{', '.join(codes)}"


def _append_rule(rules: List[Dict[str, Any]], code: str, title: str, source_section: str,
                 source_text: str, values: Dict[str, Any], status: str = "review_only") -> None:
    if any(rule["code"] == code for rule in rules):
        return
    rules.append({
        "code": code,
        "title": title,
        "source_section": source_section,
        "source_text": source_text,
        "values": values,
        "status": status,
    })


def _extract_course_load_rules(paragraphs: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    rules: List[Dict[str, Any]] = []
    current_section = ""
    for item in paragraphs:
        style = item["style"]
        text = item["text"]
        if style in {"Heading2", "Heading3", "Heading4"}:
            current_section = text
        lower = text.lower()
        if "normal course load is" in lower and "first 7-week session" in lower:
            normal = re.search(
                r"normal course load is\s+(\d+)\s*-\s*(\d+)\s+credits\s+\((\d+)\s*-\s*(\d+)\s+credits",
                text,
                flags=re.IGNORECASE,
            )
            first = re.search(r"first 7-week session of their first term.*?maximum of\s+(\d+)\s+credits", text, flags=re.IGNORECASE)
            later = re.search(r"subsequent 7-week session.*?is\s+(\d+)", text, flags=re.IGNORECASE)
            _append_rule(rules, "normal_fall_spring_load", "Fall/Spring normal credit load",
                         current_section, text, {
                             "term_min_credits": int(normal.group(1)) if normal else 16,
                             "term_max_credits": int(normal.group(2)) if normal else 20,
                             "session_min_credits": int(normal.group(3)) if normal else 8,
                             "session_max_credits": int(normal.group(4)) if normal else 10,
                             "first_year_first_term_s1_max_credits": int(first.group(1)) if first else 8,
                             "subsequent_session_max_without_permission": int(later.group(1)) if later else 10,
                         }, status="enforced")
        if "first-year students may only enroll in 300- or 400-level courses" in lower:
            _append_rule(rules, "first_year_upper_level_restriction", "First-year upper-level course restriction",
                         current_section, text, {"year_level": 1, "course_levels": [300, 400], "requires_consent_without_specific_prereq": True})
        if "may not register for two courses officially listed as meeting at the same time" in lower:
            _append_rule(rules, "no_official_time_conflict", "No official time conflict",
                         current_section, text, {"already_enforced_by_scheduler": True})
        if "first-year students are required" in lower and "2-credit writing course" in lower and "first session" in lower:
            _append_rule(rules, "first_year_writing_first_session", "First-year writing course timing",
                         current_section, text, {
                             "year_level": 1,
                             "credits": 2,
                             "timing": "first session at DKU",
                             "course_attribute_values": ["CURR-WRITING"],
                         })
        if "common core courses" in lower and "must be taken during the fall or spring term in the designated year" in lower:
            _append_rule(rules, "common_core_designated_year", "Common Core designated-year timing",
                         current_section, text, {
                             "year_1": "GCHINA 101",
                             "year_2": "GLOCHALL 201",
                             "year_3": "ETHLDR 201",
                         })
        if "all students are required to take each of the three common core courses during the designated year" in lower:
            _append_rule(rules, "common_core_designated_year_detail", "Common Core yearly sequence",
                         current_section, text, {
                             "year_1": "GCHINA 101",
                             "year_2": "GLOCHALL 201",
                             "year_3": "ETHLDR 201",
                         })
        if "required to take 8-16 credits of foreign language courses" in lower:
            _append_rule(rules, "language_requirement", "Language course requirement",
                         current_section, text, {"credits_min": 8, "credits_max": 16, "needs_placement": True})
        if "eap track" in lower and "eap 101a to eap 102b" in lower:
            _append_rule(rules, "eap_track_sequence", "EAP track sequence",
                         current_section, text, {"courses": ["EAP 101A", "EAP 101B", "EAP 102A", "EAP 102B"], "needs_placement": True})
        if "chinese 202b" in lower and "at least eight credits of chinese language courses" in lower:
            _append_rule(rules, "csl_track_sequence", "Chinese language track sequence",
                         current_section, text, {"minimum_terminal_course": "CHINESE 202B", "credits_min": 8, "needs_placement": True})
        if "students are required to take one mini-term course" in lower:
            _append_rule(rules, "mini_term_once", "Mini-term requirement",
                         current_section, text, {"subject": "MINITERM", "credits": 0, "timing": "spring between sessions", "required_once": True})
        if "dku 101" in lower and "all" in lower and "first-year students" in lower:
            _append_rule(rules, "dku101_first_year", "DKU 101 first-year requirement",
                         current_section, text, {"course": "DKU 101", "credits": 0, "year_level": 1})
        if "two capstone courses" in lower and "senior year" in lower:
            _append_rule(rules, "capstone_senior_sequence", "Senior capstone sequence",
                         current_section, text, {"courses": ["CAPSTONE 495", "CAPSTONE 496"], "year_level": 4, "credits_total": 8})
    return rules


def _parse_bulletin(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {
            "source_path": path,
            "tracks": [],
            "rules": [],
            "error": f"找不到 bulletin 文件：{path}",
        }

    body = _read_docx_body(path)
    tracks: List[Dict[str, Any]] = []
    paragraphs: List[Dict[str, str]] = []
    in_majors = False
    current_major = ""
    current_track: Optional[Dict[str, Any]] = None
    current_category = ""
    seen_major_requirements = False

    def close_track() -> None:
        nonlocal current_track
        if not current_track:
            return
        _finalize_requirement_groups(current_track["requirement_groups"])
        current_track["course_pool"] = _track_course_pool(current_track["requirement_groups"])
        current_track["course_count"] = len(current_track["course_pool"])
        if current_track["course_pool"]:
            tracks.append(current_track)
        current_track = None

    for child in body:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            text = _paragraph_text(child)
            if not text:
                continue
            style = _paragraph_style(child)
            paragraphs.append({"style": style, "text": text})
            if style == "Heading2" and text == "Majors (listed in alphabetical order)":
                in_majors = True
                current_major = ""
                seen_major_requirements = False
                continue
            if style == "Heading2" and text == "Course Descriptions":
                close_track()
                in_majors = False
                continue
            if not in_majors:
                continue
            if style == "Heading3" and text != "Major Requirements":
                close_track()
                current_major = text
                seen_major_requirements = False
                current_category = ""
                continue
            if style == "Heading3" and text == "Major Requirements":
                seen_major_requirements = True
                current_category = ""
                continue
            if style == "Heading4" and not _is_available_only_heading(text) and seen_major_requirements:
                close_track()
                current_track = {
                    "key": _slug(text),
                    "label": text,
                    "major": current_major,
                    "requirement_groups": [],
                    "course_pool": [],
                    "course_count": 0,
                }
                current_category = ""
                continue
            category = _category_from_text(text)
            if category and current_track is not None:
                current_category = category
        elif tag == "tbl" and in_majors and current_track is not None:
            rows = _table_rows(child)
            if not rows:
                continue
            category = current_category or "Major Requirements"
            current_track["requirement_groups"].extend(_parse_requirement_groups(rows, category))
    close_track()

    return {
        "source_path": path,
        "tracks": tracks,
        "rules": _extract_course_load_rules(paragraphs),
        "error": "",
    }


def get_bulletin_data() -> Dict[str, Any]:
    global _CACHE
    try:
        mtime = os.path.getmtime(BULLETIN_DOCX_PATH)
    except OSError:
        mtime = 0.0
    if _CACHE and _CACHE[0] == mtime:
        return deepcopy(_CACHE[1])
    data = _parse_bulletin(BULLETIN_DOCX_PATH)
    _CACHE = (mtime, data)
    return deepcopy(data)


def get_bulletin_track(track_key: str) -> Optional[Dict[str, Any]]:
    if not track_key:
        return None
    for track in get_bulletin_data().get("tracks", []):
        if track.get("key") == track_key:
            return track
    return None
