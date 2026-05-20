import os
from glob import glob
from typing import Optional

from config import DATABASE_DIR, DEFAULT_CAREER, DEFAULT_TERM


def get_db_path(db_file: Optional[str] = None) -> Optional[str]:
    if not db_file:
        return None
    safe_name = os.path.basename(db_file)
    if not safe_name.endswith(".db"):
        return None
    return os.path.join(DATABASE_DIR, safe_name)


def get_existing_db_path(db_file: Optional[str] = None) -> Optional[str]:
    db_path = get_db_path(db_file)
    if db_path and os.path.exists(db_path):
        return db_path
    return None


def get_db_files():
    db_files = [os.path.basename(path) for path in glob(os.path.join(DATABASE_DIR, "*.db"))]
    return sorted(db_files)


def parse_term_short_label(term: str) -> str:
    term = str(term or "").strip()
    season_by_code = {
        "1": "SPRG",
        "5": "SUMR",
        "8": "FALL",
    }
    if len(term) < 3:
        return ""
    year = term[-3:-1]
    season = season_by_code.get(term[-1], "")
    if not year.isdigit() or not season:
        return ""
    return f"{year}{season}"


def build_course_db_filename(career: str, term: str, date_str: str) -> str:
    parts = [str(career or DEFAULT_CAREER).strip(), str(term or DEFAULT_TERM).strip()]
    term_label = parse_term_short_label(term)
    if term_label:
        parts.append(term_label)
    parts.append(date_str)
    return f"{'-'.join(parts)}.db"


def remove_existing_db_files(db_path: str):
    base_dir = os.path.abspath(DATABASE_DIR)
    target = os.path.abspath(db_path)
    if os.path.commonpath([base_dir, target]) != base_dir:
        raise RuntimeError("数据库路径不在项目目录内，已停止删除。")
    for path in [target, f"{target}-wal", f"{target}-shm", f"{target}-journal"]:
        if os.path.exists(path):
            os.remove(path)
