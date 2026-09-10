"""站点公告采集：配置解析、两层解析、run_once 集成、存储与 webapi 接口。"""
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from llm_price_monitor.ai import AIConfig, extract_notice_content
from llm_price_monitor.config import MonitorConfig, PriceMonitorError, SiteSpec, load_config, sites_from_raw
from llm_price_monitor.notice import fetch_site_notice, resolve_notice_url
from llm_price_monitor.report import run_once
from llm_price_monitor.store import Store
from llm_price_monitor.webapi.app import create_app


def _spec(notice: dict | str | None = None, site_id: str = "demo") -> SiteSpec:
    raw = {
        "id": site_id,
        "models": ["demo-model"],
        "network": {"url": f"https://{site_id}.test/api/pricing"},
    }
    if notice is not None:
        raw["notice"] = notice
    return sites_from_raw([raw])[0]


def _site_raw_config(notice: dict | None) -> dict:
    site = {
        "id": "demo",
        "models": ["demo-model"],
        "network": {"url": "https://demo.test/api/pricing"},
    }
    if notice is not None:
        site["notice"] = notice
    return site


def _config_file(tmp_path: Path, site: dict) -> MonitorConfig:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"settings": {}, "ai": {"enabled": False}, "sites": [site]}), encoding="utf-8")
    return load_config(config_path)


# ---------- 配置解析 ----------

def test_notice_accepts_object_and_url_string_shorthand():
    assert _spec({"url": "https://demo.test/notice"}).notice == {"url": "https://demo.test/notice"}
    assert _spec("https://demo.test/notice").notice == {"url": "https://demo.test/notice"}


def test_notice_without_config_defaults_to_empty():
    spec = sites_from_raw([{"id": "demo", "models": ["m"], "network": {"url": "https://demo.test/api"}}])[0]
    assert spec.notice == {}


def test_notice_rejects_bad_url_and_bad_field_types():
    with pytest.raises(ValueError, match="notice.url"):
        _spec({"url": "not-a-url"})
    with pytest.raises(ValueError, match="notice.url"):
        _spec({"headers": {}})
    with pytest.raises(ValueError, match="notice.headers"):
        _spec({"url": "https://demo.test/notice", "headers": "Bearer x"})
    with pytest.raises(ValueError, match="必须是 URL 字符串或对象"):
        _spec(123)
    assert _spec({}).notice == {}


# ---------- 地址解析与两层解析 ----------

def test_resolve_notice_url_prefers_config_then_derives_from_network():
    assert resolve_notice_url(_spec({"url": "https://cdn.test/notice.txt"})) == "https://cdn.test/notice.txt"
    assert resolve_notice_url(_spec(None)) == "https://demo.test/api/notice"
    bare = sites_from_raw([{"id": "demo", "models": ["m"]}])[0]
    assert resolve_notice_url(bare) is None


