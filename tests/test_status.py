"""渠道状态采集：配置解析、三层解析、diff、存储、run_once 集成与 webapi 接口。"""
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from llm_price_monitor.config import AIConfig, MonitorConfig, PriceMonitorError, SiteSpec, load_config, sites_from_raw
from llm_price_monitor.report import run_once
from llm_price_monitor.status import ai_extract_status, diff_status, fetch_site_status
from llm_price_monitor.store import Store
from llm_price_monitor.webapi.app import create_app


def _spec(status: dict | str, site_id: str = "demo") -> SiteSpec:
    return sites_from_raw([{
        "id": site_id,
        "models": ["demo-model"],
        "network": {"url": f"https://{site_id}.test/api/pricing"},
        "status": status,
    }])[0]


def _site_raw_config(status: dict | None) -> dict:
    site = {
        "id": "demo",
        "models": ["demo-model"],
        "network": {"url": "https://demo.test/api/pricing"},
    }
    if status is not None:
        site["status"] = status
    return site


def _config_file(tmp_path: Path, site: dict) -> MonitorConfig:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"settings": {}, "ai": {"enabled": False}, "sites": [site]}), encoding="utf-8")
    return load_config(config_path)


# ---------- 配置解析 ----------

def test_status_accepts_object_and_url_string_shorthand():
    assert _spec({"url": "https://demo.test/status"}).status == {"url": "https://demo.test/status"}
    assert _spec("https://demo.test/status").status == {"url": "https://demo.test/status"}


def test_status_without_config_defaults_to_empty():
    spec = sites_from_raw([{"id": "demo", "models": ["m"], "network": {"url": "https://demo.test/api"}}])[0]
    assert spec.status == {}


def test_status_rejects_bad_url_and_bad_field_types():
    with pytest.raises(ValueError, match="status.url"):
        _spec({"url": "not-a-url"})
    with pytest.raises(ValueError, match="status.url"):
        _spec({"headers": {}})
    with pytest.raises(ValueError, match="status.headers"):
        _spec({"url": "https://demo.test/status", "headers": "Bearer x"})
    with pytest.raises(ValueError, match="必须是 URL 字符串或对象"):
        _spec(123)
    # 空 status 等价于未配置
    assert _spec({}).status == {}


# ---------- 三层解析 ----------

def test_fetch_site_status_uses_json_response_directly():
    spec = _spec({"url": "https://demo.test/status"})
    handler = lambda request: httpx.Response(200, json={"channels": [{"name": "gpt", "status": "up"}]})  # noqa: E731
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        record = fetch_site_status(spec, client, 10.0, "ua/1")
    assert record["parse"] == "json"
    assert record["data"]["channels"][0]["status"] == "up"


def test_fetch_site_status_extracts_embedded_json_from_html():
    spec = _spec({"url": "https://demo.test/status"})
    html = """<html><head><script>var analytics = {"track": true};</script></head>
<body><script>window.__CHANNEL_STATUS__ = {"items": [{"name": "渠道A", "status": "down"}]};</script></body></html>"""
    handler = lambda request: httpx.Response(200, text=html)  # noqa: E731
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        record = fetch_site_status(spec, client, 10.0, "ua/1")
    assert record["parse"] == "embedded_json"
    assert record["data"]["items"][0]["name"] == "渠道A"


def test_fetch_site_status_wraps_top_level_json_array():
    spec = _spec({"url": "https://demo.test/status"})
    handler = lambda request: httpx.Response(200, json=[{"name": "gpt", "status": "up"}])  # noqa: E731
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        record = fetch_site_status(spec, client, 10.0, "ua/1")
    assert record["parse"] == "json"
    assert record["data"]["items"][0]["name"] == "gpt"


def test_fetch_site_status_requires_ai_for_plain_text_without_ai():
    spec = _spec({"url": "https://demo.test/status"})
    handler = lambda request: httpx.Response(200, text="<html><body>维护中</body></html>")  # noqa: E731
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PriceMonitorError, match="未配置可用 AI"):
            fetch_site_status(spec, client, 10.0, "ua/1", ai=None)


