import csv
import io
import json
import os
import re
from datetime import datetime, timezone

import requests
from flask import (Flask, Response, jsonify, redirect,
                   render_template, request, url_for, stream_with_context)

from config import DATABASE_DIR, DEFAULT_TERM, DEFAULT_CAREER
from database import CourseDB
from dku_client import DKUApiClient, DKUCookieError
from services import CourseService
from cache import OptionsCache
from bulletin import get_bulletin_data
from graduation_rules import (
    IDENTITIES,
    COURSE_LOAD_RULES,
    compute_class_of,
    get_graduation_summary,
    get_degree_summary_rules,
    get_language_courses_for_term,
)
from db_files import (
    build_course_db_filename,
    get_db_files,
    get_db_path,
    get_existing_db_path,
    remove_existing_db_files,
)
from utils import has_cookie, load_cookie, load_json_value, save_cookie, to_bool, to_int
from view_models import (
    build_course_detail_view_model,
    build_course_list_view_model,
    parse_detail_raw,
)
from scheduler import (
    ScheduleRequest,
    Course,
    build_schedule,
    clamp_int,
    parse_blocked_meetings,
)

def _enrich_related_sections(vm: dict, db, self_nbr: int = 0, term: str = "", career: str = "", db_path: str = "") -> None:
    if not vm.get("related_sections"):
        return

    groups = {}
    for group in vm["related_sections"]:
        comp = group["component"]
        g = groups.setdefault(comp, {"component": comp, "descr": group.get("descr", comp),
                                     "is_required": group.get("is_required", True), "sections": []})
        for sec in group.get("sections", []):
            g["sections"].append(sec)

    seen = set()
    for g in groups.values():
        for s in g["sections"]:
            if s.get("class_nbr"):
                seen.add(s["class_nbr"])

    # One level deeper: from each known class_nbr, pull new ones from its related_sections_json
    for nbr in list(seen):
        course = db.get_course_by_class_nbr(str(nbr))
        if not course:
            continue
        if not course["related_sections_json"] and has_cookie() and term and career and db_path:
            try:
                CourseService.refresh_one_detail(str(nbr), term, career, db_path)
                course = db.get_course_by_class_nbr(str(nbr)) or course
            except Exception:
                pass
        if not course["related_sections_json"]:
            continue
        raw_related = load_json_value(course["related_sections_json"], {})
        if isinstance(raw_related, list):
            rs_list = raw_related
        elif isinstance(raw_related, dict):
            rs_list = raw_related.get("related_sections", [])
        else:
            continue
        for rg in rs_list:
            if not isinstance(rg, dict):
                continue
            comp = str(rg.get("Component", "")).strip()
            for s in rg.get("Sections", []):
                if not isinstance(s, dict):
                    continue
                cn = to_int(s.get("ClassNbr"), 0)
                if cn <= 0 or cn in seen or cn == self_nbr:
                    continue
                seen.add(cn)
                g = groups.setdefault(comp, {"component": comp, "descr": str(rg.get("Descr", comp)),
                                             "is_required": to_bool(rg.get("IsRequired")), "sections": []})
                g["sections"].append({"class_nbr": cn})

    vm["related_sections"] = list(groups.values())


