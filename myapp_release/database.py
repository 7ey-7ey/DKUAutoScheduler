import json
import sqlite3
from contextlib import closing
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from utils import first_mapping, load_json_value, parse_meeting_location, to_int


def parse_meeting_location_from_search_raw(raw_json: str) -> Dict[str, str]:
    payload = load_json_value(raw_json, {})
    if not isinstance(payload, dict):
        return {"building": "", "classroom": ""}
    meeting = first_mapping(payload.get("meetings", []))
    building, classroom = parse_meeting_location(meeting)
    return {"building": building, "classroom": classroom}


class CourseDB:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def init(self):
        with closing(self.get_conn()) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS courses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_key TEXT UNIQUE NOT NULL,
                    term TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    subject_descr TEXT,
                    catalog_nbr TEXT NOT NULL,
                    class_section TEXT NOT NULL,
                    course_name TEXT DEFAULT '',
                    meeting_duration INTEGER DEFAULT 0,
                    units TEXT,
                    grading_basis TEXT,
                    component TEXT,
                    class_nbr TEXT NOT NULL,
                    session_code TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    meeting_days TEXT,
                    meeting_start_time TEXT,
                    meeting_end_time TEXT,
                    building TEXT DEFAULT '',
                    classroom TEXT DEFAULT '',
                    instructor_names TEXT,
                    enrl_status TEXT,
                    class_capacity INTEGER DEFAULT 0,
                    enrollment_available INTEGER DEFAULT 0,
                    wait_list_capacity INTEGER DEFAULT 0,
                    wait_list_available INTEGER DEFAULT 0,
                    is_combined TEXT DEFAULT 'N',
                    course_attributes TEXT,
                    course_attribute_values TEXT,
                    requirement_designation TEXT DEFAULT '',
                    enroll_requirements TEXT,
                    prereqs TEXT,
                    antireqs TEXT,
                    coreqs TEXT DEFAULT '',
                    combined_sections_json TEXT,
                    related_sections_json TEXT DEFAULT '',
                    search_raw_json TEXT,
                    detail_raw_json TEXT,
                    enroll_detail_raw_json TEXT DEFAULT '',
                    requisite_raw TEXT DEFAULT '',
                    requisite_parsed_json TEXT DEFAULT '',
                    reserved_seats_raw TEXT DEFAULT '',
                    reserved_seats_parsed_json TEXT DEFAULT '',
                    requisite_tags TEXT DEFAULT '',
                    reserved_seats_tags TEXT DEFAULT '',
                    search_synced_at TEXT,
                    detail_synced_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_courses_term ON courses(term);
                CREATE INDEX IF NOT EXISTS idx_courses_subject ON courses(subject);
                CREATE INDEX IF NOT EXISTS idx_courses_class_nbr ON courses(class_nbr);
                CREATE INDEX IF NOT EXISTS idx_courses_instructor ON courses(instructor_names);

                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            # 兼容旧数据库：尝试添加新列
            for col in [
                ("course_name", "TEXT DEFAULT ''"),
                ("meeting_duration", "INTEGER DEFAULT 0"),
                ("requirement_designation", "TEXT DEFAULT ''"),
                ("building", "TEXT DEFAULT ''"),
                ("classroom", "TEXT DEFAULT ''"),
                ("related_sections_json", "TEXT DEFAULT ''"),
                ("enroll_detail_raw_json", "TEXT DEFAULT ''"),
                ("requisite_raw", "TEXT DEFAULT ''"),
                ("requisite_parsed_json", "TEXT DEFAULT ''"),
                ("reserved_seats_raw", "TEXT DEFAULT ''"),
                ("reserved_seats_parsed_json", "TEXT DEFAULT ''"),
                ("requisite_tags", "TEXT DEFAULT ''"),
                ("reserved_seats_tags", "TEXT DEFAULT ''"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE courses ADD COLUMN {col[0]} {col[1]}")
                except sqlite3.OperationalError:
                    pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_courses_building ON courses(building)")
            rows_to_backfill = conn.execute(
                """
                SELECT id, search_raw_json
                FROM courses
                WHERE COALESCE(requirement_designation, '') = ''
                  AND COALESCE(search_raw_json, '') != ''
                """
            ).fetchall()
            for row in rows_to_backfill:
                payload = load_json_value(row["search_raw_json"], {})
                if not isinstance(payload, dict):
                    continue
                requirement_designation = str(payload.get("rqmnt_designtn", "") or "").strip()
                if requirement_designation:
                    conn.execute(
                        "UPDATE courses SET requirement_designation = ? WHERE id = ?",
                        (requirement_designation, row["id"]),
                    )
            location_rows = conn.execute(
                """
                SELECT id, search_raw_json
                FROM courses
                WHERE (COALESCE(building, '') = '' OR COALESCE(classroom, '') = '')
                  AND COALESCE(search_raw_json, '') != ''
                """
            ).fetchall()
            for row in location_rows:
                location = parse_meeting_location_from_search_raw(row["search_raw_json"])
                if location["building"] or location["classroom"]:
                    conn.execute(
                        """
                        UPDATE courses
                        SET building = CASE WHEN COALESCE(building, '') = '' THEN ? ELSE building END,
                            classroom = CASE WHEN COALESCE(classroom, '') = '' THEN ? ELSE classroom END
                        WHERE id = ?
                        """,
                        (location["building"], location["classroom"], row["id"]),
                    )
            conn.commit()
            # Backfill requisite and reserved seat parsed data from existing raw JSON
            backfill = conn.execute(
                """
                SELECT id, detail_raw_json, search_raw_json,
                       COALESCE(requisite_parsed_json, '') as rp,
                       COALESCE(reserved_seats_parsed_json, '') as rs
                FROM courses
                WHERE COALESCE(detail_raw_json, '') != ''
                  AND (COALESCE(requisite_parsed_json, '') = ''
                       OR COALESCE(reserved_seats_parsed_json, '') = '')
                """
            ).fetchall()
            backfill_version = conn.execute(
                "SELECT value FROM metadata WHERE key='requisite_backfill_version'"
            ).fetchone()
            current_version = "v2"
            force = (backfill_version is None or backfill_version[0] != current_version)
            if force:
                extra = conn.execute(
                    """
                    SELECT id, detail_raw_json, search_raw_json
                    FROM courses
                    WHERE COALESCE(detail_raw_json, '') != ''
                      AND COALESCE(requisite_parsed_json, '') != ''
                    """
                ).fetchall()
                backfill.extend(extra)
            if backfill:
                from requisite_parser import (parse_requisite_text,
                                              extract_requirements_from_detail_json,
                                              classify_requisite)
                from reserved_seat_parser import (extract_reserve_caps,
                                                  parse_reserve_caps,
                                                  classify_reserved_seats)
                for br in backfill:
                    updates = {}
                    d = br["detail_raw_json"] or ""
                    s = br["search_raw_json"] or ""
                    req = extract_requirements_from_detail_json(d)
                    if req:
                        try:
                            p = parse_requisite_text(req)
                            updates["requisite_raw"] = req
                            updates["requisite_parsed_json"] = json.dumps(p, ensure_ascii=False)
                            updates["requisite_tags"] = ",".join(classify_requisite(p))
                        except Exception:
                            pass
                    caps, _ = extract_reserve_caps(s, d)
                    if caps:
                        try:
                            pc = parse_reserve_caps(caps)
                            updates["reserved_seats_raw"] = json.dumps(caps, ensure_ascii=False)
                            updates["reserved_seats_parsed_json"] = json.dumps(pc, ensure_ascii=False)
                            updates["reserved_seats_tags"] = ",".join(classify_reserved_seats(pc))
                        except Exception:
                            pass
                    if updates:
                        set_clause = ", ".join(f"{k}=:{k}" for k in updates)
                        conn.execute(f"UPDATE courses SET {set_clause} WHERE id=:id",
                                     {**updates, "id": br["id"]})
                conn.execute(
                    "INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES (?, ?, ?)",
                    ("requisite_backfill_version", current_version,
                     datetime.now(timezone.utc).isoformat(timespec="seconds")),
                )
                conn.commit()

    def set_metadata(self, key: str, value: Any, updated_at: Optional[str] = None):
        payload = json.dumps(value, ensure_ascii=False)
        now = updated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self.get_conn()) as conn:
            conn.execute(
                """
                INSERT INTO metadata (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (key, payload, now),
            )
            conn.commit()

    def get_metadata(self, key: str, default: Any = None) -> Any:
        with closing(self.get_conn()) as conn:
            try:
                row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
            except sqlite3.OperationalError:
                return default
            if not row:
                return default
            try:
                return json.loads(row["value"])
            except json.JSONDecodeError:
                return default

    def save_search_options_metadata(self, term: str, career: str,
                                     response: Dict[str, Any], course_count: int,
                                     synced_at: Optional[str] = None):
        synced_at = synced_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.set_metadata("search_options", {
            "term": term,
            "career": career,
            "course_count": course_count,
            "synced_at": synced_at,
            "response": response,
        }, updated_at=synced_at)

    def get_search_options_metadata(self) -> Dict[str, Any]:
        metadata = self.get_metadata("search_options", {})
        if isinstance(metadata, dict):
            return metadata
        return {}

    def upsert_courses(self, rows: List[Dict[str, Any]]):
        with closing(self.get_conn()) as conn:
            for row in rows:
                cur = conn.execute("SELECT id FROM courses WHERE course_key = ?", (row["course_key"],))
                existing = cur.fetchone()
                if existing:
                    set_clause = ", ".join(f"{k}=:{k}" for k in row if k != "course_key")
                    conn.execute(f"UPDATE courses SET {set_clause} WHERE id=:id", {**row, "id": existing["id"]})
                else:
                    columns = ", ".join(row.keys())
                    placeholders = ", ".join(f":{k}" for k in row)
                    conn.execute(f"INSERT INTO courses ({columns}) VALUES ({placeholders})", row)
            conn.commit()

    def replace_courses(self, rows: List[Dict[str, Any]]):
        with closing(self.get_conn()) as conn:
            conn.execute("DELETE FROM courses")
            try:
                conn.execute("DELETE FROM sqlite_sequence WHERE name = 'courses'")
            except sqlite3.OperationalError:
                pass
            for row in rows:
                cur = conn.execute("SELECT id FROM courses WHERE course_key = ?", (row["course_key"],))
                existing = cur.fetchone()
                if existing:
                    set_clause = ", ".join(f"{k}=:{k}" for k in row if k != "course_key")
                    conn.execute(f"UPDATE courses SET {set_clause} WHERE id=:id", {**row, "id": existing["id"]})
                else:
                    columns = ", ".join(row.keys())
                    placeholders = ", ".join(f":{k}" for k in row)
                    conn.execute(f"INSERT INTO courses ({columns}) VALUES ({placeholders})", row)
            conn.commit()

    def get_course_by_class_nbr(self, class_nbr: str, term: str = "") -> Optional[sqlite3.Row]:
        with closing(self.get_conn()) as conn:
            if term:
                return conn.execute("SELECT * FROM courses WHERE class_nbr = ? AND term = ?", (class_nbr, term)).fetchone()
            return conn.execute("SELECT * FROM courses WHERE class_nbr = ? ORDER BY term DESC", (class_nbr,)).fetchone()

    def search_courses(self,
                       q: str = "",
                       term_filter: str = "",
                       subject: str = "",
                       catalog_nbr: str = "",
                       catalog_nbr_prefix: str = "",
                       class_section: str = "",
                       component: str = "",
                       session_code: str = "",
                       days: str = "",
                       days_exact: bool = False,
                       start_time_min: str = "",
                       start_time_max: str = "",
                       end_time_min: str = "",
                       end_time_max: str = "",
                       units: str = "",
                       grading_basis: str = "",
                       course_attr: str = "",
                       course_attr_value: str = "",
                       requirement_designation: str = "",
                       is_combined: str = "",
                       enrl_stat: str = "",
                       instructor_name: str = "",
                       building: str = "",
                       classroom: str = "",
                       duration: str = "",
                        requisite_tag: str = "",
                        reserved_seats_tag: str = "",
                        class_note: str = "",
                       limit: int = 500) -> List[sqlite3.Row]:
        with closing(self.get_conn()) as conn:
            sql = "SELECT * FROM courses WHERE 1=1"
            params = []
            if term_filter:
                sql += " AND term = ?"
                params.append(term_filter)
            if subject:
                sql += " AND subject = ?"
                params.append(subject.upper())
            if catalog_nbr_prefix:
                sql += " AND catalog_nbr LIKE ?"
                params.append(f"{catalog_nbr_prefix}%")
            elif catalog_nbr:
                sql += " AND catalog_nbr = ?"
                params.append(catalog_nbr)
            if class_section:
                sql += " AND class_section LIKE ?"
                params.append(f"%{class_section}%")
            if component:
                sql += " AND component = ?"
                params.append(component)
            if session_code:
                sql += " AND session_code = ?"
                params.append(session_code)
            if days:
                if days_exact:
                    sql += " AND meeting_days = ?"
                    params.append(days)
                else:
                    sql += " AND meeting_days LIKE ?"
                    params.append(f"%{days}%")
            if start_time_min:
                sql += " AND meeting_start_time >= ?"
                params.append(start_time_min)
            if start_time_max:
                sql += " AND meeting_start_time <= ?"
                params.append(start_time_max)
            if end_time_min:
                sql += " AND meeting_end_time >= ?"
                params.append(end_time_min)
            if end_time_max:
                sql += " AND meeting_end_time <= ?"
                params.append(end_time_max)
            if units:
                sql += " AND units = ?"
                params.append(units)
            if grading_basis:
                sql += " AND grading_basis = ?"
                params.append(grading_basis)
            if course_attr:
                sql += " AND (',' || course_attributes || ',') LIKE ?"
                params.append(f"%,{course_attr},%")
            if course_attr_value:
                sql += " AND (',' || course_attribute_values || ',') LIKE ?"
                params.append(f"%,{course_attr_value},%")
            if requirement_designation:
                sql += " AND requirement_designation = ?"
                params.append(requirement_designation)
            if is_combined in {"Y", "N"}:
                sql += " AND is_combined = ?"
                params.append(is_combined)
            if enrl_stat:
                sql += " AND enrl_status = ?"
                params.append(enrl_stat)
            if instructor_name:
                sql += " AND instructor_names LIKE ?"
                params.append(f"%{instructor_name}%")
            if building:
                sql += " AND building = ?"
                params.append(building)
            if classroom:
                sql += " AND classroom LIKE ?"
                params.append(f"%{classroom}%")
            if duration:
                parsed_duration = to_int(duration, None)
                if parsed_duration is not None:
                    sql += " AND meeting_duration = ?"
                    params.append(parsed_duration)
            if requisite_tag:
                sql += " AND COALESCE(requisite_tags,'') LIKE ?"
                params.append(f"%{requisite_tag}%")
            if reserved_seats_tag:
                sql += " AND COALESCE(reserved_seats_tags,'') LIKE ?"
                params.append(f"%{reserved_seats_tag}%")
            if class_note == "1":
                sql += " AND json_extract(detail_raw_json, '$.section_info.notes.class_notes') IS NOT NULL"
                sql += " AND json_extract(detail_raw_json, '$.section_info.notes.class_notes') != ''"
            elif class_note == "0":
                sql += " AND (detail_raw_json IS NULL"
                sql += " OR json_extract(detail_raw_json, '$.section_info.notes.class_notes') IS NULL"
                sql += " OR json_extract(detail_raw_json, '$.section_info.notes.class_notes') = '')"

            if q:
                sql += " AND (subject || ' ' || catalog_nbr LIKE ? OR instructor_names LIKE ? OR subject_descr LIKE ? OR course_name LIKE ?)"
                like = f"%{q}%"
                params.extend([like, like, like, like])
            sql += " ORDER BY term DESC, subject, catalog_nbr LIMIT ?"
            params.append(limit)
            return conn.execute(sql, params).fetchall()

    def get_filter_options(self) -> Dict[str, List[Any]]:
        with closing(self.get_conn()) as conn:
            units = [
                row[0] for row in conn.execute(
                    "SELECT DISTINCT units FROM courses WHERE COALESCE(units, '') != '' ORDER BY units"
                ).fetchall()
            ]
            grading_bases = [
                row[0] for row in conn.execute(
                    "SELECT DISTINCT grading_basis FROM courses WHERE COALESCE(grading_basis, '') != '' ORDER BY grading_basis"
                ).fetchall()
            ]
            durations = [
                row[0] for row in conn.execute(
                    """
                    SELECT DISTINCT meeting_duration
                    FROM courses
                    WHERE meeting_duration IS NOT NULL AND meeting_duration > 0
                    ORDER BY meeting_duration
                    """
                ).fetchall()
            ]
            components = [
                row[0] for row in conn.execute(
                    "SELECT DISTINCT component FROM courses WHERE COALESCE(component, '') != '' ORDER BY component"
                ).fetchall()
            ]
            buildings = [
                row[0] for row in conn.execute(
                    "SELECT DISTINCT building FROM courses WHERE COALESCE(building, '') != '' ORDER BY building"
                ).fetchall()
            ]
            return {
                "units": units,
                "grading_bases": grading_bases,
                "durations": durations,
                "components": components,
                "buildings": buildings,
            }

    def get_requirement_designation_options_from_courses(self) -> List[Dict[str, str]]:
        with closing(self.get_conn()) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT requirement_designation
                FROM courses
                WHERE COALESCE(requirement_designation, '') != ''
                ORDER BY requirement_designation
                """
            ).fetchall()
        return [
            {"rqmnt_designtn": row["requirement_designation"], "descr": row["requirement_designation"]}
            for row in rows
        ]

    def get_course_attribute_options_from_courses(self) -> List[Dict[str, Any]]:
        with closing(self.get_conn()) as conn:
            rows = conn.execute(
                """
                SELECT course_attributes, course_attribute_values
                FROM courses
                WHERE COALESCE(course_attributes, '') != ''
                   OR COALESCE(course_attribute_values, '') != ''
                """
            ).fetchall()
        attr_codes = set()
        values_by_attr = {}
        for row in rows:
            for attr in str(row["course_attributes"] or "").split(","):
                attr = attr.strip()
                if attr:
                    attr_codes.add(attr)
                    values_by_attr.setdefault(attr, set())
            for value in str(row["course_attribute_values"] or "").split(","):
                value = value.strip()
                if not value:
                    continue
                attr = value.split("-", 1)[0] if "-" in value else ""
                if attr:
                    attr_codes.add(attr)
                    values_by_attr.setdefault(attr, set()).add(value)

        options = []
        for attr in sorted(attr_codes):
            options.append({
                "crse_attr": attr,
                "descr": attr,
                "values": [
                    {"crse_attr_value": value, "descr": value}
                    for value in sorted(values_by_attr.get(attr, set()))
                ],
            })
        return options

    def count_courses(self, term_filter: str = "") -> int:
        with closing(self.get_conn()) as conn:
            if term_filter:
                cur = conn.execute("SELECT COUNT(*) FROM courses WHERE term = ?", (term_filter,))
            else:
                cur = conn.execute("SELECT COUNT(*) FROM courses")
            return cur.fetchone()[0]

    def export_all_courses(self) -> List[sqlite3.Row]:
        with closing(self.get_conn()) as conn:
            return conn.execute("SELECT * FROM courses ORDER BY subject, catalog_nbr").fetchall()

    def has_detailed_info(self, term: str = "") -> bool:
        """检查数据库是否包含课程详细信息。
           search_raw_json（大课搜索）和 detail_raw_json（课程详情）≥95% 即为合格。
           related_sections_json 不参与判断，因为独立课程天然无关联 section。"""
        with closing(self.get_conn()) as conn:
            if term:
                total = conn.execute("SELECT COUNT(*) FROM courses WHERE term = ?", (term,)).fetchone()[0]
                detailed = conn.execute(
                    """SELECT COUNT(*) FROM courses WHERE term = ?
                        AND search_raw_json IS NOT NULL AND search_raw_json != ''
                        AND detail_raw_json IS NOT NULL AND detail_raw_json != ''""",
                    (term,)
                ).fetchone()[0]
            else:
                total = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
                detailed = conn.execute(
                    """SELECT COUNT(*) FROM courses
                        WHERE search_raw_json IS NOT NULL AND search_raw_json != ''
                        AND detail_raw_json IS NOT NULL AND detail_raw_json != ''"""
                ).fetchone()[0]
            if total == 0:
                return False
            return detailed > 0 and detailed >= total * 0.95
