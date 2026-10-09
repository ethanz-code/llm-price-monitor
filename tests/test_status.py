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
from llm_price_monitor.timeline import strip_status_delta
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


def test_prune_status_history_keeps_only_selected_groups(tmp_path: Path):
    """保存分组过滤后清理历史：快照与参照只留选中分组，指向未选中分组的事件变化被剔除。"""
    store = Store(tmp_path / "monitor.db")
    store.append_status_records([
        {
            "site_id": "a",
            "captured_at": 1.0,
            "data": {"channels": [{"name": "svip", "state": "ok"}, {"name": "vip", "state": "down"}]},
        },
        {"site_id": "a", "captured_at": 2.0, "data": {"channels": [{"name": "svip", "state": "warn"}]}},
    ])
    store.set_document("status_ref:a", {
        "data": {"channels": [{"name": "svip", "state": "warn"}, {"name": "vip", "state": "down"}]}
    })
    store.append_status_events([
        {
            "site_id": "a",
            "kind": "status_changed",
            "detected_at": 3.0,
            "changes": [
                {"op": "change", "path": "$.channels[0].state"},
                {"op": "change", "path": "$.channels[1].state"},
            ],
        },
    ])

    stats = store.prune_status_history("a", ["svip"])
    assert stats["records"] == 1 and stats["ref"] == 1
    assert stats["events"] == 1 and stats["events_removed"] == 0

    records, _ = store.read_status(site_id="a")
    assert [channel["name"] for record in records for channel in record["data"]["channels"]] == ["svip", "svip"]
    assert store.status_reference("a")["data"]["channels"] == [{"name": "svip", "state": "warn"}]
    events, _ = store.read_status_events(site_id="a")
    assert events[0]["changes"] == [{"op": "change", "path": "$.channels[0].state"}]

    # 幂等：重复执行不再有改动；事件变化全部落到未选中分组时整条删除
    assert store.prune_status_history("a", ["svip"]) == {"records": 0, "ref": 0, "events": 0, "events_removed": 0}
    store.append_status_events([
        {"site_id": "a", "kind": "status_changed", "detected_at": 4.0,
         "changes": [{"op": "change", "path": "$.channels[0].state"}]},
    ])
    store.set_document("status_ref:a", {"data": {"channels": [{"name": "vip", "state": "down"}]}})
    # 参照已换成仅剩未选中分组：两条事件的变化都归因到未选中分组，全部整条删除
    stats = store.prune_status_history("a", ["svip"])
    assert stats["events_removed"] == 2
    assert store.read_status_events(site_id="a")[1] == 0


def test_store_status_since_filters_by_time(tmp_path: Path):
    """since 只取该时间之后的快照，plain 与 per_site 两条路径一致，total 同步收窄。"""
    store = Store(tmp_path / "monitor.db")
    store.append_status_records([
        {"site_id": "a", "captured_at": 1.0, "data": {"ok": True}},
        {"site_id": "a", "captured_at": 5.0, "data": {"ok": False}},
        {"site_id": "b", "captured_at": 9.0, "data": {"ok": True}},
    ])
    records, total = store.read_status(since=2.0)
    assert total == 2
    assert [record["captured_at"] for record in records] == [5.0, 9.0]

    per_site_records, per_site_total = store.read_status(per_site=10, since=2.0)
    assert per_site_total == 2
    assert [record["captured_at"] for record in per_site_records] == [5.0, 9.0]


def test_store_status_max_records_samples_evenly(tmp_path: Path):
    """窗口内记录超过 max_records 时按 id 均匀抽样返回，total 仍是全量行数。"""
    store = Store(tmp_path / "monitor.db")
    store.append_status_records([
        {"site_id": "a", "captured_at": float(i), "data": {"ok": True}} for i in range(1, 21)
    ])
    records, total = store.read_status(site_id="a", max_records=5)
    assert total == 20
    assert [record["captured_at"] for record in records] == [4.0, 8.0, 12.0, 16.0, 20.0]

    # 窗口内不超过 max_records 时不抽样，逐条返回
    all_records, all_total = store.read_status(site_id="a", max_records=100)
    assert all_total == 20
    assert len(all_records) == 20


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


# ---------- 时间线增量裁剪与 diff 噪音 ----------

