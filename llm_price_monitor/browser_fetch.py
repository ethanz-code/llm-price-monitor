"""无头浏览器网页采集：打开网页前注入 cookies 和 localStorage 登录态，渲染后返回 HTML。

仅在站点配置启用 network.headless 时使用；playwright 延迟导入，未安装不影响其他采集路径。
"""
import json
from urllib.parse import urlsplit

from .config import PriceMonitorError


def fetch_page_html(
    url: str,
    headless_config: dict,
    user_agent: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> str:
    """用 headless Chromium 打开 url，注入登录态和自定义请求头后返回渲染后的 HTML。

    失败抛 PriceMonitorError。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PriceMonitorError("服务器还没装无头浏览器组件，请在部署环境执行 pip install playwright && playwright install chromium") from exc

    from .adapters import expand_header_value

    parsed = urlsplit(url)
    host = parsed.netloc.rsplit("@", 1)[-1].split(":", 1)[0]
    cookies = [
        {"name": item["name"], "value": expand_header_value(item["value"]), "domain": host, "path": "/"}
        for item in headless_config.get("cookies") or []
    ]
    local_storage = {
        key: expand_header_value(str(value))
        for key, value in (headless_config.get("localStorage") or {}).items()
    }
    wait_seconds = float(headless_config.get("wait_seconds", 3))

    playwright = None
    browser = None
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        # UA 与 HTTP 采集路径保持一致，避免同一站点两条链路指纹不一致触发风控
        context = browser.new_context(user_agent=user_agent) if user_agent else browser.new_context()
        if extra_headers:
            # 浏览器自管的头不透传，避免与 context 自身设置冲突
            context.set_extra_http_headers({
                name: value
                for name, value in extra_headers.items()
                if name.casefold() not in {"host", "content-length", "content-type", "cookie", "user-agent"}
            })
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
        raise PriceMonitorError(f"无头浏览器采集 {url} 失败：{exc}") from exc
    finally:
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()
