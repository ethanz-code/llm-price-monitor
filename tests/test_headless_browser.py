"""network.headless 网页模式配置校验与采集分支测试；不真启动浏览器，全部用 monkeypatch 替身。"""
import httpx
import pytest

from llm_price_monitor.config import sites_from_raw
from llm_price_monitor.tracker import fetch_price


def _site(network: dict) -> dict:
    return {"id": "demo", "models": ["gpt-5.6-luna"], "network": network}


def test_headless_config_valid_passes_validation():
    (spec,) = sites_from_raw([
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
    (spec,) = sites_from_raw([
        _site({"url": "https://demo.test/pricing", "headless": {"enabled": False, "cookies": "oops", "wait_seconds": 999}})
    ])
    assert spec.network["headless"]["enabled"] is False
    (bare,) = sites_from_raw([_site({"url": "https://demo.test/pricing"})])
    assert "headless" not in bare.network


def test_headless_enabled_must_be_bool():
    with pytest.raises(ValueError, match="headless.enabled 必须是布尔值"):
        sites_from_raw([_site({"url": "https://demo.test/pricing", "headless": {"enabled": "yes"}})])


def test_headless_cookie_missing_value_rejected():
    with pytest.raises(ValueError, match=r"headless\.cookies\[0\]\.value 必须是非空字符串"):
        sites_from_raw([_site({"url": "https://demo.test/pricing", "headless": {"enabled": True, "cookies": [{"name": "session"}]}})])


def test_headless_local_storage_must_be_string_dict():
    with pytest.raises(ValueError, match="headless.localStorage 必须是字符串键值对象"):
        sites_from_raw([_site({"url": "https://demo.test/pricing", "headless": {"enabled": True, "localStorage": {"token": 123}}})])


def test_headless_wait_seconds_out_of_range_rejected():
    with pytest.raises(ValueError, match="wait_seconds 必须是 0~60 之间的数字"):
        sites_from_raw([_site({"url": "https://demo.test/pricing", "headless": {"enabled": True, "wait_seconds": 61}})])
    with pytest.raises(ValueError, match="wait_seconds 必须是 0~60 之间的数字"):
        sites_from_raw([_site({"url": "https://demo.test/pricing", "headless": {"enabled": True, "wait_seconds": -1}})])


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
    def fail_fetch(url: str, headless_config: dict) -> str:
        raise AssertionError("未启用 headless 时不应走浏览器分支")

    monkeypatch.setattr("llm_price_monitor.tracker._fetch_page_via_browser", fail_fetch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="gpt-5.6-luna 输入价格 ¥1.1 / 1M tokens，输出价格 ¥2.2 / 1M tokens")

    record = fetch_price("https://demo.test/pricing", "gpt-5.6-luna", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert record.input_price == 1.1 and record.output_price == 2.2


def test_fetch_price_browser_failure_raises_like_collect_failure(monkeypatch):
    def fail_fetch(url: str, headless_config: dict) -> str:
        raise RuntimeError("无头浏览器采集失败：超时")

    monkeypatch.setattr("llm_price_monitor.tracker._fetch_page_via_browser", fail_fetch)
    with pytest.raises(RuntimeError, match="无头浏览器采集失败"):
        fetch_price("https://demo.test/pricing", "gpt-5.6-luna", network={"headless": {"enabled": True}})


def test_browser_fetch_is_importable_without_playwright():
    # 懒加载：即便环境没装 playwright，模块导入也必须成功
    import llm_price_monitor.browser_fetch  # noqa: F401


# ---------- 生产采集路径：NetworkAdapter.collect ----------

def _spec(headless: dict | None):
    network = {"url": "https://demo.test/pricing"}
    if headless is not None:
        network["headless"] = headless
    (spec,) = sites_from_raw([_site(network)])
    return spec


_PAGE_HTML = '<html><body>{category:"text",models:["gpt-5.6-luna"],provider:"demo",input:3.3,output:9.9}</body></html>'


def test_network_adapter_uses_browser_when_headless_enabled(monkeypatch):
    from llm_price_monitor.adapters import NetworkAdapter

    calls: list[str] = []

    def fake_fetch_page_html(url: str, headless_config: dict) -> str:
        calls.append(url)
        return _PAGE_HTML

    monkeypatch.setattr("llm_price_monitor.browser_fetch.fetch_page_html", fake_fetch_page_html)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"启用 headless 时不应走 httpx 请求：{request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        records = NetworkAdapter().collect(_spec({"enabled": True}), client, 20.0, "test-ua")
    assert calls == ["https://demo.test/pricing"]
    record = next(item for item in records if item.model == "gpt-5.6-luna")
    assert record.input_price == 3.3 and record.output_price == 9.9


def test_network_adapter_keeps_httpx_when_headless_disabled(monkeypatch):
    from llm_price_monitor.adapters import NetworkAdapter

    def fail_fetch(url: str, headless_config: dict) -> str:
        raise AssertionError("未启用 headless 时不应走浏览器分支")

    monkeypatch.setattr("llm_price_monitor.browser_fetch.fetch_page_html", fail_fetch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_PAGE_HTML)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        records = NetworkAdapter().collect(_spec(None), client, 20.0, "test-ua")
    record = next(item for item in records if item.model == "gpt-5.6-luna")
    assert record.input_price == 3.3 and record.output_price == 9.9


# ---------- 倍率接口独立请求头（ratio_url 对象形态） ----------

def _ratio_site(ratio: dict | str):
    network = {"url": "https://demo.test/pricing", "ratio_url": ratio}
    (spec,) = sites_from_raw([_site(network)])
    return spec


_RATIO_PAGE = '<html><body>{category:"text",models:["gpt-5.6-luna"],provider:"demo",input:3.3,output:9.9}</body></html>'
def _ratio_json() -> httpx.Response:
    return httpx.Response(200, json={"pricing": [{"provider": "demo", "rate": 0.5}]})


def test_ratio_url_object_with_headers(monkeypatch):
    from llm_price_monitor.adapters import NetworkAdapter

    monkeypatch.setattr("llm_price_monitor.browser_fetch.fetch_page_html", lambda url, cfg: _RATIO_PAGE)
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

    monkeypatch.setattr("llm_price_monitor.browser_fetch.fetch_page_html", lambda url, cfg: _RATIO_PAGE)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ratio.test":
            return _ratio_json()
        return httpx.Response(200, text=_RATIO_PAGE)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        records = NetworkAdapter().collect(_ratio_site("https://ratio.test/api/rate"), client, 20.0, "test-ua")
    priced = [item for item in records if item.model == "gpt-5.6-luna" and item.price_status == "confirmed"]
    assert priced and priced[0].output_price == 9.9 * 0.5


def test_ratio_headers_config_validation():
    with pytest.raises(ValueError, match="ratio_url.headers 必须是对象"):
        sites_from_raw([_site({"url": "https://demo.test/pricing", "ratio_url": {"url": "https://ratio.test", "headers": "bad"}})])
    with pytest.raises(ValueError, match="ratio_url.url 必须是完整的"):
        sites_from_raw([_site({"url": "https://demo.test/pricing", "ratio_url": {"url": "not-a-url"}})])
