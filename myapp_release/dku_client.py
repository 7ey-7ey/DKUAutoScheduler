import re
import requests
from datetime import date, datetime
from typing import Dict, List, Optional
from config import (DKU_OPTIONS_URL, DKU_SEARCH_URL, DKU_DETAIL_URL,
                    DKU_ENROLL_DETAIL_URL, DKU_DROP_CLASSES_URL,
                    DKU_SCHEDULE_TERMS_URL, DKU_SCHEDULE_BY_TERM_URL,
                    DEFAULT_TERM, DEFAULT_CAREER)
from utils import load_cookie, has_cookie


class DKUCookieError(RuntimeError):
    pass


class DKUApiClient:
    def __init__(self, timeout: int = 20):
        if not has_cookie():
            raise RuntimeError("请先在首页填写并保存 DKU Cookie。")
        self.cookie = load_cookie()
        self.timeout = timeout
        self._session = requests.Session()
        self._session.trust_env = False  # 不走系统代理，避免 ProxyError

    def _csrf_token(self) -> Optional[str]:
        match = re.search(r"CSRFCookie=([^;]+)", self.cookie)
        return match.group(1) if match else None

    def _headers(self) -> Dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Cookie": self.cookie,
            "Referer": "https://dkuhub.dku.edu.cn/psc/CSPRD01/EMPLOYEE/SA/s/"
                       "WEBLIB_HCX_CM.H_CLASS_SEARCH.FieldFormula.IScript_Main",
        }

    def _enroll_headers(self) -> Dict[str, str]:
        headers = self._headers()
        token = self._csrf_token()
        if token:
            headers["X-CSRF-Token"] = token
        return headers

    def _get_json(self, url: str, params: Dict, action_name: str):
        resp = self._session.get(url, params=params, headers=self._headers(), timeout=self.timeout)
        if resp.status_code in {401, 403}:
            raise DKUCookieError(f"{action_name}失败：DKU Cookie 可能已过期，请重新登录 DKUHub 后复制新的 Cookie。")
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        try:
            return resp.json()
        except ValueError:
            if "text/html" in content_type.lower() or "<html" in resp.text[:300].lower():
                raise DKUCookieError(f"{action_name}失败：DKU 返回了登录页面，Cookie 可能已过期，请重新登录 DKUHub 后复制新的 Cookie。")
            raise RuntimeError(f"{action_name}失败：DKU 接口返回的不是 JSON 内容。")

    def fetch_search_options(self,
                             term: Optional[str] = DEFAULT_TERM,
                             career: Optional[str] = DEFAULT_CAREER) -> Dict:
        params = {"institution": "DKUNV"}
        if term:
            params["term"] = term
        if career:
            params["x_acad_career"] = career
        data = self._get_json(DKU_OPTIONS_URL, params, "检查 Cookie")
        if not isinstance(data, dict):
            raise RuntimeError(f"检查 Cookie 失败：DKU 接口返回异常类型 {type(data)}。")
        return data

    def fetch_courses(self,
                      term: str = DEFAULT_TERM,
                      career: str = DEFAULT_CAREER,
                      enrl_stat: str = "",
                      subject: str = "",
                      catalog_nbr: str = "",
                      keyword: str = "",
                      days: str = "",
                      start_time_ge: str = "",
                      end_time_le: str = "",
                      session_code: str = "",
                      units: str = "") -> List[Dict]:
        all_courses = []
        total_pages = None
        page = 1
        while True:
            params = {
                "institution": "DKUNV",
                "term": term,
                "x_acad_career": career,
                "enrl_stat": enrl_stat,
                "subject": subject,
                "catalog_nbr": catalog_nbr,
                "keyword": keyword,
                "days": days,
                "start_time_ge": start_time_ge,
                "end_time_le": end_time_le,
                "session_code": session_code,
                "units": units,
                "crse_attr": "",
                "crse_attr_value": "",
                "page": str(page),
            }
            data = self._get_json(DKU_SEARCH_URL, params, "同步课程列表")
            if not isinstance(data, dict):
                raise RuntimeError(f"接口返回异常类型: {type(data)}")
            if total_pages is None:
                total_pages = int(data.get("pageCount", 1))
            page_courses = data.get("classes", [])
            if not page_courses:
                break
            all_courses.extend(page_courses)
            if total_pages and page >= total_pages:
                break
            page += 1
        return all_courses

    def fetch_course_detail(self, class_nbr: str, term: str = DEFAULT_TERM) -> Dict:
        params = {"institution": "DKUNV", "class_nbr": str(class_nbr), "term": term}
        data = self._get_json(DKU_DETAIL_URL, params, "获取课程详情")
        if not isinstance(data, dict):
            raise RuntimeError(f"详情接口返回异常类型: {type(data)}")
        return data

    def fetch_enrollment_class_details(self, class_nbr: str, term: str = DEFAULT_TERM,
                                       career: str = DEFAULT_CAREER) -> Dict:
        params = {
            "term": term,
            "classNbr": str(class_nbr),
            "acad_career": career,
            "institution": "DKUNV",
        }
        resp = self._session.post(
            DKU_ENROLL_DETAIL_URL,
            params=params,
            headers=self._enroll_headers(),
            timeout=self.timeout,
            data=b"",
        )
        if resp.status_code in {401, 403}:
            raise DKUCookieError("获取选课详情失败：DKU Cookie 可能已过期或 CSRF Token 失效。")
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        try:
            return resp.json()
        except ValueError:
            if "text/html" in content_type.lower() or "<html" in resp.text[:300].lower():
                raise DKUCookieError("获取选课详情失败：DKU 返回了登录页面，Cookie 可能已过期。")
            raise RuntimeError("获取选课详情失败：DKU 接口返回的不是 JSON 内容。")

    def fetch_drop_classes(self, term: str = DEFAULT_TERM,
                           career: str = DEFAULT_CAREER) -> List[Dict]:
        params = {"term": term, "acad_career": career}
        data = self._get_json(DKU_DROP_CLASSES_URL, params, "获取已选/候补课程")
        if not isinstance(data, dict):
            raise RuntimeError(f"已选/候补课程接口返回异常类型: {type(data)}")
        courses = data.get("courses", [])
        return courses if isinstance(courses, list) else []

    def fetch_schedule_terms(self) -> Dict:
        data = self._get_json(DKU_SCHEDULE_TERMS_URL, {}, "获取课表学期")
        if not isinstance(data, dict):
            raise RuntimeError(f"课表学期接口返回异常类型: {type(data)}")
        return data

    def fetch_schedule_by_term(self, term: str) -> Dict:
        data = self._get_json(DKU_SCHEDULE_BY_TERM_URL, {"x_term": term}, "获取学期课表")
        if not isinstance(data, dict):
            raise RuntimeError(f"学期课表接口返回异常类型: {type(data)}")
        return data

    def fetch_completed_course_codes(self, include_in_progress: bool = False) -> Dict:
        terms_data = self.fetch_schedule_terms()
        today = date.today()
        checked_terms = []
        course_codes = []
        for term in terms_data.get("student_class_terms", []) or []:
            if not isinstance(term, dict):
                continue
            term_code = str(term.get("strm", "")).strip()
            if not term_code:
                continue
            end_date = _parse_iso_date(str(term.get("end_dt", "")).strip())
            if not include_in_progress and end_date and end_date >= today:
                continue
            try:
                schedule = self.fetch_schedule_by_term(term_code)
            except Exception:
                continue  # 这个学期的API挂了（Cookie过期/接口关闭），跳过继续
            checked_terms.append({
                "strm": term_code,
                "descr": str(term.get("descr", "")).strip(),
                "end_dt": str(term.get("end_dt", "")).strip(),
            })
            for row in schedule.get("class_schedule", []) or []:
                if not isinstance(row, dict):
                    continue
                if str(row.get("stdnt_enrl_status", "")).strip().upper() not in {"E", "ENROLLED"}:
                    continue
                try:
                    units = float(row.get("units") or 0)
                except (TypeError, ValueError):
                    units = 0.0
                if units <= 0:
                    continue
                code = _course_code_from_schedule_row(row)
                if code and code not in course_codes:
                    course_codes.append(code)
        return {
            "course_codes": course_codes,
            "terms": checked_terms,
            "terms_checked": len(checked_terms),
        }


def _parse_iso_date(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _course_code_from_schedule_row(row: Dict) -> str:
    subject = str(row.get("subject", "") or "").strip().upper()
    catalog = str(row.get("catalog_nbr", "") or row.get("catalog_number", "") or "").strip().upper()
    if not subject or not catalog:
        return ""
    catalog = re.sub(r"\s+", "", catalog)
    return f"{subject} {catalog}"
