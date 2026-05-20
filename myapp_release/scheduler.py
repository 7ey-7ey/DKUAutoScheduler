"""
DKU 自动排课核心算法
处理课程组合生成、冲突检测、评分排序、回溯搜索。
"""

import hashlib
import itertools
import json
import math
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from utils import (
    first_text,
    format_time,
    load_json_value,
    parse_meeting_location,
    row_get,
    to_int,
)

from graduation_rules import (
    COMMON_CORE,
    DISTRIBUTION_AREAS,
    DUKE_TAUGHT_REQUIREMENT,
    EAP_SEQUENCE,
    FIRST_YEAR_RESTRICTION,
    IDENTITIES,
    CSL_SEQUENCE,
    HERITAGE_SEQUENCE,
    WRITING_REQUIREMENT,
    DKU101_REQUIREMENT,
    CAPSTONE_REQUIREMENT,
    PE_REQUIREMENT,
    QR_REQUIREMENT,
    COURSE_LOAD_RULES,
    compute_class_of,
    get_language_courses_for_term,
)

# ── 常量 ──
DAY_ORDER = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
DAY_LABELS = {"Mo": "周一", "Tu": "周二", "We": "周三", "Th": "周四", "Fr": "周五", "Sa": "周六", "Su": "周日"}
DAY_FROM_PATTERN = {
    "Sun": "Su", "Mon": "Mo", "Tues": "Tu", "Tue": "Tu",
    "Wed": "We", "Thurs": "Th", "Thu": "Th", "Fri": "Fr", "Sat": "Sa",
}
PRIMARY_COMPONENTS = {"SEM", "LEC", "IND", "PED"}
GRID_START_MINUTE = 8 * 60
GRID_END_MINUTE = 22 * 60
GRID_STEP_MINUTE = 15
MAX_BUNDLES_PER_COURSE = 80
MAX_SEARCH_STATES = 50000

COURSE_CODE_ALIASES: Dict[str, Set[str]] = {
    "CCORE 101": {"GCHINA 101"},
    "GCHINA 101": {"CCORE 101"},
    "CCORE 201": {"GLOCHALL 201"},
    "GLOCHALL 201": {"CCORE 201"},
    "CCORE 202": {"ETHLDR 201"},
    "ETHLDR 201": {"CCORE 202"},
}


# ── 课程类 ──
@dataclass
class Course:
    """用户输入的课程标识。有 class_nbr 则强制按号匹配，否则按名匹配。"""
    name: str = ""       # 课名，如 "BIOL 110"
    class_nbr: str = ""  # 课号，如 "1247"

    @property
    def is_by_nbr(self) -> bool:
        return bool(self.class_nbr)

    @property
    def display(self) -> str:
        if self.class_nbr:
            return f"#{self.class_nbr} ({self.name})" if self.name else f"#{self.class_nbr}"
        return self.name

    @staticmethod
    def parse(text: str) -> "Course":
        text = text.strip()
        if not text:
            return Course()
        if text.startswith("#"):
            nbr = text[1:].strip()
            return Course(class_nbr=nbr)
        if text.isdigit():
            return Course(class_nbr=text)
        return Course(name=normalize_course_code(text))

    @staticmethod
    def parse_list(text: str) -> List["Course"]:
        courses = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            courses.append(Course.parse(line))
        return courses


# ── 辅助函数 ──
def normalize_course_code(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().upper())
    match = re.fullmatch(r"([A-Z]+)\s*([0-9][0-9A-Z]*)", text)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return text


def normalize_class_nbr(value: Any) -> str:
    text = str(value or "").strip()
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        t = str(value).strip()
        return float(t) if t else default
    except (TypeError, ValueError):
        return default


def clamp_int(value: Any, default: int = 0, low: int = 0, high: int = 100) -> int:
    v = to_int(value, default)
    return max(low, min(high, v))


def code_in_set(code: str, pool: Set[str]) -> bool:
    if code in pool:
        return True
    for alias in COURSE_CODE_ALIASES.get(code, set()):
        if alias in pool:
            return True
    return False


def make_hash_seed(seed: str) -> float:
    d = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    n = int(d[:8], 16)
    return (n / 0xFFFFFFFF) - 0.5


# ── 时间相关 ──
@dataclass
class Meeting:
    day: str
    start_minute: int
    end_minute: int
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    location: str = ""
    instructor: str = ""
    source: str = ""
    is_blocked: bool = False

    def overlaps(self, other: "Meeting") -> bool:
        if self.day != other.day:
            return False
        if self.end_minute <= other.start_minute or other.end_minute <= self.start_minute:
            return False
        return _dates_overlap(self.start_date, self.end_date, other.start_date, other.end_date)

    @property
    def start_label(self) -> str:
        return f"{self.start_minute // 60:02d}:{self.start_minute % 60:02d}"

    @property
    def end_label(self) -> str:
        return f"{self.end_minute // 60:02d}:{self.end_minute % 60:02d}"


def _dates_overlap(a_s: Optional[datetime], a_e: Optional[datetime],
                   b_s: Optional[datetime], b_e: Optional[datetime]) -> bool:
    if not a_s or not a_e or not b_s or not b_e:
        return True
    return a_s <= b_e and b_s <= a_e


def parse_days(value: Any) -> List[str]:
    if isinstance(value, dict):
        days = []
        for raw_key, enabled in value.items():
            if not enabled:
                continue
            token = DAY_FROM_PATTERN.get(str(raw_key), "")
            if token:
                days.append(token)
        return [d for d in DAY_ORDER if d in days]
    text = str(value or "").strip()
    if not text or text.upper() in {"TBA", "ARR"}:
        return []
    cleaned = re.sub(r"[\s,/]+", "", text)
    for old, new in [("Monday","Mo"),("Tuesday","Tu"),("Wednesday","We"),("Thursday","Th"),
                     ("Friday","Fr"),("Saturday","Sa"),("Sunday","Su"),
                     ("Mon","Mo"),("Tue","Tu"),("Tues","Tu"),("Wed","We"),
                     ("Thu","Th"),("Thur","Th"),("Thurs","Th"),("Fri","Fr"),
                     ("Sat","Sa"),("Sun","Su")]:
        cleaned = re.sub(old, new, cleaned, flags=re.IGNORECASE)
    days = []
    i = 0
    while i < len(cleaned):
        tok = cleaned[i:i+2]
        if tok in DAY_ORDER:
            days.append(tok)
            i += 2
        else:
            i += 1
    return [d for d in DAY_ORDER if d in days]


def parse_time_minutes(value: Any) -> Optional[int]:
    label = format_time(str(value or "").strip())
    m = re.match(r"^(\d{1,2}):(\d{2})", label)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def parse_date(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text[:10], fmt)
        except ValueError:
            continue
    return None


def make_meetings(days_value: Any, start_value: Any, end_value: Any,
                  start_date_value: Any = "", end_date_value: Any = "",
                  location: str = "", instructor: str = "",
                  source: str = "", is_blocked: bool = False) -> List[Meeting]:
    days = parse_days(days_value)
    start = parse_time_minutes(start_value)
    end = parse_time_minutes(end_value)
    if not days or start is None or end is None or end <= start:
        return []
    sd = parse_date(start_date_value)
    ed = parse_date(end_date_value)
    return [
        Meeting(day=d, start_minute=start, end_minute=end, start_date=sd, end_date=ed,
                location=location, instructor=instructor, source=source, is_blocked=is_blocked)
        for d in days
    ]


def parse_blocked_meetings(text: str) -> List[Meeting]:
    meetings = []
    for line in str(text or "").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        m = re.match(r"^(?P<days>[A-Za-z/,\s]+)\s+(?P<start>\d{1,2}[:.]\d{2})\s*-\s*(?P<end>\d{1,2}[:.]\d{2})(?:\s+(?P<label>.*))?$", raw)
        if not m:
            continue
        meetings.extend(make_meetings(
            m.group("days"), m.group("start"), m.group("end"),
            location=m.group("label") or "不可用", source=raw, is_blocked=True,
        ))
    return meetings


def meetings_from_search_raw(search_raw: Dict[str, Any], row: Any) -> List[Meeting]:
    result = []
    raw_meetings = search_raw.get("meetings", []) if isinstance(search_raw, dict) else []
    if isinstance(raw_meetings, list):
        for meeting in raw_meetings:
            if not isinstance(meeting, dict):
                continue
            bld, cls = parse_meeting_location(meeting)
            loc = first_text(cls, bld, row_get(row, "classroom"), row_get(row, "building"))
            inst = first_text(meeting.get("instructor"), row_get(row, "instructor_names"))
            result.extend(make_meetings(
                meeting.get("days"), meeting.get("start_time"), meeting.get("end_time"),
                meeting.get("start_dt"), meeting.get("end_dt"),
                location=loc, instructor=inst,
            ))
    if result:
        return result
    return make_meetings(
        row_get(row, "meeting_days"), row_get(row, "meeting_start_time"), row_get(row, "meeting_end_time"),
        row_get(row, "start_date"), row_get(row, "end_date"),
        location=first_text(row_get(row, "classroom"), row_get(row, "building")),
        instructor=row_get(row, "instructor_names"),
    )


def meetings_from_related_section(raw_section: Dict[str, Any]) -> List[Meeting]:
    result = []
    patterns = raw_section.get("MeetingPatterns", []) or []
    if isinstance(patterns, list):
        for p in patterns:
            if not isinstance(p, dict):
                continue
            result.extend(make_meetings(
                p.get("Days"), p.get("StartTime"), p.get("EndTime"),
                p.get("StartDt"), p.get("EndDt"),
                location=first_text(p.get("FacilityDescr"), p.get("FacilityId")),
                instructor=first_text(p.get("Instructor")),
            ))
    return result


# ── Section / Bundle ──
@dataclass
class Section:
    class_nbr: str
    course_code: str
    subject: str = ""
    catalog_nbr: str = ""
    section: str = ""
    component: str = ""
    title: str = ""
    units: float = 0.0
    session_code: str = ""
    status: str = ""
    enrollment_available: int = 0
    class_capacity: int = 0
    wait_list_available: int = 0
    wait_list_capacity: int = 0
    instructor_names: str = ""
    meetings: List[Meeting] = field(default_factory=list)
    requisites_raw: str = ""
    requisites_parsed: Dict[str, Any] = field(default_factory=dict)
    requisites_tags: str = ""
    reserved_data: List[Dict[str, Any]] = field(default_factory=list)
    reserved_tags: str = ""
    course_attributes: str = ""
    course_attr_values: str = ""
    requirement_designation: str = ""
    related_sections_raw: str = ""

    @property
    def display_name(self) -> str:
        return " ".join(p for p in [self.course_code, self.component, self.section] if p)

    @property
    def is_pe(self) -> bool:
        return self.subject.upper() == "PHYSEDU"

    @property
    def is_open(self) -> bool:
        return self.status.lower() == "open"

    @property
    def is_waitlist(self) -> bool:
        return "wait" in self.status.lower()

    @property
    def catalog_prefix(self) -> str:
        return re.sub(r"[^0-9].*", "", self.catalog_nbr or "")[:1]