def test_strip_status_delta_keeps_only_new_timeline_entries():
    """时间线按检测时间戳去重：只留上一条没有的条目；无内容可裁时原对象返回。"""
    previous = {"groups": [{"name": "a", "state": "operational", "timeline": [
        {"state": "operational", "time": 1}, {"state": "down", "time": 2},
    ]}]}
    current = {"groups": [{"name": "a", "state": "operational", "timeline": [
        {"state": "operational", "time": 2}, {"state": "operational", "time": 3},
    ]}]}
    stripped = strip_status_delta(previous, current)
    assert stripped["groups"][0]["timeline"] == [{"state": "operational", "time": 3}]
    # 当前状态字段不属于时间线，不裁
    assert stripped["groups"][0]["state"] == "operational"
    # 没有可裁内容时 identity 相等，调用方据此跳过重写
    assert strip_status_delta(None, current) is current


def test_diff_status_skips_timeline_arrays_but_keeps_state_changes():
    """时间线是滚动窗口，diff 必须跳过；渠道当前状态字段的变化仍要报。"""
    previous = {"state": "operational", "timeline": [{"state": "ok", "time": 1}]}
    same_state_grown = {"state": "operational", "timeline": [{"state": "ok", "time": 1}, {"state": "ok", "time": 2}]}
    assert diff_status(previous, same_state_grown) == []
    degraded = {"state": "degraded", "timeline": []}
    changes = diff_status(previous, degraded)
    assert [change["path"] for change in changes] == ["$.state"]


def test_strip_sliding_synthetic_timeline_collapses():
    """时间戳与上一条完全无交集的窗口是滑动合成时间轴：整条裁掉，时序由采集时刻的状态点承载。"""
    base = [{"state": "operational", "checked_at": 1000 + i * 10080} for i in range(60)]
    slid = [{"state": "operational", "checked_at": 1100 + i * 10080} for i in range(60)]
    out = strip_status_delta({"timeline": base}, {"timeline": slid}, timeline_key="timeline")
    assert out["timeline"] == []
    # 双方都没有可解析时间戳：内容一致才裁
    plain_a = [{"state": "operational"}, {"state": "down"}]
    plain_b = [{"state": "operational"}, {"state": "down"}]
    assert strip_status_delta({"timeline": plain_a}, {"timeline": plain_b}, timeline_key="timeline")["timeline"] == []
    plain_c = [{"state": "operational"}, {"state": "operational"}]
    kept = strip_status_delta({"timeline": plain_a}, {"timeline": plain_c}, timeline_key="timeline")
    assert kept["timeline"] == plain_c


def test_store_status_delta_append_and_compact(tmp_path):
    """写入只存时间线增量、参照保留原文；压缩后合并视图仍还原状态序列。"""
    def t(time: int, state: str = "operational") -> dict:
        return {"state": state, "time": time}

    def snapshot(captured_at: float, timeline: list[dict], state: str = "operational") -> dict:
        return {"site_id": "a", "captured_at": captured_at, "data": {"data": {"groups": [
            {"name": "g1", "state": state, "timeline": timeline},
        ]}}}

    store = Store(tmp_path / "monitor.db")
    store.append_status_records([snapshot(1.0, [t(1), t(2), t(3)])])
    store.append_status_records([snapshot(2.0, [t(3), t(4, "degraded")], state="degraded")])  # 3 已存，只留 4
    rows, total = store.read_status(site_id="a")
    assert total == 2
    assert rows[1]["data"]["data"]["groups"][0]["timeline"] == [t(4, "degraded")]
    reference = store.status_reference("a")
    assert [item["time"] for item in reference["data"]["data"]["groups"][0]["timeline"]] == [3, 4]

    noisy_changes = [
        {"op": "change", "path": "$.data.data.groups[0].timeline[3].state", "old": "x", "new": "y"},
        {"op": "change", "path": "$.data.data.groups[0].state", "old": "operational", "new": "degraded"},
    ]
    store.append_status_events([{"site_id": "a", "kind": "status_changed", "detected_at": 2.0, "changes": noisy_changes}])
    stats = store.compact_status_history()
    assert stats["records"] == 2
    rows_after = store.read_status(site_id="a")[0]
    merged_times = [
        item["time"]
        for row in rows_after
        for group in row["data"]["data"]["groups"]
        for item in group["timeline"]
    ]
    assert merged_times == [1, 2, 3]
    # 最新状态不依赖被裁掉的时间线：渠道当前字段原样保留
    assert rows_after[1]["data"]["data"]["groups"][0]["state"] == "degraded"
    # 噪音 change 被清洗，真实状态变化保留
    events, _ = store.read_status_events(site_id="a")
    assert events[0]["changes"] == [noisy_changes[1]]
