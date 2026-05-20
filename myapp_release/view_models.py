import json
import re
from typing import List, Optional, Tuple

from utils import (
    calc_duration,
    first_mapping,
    first_text,
    fmt_enrl_status,
    format_time,
    get_instructor_emails,
    join_instructors,
    load_json_value,
    parse_meeting_location as parse_location_from_meeting,
    parse_requisites,
    row_get,
    to_bool as as_bool,
    to_int as as_int,
)


def parse_detail_raw(raw_json: str) -> dict:
    data = load_json_value(raw_json, {})
    return data if isinstance(data, dict) else {}


def parse_search_raw(raw_json: str) -> dict:
    data = load_json_value(raw_json, {})
    return data if isinstance(data, dict) else {}


def html_to_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def search_meeting_parts(search_raw: dict, row) -> Tuple[str, str, str]:
    first_meeting = first_mapping(search_raw.get("meetings", []))
    days = first_text(first_meeting.get("days"), row_get(row, "meeting_days"))
    start_time = format_time(first_text(first_meeting.get("start_time"), row_get(row, "meeting_start_time")))
    end_time = format_time(first_text(first_meeting.get("end_time"), row_get(row, "meeting_end_time")))
    return days, start_time, end_time


def search_location_parts(search_raw: dict, row) -> Tuple[str, str]:
    first_meeting = first_mapping(search_raw.get("meetings", []))
    search_building, search_classroom = parse_location_from_meeting(first_meeting)
    return (
        first_text(search_building, row_get(row, "building")),
        first_text(search_classroom, row_get(row, "classroom")),
    )


def search_instructor_names(search_raw: dict, row) -> str:
    first_meeting = first_mapping(search_raw.get("meetings", []))
    return first_text(
        join_instructors(search_raw.get("instructors", []) or []),
        first_meeting.get("instructor"),
        row_get(row, "instructor_names"),
    )


def build_course_list_view_model(row) -> dict:
    base = {key: row[key] for key in row.keys()} if hasattr(row, "keys") else dict(row)
    search_raw = parse_search_raw(row_get(row, "search_raw_json"))
    meeting_days, meeting_start_time, meeting_end_time = search_meeting_parts(search_raw, row)
    building, classroom = search_location_parts(search_raw, row)
    wait_cap = as_int(first_text(search_raw.get("wait_cap"), row_get(row, "wait_list_capacity")), 0)
    wait_tot = as_int(search_raw.get("wait_tot"), None)
    wait_available = (
        max(wait_cap - wait_tot, 0)
        if wait_tot is not None
        else as_int(row_get(row, "wait_list_available"), 0)
    )

    subject_code = first_text(search_raw.get("subject"), row_get(row, "subject")).upper().strip()
    catalog_code = first_text(search_raw.get("catalog_nbr"), row_get(row, "catalog_nbr")).strip()
    base.update({
        "term": first_text(search_raw.get("strm"), row_get(row, "term")),
        "subject": subject_code,
        "catalog_nbr": catalog_code,
        "class_section": first_text(search_raw.get("class_section"), row_get(row, "class_section")),
        "course_code": f"{subject_code} {catalog_code}" if subject_code and catalog_code else "",
        "course_name": first_text(search_raw.get("descr"), row_get(row, "course_name")),
        "meeting_duration": calc_duration(meeting_start_time, meeting_end_time) or row_get(row, "meeting_duration"),
        "units": first_text(search_raw.get("units"), row_get(row, "units")),
        "grading_basis": first_text(search_raw.get("grading_basis"), row_get(row, "grading_basis")),
        "component": first_text(search_raw.get("component"), row_get(row, "component")),
        "class_nbr": first_text(search_raw.get("class_nbr"), row_get(row, "class_nbr")),
        "session_code": first_text(search_raw.get("session_code"), row_get(row, "session_code")),
        "start_date": first_text(search_raw.get("start_dt"), row_get(row, "start_date")),
        "end_date": first_text(search_raw.get("end_dt"), row_get(row, "end_date")),
        "meeting_days": meeting_days,
        "meeting_start_time": meeting_start_time,
        "meeting_end_time": meeting_end_time,
        "building": building,
        "classroom": classroom,
        "instructor_names": search_instructor_names(search_raw, row),
        "enrl_status": first_text(
            fmt_enrl_status(
                first_text(search_raw.get("enrl_stat")),
                first_text(search_raw.get("enrl_stat_descr")),
            ),
            row_get(row, "enrl_status"),
        ),
        "class_capacity": as_int(first_text(search_raw.get("class_capacity"), row_get(row, "class_capacity")), 0),
        "enrollment_available": as_int(
            first_text(search_raw.get("enrollment_available"), row_get(row, "enrollment_available")),
            0,
        ),
        "wait_list_capacity": wait_cap,
        "wait_list_available": wait_available,
        "is_combined": "Y" if first_text(search_raw.get("combined_section"), row_get(row, "is_combined")).upper() == "Y" else "N",
        "course_attributes": first_text(search_raw.get("crse_attr"), row_get(row, "course_attributes")),
        "course_attribute_values": first_text(search_raw.get("crse_attr_value"), row_get(row, "course_attribute_values")),
        "requirement_designation": first_text(search_raw.get("rqmnt_designtn"), row_get(row, "requirement_designation")),
    })
    return base