@dataclass
class Bundle:
    """一个课程候选组合（大课 + LAB + REC 等）。"""
    sections: List[Section]
    target_priority: int = 100
    target_query: str = ""
    target_label: str = ""
    score: float = 0.0
    warnings: List[str] = field(default_factory=list)
    review_notes: List[str] = field(default_factory=list)
    blocked_reasons: List[str] = field(default_factory=list)
    color_index: int = 0

    @property
    def class_nbrs(self) -> Tuple[str, ...]:
        return tuple(sorted({s.class_nbr for s in self.sections if s.class_nbr}))

    @property
    def key(self) -> str:
        return "+".join(self.class_nbrs)

    @property
    def course_codes(self) -> Set[str]:
        return {s.course_code for s in self.sections if s.course_code}

    @property
    def primary_code(self) -> str:
        for s in self.sections:
            if s.component in PRIMARY_COMPONENTS and s.course_code:
                return s.course_code
        for s in self.sections:
            if s.course_code:
                return s.course_code
        return ""

    @property
    def title(self) -> str:
        for s in self.sections:
            if s.component in PRIMARY_COMPONENTS and s.title:
                return s.title
        for s in self.sections:
            if s.title:
                return s.title
        return ""

    @property
    def units(self) -> float:
        seen: Dict[str, float] = {}
        for s in self.sections:
            c = s.course_code or s.class_nbr
            seen[c] = max(seen.get(c, 0.0), s.units)
        return sum(seen.values())

    @property
    def meetings(self) -> List[Meeting]:
        ms: List[Meeting] = []
        for s in self.sections:
            ms.extend(s.meetings)
        return ms

    @property
    def session_s1_units(self) -> float:
        return _session_units(self, "session-s1")

    @property
    def session_s2_units(self) -> float:
        return _session_units(self, "session-s2")

    @property
    def is_dins(self) -> bool:
        return DUKE_TAUGHT_REQUIREMENT["designation"] in (self.sections[0].requirement_designation.upper()
                                                          if self.sections else "")

    @property
    def session_sort_key(self) -> int:
        for s in self.sections:
            if s.session_code:
                t = s.session_code.upper()
                if "7W1" in t or t.endswith("1"):
                    return 0
                if "7W2" in t or t.endswith("2"):
                    return 2
        return 1


def _session_units(bundle: Bundle, session_class: str) -> float:
    by_code: Dict[str, Tuple[float, str]] = {}
    for s in bundle.sections:
        c = s.course_code or s.class_nbr
        current = by_code.get(c, (0.0, ""))[0]
        if s.units >= current:
            by_code[c] = (s.units, s.session_code)
    result = 0.0
    for units, sc in by_code.values():
        cls = _session_class(sc)
        if cls == session_class:
            result += units
        elif cls == "session-full":
            result += units / 2.0
    return result


def _session_class(session_code: str) -> str:
    t = str(session_code or "").upper()
    if "7W1" in t:
        return "session-s1"
    if "7W2" in t:
        return "session-s2"
    return "session-full"


# ── 从数据库行构建 Section ──
def section_from_row(row: Any) -> Section:
    sr = load_json_value(row_get(row, "search_raw_json"), {})
    subj = first_text(sr.get("subject"), row_get(row, "subject")).upper()
    cat = first_text(sr.get("catalog_nbr"), row_get(row, "catalog_nbr")).strip()
    cnbr = normalize_class_nbr(first_text(sr.get("class_nbr"), row_get(row, "class_nbr")))
    status = first_text(sr.get("enrl_stat_descr"), row_get(row, "enrl_status"))
    if not status:
        s_code = str(sr.get("enrl_stat", "")).upper()
        status = {"O": "Open", "W": "Wait List", "C": "Closed"}.get(s_code, "")
    return Section(
        class_nbr=cnbr,
        course_code=normalize_course_code(f"{subj} {cat}"),
        subject=subj,
        catalog_nbr=cat,
        section=first_text(sr.get("class_section"), row_get(row, "class_section")),
        component=first_text(sr.get("component"), row_get(row, "component")).upper(),
        title=first_text(sr.get("descr"), row_get(row, "course_name")),
        units=parse_float(first_text(sr.get("units"), row_get(row, "units"))),
        session_code=first_text(sr.get("session_code"), row_get(row, "session_code")),
        status=status,
        enrollment_available=to_int(first_text(sr.get("enrollment_available"), row_get(row, "enrollment_available"))),
        class_capacity=to_int(first_text(sr.get("class_capacity"), row_get(row, "class_capacity"))),
        wait_list_available=to_int(row_get(row, "wait_list_available")),
        wait_list_capacity=to_int(row_get(row, "wait_list_capacity")),
        instructor_names=first_text(row_get(row, "instructor_names")),
        meetings=meetings_from_search_raw(sr, row),
        requisites_raw=row_get(row, "requisite_raw"),
        requisites_parsed=load_json_value(row_get(row, "requisite_parsed_json"), {}),
        requisites_tags=row_get(row, "requisite_tags"),
        reserved_data=load_json_value(row_get(row, "reserved_seats_parsed_json"), []),
        reserved_tags=row_get(row, "reserved_seats_tags"),
        course_attributes=row_get(row, "course_attributes"),
        course_attr_values=row_get(row, "course_attribute_values"),
        requirement_designation=row_get(row, "requirement_designation"),
        related_sections_raw=row_get(row, "related_sections_json"),
    )


def section_from_related(raw: Dict[str, Any]) -> Section:
    cnbr = normalize_class_nbr(raw.get("ClassNbr") or raw.get("class_nbr"))
    subj = first_text(raw.get("Subject")).upper()
    cat = first_text(raw.get("CatalogNbr")).strip()
    cap = to_int(raw.get("ClassCapacity") or raw.get("EnrlCap"))
    tot = to_int(raw.get("ClassTotal") or raw.get("EnrlTot"))
    wc = to_int(raw.get("WaitCap") or raw.get("WaitCapacity"))
    wt = to_int(raw.get("WaitTot"))
    status = first_text(raw.get("StatusDescr"))
    if not status:
        status = {"O": "Open", "W": "Wait List", "C": "Closed"}.get(
            first_text(raw.get("Status")).upper(), "")
    return Section(
        class_nbr=cnbr,
        course_code=normalize_course_code(f"{subj} {cat}") if subj and cat else "",
        subject=subj, catalog_nbr=cat,
        section=first_text(raw.get("Section")),
        component=first_text(raw.get("Component") or raw.get("component")).upper(),
        title=first_text(raw.get("Descr") or raw.get("Description")),
        units=parse_float(raw.get("Units")),
        session_code=first_text(raw.get("SessionCode")),
        status=status,
        enrollment_available=max(cap - tot, 0),
        class_capacity=cap,
        wait_list_available=max(wc - wt, 0),
        wait_list_capacity=wc,
        instructor_names=first_text(raw.get("Instructor")),
        meetings=meetings_from_related_section(raw),
    )


# ── Bundle 构建 ──
def build_section_indexes(rows: Sequence[Any]) -> Tuple[Dict[str, Section], Dict[str, List[Section]]]:
    by_nbr: Dict[str, Section] = {}
    by_code: Dict[str, List[Section]] = {}
    for row in rows:
        s = section_from_row(row)
        if not s.class_nbr:
            continue
        by_nbr[s.class_nbr] = s
        by_code.setdefault(s.course_code, []).append(s)
    return by_nbr, by_code


def _resolve_related(raw_sec: Any, by_nbr: Dict[str, Section]) -> Optional[Section]:
    if isinstance(raw_sec, dict):
        cnbr = normalize_class_nbr(raw_sec.get("ClassNbr") or raw_sec.get("class_nbr"))
        if cnbr in by_nbr:
            return by_nbr[cnbr]
        if cnbr:
            return section_from_related(raw_sec)
    else:
        cnbr = normalize_class_nbr(raw_sec)
        return by_nbr.get(cnbr)
    return None


def _related_option_groups(section: Section, by_nbr: Dict[str, Section]) -> List[List[Optional[Section]]]:
    raw = load_json_value(section.related_sections_raw, {})
    rs_list = []
    if isinstance(raw, list):
        rs_list = raw
    elif isinstance(raw, dict):
        rs_list = raw.get("related_sections", []) or []
    groups: List[List[Optional[Section]]] = []
    for group in rs_list:
        if not isinstance(group, dict):
            continue
        secs: List[Section] = []
        for rs in group.get("Sections", []) or []:
            r = _resolve_related(rs, by_nbr)
            if r and r.class_nbr:
                secs.append(r)
        if not secs:
            continue
        required = bool(group.get("IsRequired", True))
        opts: List[Optional[Section]] = list(secs)
        if not required:
            opts = [None] + opts
        groups.append(opts)

    # 如果 related_sections 为空且是主课，从 enrollment_sections 补充（仅顶层展开）
    if not groups and isinstance(raw, dict) and section.component in PRIMARY_COMPONENTS:
        enrl_list = raw.get("enrollment_sections", []) or []
        by_comp: Dict[str, List[Section]] = {}
        for es in enrl_list:
            if not isinstance(es, dict):
                continue
            r = _resolve_related(es, by_nbr)
            if r and r.class_nbr and r.class_nbr != section.class_nbr:
                comp = r.component or "OTH"
                by_comp.setdefault(comp, []).append(r)
        for comp, secs in by_comp.items():
            groups.append(list(secs))  # enrollment sections 默认 required
    return groups


def _expand_section(section: Section, by_nbr: Dict[str, Section],
                    visiting: Optional[Set[str]] = None) -> List[List[Section]]:
    visiting = set(visiting or set())
    if section.class_nbr in visiting:
        return [[section]]
    visiting.add(section.class_nbr)
    combos: List[List[Section]] = [[section]]
    for opts in _related_option_groups(section, by_nbr):
        next_combos: List[List[Section]] = []
        for combo in combos:
            for opt in opts:
                if opt is None:
                    next_combos.append(combo)
                    continue
                for expanded in _expand_section(opt, by_nbr, set(visiting)):
                    merged = _merge_sections(combo + expanded)
                    next_combos.append(merged)
                    if len(next_combos) >= MAX_BUNDLES_PER_COURSE:
                        break
                if len(next_combos) >= MAX_BUNDLES_PER_COURSE:
                    break
            if len(next_combos) >= MAX_BUNDLES_PER_COURSE:
                break
        combos = next_combos or combos
    return combos[:MAX_BUNDLES_PER_COURSE]