def test_fetch_site_notice_reads_newapi_data_field():
    spec = _spec(None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/notice":  # 未配置时从 network.url 推导默认公告接口
            return httpx.Response(200, json={"success": True, "message": "", "data": "# 公告\n\n充值满 100 送 10。"})
        assert request.url.path == "/api/status"
        return httpx.Response(200, json={"success": True, "data": {}})  # 无公告列表，回落单条公告

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        record = fetch_site_notice(spec, client, 10.0, "ua/1")
    assert record["parse"] == "json"
    assert record["content"].startswith("# 公告")
    assert record["source_url"] == "https://demo.test/api/notice"


def test_fetch_site_notice_handles_json_list_payload():
    """公告接口顶层是 JSON 数组时按原文处理，不再把正文丢成空串。"""
    spec = _spec({"url": "https://demo.test/api/notice"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/notice":
            return httpx.Response(200, json=[{"id": 1, "content": "数组公告正文"}])
        return httpx.Response(200, json={"success": True, "data": {}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        record = fetch_site_notice(spec, client, 10.0, "ua/1")
    assert record["parse"] == "text"
    assert "数组公告正文" in record["content"]


def test_fetch_site_notice_merges_status_announcements():
    """new-api 多条公告走 /api/status：分节拼进正文，置顶公告在前、列表最新在前。"""
    spec = _spec(None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/notice":
            return httpx.Response(200, json={"success": True, "data": "置顶维护公告"})
        assert request.url.path == "/api/status"
        return httpx.Response(200, json={"success": True, "data": {"announcements": [
            {"id": 2, "content": "新公告正文", "extra": "新公告", "publishDate": "2026-09-03T15:30:00.000Z"},
            {"id": 1, "content": "旧公告正文", "extra": "旧公告", "publishDate": "2026-08-01T10:00:00.000Z"},
        ]}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        record = fetch_site_notice(spec, client, 10.0, "ua/1")
    assert record["parse"] == "json+status"
    assert record["content"].startswith("置顶维护公告")
    assert "## 新公告（2026-09-03）" in record["content"]
    assert record["content"].index("新公告正文") < record["content"].index("旧公告正文")


def test_fetch_site_notice_falls_back_when_status_unavailable():
    """/api/status 失败（非 new-api 或接口挂了）：回落只存 /api/notice 单条公告。"""
    spec = _spec(None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/notice":
            return httpx.Response(200, json={"success": True, "data": "单条公告"})
        return httpx.Response(500, text="boom")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        record = fetch_site_notice(spec, client, 10.0, "ua/1")
    assert record["parse"] == "json"
    assert record["content"] == "单条公告"


def test_fetch_site_notice_rejects_success_false_and_404():
    spec = _spec({"url": "https://demo.test/api/notice"})
    handler_403 = lambda request: httpx.Response(403, text="forbidden")  # noqa: E731
    with httpx.Client(transport=httpx.MockTransport(handler_403)) as client:
        with pytest.raises(PriceMonitorError, match="可能需要认证"):
            fetch_site_notice(spec, client, 10.0, "ua/1")

    handler_404 = lambda request: httpx.Response(404, text="not found")  # noqa: E731
    with httpx.Client(transport=httpx.MockTransport(handler_404)) as client:
        with pytest.raises(PriceMonitorError, match="404"):
            fetch_site_notice(spec, client, 10.0, "ua/1")

    bare_spec = _spec(None)  # 未配置 notice.url：404 = 站点没有公告接口，返回 None 静默跳过
    with httpx.Client(transport=httpx.MockTransport(handler_404)) as client:
        assert fetch_site_notice(bare_spec, client, 10.0, "ua/1") is None

    handler_failed = lambda request: httpx.Response(200, json={"success": False, "message": "无权访问"})  # noqa: E731
    with httpx.Client(transport=httpx.MockTransport(handler_failed)) as client:
        with pytest.raises(PriceMonitorError, match="无权访问"):
            fetch_site_notice(spec, client, 10.0, "ua/1")


def test_fetch_site_notice_keeps_plain_text_page():
    spec = _spec({"url": "https://demo.test/notice.txt"})
    handler = lambda request: httpx.Response(200, text="系统维护中，预计 2 小时后恢复。")  # noqa: E731
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        record = fetch_site_notice(spec, client, 10.0, "ua/1")
    assert record["parse"] == "text"
    assert record["content"] == "系统维护中，预计 2 小时后恢复。"


def test_fetch_site_notice_empty_data_yields_empty_content():
    spec = _spec(None)
    handler = lambda request: httpx.Response(200, json={"success": True, "message": "", "data": ""})  # noqa: E731
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        record = fetch_site_notice(spec, client, 10.0, "ua/1")
    assert record["content"] == ""


def test_fetch_site_notice_reuses_network_headers():
    """公告请求（含 /api/status）继承 network.headers 的认证/Cookie 头。"""
    seen: dict[str, dict[str, str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = {k.lower(): v for k, v in request.headers.items()}
        if request.url.path == "/api/notice":
            return httpx.Response(200, json={"success": True, "data": "公告"})
        return httpx.Response(200, json={"success": True, "data": {}})

    spec = sites_from_raw([{
        "id": "demo",
        "models": ["m"],
        "network": {"url": "https://demo.test/api/pricing", "headers": {"Authorization": "Bearer tok", "New-Api-User": "9391"}},
    }])[0]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetch_site_notice(spec, client, 10.0, "ua/1")
    assert seen["/api/notice"]["authorization"] == "Bearer tok"
    assert seen["/api/notice"]["new-api-user"] == "9391"
    assert seen["/api/status"]["authorization"] == "Bearer tok"


def test_fetch_site_notice_ai_fallback(monkeypatch):
    """固定解析认不出的响应结构（announcements 数组）交给 AI 提取正文。"""
    spec = _spec({"url": "https://demo.test/api/announcements"})

    def fake_extract(config, raw_text, *, client=None):
        assert '"announcements"' in raw_text
        return "## 平台公告\n\n切换 GPT-6。"

    monkeypatch.setattr("llm_price_monitor.notice.extract_notice_content", fake_extract)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"announcements": [{"id": 1, "title": "平台公告", "content": "切换 GPT-6。"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        record = fetch_site_notice(spec, client, 10.0, "ua/1", ai=AIConfig(enabled=True))
    assert record["parse"] == "ai"
    assert "GPT-6" in record["content"]


def test_extract_notice_content_parses_json_reply():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"content": "公告正文"}'}}]})

    ai = AIConfig(enabled=True, base_url="https://ai.test/v1", api_key="k", models=("m1",))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert extract_notice_content(ai, '{"announcements": []}', client=client) == "公告正文"


def test_extract_notice_content_disabled_returns_none():
    assert extract_notice_content(AIConfig(enabled=False), "anything") is None


# ---------- run_once 集成 ----------

def _patch_collect(monkeypatch) -> None:
    from llm_price_monitor.adapters import NetworkAdapter
    from llm_price_monitor.tracker import PriceRecord

    monkeypatch.setattr(
        NetworkAdapter,
        "collect",
        lambda *_args: [PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {})],
    )


def test_run_once_collects_notice_and_emits_init_then_changed_events(tmp_path: Path, monkeypatch):
    _patch_collect(monkeypatch)
    notice_payload = {"success": True, "message": "", "data": "初始公告"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/notice":
            return httpx.Response(200, json=notice_payload)
        return httpx.Response(200, json={})  # 其他请求返回空 JSON，公告内容为空不入库

    config = _config_file(tmp_path, _site_raw_config(None))
    store = Store(tmp_path / "monitor.db")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = run_once(config, store=store, client=client)
    assert [event["kind"] for event in first.notice_events] == ["notice_init"]
    assert first.notice_records[0]["content"] == "初始公告"
    assert store.read_notice()[1] == 1

    # 内容不变：不重复存版本、不发事件
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        second = run_once(config, store=store, client=client)
    assert second.notice_events == []
    assert second.notice_records == []
    assert store.read_notice()[1] == 1

    notice_payload["data"] = "维护公告：今晚 22:00 升级"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        third = run_once(config, store=store, client=client)
    assert [event["kind"] for event in third.notice_events] == ["notice_changed"]
    assert third.notice_events[0]["content"] == "维护公告：今晚 22:00 升级"
    assert store.read_notice()[1] == 2
    stored_events, total = store.read_notice_events()
    assert total == 2
    assert [event["kind"] for event in stored_events] == ["notice_init", "notice_changed"]


def test_run_once_skips_missing_notice_silently_and_reports_other_failures(tmp_path: Path, monkeypatch):
    _patch_collect(monkeypatch)
    config = _config_file(tmp_path, _site_raw_config(None))
    store = Store(tmp_path / "monitor.db")

    # 未配置 notice.url 且自动推导地址 404：视为站点没有公告接口，静默跳过不刷错误
    handler_404 = lambda request: httpx.Response(404, text="not found")  # noqa: E731
    with httpx.Client(transport=httpx.MockTransport(handler_404)) as client:
        report = run_once(config, store=store, client=client)
    assert report.notice_records == []
    assert report.notice_events == []
    assert not any("站点公告采集失败" in error["error"] for error in report.errors)
    assert len(report.records) == 1

    # 其他失败（如 500）：只记错误，不影响价格记录
    handler_500 = lambda request: httpx.Response(500, text="boom")  # noqa: E731
    with httpx.Client(transport=httpx.MockTransport(handler_500)) as client:
        report = run_once(config, store=store, client=client)
    assert any("站点公告采集失败" in error["error"] for error in report.errors)
    assert len(report.records) == 1


# ---------- store ----------

def test_store_notice_round_trip(tmp_path: Path):
    store = Store(tmp_path / "monitor.db")
    store.append_notice_records([
        {"site_id": "a", "captured_at": 1.0, "content": "v1"},
        {"site_id": "a", "captured_at": 2.0, "content": "v2"},
        {"site_id": "b", "captured_at": 3.0, "content": "v1"},
    ])
    records, total = store.read_notice(site_id="a")
    assert total == 2
    assert [record["content"] for record in records] == ["v1", "v2"]
    assert store.latest_notice("a")["content"] == "v2"
    assert store.latest_notice("missing") is None

    store.append_notice_events([
        {"site_id": "a", "kind": "notice_changed", "detected_at": 4.0, "content": "v2"},
    ])
    events, event_total = store.read_notice_events(site_id="a")
    assert event_total == 1
    assert events[0]["kind"] == "notice_changed"


# ---------- webapi ----------

def test_webapi_notice_endpoints(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "var").mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        # 关闭后台调度：测试环境不发起任何定时采集与官方价同步
        "settings": {"schedule": {"price": 0, "status": 0, "notice": 0, "catalog": 0}},
        "ai": {"enabled": False},
        "sites": [_site_raw_config(None)],
    }), encoding="utf-8")

    app = create_app(config_path)
    store = app.state.store
    store.append_notice_records([{"site_id": "demo", "captured_at": 1.0, "content": "公告一"}])
    store.append_notice_events([{"site_id": "demo", "kind": "notice_init", "detected_at": 1.0, "content": "公告一"}])

    client = TestClient(app)
    assert client.get("/api/notice").json() == {
        "records": [{"site_id": "demo", "captured_at": 1.0, "content": "公告一"}],
        "total": 1,
    }
    assert client.get("/api/notice", params={"site_id": "missing"}).json()["total"] == 0
    # 公告事件并入统一事件流 /api/feed
    feed = client.get("/api/feed").json()
    assert feed["notice_total"] == 1
    assert [e["kind"] for e in feed["events"] if e["kind"] == "notice_init"] == ["notice_init"]


def test_scan_notices_runs_independently_of_prices(tmp_path: Path, monkeypatch):
    """站点公告可单独按自己的周期采集：scan_notices 不触发价格采集，scan_prices 不碰公告。"""
    from llm_price_monitor.adapters import NetworkAdapter
    from llm_price_monitor.report import scan_notices, scan_prices
    from llm_price_monitor.tracker import PriceRecord

    calls = {"collect": 0}

    def collect(*_args):
        calls["collect"] += 1
        return [PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {})]

    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    config = _config_file(tmp_path, _site_raw_config({"url": "https://demo.test/notice"}))
    store = Store(tmp_path / "monitor.db")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": "第一版公告"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        scan = scan_notices(config, store=store, client=client)
    assert [event["kind"] for event in scan.events] == ["notice_init"]
    assert calls["collect"] == 0  # 独立公告采集不触发价格采集
    assert store.read_notice()[1] == 1

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = scan_prices(config, store=store, client=client)
    assert calls["collect"] == 1
    assert report.notice_records == []  # 独立价格采集不碰站点公告
