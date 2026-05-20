from typing import Dict, List
from dku_client import DKUApiClient
from utils import has_cookie
from config import DEFAULT_TERM, DEFAULT_CAREER


class OptionsCache:
    _global_terms = []
    _global_careers = []
    _seed_options = {}
    _seed_attempted = False
    _options_by_term_career = {}

    @classmethod
    def clear(cls):
        cls._global_terms = []
        cls._global_careers = []
        cls._seed_options = {}
        cls._seed_attempted = False
        cls._options_by_term_career = {}

    @staticmethod
    def _key(term: str, career: str):
        return (term or DEFAULT_TERM, career or DEFAULT_CAREER)

    @classmethod
    def _load_seed_options(cls):
        if cls._seed_options or cls._seed_attempted:
            return
        cls._seed_attempted = True
        if not has_cookie():
            return
        try:
            client = DKUApiClient()
            # First try the broad request. If DKU needs a seed, fall back to
            # the known default term/career.
            data = client.fetch_search_options(term=None, career=None)
            if not data.get("terms") or not data.get("careers"):
                data = client.fetch_search_options(term=DEFAULT_TERM, career=DEFAULT_CAREER)
            cls._seed_options = data
            cls._global_terms = data.get("terms", [])
            cls._global_careers = data.get("careers", [])
        except Exception as e:
            print(f"加载全局学期/学制列表失败: {e}")
            try:
                client = DKUApiClient()
                data = client.fetch_search_options(term=DEFAULT_TERM, career=DEFAULT_CAREER)
                cls._seed_options = data
                cls._global_terms = data.get("terms", [])
                cls._global_careers = data.get("careers", [])
            except Exception as fallback_error:
                print(f"使用默认引子加载 options 失败: {fallback_error}")

    @classmethod
    def load(cls, term: str = DEFAULT_TERM, career: str = DEFAULT_CAREER):
        key = cls._key(term, career)
        if key in cls._options_by_term_career:
            return
        if not has_cookie():
            cls._options_by_term_career[key] = {}
            return
        try:
            client = DKUApiClient()
            cls._options_by_term_career[key] = client.fetch_search_options(term=key[0], career=key[1])
        except Exception as e:
            print(f"加载 options (term={key[0]}, career={key[1]}) 失败: {e}")
            cls._options_by_term_career[key] = {}

    @classmethod
    def get_terms(cls) -> List[Dict]:
        cls._load_seed_options()
        return cls._global_terms

    @classmethod
    def get_careers(cls) -> List[Dict]:
        cls._load_seed_options()
        return cls._global_careers

    @classmethod
    def get_subjects(cls, term: str = DEFAULT_TERM, career: str = DEFAULT_CAREER) -> List[Dict]:
        cls.load(term, career)
        return cls._options_by_term_career.get(cls._key(term, career), {}).get("subjects", [])

    @classmethod
    def get_sessions(cls, term: str = DEFAULT_TERM, career: str = DEFAULT_CAREER) -> List[Dict]:
        cls.load(term, career)
        return cls._options_by_term_career.get(cls._key(term, career), {}).get("sessions", [])
