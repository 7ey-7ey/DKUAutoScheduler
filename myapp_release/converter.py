import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from utils import (
    join_instructors, parse_requisites, fmt_enrl_status,
    format_time, calc_duration, parse_meeting_location, to_int
)


def course_to_db_row(course: Dict, term: str, detail: Optional[Dict] = None,
                      related_sections_json: str = "",
                      enroll_detail_raw_json: str = "",
                      requisite_raw: str = "",
                      requisite_parsed_json: str = "",
                      reserved_seats_raw: str = "",
                      reserved_seats_parsed_json: str = "",
                      requisite_tags: str = "",
                      reserved_seats_tags: str = "") -> Dict[str, Any]:
    subject = str(course.get("subject", "")).strip()
    catalog_nbr = str(course.get("catalog_nbr", "")).strip()
    class_section = str(course.get("class_section", "")).strip()
    class_nbr = str(course.get("class_nbr", "")).strip()
    course_key = f"{term}:{class_nbr}" if class_nbr else f"{term}:{subject}{catalog_nbr}-{class_section}"

    course_name = str(course.get("descr", "")).strip()

    meetings = course.get("meetings", []) or []
    first_meeting = meetings[0] if meetings else {}
    meeting_days = str(first_meeting.get("days", ""))
    start_time = str(first_meeting.get("start_time", ""))
    end_time = str(first_meeting.get("end_time", ""))
    building, classroom = parse_meeting_location(first_meeting)

    duration = calc_duration(start_time, end_time) or 0

    instructors = course.get("instructors", []) or []
    instructor_names = join_instructors(instructors)
    grading_basis = str(course.get("grading_basis", "")).strip()
    enrl_stat_code = str(course.get("enrl_stat", "O"))
    enrl_stat_descr = str(course.get("enrl_stat_descr", ""))
    enrl_status = fmt_enrl_status(enrl_stat_code, enrl_stat_descr)

    class_capacity = to_int(course.get("class_capacity"), 0)
    enrollment_available = to_int(course.get("enrollment_available"), 0)
    wait_cap = to_int(course.get("wait_cap"), 0)
    wait_tot = to_int(course.get("wait_tot"), 0)
    wait_list_available = max(wait_cap - wait_tot, 0)

    enroll_requirements = ""
    prereqs_str = ""
    antireqs_str = ""
    coreqs_str = ""
    combined_sections_json = ""
    detail_raw_json = ""
    detail_synced_at = ""

    if detail:
        section_info = detail.get("section_info", {}) or {}
        enroll_info = section_info.get("enrollment_information", {}) or {}
        enroll_requirements = str(enroll_info.get("enroll_requirements", "")).strip()
        prereqs, antireqs = parse_requisites(enroll_requirements)
        prereqs_str = ", ".join(prereqs)
        antireqs_str = ", ".join(antireqs)

        combined_sections = section_info.get("combined_sections", [])
        if combined_sections:
            combined_sections_json = json.dumps(combined_sections, ensure_ascii=False)

        detail_synced_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        detail_raw_json = json.dumps(detail, ensure_ascii=False)

    return {
        "course_key": course_key,
        "term": term,
        "subject": subject,
        "subject_descr": str(course.get("subject_descr", "")).strip(),
        "catalog_nbr": catalog_nbr,
        "class_section": class_section,
        "course_name": course_name,
        "meeting_duration": duration,
        "units": str(course.get("units", "")).strip(),
        "grading_basis": grading_basis,
        "component": str(course.get("component", "")).strip(),
        "class_nbr": class_nbr,
        "session_code": str(course.get("session_code", "")).strip(),
        "start_date": str(course.get("start_dt", "")).strip(),
        "end_date": str(course.get("end_dt", "")).strip(),
        "meeting_days": meeting_days,
        "meeting_start_time": format_time(start_time),
        "meeting_end_time": format_time(end_time),
        "building": building,
        "classroom": classroom,
        "instructor_names": instructor_names,
        "enrl_status": enrl_status,
        "class_capacity": class_capacity,
        "enrollment_available": enrollment_available,
        "wait_list_capacity": wait_cap,
        "wait_list_available": wait_list_available,
        "is_combined": "Y" if course.get("combined_section", "").upper() == "Y" else "N",
        "course_attributes": str(course.get("crse_attr", "")).strip(),
        "course_attribute_values": str(course.get("crse_attr_value", "")).strip(),
        "requirement_designation": str(course.get("rqmnt_designtn", "")).strip(),
        "enroll_requirements": enroll_requirements,
        "prereqs": prereqs_str,
        "antireqs": antireqs_str,
        "coreqs": coreqs_str,
        "combined_sections_json": combined_sections_json,
        "related_sections_json": related_sections_json,
        "enroll_detail_raw_json": enroll_detail_raw_json,
        "requisite_raw": requisite_raw,
        "requisite_parsed_json": requisite_parsed_json,
        "reserved_seats_raw": reserved_seats_raw,
        "reserved_seats_parsed_json": reserved_seats_parsed_json,
        "requisite_tags": requisite_tags,
        "reserved_seats_tags": reserved_seats_tags,
        "search_raw_json": json.dumps(course, ensure_ascii=False),
        "detail_raw_json": detail_raw_json,
        "search_synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "detail_synced_at": detail_synced_at,
    }
