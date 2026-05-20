"""
DKU 毕业/报课要求 —— 硬编码版本
来源：ug_bulletin_2025-2026
"""

from typing import Any, Dict, List, Optional


# ── 课程负载规则 ──
COURSE_LOAD_RULES = {
    "term_min_credits": 16,
    "term_max_credits": 20,
    "session_min_credits": 8,
    "session_max_credits": 10,
    "first_year_s1_max_credits": 8,
    "pe_max_per_session": 1,
}

# ── 根据年级+学期计算 Class Of ──
# Fall term ends with "8", Spring term ends with "1"
# e.g. 2268 = 2026 Fall, 2261 = 2026 Spring
def compute_class_of(year_level: int, term: str) -> Optional[int]:
    if not year_level or not term:
        return None
    year = 2000 + int(term[1:3])  # "2268" → "26" → 2026
    if term.endswith("8"):  # Fall
        return year + 5 - year_level
    else:                   # Spring
        return year + 4 - year_level


# ── 身份分类 ──
IDENTITIES = {
    "chinese": {"label": "中国学生", "language": "eap"},
    "international-csl": {"label": "国际学生-CSL", "language": "csl"},
    "international-heritage": {"label": "国际学生-Heritage", "language": "heritage"},
    "international-other": {"label": "国际学生-其他语言课", "language": "third"},
}


# ── Common Core ──
COMMON_CORE = {
    1: {
        "course": "CCORE 101",
        "aliases": ["CCORE 101", "GCHINA 101"],
        "credits": 4,
        "name": "China in the World",
    },
    2: {
        "course": "CCORE 201",
        "aliases": ["CCORE 201", "GLOCHALL 201"],
        "credits": 4,
        "name": "Global Challenges in Science, Technology and Health",
    },
    3: {
        "course": "CCORE 202",
        "aliases": ["CCORE 202", "ETHLDR 201"],
        "credits": 4,
        "name": "Ethics, Citizenship and the Examined Life",
    },
}

# ── 语言课路线 ──
# EAP: 中国学生
EAP_SEQUENCE = {
    "fall_s1": "EAP 101A",
    "fall_s2": "EAP 101B",
    "spring_s1": "EAP 102A",
    "spring_s2": "EAP 102B",
    "total_credits": 8,
    "description": "EAP 路线：每 Session 一门",
}

# CSL: 国际生学中文
CSL_SEQUENCE = {
    "year1_fall": ["CHINESE 101A", "CHINESE 101B"],
    "year1_spring": ["CHINESE 102A", "CHINESE 102B"],
    "year2_fall": ["CHINESE 201A", "CHINESE 201B"],
    "year2_spring": ["CHINESE 202A", "CHINESE 202B"],
    "terminal": "CHINESE 202B",
    "total_credits": 16,
    "description": "CSL 路线：两年制（可能根据 placement 调整）",
}

# Heritage: 华裔学生
HERITAGE_SEQUENCE = {
    "fall": ["CHINESE 131A", "CHINESE 131B"],
    "spring": ["CHINESE 132A", "CHINESE 132B"],
    "total_credits": 8,
    "description": "Heritage 路线：Fall 131AB, Spring 132AB",
}

# 三外: 国际生不修中文
THIRD_LANGUAGE_REQUIREMENT = {
    "credits": 8,
    "description": "国际生：8 学分三外语",
}

# ── 大一写作课 ──
WRITING_REQUIREMENT = {
    "attr": "CURR-WRITING",
    "credits": 2,
    "year_level": 1,
    "description": "大一第一 Session (S1) 必修 2-credit writing course (W)",
}

# ── DKU 101 ──
DKU101_REQUIREMENT = {
    "course": "DKU 101",
    "credits": 0,
    "year_level": 1,
    "description": "大一第一 Session 必修 DKU 101（0 学分）",
}

# ── Capstone ──
CAPSTONE_REQUIREMENT = {
    "courses": ["CAPSTONE 495", "CAPSTONE 496"],
    "credits_each": 4,
    "total_credits": 8,
    "year_level": 4,
    "description": "大四两门 Capstone 课，共 8 学分",
}

# ── Mini-Term ──
MINITERM_REQUIREMENT = {
    "count": 1,
    "description": "四年内至少上一门 mini-term（春季两 session 之间）",
}