def _merge_sections(sections: Iterable[Section]) -> List[Section]:
    by_nbr: Dict[str, Section] = {}
    for s in sections:
        if s.class_nbr:
            by_nbr[s.class_nbr] = s
    return [by_nbr[n] for n in sorted(by_nbr)]


def make_bundle(sections: List[Section], priority: int = 100,
                query: str = "", label: str = "") -> Bundle:
    return Bundle(
        sections=_merge_sections(sections),
        target_priority=priority,
        target_query=query,
        target_label=label,
    )


# ── 查找课程 ──
def find_sections(course: Course, by_nbr: Dict[str, Section],
                  by_code: Dict[str, List[Section]]) -> List[Section]:
    if course.is_by_nbr:
        s = by_nbr.get(normalize_class_nbr(course.class_nbr))
        return [s] if s else []
    normalized = normalize_course_code(course.name)
    if normalized in by_code:
        return by_code[normalized]
    lower = course.name.lower()
    result = []
    for s in by_nbr.values():
        hay = f"{s.course_code} {s.title} {s.class_nbr}".lower()
        if lower in hay:
            result.append(s)
    return result


# ── ScheduleRequest ──
@dataclass
class ScheduleRequest:
    db_file: str = ""
    term_filter: str = ""
    year_level: int = 0
    class_of: Optional[int] = None
    identity: str = ""

    # 已修课程
    completed_courses: Set[str] = field(default_factory=set)
    # 已经完成的学位进度
    completed_qr_units: float = 0.0
    completed_ah_units: float = 0.0
    completed_ss_units: float = 0.0
    completed_ns_units: float = 0.0
    completed_miniterm_count: int = 0
    completed_pe_units: float = 0.0

    # 硬性条件
    must_include: List[Course] = field(default_factory=list)
    must_exclude: List[Course] = field(default_factory=list)
    blocked_meetings: List[Meeting] = field(default_factory=list)
    availability_mode: str = "open_only"  # open_only / allow_waitlist / include_closed
    ignore_reserved: bool = False
    ignore_grade_req: bool = False
    consent_as_true: bool = False
    equivalent_as_true: bool = False
    recommend_enforced: bool = True
    unknown_as_true: bool = True

    # 学分
    min_units: float = 16.0
    max_units: float = 20.0
    session_min_units: float = 8.0
    session_max_units: float = 10.0
    freshman_s1_max_units: float = 8.0

    # 毕业要求开关
    enforce_miniterm: bool = True

    # PE 和两分课
    pe_count: int = 0
    two_credit_pref_extra: float = 0.0
    allow_two_writing: bool = False

    # 软偏好权重
    major_focus: int = 80    # 0-100，专业倾向
    recitation_pref: int = 100  # 0-100，recitation 偏好
    two_credit_pref: int = 20   # 0-100，两分课偏好
    risk_aversion: int = 50     # 0-100，风险规避
    avoid_early: int = 50       # 0-100，避开早八
    avoid_evening: int = 50     # 0-100，避开晚课
    compactness: int = 50       # -100~100，松散程度（负=紧凑，正=松散）
    day_distribution: int = 50  # -100~100（负=集中，正=均匀）
    pe_gap: int = 50            # 0-100，体育课避开正课
    total_time: int = 0         # 0-100，减少总上课时间

    target_courses: List[Course] = field(default_factory=list)
    prefer_professors: List[Tuple[str, int]] = field(default_factory=list)   # (name, -100~100)
    prefer_attrs: Dict[str, int] = field(default_factory=dict)  # {attr_code: -100~100}

    major_courses: Set[str] = field(default_factory=set)
    major_n_in_m: List[Dict[str, Any]] = field(default_factory=list)

    random_seed: str = ""
    max_results: int = 8
    max_attempts: int = 8000
    target_multiplier: int = 3  # 生成 max_results * target_multiplier 个后停止

    # 是否从 DKU 读取 enrolled courses 并添加到 must_include
    load_enrolled: bool = False


# ── 硬性约束验证 ──
def validate_bundle(bundle: Bundle, req: ScheduleRequest,
                    selected_codes: Set[str] = None,
                    skip_availability: bool = False) -> None:
    """验证一个 bundle 是否满足硬性约束，结果写入 bundle.blocked_reasons/warnings/review_notes。
       skip_availability=True 时跳过名额和 reserved seat 检查（用于已选课）。"""
    bundle.blocked_reasons = []
    bundle.warnings = []
    bundle.review_notes = []

    for section in bundle.sections:
        # 排除
        if section.class_nbr in {c.class_nbr for c in req.must_exclude if c.is_by_nbr}:
            bundle.blocked_reasons.append(f"{section.display_name} 被手动排除。")
            return
        exc_names = {normalize_course_code(c.name) for c in req.must_exclude if not c.is_by_nbr}
        if section.course_code in exc_names:
            bundle.blocked_reasons.append(f"{section.display_name} 被手动排除。")
            return

        # 名额可用性（must_include 已选课跳过）
        if not skip_availability:
            avail = _check_availability(section, req)
            if avail["blocked"]:
                bundle.blocked_reasons.append(avail["message"])
                return
            if avail["message"]:
                bundle.warnings.append(avail["message"])

        # 大一不能上 300/400
        if req.year_level == FIRST_YEAR_RESTRICTION["year_level"]:
            prefix = section.catalog_prefix
            if prefix in FIRST_YEAR_RESTRICTION["restricted_prefixes"]:
                bundle.blocked_reasons.append(
                    f"{section.display_name} 是 {prefix}00 级课，大一不能上。")
                return

        # Reserved seat（must_include 已选课跳过）
        if not skip_availability:
            reserved = _check_reserved(section, req)
            if reserved["blocked"]:
                bundle.blocked_reasons.append(reserved["message"])
                return
            if reserved["message"]:
                if reserved.get("review"):
                    bundle.review_notes.append(reserved["message"])
                else:
                    bundle.warnings.append(reserved["message"])

    # Requisite 检测
    req_ok, req_warn, req_rev = _check_requisites(bundle, req, selected_codes)
    if not req_ok:
        bundle.blocked_reasons.extend(req_warn)
        return
    bundle.warnings.extend(req_warn)
    bundle.review_notes.extend(req_rev)


def _check_availability(section: Section, req: ScheduleRequest) -> Dict[str, Any]:
    if req.availability_mode == "include_closed":
        if section.enrollment_available > 0:
            return {"blocked": False, "message": ""}
        return {"blocked": False, "message": f"{section.display_name} 已满。"}
    if section.enrollment_available > 0 and section.is_open:
        return {"blocked": False, "message": ""}
    if req.availability_mode == "allow_waitlist" and section.is_waitlist:
        return {"blocked": False, "message": f"{section.display_name} 无开放名额但可候补。"}
    return {"blocked": True, "message": f"{section.display_name} 没有可用名额。"}


def _check_reserved(section: Section, req: ScheduleRequest) -> Dict[str, Any]:
    if req.ignore_reserved:
        return {"blocked": False, "review": False, "message": ""}
    caps = section.reserved_data if isinstance(section.reserved_data, list) else []
    if not caps:
        return {"blocked": False, "review": False, "message": ""}
    matching_avail = 0
    known_avail = 0
    unknown_notes = []
    for cap in caps:
        avail = to_int(cap.get("available"))
        known_avail += max(avail, 0)
        info = cap.get("description_parse", {}) if isinstance(cap, dict) else {}
        conds = info.get("conditions", []) or []
        if not conds:
            continue
        has_unknown = any(c.get("type") == "unknown" for c in conds)
        if has_unknown:
            if avail > 0:
                unknown_notes.append(first_text(cap.get("description"), "未知 reserved seat"))
            continue
        if all(_reserved_cond_matches(c, req) for c in conds):
            matching_avail += max(avail, 0)
    unreserved = max(section.enrollment_available - known_avail, 0)
    if matching_avail > 0 or unreserved > 0:
        msg = ""
        if unknown_notes:
            msg = f"{section.display_name} 有未解析 reserved seat：{'；'.join(unknown_notes)}"
        return {"blocked": False, "review": bool(unknown_notes), "message": msg}
    if not req.identity and not req.year_level:
        return {"blocked": False, "review": True, "message": f"{section.display_name} 有 reserved seat 但个人信息未设。"}
    if unknown_notes:
        return {"blocked": False, "review": True,
                "message": f"{section.display_name} 只剩未解析 reserved seat：{'；'.join(unknown_notes)}"}
    return {"blocked": True, "review": False,
            "message": f"{section.display_name} 的 reserved seat 不匹配你的年级/身份。"}


def _reserved_cond_matches(condition: Dict[str, Any], req: ScheduleRequest) -> bool:
    ct = condition.get("type", "")
    vals = condition.get("values", []) or []
    if ct == "class_year":
        return req.class_of is not None and req.class_of in vals
    if ct == "year_level":
        return req.year_level is not None and req.year_level in vals
    if ct == "student_identity":
        if not req.identity:
            return False
        for v in vals:
            if req.identity == v or req.identity.startswith(v + "-"):
                return True
        return False
    return False


def _check_requisites(bundle: Bundle, req: ScheduleRequest,
                      selected_codes: Set[str]) -> Tuple[bool, List[str], List[str]]:
    """检测 bundle 中所有 section 的 requisite，返回 (ok, warnings, review_notes)。"""
    warnings: List[str] = []
    reviews: List[str] = []
    seen = set()
    for section in bundle.sections:
        if not section.requisites_parsed or section.course_code in seen:
            continue
        seen.add(section.course_code)
        tree = section.requisites_parsed.get("normalized_eval_tree") or \
               section.requisites_parsed.get("eval_tree")
        if not tree:
            continue
        ok, w, r = _eval_tree(tree, req,
                              (selected_codes - {section.course_code}) if selected_codes is not None else None)
        if not ok:
            warnings.append(f"{section.display_name} 先修/同修不满足：{'；'.join(w) or '未知原因'}")
        reviews.extend(f"{section.display_name}: {x}" for x in r)
    return not warnings, warnings, reviews


