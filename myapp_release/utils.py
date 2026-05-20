import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from config import COOKIE_PATH


def load_cookie() -> str:
    if not os.path.exists(COOKIE_PATH):
        return ""
    with open(COOKIE_PATH, encoding="utf-8") as f:
        return f.read().strip()


def save_cookie(cookie: str) -> None:
    cookie = cookie.strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    with open(COOKIE_PATH, "w", encoding="utf-8") as f:
        f.write(cookie)


def has_cookie() -> bool:
    return bool(load_cookie())


def load_json_value(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default
    return parsed if parsed is not None else default


def row_get(row: Any, key: str, default: Any = "") -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def to_int(value: Any, default: Any = 0) -> Any:
    try:
        text = str(value).strip()
        if not text:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def first_mapping(items: Any) -> Dict:
    return items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}


def parse_meeting_location(meeting: Dict) -> Tuple[str, str]:
    if not isinstance(meeting, dict):
        return "", ""
    classroom = first_text(
        meeting.get("facility_descr"),
        meeting.get("room"),
        meeting.get("facility_id"),
    )
    building = first_text(meeting.get("bldg_descr"))
    if not building and classroom:
        building = classroom.split()[0].strip()
    if not building:
        building = first_text(meeting.get("bldg_cd"))
    return building, classroom


def join_instructors(inst_list: List[Dict]) -> str:
    names = []
    for inst in (inst_list or []):
        name = str(inst.get("name", "")).strip()
        if name:
            names.append(name)
    return ", ".join(names)


def parse_requisites(text: str) -> Tuple[List[str], List[str]]:
    if not text:
        return [], []
    prereq = []
    antireq = []
    pre_match = re.search(r"Prerequisite:(.*?)(?:Anti-requisite:|$)", text, re.IGNORECASE)
    if pre_match:
        prereq = re.findall(r"[A-Z]+\s?\d+", pre_match.group(1))
    anti_match = re.search(r"Anti-requisite:(.*)$", text, re.IGNORECASE)
    if anti_match:
        antireq = re.findall(r"[A-Z]+\s?\d+", anti_match.group(1))
    prereq = [x.replace(" ", "") for x in prereq]
    antireq = [x.replace(" ", "") for x in antireq]
    return prereq, antireq


def fmt_enrl_status(code: str, descr: str) -> str:
    return descr if descr else {"O": "Open", "C": "Closed", "W": "Wait List"}.get(code, code)


def format_time(raw_time: str) -> str:
    if not raw_time:
        return ""
    if '.' in raw_time:
        parts = raw_time.split('.')
        if len(parts) >= 2:
            return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}"
    elif ':' in raw_time:
        parts = raw_time.split(':')
        if len(parts) >= 2:
            return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}"
    return raw_time[:5] if len(raw_time) >= 5 else raw_time


def get_instructor_emails(inst_list: List[Dict]) -> str:
    emails = []
    for inst in (inst_list or []):
        email = str(inst.get("email", "")).strip()
        if email:
            emails.append(email)
    return ", ".join(emails)


def calc_duration(start_str: str, end_str: str) -> Optional[int]:
    try:
        s = format_time(start_str)
        e = format_time(end_str)
        if not s or not e:
            return None
        sh, sm = map(int, s.split(':'))
        eh, em = map(int, e.split(':'))
        return (eh * 60 + em) - (sh * 60 + sm)
    except (TypeError, ValueError):
        return None