# ── 分布要求 ──
DISTRIBUTION_AREAS = [
    {"name": "Arts and Humanities", "attr": "DVSN-ARTHUM", "attr_code": "AH", "credits": 4},
    {"name": "Social Sciences", "attr": "DVSN-SOCSCI", "attr_code": "SS", "credits": 4},
    {"name": "Natural and Applied Sciences", "attr": "DVSN-NATSCI", "attr_code": "NS", "credits": 4},
]

# ── Quantitative Reasoning ──
QR_REQUIREMENT = {
    "attr_pattern": "QR",
    "credits": 4,
    "description": "4 学分 Quantitative Reasoning 课",
}

# ── PE 体育课（仅中国学生）─
PE_REQUIREMENT = {
    "subject": "PHYSEDU",
    "total_credits": 4,
    "credits_per_course": 0.5,
    "course_count": 8,
    "max_per_session": 1,
    "applies_to": "chinese",
    "description": "中国学生须修 8 门 0.5 学分体育课（共 4 学分）",
}

# ── Duke-Taught ──
DUKE_TAUGHT_REQUIREMENT = {
    "designation": "DINS",
    "total_credits": 34,
    "description": "至少 34 DKU 学分由 Duke 教授教（约 8.5 门）",
}

# ── 大一限制 ──
FIRST_YEAR_RESTRICTION = {
    "year_level": 1,
    "restricted_prefixes": ["3", "4"],
    "description": "大一不能上 300/400 level 课程",
}


# ── 获取当前学期必须考虑的语言课 ──
def get_language_courses_for_term(year_level: int, identity: str, term: str,
                                   is_first_term_s1: bool = False) -> List[str]:
    """返回当前学期应该上的语言课代码列表。"""
    is_fall = term.endswith("8")
    is_spring = term.endswith("1")
    courses = []

    lang = IDENTITIES.get(identity, {}).get("language", "")

    if lang == "eap":
        if year_level == 1:
            if is_fall:
                courses = [EAP_SEQUENCE["fall_s1"], EAP_SEQUENCE["fall_s2"]]
            elif is_spring:
                courses = [EAP_SEQUENCE["spring_s1"], EAP_SEQUENCE["spring_s2"]]
    elif lang == "csl":
        if year_level == 1:
            if is_fall:
                courses = CSL_SEQUENCE["year1_fall"]
            elif is_spring:
                courses = CSL_SEQUENCE["year1_spring"]
        elif year_level == 2:
            if is_fall:
                courses = CSL_SEQUENCE["year2_fall"]
            elif is_spring:
                courses = CSL_SEQUENCE["year2_spring"]
    elif lang == "heritage":
        if year_level in (1, 2):
            if is_fall:
                courses = HERITAGE_SEQUENCE["fall"]
            elif is_spring:
                courses = HERITAGE_SEQUENCE["spring"]
    elif lang == "third":
        # 无特定课程，只要求 8 学分，由用户手动添加
        pass

    return courses


# ── 返回毕业要求的人类可读摘要 ──
def get_graduation_summary() -> str:
    return """毕业要求摘要（136 学分/国际生，158 学分/中国生）：

1. Common Core ×3：CCORE 101/GCHINA 101 (Y1), CCORE 201/GLOCHALL 201 (Y2), CCORE 202/ETHLDR 201 (Y3)
2. DKU 101：大一第一 Session，0 学分
3. 写作课 (W)：大一第一 Session，2 学分
4. 语言课：EAP 101AB→102AB / CSL 101AB→202AB / Heritage 132AB / 三外 8 学分
5. 定量推理 (QR)：4 学分
6. 分布要求：AH / SS / NS 各 4 学分
7. Mini-Term：至少 1 门（春季）
8. PE：中国学生 8 门 × 0.5 学分
9. Capstone：大四 CAPSTONE 495 + 496，共 8 学分
10. Duke-Taught：34 DKU 学分
11. 大一不能上 300/400 level 课
12. 正常负载：16-20 学分/学期，8-10 学分/Session；大一 S1 上限 8 学分"""


def get_degree_summary_rules() -> List[Dict[str, Any]]:
    """返回毕业要求列表，供前端显示。"""
    rules = [
        {"id": "miniterm", "label": "Mini-Term", "total": "1", "unit": "门"},
        {"id": "pe", "label": "体育课 (PE)", "total": "4", "unit": "units", "identity": "chinese"},
    ]
    return rules