def attr_value_label(value_code: str, course_attribute_options: Optional[List[dict]]) -> str:
    value_code = first_text(value_code)
    if not value_code:
        return ""
    attr_code, _, short_value = value_code.partition("-")
    if not short_value:
        short_value = attr_code
        attr_code = ""
    for option in course_attribute_options or []:
        if attr_code and option.get("crse_attr") != attr_code:
            continue
        attr_descr = first_text(option.get("descr"), option.get("crse_attr"))
        for value in option.get("values", []) or []:
            raw_value = first_text(value.get("crse_attr_value"))
            if raw_value in {short_value, value_code}:
                value_descr = first_text(value.get("descr"), raw_value)
                return f"{attr_descr}: {value_descr}" if attr_descr else value_descr
    return ""


def build_class_attribute_list(search_raw: dict, row, detail_text: str, course_attribute_options: Optional[List[dict]]) -> List[str]:
    raw_values = first_text(search_raw.get("crse_attr_value"), row_get(row, "course_attribute_values"))
    values = [item.strip() for item in raw_values.split(",") if item.strip()]
    labels = [attr_value_label(value, course_attribute_options) for value in values]
    labels = [label for label in labels if label]
    detail_labels = [
        item.strip()
        for item in re.split(r"[\r\n,]+", str(detail_text or ""))
        if item.strip()
    ]
    result = list(dict.fromkeys(labels))
    for detail_label in detail_labels:
        if any(detail_label == label or detail_label in label for label in result):
            continue
        result.append(detail_label)
    if result:
        return result
    return values


