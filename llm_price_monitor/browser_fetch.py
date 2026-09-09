"""无头浏览器网页采集：打开网页前注入 cookies 和 localStorage 登录态，渲染后返回 HTML。

仅在站点配置启用 network.headless 时使用；playwright 延迟导入，未安装不影响其他采集路径。
"""
import json
from urllib.parse import urlsplit


def fetch_page_html(url: str, headless_config: dict) -> str:
    """用 headless Chromium 打开 url，注入登录态后返回渲染后的 HTML。失败抛 RuntimeError。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("服务器还没装无头浏览器组件，请在部署环境执行 pip install playwright && playwright install chromium") from exc

    parsed = urlsplit(url)
    host = parsed.netloc.rsplit("@", 1)[-1].split(":", 1)[0]
    cookies = [
        {"name": item["name"], "value": item["value"], "domain": host, "path": "/"}
        for item in headless_config.get("cookies") or []
    ]
    local_storage = headless_config.get("localStorage") or {}
    wait_seconds = float(headless_config.get("wait_seconds", 3))

    playwright = None
    browser = None
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        if cookies:
            context.add_cookies(cookies)
        if local_storage:
            # 用 JSON 序列化一次性写入，避免手动拼接字符串的转义问题
            payload = json.dumps(local_storage, ensure_ascii=False)
            context.add_init_script(
                f"(() => {{ const entries = {payload}; for (const [key, value] of Object.entries(entries)) "
                f"window.localStorage.setItem(key, String(value)); }})();"
            )
        page = context.new_page()
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(int(wait_seconds * 1000))
        return page.content()
    except Exception as exc:
        raise RuntimeError(f"无头浏览器采集 {url} 失败：{exc}") from exc
    finally:
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()