def _eval_tree(node: Any, req: ScheduleRequest, selected_codes: Set[str]) -> Tuple[bool, List[str], List[str]]:
    if not isinstance(node, dict):
        return True, [], []
    nt = node.get("type")
    if nt == "bool":
        v = bool(node.get("value"))
        return v, ([] if v else ["条件为 False"]), []
    if nt == "and":
        bs = []
        rs = []
        for item in node.get("items", []) or []:
            ok, bi, ri = _eval_tree(item, req, selected_codes)
            if not ok:
                bs.extend(bi)
            rs.extend(ri)
        return not bs, bs, rs
    if nt == "or":
        all_bs = []
        all_rs = []
        for item in node.get("items", []) or []:
            ok, bi, ri = _eval_tree(item, req, selected_codes)
            if ok:
                return True, [], ri
            all_bs.extend(bi)
            all_rs.extend(ri)
        return False, all_bs, all_rs
    if nt == "not":
        ok, bs, rs = _eval_tree(node.get("item"), req, selected_codes)
        return not ok, ([] if not ok else ["反修课程已满足"]), rs
    if nt in {"pre", "co", "pre_or_co"}:
        return _eval_relation(node, nt, req, selected_codes)
    if nt == "recommend":
        ok, bs, rs = _eval_relation(node, "pre", req, selected_codes)
        if req.recommend_enforced and not ok:
            return False, bs, rs
        if not ok:
            rs.append("推荐未满足（按设置已忽略）")
        return True, [], rs
    if nt == "course":
        code = normalize_course_code(node.get("code", ""))
        ok = code_in_set(code, req.completed_courses | selected_codes)
        return ok, ([] if ok else [f"需要 {code}"]), []
    if nt == "special":
        return _eval_special(node, req)
    return True, [], [f"未识别节点 {nt}，需人工确认"]


def _eval_relation(node: Dict[str, Any], relation: str, req: ScheduleRequest,
                   selected_codes: Set[str]) -> Tuple[bool, List[str], List[str]]:
    bs = []
    rs = []
    for item in node.get("items", []) or []:
        if isinstance(item, dict) and item.get("type") == "course":
            code = normalize_course_code(item.get("code", ""))
            if relation == "pre":
                # 预过滤阶段（selected_codes 为 None）放行；最终检查严格验证
                if selected_codes is None:
                    continue
                ok = code_in_set(code, req.completed_courses | (selected_codes or set()))
                if not ok:
                    bs.append(f"需要已修 {code}")
            elif relation == "co":
                if selected_codes is None:
                    continue
                ok = code_in_set(code, req.completed_courses | (selected_codes or set()))
                if not ok:
                    bs.append(f"需要同修 {code}")
            else:  # pre_or_co
                if code_in_set(code, req.completed_courses):
                    continue
                if selected_codes is None:
                    continue
                ok = code_in_set(code, selected_codes or set())
                if not ok:
                    bs.append(f"需要已修或同修 {code}")
        elif isinstance(item, dict) and item.get("type") == "special":
            ok, ib, ir = _eval_special(item, req)
            if not ok:
                bs.extend(ib)
            rs.extend(ir)
        else:
            ok, ib, ir = _eval_tree(item, req, selected_codes)
            if not ok:
                bs.extend(ib)
            rs.extend(ir)
    return not bs, bs, rs


def _eval_special(node: Dict[str, Any], req: ScheduleRequest) -> Tuple[bool, List[str], List[str]]:
    cond = node.get("condition", "")
    if cond in {"standing", "standing_above", "grade"}:
        if req.ignore_grade_req:
            return True, [], ["年级/等级要求已忽略"]
        required = to_int(node.get("grade"))
        if not required or not req.year_level:
            return True, [], ["年级信息未设，跳过年级要求检测"]
        if cond == "standing":
            ok = req.year_level == required
        else:
            ok = req.year_level >= required
        return ok, ([] if ok else [f"需要 Y{required}{'或以上' if cond != 'standing' else ''}"]), []
    if cond == "consent_of_instructor":
        if req.consent_as_true:
            return True, [], ["假设已获得 Instructor Consent"]
        return False, ["需要 Instructor Consent"], []
    if cond in {"equivalent_course_or_background", "equiv"}:
        eq = node.get("equivalent_to", "")
        if req.equivalent_as_true:
            return True, [], [f"假设满足 equivalent：{eq}" if eq else "假设满足 equivalent 条件"]
        return False, [f"需要 equivalent {eq}" if eq else "需要 equivalent 条件"], []
    if cond == "placement":
        return req.unknown_as_true, ([] if req.unknown_as_true else ["需要 placement test"]), \
               (["需要 placement test（按设置假设满足）"] if req.unknown_as_true else [])
    text = first_text(node.get("text"), cond, "特殊条件")
    if req.unknown_as_true:
        return True, [], [f"未知条件假设满足：{text}"]
    return False, [f"特殊条件：{text}"], []


# ── 提名额计算（用于风险规避） ──
def compute_availability_ratio(bundle: Bundle, req: ScheduleRequest) -> float:
    """计算 bundle 的名额可用率 (0.0 ~ 1.0)，用于风险规避。"""
    ratios = []
    for s in bundle.sections:
        cap = s.class_capacity
        if cap <= 0:
            ratios.append(0.3)
            continue
        avail = s.enrollment_available
        if not req.ignore_reserved and isinstance(s.reserved_data, list) and s.reserved_data:
            matching = 0
            known = 0
            for cap_item in s.reserved_data:
                a = to_int(cap_item.get("available"))
                known += max(a, 0)
                info = cap_item.get("description_parse", {}) if isinstance(cap_item, dict) else {}
                conds = info.get("conditions", []) or []
                if not conds:
                    continue
                if all(_reserved_cond_matches(c, req) for c in conds):
                    matching += max(a, 0)
            avail = max(s.enrollment_available - known, 0) + matching
        ratios.append(max(avail, 0) / cap)
    return sum(ratios) / max(len(ratios), 1)


# ── 时间冲突检测 ──
def time_conflict(existing_meetings: List[Meeting], bundle: Bundle) -> Optional[str]:
    for m in bundle.meetings:
        for o in existing_meetings:
            if m.overlaps(o):
                return f"{bundle.primary_code} {m.day} {m.start_label}-{m.end_label} 与 {o.source or o.location} 冲突"
    return None


def _compute_gap_score(selected: List[Bundle], blocked: List[Meeting], req: ScheduleRequest) -> float:
    """松散度评分：按 Session 分别计算每天空闲时间方差。方差越低越松散。"""
    s1_gaps: List[float] = []
    s2_gaps: List[float] = []

    for b in selected:
        is_miniterm = any(s.subject.upper() == "MINITERM" for s in b.sections)
        if is_miniterm:
            continue
        for m in b.meetings:
            if m.is_blocked:
                continue
            sc = _session_class(b.sections[0].session_code)
            gap = float(m.end_minute - m.start_minute)
            if sc == "session-s1":
                s1_gaps.append(gap)
            elif sc == "session-s2":
                s2_gaps.append(gap)
            else:
                s1_gaps.append(gap / 2.0)
                s2_gaps.append(gap / 2.0)

    # 按天分组计算空闲时间
    def _session_variance(meetings_for_session):
        """7:00-22:00范围内，每天空闲时段的方差之和。"""
        DAY_START = 7 * 60
        DAY_END = 22 * 60
        by_day: Dict[str, List[Tuple[int, int]]] = {}
        for m in meetings_for_session:
            by_day.setdefault(m.day, []).append((m.start_minute, m.end_minute))
        total_var = 0.0
        for day, intervals in by_day.items():
            intervals.sort()
            gaps = []
            prev_end = DAY_START
            for s, e in intervals:
                if s > prev_end:
                    gaps.append(s - prev_end)
                prev_end = max(prev_end, e)
            if prev_end < DAY_END:
                gaps.append(DAY_END - prev_end)
            if len(gaps) >= 2:
                avg = sum(gaps) / len(gaps)
                total_var += sum((g - avg) ** 2 for g in gaps) / len(gaps)
        return total_var

    # 分别统计 S1 和 S2 的 meetings
    s1_meetings = []
    s2_meetings = []
    for b in selected:
        is_miniterm = any(s.subject.upper() == "MINITERM" for s in b.sections)
        if is_miniterm:
            continue
        for m in b.meetings:
            sc = _session_class(b.sections[0].session_code if b.sections else "")
            if sc == "session-s1":
                s1_meetings.append(m)
            elif sc == "session-s2":
                s2_meetings.append(m)
            else:
                s1_meetings.append(m)
                s2_meetings.append(m)

    var_s1 = _session_variance(s1_meetings)
    var_s2 = _session_variance(s2_meetings)
    total_var = (var_s1 + var_s2) / 3600.0  # 分钟² → 小时²
    # compactness: -100 偏好紧凑(高分=高方差), 100 偏好松散(高分=低方差)
    return -req.compactness * total_var * 0.5


def _compute_day_distribution_score(selected: List[Bundle], req: ScheduleRequest) -> float:
    """天数分布评分。S1/S2分别计算后再求和。忽略Miniterm。
    |Mon+Wed总时间 - Tue+Thu总时间| 越大 → 越集中。"""
    def _session_diff(meetings):
        mw = 0.0
        tt = 0.0
        for m in meetings:
            if m.is_blocked:
                continue
            dur = m.end_minute - m.start_minute
            if m.day in ("Mo", "We"):
                mw += dur
            elif m.day in ("Tu", "Th"):
                tt += dur
        return abs(mw - tt)

    s1_ms = []
    s2_ms = []
    for b in selected:
        if any(s.subject.upper() == "MINITERM" for s in b.sections):
            continue
        for m in b.meetings:
            sc = _session_class(b.sections[0].session_code if b.sections else "")
            if sc == "session-s1":
                s1_ms.append(m)
            elif sc == "session-s2":
                s2_ms.append(m)
            else:
                s1_ms.append(m)
                s2_ms.append(m)
    total_diff = _session_diff(s1_ms) + _session_diff(s2_ms)
    return -req.day_distribution * total_diff / 60.0 * 0.3


def _compute_pe_gap_score(selected: List[Bundle], req: ScheduleRequest) -> float:
    """PE 紧接正课扣分。req.pe_gap > 0 = 不想 PE 紧接正课。"""
    if req.pe_gap <= 0:
        return 0
    pe_meetings: List[Meeting] = []
    regular_meetings: List[Meeting] = []
    for b in selected:
        for s in b.sections:
            if s.is_pe:
                pe_meetings.extend(s.meetings)
            else:
                regular_meetings.extend(s.meetings)
    penalty = 0
    for pe in pe_meetings:
        for reg in regular_meetings:
            if pe.day != reg.day:
                continue
            gap = abs(pe.end_minute - reg.start_minute) if pe.end_minute <= reg.start_minute else \
                  abs(reg.end_minute - pe.start_minute)
            if gap <= 30:
                penalty += (req.pe_gap / 100.0) * 10.0
    return penalty


