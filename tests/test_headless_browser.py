"""network.headless 网页模式配置校验与采集分支测试；不真启动浏览器，全部用 monkeypatch 替身。"""
import httpx
import pytest

from llm_price_monitor.config import ModelTarget, sites_from_raw
from llm_price_monitor.tracker import fetch_price


def _from_raw(values):
    """站点级 models 已废弃：目标模型经通用清单参数注入。"""
    return sites_from_raw(values, models=(ModelTarget("gpt-5.6-luna"),))

def _site(network: dict) -> dict:
    return {"id": "demo", "network": network}


def test_headless_config_valid_passes_validation():
    (spec,) = _from_raw([
        _site({
            "url": "https://demo.test/pricing",
            "headless": {
                "enabled": True,
                "cookies": [{"name": "session", "value": "abc"}],
                "localStorage": {"token": "xxx"},
                "wait_seconds": 5,
            },
        })
    ])
    assert spec.network["headless"]["enabled"] is True


def test_headless_absent_or_disabled_keeps_other_fields_unchecked():
    # 未启用时只校验 enabled，其余字段即使畸形也不报错
    (spec,) = _from_raw([
        _site({"url": "https://demo.test/pricing", "headless": {"enabled": False, "cookies": "oops", "wait_seconds": 999}})
    ])
    assert spec.network["headless"]["enabled"] is False
    (bare,) = _from_raw([_site({"url": "https://demo.test/pricing"})])
    assert "headless" not in bare.network


def test_headless_enabled_must_be_bool():
    with pytest.raises(ValueError, match="headless.enabled 必须是布尔值"):
        _from_raw([_site({"url": "https://demo.test/pricing", "headless": {"enabled": "yes"}})])


def test_headless_cookie_missing_value_rejected():
    with pytest.raises(ValueError, match=r"headless\.cookies\[0\]\.value 必须是非空字符串"):
        _from_raw([_site({"url": "https://demo.test/pricing", "headless": {"enabled": True, "cookies": [{"name": "session"}]}})])


def test_headless_local_storage_must_be_string_dict():
    with pytest.raises(ValueError, match="headless.localStorage 必须是字符串键值对象"):
        _from_raw([_site({"url": "https://demo.test/pricing", "headless": {"enabled": True, "localStorage": {"token": 123}}})])


def test_headless_wait_seconds_out_of_range_rejected():
    with pytest.raises(ValueError, match="wait_seconds 必须是 0~60 之间的数字"):
        _from_raw([_site({"url": "https://demo.test/pricing", "headless": {"enabled": True, "wait_seconds": 61}})])
    with pytest.raises(ValueError, match="wait_seconds 必须是 0~60 之间的数字"):
        _from_raw([_site({"url": "https://demo.test/pricing", "headless": {"enabled": True, "wait_seconds": -1}})])