def build_course_detail_view_model(row, detail_raw: dict, course_attribute_options: Optional[List[dict]] = None) -> dict:
    search_raw = parse_search_raw(row["search_raw_json"])
    section = detail_raw.get("section_info", {}) if isinstance(detail_raw, dict) else {}
    class_details = section.get("class_details", {}) or {}
    meetings = section.get("meetings", []) or []
    enrollment_info = section.get("enrollment_information", {}) or {}
    class_avail = section.get("class_availability", {}) or {}
    catalog_descr = section.get("catalog_descr", {}) or {}
    class_enroll = detail_raw.get("class_enroll_info", {}) or {}

    search_days, search_start, search_end = search_meeting_parts(search_raw, row)
    first_detail_meeting = first_mapping(meetings)
    detail_building, detail_classroom = parse_location_from_meeting(first_detail_meeting)
    search_building, search_classroom = search_location_parts(search_raw, row)
    detail_meeting_text = first_text(first_detail_meeting.get("meets"))
    if not detail_meeting_text and first_detail_meeting:
        detail_days = first_text(first_detail_meeting.get("days"))
        detail_start = first_text(first_detail_meeting.get("meeting_time_start"))
        detail_end = first_text(first_detail_meeting.get("meeting_time_end"))
        detail_meeting_text = first_text(f"{detail_days} {detail_start}-{detail_end}".strip(" -"))
    search_meeting_text = first_text(f"{search_days} {search_start}-{search_end}".strip(" -"))

    detail_instructors = []
    for meeting in meetings:
        detail_instructors.extend(meeting.get("instructors", []) or [])
    search_instructors = search_raw.get("instructors", []) or []

    search_status = fmt_enrl_status(
        first_text(search_raw.get("enrl_stat")),
        first_text(search_raw.get("enrl_stat_descr")),
    )

    subject = first_text(class_details.get("subject"), search_raw.get("subject"), row_get(row, "subject"))
    catalog_nbr = first_text(class_details.get("catalog_nbr"), search_raw.get("catalog_nbr"), row_get(row, "catalog_nbr"))
    course_title = first_text(class_details.get("course_title"), search_raw.get("descr"), row_get(row, "course_name"))
    instruction_mode = first_text(
        class_details.get("instruction_mode"),
        search_raw.get("instruction_mode_descr"),
        search_raw.get("instruction_mode"),
    )
    component = first_text(class_details.get("component"), search_raw.get("component"), row_get(row, "component"))
    grading_basis = first_text(class_details.get("grading_basis"), search_raw.get("grading_basis"), row_get(row, "grading_basis"))
    campus = first_text(class_details.get("campus"), search_raw.get("campus_descr"), search_raw.get("campus"))
    location = first_text(class_details.get("location"), search_raw.get("location_descr"), search_raw.get("location"))
    status = first_text(class_details.get("status"), search_status, row_get(row, "enrl_status"))
    session = first_text(
        class_details.get("session"),
        search_raw.get("session_descr"),
        search_raw.get("session_code"),
        row_get(row, "session_code"),
    )
    units = first_text(class_details.get("units"), search_raw.get("units"), row_get(row, "units"))
    class_components = html_to_text(class_details.get("class_components", ""))
    catalog_description = catalog_descr.get("crse_catalog_description", "") or ""
    notes = section.get("notes", {}) or {}
    class_notes = notes.get("class_notes", "") or ""

    class_capacity = as_int(
        first_text(class_avail.get("class_capacity"), search_raw.get("class_capacity"), row_get(row, "class_capacity")),
        0,
    )
    enrollment_available = as_int(
        first_text(
            class_avail.get("enrollment_available"),
            search_raw.get("enrollment_available"),
            row_get(row, "enrollment_available"),
        ),
        0,
    )
    enrollment_total = as_int(first_text(class_avail.get("enrollment_total"), search_raw.get("enrollment_total")), None)
    if enrollment_total is None:
        enrollment_total = max(class_capacity - enrollment_available, 0)
    wait_cap = as_int(
        first_text(class_avail.get("wait_list_capacity"), search_raw.get("wait_cap"), row_get(row, "wait_list_capacity")),
        0,
    )
    wait_tot = as_int(first_text(class_avail.get("wait_list_total"), search_raw.get("wait_tot")), None)
    if wait_tot is None:
        wait_tot = max(wait_cap - as_int(row_get(row, "wait_list_available"), 0), 0)
    wait_available = max(wait_cap - wait_tot, 0)

    enroll_requirements = first_text(enrollment_info.get("enroll_requirements"), row_get(row, "enroll_requirements"))
    detail_requirement_designation = first_text(enrollment_info.get("requirement_desig"))
    search_requirement_designation = first_text(search_raw.get("rqmnt_designtn"), row_get(row, "requirement_designation"))
    requirement_designation = first_text(detail_requirement_designation, search_requirement_designation)
    requirement_designation_descr = first_text(detail_requirement_designation, requirement_designation)
    is_duke_instructor = (
        requirement_designation.upper() == "DINS"
        or "duke instructor" in requirement_designation.lower()
        or "duke instructor" in requirement_designation_descr.lower()
    )
    class_attributes = first_text(
        enrollment_info.get("class_attributes"),
        search_raw.get("crse_attr_value"),
        search_raw.get("crse_attr"),
        row_get(row, "course_attributes"),
    )
    class_attribute_list = build_class_attribute_list(
        search_raw,
        row,
        enrollment_info.get("class_attributes", ""),
        course_attribute_options,
    )

    prereq_list, antireq_list = parse_requisites(enroll_requirements)
    if not prereq_list:
        prereq_list = [item.strip() for item in str(row_get(row, "prereqs") or "").split(",") if item.strip()]
    if not antireq_list:
        antireq_list = [item.strip() for item in str(row_get(row, "antireqs") or "").split(",") if item.strip()]

    combined = section.get("combined_sections", []) if isinstance(section, dict) else []
    reserve_caps = []
    for reserve in section.get("reserve_caps", []) or search_raw.get("reserve_caps", []) or []:
        if not isinstance(reserve, dict):
            continue
        reserve_capacity = as_int(reserve.get("enrl_cap"), 0)
        reserve_total = as_int(reserve.get("enrl_tot"), 0)
        reserve_caps.append({
            "description": reserve.get("descr", ""),
            "start_date": reserve.get("start_dt", ""),
            "capacity": reserve_capacity,
            "enrollment_total": reserve_total,
            "available": reserve_capacity - reserve_total,
        })

    instructors_for_email = detail_instructors or search_instructors

    valid_to_enroll = first_text(section.get("valid_to_enroll"))
    has_show_enroll = isinstance(detail_raw, dict) and "show_enroll" in detail_raw

    related_sections_list = []
    raw_related = load_json_value(row_get(row, "related_sections_json"), [])
    if isinstance(raw_related, list):
        raw_related = {"related_sections": raw_related, "enrollment_sections": []}
    elif not isinstance(raw_related, dict):
        raw_related = {"related_sections": [], "enrollment_sections": []}
    # Process related_sections (parent relationships shown on child courses)
    for group in (raw_related.get("related_sections") or []):
        if not isinstance(group, dict):
            continue
        sections = []
        for sec in group.get("Sections", []) or []:
            if not isinstance(sec, dict):
                continue
            nbr = as_int(sec.get("ClassNbr"), 0)
            if nbr <= 0:
                continue
            meetings = sec.get("MeetingPatterns", []) or []
            sections.append({
                "class_nbr": nbr,
                "section": first_text(sec.get("Section")),
                "desc": first_text(sec.get("Descr"), sec.get("Description")),
                "meeting": first_text(meetings[0] if meetings else ""),
                "status": first_text(sec.get("Status"), sec.get("ClassEnrlStatus")),
                "enrolled": as_int(sec.get("ClassTotal"), as_int(sec.get("EnrlTot"), 0)),
                "wait": as_int(sec.get("WaitTot"), 0),
                "capacity": as_int(sec.get("ClassCapacity"), as_int(sec.get("EnrlCap"), 0)),
            })
        related_sections_list.append({
            "component": first_text(group.get("Component")),
            "descr": first_text(group.get("Descr"), group.get("Component")),
            "is_required": as_bool(group.get("IsRequired")),
            "sections": sections,
        })
    # Process enrollment_sections (child courses shown on parent courses)
    enrollment_by_comp = {}
    for sec in (raw_related.get("enrollment_sections") or []):
        if not isinstance(sec, dict):
            continue
        nbr = as_int(sec.get("class_nbr"), as_int(sec.get("ClassNbr"), 0))
        if nbr <= 0:
            continue
        comp = first_text(sec.get("component"), sec.get("Component"), "OTH")
        enrollment_by_comp.setdefault(comp, []).append({"class_nbr": nbr})
    for comp in sorted(enrollment_by_comp):
        related_sections_list.append({
            "component": comp,
            "descr": comp,
            "is_required": True,
            "sections": enrollment_by_comp[comp],
        })

    return {
        "course_code": f"{subject} {catalog_nbr}",
        "course_title": course_title,
        "term": first_text(search_raw.get("strm"), row_get(row, "term")),
        "component": component,
        "class_section": first_text(class_details.get("class_section"), search_raw.get("class_section"), row_get(row, "class_section")),
        "class_nbr": first_text(class_details.get("class_number"), search_raw.get("class_nbr"), row_get(row, "class_nbr")),
        "search_synced_at": row_get(row, "search_synced_at") or "",
        "detail_synced_at": row_get(row, "detail_synced_at") or "",
        "instructor_names": first_text(join_instructors(detail_instructors), search_instructor_names(search_raw, row)),
        "instructor_emails": get_instructor_emails(instructors_for_email),
        "status": status,
        "instruction_mode": instruction_mode,
        "grading_basis": grading_basis,
        "campus": campus,
        "location": location,
        "session": session,
        "units": units,
        "class_components": class_components,
        "meeting_text": first_text(detail_meeting_text, search_meeting_text),
        "building": first_text(detail_building, search_building),
        "classroom": first_text(detail_classroom, search_classroom),
        "class_capacity": class_capacity,
        "enrollment_total": enrollment_total,
        "enrollment_available": enrollment_available,
        "wait_list_capacity": wait_cap,
        "wait_list_total": wait_tot,
        "wait_list_available": wait_available,
        "valid_to_enroll": valid_to_enroll,
        "show_enroll": as_bool(detail_raw.get("show_enroll", False)),
        "enroll_permission_missing": not valid_to_enroll or not has_show_enroll,
        "is_in_cart": as_bool(class_enroll.get("is_in_cart", search_raw.get("isInCart", False))),
        "is_enrolled": as_bool(class_enroll.get("is_enrolled", search_raw.get("isEnrolled", False))),
        "is_waitlisted": as_bool(class_enroll.get("is_waitlisted", search_raw.get("isWaitlisted", False))),
        "enroll_requirements": enroll_requirements,
        "requirement_designation": requirement_designation,
        "requirement_designation_descr": requirement_designation_descr,
        "is_duke_instructor": is_duke_instructor,
        "prerequisites": prereq_list,
        "antirequisites": antireq_list,
        "class_attributes": class_attributes,
        "class_attribute_list": class_attribute_list,
        "catalog_description": catalog_description,
        "class_notes": class_notes,
        "combined_sections": combined,
        "reserve_caps": reserve_caps,
        "related_sections": related_sections_list,
        "requisite_parsed_json": row_get(row, "requisite_parsed_json") or "",
        "reserved_seats_parsed_json": row_get(row, "reserved_seats_parsed_json") or "",
        "raw_json": json.dumps(detail_raw, ensure_ascii=False, indent=2),
        "search_raw_json": row["search_raw_json"] or "",
        "enroll_detail_raw_json": row_get(row, "enroll_detail_raw_json") or "",
    }
