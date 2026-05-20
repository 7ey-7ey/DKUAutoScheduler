import time
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from dku_client import DKUApiClient
from database import CourseDB
from converter import course_to_db_row
from requisite_parser import parse_requisite_text, extract_requirements_from_detail_json, classify_requisite
from reserved_seat_parser import extract_reserve_caps, parse_reserve_caps, classify_reserved_seats
from utils import load_json_value, to_int


DETAIL_FETCH_DELAY_SECONDS = 0.2


@dataclass
class EnrollmentArtifacts:
    related_sections_json: str = ""
    enroll_detail_raw_json: str = ""


@dataclass
class RuleArtifacts:
    requisite_raw: str = ""
    requisite_parsed_json: str = ""
    requisite_tags: str = ""
    reserved_seats_raw: str = ""
    reserved_seats_parsed_json: str = ""
    reserved_seats_tags: str = ""


def json_dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def compact_enrollment_sections(sections) -> list:
    result = []
    for section in sections or []:
        if not isinstance(section, dict):
            continue
        class_nbr = to_int(section.get("ClassNbr") or section.get("class_nbr"), 0)
        if class_nbr <= 0:
            continue
        result.append({
            "class_nbr": class_nbr,
            "component": str(section.get("Component") or section.get("component", "")).strip(),
        })
    return result


def build_enrollment_artifacts(enroll_data) -> EnrollmentArtifacts:
    if not isinstance(enroll_data, dict):
        return EnrollmentArtifacts()

    related_sections = enroll_data.get("related_sections", []) or []
    enrollment_sections = compact_enrollment_sections(enroll_data.get("enrollment_sections", []))
    related_sections_json = ""
    if related_sections or enrollment_sections:
        related_sections_json = json_dumps({
            "related_sections": related_sections,
            "enrollment_sections": enrollment_sections,
        })

    return EnrollmentArtifacts(
        related_sections_json=related_sections_json,
        enroll_detail_raw_json=json_dumps(enroll_data),
    )


def fetch_enrollment_artifacts(client: DKUApiClient, class_nbr: str, term: str, career: str) -> EnrollmentArtifacts:
    try:
        enroll_data = client.fetch_enrollment_class_details(class_nbr, term, career)
    except Exception as exc:
        print(f"抓取关联课程失败 class_nbr={class_nbr}: {exc}")
        return EnrollmentArtifacts()
    return build_enrollment_artifacts(enroll_data)


def parse_requisite_artifacts(detail_json: str, class_nbr: str) -> RuleArtifacts:
    artifacts = RuleArtifacts()
    if not detail_json:
        return artifacts

    req_text = extract_requirements_from_detail_json(detail_json)
    if not req_text:
        return artifacts

    artifacts.requisite_raw = req_text
    try:
        parsed = parse_requisite_text(req_text)
    except Exception as exc:
        print(f"解析requisite失败 class_nbr={class_nbr}: {exc}")
        return artifacts

    artifacts.requisite_parsed_json = json_dumps(parsed)
    artifacts.requisite_tags = ",".join(classify_requisite(parsed))
    return artifacts


def parse_reserved_seat_artifacts(course_json: str, detail_json: str, class_nbr: str) -> RuleArtifacts:
    artifacts = RuleArtifacts()
    caps, _ = extract_reserve_caps(course_json, detail_json)
    if not caps:
        return artifacts

    artifacts.reserved_seats_raw = json_dumps(caps)
    try:
        parsed_caps = parse_reserve_caps(caps)
    except Exception as exc:
        print(f"解析reserved seats失败 class_nbr={class_nbr}: {exc}")
        return artifacts

    artifacts.reserved_seats_parsed_json = json_dumps(parsed_caps)
    artifacts.reserved_seats_tags = ",".join(classify_reserved_seats(parsed_caps))
    return artifacts


def parse_rule_artifacts(course, detail, class_nbr: str) -> RuleArtifacts:
    course_json = json_dumps(course)
    detail_json = json_dumps(detail) if detail else ""
    requisite = parse_requisite_artifacts(detail_json, class_nbr)
    reserved = parse_reserved_seat_artifacts(course_json, detail_json, class_nbr)
    return RuleArtifacts(
        requisite_raw=requisite.requisite_raw,
        requisite_parsed_json=requisite.requisite_parsed_json,
        requisite_tags=requisite.requisite_tags,
        reserved_seats_raw=reserved.reserved_seats_raw,
        reserved_seats_parsed_json=reserved.reserved_seats_parsed_json,
        reserved_seats_tags=reserved.reserved_seats_tags,
    )


