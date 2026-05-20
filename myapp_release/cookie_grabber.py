"""
DKU Cookie 自动获取工具
"""

import sys
import time
import os
from typing import Optional, Callable

COOKIE_DOMAIN = "dkuhub.dku.edu.cn"
DKUHUB_URL = f"https://{COOKIE_DOMAIN}/"
LOGGED_IN_HINT = f"{COOKIE_DOMAIN}/ps"
SHIB_HINT = "shib.oit.duke.edu"
KEY_COOKIE_NAMES = {
    "PS_TOKEN", "PS_TOKENEXPIRE", "PS_JSESSIONID",
    "CSRFCookie", "PS_LOGINLIST",
}

ProgressCallback = Callable[[str, str], None]


def _cookie_string(cookies: list) -> str:
    pairs = []
    for c in cookies:
        pairs.append(f"{c.get('name','')}={c.get('value','')}")
    return "; ".join(p for p in pairs if "=" in p)


def _dump_cookies(cookies: list) -> str:
    """生成所有 Cookie 的详细 dump 字符串"""
    lines = []
    for c in cookies:
        lines.append(
            f"  domain={c.get('domain','')} name={c.get('name','')} "
            f"path={c.get('path','')} httpOnly={c.get('httpOnly','')} "
            f"secure={c.get('secure','')} value={str(c.get('value',''))[:50]}"
        )
    return "\n".join(lines) if lines else "  (empty)"


def _grab_all_cookies(context, page) -> list:
    """
    用多种方式尝试获取所有 Cookie，返回合并后的列表。
    """
    all_cookies = []

    # 方式 1: context.cookies() 无参
    try:
        c1 = context.cookies()
        all_cookies.extend(c1)
    except Exception as e:
        print(f"[cookie method 1] {e}", file=sys.stderr)

    # 方式 2: context.cookies() 带 URL 过滤
    try:
        c2 = context.cookies([f"https://{COOKIE_DOMAIN}/"])
        for c in c2:
            if c not in all_cookies:
                all_cookies.append(c)
    except Exception as e:
        print(f"[cookie method 2] {e}", file=sys.stderr)

    # 方式 3: CDP Network.getCookies（可获取 HTTP-only Cookie）
    try:
        cdp = page.context.new_cdp_session(page)
        # 传 URL 列表以获取该域所有 Cookie
        for url_pattern in [
            f"https://{COOKIE_DOMAIN}/",
            f"https://{COOKIE_DOMAIN}/psc/",
            f"https://{COOKIE_DOMAIN}/psp/",
        ]:
            try:
                result = cdp.send("Network.getCookies", {"urls": [url_pattern]})
                for c in result.get("cookies", []):
                    c_norm = {
                        "name": c.get("name", ""),
                        "value": c.get("value", ""),
                        "domain": c.get("domain", ""),
                        "path": c.get("path", ""),
                        "httpOnly": c.get("httpOnly", False),
                        "secure": c.get("secure", False),
                    }
                    if c_norm not in all_cookies:
                        all_cookies.append(c_norm)
            except Exception as e:
                print(f"[CDP {url_pattern}] {e}", file=sys.stderr)
    except Exception as e:
        print(f"[CDP session] {e}", file=sys.stderr)

    # 方式 4: page.evaluate("document.cookie")
    try:
        doc_cookie = page.evaluate("document.cookie")
        for pair in doc_cookie.split(";"):
            pair = pair.strip()
            if "=" in pair:
                name, value = pair.split("=", 1)
                c = {
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": COOKIE_DOMAIN,
                    "path": "/",
                }
                if c not in all_cookies:
                    all_cookies.append(c)
    except Exception as e:
        print(f"[cookie method 4 JS] {e}", file=sys.stderr)

    return all_cookies


def _launch_browser(playwright, progress: ProgressCallback = None):
    def log(msg):
        if progress: progress("step", msg)
        print(msg)

    channels = ["msedge", "chrome", "chromium"]
    for channel in channels:
        try:
            log(f"尝试启动 {channel} ...")
            browser = playwright.chromium.launch(channel=channel, headless=False)
            log(f"已启动 {channel}")
            return browser, channel
        except Exception as e:
            log(f"{channel}: {e}")

    try:
        log("尝试 Playwright Chromium ...")
        browser = playwright.chromium.launch(headless=False)
        log("已启动 Chromium")
        return browser, "chromium"
    except Exception as e:
        log(f"Chromium: {e}")

    return None, None