def create_app() -> Flask:
    os.makedirs(DATABASE_DIR, exist_ok=True)
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False

    def require_selected_db(db_file: str):
        db_path = get_existing_db_path(db_file)
        if db_path:
            return db_path
        return None

    def get_terms_and_careers():
        terms = OptionsCache.get_terms()
        careers = OptionsCache.get_careers()
        if not terms:
            terms = [{"strm": DEFAULT_TERM, "descr": "2026 Fall"}]
        if not careers:
            careers = [{"acad_career": DEFAULT_CAREER, "descr": "Undergraduate"}]
        return terms, careers

    def get_course_options(db, option_term: str, option_career: str):
        db_options = db.get_search_options_metadata()
        db_options_response = db_options.get("response", {}) if isinstance(db_options, dict) else {}
        subjects = db_options_response.get("subjects", []) if isinstance(db_options_response, dict) else []
        sessions = db_options_response.get("sessions", []) if isinstance(db_options_response, dict) else []
        if not subjects:
            subjects = OptionsCache.get_subjects(term=option_term, career=option_career)
        if not sessions:
            sessions = OptionsCache.get_sessions(term=option_term, career=option_career)
        return subjects, sessions

    def get_course_attribute_options(db):
        db_options = db.get_search_options_metadata()
        db_options_response = db_options.get("response", {}) if isinstance(db_options, dict) else {}
        crse_attrs = db_options_response.get("crse_attrs", []) if isinstance(db_options_response, dict) else []
        if crse_attrs:
            return crse_attrs
        return db.get_course_attribute_options_from_courses()

    def get_requirement_designation_options(db):
        db_options = db.get_search_options_metadata()
        db_options_response = db_options.get("response", {}) if isinstance(db_options, dict) else {}
        options = db_options_response.get("requirement_designations", []) if isinstance(db_options_response, dict) else []
        if options:
            return options
        return db.get_requirement_designation_options_from_courses()

    def check_cookie_status(term: str, career: str):
        if not has_cookie():
            return {"level": "bad", "message": "未保存 Cookie"}
        try:
            DKUApiClient(timeout=8).fetch_search_options(term=term, career=career)
            return {"level": "ok", "message": "Cookie 当前可用"}
        except DKUCookieError as e:
            return {"level": "bad", "message": str(e)}
        except requests.RequestException as e:
            return {"level": "warn", "message": f"暂时无法连接 DKU 检查 Cookie：{e}"}
        except Exception as e:
            return {"level": "warn", "message": f"暂时无法确认 Cookie 状态：{e}"}

    @app.route("/")
    def index():
        cookie = load_cookie()
        cookie_preview = cookie[:80] + "..." if len(cookie) > 80 else cookie
        terms, careers = get_terms_and_careers()
        default_term = terms[0]["strm"] if terms else DEFAULT_TERM
        option_term = request.args.get("option_term", "").strip() or default_term
        option_career = request.args.get("option_career", "").strip() or DEFAULT_CAREER

        return render_template("index.html",
            current_term=option_term,
            current_career=option_career,
            has_cookie=bool(cookie),
            cookie_preview=cookie_preview,
            cookie_status=check_cookie_status(option_term, option_career),
            terms=terms,
            careers=careers,
        )

    @app.route("/courses")
    def courses():
        db_file = request.args.get("db_file", "").strip()
        db_path = get_existing_db_path(db_file)
        q = request.args.get("q", "").strip()
        term_filter = request.args.get("term_filter", "").strip()
        subject = request.args.get("subject", "").strip()
        catalog_nbr = request.args.get("catalog_nbr", "").strip()
        catalog_nbr_prefix = request.args.get("catalog_nbr_prefix", "").strip()
        class_section = request.args.get("class_section", "").strip()
        component = request.args.get("component", "").strip()
        session_code = request.args.get("session_code", "").strip()
        days = request.args.get("days", "").strip()
        days_exact = request.args.get("days_exact", "").lower() == "true"
        start_time_min = request.args.get("start_time_min", "").strip()
        start_time_max = request.args.get("start_time_max", "").strip()
        end_time_min = request.args.get("end_time_min", "").strip()
        end_time_max = request.args.get("end_time_max", "").strip()
        units = request.args.get("units", "").strip()
        grading_basis = request.args.get("grading_basis", "").strip()
        course_attr = request.args.get("course_attr", "").strip()
        course_attr_value = request.args.get("course_attr_value", "").strip()
        requirement_designation = request.args.get("requirement_designation", "").strip()
        is_combined = request.args.get("is_combined", "").strip()
        enrl_stat = request.args.get("enrl_stat", "").strip()
        instructor_name = request.args.get("instructor_name", "").strip()
        building = request.args.get("building", "").strip()
        classroom = request.args.get("classroom", "").strip()
        duration = request.args.get("duration", "").strip()
        requisite_tag = request.args.get("requisite_tag", "").strip()
        reserved_seats_tag = request.args.get("reserved_seats_tag", "").strip()
        class_note = request.args.get("class_note", "").strip()

        terms, careers = get_terms_and_careers()
        default_term = terms[0]["strm"] if terms else DEFAULT_TERM
        db_options = {}
        rows = []
        total_count = 0
        subjects = []
        sessions = []
        filter_options = {
            "units": [],
            "grading_bases": [],
            "durations": [],
            "components": [],
            "buildings": [],
        }
        course_attribute_options = []
        requirement_designation_options = []
        current_db = ""

        if db_path:
            db = CourseDB(db_path)
            db.init()
            raw_rows = db.search_courses(
                q=q, term_filter=term_filter, subject=subject,
                catalog_nbr=catalog_nbr, catalog_nbr_prefix=catalog_nbr_prefix,
                class_section=class_section, component=component,
                session_code=session_code, days=days, days_exact=days_exact,
                start_time_min=start_time_min, start_time_max=start_time_max,
                end_time_min=end_time_min, end_time_max=end_time_max,
                units=units, grading_basis=grading_basis,
                course_attr=course_attr, course_attr_value=course_attr_value,
                requirement_designation=requirement_designation,
                is_combined=is_combined,
                enrl_stat=enrl_stat,
                instructor_name=instructor_name,
                building=building,
                classroom=classroom,
                duration=duration,
                requisite_tag=requisite_tag,
                reserved_seats_tag=reserved_seats_tag,
                class_note=class_note,
            )
            rows = [build_course_list_view_model(row) for row in raw_rows]
            db_options = db.get_search_options_metadata()
            total_count = db.count_courses(term_filter=term_filter)
            current_db = os.path.basename(db_path)

        option_term = (
            request.args.get("option_term", "").strip()
            or db_options.get("term", "")
            or term_filter
            or default_term
        )
        option_career = (
            request.args.get("option_career", "").strip()
            or db_options.get("career", "")
            or DEFAULT_CAREER
        )
        if db_path:
            subjects, sessions = get_course_options(db, option_term, option_career)
            filter_options = db.get_filter_options()
            course_attribute_options = get_course_attribute_options(db)
            requirement_designation_options = get_requirement_designation_options(db)

        return render_template("courses.html",
            rows=rows,
            q=q,
            term_filter=term_filter,
            subject_filter=subject,
            catalog_nbr=catalog_nbr,
            catalog_nbr_prefix=catalog_nbr_prefix,
            class_section=class_section,
            component=component,
            session_code=session_code,
            days=days,
            days_exact=days_exact,
            start_time_min=start_time_min,
            start_time_max=start_time_max,
            end_time_min=end_time_min,
            end_time_max=end_time_max,
            units=units,
            grading_basis=grading_basis,
            course_attr=course_attr,
            course_attr_value=course_attr_value,
            requirement_designation=requirement_designation,
            is_combined=is_combined,
            enrl_stat=enrl_stat,
            instructor_name=instructor_name,
            building=building,
            classroom=classroom,
            duration=duration,
            requisite_tag=requisite_tag,
            reserved_seats_tag=reserved_seats_tag,
            class_note=class_note,
            total_count=total_count,
            current_term=option_term,
            current_career=option_career,
            has_cookie=has_cookie(),
            db_files=get_db_files(),
            current_db=current_db,
            search_options_synced_at=db_options.get("synced_at", ""),
            subjects=subjects,
            sessions=sessions,
            unit_options=filter_options["units"],
            grading_basis_options=filter_options["grading_bases"],
            duration_options=filter_options["durations"],
            component_options=filter_options["components"],
            building_options=filter_options["buildings"],
            course_attribute_options=course_attribute_options,
            requirement_designation_options=requirement_designation_options,
        )

    def get_latest_db_file():
        files = get_db_files()
        if not files:
            return ""
        return max(files, key=lambda name: os.path.getmtime(get_db_path(name) or ""))

    @app.route("/schedule", methods=["GET", "POST"])
    def schedule():
        db_files = get_db_files()
        bulletin_data = get_bulletin_data()
        bulletin_tracks = bulletin_data.get("tracks", [])
        requested_db = (request.values.get("db_file", "").strip()
                        or get_latest_db_file())
        db_path = get_existing_db_path(requested_db)
        current_db = os.path.basename(db_path) if db_path else requested_db
        db_options = {}
        option_term = DEFAULT_TERM
        option_career = DEFAULT_CAREER
        if db_path:
            db = CourseDB(db_path)
            db.init()
            db_options = db.get_search_options_metadata()
            option_term = db_options.get("term", "") or DEFAULT_TERM
            option_career = db_options.get("career", "") or DEFAULT_CAREER
        term_filter = request.values.get("term_filter", "").strip() or option_term
        source = request.form if request.method == "POST" else request.args

        # ── 默认表单值 ──
        defaults = {
            "db_file": current_db,
            "term_filter": term_filter,
            "class_of": "",
            "year_level": "",
            "identity": "",
            "major_preset": "",
            "major_courses": "",
            "completed_courses": "",
            "must_include": "",
            "must_exclude": "",
            "blocked_times": "",
            "availability_mode": "open_only",
            "ignore_reserved": "false",
            "ignore_grade_req": "false",
            "consent_as_true": "false",
            "equivalent_as_true": "false",
            "recommend_enforced": "true",
            "unknown_as_true": "true",
            "min_units": "16",
            "max_units": "20",
            "session_min_units": "8",
            "session_max_units": "10",
            "freshman_s1_max_units": "8",
            "max_results": "8",
            "max_attempts": "8000",
            "target_multiplier": "3",
            "random_seed": "",
            "target_courses": "",
            "prefer_professors": "",
            "attr_AH": "0",
            "attr_SS": "0",
            "attr_NS": "0",
            "attr_QR": "0",
            "enforce_miniterm": "true",
            "major_focus": "80",
            "recitation_pref": "100",
            "two_credit_pref": "20",
            "risk_aversion": "50",
            "avoid_early": "50",
            "avoid_evening": "50",
            "compactness": "0",
            "day_distribution": "0",
            "pe_gap": "50",
            "total_time": "0",
            "two_credit_extra": "0",
            "pe_count": "2",
            "allow_two_writing": "false",
            "completed_qr": "4",
            "completed_ah": "4",
            "completed_ss": "4",
            "completed_ns": "4",
        }
        # 学位进度默认值
        for gr in get_degree_summary_rules():
            defaults["completed_" + gr["id"]] = ""

        form_state = dict(defaults)
        for key in defaults:
            if key in source:
                form_state[key] = source.get(key)

        # Auto-compute class_of if year_level provided
        yl = to_int(form_state.get("year_level"), 0)
        if yl and not form_state.get("class_of"):
            form_state["class_of"] = str(compute_class_of(yl, term_filter) or "")

        result = None
        error = ""
        load_messages = []

        # 检查当前数据库详情完整性
        detail_warning = ""
        if db_path:
            db_check = CourseDB(db_path)
            db_check.init()
            opt_term = db_check.get_search_options_metadata().get("term", "") or DEFAULT_TERM
            if not db_check.has_detailed_info(term=opt_term):
                detail_warning = "此数据库未拉取课程详情，无法正常排课。请前往首页同步数据库时勾选「拉取详细信息」。"

        if request.method == "POST":
            if not db_path:
                error = "请先选择一个有效数据库。"
            else:
                # 从 DKU 同步已选课程
                if source.get("load_enrolled") and has_cookie():
                    try:
                        courses = DKUApiClient(timeout=12).fetch_drop_classes(term_filter, option_career)
                        loaded = [str(c.get("class_number", "")).strip()
                                  for c in courses if str(c.get("class_number", "")).strip()]
                        existing = [s for s in str(form_state.get("must_include") or "").splitlines() if s.strip()]
                        merged = list(dict.fromkeys(existing + ["#" + n for n in loaded]))
                        form_state["must_include"] = "\n".join(merged)
                        load_messages.append(f"已从 DKU 读取 {len(loaded)} 个已选 class number。")
                    except Exception as e:
                        load_messages.append(f"读取 DKU 已选课程失败：{e}")

                # 从 DKU 同步已修课程
                if source.get("load_completed") and has_cookie():
                    try:
                        history = DKUApiClient(timeout=12).fetch_completed_course_codes()
                        loaded_codes = history.get("course_codes", [])
                        existing = [s for s in str(form_state.get("completed_courses") or "").splitlines() if s.strip()]
                        merged = list(dict.fromkeys(existing + loaded_codes))
                        form_state["completed_courses"] = "\n".join(merged)
                        load_messages.append(
                            f"已从 DKU 读取 {len(loaded_codes)} 门已修课程。"
                        )
                    except Exception as e:
                        load_messages.append(f"读取 DKU 已修课程失败：{e}")

                # ── 构建 ScheduleRequest ──
                req = ScheduleRequest(
                    db_file=current_db,
                    term_filter=term_filter,
                    year_level=yl,
                    class_of=to_int(form_state.get("class_of")) or None,
                    identity=str(form_state.get("identity") or "").strip(),
                    completed_courses=set(
                        s.strip() for s in str(form_state.get("completed_courses") or "").splitlines()
                        if s.strip() and not s.strip().startswith("#")
                    ),
                    must_include=Course.parse_list(form_state.get("must_include", "")),
                    must_exclude=Course.parse_list(form_state.get("must_exclude", "")),
                    blocked_meetings=parse_blocked_meetings(form_state.get("blocked_times", "")),
                    availability_mode=form_state.get("availability_mode", "open_only"),
                    ignore_reserved=form_state.get("ignore_reserved", "false") == "true",
                    ignore_grade_req=form_state.get("ignore_grade_req", "false") == "true",
                    consent_as_true=form_state.get("consent_as_true", "false") == "true",
                    equivalent_as_true=form_state.get("equivalent_as_true", "false") == "true",
                    recommend_enforced=form_state.get("recommend_enforced", "true") == "true",
                    unknown_as_true=form_state.get("unknown_as_true", "true") == "true",
                    min_units=float(form_state.get("min_units", "16")),
                    max_units=float(form_state.get("max_units", "20")),
                    session_min_units=float(form_state.get("session_min_units", "8")),
                    session_max_units=float(form_state.get("session_max_units", "10")),
                    freshman_s1_max_units=float(form_state.get("freshman_s1_max_units", "8")),
                    enforce_miniterm=form_state.get("enforce_miniterm", "true") == "true",
                    major_focus=clamp_int(form_state.get("major_focus"), 80, low=0, high=100),
                    recitation_pref=clamp_int(form_state.get("recitation_pref"), 100, low=0, high=100),
                    two_credit_pref=clamp_int(form_state.get("two_credit_pref"), 20, low=0, high=100),
                    risk_aversion=clamp_int(form_state.get("risk_aversion"), 50, low=0, high=100),
                    avoid_early=clamp_int(form_state.get("avoid_early"), 50, low=0, high=100),
                    avoid_evening=clamp_int(form_state.get("avoid_evening"), 50, low=0, high=100),
                    compactness=clamp_int(form_state.get("compactness"), 50, low=-100, high=100),
                    day_distribution=clamp_int(form_state.get("day_distribution"), 50, low=-100, high=100),
                    pe_gap=clamp_int(form_state.get("pe_gap"), 50, low=0, high=100),
                    total_time=clamp_int(form_state.get("total_time"), 0, low=0, high=100),
                    random_seed=form_state.get("random_seed", ""),
                    max_results=clamp_int(form_state.get("max_results"), 8, low=1, high=30),
                    max_attempts=clamp_int(form_state.get("max_attempts"), 8000, low=100, high=50000),
                    target_multiplier=clamp_int(form_state.get("target_multiplier"), 3, low=1, high=20),
                )
                # pe_count
                req.pe_count = clamp_int(form_state.get("pe_count"), 0, low=0, high=2)
                req.allow_two_writing = form_state.get("allow_two_writing", "false") == "true"
                for attr in ["qr", "ah", "ss", "ns"]:
                    try:
                        setattr(req, f"completed_{attr}_units", float(form_state.get(f"completed_{attr}", "0") or 0))
                    except ValueError:
                        pass
                req.two_credit_pref_extra = float(form_state.get("two_credit_extra", "0") or "0")
                # 想上的课
                target_lines = str(form_state.get("target_courses") or "").splitlines()
                for line in target_lines:
                    line = line.strip()
                    if not line:
                        continue
                    # 兼容旧格式 "课名:优先级"，只取课名部分
                    m = re.match(r"^(.+?):\d{1,3}\s*$", line)
                    code = m.group(1).strip() if m else line
                    req.target_courses.append(Course.parse(code))

                # 教授偏好
                prof_lines = str(form_state.get("prefer_professors") or "").splitlines()
                for line in prof_lines:
                    line = line.strip()
                    if not line:
                        continue
                    m = re.match(r"^(.+?):(-?\d{1,3})\s*$", line)
                    if m:
                        req.prefer_professors.append((m.group(1).strip(), clamp_int(m.group(2), 0, low=-100, high=100)))

                # 属性偏好 (-3 ~ 3 float)
                for attr_code in ["AH", "SS", "NS", "QR"]:
                    try:
                        val = float(form_state.get(f"attr_{attr_code}") or "0")
                        if val != 0:
                            req.prefer_attrs[attr_code] = val
                    except ValueError:
                        pass

                # 专业课程（包含 n_in_m 组信息，用于评分和排课）
                major_codes = set()
                major_n_in_m_groups = []
                for line in str(form_state.get("major_courses") or "").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    nm = re.match(r"n_in_m\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*((?:\"[^\"]*\"\s*,?\s*)+)\)", line, re.IGNORECASE)
                    if nm:
                        total = int(nm.group(1))
                        pick = int(nm.group(2))
                        codes = [c.strip() for c in re.findall(r'"([^"]*)"', nm.group(3))]
                        major_n_in_m_groups.append({"total": total, "pick": pick, "codes": codes})
                        for code in codes:
                            major_codes.add(code)
                    else:
                        major_codes.add(line)
                req.major_courses = major_codes
                req.major_n_in_m = major_n_in_m_groups

                # 学位进度
                for gr in get_degree_summary_rules():
                    val = form_state.get("completed_" + gr["id"], "")
                    try:
                        setattr(req, "completed_" + gr["id"] + "_units", float(val) if val else 0.0)
                    except ValueError:
                        pass

                # 运行排课
                rows = db.search_courses(term_filter=req.term_filter, limit=10000)
                result = build_schedule(rows, req)

        # 学位进度面板
        grad_rules = get_degree_summary_rules()
        for gr in grad_rules:
            gr["id"] = gr["id"]  # ensure id exists

        return render_template(
            "schedule.html",
            db_files=db_files,
            current_db=current_db,
            current_term=term_filter,
            current_career=option_career,
            form=form_state,
            result=result,
            error=error,
            detail_warning=detail_warning,
            has_cookie=has_cookie(),
            load_messages=load_messages,
            bulletin_tracks=bulletin_tracks,
            identities=IDENTITIES,
            grad_rules=grad_rules,
            graduation_summary=get_graduation_summary(),
        )

    @app.route("/sync_dku_info", methods=["POST"])
    def sync_dku_info():
        """从 DKU 获取已选课程和已修课程信息，返回 JSON。"""
        if not has_cookie():
            return jsonify({"ok": False, "error": "未保存 Cookie"})
        term = request.form.get("term", DEFAULT_TERM).strip() or DEFAULT_TERM
        career = request.form.get("career", DEFAULT_CAREER).strip() or DEFAULT_CAREER
        result = {"ok": True, "enrolled": [], "completed": []}
        try:
            courses = DKUApiClient(timeout=12).fetch_drop_classes(term, career)
            result["enrolled"] = [
                str(c.get("class_number", "")).strip()
                for c in courses if str(c.get("class_number", "")).strip()
            ]
        except Exception as e:
            result["enrolled_error"] = str(e)
        try:
            history = DKUApiClient(timeout=12).fetch_completed_course_codes()
            result["completed"] = history.get("course_codes", [])
        except Exception as e:
            result["completed_error"] = str(e)
        return jsonify(result)

    def sse(data):
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    @app.route("/save_cookie", methods=["POST"])
    def save_cookie_route():
        cookie = request.form.get("cookie", "").strip()
        if not cookie:
            return "<script>alert('Cookie 不能为空');history.back();</script>"
        save_cookie(cookie)
        OptionsCache.clear()
        OptionsCache.load(DEFAULT_TERM, DEFAULT_CAREER)
        return redirect(url_for("index"))

    @app.route("/sync_courses_stream", methods=["POST"])
    def sync_courses_stream():
        if not has_cookie():
            return Response(sse({"status": "error", "error": "请先填写 Cookie"}), status=400,
                            mimetype="text/event-stream")
        term = request.form.get("term", DEFAULT_TERM).strip() or DEFAULT_TERM
        career = request.form.get("career", DEFAULT_CAREER).strip() or DEFAULT_CAREER
        fetch_details = request.form.get("fetch_details", "false").lower() == "true"
        db_file = request.args.get("db_file", "").strip()
        if db_file:
            db_path = get_db_path(db_file)
            if not db_path:
                return Response(sse({"status": "error", "error": "请先选择有效的数据库文件"}), status=400,
                                mimetype="text/event-stream")
            db_file = os.path.basename(db_path)
        else:
            db_file = ""
            db_path = None
        generated_db = {"file": db_file, "path": db_path}

        def build_generated_db_path(synced_datetime):
            date_str = synced_datetime.strftime("%Y-%m-%d-%H-%M-%S")
            generated_db["file"] = build_course_db_filename(career, term, date_str)
            generated_db["path"] = get_db_path(generated_db["file"])
            remove_existing_db_files(generated_db["path"])
            return generated_db["path"]

        def generate():
            yield sse({"status": "syncing", "term_code": term})
            try:
                if db_path:
                    remove_existing_db_files(db_path)
                    db_path_kwargs = {"db_path": db_path}
                else:
                    db_path_kwargs = {"db_path_builder": build_generated_db_path}
                for event in CourseService.iter_sync_from_dku_events(
                    term=term,
                    career=career,
                    fetch_details=fetch_details,
                    **db_path_kwargs,
                ):
                    event = {**event, "term_code": term}
                    if event["status"] == "completed":
                        event["db_filename"] = os.path.basename(generated_db["path"])
                        event["courses_url"] = url_for(
                            "courses",
                            db_file=generated_db["file"],
                            option_term=term,
                            option_career=career,
                            term_filter=term,
                        )
                    yield sse(event)
            except Exception as e:
                yield sse({"status": "error", "term_code": term, "error": str(e)})
                return
            yield sse({"status": "done"})

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    @app.route("/course/<class_nbr>")
    def course_detail_page(class_nbr: str):
        db_file = request.args.get("db_file", "").strip()
        db_path = require_selected_db(db_file)
        if not db_path:
            return "请先在课程筛选页选择数据库<br><a href='/courses'>返回课程筛选页</a>", 400
        db_file = os.path.basename(db_path)
        db = CourseDB(db_path)
        db.init()
        term = request.args.get("term", "").strip() or DEFAULT_TERM
        career = request.args.get("career", "").strip() or DEFAULT_CAREER
        row = db.get_course_by_class_nbr(class_nbr, term=term)
        if not row:
            return f"找不到 class_nbr={class_nbr} 的课程"

        refresh_error = None
        if not row["detail_raw_json"] and has_cookie():
            try:
                ok = CourseService.refresh_one_detail(class_nbr, term, career, db_path)
                if ok:
                    row = db.get_course_by_class_nbr(class_nbr, term=term)
                else:
                    refresh_error = f"无法获取 {term} 学期 class_nbr={class_nbr} 的详细数据"
            except Exception as e:
                refresh_error = str(e)

        if not row:
            return f"抓取详情后仍未找到 class_nbr={class_nbr} 的课程"

        detail_sync_message = request.args.get("detail_sync_message", "").strip()
        detail_raw = parse_detail_raw(row["detail_raw_json"])
        vm = build_course_detail_view_model(row, detail_raw, get_course_attribute_options(db))
        _enrich_related_sections(vm, db, to_int(class_nbr, 0), term, career, db_path)
        return render_template("detail.html", detail=vm, requested_db=db_file,
                               has_cookie=has_cookie(),
                               refresh_error=refresh_error,
                               detail_sync_message=detail_sync_message)

    @app.route("/course/<class_nbr>/sync_detail", methods=["POST"])
    def sync_course_detail(class_nbr: str):
        if not has_cookie():
            return "请先填写 Cookie<br><a href='/'>返回首页</a>", 400
        db_file = request.args.get("db_file", "").strip()
        db_path = require_selected_db(db_file)
        if not db_path:
            return "请先在课程筛选页选择数据库<br><a href='/courses'>返回课程筛选页</a>", 400
        db_file = os.path.basename(db_path)
        term = request.args.get("term", "").strip() or DEFAULT_TERM
        career = request.args.get("career", "").strip() or DEFAULT_CAREER
        try:
            ok = CourseService.refresh_one_detail(class_nbr, term, career, db_path)
            if ok:
                message = "详细信息已重新同步"
            else:
                message = f"无法获取 {term} 学期 class_nbr={class_nbr} 的详细数据"
        except Exception as e:
            message = str(e)
        return redirect(url_for("course_detail_page", class_nbr=class_nbr,
                                term=term, career=career, db_file=db_file,
                                detail_sync_message=message))

    @app.route("/export/courses")
    def export_courses():
        db_file = request.args.get("db_file", "").strip()
        db_path = require_selected_db(db_file)
        if not db_path:
            return "请先在课程筛选页选择数据库<br><a href='/courses'>返回课程筛选页</a>", 400
        db_file = os.path.basename(db_path)
        db = CourseDB(db_path)
        db.init()
        rows = db.export_all_courses()
        output = io.StringIO()
        writer = csv.writer(output)
        if rows:
            writer.writerow(rows[0].keys())
        for row in rows:
            writer.writerow(row)
        return Response(output.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=courses_{db_file}.csv"})

    @app.route("/sync_all_terms_stream", methods=["POST"])
    def sync_all_terms_stream():
        if not has_cookie():
            return Response(sse({"status": "error", "error": "请先填写 Cookie"}), status=400,
                            mimetype="text/event-stream")
        fetch_details = request.form.get("fetch_details", "false").lower() == "true"
        career = request.form.get("career", DEFAULT_CAREER).strip() or DEFAULT_CAREER

        def generate():
            terms = OptionsCache.get_terms()
            if not terms:
                yield sse({"status": "error", "error": "无法获取学期列表"})
                return
            total = len(terms)
            for i, term_info in enumerate(terms, 1):
                term_code = term_info["strm"]
                yield sse({"status": "syncing", "current": i, "total": total,
                           "term_code": term_code, "term_descr": term_info["descr"]})
                generated_db = {"file": "", "path": ""}

                def build_generated_db_path(synced_datetime, term_code=term_code):
                    date_str = synced_datetime.strftime("%Y-%m-%d-%H-%M-%S")
                    generated_db["file"] = build_course_db_filename(career, term_code, date_str)
                    generated_db["path"] = get_db_path(generated_db["file"])
                    remove_existing_db_files(generated_db["path"])
                    return generated_db["path"]

                try:
                    for event in CourseService.iter_sync_from_dku_events(
                        term=term_code,
                        career=career,
                        fetch_details=fetch_details,
                        db_path_builder=build_generated_db_path,
                    ):
                        if event["status"] in {"details_start", "detail_progress"}:
                            yield sse({**event, "term_code": term_code,
                                       "term_descr": term_info["descr"],
                                       "term_current": i, "term_total": total})
                        elif event["status"] == "completed":
                            yield sse({"status": "completed", "current": i, "total": total,
                                       "term_code": term_code, "count": event["count"],
                                       "db_filename": generated_db["file"]})
                except Exception as e:
                    yield sse({"status": "error", "current": i, "total": total,
                               "term_code": term_code, "error": str(e)})
            yield sse({"status": "done"})

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    @app.route("/sync_selected_terms_stream", methods=["POST"])
    def sync_selected_terms_stream():
        if not has_cookie():
            return Response(sse({"status": "error", "error": "请先填写 Cookie"}), status=400,
                            mimetype="text/event-stream")
        fetch_details = request.form.get("fetch_details", "false").lower() == "true"
        career = request.form.get("career", DEFAULT_CAREER).strip() or DEFAULT_CAREER
        selected_str = request.form.get("terms", "")
        selected = [t.strip() for t in selected_str.split(",") if t.strip()]

        def generate():
            if not selected:
                yield sse({"status": "error", "error": "未选择任何学期"})
                return
            total = len(selected)
            for i, term_code in enumerate(selected, 1):
                yield sse({"status": "syncing", "current": i, "total": total,
                           "term_code": term_code, "term_descr": term_code})
                generated_db = {"file": "", "path": ""}

                def build_generated_db_path(synced_datetime, term_code=term_code):
                    date_str = synced_datetime.strftime("%Y-%m-%d-%H-%M-%S")
                    generated_db["file"] = build_course_db_filename(career, term_code, date_str)
                    generated_db["path"] = get_db_path(generated_db["file"])
                    remove_existing_db_files(generated_db["path"])
                    return generated_db["path"]

                try:
                    for event in CourseService.iter_sync_from_dku_events(
                        term=term_code,
                        career=career,
                        fetch_details=fetch_details,
                        db_path_builder=build_generated_db_path,
                    ):
                        if event["status"] == "completed":
                            yield sse({"status": "completed", "current": i, "total": total,
                                       "term_code": term_code, "count": event["count"],
                                       "db_filename": generated_db["file"]})
                except Exception as e:
                    yield sse({"status": "error", "current": i, "total": total,
                               "term_code": term_code, "error": str(e)})
            yield sse({"status": "done"})

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    @app.route("/class_nbr_names", methods=["POST"])
    def class_nbr_names():
        db_file = request.form.get("db_file", "").strip()
        db_path = require_selected_db(db_file)
        if not db_path:
            return jsonify({"ok": False, "error": "请先选择数据库"})
        db = CourseDB(db_path)
        db.init()
        nbrs_text = request.form.get("class_nbrs", "")
        nbrs = [n.strip() for n in re.split(r"[\n,;\s]+", nbrs_text) if n.strip().isdigit()]
        result = {}
        for nbr in nbrs:
            row = db.get_course_by_class_nbr(nbr)
            if row:
                from view_models import build_course_list_view_model
                vm = build_course_list_view_model(row)
                result[nbr] = {
                    "course_code": vm.get("course_code", ""),
                    "course_name": vm.get("course_name", ""),
                    "component": vm.get("component", ""),
                    "class_section": vm.get("class_section", ""),
                }
        return jsonify({"ok": True, "courses": result})

    # ── Cookie 自动获取 ──
    _grab_state = {"status": "idle", "message": "", "cookie": ""}

    @app.route("/grab_cookie", methods=["POST"])
    def grab_cookie_start():
        """启动 Cookie 获取（背景线程）"""
        from cookie_grabber import grab_cookie
        import threading

        # 如果之前卡住了，先重置
        if _grab_state["status"] == "running":
            _grab_state["status"] = "idle"
            _grab_state["message"] = "上一次获取已中止"

        _grab_state["status"] = "running"
        _grab_state["message"] = "启动中..."
        _grab_state["cookie"] = ""

        def _run():
            try:
                cookie_str = grab_cookie(
                    timeout=600,
                    progress_callback=lambda stage, msg: _grab_state.update(
                        {"message": msg, "status": "running" if stage != "ok" and stage != "error" else stage}
                    ),
                )
                if cookie_str:
                    from utils import save_cookie
                    save_cookie(cookie_str)
                    _grab_state["status"] = "ok"
                    _grab_state["cookie"] = cookie_str[:80] + "..."
                    _grab_state["message"] = "Cookie 已保存"
                else:
                    _grab_state["status"] = "error"
                    if not _grab_state["message"] or _grab_state["message"] == "启动中...":
                        _grab_state["message"] = "获取失败，请确认 Chrome 已用调试模式启动"
            except Exception as e:
                _grab_state["status"] = "error"
                _grab_state["message"] = str(e)

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"ok": True, "message": "started"})

    @app.route("/grab_cookie/status", methods=["GET"])
    def grab_cookie_status():
        return jsonify(_grab_state.copy())

    @app.route("/grab_cookie/reset", methods=["POST"])
    def grab_cookie_reset():
        _grab_state["status"] = "idle"
        _grab_state["message"] = ""
        _grab_state["cookie"] = ""
        return jsonify({"ok": True})

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000, use_reloader=False)
