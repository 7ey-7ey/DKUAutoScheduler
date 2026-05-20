import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "databases")
COOKIE_PATH = os.path.join(BASE_DIR, "dku_cookie.txt")

DKU_BASE = "https://dkuhub.dku.edu.cn/psc/CSPRD01/EMPLOYEE/SA/s/WEBLIB_HCX_CM.H_CLASS_SEARCH.FieldFormula.IScript"
DKU_ENROLL_BASE = "https://dkuhub.dku.edu.cn/psc/CSPRD01/EMPLOYEE/SA/s/WEBLIB_HCX_EN.H_SHOPPING_CART.FieldFormula.IScript"
DKU_DROP_CLASSES_URL = "https://dkuhub.dku.edu.cn/psc/CSPRD01/EMPLOYEE/SA/s/WEBLIB_HCX_EN.H_DROP_CLASSES.FieldFormula.IScript_List"
DKU_SCHEDULE_BASE = "https://dkuhub.dku.edu.cn/psc/CSPRD01/EMPLOYEE/SA/s/WEBLIB_HCX_EN.H_SCHEDULE.FieldFormula.IScript"
DKU_SEARCH_URL = f"{DKU_BASE}_ClassSearch"
DKU_DETAIL_URL = f"{DKU_BASE}_ClassDetails"
DKU_OPTIONS_URL = f"{DKU_BASE}_ClassSearchOptions"
DKU_ENROLL_DETAIL_URL = f"{DKU_ENROLL_BASE}_ClassDetails"
DKU_SCHEDULE_TERMS_URL = f"{DKU_SCHEDULE_BASE}_ScheduleTerms"
DKU_SCHEDULE_BY_TERM_URL = f"{DKU_SCHEDULE_BASE}_ScheduleByTerm"

DEFAULT_TERM = "2268"
DEFAULT_CAREER = "UGRD"
