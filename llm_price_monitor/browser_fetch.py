"""Headless网页采集：打开网页前注入 cookies 和 localStorage 登录态，渲染后返回 HTML。

仅在站点配置启用 network.headless 时使用；playwright 延迟导入，未安装不影响其他采集路径。
"""
import json
from urllib.parse import urlsplit

from .config import ACCESS_TOKEN_VAR, REFRESH_TOKEN_VAR, PriceMonitorError, SiteSpec


def fetch_page_html(
    url: str, headless_config: dict, user_agent: str | None = None, *, spec: SiteSpec | None = None
) -> str:
    """用 headless Chromium 打开 url，注入 cookies 和 localStorage 登录态后返回渲染后的 HTML。

    请求头一律由浏览器机制自行携带，不做特殊注入。localStorage 值支持 ${access_token}/
    ${refresh_token} 引用「认证与续签」的当前凭证（续签换新后下次采集自动带上）；
    引用了 ${access_token} 但还没拿到 token 时该条跳过不写入，等续签补上。
    spec 是可选的站点配置，不传时凭证占位符按"无凭证"处理。失败抛 PriceMonitorError。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PriceMonitorError("服务器还没装Headless组件，请在部署环境执行 pip install playwright && playwright install chromium") from exc

    from .adapters import expand_header_value

    parsed = urlsplit(url)
    host = parsed.netloc.rsplit("@", 1)[-1].split(":", 1)[0]
    cookies = [
        {"name": item["name"], "value": expand_header_value(item["value"]), "domain": host, "path": "/"}
        for item in headless_config.get("cookies") or []
    ]
    auth_token = (spec.auth_token or "") if spec is not None else ""
    refresh_token = str((spec.token_refresh or {}).get("refresh_token") or "") if spec is not None else ""
    local_storage = {}
    for key, raw in (headless_config.get("localStorage") or {}).items():
        value = str(raw)
        if ACCESS_TOKEN_VAR in value and not auth_token:
            # 与请求头的引导链一致：还没拿到 Access Token 就先不写这条，等续签补上后下次采集自然带上
            continue
        if REFRESH_TOKEN_VAR in value and not refresh_token:
            raise PriceMonitorError(
                "localStorage 值引用了 ${refresh_token}，但当前认证方式没有 Refresh Token（只有「登录会话自动续签」才有）"
            )
        # 展开顺序与请求头一致：先凭证占位符，再环境变量，反了会被当成未设置的环境变量报错
        value = value.replace(ACCESS_TOKEN_VAR, auth_token).replace(REFRESH_TOKEN_VAR, refresh_token)
        local_storage[str(key)] = expand_header_value(value)
    wait_seconds = float(headless_config.get("wait_seconds", 3))

    playwright = None
    browser = None
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        # UA 与 HTTP 采集路径保持一致，避免同一站点两条链路指纹不一致触发风控
        context = browser.new_context(user_agent=user_agent) if user_agent else browser.new_context()
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
        raise PriceMonitorError(f"Headless采集 {url} 失败：{exc}") from exc
    finally:
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()