def build_course_row(course, term: str, detail, enrollment: EnrollmentArtifacts, rules: RuleArtifacts):
    return course_to_db_row(
        course,
        term,
        detail,
        enrollment.related_sections_json,
        enrollment.enroll_detail_raw_json,
        rules.requisite_raw,
        rules.requisite_parsed_json,
        rules.reserved_seats_raw,
        rules.reserved_seats_parsed_json,
        rules.requisite_tags,
        rules.reserved_seats_tags,
    )


class CourseService:
    @staticmethod
    def iter_sync_from_dku_events(term: str, career: str, enrl_stat: str = "",
                                  fetch_details: bool = False, db_path: str = None,
                                  db_path_builder: Optional[Callable[[datetime], str]] = None,
                                  **kwargs):
        if not db_path and not db_path_builder:
            raise ValueError("同步课程需要明确的数据库路径")
        client = DKUApiClient()
        search_options = client.fetch_search_options(term=term, career=career)
        courses = client.fetch_courses(term=term, career=career, enrl_stat=enrl_stat, **kwargs)
        yield {"status": "list_completed", "total": len(courses)}
        rows = []
        detail_total = sum(1 for course in courses if str(course.get("class_nbr", "")).strip())
        detail_current = 0
        if fetch_details:
            yield {"status": "details_start", "current": 0, "total": detail_total}
        for course in courses:
            detail = None
            enrollment_artifacts = EnrollmentArtifacts()
            class_nbr = str(course.get("class_nbr", "")).strip()
            if fetch_details and class_nbr:
                detail_error = ""
                try:
                    detail = client.fetch_course_detail(class_nbr, term)
                except Exception as e:
                    detail_error = str(e)
                    print(f"抓取详情失败 class_nbr={class_nbr}: {e}")
                enrollment_artifacts = fetch_enrollment_artifacts(client, class_nbr, term, career)
                detail_current += 1
                yield {
                    "status": "detail_progress",
                    "current": detail_current,
                    "total": detail_total,
                    "class_nbr": class_nbr,
                    "error": detail_error,
                }
                time.sleep(DETAIL_FETCH_DELAY_SECONDS)
            rule_artifacts = parse_rule_artifacts(course, detail, class_nbr)
            rows.append(build_course_row(course, term, detail, enrollment_artifacts, rule_artifacts))
        synced_datetime = datetime.now(timezone.utc)
        synced_at = synced_datetime.isoformat(timespec="seconds")
        if db_path_builder:
            db_path = db_path_builder(synced_datetime)
        db = CourseDB(db_path)
        db.init()
        db.replace_courses(rows)
        db.save_search_options_metadata(term, career, search_options, len(rows), synced_at=synced_at)
        yield {"status": "completed", "count": len(rows), "synced_at": synced_at}

    @staticmethod
    def refresh_one_detail(class_nbr: str, term: str, career: str, db_path: str) -> bool:
        db = CourseDB(db_path)
        db.init()
        existing = db.get_course_by_class_nbr(class_nbr, term=term)
        raw = existing["search_raw_json"] if existing else ""
        if not raw:
            raise RuntimeError(f"数据库中找不到 class_nbr={class_nbr} 的课程，请先同步列表。")
        course = load_json_value(raw, {})
        if not isinstance(course, dict):
            raise RuntimeError(f"数据库中的 class_nbr={class_nbr} 课程原始数据无法解析，请重新同步列表。")
        try:
            client = DKUApiClient()
            detail = client.fetch_course_detail(class_nbr, term)
            enrollment_artifacts = fetch_enrollment_artifacts(client, class_nbr, term, career)
            rule_artifacts = parse_rule_artifacts(course, detail, class_nbr)
            row = build_course_row(course, term, detail, enrollment_artifacts, rule_artifacts)
            if existing:
                row["search_synced_at"] = existing["search_synced_at"] or row["search_synced_at"]
            db.upsert_courses([row])
            return True
        except Exception as e:
            print(f"刷新详情失败 class_nbr={class_nbr}, term={term}: {e}")
            return False