def _is_freshman_fall(req: ScheduleRequest) -> bool:
    return req.year_level == 1 and str(req.term_filter or "").endswith("8")


# ── 毕业要求检查 ──
def check_graduation_progress(selected: List[Bundle], req: ScheduleRequest) -> List[str]:
    """检查毕业要求缺口，返回提醒列表。"""
    warnings = []
    codes = set()
    for b in selected:
        codes.update(b.course_codes)

    # Common Core
    if req.year_level in COMMON_CORE:
        cc = COMMON_CORE[req.year_level]
        found = bool(set(cc["aliases"]) & codes)
        if not found:
            warnings.append(f"毕业要求：未找到 {cc['name']} ({'/'.join(cc['aliases'])})")

    # Writing course (大一 S1)
    if req.year_level == 1 and _is_freshman_fall(req):
        has_w = False
        for b in selected:
            for s in b.sections:
                if WRITING_REQUIREMENT["attr"] in (s.course_attr_values or "").upper():
                    has_w = True
                    break
        if not has_w:
            warnings.append("毕业要求：大一第一 Session 未找到写作课 (W)")

    # DKU 101
    if req.year_level == 1 and _is_freshman_fall(req):
        found_dku = DKU101_REQUIREMENT["course"] in codes
        if not found_dku:
            warnings.append(f"毕业要求提醒：未找到 {DKU101_REQUIREMENT['course']}（0学分）")

    # 语言课
    lang_courses = get_language_courses_for_term(
        req.year_level, req.identity, req.term_filter)
    for lc in lang_courses:
        found = lc in codes
        if not found:
            warnings.append(f"毕业要求：语言课 {lc} 未找到")

    # Capstone
    if req.year_level == 4:
        found_cap = sum(1 for c in CAPSTONE_REQUIREMENT["courses"] if c in codes)
        if found_cap < 2:
            warnings.append(f"毕业要求：大四需 {2 - found_cap} 门 Capstone 课")

    return warnings


# ── 课表时间线 ──
@dataclass
class ScheduleResult:
    selected: List[Bundle]
    total_score: float
    total_units: float
    warnings: List[str]
    review_notes: List[str]
    timeline: List[Dict[str, Any]]
    active_days: int
    total_gap_minutes: int