def test_fetch_price_uses_browser_when_headless_enabled(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_fetch_page_html(url: str, headless_config: dict) -> str:
        calls.append((url, headless_config))
        return "<html><body>gpt-5.6-luna 输入价格 ¥3.3 / 1M tokens，输出价格 ¥9.9 / 1M tokens</body></html>"

    monkeypatch.setattr("llm_price_monitor.tracker._fetch_page_via_browser", fake_fetch_page_html)
    record = fetch_price(
        "https://demo.test/pricing",
        "gpt-5.6-luna",
        network={"headless": {"enabled": True, "cookies": [{"name": "session", "value": "abc"}], "wait_seconds": 3}},
    )
    assert calls == [("https://demo.test/pricing", {"enabled": True, "cookies": [{"name": "session", "value": "abc"}], "wait_seconds": 3})]
    assert record.input_price == 3.3 and record.output_price == 9.9


def test_fetch_price_uses_httpx_when_headless_disabled(monkeypatch):
    def fail_fetch(url: str, headless_config: dict, user_agent: str | None = None) -> str:
        raise AssertionError("未启用 headless 时不应走浏览器分支")

    monkeypatch.setattr("llm_price_monitor.tracker._fetch_page_via_browser", fail_fetch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="gpt-5.6-luna 输入价格 ¥1.1 / 1M tokens，输出价格 ¥2.2 / 1M tokens")

    record = fetch_price("https://demo.test/pricing", "gpt-5.6-luna", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert record.input_price == 1.1 and record.output_price == 2.2


def test_fetch_price_browser_failure_raises_like_collect_failure(monkeypatch):
    def fail_fetch(url: str, headless_config: dict) -> str:
        raise RuntimeError("Headless采集失败：超时")

    monkeypatch.setattr("llm_price_monitor.tracker._fetch_page_via_browser", fail_fetch)
    with pytest.raises(RuntimeError, match="Headless采集失败"):
        fetch_price("https://demo.test/pricing", "gpt-5.6-luna", network={"headless": {"enabled": True}})


def test_browser_fetch_is_importable_without_playwright():
    # 懒加载：即便环境没装 playwright，模块导入也必须成功
    import llm_price_monitor.browser_fetch  # noqa: F401


# ---------- 生产采集路径：NetworkAdapter.collect ----------

def _spec(headless: dict | None):
    network = {"url": "https://demo.test/pricing"}
    if headless is not None:
        network["headless"] = headless
    (spec,) = _from_raw([_site(network)])
    return spec


_PAGE_HTML = '<html><body>{category:"text",models:["gpt-5.6-luna"],provider:"demo",input:3.3,output:9.9}</body></html>'


def test_network_adapter_uses_browser_when_headless_enabled(monkeypatch):
    from llm_price_monitor.adapters import NetworkAdapter

    calls: list[tuple[str, dict | None]] = []

    def fake_fetch_page_html(
        url: str, headless_config: dict, user_agent: str | None = None, *, spec=None
    ) -> str:
        calls.append((url, spec.id if spec is not None else None))
        assert user_agent == "test-ua"  # UA 应与 HTTP 采集路径保持一致传入浏览器
        return _PAGE_HTML

    monkeypatch.setattr("llm_price_monitor.browser_fetch.fetch_page_html", fake_fetch_page_html)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"启用 headless 时不应走 httpx 请求：{request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        records = NetworkAdapter().collect(_spec({"enabled": True}), client, 20.0, "test-ua")
    # 浏览器链路不做请求头注入，登录态只走 cookies/localStorage；站点配置要传入，
    # localStorage 值里的 ${access_token} 等凭证占位符靠它展开
    assert calls == [("https://demo.test/pricing", "demo")]
    record = next(item for item in records if item.model == "gpt-5.6-luna")
    assert record.input_price == 3.3 and record.output_price == 9.9


def test_network_adapter_keeps_httpx_when_headless_disabled(monkeypatch):
    from llm_price_monitor.adapters import NetworkAdapter

    def fail_fetch(url: str, headless_config: dict, user_agent: str | None = None, *, spec=None) -> str:
        raise AssertionError("未启用 headless 时不应走浏览器分支")

    monkeypatch.setattr("llm_price_monitor.browser_fetch.fetch_page_html", fail_fetch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_PAGE_HTML)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        records = NetworkAdapter().collect(_spec(None), client, 20.0, "test-ua")
    record = next(item for item in records if item.model == "gpt-5.6-luna")
    assert record.input_price == 3.3 and record.output_price == 9.9


def test_network_adapter_html_shell_fails_without_auto_headless(monkeypatch):
    from llm_price_monitor.adapters import NetworkAdapter
    from llm_price_monitor.config import PriceMonitorError

    # 接口直采抓到空壳页就按失败上报，不再自动换Headless重试；
    # 需要渲染的站点必须显式切「网页模式」
    def fail_fetch(url: str, headless_config: dict, user_agent: str | None = None, *, spec=None) -> str:
        raise AssertionError("未启用 headless 时不应走浏览器分支")

    monkeypatch.setattr("llm_price_monitor.browser_fetch.fetch_page_html", fail_fetch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='<html><body><div id="root"></div></body></html>')

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(PriceMonitorError):
        NetworkAdapter().collect(_spec(None), client, 20.0, "test-ua")


# ---------- 倍率接口独立请求头（ratio_url 对象形态） ----------

def _ratio_site(ratio: dict | str):
    network = {"url": "https://demo.test/pricing", "ratio_url": ratio}
    (spec,) = _from_raw([_site(network)])
    return spec


_RATIO_PAGE = '<html><body>{category:"text",models:["gpt-5.6-luna"],provider:"demo",input:3.3,output:9.9}</body></html>'
def _ratio_json() -> httpx.Response:
    return httpx.Response(200, json={"pricing": [{"provider": "demo", "rate": 0.5}]})


def test_ratio_url_object_with_headers(monkeypatch):
    from llm_price_monitor.adapters import NetworkAdapter

    monkeypatch.setattr("llm_price_monitor.browser_fetch.fetch_page_html", lambda url, cfg, user_agent=None, spec=None: _RATIO_PAGE)
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ratio.test":
            seen.append(request.headers)
            return _ratio_json()
        return httpx.Response(200, text=_RATIO_PAGE)

    ratio = {"url": "https://ratio.test/api/rate", "headers": {"X-Rate-Key": "secret"}}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        records = NetworkAdapter().collect(_ratio_site(ratio), client, 20.0, "test-ua")
    assert seen[0]["x-rate-key"] == "secret"
    priced = [item for item in records if item.model == "gpt-5.6-luna" and item.price_status == "confirmed"]
    assert priced and priced[0].output_price == 9.9 * 0.5


def test_ratio_url_string_still_works(monkeypatch):
    from llm_price_monitor.adapters import NetworkAdapter

    monkeypatch.setattr("llm_price_monitor.browser_fetch.fetch_page_html", lambda url, cfg, user_agent=None, spec=None: _RATIO_PAGE)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ratio.test":
            return _ratio_json()
        return httpx.Response(200, text=_RATIO_PAGE)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        records = NetworkAdapter().collect(_ratio_site("https://ratio.test/api/rate"), client, 20.0, "test-ua")
    priced = [item for item in records if item.model == "gpt-5.6-luna" and item.price_status == "confirmed"]
    assert priced and priced[0].output_price == 9.9 * 0.5


def test_ratio_url_headers_sent_literally():
    from llm_price_monitor.adapters import NetworkAdapter

    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ratio.test":
            seen.append(request.headers)
            return _ratio_json()
        return httpx.Response(200, text=_RATIO_PAGE)

    ratio = {"url": "https://ratio.test/api/rate", "headers": {"X-Rate-Key": "rk-9"}}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        NetworkAdapter().collect(_ratio_site(ratio), client, 20.0, "test-ua")
    assert seen[0]["x-rate-key"] == "rk-9"


# ---------- browser_fetch：异常类型、UA 透传与凭证占位符 ----------

def _fake_playwright(monkeypatch, captured: dict, *, fail: bool = False):
    import sys
    import types

    class FakePage:
        def goto(self, *args, **kwargs): ...
        def wait_for_timeout(self, ms): ...
        def content(self): return "<html>ok</html>"

    class FakeContext:
        def add_cookies(self, cookies): captured["cookies"] = cookies
        def add_init_script(self, script): captured["script"] = script
        def new_page(self): return FakePage()

    class FakeBrowser:
        def new_context(self, user_agent=None):
            captured["ua"] = user_agent
            return FakeContext()
        def close(self): ...

    class FakeChromium:
        chromium = types.SimpleNamespace(launch=lambda headless: FakeBrowser())
        def stop(self): ...

    class FakePlaywright:
        def start(self):
            if fail:
                raise OSError("chromium 启动失败")
            return FakeChromium()
        def stop(self): ...

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = FakePlaywright
    playwright_mod = types.ModuleType("playwright")
    playwright_mod.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", playwright_mod)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)


def test_fetch_page_html_wraps_failure_as_price_monitor_error(monkeypatch):
    from llm_price_monitor.browser_fetch import fetch_page_html
    from llm_price_monitor.config import PriceMonitorError

    _fake_playwright(monkeypatch, {}, fail=True)
    with pytest.raises(PriceMonitorError, match="Headless采集"):
        fetch_page_html("https://demo.test/pricing", {"enabled": True})


def test_fetch_page_html_literal_values_and_user_agent(monkeypatch):
    from llm_price_monitor.browser_fetch import fetch_page_html

    captured: dict = {}
    _fake_playwright(monkeypatch, captured)
    # 环境变量注入已移除：值里除凭证占位符外的文本原样透传，不再做环境变量替换
    fetch_page_html(
        "https://demo.test/pricing",
        {
            "enabled": True,
            "cookies": [{"name": "session", "value": "${TEST_HEADLESS_TOKEN}"}],
            "localStorage": {"token": "${TEST_HEADLESS_TOKEN}"},
        },
        "ua-1",
    )
    assert captured["cookies"][0]["value"] == "${TEST_HEADLESS_TOKEN}"
    assert '"${TEST_HEADLESS_TOKEN}"' in captured["script"]
    assert captured["ua"] == "ua-1"


def test_local_storage_expands_access_token_placeholder(monkeypatch):
    from llm_price_monitor.browser_fetch import fetch_page_html
    from llm_price_monitor.config import SiteSpec

    captured: dict = {}
    _fake_playwright(monkeypatch, captured)
    spec = SiteSpec(id="demo", auth_token="tk-9")
    fetch_page_html(
        "https://demo.test/pricing",
        {"enabled": True, "localStorage": {"token": "Bearer ${access_token}", "note": "static"}},
        spec=spec,
    )
    assert '"Bearer tk-9"' in captured["script"]
    assert '"static"' in captured["script"]


def test_local_storage_access_token_placeholder_skipped_without_token(monkeypatch):
    from llm_price_monitor.browser_fetch import fetch_page_html

    captured: dict = {}
    _fake_playwright(monkeypatch, captured)
    # 还没拿到 Access Token（新站点或刚清空）：引用它的条目不写入，等续签补上后下次采集自然带上
    fetch_page_html(
        "https://demo.test/pricing",
        {"enabled": True, "localStorage": {"token": "${access_token}", "note": "static"}},
    )
    assert '"token"' not in captured["script"]
    assert '"static"' in captured["script"]


def test_local_storage_refresh_token_placeholder_expands_and_reports_missing(monkeypatch):
    from llm_price_monitor.browser_fetch import fetch_page_html
    from llm_price_monitor.config import PriceMonitorError, SiteSpec

    captured: dict = {}
    _fake_playwright(monkeypatch, captured)
    spec = SiteSpec(id="demo", token_refresh={"refresh_token": "rf-1"})
    fetch_page_html(
        "https://demo.test/pricing",
        {"enabled": True, "localStorage": {"refresh": "${refresh_token}"}},
        spec=spec,
    )
    assert '"rf-1"' in captured["script"]

    with pytest.raises(PriceMonitorError, match="没有 Refresh Token"):
        fetch_page_html(
            "https://demo.test/pricing",
            {"enabled": True, "localStorage": {"refresh": "${refresh_token}"}},
            spec=SiteSpec(id="demo"),
        )


def test_cookie_expands_access_token_placeholder(monkeypatch):
    from llm_price_monitor.browser_fetch import fetch_page_html
    from llm_price_monitor.config import SiteSpec

    captured: dict = {}
    _fake_playwright(monkeypatch, captured)
    spec = SiteSpec(id="demo", auth_token="tk-9")
    fetch_page_html(
        "https://demo.test/pricing",
        {"enabled": True, "cookies": [{"name": "session", "value": "sid=${access_token}; Path=/"}]},
        spec=spec,
    )
    assert captured["cookies"][0]["value"] == "sid=tk-9; Path=/"


def test_cookie_access_token_placeholder_skipped_without_token(monkeypatch):
    from llm_price_monitor.browser_fetch import fetch_page_html

    captured: dict = {}
    _fake_playwright(monkeypatch, captured)
    # 与 localStorage 同一条引导链：还没拿到 Access Token 就不写这条，等续签补上后下次采集自然带上
    fetch_page_html(
        "https://demo.test/pricing",
        {
            "enabled": True,
            "cookies": [
                {"name": "session", "value": "${access_token}"},
                {"name": "theme", "value": "dark"},
            ],
        },
    )
    assert [item["name"] for item in captured["cookies"]] == ["theme"]


def test_cookie_refresh_token_missing_reports(monkeypatch):
    from llm_price_monitor.browser_fetch import fetch_page_html
    from llm_price_monitor.config import PriceMonitorError, SiteSpec

    captured: dict = {}
    _fake_playwright(monkeypatch, captured)
    with pytest.raises(PriceMonitorError, match="没有 Refresh Token"):
        fetch_page_html(
            "https://demo.test/pricing",
            {"enabled": True, "cookies": [{"name": "refresh", "value": "${refresh_token}"}]},
            spec=SiteSpec(id="demo"),
        )


def test_login_value_supports_mixed_text_and_multiple_placeholders(monkeypatch):
    from llm_price_monitor.browser_fetch import fetch_page_html
    from llm_price_monitor.config import SiteSpec

    captured: dict = {}
    _fake_playwright(monkeypatch, captured)
    spec = SiteSpec(id="demo", auth_token="tk-9", token_refresh={"refresh_token": "rf-1"})
    fetch_page_html(
        "https://demo.test/pricing",
        {
            "enabled": True,
            "cookies": [{"name": "combo", "value": "a=${access_token}&r=${refresh_token}"}],
            "localStorage": {"combo": "Bearer ${access_token}|refresh=${refresh_token}|static"},
        },
        spec=spec,
    )
    assert captured["cookies"][0]["value"] == "a=tk-9&r=rf-1"
    assert "Bearer tk-9|refresh=rf-1|static" in captured["script"]


def test_ratio_headers_config_validation():
    with pytest.raises(ValueError, match="ratio_url.headers 必须是对象"):
        _from_raw([_site({"url": "https://demo.test/pricing", "ratio_url": {"url": "https://ratio.test", "headers": "bad"}})])
    with pytest.raises(ValueError, match="ratio_url.url 必须是完整的"):
        _from_raw([_site({"url": "https://demo.test/pricing", "ratio_url": {"url": "not-a-url"}})])


# ---------- 启动自检：browser_setup ----------

def _store_with(tmp_path, network: dict):
    from llm_price_monitor.store import Store

    store = Store(tmp_path / "monitor.db")
    store.upsert_site("demo", _site(network))
    return store


def test_setup_skips_when_no_site_needs_browser(tmp_path, monkeypatch):
    from llm_price_monitor import browser_setup

    def fail_launch() -> bool:
        raise AssertionError("无站点启用 headless 时不应检查浏览器")

    monkeypatch.setattr(browser_setup, "_chromium_launches", fail_launch)
    browser_setup.ensure_browser_ready(_store_with(tmp_path, {"url": "https://demo.test/pricing"}))
    browser_setup.ensure_browser_ready(
        _store_with(tmp_path, {"url": "https://demo.test/pricing", "headless": {"enabled": False}})
    )


def test_setup_passes_when_chromium_launches(tmp_path, monkeypatch):
    from llm_price_monitor import browser_setup

    monkeypatch.setattr(browser_setup, "_chromium_launches", lambda: True)
    monkeypatch.setattr(browser_setup, "_install_chromium", lambda: (_ for _ in ()).throw(AssertionError("不应触发安装")))
    browser_setup.ensure_browser_ready(
        _store_with(tmp_path, {"url": "https://demo.test/pricing", "headless": {"enabled": True}})
    )


def test_setup_installs_when_chromium_missing(tmp_path, monkeypatch):
    from llm_price_monitor import browser_setup

    state = {"launches": 0, "installed": 0}

    def fake_launch() -> bool:
        state["launches"] += 1
        return state["launches"] > 1  # 首次失败，安装后成功

    def fake_install() -> None:
        state["installed"] += 1

    monkeypatch.setattr(browser_setup, "_chromium_launches", fake_launch)
    monkeypatch.setattr(browser_setup, "_install_chromium", fake_install)
    browser_setup.ensure_browser_ready(
        _store_with(tmp_path, {"url": "https://demo.test/pricing", "headless": {"enabled": True}})
    )
    assert state == {"launches": 2, "installed": 1}


def test_setup_reports_failure_without_crashing(tmp_path, monkeypatch):
    from llm_price_monitor import browser_setup

    monkeypatch.setattr(browser_setup, "_chromium_launches", lambda: False)
    monkeypatch.setattr(browser_setup, "_install_chromium", lambda: (_ for _ in ()).throw(RuntimeError("网络不通")))
    # 安装失败只写任务日志，不能让服务起不来
    browser_setup.ensure_browser_ready(
        _store_with(tmp_path, {"url": "https://demo.test/pricing", "headless": {"enabled": True}})
    )