def grab_cookie(timeout: int = 600,
                progress_callback: ProgressCallback = None) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        msg = "缺少 playwright，请运行: pip install playwright && playwright install chromium"
        if progress_callback: progress_callback("error", msg)
        return None

    def progress(stage: str, message: str):
        if progress_callback: progress_callback(stage, message)
        print(f"[{stage}] {message}")

    try:
        with sync_playwright() as p:
            browser, browser_name = _launch_browser(p, progress)
            if not browser:
                progress("error", "未找到可用浏览器")
                return None

            try:
                context = browser.new_context()
                page = context.new_page()

                # 导航
                progress("step", f"打开 {DKUHUB_URL}")
                goto_ok = False
                for attempt in range(3):
                    try:
                        page.goto(DKUHUB_URL, wait_until="commit", timeout=120000)
                        goto_ok = True
                        break
                    except Exception as e:
                        progress("info", f"导航 {attempt+1}/3: {e}")
                        time.sleep(3)
                if not goto_ok:
                    progress("error", "无法打开 dkuhub")
                    return None

                time.sleep(5)
                current_url = page.url

                logged_in = False
                if LOGGED_IN_HINT in current_url and SHIB_HINT not in current_url:
                    progress("info", "检测到已登录状态")
                    logged_in = True
                else:
                    progress("step", "请在浏览器窗口中登录 DKU Hub")
                    progress("step", f"Shibboleth 阶段最长 {timeout}s，到 dkuhub 后无限等")

                start_time = time.time()
                last_url = ""
                last_progress = ""
                arrived_at_dkuhub = False

                while not logged_in:
                    elapsed = time.time() - start_time
                    if not arrived_at_dkuhub and elapsed > timeout:
                        progress("error",
                                 f"Shibboleth 超时（{timeout}s）\n"
                                 f"当前 URL: {page.url}\n"
                                 f"Cookie dump:\n{_dump_cookies(_grab_all_cookies(context, page))}")
                        return None

                    time.sleep(3)
                    try:
                        current_url = page.url
                    except Exception:
                        continue

                    on_dkuhub = LOGGED_IN_HINT in current_url and SHIB_HINT not in current_url
                    if on_dkuhub and not arrived_at_dkuhub:
                        arrived_at_dkuhub = True
                        progress("info", f"到达 dkuhub（{elapsed:.0f}s），无限等待 PS_TOKEN...")

                    # 多种方式检查 PS_TOKEN
                    all_cookies = _grab_all_cookies(context, page)
                    dkuhub_cookies = [c for c in all_cookies
                                      if COOKIE_DOMAIN in (c.get("domain") or "")]
                    has_ps = any("PS_TOKEN" in (c.get("name") or "") for c in dkuhub_cookies)

                    if on_dkuhub and has_ps:
                        progress("info", f"PS_TOKEN 已获取！")
                        logged_in = True
                        break

                    if current_url != last_url:
                        last_url = current_url
                        if SHIB_HINT in current_url:
                            msg = f"Shibboleth 登录中..."
                        elif on_dkuhub:
                            n_cookies = len(dkuhub_cookies)
                            names = [c.get("name","") for c in dkuhub_cookies]
                            msg = f"dkuhub 上({n_cookies} cookie: {names})"
                        else:
                            msg = f"URL: {current_url[:100]}"
                        if msg != last_progress:
                            last_progress = msg
                            progress("info", msg)

                # 成功！等一等让 Cookie 稳定
                time.sleep(5)
                all_cookies = _grab_all_cookies(context, page)

                progress("info", f"共获取 {len(all_cookies)} 个 Cookie:\n{_dump_cookies(all_cookies)}")

                dkuhub_cookies = [c for c in all_cookies
                                  if COOKIE_DOMAIN in (c.get("domain") or "")
                                  and c.get("value")]
                # 返回所有 dkuhub 域下的 Cookie（和手动复制的一样）
                cookie_str = _cookie_string(dkuhub_cookies)

                if not cookie_str:
                    progress("error", f"Cookie 为空！全部 Cookie:\n{_dump_cookies(all_cookies)}")
                    return None

                progress("ok", f"成功！{len(dkuhub_cookies)} 个 Cookie，总长 {len(cookie_str)} 字符")
                return cookie_str

            finally:
                try: browser.close()
                except Exception: pass

    except Exception as e:
        import traceback
        progress("error", f"异常: {e}\n{traceback.format_exc()}")
        return None


def save_cookie_to_file(cookie_str: str, cookie_path: str) -> bool:
    try:
        os.makedirs(os.path.dirname(cookie_path) or ".", exist_ok=True)
        with open(cookie_path, "w", encoding="utf-8") as f:
            f.write(cookie_str)
        return True
    except Exception as e:
        print(f"保存失败: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--save", help="保存路径")
    args = parser.parse_args()

    cookie_str = grab_cookie(timeout=args.timeout)
    if cookie_str:
        save_path = args.save or os.path.join(os.path.dirname(os.path.abspath(__file__)), "dku_cookie.txt")
        if save_cookie_to_file(cookie_str, save_path):
            print(f"\nCookie 已保存到: {save_path}")
        else:
            print(f"Cookie 字符串: {cookie_str}")
    else:
        print("\n获取失败", file=sys.stderr)
        sys.exit(1)