def test_fetch_site_status_falls_back_to_ai_for_plain_text():
    spec = _spec({"url": "https://demo.test/status"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, text="<html><body><script>var s = '维护中';</script></body></html>")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"items": [{"name": "渠道A", "status": "维护中"}]})}}]})

    ai = AIConfig(enabled=True, base_url="https://ai.test/v1", models=("test-model",), api_key="k")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        record = fetch_site_status(spec, client, 10.0, "ua/1", ai=ai)
    assert record["parse"] == "ai"
    assert record["data"]["items"][0]["status"] == "维护中"


def test_ai_extract_status_sends_status_prompt_and_parses_json():
    captured = {}
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        captured["body"] = json.loads(request.read())
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"items": []}'}}]})

    cache: dict[str, dict] = {}
    ai = AIConfig(
        enabled=True, base_url="https://ai.test/v1", models=("test-model",), api_key="k",
        cache=SimpleNamespace(cache_get=cache.get, cache_put=lambda key, value: cache.update({key: value})),
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = ai_extract_status(ai, "<html>渠道全部正常</html>", "https://demo.test/status", client=client)
        second = ai_extract_status(ai, "<html>渠道全部正常</html>", "https://demo.test/status", client=client)
    assert first == second == {"items": []}
    assert calls["count"] == 1  # 页面原文不变时第二次直接命中缓存
    assert "渠道" in captured["body"]["messages"][0]["content"]


# ---------- diff ----------

def test_diff_status_reports_path_level_changes():
    previous = {"channels": [{"name": "a", "status": "up"}, {"name": "b", "status": "up"}], "note": "ok"}
    current = {"channels": [{"name": "a", "status": "down"}, {"name": "b", "status": "up"}], "note": "ok"}
    assert diff_status(previous, current) == [
        {"op": "change", "path": "$.channels[0].status", "old": "up", "new": "down"}
    ]


def test_diff_status_reports_add_and_remove():
    changes = diff_status({"a": 1}, {"b": 2})
    assert {"op": "remove", "path": "$.a", "old": 1} in changes
    assert {"op": "add", "path": "$.b", "new": 2} in changes
    assert diff_status({"same": 1}, {"same": 1}) == []


def test_diff_status_skips_volatile_time_fields():
    previous = {"status": "up", "checked_at": 1, "time": 5, "updated_at": 10}
    current = {"status": "up", "checked_at": 2, "time": 6, "updated_at": 11}
    assert diff_status(previous, current) == []
    assert diff_status({"groups": [{"name": "g", "checked_at": 1}]}, {"groups": [{"name": "g", "checked_at": 2}]}) == []
    assert diff_status({"status": "up", "time": 5}, {"status": "down", "time": 6}) == [
        {"op": "change", "path": "$.status", "old": "up", "new": "down"}
    ]


# ---------- run_once 集成 ----------

def test_run_once_collects_status_and_emits_init_then_changed_events(tmp_path: Path, monkeypatch):
    from llm_price_monitor.adapters import NetworkAdapter
    from llm_price_monitor.tracker import PriceRecord

    def collect(*_args):
        return [PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {})]

    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    status_payload = {"channels": [{"name": "gpt", "status": "up"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=status_payload)

    config = _config_file(tmp_path, _site_raw_config({"url": "https://demo.test/status"}))
    store = Store(tmp_path / "monitor.db")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = run_once(config, store=store, client=client)
    assert [event["kind"] for event in first.status_events] == ["status_init"]
    assert first.status_records[0]["data"]["channels"][0]["status"] == "up"
    assert store.read_status()[1] == 1

    status_payload["channels"][0]["status"] = "down"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        second = run_once(config, store=store, client=client)
    assert [event["kind"] for event in second.status_events] == ["status_changed"]
    assert second.status_events[0]["changes"] == [
        {"op": "change", "path": "$.channels[0].status", "old": "up", "new": "down"}
    ]
    assert store.read_status()[1] == 2
    stored_events, total = store.read_status_events()
    assert total == 2
    assert [event["kind"] for event in stored_events] == ["status_init", "status_changed"]


def test_run_once_skips_status_when_not_configured_and_reports_fetch_failure(tmp_path: Path, monkeypatch):
    from llm_price_monitor.adapters import NetworkAdapter
    from llm_price_monitor.tracker import PriceRecord

    def collect(*_args):
        return [PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {})]

    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    config = _config_file(tmp_path, _site_raw_config(None))
    store = Store(tmp_path / "monitor.db")
    report = run_once(config, store=store, client=httpx.Client())
    assert report.status_records == []
    assert report.status_events == []

    # 配置了 status 但地址 500：状态采集失败只记错误，不影响价格记录
    merged = MonitorConfig(
        settings=config.settings,
        ai=config.ai,
        sites=sites_from_raw([_site_raw_config({"url": "https://demo.test/status"})]),
    )
    handler = lambda request: httpx.Response(500, text="boom")  # noqa: E731
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_once(merged, store=store, client=client)
    assert report.status_records == []
    assert any("渠道状态采集失败" in error["error"] for error in report.errors)
    assert len(report.records) == 1  # 价格采集不受影响


# ---------- store ----------

def test_store_status_round_trip(tmp_path: Path):
    store = Store(tmp_path / "monitor.db")
    store.append_status_records([
        {"site_id": "a", "captured_at": 1.0, "data": {"ok": True}},
        {"site_id": "a", "captured_at": 2.0, "data": {"ok": False}},
        {"site_id": "b", "captured_at": 3.0, "data": {"ok": True}},
    ])
    records, total = store.read_status(site_id="a")
    assert total == 2
    assert [record["captured_at"] for record in records] == [1.0, 2.0]
    latest = store.latest_status_all()
    assert set(latest) == {"a", "b"}
    assert latest["a"]["data"] == {"ok": False}
    assert store.latest_status("a")["data"] == {"ok": False}
    assert store.latest_status("missing") is None

    store.append_status_events([
        {"site_id": "a", "kind": "status_changed", "detected_at": 4.0, "changes": [{"op": "change", "path": "$.ok"}]},
    ])
    events, event_total = store.read_status_events(site_id="a")
    assert event_total == 1
    assert events[0]["kind"] == "status_changed"


# ---------- webapi ----------

def test_webapi_status_endpoints(tmp_path: Path, monkeypatch):
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
    store.append_status_records([{"site_id": "demo", "captured_at": 1.0, "data": {"ok": True}}])
    store.append_status_events([{"site_id": "demo", "kind": "status_init", "detected_at": 1.0, "changes": [{"op": "init", "path": "$"}]}])

    client = TestClient(app)
    assert client.get("/api/status").json() == {
        "records": [{"site_id": "demo", "captured_at": 1.0, "data": {"ok": True}}],
        "total": 1,
    }
    assert client.get("/api/status", params={"site_id": "missing"}).json()["total"] == 0
    assert client.get("/api/status/latest").json()["demo"]["data"] == {"ok": True}
    events = client.get("/api/status/events").json()
    assert events["total"] == 1 and events["events"][0]["kind"] == "status_init"


def test_scan_statuses_runs_independently_of_prices(tmp_path: Path, monkeypatch):
    """渠道状态可单独按自己的周期采集：scan_statuses 不触发价格采集，scan_prices 不碰状态。"""
    from llm_price_monitor.adapters import NetworkAdapter
    from llm_price_monitor.report import scan_prices, scan_statuses
    from llm_price_monitor.tracker import PriceRecord

    calls = {"collect": 0}

    def collect(*_args):
        calls["collect"] += 1
        return [PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {})]

    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    config = _config_file(tmp_path, _site_raw_config({"url": "https://demo.test/status"}))
    store = Store(tmp_path / "monitor.db")
    handler = lambda request: httpx.Response(200, json={"channels": [{"name": "gpt", "status": "up"}]})  # noqa: E731

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        scan = scan_statuses(config, store=store, client=client)
    assert [event["kind"] for event in scan.events] == ["status_init"]
    assert scan.records[0]["data"]["channels"][0]["status"] == "up"
    assert calls["collect"] == 0  # 独立状态采集不触发价格采集
    assert store.read_status()[1] == 1

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = scan_prices(config, store=store, client=client)
    assert calls["collect"] == 1
    assert report.status_records == []  # 独立价格采集不碰渠道状态