def build_timeline(selected: List[Bundle], blocked: List[Meeting]) -> List[Dict[str, Any]]:
    entries = []
    for ci, bundle in enumerate(selected):
        bundle.color_index = ci % 10
        is_miniterm = any(s.subject.upper() == "MINITERM" for s in bundle.sections)
        if is_miniterm:
            continue
        for section in bundle.sections:
            for m in section.meetings:
                entries.append({
                    "day": m.day,
                    "day_label": DAY_LABELS.get(m.day, m.day),
                    "start": m.start_label,
                    "end": m.end_label,
                    "title": f"{section.course_code} {section.component} {section.section}".strip(),
                    "subtitle": first_text(section.title, section.instructor_names),
                    "location": m.location,
                    "instructor": section.instructor_names,
                    "class_nbr": section.class_nbr,
                    "session": section.session_code,
                    "session_class": _session_class(section.session_code),
                    "color_class": f"bundle-color-{ci % 10}",
                    "grid_column": DAY_ORDER.index(m.day) + 2 if m.day in DAY_ORDER else 1,
                    "grid_row": max(2, (m.start_minute - GRID_START_MINUTE) // GRID_STEP_MINUTE + 2),
                    "grid_span": max(1, math.ceil((m.end_minute - m.start_minute) / GRID_STEP_MINUTE)),
                    "sort_key": (DAY_ORDER.index(m.day) if m.day in DAY_ORDER else 99, m.start_minute),
                })
    for m in blocked:
        entries.append({
            "day": m.day,
            "day_label": DAY_LABELS.get(m.day, m.day),
            "start": m.start_label,
            "end": m.end_label,
            "title": m.location or "不可用",
            "subtitle": m.source or "",
            "session_class": "session-blocked",
            "color_class": "bundle-blocked",
            "grid_column": DAY_ORDER.index(m.day) + 2 if m.day in DAY_ORDER else 1,
            "grid_row": max(2, (m.start_minute - GRID_START_MINUTE) // GRID_STEP_MINUTE + 2),
            "grid_span": max(1, math.ceil((m.end_minute - m.start_minute) / GRID_STEP_MINUTE)),
            "sort_key": (DAY_ORDER.index(m.day) if m.day in DAY_ORDER else 99, m.start_minute),
        })
    # 列内重叠：S1 靠左，S2 靠右
    _assign_overlap_positions(entries)
    return sorted(entries, key=lambda e: e["sort_key"])


def _assign_overlap_positions(entries: List[Dict[str, Any]]) -> None:
    """同列同时间段重叠 → S1全左 S2全右，各50%宽。"""
    by_col: Dict[int, List[Dict]] = {}
    for e in entries:
        if e.get("session_class") == "session-blocked":
            continue
        by_col.setdefault(e.get("grid_column", 1), []).append(e)

    for col_entries in by_col.values():
        col_entries.sort(key=lambda e: e["grid_row"])
        i = 0
        while i < len(col_entries):
            group = [col_entries[i]]
            a_e = col_entries[i]["grid_row"] + col_entries[i]["grid_span"]
            j = i + 1
            while j < len(col_entries):
                b_s = col_entries[j]["grid_row"]
                if b_s < a_e:
                    group.append(col_entries[j])
                    a_e = max(a_e, b_s + col_entries[j]["grid_span"])
                else:
                    break
                j += 1
            if len(group) > 1:
                # 检查是否有 S1+S2 混合
                has_s1 = any(_session_overlap_order(e.get("session", "")) == 0 for e in group)
                has_s2 = any(_session_overlap_order(e.get("session", "")) == 2 for e in group)
                if has_s1 and has_s2:
                    for e in group:
                        e["overlap_count"] = 2
                        e["overlap_index"] = 0 if _session_overlap_order(e.get("session", "")) == 0 else 1
                i = j
            else:
                i += 1


def _session_overlap_order(session_code: str) -> int:
    """重叠时位置：S1=0 左, S2=2 右, 其他=1 中"""
    t = str(session_code or "").upper()
    if "7W1" in t:
        return 0
    if "7W2" in t:
        return 2
    return 1


# ── 主课 & 池子 ──
@dataclass
class MainCourse:
    """一个'主课'：同一个 course_code 下所有 bundle 的集合。"""
    course_code: str
    title: str = ""
    bundles: List[Bundle] = field(default_factory=list)
    is_pe: bool = False
    is_miniterm: bool = False
    is_two_credit: bool = False  # 平均每session ~2学分
    attrs: Set[str] = field(default_factory=set)  # AH, SS, NS, QR, DINS 等
    completed: bool = False  # 已修→踢出池子

    @property
    def display(self) -> str:
        return self.course_code


def _build_main_courses(by_nbr: Dict[str, Section], by_code: Dict[str, List[Section]],
                         request: ScheduleRequest) -> Dict[str, MainCourse]:
    """遍历所有 section 构建 MainCourse 字典。key = normalize_course_code。"""
    mc_map: Dict[str, MainCourse] = {}
    ex_names = {normalize_course_code(c.name) for c in request.must_exclude if not c.is_by_nbr}
    ex_nbrs = {c.class_nbr for c in request.must_exclude if c.is_by_nbr}

    for code, sections in by_code.items():
        title = ""
        bundles: List[Bundle] = []
        seen_b_keys: Set[str] = set()
        is_pe = False
        is_miniterm = False
        attrs: Set[str] = set()

        for section in sections:
            if section.class_nbr in ex_nbrs:
                continue
            if section.component not in PRIMARY_COMPONENTS:
                continue
            if not section.meetings:
                continue
            # 大一不能上300/400
            if request.year_level == 1 and section.catalog_prefix in ("3", "4"):
                continue

            title = title or section.title
            if section.subject.upper() == "PHYSEDU":
                is_pe = True
            if section.subject.upper() == "MINITERM":
                is_miniterm = True

            # 收集属性
            av = (section.course_attr_values or "").upper()
            for tag in ["AH", "SS", "NS", "QR", "DINS", "CURR-WRITING"]:
                if tag in av:
                    attrs.add(tag)
            if "DVSN-ARTHUM" in av:
                attrs.add("AH")
            if "DVSN-SOCSCI" in av:
                attrs.add("SS")
            if "DVSN-NATSCI" in av:
                attrs.add("NS")

            for combo in _expand_section(section, by_nbr):
                b = make_bundle(combo, priority=50)
                if b.key and b.key not in seen_b_keys:
                    seen_b_keys.add(b.key)
                    # 验证硬性条件
                    validate_bundle(b, request)
                    if not b.blocked_reasons:
                        bundles.append(b)
                if len(bundles) >= 20:
                    break
            if len(bundles) >= 20:
                break

        if not bundles:
            continue
        if code in ex_names:
            continue

        # 身份过滤：中国学生不上中文基础课（101-202, 131），国际学生不上 EAP
        subj = code.split()[0] if " " in code else ""
        cat = code.split()[1] if " " in code and len(code.split()) > 1 else ""
        if request.identity == "chinese" and subj == "CHINESE":
            if cat in ("101A", "101B", "102A", "102B", "131A", "131B",
                       "201A", "201B", "202A", "202B"):
                continue  # CSL基础课/Heritage课，中国学生不上
        if request.identity == "international-heritage" and subj == "CHINESE":
            if cat in ("101A", "101B", "102A", "102B",
                       "201A", "201B", "202A", "202B"):
                continue
        if request.identity == "international-other" and subj == "CHINESE":
            continue  # 不上中文课
        if request.identity and request.identity.startswith("international") and subj == "EAP":
            continue

        # 判断是否两分课：按每个 session 的学分算
        total_u = sum(b.units for b in bundles)
        avg_u = total_u / len(bundles) if bundles else 0
        # 全 term 课每 session 只占一半学分
        session_u = avg_u
        if bundles:
            sc = bundles[0].sections[0].session_code if bundles[0].sections else ""
            if _session_class(sc) == "session-full":
                session_u = avg_u / 2.0
        is_two = session_u <= 2.5 and session_u > 0.5

        mc = MainCourse(
            course_code=code, title=title, bundles=bundles,
            is_pe=is_pe, is_miniterm=is_miniterm,
            is_two_credit=is_two, attrs=attrs,
            completed=code_in_set(code, request.completed_courses),
        )
        mc_map[code] = mc

    return mc_map


def _build_pools(mc_map: Dict[str, MainCourse], request: ScheduleRequest
                 ) -> Tuple[List[MainCourse], List[MainCourse], List[MainCourse],
                            List[MainCourse], List[MainCourse], List[MainCourse],
                            Dict[str, MainCourse]]:
    """构建六个池子：专业、兴趣、其他4分、小课、PE、Minitem。返回(major, interest, other, small, pe, miniterm, mc_map)."""
    major: List[MainCourse] = []
    interest: List[MainCourse] = []
    other: List[MainCourse] = []
    small: List[MainCourse] = []
    pe: List[MainCourse] = []
    miniterm_list: List[MainCourse] = []

    major_codes = {normalize_course_code(c) for c in request.major_courses}
    # 也把 n_in_m 组内的 codes 加入 major_codes
    for group in request.major_n_in_m:
        for c in group.get("codes", []):
            major_codes.add(normalize_course_code(c))

    interest_codes = {normalize_course_code(c.name) for c in request.target_courses}

    for code, mc in mc_map.items():
        if mc.completed:
            continue
        if mc.is_pe:
            pe.append(mc)
        elif mc.is_miniterm:
            miniterm_list.append(mc)
        elif code in major_codes:
            major.append(mc)
        elif code in interest_codes:
            interest.append(mc)
        elif mc.is_two_credit:
            small.append(mc)
        else:
            other.append(mc)

    return major, interest, other, small, pe, miniterm_list, mc_map


def _draw_main_course(pool: List[MainCourse], attr_prefs: Dict[str, float],
                       exclude_codes: Set[str]) -> Optional[MainCourse]:
    """从池中按属性权重随机抽取一个主课（排除已选的课号）。"""
    available = [mc for mc in pool if mc.course_code not in exclude_codes]
    if not available:
        return None
    weights = []
    for mc in available:
        w = 1.0
        for attr, pref in attr_prefs.items():
            if attr in mc.attrs:
                w *= math.exp(pref) if pref != 0 else 1.0
        weights.append(max(w, 0.01))
    total_w = sum(weights)
    r = total_w * (hashlib.sha256(str(random.random()).encode()).digest()[0] / 255.0)
    # 简单随机：用Python random
    r = random.random() * total_w
    cumulative = 0.0
    for i, mc in enumerate(available):
        cumulative += weights[i]
        if r <= cumulative:
            return mc
    return available[-1]


def _select_bundle(mc: MainCourse, recitation_pref: int) -> Optional[Bundle]:
    """从主课的 bundle 列表中选一个。优先完整 bundle（节数多）。"""
    bundles = sorted(mc.bundles, key=lambda b: len(b.sections), reverse=True)
    if not bundles:
        return None
    rec_bundles = [b for b in bundles if any(s.component == "REC" for s in b.sections)]
    non_rec = [b for b in bundles if b not in rec_bundles]
    if recitation_pref >= 100:
        pool = rec_bundles or bundles
        return random.choice(pool)
    if recitation_pref <= 0:
        pool = non_rec or bundles
        return random.choice(pool)
    weights = []
    for b in bundles:
        has_rec = b in rec_bundles
        w = recitation_pref / 100.0 if has_rec else 1.0 - recitation_pref / 100.0
        weights.append(max(w, 0.01))
    total_w = sum(weights)
    r = random.random() * total_w
    cumulative = 0.0
    for i, b in enumerate(bundles):
        cumulative += weights[i]
        if r <= cumulative:
            return b
    return random.choice(bundles)


def _can_insert(bundle: Bundle, selected: List[Bundle], meetings: List[Meeting],
                codes: Set[str], units_s1: float, units_s2: float,
                request: ScheduleRequest) -> Tuple[bool, str]:
    """检查 bundle 能否塞进当前课表。返回(可以, 拒绝原因)。"""
    # 课程重复
    if codes & bundle.course_codes:
        return False, f"{bundle.primary_code} 已选同课号"
    # 时间冲突
    for m in bundle.meetings:
        for existing in meetings:
            if m.overlaps(existing):
                return False, f"{bundle.primary_code} 时间冲突 {m.day} {m.start_label}"
    # 学分上限（体育课不计入）
    new_s1 = units_s1 + bundle.session_s1_units
    new_s2 = units_s2 + bundle.session_s2_units
    non_pe_s1 = sum(b.session_s1_units for b in selected if not b.sections[0].is_pe)
    non_pe_s2 = sum(b.session_s2_units for b in selected if not b.sections[0].is_pe)
    if not bundle.sections[0].is_pe:
        non_pe_s1 += bundle.session_s1_units
        non_pe_s2 += bundle.session_s2_units
    non_pe_total = sum(b.units for b in selected if not b.sections[0].is_pe)
    if not bundle.sections[0].is_pe:
        non_pe_total += bundle.units
    s1_max = request.freshman_s1_max_units if _is_freshman_fall(request) else request.session_max_units
    if non_pe_s1 > s1_max + 0.01:
        return False, f"S1 {non_pe_s1:g}>{s1_max:g}"
    if non_pe_s2 > request.session_max_units + 0.01:
        return False, f"S2 {non_pe_s2:g}>{request.session_max_units:g}"
    if non_pe_total > request.max_units + 2:
        return False, f"总学分 {non_pe_total:g}>{request.max_units:g}"
    # PE per session
    pe_s1 = sum(1 for b in selected for s in b.sections if s.is_pe and _session_class(s.session_code) == "session-s1")
    pe_s2 = sum(1 for b in selected for s in b.sections if s.is_pe and _session_class(s.session_code) == "session-s2")
    if bundle.sections[0].is_pe:
        for s in bundle.sections:
            if s.is_pe:
                sc = _session_class(s.session_code)
                if sc == "session-s1" and pe_s1 >= COURSE_LOAD_RULES["pe_max_per_session"]:
                    return False, "S1 PE已达上限"
                if sc == "session-s2" and pe_s2 >= COURSE_LOAD_RULES["pe_max_per_session"]:
                    return False, "S2 PE已达上限"
    return True, ""


# ── 排课辅助 ──
def _inject_graduation(mc_map, selected, meetings, codes, request, _add):
    """毕业要求注入：Common Core + Writing + DKU101 + 语言课。"""
    yl = request.year_level
    term = request.term_filter
    identity = request.identity
    is_s1_freshman = term.endswith("8") and yl == 1

    # Common Core
    if yl in COMMON_CORE:
        aliases = list(COMMON_CORE[yl]["aliases"])
        random.shuffle(aliases)
        for alias in aliases:
            key = normalize_course_code(alias)
            if key in mc_map and key not in codes:
                mc = mc_map[key]
                if not mc.completed:
                    for _ in range(10):
                        b = _select_bundle(mc, request.recitation_pref)
                        us1 = sum(x.session_s1_units for x in selected)
                        us2 = sum(x.session_s2_units for x in selected)
                        if b and _can_insert(b, selected, meetings, codes, us1, us2, request)[0]:
                            _add(b)
                            break

    # Writing (大一S1)
    if is_s1_freshman:
        w_courses = [mc for code, mc in mc_map.items()
                     if not mc.completed and code not in codes
                     and WRITING_REQUIREMENT["attr"] in "".join(mc.attrs)]
        if w_courses:
            random.shuffle(w_courses)
            for mc in w_courses:
                for _ in range(10):
                    b = _select_bundle(mc, request.recitation_pref)
                    us1 = sum(x.session_s1_units for x in selected)
                    us2 = sum(x.session_s2_units for x in selected)
                    if b and _can_insert(b, selected, meetings, codes, us1, us2, request)[0]:
                        _add(b)
                        break
                else:
                    continue
                break

    # DKU 101
    if is_s1_freshman:
        key = normalize_course_code(DKU101_REQUIREMENT["course"])
        if key in mc_map and key not in codes:
            mc = mc_map[key]
            if not mc.completed:
                for _ in range(10):
                    b = _select_bundle(mc, request.recitation_pref)
                    us1 = sum(x.session_s1_units for x in selected)
                    us2 = sum(x.session_s2_units for x in selected)
                    if b and _can_insert(b, selected, meetings, codes, us1, us2, request)[0]:
                        _add(b)
                        break

    # 语言课
    lang_codes = get_language_courses_for_term(yl, identity, term, is_s1_freshman)
    for lc in lang_codes:
        key = normalize_course_code(lc)
        if key in mc_map and key not in codes:
            mc = mc_map[key]
            if not mc.completed:
                for _ in range(10):
                    b = _select_bundle(mc, request.recitation_pref)
                    us1 = sum(x.session_s1_units for x in selected)
                    us2 = sum(x.session_s2_units for x in selected)
                    if b and _can_insert(b, selected, meetings, codes, us1, us2, request)[0]:
                        _add(b)
                        break


def _try_build_one(major_pool, interest_pool, other_pool, small_pool, pe_pool, miniterm_pool,
                    mc_map, by_nbr, request, attr_prefs) -> Tuple[Optional[ScheduleResult], str]:
    """随机抽样构建一个课表。返回 (schedule_or_None, 失败原因)。"""

    selected: List[Bundle] = []
    meetings: List[Meeting] = list(request.blocked_meetings)
    codes: Set[str] = set()
    grad_codes: Set[str] = set()  # 毕业注入的课号，pre-req 失败时不允许踢
    units_s1 = 0.0
    units_s2 = 0.0
    pe_count = 0
    code_session: Dict[str, int] = {}  # 0=S1, 1=full, 2=S2

    def _add(bundle):
        nonlocal units_s1, units_s2, pe_count
        selected.append(bundle)
        meetings.extend(bundle.meetings)
        codes.update(bundle.course_codes)
        units_s1 += bundle.session_s1_units
        units_s2 += bundle.session_s2_units
        sess = bundle.session_sort_key
        for cc in bundle.course_codes:
            old = code_session.get(cc, 1)
            # 如果一个课号同时出现在S1和S2，记作full
            if old != sess and old != 1:
                code_session[cc] = 1
            else:
                code_session[cc] = sess
        if bundle.sections[0].is_pe:
            pe_count += 1

    # ── 0. 一定要报的课（最优先）──
    # 先收集所有 by_nbr must_include，按 course_code 合并去重
    must_include_by_course: Dict[str, List[str]] = {}  # course_code -> [nbrs]
    must_include_by_name: List[Course] = []
    for course in request.must_include:
        if course.is_by_nbr:
            nbr = normalize_class_nbr(course.class_nbr)
            if nbr not in by_nbr:
                return None, f"必须报的课号 #{nbr} 在当前数据库中未找到"
            cc = by_nbr[nbr].course_code
            if cc not in must_include_by_course:
                must_include_by_course[cc] = []
            must_include_by_course[cc].append(nbr)
        else:
            must_include_by_name.append(course)

    # 为每个不同的 course_code 构建一个合并 bundle
    for cc, nbrs in must_include_by_course.items():
        # 以用户指定的 section 为基础
        base_secs: List[Section] = [by_nbr[n] for n in nbrs]
        base_components: Set[str] = {s.component for s in base_secs if s.component}

        # 用第一个 nbr 的 expansion 找出缺的 component 类型（用户没选但该课需要的）
        first_sec = by_nbr[nbrs[0]]
        combos = list(_expand_section(first_sec, by_nbr))
        # 从所有 combo 中收集每种 component 的候选 section，优先不重复时间
        component_candidates: Dict[str, List[Section]] = {}
        for combo in combos:
            for s in combo:
                if s.class_nbr and s.course_code == cc and s.component not in base_components:
                    component_candidates.setdefault(s.component, []).append(s)

        # 只补用户没选的 component 类型，每种取一个（仅选开放名额的 section）
        extra_secs: List[Section] = []
        for comp, candidates in component_candidates.items():
            # 去重（同 class_nbr 只取一次），仅保留有开放名额的 section
            seen_nbrs: Set[str] = set()
            picked = None
            for c in candidates:
                if c.class_nbr in seen_nbrs:
                    continue
                seen_nbrs.add(c.class_nbr)
                avail = _check_availability(c, request)
                if not avail["blocked"]:
                    picked = c
                    break
            if picked:
                extra_secs.append(picked)
            else:
                return None, f"必须报的课 {cc} 缺 {comp} 组件，且无开放名额可用"

        b = make_bundle(base_secs + extra_secs, priority=50, label=f"must:{','.join(nbrs)}")
        # 验证（must_include 是已选课，跳过名额/reserved 检查）
        validate_bundle(b, request, skip_availability=True)
        if b.blocked_reasons:
            return None, f"必须报的课 {cc} 不可用：{';'.join(b.blocked_reasons)}"
        conflict = time_conflict(meetings, b)
        if conflict:
            return None, conflict
        _add(b)

    # 按课名的 must_include
    for course in must_include_by_name:
        key = normalize_course_code(course.name)
        if key in mc_map:
            mc = mc_map[key]
            if mc.bundles:
                ok = False
                for _ in range(10):
                    b = _select_bundle(mc, request.recitation_pref)
                    conflict = time_conflict(meetings, b)
                    us1 = sum(x.session_s1_units for x in selected)
                    us2 = sum(x.session_s2_units for x in selected)
                    non_pe_total = sum(x.units for x in selected if not x.sections[0].is_pe)
                    if not b.sections[0].is_pe:
                        non_pe_total += b.units
                    if not conflict and non_pe_total <= request.max_units + 2:
                        can, _ = _can_insert(b, selected, meetings, codes, us1, us2, request)
                        if can:
                            _add(b)
                            ok = True
                            break
                if not ok:
                    return None, f"必须报的课 {course.name} 无可用时间或超额"

    # ── 1. 毕业要求 ──
    _inject_graduation(mc_map, selected, meetings, codes, request, _add)
    # Update tracking vars after injection
    units_s1 = sum(b.session_s1_units for b in selected)
    units_s2 = sum(b.session_s2_units for b in selected)
    pe_count = sum(1 for b in selected for s in b.sections if s.is_pe)

    # ── 分布要求注入：QR/AH/SS/NS 各4分 ──
    dist_attrs = {"QR": request.completed_qr_units, "AH": request.completed_ah_units,
                  "SS": request.completed_ss_units, "NS": request.completed_ns_units}
    for attr, completed_credits in dist_attrs.items():
        if completed_credits >= 4.0:
            continue  # 已满足，跳过
        # 收集所有带此属性的4分课(未选的，非已修)
        pool = [mc for code, mc in mc_map.items()
                if attr in mc.attrs and not mc.completed and code not in codes
                and any(b.units >= 3.5 for b in mc.bundles)]
        if not pool:
            continue  # 库里没有，跳过不卡表
        random.shuffle(pool)
        for mc in pool:
            us1 = sum(x.session_s1_units for x in selected)
            us2 = sum(x.session_s2_units for x in selected)
            for _ in range(10):
                b = _select_bundle(mc, request.recitation_pref)
                if b and _can_insert(b, selected, meetings, codes, us1, us2, request)[0]:
                    _add(b)
                    break
            else:
                continue
            break

    # ── 1.5 一定要报的课 ──
    for course in request.must_include:
        if course.is_by_nbr:
            # 按课号找
            key = course.class_nbr
            for code, mc in mc_map.items():
                for s in mc.bundles:
                    if any(sec.class_nbr == key for sec in s.sections):
                        # 找到了，直接塞
                        us1 = sum(x.session_s1_units for x in selected)
                        us2 = sum(x.session_s2_units for x in selected)
                        for _ in range(10):
                            b = _select_bundle(mc, request.recitation_pref)
                            if b and _can_insert(b, selected, meetings, codes, us1, us2, request)[0]:
                                _add(b)
                                break
                        break
                else:
                    continue
                break
        else:
            # 按课名找
            key = normalize_course_code(course.name)
            if key in mc_map and key not in codes:
                mc = mc_map[key]
                if not mc.completed:
                    us1 = sum(x.session_s1_units for x in selected)
                    us2 = sum(x.session_s2_units for x in selected)
                    for _ in range(10):
                        b = _select_bundle(mc, request.recitation_pref)
                        if b and _can_insert(b, selected, meetings, codes, us1, us2, request)[0]:
                            _add(b)
                            break
    grad_codes = set(codes)  # 冻结必修课号：must_include + 毕业 + 分布
    p = request.major_focus
    total_pool = list(major_pool) + list(interest_pool) + list(other_pool)
    grad_credits = sum(b.units for b in selected)
    min_big_courses = 3 if _is_freshman_fall(request) else max(2, (20 - grad_credits) // 4 - 1)

    attempts = 0
    while attempts < 100:
        attempts += 1
        r = random.random() * 100
        if r < p and major_pool:
            pool = major_pool
        elif r < p + (100 - p) * 0.8 and interest_pool:
            pool = interest_pool
        elif other_pool:
            pool = other_pool
        else:
            pool = total_pool

        mc = _draw_main_course(pool, attr_prefs, codes)
        if not mc:
            continue
        b = _select_bundle(mc, request.recitation_pref)
        if not b:
            continue
        ok, reason = _can_insert(b, selected, meetings, codes, units_s1, units_s2, request)
        if ok:
            _add(b)
            attempts = 0  # 重置，继续尝试
        # 塞不进去就继续试

    # 大课不够时继续试（放宽attempts限制）
    big_count = sum(1 for b in selected if b.units >= 3.5) - sum(1 for b in selected if b.course_codes.issubset(grad_codes))
    big_attempts = 0
    while big_count < min_big_courses and total_pool and big_attempts < 500:
        big_attempts += 1
        mc = _draw_main_course(total_pool, attr_prefs, codes)
        if not mc:
            break
        b = _select_bundle(mc, request.recitation_pref)
        if b and _can_insert(b, selected, meetings, codes, units_s1, units_s2, request)[0]:
            _add(b)
            big_count += 1

    # ── 3. 塞小课 ──
    big_count = sum(1 for b in selected if b.units >= 3.5) - sum(1 for b in selected if b.course_codes.issubset(grad_codes))
    extra_credits = getattr(request, 'two_credit_pref_extra', 0) or 0
    current_total = sum(b.units for b in selected)
    # 大课达标后才自动补小课
    if big_count >= min_big_courses and current_total < request.min_units and small_pool:
        extra_credits = max(extra_credits, request.min_units - current_total)
    if extra_credits > 0 and small_pool:
        pool = [mc for mc in small_pool if request.allow_two_writing or "CURR-WRITING" not in mc.attrs]
        if pool:
            for _ in range(int(extra_credits)):
                mc = _draw_main_course(pool, attr_prefs, codes)
                if not mc:
                    break
                b = _select_bundle(mc, request.recitation_pref)
                if b:
                    ok, _ = _can_insert(b, selected, meetings, codes, units_s1, units_s2, request)
                    if ok:
                        _add(b)

    # ── 4. 塞PE ──
    pe_needed = request.pe_count
    if pe_needed > 0 and pe_pool:
        # 按 session 分类 PE pool
        pe_s1_list = [mc for mc in pe_pool if any(b.session_s1_units > 0 for b in mc.bundles)]
        pe_s2_list = [mc for mc in pe_pool if any(b.session_s2_units > 0 for b in mc.bundles)]
        pe_any_list = [mc for mc in pe_pool if mc not in pe_s1_list and mc not in pe_s2_list]
        # S1,S2 去重后合并 prio: S1 > S2 > 其他
        pe_ordered = pe_s1_list + pe_s2_list + pe_any_list

        def _try_add_pe():
            nonlocal pe_count
            if pe_count >= pe_needed:
                return
            for mc in pe_ordered:
                if mc.course_code in codes:
                    continue
                b = _select_bundle(mc, request.recitation_pref)
                if not b:
                    continue
                ok, _ = _can_insert(b, selected, meetings, codes, units_s1, units_s2, request)
                if ok:
                    _add(b)
                    return

        # pe_count=2: 优先 S1+S2 各一节，重试 3 次
        if pe_needed == 2:
            for _ in range(3):
                if pe_count >= 2:
                    break
                # 统计当前各 session PE 数
                s1_now = sum(1 for bx in selected for s in bx.sections
                             if s.is_pe and _session_class(s.session_code) == "session-s1")
                s2_now = sum(1 for bx in selected for s in bx.sections
                             if s.is_pe and _session_class(s.session_code) == "session-s2")
                # 缺哪边优先补哪边
                if s1_now == 0:
                    mc = _draw_main_course(pe_s1_list if pe_s1_list else pe_pool, {}, codes)
                elif s2_now == 0:
                    mc = _draw_main_course(pe_s2_list if pe_s2_list else pe_pool, {}, codes)
                else:
                    mc = _draw_main_course(pe_pool, {}, codes)
                if not mc:
                    continue
                b = _select_bundle(mc, request.recitation_pref)
                if b:
                    ok, _ = _can_insert(b, selected, meetings, codes, units_s1, units_s2, request)
                    if ok:
                        _add(b)

        # 剩余需要补齐的（pe_count=1 或 >2 或均衡后还不够）
        pe_attempts = 0
        while pe_count < pe_needed and pe_attempts < 50:
            pe_attempts += 1
            _try_add_pe()

    elif pe_needed > 0:
        return None, f"体育课池为空，无法满足 pe_count={pe_needed}"

    # ── 5. 塞Minitem ──
    if miniterm_pool and request.enforce_miniterm:
        mc = _draw_main_course(miniterm_pool, {}, codes)
        if mc:
            b = _select_bundle(mc, request.recitation_pref)
            if b:
                ok, _ = _can_insert(b, selected, meetings, codes, units_s1, units_s2, request)
                if ok:
                    _add(b)

    # ── Requisite 验证 ──
    while True:
        removed_any = False
        for b in list(selected):
            b_sess = b.session_sort_key
            earlier_codes = {cc for cc, sess in code_session.items() if sess < b_sess}

            # 第一遍：co/anti 用全量 codes（名额已在 pool 构建时验证，此处仅查 requisites）
            validate_bundle(b, request, codes | request.completed_courses, skip_availability=True)
            all_fails = set(b.blocked_reasons or [])
            # 第二遍：只收集 pre-req 失败（用 session filtered）
            # 注意：co-req 的失败只从第一遍（全量）取，第二遍不覆盖
            validate_bundle(b, request, earlier_codes | request.completed_courses, skip_availability=True)
            for r in (b.blocked_reasons or []):
                if "需要已修" in r:
                    all_fails.add(r)
            b.blocked_reasons = list(all_fails)
            b.warnings = []
            b.review_notes = []

            if b.blocked_reasons:
                if not b.course_codes.issubset(grad_codes):
                    selected.remove(b)
                    for cc in b.course_codes:
                        codes.discard(cc)
                    for m in b.meetings:
                        if m in meetings:
                            meetings.remove(m)
                    units_s1 -= b.session_s1_units
                    units_s2 -= b.session_s2_units
                    removed_any = True
                else:
                    return None, f"requisite失败: {b.primary_code} " + ";".join(b.blocked_reasons[:2])
        if not removed_any:
            break

    # ── 最终验证 ──
    # pe_count 不足时毙表（检查实际的 PE 数量，因为 requisites 可能移除 PE）
    actual_pe = sum(1 for b in selected if b.sections[0].is_pe)
    if actual_pe < request.pe_count:
        return None, f"体育课仅报入 {actual_pe}/{request.pe_count} 节（无可排时间或名额已满）"
    total_units = sum(b.units for b in selected)
    non_pe_units = sum(b.units for b in selected if not b.sections[0].is_pe)
    non_pe_s1 = sum(b.session_s1_units for b in selected if not b.sections[0].is_pe)
    non_pe_s2 = sum(b.session_s2_units for b in selected if not b.sections[0].is_pe)
    s1_u, s2_u = units_s1, units_s2
    if non_pe_units + 0.01 < request.min_units:
        return None, f"总学分不足({non_pe_units:.1f}<{request.min_units})"
    if non_pe_units > request.max_units + 0.01:
        return None, f"总学分超限({non_pe_units:.1f}>{request.max_units})"
    if non_pe_s1 + 0.01 < request.session_min_units:
        return None, f"S1学分不足({non_pe_s1:.1f}<{request.session_min_units})"
    if non_pe_s2 + 0.01 < request.session_min_units:
        return None, f"S2学分不足({non_pe_s2:.1f}<{request.session_min_units})"

    # 最终 requisite 检查（名额已在 pool 构建时验证）
    for b in selected:
        validate_bundle(b, request, codes, skip_availability=True)
        if b.blocked_reasons:
            return None, f"requisite失败: {b.primary_code} " + ";".join(b.blocked_reasons[:3])

    timeline = build_timeline(selected, request.blocked_meetings)
    all_ms = list(itertools.chain.from_iterable(b.meetings for b in selected))
    active_days = len(set(m.day for m in all_ms if not m.is_blocked))

    gap_total = 0
    by_d: Dict[str, List[Meeting]] = {}
    for m in all_ms:
        if m.is_blocked:
            continue
        by_d.setdefault(m.day, []).append(m)
    for d_ms in by_d.values():
        orded = sorted(d_ms, key=lambda x: x.start_minute)
        busy = sum(m.end_minute - m.start_minute for m in orded)
        span = max(m.end_minute for m in orded) - min(m.start_minute for m in orded)
        gap_total += max(span - busy, 0)

    all_w = list(dict.fromkeys(itertools.chain.from_iterable(b.warnings for b in selected)))
    all_r = list(dict.fromkeys(itertools.chain.from_iterable(b.review_notes for b in selected)))

    return ScheduleResult(
        selected=selected, total_score=0.0, total_units=total_units,
        warnings=all_w, review_notes=all_r, timeline=timeline,
        active_days=active_days, total_gap_minutes=gap_total,
    ), ""


def _score_schedule(sr: ScheduleResult, request: ScheduleRequest, attr_prefs) -> float:
    """为整个课表打分。"""
    score = 0.0
    p = request.major_focus
    selected = sr.selected
    meetings = list(itertools.chain.from_iterable(b.meetings for b in selected))

    # 专业课加分
    major_codes = {normalize_course_code(c) for c in request.major_courses}
    for group in request.major_n_in_m:
        for c in group.get("codes", []):
            major_codes.add(normalize_course_code(c))
    interest_codes = {normalize_course_code(c.name) for c in request.target_courses}

    for b in selected:
        cc = b.course_codes
        if cc & major_codes:
            score += p * 2.0
        elif cc & interest_codes:
            score += (100 - p) * 2.0
        else:
            score -= 20

    # 风险规避
    if request.availability_mode in ("open_only", "allow_waitlist"):
        for b in selected:
            score += (compute_availability_ratio(b, request) - 0.5) * request.risk_aversion * 2.0

    # 避开早八
    for m in meetings:
        if m.start_minute < 9 * 60:
            score -= request.avoid_early * 0.8

    # 避开晚课
    for m in meetings:
        if m.start_minute >= 18 * 60 + 45:
            score -= request.avoid_evening * 0.7

    # 教授偏好
    for pname, pref in request.prefer_professors:
        for b in selected:
            for s in b.sections:
                if pname.lower() in (s.instructor_names or "").lower():
                    score += pref * 2.0
                    break

    # 松散度
    gap_score = _compute_gap_score(selected, request.blocked_meetings, request)
    score += gap_score

    # 天数分布
    day_score = _compute_day_distribution_score(selected, request)
    score += day_score

    # PE紧接
    score -= _compute_pe_gap_score(selected, request)

    # 总上课时间
    total_min = sum(m.end_minute - m.start_minute for m in meetings if not m.is_blocked)
    if request.total_time > 0:
        score -= total_min / 60.0 * (request.total_time / 100.0) * 5.0

    sr.total_score = score
    return score


# ── 主入口 ──
def build_schedule(rows: Sequence[Any], request: ScheduleRequest) -> Dict[str, Any]:
    """随机抽样排课主入口。"""
    random.seed(request.random_seed if request.random_seed else None)

    by_nbr, by_code = build_section_indexes(rows)
    warnings: List[str] = []

    if not request.class_of and request.year_level:
        request.class_of = compute_class_of(request.year_level, request.term_filter)

    # 构建主课和池子
    mc_map = _build_main_courses(by_nbr, by_code, request)
    if not mc_map:
        return {
            "schedules": [], "target_summaries": [],
            "blocked_reasons": ["当前数据库中没有可用课程。"],
            "warnings": warnings,
            "graduation_warnings": check_graduation_progress([], request),
            "searched_states": 0, "generated_count": 0,
        }
    major_pool, interest_pool, other_pool, small_pool, pe_pool, miniterm_pool, mc_map = \
        _build_pools(mc_map, request)

    # 属性偏好
    attr_prefs = request.prefer_attrs

    summaries = [
        {"target": "专业课池", "priority": request.major_focus, "candidate_count": len(major_pool), "required": request.major_focus >= 100, "blocked": False},
        {"target": "兴趣课池", "priority": 100 - request.major_focus, "candidate_count": len(interest_pool), "required": False, "blocked": False},
        {"target": "其他4分课池", "priority": 0, "candidate_count": len(other_pool), "required": False, "blocked": False},
        {"target": "两分课池", "priority": request.two_credit_pref, "candidate_count": len(small_pool), "required": False, "blocked": False},
        {"target": "体育课池", "priority": getattr(request, 'pe_count', 0), "candidate_count": len(pe_pool), "required": False, "blocked": False},
        {"target": "Minitem池", "priority": 100 if request.enforce_miniterm else 0, "candidate_count": len(miniterm_pool), "required": False, "blocked": False},
    ]

    # 生成课表
    schedules: List[ScheduleResult] = []
    seen_schedule_keys: Set[Tuple[str, ...]] = set()
    total_courses = sum(len(p) for p in [major_pool, interest_pool, other_pool, small_pool, pe_pool, miniterm_pool])
    max_attempts = min(request.max_attempts, total_courses * 100) if total_courses > 0 else 50
    target_count = min(request.max_results * request.target_multiplier, 500)
    attempts = 0
    fail_reasons: Dict[str, int] = {}
    for _ in range(max_attempts):
        attempts += 1
        sr, reason = _try_build_one(major_pool, interest_pool, other_pool, small_pool, pe_pool,
                                     miniterm_pool, mc_map, by_nbr, request, attr_prefs)
        if sr:
            key = tuple(sorted(nbr for b in sr.selected for nbr in b.class_nbrs))
            if key in seen_schedule_keys:
                continue
            seen_schedule_keys.add(key)
            _score_schedule(sr, request, attr_prefs)
            schedules.append(sr)
        else:
            fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
        if len(schedules) >= target_count:
            break

    schedules.sort(key=lambda s: s.total_score, reverse=True)
    final = schedules[:request.max_results]

    grad_w = check_graduation_progress(
        final[0].selected if final else [], request)

    grad_w_initial = check_graduation_progress([], request)

    if not final:
        warnings.append(f"未能生成课表（{attempts} 尝试，{len(schedules)} 成功）。")
        warnings.append(f"  must_include={len(request.must_include)}门 mc_map={len(mc_map)}门")
        top_fails = sorted(fail_reasons.items(), key=lambda x: -x[1])[:5]
        for reason, count in top_fails:
            warnings.append(f"  [{count}次] {reason}")
        warnings.append(f"池子: 专业{len(major_pool)} 兴趣{len(interest_pool)} 其他{len(other_pool)} 小课{len(small_pool)} PE{len(pe_pool)} Miniterm{len(miniterm_pool)}")

    return {
        "schedules": final,
        "target_summaries": summaries,
        "blocked_reasons": [],
        "warnings": warnings,
        "graduation_warnings": grad_w if final else grad_w_initial,
        "searched_states": len(schedules),
        "generated_count": len(schedules),
    }


