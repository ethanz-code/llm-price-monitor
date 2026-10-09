"""Headless网页采集：打开网页前注入 cookies 和 localStorage 登录态，渲染后返回 HTML。

仅在站点配置启用 network.headless 时使用；playwright 延迟导入，未安装不影响其他采集路径。
"""
import json
from urllib.parse import unquote, urlsplit

from .config import ACCESS_TOKEN_VAR, REFRESH_TOKEN_VAR, PriceMonitorError, SiteSpec
from . import egress


def _expand_login_value(value: str, *, auth_token: str, refresh_token: str) -> str | None:
    """登录态值的凭证占位符展开：${access_token}/${refresh_token} → 当前凭证值。

    引用 ${access_token} 但还没拿到 token 时返回 None（该条跳过，等续签补上后下次采集自然带上）；
    引用 ${refresh_token} 但没有 Refresh Token 明确报错，不静默发空值。
    """
    if ACCESS_TOKEN_VAR in value and not auth_token:
        return None
    if REFRESH_TOKEN_VAR in value and not refresh_token:
        raise PriceMonitorError(
            f"登录态值引用了 {REFRESH_TOKEN_VAR}，但当前认证方式没有 Refresh Token（只有「登录会话自动续签」才有）"
        )
    return value.replace(ACCESS_TOKEN_VAR, auth_token).replace(REFRESH_TOKEN_VAR, refresh_token)


def _chromium_proxy(proxy_url: str) -> dict[str, str]:
    """把代理地址转成 Playwright 的 proxy 形态：认证不写在 server 里，
    拆成独立 username/password（支持 http://user:pass@host:port 写法，值按 URL 解码）。"""
    parsed = urlsplit(proxy_url)
    proxy = {"server": f"{parsed.scheme}://{parsed.netloc.rsplit('@', 1)[-1]}"}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


def fetch_page_html(
    url: str, headless_config: dict, user_agent: str | None = None, *, spec: SiteSpec | None = None
) -> str:
    """用 headless Chromium 打开 url，注入 cookies 和 localStorage 登录态后返回渲染后的 HTML。

    请求头一律由浏览器机制自行携带，不做特殊注入。cookies 和 localStorage 的值都支持
    ${access_token}/${refresh_token} 引用「认证与续签」的当前凭证（续签换新后下次采集自动带上），
    其余文本原样保留；引用了 ${access_token} 但还没拿到 token 时该条跳过不写入，等续签补上。
    spec 是可选的站点配置，不传时凭证占位符按"无凭证"处理。失败抛 PriceMonitorError。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PriceMonitorError("服务器还没装Headless组件，请在部署环境执行 pip install playwright && playwright install chromium") from exc

    parsed = urlsplit(url)
    host = parsed.netloc.rsplit("@", 1)[-1].split(":", 1)[0]
    auth_token = (spec.auth_token or "") if spec is not None else ""
    refresh_token = str((spec.token_refresh or {}).get("refresh_token") or "") if spec is not None else ""
    cookies = []
    for item in headless_config.get("cookies") or []:
        value = _expand_login_value(str(item["value"]), auth_token=auth_token, refresh_token=refresh_token)
        if value is None:
            continue
        cookies.append({"name": item["name"], "value": value, "domain": host, "path": "/"})
    local_storage = {}
    for key, raw in (headless_config.get("localStorage") or {}).items():
        value = _expand_login_value(str(raw), auth_token=auth_token, refresh_token=refresh_token)
        if value is not None:
            local_storage[str(key)] = value
    wait_seconds = float(headless_config.get("wait_seconds", 3))

    playwright = None
    browser = None
    try:
        playwright = sync_playwright().start()
        # 出口跟随直连失败记忆（只读不写）：被墙站的无头渲染直接带代理，不再白撞一次墙；
        # 失败标记由 HTTP 直采链路维护，两条链路对同一域名的出口选择保持一致
        proxy_server = egress.fallback_proxy() if egress.plan(host).use_proxy else None
        browser = playwright.chromium.launch(
            headless=True, proxy=_chromium_proxy(proxy_server) if proxy_server else None
        )
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
