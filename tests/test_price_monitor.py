import json
import time
from pathlib import Path

import httpx
import pytest

from llm_price_monitor.adapters import ADAPTERS, NetworkAdapter, headers as _headers, network_pricing_records as _network_pricing_records, parse_base_price_entries
from llm_price_monitor.ai import (
    AIExtractionError,
    AIPriceExtractor,
    NEWAPI_ONEAPI_PRICING_GUIDANCE,
    ai_content,
    ai_request,
    ai_stream_fallback,
    ai_stream_messages_fallback,
    ping_model,
    provider_error_detail,
    request_with_model_fallback,
)
from llm_price_monitor.config import AIConfig, ModelTarget, PriceMonitorError, SiteSpec, load_config, sites_from_raw
from llm_price_monitor.evidence import decode_response_body as _decode_response_body, is_preferred_response_url as _is_preferred_response_url, repair_mojibake as _repair_mojibake, slim_pricing_payload as _slim_pricing_payload, target_page_text as _target_page_text
from llm_price_monitor.report import GROUP_REMOVED_MISSES, _persist_scan_results, classify, fingerprint, record_dict, run_once, summary_row as _summary_row
from llm_price_monitor.store import Store
from llm_price_monitor.useragent import BROWSER_USER_AGENTS, choose_user_agent
from llm_price_monitor.tracker import PriceRecord


def _config(tmp_path: Path) -> dict:
    return {
        "settings": {
            "history_file": str(tmp_path / "history.jsonl"),
            "latest_file": str(tmp_path / "latest.json"),
            "event_file": str(tmp_path / "events.jsonl")
        },
        "sites": [{
            "id": "demo",
            "adapter": "standard",
            "model_list_url": "https://demo.test/pricing",
            "models": ["demo-model"]
        }]
    }


def _ai_request_body(spec, page_text, responses, *, page_sources=None, config=None):
    """用 MockTransport 捕获 AIPriceExtractor 实际发出的 AI 请求体，用于断言请求构建。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"models": [], "cross_validation": {"status": "none", "conflicts": []}})}}]})

    extractor = AIPriceExtractor(config or AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="k"))
    extractor.extract(spec, page_text, responses, client=httpx.Client(transport=httpx.MockTransport(handler)), page_sources=page_sources)
    return captured["body"]


def test_slim_pricing_payload_projects_newapi_fields():
    payload = {
        "success": True,
        "message": "ok",
        "announcement": "双十一全场促销，充值满 1000 送 100！",
        "data": [{
            "model_name": "gpt-5.6-sol",
            "model_ratio": 1.25,
            "completion_ratio": 4,
            "quota_type": 0,
            "enable_groups": ["default", "vip"],
            "description": "GPT-5.6 Sol 是 OpenAI 的最新推理模型，支持超长上下文…" * 20,
            "icon": "https://demo.test/icons/gpt.png",
            "tags": ["热门", "推荐"],
            "supported_endpoint_types": ["chat", "embeddings"],
        }],
        "group_ratio": {"default": 1, "vip": 0.8},
        "usable_group": {"default": "默认分组", "vip": "VIP 分组"},
    }

    slimmed = _slim_pricing_payload(payload)

    entry = slimmed["data"][0]
    assert entry == {
        "model_name": "gpt-5.6-sol",
        "model_ratio": 1.25,
        "completion_ratio": 4,
        "quota_type": 0,
        "enable_groups": ["default", "vip"],
    }
    assert set(slimmed) == {"data", "group_ratio", "usable_group"}
    assert "announcement" in json.dumps(payload, ensure_ascii=False)
    assert "description" not in json.dumps(slimmed, ensure_ascii=False)


def test_slim_pricing_payload_keeps_non_newapi_intact():
    payload = {"models": [{"id": "gpt-5.6-sol", "input": 1, "output": 2, "description": "长描述"}]}

    assert _slim_pricing_payload(payload) == payload


def _micro_usd_row(mid: str, role: str, comp: str, usd: int, *, active: bool = True, unit: str = "per_1m_tokens") -> str:
    row = (
        f'{{"id":1,"modelId":"{mid}","role":"{role}","billingMode":"token","component":"{comp}",'
        f'"unit":"{unit}","tierLabel":"","priceMicroUsd":{usd},"active":{"true" if active else "false"}}}'
    )
    return row


def _micro_usd_rsc(rows: list[str], *, level: int = 1) -> str:
    """按 RSC 转义层级包一层：level=1 对应 \\" 前缀，level=2 对应 \\\\" 前缀。"""
    body = ",".join(rows)
    if level == 1:
        body = body.replace('"', '\\"')
    else:
        body = body.replace('"', '\\\\"')
    return f'<!doctype html><script>self.__next_f.push([1,"{body}"])</script>'


def test_parse_micro_usd_entries_prefers_sell_group_and_converts_micro_usd():
    rows = [
        _micro_usd_row("anthropic/claude-opus-5-5", "official", "input", 4000000),
        _micro_usd_row("anthropic/claude-opus-5-5", "official", "output", 20000000),
        _micro_usd_row("anthropic/claude-opus-5-5", "sell", "cache_read", 40000),
        _micro_usd_row("anthropic/claude-opus-5-5", "sell", "input", 800000),
        _micro_usd_row("anthropic/claude-opus-5-5", "sell", "output", 4000000),
    ]
    entries = parse_base_price_entries(_micro_usd_rsc(rows))
    assert len(entries) == 1
    entry = entries[0]
    # 站点售价取 sell 组；微美元换算成 USD/1M tokens
    assert entry["input"] == 0.8
    assert entry["output"] == 4.0
    assert entry["cache_read"] == 0.04
    assert entry["provider"] == "anthropic"
    assert "anthropic/claude-opus-5-5" in entry["models"]


def test_parse_micro_usd_entries_skips_models_without_sell_input_output():
    rows = [
        # 只有官方组：站点没挂售，不产出条目
        _micro_usd_row("a/only-official", "official", "input", 1000000),
        _micro_usd_row("a/only-official", "official", "output", 2000000),
        # 售组缺 output：不完整，跳过
        _micro_usd_row("a/no-output", "sell", "input", 300000),
        _micro_usd_row("a/no-output", "sell", "cache_read", 30000),
        # 非按 token 计价 / 未上架的行不算数
        _micro_usd_row("a/per-image", "sell", "input", 500000, unit="per_image"),
        _micro_usd_row("a/inactive", "sell", "input", 500000, active=False),
        _micro_usd_row("a/inactive", "sell", "output", 900000, active=False),
    ]
    assert parse_base_price_entries(_micro_usd_rsc(rows)) == []


def test_parse_micro_usd_entries_handles_double_escaped_and_dedupes():
    row = _micro_usd_row("anthropic/claude-sonnet-5", "sell", "input", 400000)
    row_out = _micro_usd_row("anthropic/claude-sonnet-5", "sell", "output", 2000000)
    text = _micro_usd_rsc([row, row_out], level=1) + _micro_usd_rsc([row], level=2)
    entries = parse_base_price_entries(text)
    assert len(entries) == 1
    assert entries[0]["input"] == 0.4
    assert entries[0]["output"] == 2.0


def test_parse_micro_usd_entries_matches_targets_via_full_or_short_id():
    from llm_price_monitor.adapters import _entry_matches_targets

    spec = SiteSpec(id="hao", models=(ModelTarget("claude-opus-5-5"),))
    rows = [
        _micro_usd_row("anthropic/claude-opus-5-5", "sell", "input", 800000),
        _micro_usd_row("anthropic/claude-opus-5-5", "sell", "output", 4000000),
    ]
    entry = parse_base_price_entries(_micro_usd_rsc(rows))[0]
    assert _entry_matches_targets(entry, spec)


def test_ai_request_slims_newapi_pricing_evidence():
    spec = SiteSpec(
        id="wild",
        adapter="browser",
        network={"url": "https://wild.test/api/pricing"},
        models=(ModelTarget("gpt-5.6-sol"),),
    )
    responses = [
        {"url": "https://wild.test/api/pricing", "status": 200, "resource_type": "fetch", "payload": {
            "success": True,
            "announcement": "促销公告" * 50,
            "data": [{
                "model_name": "gpt-5.6-sol",
                "model_ratio": 1.25,
                "completion_ratio": 4,
                "enable_groups": ["default"],
                "description": "最新模型的超长介绍文本" * 50,
                "icon": "https://wild.test/icon.png",
            }],
            "group_ratio": {"default": 1},
        }},
    ]

    body = _ai_request_body(spec, "", responses)

    request_content = body["messages"][1]["content"]
    evidence = json.loads(request_content.split("网页证据：\n", 1)[1])
    quote = evidence["network_evidence"][0]["quote"]
    assert '"model_ratio":1.25' in quote
    assert "最新模型的超长介绍文本" not in quote
    assert "促销公告" not in quote


def test_monitor_writes_snapshot_and_detects_price_change(tmp_path: Path, monkeypatch):
    payload = {"data": [{"model_name": "demo-model", "official": {"input": 1, "output": 2, "unit": "USD/1M tokens"}}]}

    def collect(*_args):
        item = payload["data"][0]
        return [PriceRecord("demo-model", item["official"]["input"], item["official"]["output"], item["official"]["unit"], "https://demo.test/pricing", 0, {})]

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config(tmp_path)), encoding="utf-8")
    config = load_config(config_path)
    store = Store(tmp_path / "monitor.db")
    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    first = run_once(config, store=store, client=httpx.Client())
    # 首轮是建档轮：价格静默入库，不发逐模型"新增"事件（避免新站点刷屏事件流）
    assert first.events == []
    assert first.records[0]["price_status"] == "confirmed"

    payload["data"][0]["official"]["output"] = 3
    second = run_once(config, store=store, client=httpx.Client())
    assert [event["kind"] for event in second.events] == ["changed"]
    assert store.count_history() == 2
    assert len(store.latest_all()) == 1


def test_per_site_persist_and_hard_timeout(tmp_path: Path, monkeypatch):
    """每站采完立即落库：站点被硬超时掐掉时，已完成站点的价格不随内存丢失；
    挂死的站点以错误收场，不拖垮整轮。网络阶段并行开工（并发槽限流），
    落库仍按站点顺序在主线程完成。"""
    def collect(self, spec, *_args):
        if spec.id == "demo":
            return [PriceRecord("demo-model", 1.0, 2.0, "USD/1M tokens", "https://demo.test/pricing", 0, {})]
        time.sleep(30)  # 模拟经代理挂死的连接：只会被硬超时掐掉，不会自己返回
        return []

    config_raw = _config(tmp_path)
    config_raw["sites"].append({
        "id": "stuck",
        "adapter": "standard",
        "model_list_url": "https://stuck.test/pricing",
        "models": ["stuck-model"],
    })
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config_raw), encoding="utf-8")
    config = load_config(config_path)
    store = Store(tmp_path / "monitor.db")
    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    monkeypatch.setattr("llm_price_monitor.report.SITE_HARD_TIMEOUT_SECONDS", 0.3)
    report = run_once(config, store=store, client=httpx.Client())

    assert "demo:demo-model:default" in store.latest_all()  # demo 已落库，不随 stuck 超时丢失
    stuck = report.site_status["stuck"]
    assert stuck["status"] == "error" and "硬上限" in str(stuck["error"])
    assert set(store.latest_all()) == {"demo:demo-model:default"}
    assert [row["site_id"] for row in report.records] == ["demo"]


def test_group_whitelist_filters_collected_prices(tmp_path: Path, monkeypatch):
    """站点分组白名单：采集层只保留选中分组的价格记录（分组名忽略大小写，缺分组视为 default）；
    白名单一个分组都匹配不上时防呆保留全量，不把站点采空。"""

    def collect_two_groups(*_args):
        return [
            PriceRecord("demo-model", 1.0, 2.0, "USD/1M tokens", "https://demo.test/pricing", 0, {"group": "svip"}),
            PriceRecord("demo-model", 3.0, 4.0, "USD/1M tokens", "https://demo.test/pricing", 0, {"group": "default"}),
        ]

    config_raw = _config(tmp_path)
    config_raw["sites"][0]["status"] = {"groups": ["svip"]}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config_raw), encoding="utf-8")
    config = load_config(config_path)
    store = Store(tmp_path / "monitor.db")
    monkeypatch.setattr(NetworkAdapter, "collect", collect_two_groups)
    report = run_once(config, store=store, client=httpx.Client())
    assert [row["metadata"]["group"] for row in report.records] == ["svip"]
    assert set(store.latest_all()) == {"demo:demo-model:svip"}

    config_raw["sites"][0]["status"] = {"groups": ["nope"]}
    config_path.write_text(json.dumps(config_raw), encoding="utf-8")
    config = load_config(config_path)
    store2 = Store(tmp_path / "monitor2.db")
    report2 = run_once(config, store=store2, client=httpx.Client())
    assert len(report2.records) == 2
    assert set(store2.latest_all()) == {"demo:demo-model:svip", "demo:demo-model:default"}


def test_distinct_price_groups_lists_collected_groups(tmp_path: Path):
    """管理台分组白名单下拉的数据源：该站点已入库价格数据里出现过的分组名（去重升序，按站点隔离）。"""
    store = Store(tmp_path / "monitor.db")
    store.append_history(
        [
            {"site_id": "demo", "model": "m1", "metadata": {"group": "vip"}, "input_price": 1, "output_price": 2, "unit": "USD/1M tokens", "captured_at": 1.0},
            {"site_id": "demo", "model": "m2", "metadata": {"group": "default"}, "input_price": 1, "output_price": 2, "unit": "USD/1M tokens", "captured_at": 2.0},
            {"site_id": "demo", "model": "m3", "metadata": {"group": "vip"}, "input_price": 1, "output_price": 2, "unit": "USD/1M tokens", "captured_at": 3.0},
            {"site_id": "demo", "model": "m4", "metadata": {}, "input_price": 1, "output_price": 2, "unit": "USD/1M tokens", "captured_at": 4.0},
            {"site_id": "other", "model": "m1", "metadata": {"group": "svip"}, "input_price": 1, "output_price": 2, "unit": "USD/1M tokens", "captured_at": 5.0},
        ]
    )
    assert store.distinct_price_groups("demo") == ["default", "vip"]
    assert store.distinct_price_groups("other") == ["svip"]
    assert store.distinct_price_groups("never-collected") == []


def test_failed_collect_keeps_last_known_price_in_snapshot(tmp_path: Path, monkeypatch):
    """本次采集只产出无价占位（需认证/无数据）时，快照沿用上次价格字段：定价页不因一次失败显示 "-"，状态如实标注。"""
    prices = {"input": 1, "output": 2}

    def collect(*_args):
        return [PriceRecord("demo-model", prices["input"], prices["output"], "USD/1M tokens", "https://demo.test/pricing", 0, {})]

    def collect_unavailable(*_args):
        return [PriceRecord("demo-model", None, None, "CNY/1M tokens", "https://demo.test/pricing", 0, {"notes": "token 过期需认证"}, "unavailable", True)]

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config(tmp_path)), encoding="utf-8")
    config = load_config(config_path)
    store = Store(tmp_path / "monitor.db")
    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    run_once(config, store=store, client=httpx.Client())

    monkeypatch.setattr(NetworkAdapter, "collect", collect_unavailable)
    failed = run_once(config, store=store, client=httpx.Client())
    snapshot = store.latest_all()["demo:demo-model:default"]

    # 状态抖动不再生成 status_changed 事件（价格数字没变）
    assert [event["kind"] for event in failed.events] == []
    assert snapshot["price_status"] == "unavailable" and snapshot["requires_auth"] is True
    assert snapshot["input_price"] == 1 and snapshot["output_price"] == 2
    assert snapshot["unit"] == "USD/1M tokens"
    assert snapshot["metadata"]["notes"] == "token 过期需认证"


def test_no_price_placeholder_never_persists_and_carry_marks_auth(tmp_path: Path, monkeypatch):
    """本次无数据：上次也没价就不入库（快照/历史都不新增）；上次有价则沿用并标"需认证"。
    旧版本遗留的无价占位行在下一轮有价采集时被清理，不再与正式记录并存。"""
    def collect_unavailable(*_args):
        return [PriceRecord("demo-model", None, None, "CNY/1M tokens", "https://demo.test/pricing", 0, {"error": "HTTP 401"}, "unavailable", True)]

    def collect(*_args):
        return [PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {})]

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config(tmp_path)), encoding="utf-8")
    config = load_config(config_path)
    store = Store(tmp_path / "monitor.db")
    # 模拟旧版本遗留：同一 site+model 留下两条无价占位行
    legacy = {"site_id": "demo", "model": "demo-model", "input_price": None, "output_price": None,
              "unit": "CNY/1M tokens", "price_status": "unavailable", "requires_auth": True,
              "captured_at": 1.0, "metadata": {"error": "HTTP 401"}}
    store.replace_latest({"demo:demo-model:default": dict(legacy), "demo:demo-model:": dict(legacy)})

    # 首轮就失败：不落任何记录
    monkeypatch.setattr(NetworkAdapter, "collect", collect_unavailable)
    run_once(config, store=store, client=httpx.Client())
    assert store.latest_all() == {} and store.count_history() == 0

    # 恢复取到价：正式记录写入，两条遗留占位被清理
    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    recovered = run_once(config, store=store, client=httpx.Client())
    assert list(store.latest_all()) == ["demo:demo-model:default"]
    assert store.count_history() == 1
    snapshot = store.latest_all()["demo:demo-model:default"]
    assert snapshot["input_price"] == 1 and snapshot["requires_auth"] is False
    # 遗留占位在上一轮持久化时已被清理，这里 previous 为空，同样按建档轮静默处理
    assert recovered.events == []

    # 再次失败：沿用上次价、状态标需认证、记录上次取到价时间，且历史不膨胀
    monkeypatch.setattr(NetworkAdapter, "collect", collect_unavailable)
    failed = run_once(config, store=store, client=httpx.Client())
    snapshot = store.latest_all()["demo:demo-model:default"]
    assert snapshot["requires_auth"] is True and snapshot["input_price"] == 1
    # last_price_at 指向上次真正取到价的那次 captured_at（本例中带价记录 captured_at=0）
    assert snapshot["last_price_at"] == 0
    assert store.count_history() == 1
    # 状态抖动不再生成 status_changed 事件（价格数字没变）
    assert [event["kind"] for event in failed.events] == []


def test_monitor_records_error_without_stopping_other_sites(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {
        "settings": {"history_file": str(tmp_path / "history.jsonl"), "latest_file": str(tmp_path / "latest.json"), "event_file": str(tmp_path / "events.jsonl")},
        "sites": [{"id": "bad", "adapter": "missing", "models": []}]
    }))
    report = run_once(config, client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))))
    assert report.records == []
    assert report.errors[0]["site_id"] == "bad"


def test_decode_response_body_defaults_to_utf8_without_charset():
    assert _decode_response_body("价格：输入 Token $1/M".encode("utf-8"), "text/x-component") == "价格：输入 Token $1/M"


def test_network_pricing_uses_newapi_ratios_without_dom_or_ai():
    spec = SiteSpec(
        id="demo",
        network={"url": "https://demo.test/pricing"},
        models=(ModelTarget("demo-model"),),
    )
    records = _network_pricing_records(spec, [{
        "url": "https://demo.test/api/pricing",
        "status": 200,
        "resource_type": "xhr",
        "payload": {
            "group_ratio": {"gpt pro": 0.5},
            "data": [{
                "model_name": "demo-model",
                "enable_groups": ["gpt pro"],
                "model_ratio": 2,
                "completion_ratio": 3,
                "cache_ratio": 0.25,
            }],
        },
    }])

    assert records[0].input_price == 2
    assert records[0].output_price == 6
    assert records[0].metadata["cache_read_price"] == 0.5
    assert records[0].metadata["adapter"] == "browser_network"
    assert records[0].metadata["page_evidence"] == []


def test_enable_groups_flicker_keeps_default_group_stable():
    """站点 enable_groups 抖动（["default"] ↔ []）时分组归一为 default，事件键不再翻转出重复"新增"。"""
    spec = SiteSpec(
        id="demo",
        network={"url": "https://demo.test/pricing"},
        models=(ModelTarget("demo-model"),),
    )

    def captured(enable_groups):
        return [{
            "url": "https://demo.test/api/pricing",
            "status": 200,
            "resource_type": "xhr",
            "payload": {
                "group_ratio": {"default": 1},
                "data": [{"model_name": "demo-model", "enable_groups": enable_groups, "model_ratio": 0.5, "completion_ratio": 2}],
            },
        }]

    first = _network_pricing_records(spec, captured(["default"]))
    second = _network_pricing_records(spec, captured([]))

    assert [record.metadata["group"] for record in first] == ["default"]
    assert [record.metadata["group"] for record in second] == ["default"]
    assert [record.input_price for record in second] == [record.input_price for record in first]


def test_network_pricing_accepts_ai_resolved_alias_for_newapi_model_name():
    spec = SiteSpec(
        id="demo",
        models=(ModelTarget("informal-gpt"),),
    )
    records = _network_pricing_records(spec, [{
        "url": "https://demo.test/api/pricing",
        "status": 200,
        "resource_type": "fetch",
        "payload": {
            "group_ratio": {"default": 1},
            "data": [{"model_name": "openai/gpt-5.6-sol", "enable_groups": ["default"], "model_ratio": 0.5, "completion_ratio": 2}],
        },
    }], {"informal-gpt": ("openai/gpt-5.6-sol", "GPT-5.6 Sol")})
    assert records[0].price_status == "confirmed"
    assert records[0].input_price == 1


def test_network_pricing_matches_site_model_name_that_omits_version():
    """站点把模型写作 deepseek-v4.1-flash，配置里是没有版本号的目录 id，仍要采到价格。"""
    spec = SiteSpec(
        id="demo",
        network={"url": "https://demo.test/pricing"},
        models=(ModelTarget("deepseek-flash"), ModelTarget("glm-5.3-flash")),
    )
    records = _network_pricing_records(spec, [{
        "url": "https://demo.test/api/pricing",
        "status": 200,
        "resource_type": "fetch",
        "payload": {
            "group_ratio": {"default": 1},
            "data": [
                {"model_name": "deepseek-v4.1-flash", "enable_groups": ["default"], "model_ratio": 0.125, "completion_ratio": 4},
                {"model_name": "glm-5.3-flash", "enable_groups": ["default"], "model_ratio": 0.5, "completion_ratio": 3},
            ],
        },
    }])
    by_model = {record.model: record for record in records}

    assert by_model["deepseek-flash"].price_status == "confirmed"
    assert by_model["deepseek-flash"].input_price == 0.25
    assert by_model["deepseek-flash"].output_price == 1
    assert by_model["deepseek-flash"].metadata["matched_model_name"] == "deepseek-v4.1-flash"
    assert by_model["glm-5.3-flash"].input_price == 1
    assert "matched_model_name" not in by_model["glm-5.3-flash"].metadata


def test_network_pricing_refuses_ambiguous_versionless_target():
    """站点同时挂着 v4 与 v4.1 两版时，没版本号的目标不猜，宁可标 unavailable。"""
    spec = SiteSpec(
        id="demo",
        network={"url": "https://demo.test/pricing"},
        models=(ModelTarget("deepseek-flash"),),
    )
    records = _network_pricing_records(spec, [{
        "url": "https://demo.test/api/pricing",
        "status": 200,
        "resource_type": "fetch",
        "payload": {
            "group_ratio": {"default": 1},
            "data": [
                {"model_name": "deepseek-v4-flash", "enable_groups": ["default"], "model_ratio": 0.125, "completion_ratio": 4},
                {"model_name": "deepseek-v4.1-flash", "enable_groups": ["default"], "model_ratio": 0.125, "completion_ratio": 4},
            ],
        },
    }])

    assert records[0].price_status == "unavailable"
    assert records[0].input_price is None


def test_network_pricing_prefers_exact_model_name_over_versionless_target():
    """精确命中优先：带版本号的目标拿到自己的条目，没版本号的目标不能抢同一个条目。"""
    spec = SiteSpec(
        id="demo",
        network={"url": "https://demo.test/pricing"},
        models=(ModelTarget("deepseek-flash"), ModelTarget("deepseek-v4-flash")),
    )
    records = _network_pricing_records(spec, [{
        "url": "https://demo.test/api/pricing",
        "status": 200,
        "resource_type": "fetch",
        "payload": {
            "group_ratio": {"default": 1},
            "data": [
                {"model_name": "deepseek-v4-flash", "enable_groups": ["default"], "model_ratio": 0.125, "completion_ratio": 4},
            ],
        },
    }])
    by_model = {record.model: record for record in records}

    assert by_model["deepseek-v4-flash"].price_status == "confirmed"
    assert by_model["deepseek-v4-flash"].input_price == 0.25
    assert by_model["deepseek-flash"].price_status == "unavailable"
    assert by_model["deepseek-flash"].input_price is None


def test_network_adapter_uses_ai_alias_before_newapi_calculation(monkeypatch):
    def fake_extract_aliases(self, spec, responses, **kwargs):
        return {"informal-gpt": {"observed_model": "openai/gpt-5.6-sol", "aliases": ["GPT-5.6 Sol"]}}

    monkeypatch.setattr("llm_price_monitor.ai.AIPriceExtractor.extract_aliases", fake_extract_aliases)
    spec = SiteSpec(
        id="demo",
        models=(ModelTarget("informal-gpt"),),
        network={"url": "https://demo.test/api/pricing"},
    )
    ai = AIConfig(enabled=True, base_url="https://ai.test/v1", models=("test-model",), api_key="key")
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "group_ratio": {"default": 1},
        "data": [{"model_name": "openai/gpt-5.6-sol", "enable_groups": ["default"], "model_ratio": 0.5, "completion_ratio": 2}],
    })))
    records = NetworkAdapter().collect(spec, client, 5, BROWSER_USER_AGENTS[0], ai)
    assert records[0].price_status == "confirmed"
    assert records[0].metadata["observed_model"] == "openai/gpt-5.6-sol"


def test_browser_adapter_requires_ai_for_non_newapi_json():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"pricing": [{"model_display": "GPT-5.5", "input_rmb": 1.5, "output_rmb": 9, "cache_read_rmb": 0.15}]})

    spec = SiteSpec(
        id="mapped",
        
        models=(ModelTarget("GPT-5.5"),),
        network={"url": "https://mapped.test/api/public-models"},
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(PriceMonitorError, match="未配置可用 AI"):
        NetworkAdapter().collect(spec, client, 5, BROWSER_USER_AGENTS[0])
    assert requests


def test_browser_adapter_passes_params_and_headers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["locale"] == "zh-CN"
        assert request.headers["x-client"] == "price-monitor"
        return httpx.Response(200, json={"data": [{"model_name": "demo-model", "input_price": 1, "output_price": 2}]})

    spec = SiteSpec(
        id="custom-request",
        models=(ModelTarget("demo-model"),),
        network={
            "url": "https://demo.test/api/pricing",
            "params": {"locale": "zh-CN"},
            "headers": {"x-client": "price-monitor"},
        },
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    records = NetworkAdapter().collect(spec, client, 5, BROWSER_USER_AGENTS[0])
    assert records[0].input_price == 1 and records[0].output_price == 2


def test_network_page_response_is_sent_to_ai_without_dom_or_browser(monkeypatch):
    seen = {}

    def fake_extract(self, spec, page_text, responses, **kwargs):
        seen["page_text"] = page_text
        seen["payload"] = responses[0].get("payload")
        return [PriceRecord("demo-model", 1, 2, "CNY/1M tokens", "", 0, {}, "confirmed")]

    monkeypatch.setattr("llm_price_monitor.ai.AIPriceExtractor.extract", fake_extract)
    spec = SiteSpec(
        id="html-page",
        models=(ModelTarget("demo-model"),),
        network={"url": "https://demo.test/pricing"},
    )
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200,
        text="<html><body>demo-model 输入价格 ¥1/M 输出价格 ¥2/M</body></html>",
        headers={"content-type": "text/html; charset=utf-8"},
    )))
    ai = AIConfig(enabled=True, base_url="https://ai.test/v1", models=("test-model",), api_key="key")
    records = NetworkAdapter().collect(spec, client, 5, BROWSER_USER_AGENTS[0], ai)
    assert records[0].input_price == 1
    assert "demo-model" in seen["page_text"]
    assert seen["payload"] is None


def test_browser_adapter_headers_sent_literally():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tk-secret"
        return httpx.Response(200, json={"data": [{"model_name": "demo-model", "input_price": 1, "output_price": 2}]})

    spec = SiteSpec(
        id="literal-header",
        models=(ModelTarget("demo-model"),),
        network={"url": "https://demo.test/api/pricing", "headers": {"Authorization": "Bearer tk-secret"}},
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    records = NetworkAdapter().collect(spec, client, 5, BROWSER_USER_AGENTS[0])
    assert records[0].input_price == 1


def test_ai_request_always_includes_newapi_oneapi_guidance():
    spec = SiteSpec(id="demo", models=(ModelTarget("demo-model"),))
    body = _ai_request_body(spec, "", [{"url": "https://demo.test/api/price", "resource_type": "fetch", "status": 200, "payload": {"items": [{"name": "demo-model", "in": 1, "out": 2}]}}])
    system = body["messages"][0]["content"]
    assert NEWAPI_ONEAPI_PRICING_GUIDANCE in system


def test_preferred_response_url_matches_price_and_model_variants():
    assert _is_preferred_response_url("https://demo.test/api/press-model-pricing")
    assert _is_preferred_response_url("https://demo.test/api/press_model")
    assert _is_preferred_response_url("https://demo.test/api/pricing")
    assert _is_preferred_response_url("https://demo.test/api/model-list")
    assert not _is_preferred_response_url("https://demo.test/api/status")
    assert not _is_preferred_response_url("https://modelflare.test/api/setup")


def test_preferred_response_bypasses_price_text_heuristic_and_is_kept_for_ai():
    spec = SiteSpec(id="demo", network={"url": "https://demo.test/pricing"}, models=(ModelTarget("demo-model"),))
    response = {
        "source": "model_list",
        "url": "https://demo.test/api/model-config",
        "resource_type": "xhr",
        "payload": {"data": [{"model_name": "demo-model", "tier": "large-context", "value": 278000}]},
    }
    body = _ai_request_body(spec, "", [response])
    evidence = json.loads(body["messages"][1]["content"].split("网页证据：\n", 1)[1])["network_evidence"]
    assert len(evidence) == 1
    assert "278000" in evidence[0]["quote"]


def test_dom_source_is_kept_even_when_target_model_card_is_not_detected():
    spec = SiteSpec(id="demo", network={"url": "https://demo.test/pricing"}, models=(ModelTarget("demo-model"),))
    content = _ai_request_body(spec, "站点动态内容尚未识别，但这里有原始 DOM 文本", [])["messages"][1]["content"]
    evidence = json.loads(content.split("网页证据：\n", 1)[1])
    assert evidence["page_evidence"][0]["quote"] == "站点动态内容尚未识别，但这里有原始 DOM 文本"


def test_decode_response_body_honors_charset_and_replaces_invalid_bytes():
    assert _decode_response_body("价格".encode("gb18030"), "text/plain; charset=gb18030") == "价格"
    assert _decode_response_body("价格".encode("utf-8") + b"\xff", "text/plain; charset=utf-8") == "价格�"


def test_repair_mojibake_does_not_change_normal_text():
    assert _repair_mojibake("正常中文和 ASCII") == "正常中文和 ASCII"
    assert _repair_mojibake("OpenAI GPT-5.6 Sol") == "OpenAI GPT-5.6 Sol"


def test_repair_mojibake_repairs_common_ssr_fragment():
    assert _repair_mojibake("OpenAI GPT-5.6 Sol APIï¼šä»·æ ¼ã€åŠŸèƒ½ä¸Žä½¿ç”¨æ–¹æ³•，查看模型 GPT-5.6 Sol API 实时价格") == "OpenAI GPT-5.6 Sol API：价格、功能与使用方法，查看模型 GPT-5.6 Sol API 实时价格"


def test_repair_mojibake_handles_spaces_inside_corrupted_fragment():
    assert _repair_mojibake("GPT-5.6 Sol API å®žæ—¶ä»·æ ¼ï¼Œä¸Šä¸‹æ–‡é•¿åº¦") == "GPT-5.6 Sol API 实时价格，上下文长度"


def test_ai_models_list_is_parsed_and_pick_model_randomly_chooses_one(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {"ai": {"base_url": "https://ai.test/v1", "models": ["m-a", "m-b", "m-a", " "]}, "settings": {}, "sites": [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}]}))
    assert config.ai.models == ("m-a", "m-b")
    for _ in range(20):
        assert config.ai.pick_model() in {"m-a", "m-b"}


def test_ai_models_rejects_non_string_entries(tmp_path: Path):
    with pytest.raises(ValueError, match="ai.models 必须是字符串数组"):
        load_config(_write_config(tmp_path, {"ai": {"base_url": "https://ai.test/v1", "models": ["m-a", 1]}, "settings": {}, "sites": [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}]}))


def test_ai_price_model_is_parsed_and_pins_price_extraction():
    """price_model 固定价格提取起始模型：留空回退随机 pick_model，两端空白等价留空。"""
    from llm_price_monitor.config import ai_from_raw

    pinned = ai_from_raw({"base_url": "https://ai.test/v1", "models": ["m-a", "m-b"], "price_model": " m-b "}, cache=None)
    assert pinned.price_model == "m-b"
    assert pinned.pick_price_model() == "m-b"

    random_only = ai_from_raw({"base_url": "https://ai.test/v1", "models": ["m-a", "m-b"]}, cache=None)
    assert random_only.price_model == ""
    assert random_only.pick_price_model() in {"m-a", "m-b"}


def test_ai_api_format_is_validated(tmp_path: Path):
    sites = [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}]
    config = load_config(_write_config(tmp_path, {"ai": {"base_url": "https://ai.test/v1", "api_format": "anthropic"}, "settings": {}, "sites": sites}))
    assert config.ai.api_format == "anthropic"

    with pytest.raises(ValueError, match="ai.api_format"):
        load_config(_write_config(tmp_path, {"ai": {"base_url": "https://ai.test/v1", "api_format": "bogus"}, "settings": {}, "sites": sites}))


def test_ai_request_builds_openai_responses_payload():
    config = AIConfig(base_url="https://api.openai.com", models=("gpt-x",), api_key="sk-oai", api_format="openai_responses")
    url, headers, body = ai_request(config, "gpt-x", "系统提示", "用户内容")
    assert url == "https://api.openai.com/v1/responses"
    assert headers["authorization"] == "Bearer sk-oai"
    assert body["input"] == [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "用户内容"},
    ]
    assert body["max_output_tokens"] == 16000
    assert body["text"] == {"format": {"type": "json_object"}}

    _, _, ping_body = ai_request(config, "gpt-x", "", "hi", max_tokens=8, json_mode=False)
    assert ping_body["input"] == [{"role": "user", "content": "hi"}]
    assert "text" not in ping_body

    # base 已带 /v1 或完整 /responses 时不再重复拼接
    assert ai_request(AIConfig(base_url="https://api.openai.com/v1", api_format="openai_responses"), "m", "", "hi")[0] == "https://api.openai.com/v1/responses"
    assert ai_request(AIConfig(base_url="https://proxy.test/v1/responses", api_format="openai_responses"), "m", "", "hi")[0] == "https://proxy.test/v1/responses"


def test_ai_content_parses_openai_responses_reply():
    payload = {"output": [{"type": "reasoning", "summary": []}, {"type": "message", "content": [{"type": "output_text", "text": "{\"models\": []}"}]}]}
    assert ai_content("openai_responses", payload) == '{"models": []}'

    with pytest.raises(AIExtractionError):
        ai_content("openai_responses", {"output": []})


def test_ai_request_builds_anthropic_messages_payload():
    config = AIConfig(base_url="https://api.anthropic.com", models=("claude-x",), api_key="sk-ant", api_format="anthropic")
    url, headers, body = ai_request(config, "claude-x", "系统提示", "用户内容")
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "sk-ant"
    assert headers["anthropic-version"] == "2023-06-01"
    assert body == {
        "model": "claude-x",
        "max_tokens": 16000,
        "temperature": 0,
        "system": "系统提示",
        "messages": [{"role": "user", "content": "用户内容"}],
    }

    _, _, ping_body = ai_request(config, "claude-x", "", "hi", max_tokens=8, json_mode=False)
    assert "system" not in ping_body
    assert "response_format" not in ping_body


def test_ai_request_builds_gemini_generate_content_payload():
    config = AIConfig(base_url="https://generativelanguage.googleapis.com", api_key="g-key", api_format="gemini")
    url, headers, body = ai_request(config, "gemini-x", "系统提示", "用户内容")
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
    assert headers["x-goog-api-key"] == "g-key"
    assert body["contents"] == [{"role": "user", "parts": [{"text": "用户内容"}]}]
    assert body["systemInstruction"] == {"parts": [{"text": "系统提示"}]}
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["maxOutputTokens"] == 16000

    # 已带 :generateContent 的地址原样使用，不再重复拼接
    full = AIConfig(base_url="https://proxy.test/v1beta/models/gemini-x:generateContent", api_format="gemini")
    url2, _, body2 = ai_request(full, "gemini-x", "", "hi")
    assert url2 == "https://proxy.test/v1beta/models/gemini-x:generateContent"
    assert "systemInstruction" not in body2
    assert body2["generationConfig"]["responseMimeType"] == "application/json"


def test_ai_content_parses_anthropic_and_gemini_replies():
    assert ai_content("anthropic", {"content": [{"type": "text", "text": "he"}, {"type": "text", "text": "llo"}]}) == "hello"
    assert ai_content("gemini", {"candidates": [{"content": {"parts": [{"text": "{\"models\": []}"}, {"text": "!"}]}}]}) == '{"models": []}!'

    with pytest.raises(AIExtractionError):
        ai_content("gemini", {"candidates": []})
    with pytest.raises(AIExtractionError):
        ai_content("anthropic", {"content": []})


def test_ai_extractor_supports_anthropic_messages_format():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps({"models": [], "cross_validation": {"status": "none", "conflicts": []}})}]})

    spec = SiteSpec(id="demo", models=(ModelTarget("demo-model"),))
    config = AIConfig(base_url="https://api.anthropic.com", models=("claude-x",), api_key="k", api_format="anthropic")
    extractor = AIPriceExtractor(config)
    records = extractor.extract(spec, "", [{"url": "https://demo.test/api/price", "resource_type": "fetch", "status": 200, "payload": {"items": [{"name": "demo-model", "in": 1, "out": 2}]}}], client=httpx.Client(transport=httpx.MockTransport(handler)))

    # AI 响应未含目标模型时按 unavailable 落一条记录
    assert [record.model for record in records] == ["demo-model"]
    assert records[0].price_status == "unavailable"
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["body"]["model"] == "claude-x"
    assert "system" in seen["body"]


def test_ai_extractor_discards_prices_for_models_missing_from_evidence():
    """模型名不在证据里时，AI 编造的价格必须清空：只降状态不清价会让幻觉价以 candidate 混进快照。

    实测案例：DaiTuAI 页面没有 MiniMax-M3，AI 自报"证据未出现具体数值"仍给出价格，每轮被
    合理性校验作废刷异常卡片。"""
    def handler(request: httpx.Request) -> httpx.Response:
        result = {"models": [
            {
                "model": "real-model",
                "observed_model": "real-model",
                "input_price": 1.5,
                "output_price": 7.5,
                "unit": "CNY/1M tokens",
                "currency": "CNY",
                "status": "candidate",
                "confidence": 0.95,
                "page_evidence": ["real-model ¥1.50 ¥7.50 / 1M tokens"],
            },
            {
                "model": "ghost-model",
                "observed_model": "ghost-model",
                "input_price": 0.0014,
                "output_price": 0.0056,
                "unit": "CNY/1M tokens",
                "currency": "CNY",
                "status": "candidate",
                "confidence": 0.8,
                "pricing_rules": {"groups": [{"name": "default", "tiers": [{"input_price": 0.0014, "output_price": 0.0056, "unit": "CNY/1M tokens"}]}]},
                "notes": "证据中未出现具体数值，仅通过 JS 结构推断字段含义",
            },
        ], "cross_validation": {"status": "none", "conflicts": []}}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="ai-secret"))
    spec = SiteSpec(id="demo", network={"url": "https://demo.test/pricing"}, models=(ModelTarget("real-model"), ModelTarget("ghost-model")))
    records = extractor.extract(
        spec, "real-model 输入 ¥1.50 输出 ¥7.50 / 1M tokens",
        [{"url": "https://demo.test/api/price", "status": 200, "resource_type": "fetch",
          "content_type": "application/json", "payload": {"models": []}}],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    by_model = {record.model: record for record in records}
    assert by_model["real-model"].price_status == "candidate"
    assert by_model["real-model"].output_price == 7.5
    # 幻觉价清空：模型名不在证据里，价格无论编得多像真的都不能落记录
    assert by_model["ghost-model"].price_status == "unavailable"
    assert by_model["ghost-model"].input_price is None
    assert by_model["ghost-model"].output_price is None
    assert "已作废" in (by_model["ghost-model"].metadata or {}).get("notes", "")


def test_ai_extractor_discards_prices_whose_digits_are_absent_from_evidence():
    """价格数字不在证据文本里时作废：模型名会被残留文案误判存在（DaiTuAI 已下线的 Kimi
    分组描述仍写着 kimi-k3），AI 还会张冠李戴（把 gpt-5.4-mini 的 ¥0.11/¥0.68 安给
    页面上不存在的 step-3.5-flash）。名字+数字双闸兜底。"""
    def handler(request: httpx.Request) -> httpx.Response:
        result = {"models": [
            {
                "model": "kimi-k3",
                "observed_model": "kimi-k3",
                "input_price": 0.11,
                "output_price": 0.68,
                "unit": "CNY/1M tokens",
                "currency": "CNY",
                "status": "confirmed",
                "confidence": 0.9,
                "network_evidence": [{"url": "https://demo.test/api/price", "quote": "kimi-k3 输入=0.11 输出=0.68"}],
                "page_evidence": ["kimi-k3 输入=0.11 输出=0.68"],
            },
        ], "cross_validation": {"status": "matched", "conflicts": []}}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="ai-secret"))
    spec = SiteSpec(id="demo", network={"url": "https://demo.test/pricing"}, models=(ModelTarget("kimi-k3"),))
    records = extractor.extract(
        spec,
        # 证据里只有 kimi-k3 的名字（残留文案），没有任何价格数字
        "Kimi 模型价格，支持 kimi-k3、kimi-k2.7-code。real-model ¥1.00 ¥5.00",
        [{"url": "https://demo.test/api/price", "status": 200, "resource_type": "fetch",
          "content_type": "application/json", "payload": {"models": []}}],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    record = records[0]
    # AI 自己补写的引用（quote 里有 0.11/0.68）不算证据：数字必须来自系统侧证据原文
    assert record.price_status == "unavailable"
    assert record.input_price is None
    assert record.output_price is None
    assert "未在证据文本中出现" in (record.metadata or {}).get("notes", "")


_ASSISTANT_TOOLS = [
    {"type": "function", "function": {"name": "get_prices", "description": "查询模型最新价格", "parameters": {"type": "object", "properties": {"site": {"type": "string"}}, "required": []}}}
]
_ASSISTANT_MESSAGES = [
    {"role": "system", "content": "你是助手"},
    {"role": "user", "content": "demo 站什么价？"},
    {"role": "assistant", "content": "我先查价格", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_prices", "arguments": '{"site": "demo"}'}}]},
    {"role": "tool", "tool_call_id": "c1", "content": '{"prices": []}'},
]


def test_ai_request_builds_anthropic_tools_payload():
    """anthropic 的多轮消息与工具定义转换：system 提顶层、tool_use/tool_result 块、input_schema。"""
    config = AIConfig(base_url="https://ai.test/v1", models=("m",), api_key="k", api_format="anthropic")
    url, headers, body = ai_request(config, "m", "sys", "user", messages=_ASSISTANT_MESSAGES, tools=_ASSISTANT_TOOLS)
    assert url.endswith("/v1/messages")
    assert headers["x-api-key"] == "k"
    assert body["system"] == "你是助手"
    assert body["tools"] == [{"name": "get_prices", "description": "查询模型最新价格", "input_schema": {"type": "object", "properties": {"site": {"type": "string"}}, "required": []}}]
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "demo 站什么价？"}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "我先查价格"},
            {"type": "tool_use", "id": "c1", "name": "get_prices", "input": {"site": "demo"}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": '{"prices": []}'}]},
    ]


def test_ai_request_builds_responses_tools_payload():
    """Responses API 的多轮消息与工具定义转换：instructions 提顶层、function_call/function_call_output 项。"""
    config = AIConfig(base_url="https://ai.test/v1", models=("m",), api_key="k", api_format="openai_responses")
    url, headers, body = ai_request(config, "m", "sys", "user", messages=_ASSISTANT_MESSAGES, tools=_ASSISTANT_TOOLS)
    assert url.endswith("/v1/responses")
    assert headers["authorization"] == "Bearer k"
    assert body["instructions"] == "你是助手"
    assert body["tools"] == [{"type": "function", "name": "get_prices", "description": "查询模型最新价格", "parameters": {"type": "object", "properties": {"site": {"type": "string"}}, "required": []}}]
    assert body["input"] == [
        {"role": "user", "content": "demo 站什么价？"},
        {"role": "assistant", "content": "我先查价格"},
        {"type": "function_call", "call_id": "c1", "name": "get_prices", "arguments": '{"site": "demo"}'},
        {"type": "function_call_output", "call_id": "c1", "output": '{"prices": []}'},
    ]


def test_ai_request_builds_gemini_tools_payload():
    """Gemini 的多轮消息与工具定义转换：systemInstruction、functionCall/functionResponse 部件。"""
    config = AIConfig(base_url="https://ai.test/v1", models=("m",), api_key="k", api_format="gemini")
    url, headers, body = ai_request(config, "m", "sys", "user", messages=_ASSISTANT_MESSAGES, tools=_ASSISTANT_TOOLS)
    assert url.endswith(":generateContent")
    assert headers["x-goog-api-key"] == "k"
    assert body["systemInstruction"] == {"parts": [{"text": "你是助手"}]}
    assert body["tools"] == [{"functionDeclarations": [{"name": "get_prices", "description": "查询模型最新价格", "parameters": {"type": "object", "properties": {"site": {"type": "string"}}, "required": []}}]}]
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "demo 站什么价？"}]},
        {"role": "model", "parts": [{"text": "我先查价格"}, {"functionCall": {"name": "get_prices", "args": {"site": "demo"}, "id": "c1"}}]},
        {"role": "user", "parts": [{"functionResponse": {"name": "get_prices", "response": {"result": '{"prices": []}'}, "id": "c1"}}]},
    ]


def test_ai_request_uses_random_model_from_models_list():
    spec = SiteSpec(id="demo", models=(ModelTarget("demo-model"),))
    config = AIConfig(base_url="https://ai.test/v1", models=("m-a", "m-b"), api_key="k")
    for _ in range(20):
        body = _ai_request_body(spec, "", [{"url": "https://demo.test/api/price", "resource_type": "fetch", "status": 200, "payload": {"items": [{"name": "demo-model", "in": 1, "out": 2}]}}], config=config)
        assert body["model"] in {"m-a", "m-b"}


def test_ai_stream_error_carries_response_body(monkeypatch):
    """供应商在流式请求上回 HTTP 错误时，异常要带上响应体里的具体原因。

    回归背景：流式响应未读正文就 raise_for_status，错误处理里取 .text 抛 ResponseNotRead，
    把"403 免费额度耗尽"这类真实原因吞成了莫名的前端报错。
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "Free quota exhausted", "code": "AllocationQuota.FreeTierOnly"}})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    config = AIConfig(base_url="https://ai.test/v1", models=("m-a",), api_key="k", enable_thinking=False)
    with pytest.raises(AIExtractionError) as exc_info:
        list(ai_stream_fallback(config, "", "hi", scene="测试"))
    # 透出的是解析后的报错要点，不是整段 JSON 原文
    assert "Free quota exhausted" in str(exc_info.value)
    assert '"error"' not in str(exc_info.value)


def test_ai_stream_transport_error_is_logged(monkeypatch):
    """流式请求的传输错误同样留痕：未出字按连接抖动记录，池子耗尽补一条整次失败。"""
    import llm_price_monitor.ai as ai_mod

    rows: list[dict] = []
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: rows.append(fields))

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    config = AIConfig(base_url="https://ai.test/v1", models=("m-a",), api_key="k")
    with pytest.raises(AIExtractionError) as exc_info:
        list(ai_stream_fallback(config, "", "hi", scene="测试"))
    assert "timed out" in str(exc_info.value)
    assert [(row["model"], row["status"]) for row in rows] == [("m-a", "transport"), ("m-a", "error")]


def _sse_response(chunks: list[dict]) -> httpx.Response:
    lines = "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=lines.encode("utf-8"))


def test_ai_stream_messages_pseudo_tool_call_falls_back_to_next_model(monkeypatch):
    """模型不走 tool_calls 协议、把调用过程当正文“演”出来：按失败换下一个模型，假动作文本不漏给用户。

    回归背景：qwen-vl-max 对“Claude 和 GPT 哪个便宜”回过一段“调用工具：get_model_price(...)”
    的解说正文且无真实 tool_calls，工具循环把它当最终答案流给了用户，看起来像卡住。
    """
    import llm_price_monitor.ai as ai_mod

    rows: list[dict] = []
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: rows.append(fields))
    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)  # 固定模型顺序 m-a → m-b

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["model"] == "m-a":
            return _sse_response([{"choices": [{"delta": {"content": "正在查询…\n\n调用工具：get_model_price(\"Cloud\")"}}]}])
        return _sse_response([{"choices": [{"delta": {"content": "Claude 的输入价更低。"}}]}])

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    config = AIConfig(base_url="https://ai.test/v1", models=("m-a", "m-b"), api_key="k")
    failures: list[tuple[str, str]] = []
    events = list(ai_stream_messages_fallback(
        config, [{"role": "user", "content": "Claude 和 GPT 哪个便宜？"}], [{"type": "function", "function": {"name": "get_prices"}}], scene="测试",
        on_model_failure=lambda model, error: failures.append((model, error)),
    ))
    text = "".join(event["text"] for event in events if event["type"] == "delta")
    assert text == "Claude 的输入价更低。"
    assert events[-1]["type"] == "finish" and events[-1]["tool_calls"] == []
    assert [(row["model"], row["status"]) for row in rows] == [("m-a", "fallback"), ("m-b", "ok")]
    assert "tool_calls" in rows[0]["error"]
    assert failures == [("m-a", "模型把工具调用当正文输出，未走 tool_calls 协议")]  # 冷却名单回调


def test_ai_stream_messages_anthropic_tool_events(monkeypatch):
    """anthropic 流式工具调用：content_block_start/input_json_delta 拼装成完整 tool_calls。"""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path.endswith("/v1/messages")
        assert body["stream"] is True and body["tools"][0]["name"] == "get_prices"
        chunks = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "get_prices"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{\"site\": \"de"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "mo\"}"}},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 5}},
        ]
        return _sse_response(chunks)

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    config = AIConfig(base_url="https://ai.test/v1", models=("m",), api_key="k", api_format="anthropic")
    events = list(ai_stream_messages_fallback(config, _ASSISTANT_MESSAGES, _ASSISTANT_TOOLS, scene="测试"))
    assert [event["type"] for event in events] == ["finish"]
    assert events[0]["tool_calls"] == [{"id": "toolu_1", "type": "function", "name": "get_prices", "arguments": '{"site": "demo"}'}]
    assert events[0]["usage"]["prompt_tokens"] == 10 and events[0]["usage"]["completion_tokens"] == 5


def test_ai_stream_messages_responses_tool_events(monkeypatch):
    """Responses API 流式工具调用：output_item.added + function_call_arguments.delta 拼装。"""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/responses")
        chunks = [
            {"type": "response.output_item.added", "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "get_prices", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "call_id": "call_1", "delta": "{\"site\": "},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "call_id": "call_1", "delta": "\"demo\"}"},
            {"type": "response.completed", "response": {"usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}}},
        ]
        return _sse_response(chunks)

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    config = AIConfig(base_url="https://ai.test/v1", models=("m",), api_key="k", api_format="openai_responses")
    events = list(ai_stream_messages_fallback(config, _ASSISTANT_MESSAGES, _ASSISTANT_TOOLS, scene="测试"))
    assert [event["type"] for event in events] == ["finish"]
    assert events[0]["tool_calls"] == [{"id": "call_1", "type": "function", "name": "get_prices", "arguments": '{"site": "demo"}'}]
    assert events[0]["usage"]["total_tokens"] == 10


def test_ai_stream_messages_gemini_tool_events(monkeypatch):
    """Gemini 流式：文本 part 产出增量，functionCall part（参数是完整对象）拼成 tool_calls。"""
    def handler(request: httpx.Request) -> httpx.Response:
        assert ":streamGenerateContent" in str(request.url)
        chunks = [
            {"candidates": [{"content": {"parts": [{"text": "查一下"}]}}]},
            {"candidates": [{"content": {"parts": [{"functionCall": {"name": "get_prices", "args": {"site": "demo"}}}]}}]},
            {"usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 4, "totalTokenCount": 12}},
        ]
        return _sse_response(chunks)

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    config = AIConfig(base_url="https://ai.test/v1", models=("m",), api_key="k", api_format="gemini")
    events = list(ai_stream_messages_fallback(config, _ASSISTANT_MESSAGES, _ASSISTANT_TOOLS, scene="测试"))
    # 工具轮的文本部件按解说处理不外流（防伪调用泄漏），只产出 finish
    assert [event["type"] for event in events] == ["finish"]
    finish = events[0]
    assert finish["tool_calls"] == [{"id": "tool_0", "type": "function", "name": "get_prices", "arguments": '{"site": "demo"}'}]
    assert finish["usage"]["total_tokens"] == 12


def test_ai_stream_messages_tool_round_narration_is_not_streamed(monkeypatch):
    """带工具轮的解说正文只留在会话上下文里，用户只收到 finish（真实工具调用）。"""
    import llm_price_monitor.ai as ai_mod

    rows: list[dict] = []
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: rows.append(fields))
    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)

    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            {"choices": [{"delta": {"content": "我先查一下价格。"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "get_prices", "arguments": "{\"site\":"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": " \"demo\"}"}}]}}]},
        ]
        return _sse_response(chunks)

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    config = AIConfig(base_url="https://ai.test/v1", models=("m-a",), api_key="k")
    events = list(ai_stream_messages_fallback(
        config, [{"role": "user", "content": "demo 站现在什么价？"}], [{"type": "function", "function": {"name": "get_prices"}}], scene="测试",
    ))
    assert [event["type"] for event in events] == ["finish"]
    assert events[0]["tool_calls"][0]["name"] == "get_prices"
    assert events[0]["tool_calls"][0]["arguments"] == '{"site": "demo"}'
    assert rows[0]["status"] == "ok"
    assert rows[0]["response_excerpt"] == "我先查一下价格。"


def test_ai_transport_error_is_logged(monkeypatch):
    """传输层失败（超时/连接重置/SSL EOF）也要落 AI 日志并按渠道故障换模型。

    回归背景：以前只捕获 HTTPStatusError，传输错误直接往上抛，AI 日志一条不留，
    报错在成功率里彻底隐形（SSL 抖动环境下尤为常见）。
    """
    import llm_price_monitor.ai as ai_mod

    rows: list[dict] = []
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: rows.append(fields))

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset by peer", request=request)

    config = AIConfig(base_url="https://ai.test/v1", models=("m-a",), api_key="k")
    with pytest.raises(httpx.ConnectError):
        request_with_model_fallback(config, "", "hi", scene="测试", client=httpx.Client(transport=httpx.MockTransport(handler)))
    # 单模型池：先留一条「连接抖动」痕迹（不算报错失败），池子耗尽再补一条整次失败
    assert [(row["model"], row["status"]) for row in rows] == [("m-a", "transport"), ("m-a", "error")]
    assert "connection reset by peer" in rows[0]["error"]
    assert "模型池全部失败" in rows[1]["error"]


def test_ai_fallback_respects_deadline():
    """换模型重试受总时长预算约束：预算已过就立刻中止，不再逐模型把时间烧光。"""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset by peer", request=request)

    config = AIConfig(base_url="https://ai.test/v1", models=("m-a", "m-b"), api_key="k")
    with pytest.raises(AIExtractionError) as exc_info:
        request_with_model_fallback(
            config, "", "hi", scene="测试", client=httpx.Client(transport=httpx.MockTransport(handler)),
            deadline=time.monotonic() - 1,
        )
    assert "时间预算" in str(exc_info.value)


def test_ai_fallback_pool_puts_preferred_model_first():
    """preferred_model 固定排首位（不在池里也照样先试），其余池子乱序作后备。"""
    import llm_price_monitor.ai as ai_mod

    config = AIConfig(base_url="https://ai.test/v1", models=("m-a", "m-b"), api_key="k")
    in_pool = ai_mod._fallback_pool(config, "m-a")
    assert in_pool[0] == "m-a"
    assert sorted(in_pool) == ["m-a", "m-b"]
    outside = ai_mod._fallback_pool(config, "m-c")
    assert outside[0] == "m-c"
    assert sorted(outside) == ["m-a", "m-b", "m-c"]
    assert ai_mod._fallback_pool(config)[0] in {"m-a", "m-b"}


def test_ai_preferred_model_tried_first_then_pool_fallback(monkeypatch):
    """价格提取固定模型：preferred_model 先试，失败仍按池子换，不因固定模型丢掉兜底。"""
    import llm_price_monitor.ai as ai_mod

    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: None)
    tried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.read())["model"]
        tried.append(model)
        if model == "m-c":
            raise httpx.ConnectError("connection reset by peer", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    config = AIConfig(base_url="https://ai.test/v1", models=("m-a", "m-b"), api_key="k")
    model, _ = request_with_model_fallback(
        config, "", "hi", scene="测试", client=httpx.Client(transport=httpx.MockTransport(handler)), preferred_model="m-c",
    )
    assert tried[0] == "m-c"
    assert model in {"m-a", "m-b"}


def test_ai_prompt_too_long_falls_back_to_next_model(monkeypatch):
    """prompt 超出单模型上下文按模型级故障换下一个，不再判死整轮：池内模型上下文差异大，
    小上下文模型（如 7b 蒸馏 32k）装不下不代表 129k 的模型装不下。"""
    import llm_price_monitor.ai as ai_mod

    rows: list[dict] = []
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: rows.append(fields))
    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.read())["model"] == "m-a":
            return httpx.Response(400, json={"error": {"message": "<400> InternalError.Algo.InvalidParameter: Range of input length should be [1, 32768]"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    config = AIConfig(base_url="https://ai.test/v1", models=("m-a", "m-b"), api_key="k")
    model, _ = request_with_model_fallback(config, "", "hi", scene="测试", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert model == "m-b"
    assert [(row["model"], row["status"]) for row in rows] == [("m-a", "fallback"), ("m-b", "ok")]
    assert "prompt 超出该模型上下文上限" in rows[0]["error"]


def test_ai_max_tokens_range_error_retries_with_clamped_budget(monkeypatch):
    """max_tokens 超出模型上限：解析报错里的上限同模型降额重试一次，并缓存供后续请求直接按上限构造。"""
    import llm_price_monitor.ai as ai_mod

    monkeypatch.setattr(ai_mod, "_MODEL_MAX_TOKENS_LIMIT", {})
    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)
    rows: list[dict] = []
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: rows.append(fields))
    bodies: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        bodies.append(body["max_tokens"])
        if len(bodies) == 1:
            return httpx.Response(400, json={"error": {"message": "Range of max_tokens should be [1, 2000]"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    config = AIConfig(base_url="https://ai.test/v1", models=("m-a",), api_key="k", max_tokens=4000)
    model, _ = request_with_model_fallback(config, "", "hi", scene="测试", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert model == "m-a"
    assert bodies == [4000, 2000]
    assert [(row["model"], row["status"]) for row in rows] == [("m-a", "param_retry"), ("m-a", "ok")]
    # 学到的上限缓存生效：之后的请求经 ai_request 直接按上限构造，不再白发那次 400
    _, _, clamped_body = ai_mod.ai_request(config, "m-a", "", "hi")
    assert clamped_body["max_tokens"] == 2000


def test_ai_learned_limit_persists_and_preloads(monkeypatch):
    """学到的 max_tokens 上限经 saver 落库、load_model_limits 预载：重启后每个模型不再白发降额 400。"""
    import llm_price_monitor.ai as ai_mod

    saved: dict[str, int] = {}
    monkeypatch.setattr(ai_mod, "_MODEL_MAX_TOKENS_LIMIT", {})
    monkeypatch.setattr(ai_mod, "model_limits_saver", lambda model, limit: saved.__setitem__(model, limit))
    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: None)
    bodies: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        bodies.append(body["max_tokens"])
        if len(bodies) == 1:
            return httpx.Response(400, json={"error": {"message": "Range of max_tokens should be [1, 8192]"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    config = AIConfig(base_url="https://ai.test/v1", models=("m-a",), api_key="k")
    request_with_model_fallback(config, "", "hi", scene="测试", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert saved == {"m-a": 8192}

    # 重启等价：内存缓存清空后从库里预载，同一请求直接按 8192 构造，不再出现 400
    monkeypatch.setattr(ai_mod, "_MODEL_MAX_TOKENS_LIMIT", {})
    monkeypatch.setattr(ai_mod, "model_limits_loader", lambda: saved)
    ai_mod.load_model_limits()
    _, _, body = ai_mod.ai_request(config, "m-a", "", "hi")
    assert body["max_tokens"] == 8192
    assert bodies == [16000, 8192]


def test_ai_thinking_required_persists_and_preloads(monkeypatch):
    """拒收 enable_thinking=false 的模型经 saver 落库、load_thinking_models 预载：重启后不再白发翻参 400。"""
    import llm_price_monitor.ai as ai_mod

    saved: list[str] = []
    monkeypatch.setattr(ai_mod, "_MODEL_THINKING_REQUIRED", set())
    monkeypatch.setattr(ai_mod, "thinking_models_saver", lambda model: saved.append(model))
    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: None)
    flags: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        flags.append(body["enable_thinking"])
        if body["enable_thinking"] is False:
            return httpx.Response(400, json={"error": {"message": "The value of the enable_thinking parameter is restricted to True."}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    config = AIConfig(base_url="https://ai.test/v1", models=("think-only",), api_key="k", enable_thinking=False)
    request_with_model_fallback(config, "", "hi", scene="测试", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert saved == ["think-only"]
    assert flags == [False, True]  # 先白发一次 400，翻参后成功

    # 重启等价：清内存后从库里预载，请求直接按 enable_thinking=true 构造，不再出现那次 400
    monkeypatch.setattr(ai_mod, "_MODEL_THINKING_REQUIRED", set())
    monkeypatch.setattr(ai_mod, "thinking_models_loader", lambda: saved)
    ai_mod.load_thinking_models()
    _, _, body = ai_mod.ai_request(config, "think-only", "", "hi")
    assert body["enable_thinking"] is True
    assert flags == [False, True]


def test_ai_fallback_skips_models_with_too_small_limit(monkeypatch):
    """已学上限低于最低单批预算的模型按规格过小剔除；请求预算本身更小时不误剔。"""
    import llm_price_monitor.ai as ai_mod

    monkeypatch.setattr(ai_mod, "_MODEL_MAX_TOKENS_LIMIT", {"tiny": 2000})
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: None)
    tried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.read())["model"]
        tried.append(model)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    # 大预算请求（价格抽取 16000）：tiny 的 2000 连一批 JSON 都装不下，直接跳过
    config = AIConfig(base_url="https://ai.test/v1", models=("tiny", "big"), api_key="k", price_model="tiny")
    model, _ = request_with_model_fallback(
        config, "", "hi", scene="测试", client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert model == "big"
    assert "tiny" not in tried

    # 小预算请求（ping 用 8）：2000 装得下，不误剔
    tried.clear()
    config_small = AIConfig(base_url="https://ai.test/v1", models=("tiny",), api_key="k")
    model, _ = request_with_model_fallback(
        config_small, "", "hi", scene="测试", client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_tokens=8, json_mode=False,
    )
    assert model == "tiny"
    assert tried == ["tiny"]


def test_ai_http_client_bypasses_env_proxy(monkeypatch):
    """AI 请求默认直连：环境变量代理（本地 Clash 等）不再接管，trust_env 关闭、不传代理。"""
    import llm_price_monitor.ai as ai_mod

    monkeypatch.setenv("http_proxy", "http://127.0.0.1:7897")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7897")
    monkeypatch.setenv("all_proxy", "http://127.0.0.1:7897")
    captured: dict = {}
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    def spy_client(*args, **kwargs):
        captured.update(kwargs)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_mod.httpx, "Client", spy_client)
    config = AIConfig(base_url="https://ai.test/v1", models=("m-a",), api_key="k")
    model, _ = request_with_model_fallback(config, "", "hi", scene="测试")
    assert model == "m-a"
    assert captured["trust_env"] is False
    assert captured["proxy"] is None


def test_ai_http_client_honors_configured_proxy(monkeypatch):
    """ai.proxy 配置了代理时显式走该代理（base_url 在海外需要代理的场景）。"""
    import llm_price_monitor.ai as ai_mod

    captured: dict = {}
    real_client = httpx.Client

    def spy_client(*args, **kwargs):
        captured.update(kwargs)
        return real_client(**kwargs)

    monkeypatch.setattr(ai_mod.httpx, "Client", spy_client)
    client = ai_mod.ai_http_client(AIConfig(base_url="https://ai.test/v1", models=("m",), proxy="http://127.0.0.1:7897"))
    client.close()
    assert captured["proxy"] == "http://127.0.0.1:7897"
    assert captured["trust_env"] is False


def test_ai_config_proxy_parsing():
    from llm_price_monitor.config import ai_from_raw

    base = {"base_url": "https://ai.test/v1", "models": ["m"], "api_key": "k"}
    assert ai_from_raw({**base, "proxy": " http://127.0.0.1:7897 "}, cache=None).proxy == "http://127.0.0.1:7897"
    assert ai_from_raw({**base}, cache=None).proxy is None
    assert ai_from_raw({**base, "proxy": ""}, cache=None).proxy is None
    with pytest.raises(ValueError, match="ai.proxy"):
        ai_from_raw({**base, "proxy": "ftp://127.0.0.1:7897"}, cache=None)


def test_ai_extract_batch_loop_stops_at_budget(monkeypatch):
    """整轮提取共享单站预算：预算耗尽后剩余批次不再发起，而不是把一轮采集拖到小时级。"""
    import llm_price_monitor.ai as ai_mod

    monkeypatch.setattr(ai_mod, "SITE_AI_BUDGET_SECONDS", 0)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        result = {"models": [{
            "model": "demo-model",
            "input_price": 5,
            "output_price": 30,
            "unit": "USD/1M tokens",
            "currency": "USD",
            "status": "confirmed",
            "confidence": 0.99,
            "network_evidence": [{"url": "https://demo.test/api/price", "quote": "input=5 output=30"}],
            "page_evidence": ["demo-model Input 5 Output 30 USD/1M tokens"],
            "notes": "",
        }], "cross_validation": {"status": "matched", "conflicts": []}}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="ai-secret"))
    spec = SiteSpec(
        id="demo", adapter="browser", network={"url": "https://demo.test/pricing"},
        models=tuple(ModelTarget(name) for name in ("m1", "m2", "m3", "m4", "m5")),
    )
    with pytest.raises(AIExtractionError) as exc_info:
        extractor.extract(
            spec, "",
            [{"url": "https://demo.test/api/price", "status": 200, "resource_type": "fetch",
              "content_type": "application/json", "payload": {"models": []}}],
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
    # 预算在批循环入口检查：一个请求都不该发出去
    assert calls["count"] == 0
    assert "预算" in str(exc_info.value)


def test_ai_extract_budget_scales_with_batch_count(monkeypatch):
    """预算随批次数等比放大：5 模型 2 批 → 2×SITE_AI_BUDGET_SECONDS，第一批正常跑完。"""
    import llm_price_monitor.ai as ai_mod

    budget = 600
    monkeypatch.setattr(ai_mod, "SITE_AI_BUDGET_SECONDS", budget)
    deadline_seen: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        result = {"models": [], "cross_validation": {"status": "none", "conflicts": []}}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    # max_tokens=4000 → 4 条/批，5 个模型正好 2 批
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="ai-secret", max_tokens=4000))
    real_fallback = ai_mod.request_with_model_fallback

    def spy_fallback(*args, **kwargs):
        deadline_seen.append(kwargs.get("deadline"))
        return real_fallback(*args, **kwargs)

    monkeypatch.setattr(ai_mod, "request_with_model_fallback", spy_fallback)
    spec = SiteSpec(
        id="demo", adapter="browser", network={"url": "https://demo.test/pricing"},
        models=tuple(ModelTarget(name) for name in ("m1", "m2", "m3", "m4", "m5")),
    )
    extractor.extract(
        spec, "",
        [{"url": "https://demo.test/api/price", "status": 200, "resource_type": "fetch",
          "content_type": "application/json", "payload": {"models": []}}],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    # extract 拿 5 个目标模型起 2 个批次，请求入口的 deadline 都是起点 + 2×预算
    assert len(deadline_seen) >= 1
    assert deadline_seen[0] - ai_mod.time.monotonic() <= 2 * budget
    assert deadline_seen[0] - ai_mod.time.monotonic() > budget


def test_ai_extract_budget_exhaustion_keeps_completed_batches(monkeypatch):
    """预算耗尽保留已完成批次：不再整轮作废，剩余批次不再发起，日志留痕。"""
    import llm_price_monitor.ai as ai_mod
    from llm_price_monitor import tasklog

    class FakeClock:
        now = 0.0

        def monotonic(self) -> float:
            return self.now

    clock = FakeClock()
    monkeypatch.setattr(ai_mod.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(ai_mod, "SITE_AI_BUDGET_SECONDS", 100)
    logs: list[tuple[str, str]] = []
    tasklog.bind(lambda message, level: logs.append((message, level)))
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        clock.now = 500.0  # 第一批请求把时钟推过 2 批总预算 200s，第二批在入口被拦下
        result = {"models": [{
            "model": "demo-model",
            "input_price": 5,
            "output_price": 30,
            "unit": "USD/1M tokens",
            "currency": "USD",
            "status": "confirmed",
            "confidence": 0.99,
            "network_evidence": [{"url": "https://demo.test/api/price", "quote": "input=5 output=30"}],
            "page_evidence": ["demo-model Input 5 Output 30 USD/1M tokens"],
            "notes": "",
        }], "cross_validation": {"status": "matched", "conflicts": []}}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    try:
        # max_tokens=4000 → 4 条/批：5 个模型拆 2 批，第一批把时钟推过总预算后第二批被入口拦下
        extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="ai-secret", max_tokens=4000))
        spec = SiteSpec(
            id="demo", adapter="browser", network={"url": "https://demo.test/pricing"},
            models=tuple(ModelTarget(name) for name in ("demo-model", "m2", "m3", "m4", "m5")),
        )
        records = extractor.extract(
            spec, "",
            [{"url": "https://demo.test/api/price", "status": 200, "resource_type": "fetch",
              "content_type": "application/json", "payload": {"models": []}}],
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
    finally:
        tasklog.unbind()
    assert calls["count"] == 1  # 第二批没有发起
    assert len(records) >= 1  # 第一批的成果保留
    assert any("保留已完成" in message and "剩余批次不再发起" in message for message, _ in logs)


def test_ai_extract_switches_model_on_bad_models_shape(monkeypatch):
    """解析得出但缺 models 数组的坏 JSON（实测 kimi 输出过）在校验层拦下换下一个模型，不再炸整轮提取。"""
    import llm_price_monitor.ai as ai_mod

    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)
    # price_model 显式钉住坏模型作起点：提取链路会把起始模型作为 preferred 注入请求顺序，留空会随机选导致用例抖动
    config = AIConfig(base_url="https://ai.test/v1", models=("garbage-model", "good-model"), api_key="k", price_model="garbage-model")
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        called.append(body["model"])
        # 实测坏响应形态：合法 JSON 对象，但 models 是字符串
        content = '{"models":":[{","model":"demo-model"}' if body["model"] == "garbage-model" else json.dumps({"models": []})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    extractor = AIPriceExtractor(config)
    spec = SiteSpec(
        id="demo", adapter="browser", network={"url": "https://demo.test/pricing"},
        models=tuple(ModelTarget(name) for name in ("demo-model",)),
    )
    records = extractor.extract(
        spec, "",
        [{"url": "https://demo.test/api/price", "status": 200, "resource_type": "fetch",
          "content_type": "application/json", "payload": {"models": []}}],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert called == ["garbage-model", "good-model"]  # 坏模型被换掉，整轮没有失败
    # good-model 返回空 models：预期模型按既有口径落 unavailable 占位，而不是整轮报错
    assert len(records) == 1
    assert records[0].price_status == "unavailable"


def test_provider_error_detail_parses_each_provider_shape():
    """任意供应商的错误形态都解析成 message 要点：JSON 递归找消息字段，HTML 剥标签，纯文本保留。"""
    def response(body: str, status: int = 403) -> httpx.Response:
        return httpx.Response(status, content=body.encode("utf-8"))

    # OpenAI / DashScope：error.message + 语义错误码
    assert provider_error_detail(response(
        '{"error":{"message":"Free quota exhausted","type":"AllocationQuota.FreeTierOnly","code":null}}',
    )) == "HTTP 403：Free quota exhausted（AllocationQuota.FreeTierOnly）"
    # Anthropic：error.type 作错误码
    assert provider_error_detail(response(
        '{"type":"error","error":{"type":"not_found_error","message":"model not found"}}', 404,
    )) == "HTTP 404：model not found（not_found_error）"
    # Gemini：数字 code 与状态码重复，只展示语义 status
    assert provider_error_detail(response(
        '{"error":{"code":429,"message":"Quota exceeded","status":"RESOURCE_EXHAUSTED"}}', 429,
    )) == "HTTP 429：Quota exceeded（RESOURCE_EXHAUSTED）"
    # FastAPI 风格：顶层 detail 字符串
    assert provider_error_detail(response('{"detail":"Not authenticated"}', 401)) == "HTTP 401：Not authenticated"
    # new-api 风格：error 直接是字符串
    assert provider_error_detail(response('{"error":"令牌额度已用完"}', 402)) == "HTTP 402：令牌额度已用完"
    # Google 风格：errors 数组
    assert provider_error_detail(response(
        '{"error":{"errors":[{"message":"billing account missing"}]}}',
    )) == "HTTP 403：billing account missing"
    # FastAPI 校验错误：detail 是数组，递归取 msg
    assert provider_error_detail(response(
        '{"detail":[{"loc":["body","question"],"msg":"field required"}]}', 422,
    )) == "HTTP 422：field required"
    # 网关 HTML 错误页：剥掉标签
    assert provider_error_detail(response("<html><body><h1>502 Bad Gateway</h1></body></html>", 502)) == "HTTP 502：502 Bad Gateway"
    # 纯文本
    assert provider_error_detail(response("服务繁忙，请稍后再试", 503)) == "HTTP 503：服务繁忙，请稍后再试"


def test_ai_api_key_comes_from_config(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {
        "ai": {
            "base_url": "https://config-ai.test/v1",
            "models": ["config-model"],
            "api_key": "secret",
        },
        "settings": {},
        "sites": [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}],
    }))
    assert config.ai.api_key == "secret"


def test_ai_config_loads_bounded_non_thinking_output(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {
        "ai": {
            "base_url": "https://config-ai.test/v1",
            "models": ["config-model"],
            "max_tokens": 4000,
            "enable_thinking": False,
        },
        "settings": {},
        "sites": [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}],
    }))

    assert config.ai.max_tokens == 4000
    assert config.ai.enable_thinking is False


def test_id_only_site_is_an_disabled_placeholder(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {
        "settings": {},
        "sites": [{"id": "placeholder"}],
    }))

    assert config.sites[0].id == "placeholder"
    assert config.sites[0].adapter == "standard"
    assert config.sites[0].enabled is False


def test_target_model_aliases_select_one_page_card():
    page_text = (
        "openai/gpt-5.6-sol 输入 Token $1/M 输出 Token $2/M "
        "anthropic/claude-4 输入 Token $3/M 输出 Token $4/M"
    )

    filtered = _target_page_text(page_text, ["gpt-5.6-sol"])

    assert "openai/gpt-5.6-sol" in filtered
    assert "anthropic/claude-4" not in filtered


def test_browser_adapter_requires_explicit_target_model():
    with pytest.raises(PriceMonitorError, match="未配置目标模型 models"):
        NetworkAdapter().collect(
            SiteSpec(id="demo", adapter="browser", network={"url": "https://demo.test/pricing"}),
            httpx.Client(),
            1,
            BROWSER_USER_AGENTS[0],
            AIConfig(base_url="https://ai.test/v1", models=("test-model",)),
        )


def test_site_request_headers_are_loaded_and_merged(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {
        "settings": {},
        "sites": [{
            "id": "demo",
            "adapter": "standard",
            "model_list_url": "https://demo.test/pricing",
            "request_headers": {"x-tenant": "tenant-a", "x-region": "cn"},
            "models": ["demo-model"],
        }],
    }))

    spec = config.sites[0]
    headers = _headers(spec, "UA-test")
    assert headers["x-tenant"] == "tenant-a"
    assert headers["x-region"] == "cn"
    assert headers["user-agent"] == "UA-test"


def test_user_agent_is_fixed_by_default_and_can_be_randomized_once(tmp_path: Path, monkeypatch):
    config = load_config(_write_config(tmp_path, {"settings": {}, "sites": [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}]}))
    assert choose_user_agent(config) == BROWSER_USER_AGENTS[0]
    monkeypatch.setattr("llm_price_monitor.useragent.secrets.choice", lambda values: values[-1])
    randomized = load_config(_write_config(tmp_path, {"settings": {"random_user_agent": True}, "sites": [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}]}))
    selected = choose_user_agent(randomized)
    assert "Macintosh" in selected
    assert "Chrome/153.0.0.0" in selected


def test_random_user_agent_can_limit_platforms_and_versions(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {
        "settings": {
            "user_agent": BROWSER_USER_AGENTS[0],
            "random_user_agent": True,
            "user_agent_platforms": ["windows"],
            "user_agent_chrome_versions": ["150.0.0.0", "151.0.0.0", "152.0.0.0"],
        },
        "sites": [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}],
    }))
    selected = choose_user_agent(config)
    assert "Windows NT 10.0; Win64; x64" in selected
    assert any(f"Chrome/{version}" in selected for version in ("150.0.0.0", "151.0.0.0", "152.0.0.0"))


def test_random_user_agent_rejects_invalid_platform(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {
        "settings": {"random_user_agent": True, "user_agent_platforms": ["android"]},
        "sites": [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}],
    }))
    with pytest.raises(ValueError, match="user_agent_platforms"):
        choose_user_agent(config)


def test_browser_probe_reports_no_discoverable_price_data(tmp_path: Path, monkeypatch):
    config = load_config(_write_config(tmp_path, {
        "settings": {"history_file": str(tmp_path / "history.jsonl"), "latest_file": str(tmp_path / "latest.json"), "event_file": str(tmp_path / "events.jsonl")},
        "sites": [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}]
    }))
    monkeypatch.setattr("llm_price_monitor.adapters.NetworkAdapter.collect", lambda *_args: (_ for _ in ()).throw(PriceMonitorError("页面已打开，但没有捕获到可识别的价格 JSON/模型")))
    report = run_once(config, client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))))
    assert report.errors[0]["site_id"] == "demo"


def test_browser_adapter_requires_network_url(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {
        "settings": {"monitor_models": ["demo-model"]},
        "sites": [{"id": "demo", "adapter": "standard"}],
    }))
    with pytest.raises(PriceMonitorError, match="未配置 network.url"):
        NetworkAdapter().collect(config.sites[0], httpx.Client(), 1, BROWSER_USER_AGENTS[0], config.ai)


def test_site_config_rejects_base_url(tmp_path: Path):
    with pytest.raises(ValueError, match="不再支持 base_url"):
        load_config(_write_config(tmp_path, {
            "settings": {},
            "sites": [{"id": "demo", "adapter": "standard", "base_url": "https://demo.test", "model_list_url": "https://demo.test/pricing"}],
        }))


def test_site_config_normalizes_legacy_adapter_values(tmp_path: Path):
    for legacy in ("browser", "network"):
        config = load_config(_write_config(tmp_path, {
            "settings": {},
            "sites": [{"id": "demo", "adapter": legacy, "model_list_url": "https://demo.test/pricing", "models": []}],
        }))
        assert config.sites[0].adapter == "standard"


def test_site_config_rejects_removed_direct_adapters(tmp_path: Path):
    with pytest.raises(ValueError, match="source_url"):
        load_config(_write_config(tmp_path, {
            "settings": {},
            "sites": [{"id": "demo", "adapter": "json", "source_url": "https://demo.test/pricing"}],
        }))


def test_site_config_rejects_removed_second_price_page(tmp_path: Path):
    with pytest.raises(ValueError, match="usage_records_url"):
        load_config(_write_config(tmp_path, {
            "settings": {},
            "sites": [{
                "id": "demo",
                "adapter": "standard",
                "model_list_url": "https://demo.test/pricing",
                "usage_records_url": "https://demo.test/usage",
            }],
        }))


def test_ai_extractor_uses_page_and_response_evidence_without_auth_values():
    request_body = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request_body.update(json.loads(request.content))
        result = {
            "models": [{
                "model": "demo-model",
                "input_price": 5,
                "output_price": 30,
                "unit": "USD/1M tokens",
                "currency": "USD",
                "status": "confirmed",
                "confidence": 0.99,
                "network_evidence": [{"url": "https://demo.test/api/v9/price?token=[REDACTED]", "quote": "input=5 output=30"}],
            "page_evidence": ["demo-model Input 5 Output 30 USD/1M tokens"],
                "notes": ""
            }],
                "cross_validation": {"status": "matched", "conflicts": []}
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="ai-secret"))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    records = extractor.extract(
                SiteSpec(id="demo", adapter="browser", network={"url": "https://demo.test/pricing?token=page-secret"}, models=(ModelTarget("demo-model"),)),
                "demo-model Input 5 Output 30 USD/1M tokens",
                [
                    {"url": "https://demo.test/api/v9/price?token=api-secret", "status": 200, "resource_type": "fetch", "content_type": "application/json", "payload": {"model_name": "demo-model", "input": 5, "output": 30, "Authorization": "auth-secret", "Cookie": "session=cookie-secret"}},
                    {"url": "https://demo.test/api/v9/price-details", "status": 200, "resource_type": "xhr", "content_type": "text/html", "text": "demo-model official_input_price_per_1m_micro_usd=5000000"},
                ],
                client=client,
            )
    assert records[0].price_status == "confirmed"
    assert records[0].input_price == 5 and records[0].output_price == 30
    body_text = json.dumps(request_body, ensure_ascii=False)
    assert "network_evidence" in body_text
    assert "captured_responses" not in body_text
    assert "official_input_price_per_1m_micro_usd" not in body_text
    assert "auth-secret" not in body_text
    assert "cookie-secret" not in body_text
    assert "api-secret" not in body_text
    assert "page-secret" not in body_text
    assert "ai-secret" not in body_text


def test_ai_request_keeps_model_list_evidence_for_ai():
    spec = SiteSpec(
        id="hao",
        adapter="browser",
        network={"url": "https://hao.test/zh/models"},
        models=(ModelTarget("gpt-5.6-sol"),),
    )
    page_text = "openai/gpt-5.6-sol 输入 Token $1/M 输出 Token $2/M anthropic/claude-4 输入 Token $3/M 输出 Token $4/M"
    responses = [
        {"url": "https://hao.test/api/login", "status": 200, "resource_type": "fetch", "text": "login success"},
        {"url": "https://hao.test/api/prices", "status": 200, "resource_type": "fetch", "payload": {"models": [{"id": "openai/gpt-5.6-sol", "input": 1, "output": 2}, {"id": "anthropic/claude-4", "input": 3, "output": 4}]}},
        {"url": "https://hao.test/api/price-details", "status": 200, "resource_type": "xhr", "text": "openai/gpt-5.6-sol official_input_price=1 official_output_price=2"},
    ]

    body = _ai_request_body(spec, page_text, responses)

    assert body["messages"][0]["content"]
    request_content = body["messages"][1]["content"]
    assert "login success" not in request_content
    assert "page_evidence" in request_content
    assert "network_evidence" in request_content
    request_evidence = json.loads(request_content.split("网页证据：\n", 1)[1])
    model_list_quote = next(item["quote"] for item in request_evidence["network_evidence"] if item["url"] == "https://hao.test/api/prices")
    assert '"openai/gpt-5.6-sol"' in model_list_quote
    assert '"input":1' in model_list_quote
    # 候选预筛只保留目标模型对象，非目标模型不进证据。
    assert '"anthropic/claude-4"' not in model_list_quote


def test_network_evidence_requires_price_like_number_not_model_metadata():
    spec = SiteSpec(id="hao", adapter="browser", network={"url": "https://hao.test/zh/models"}, models=(ModelTarget("gpt-5.6-sol"),))

    body = _ai_request_body(
        spec,
        "openai/gpt-5.6-sol 输入 Token $1/M 输出 Token $2/M",
        [
            {"url": "https://hao.test/api/model", "status": 200, "resource_type": "fetch", "text": "gpt-5.6-sol context_length=372000 release_date=2026-07-09"},
            {"url": "https://hao.test/_next/static/chunks/app/page.rsc", "status": 200, "resource_type": "fetch", "text": 'gpt-5.6-sol metadata "$1" "$5"'},
            {"url": "https://hao.test/api/price", "status": 200, "resource_type": "fetch", "text": "gpt-5.6-sol input_price=1 output_price=2"},
        ],
    )

    request_content = body["messages"][1]["content"]
    evidence = json.loads(request_content.split("网页证据：\n", 1)[1])["network_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["url"] == "https://hao.test/api/price"


def test_model_list_response_keeps_all_target_model_objects():
    spec = SiteSpec(
        id="sudocode",
        adapter="browser",
        network={"url": "https://sudocode.test/pricing"},
        models=(ModelTarget("gpt-5.6-sol"), ModelTarget("claude-fable-5")),
    )
    response = {
        "source": "model_list",
        "url": "https://sudocode.test/api/pricing",
        "status": 200,
        "resource_type": "xhr",
        "payload": {
            "data": [
                {"model_name": "gpt-5.6-sol", "official_pricing": {"input": 5, "output": 30}},
                {"model_name": "claude-fable-5", "official_pricing": {"input": 10, "output": 50}},
            ]
        },
    }

    evidence = json.loads(
        _ai_request_body(spec, "", [response])["messages"][1]["content"].split("网页证据：\n", 1)[1]
    )["network_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["target_model"] == "unresolved"
    assert '"input":5' in evidence[0]["quote"]
    assert '"input":10' in evidence[0]["quote"]


def test_page_text_stops_at_next_display_model_heading():
    spec = SiteSpec(id="hao", adapter="browser", network={"url": "https://hao.test/zh/models"}, models=(ModelTarget("gpt-5.6-sol"),))
    page_text = (
        "openai/gpt-5.6-sol 上下文 372K 输入 Token $0.75/M 输出 Token $4.5/M "
        "OpenAI: GPT-5.6 Terra 1.5折 OpenAI: GPT-5.6 Terra"
    )

    request_content = _ai_request_body(spec, page_text, [])["messages"][1]["content"]
    filtered = " ".join(item["quote"] for item in json.loads(request_content.split("网页证据：\n", 1)[1])["page_evidence"])
    assert "openai/gpt-5.6-sol" in filtered
    assert "输入 Token $0.75/M" in filtered
    assert "输出 Token $4.5/M" in filtered
    assert "OpenAI: GPT-5.6 Terra" not in filtered


def test_page_evidence_preserves_raw_quote_without_inventing_price_mapping():
    spec = SiteSpec(id="hao", adapter="browser", network={"url": "https://hao.test/zh/models"}, models=(ModelTarget("gpt-5.6-sol"),))

    request_content = _ai_request_body(
        spec,
        "",
        [],
        page_sources=[{
            "source": "model_list",
            "url": "https://hao.test/zh/models",
            "text": "openai/gpt-5.6-sol 定价 HaoAI(0.15x) 官方 输入 Token $0.75/M $5/M 输出 Token $4.5/M $30/M 缓存读取 $0.075/M $0.5/M 缓存创建 $0.938/M $6.25/M",
        }],
    )["messages"][1]["content"]
    evidence = json.loads(request_content.split("网页证据：\n", 1)[1])["page_evidence"][0]
    assert evidence == {
        "quote": "openai/gpt-5.6-sol 定价 HaoAI(0.15x) 官方 输入 Token $0.75/M $5/M 输出 Token $4.5/M $30/M 缓存读取 $0.075/M $0.5/M 缓存创建 $0.938/M $6.25/M",
        "source": "model_list",
        "target_model": "gpt-5.6-sol",
        "url": "https://hao.test/zh/models",
    }


def test_annotate_positional_price_row_prefers_site_base_price_times_group_ratio():
    from llm_price_monitor.evidence import annotate_positional_price_arrays

    page_text = (
        'models:[["gpt-5.6-terra",14,84,1.4,2,12,.2],["gpt-5.6-luna",1.4,8.4,.14,.2,1.2,.02]],'
        'groups:[["lite",txt.gptLite,"","",.15,txt.gptLiteIntro,["gpt-5.6-terra","gpt-5.6-luna"]],'
        '["plus",txt.gptPlus,"","",.18,txt.gptPlusIntro,["gpt-5.6-terra"]]],'
        'const gptPlusGroup = chatgptGroups.find(g => g[0] === "plus");'
        'if (gptPlusGroup) gptPlusGroup[4] = .2;'
    )

    annotated = annotate_positional_price_arrays(page_text, ["gpt-5.6-terra"])

    assert "官方参考价 输入=14 / 输出=84 / 缓存读取=1.4" in annotated
    assert "站内实付基础价 输入=2 / 输出=12 / 缓存读取=.2" in annotated
    # 分组倍率按 JS 覆写后的值折算：lite .15x、plus .2x
    assert "@ lite（倍率 0.15x）：输入=0.3 / 输出=1.8 / 缓存读取=0.03" in annotated
    assert "@ plus（倍率 0.2x）：输入=0.4 / 输出=2.4 / 缓存读取=0.04" in annotated
    assert "gpt-5.6-luna" not in annotated.split("[位置型价格数组解读]")[-1]


def test_annotate_positional_price_row_skips_other_models_and_plain_text():
    from llm_price_monitor.evidence import annotate_positional_price_arrays

    assert annotate_positional_price_arrays("没有价格数组", ["gpt-5.6-terra"]) == "没有价格数组"
    other = 'models:[["claude-opus-5",35,175,0,3.5,5,25,.5]]'
    assert annotate_positional_price_arrays(other, ["gpt-5.6-terra"]) == other


def test_positional_price_array_missing_group_ratio_marks_rule_only():
    from llm_price_monitor.evidence import annotate_positional_price_arrays

    annotated = annotate_positional_price_arrays('["gpt-5.6-terra",14,84,1.4,2,12,.2]', ["gpt-5.6-terra"])

    assert "未识别到分组倍率" in annotated
    assert "rule_only" in annotated


def test_ai_request_uses_model_list_only():
    spec = SiteSpec(
        id="hao",
        adapter="browser",
        network={"url": "https://hao.test/zh/models"},
        models=(ModelTarget("gpt-5.6-sol"),),
    )
    page_sources = [
        {"source": "model_list", "url": "https://hao.test/zh/models", "text": "openai/gpt-5.6-sol 输入 Token $1/M 输出 Token $2/M anthropic/claude-4 $3/M"},
    ]
    responses = [
        {"source": "model_list", "url": "https://hao.test/api/models", "status": 200, "resource_type": "fetch", "payload": {"data": [{"id": "openai/gpt-5.6-sol", "input": 1, "output": 2}, {"id": "anthropic/claude-4", "input": 3, "output": 4}]}},
    ]

    body = _ai_request_body(spec, "", responses, page_sources=page_sources)

    request_evidence = json.loads(body["messages"][1]["content"].split("网页证据：\n", 1)[1])
    assert {item["source"] for item in request_evidence["page_evidence"]} == {"model_list"}
    assert len(request_evidence["network_evidence"]) == 1
    assert request_evidence["network_evidence"][0]["source"] == "model_list"
    model_list_responses = [item for item in request_evidence["network_evidence"] if item["source"] == "model_list"]
    assert '"openai/gpt-5.6-sol"' in model_list_responses[0]["quote"]


def test_confirmed_accepts_model_list_page_and_network_evidence():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="secret"))
    result = {
        "models": [{
            "model": "gpt-5.6-sol",
            "input_price": 1,
            "output_price": 2,
            "unit": "USD/1M tokens",
            "status": "confirmed",
            "network_evidence": [{"source": "model_list", "url": "https://hao.test/api/models", "quote": "gpt-5.6-sol input=1 output=2"}],
            "page_evidence": ["gpt-5.6-sol Input $1/M Output $2/M"],
            "confidence": 1,
        }],
        "cross_validation": {"status": "matched", "conflicts": []},
    }

    records = extractor._records(
        SiteSpec(id="hao", adapter="browser", network={"url": "https://hao.test/zh/models"}),
        result,
        "gpt-5.6-sol Input $1/M Output $2/M",
        "result-hash",
        {"https://hao.test/api/models"},
        {"https://hao.test/api/models": "gpt-5.6-sol input=1 output=2"},
        ["gpt-5.6-sol"],
    )

    assert records[0].price_status == "confirmed"


def test_ai_result_keeps_canonical_model_and_observed_aliases():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",)))
    records = extractor._records(
        SiteSpec(id="demo", adapter="browser", network={"url": "https://demo.test/pricing"}),
        {
            "models": [{
                "model": "claude-fable-5",
                "observed_model": "Claude Opus 5",
                "aliases": ["Claude Opus 5", "claude-opus-5"],
                "input_price": 1,
                "output_price": 2,
                "unit": "USD/1M tokens",
                "status": "candidate",
                "network_evidence": [],
                "page_evidence": [],
            }],
            "cross_validation": {"status": "none", "conflicts": []},
        },
        "Claude Opus 5 input price 1 output price 2",
        "result-hash",
        set(),
        {},
        ["claude-fable-5"],
    )

    assert records[0].model == "claude-fable-5"
    assert records[0].metadata["observed_model"] == "Claude Opus 5"
    assert records[0].metadata["aliases"] == ["Claude Opus 5", "claude-opus-5"]


def test_target_page_text_keeps_shared_price_field_legend_for_target_card():
    text = """模型 输入 缓存写 缓存读 输出
claude-fable-5
¥14.00
¥17.50
¥1.40
¥70.00
other-model
¥1.00
¥2.00
"""

    evidence = _target_page_text(text, ["claude-fable-5"])

    assert "页面共享价格字段说明：模型 输入 缓存写 缓存读 输出" in evidence
    assert "claude-fable-5 ¥14.00 ¥17.50 ¥1.40 ¥70.00" in evidence
    assert "other-model" not in evidence


def test_target_page_text_legend_excludes_digit_bearing_price_rows():
    """图例只收说明行：无货币符号但含数字的价格行不得混进共享字段说明（\\d 正则笔误回归）。"""
    text = """模型 输入 缓存写 缓存读 输出
输入 0.002 输出 0.008
claude-fable-5
¥14.00
¥17.50
¥1.40
¥70.00
"""

    evidence = _target_page_text(text, ["claude-fable-5"])

    legend = evidence.split("目标模型卡片")[0]
    assert "页面共享价格字段说明：模型 输入 缓存写 缓存读 输出" in legend
    assert "0.002" not in legend


def test_ai_result_merges_context_tiers_and_keeps_unified_default_rule():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",)))
    result = {
        "models": [
            {
                "model": "demo-model",
                "input_price": 1,
                "output_price": 6,
                "unit": "CNY/1M tokens",
                "status": "candidate",
                "pricing_rules": {"groups": [{"name": "default", "tiers": [{"context_max": 128000, "input_price": 1, "output_price": 6}]}]},
                "network_evidence": [], "page_evidence": [],
            },
            {
                "model": "demo-model",
                "group": "Codex-Pro",
                "pricing_rules": {"groups": [{"name": "Codex-Pro", "tiers": [{"context_min": 128001, "input_price": 2, "output_price": 12}]}]},
                "network_evidence": [], "page_evidence": [],
            },
        ],
        "cross_validation": {"status": "none", "conflicts": []},
    }
    records = extractor._records(
        SiteSpec(id="demo", adapter="browser", network={"url": "https://demo.test/pricing"}),
        result,
        "demo-model input 1 output 6",
        "hash",
        set(), {}, ["demo-model"],
    )
    assert len(records) == 2
    assert [record.metadata["group"] for record in records] == ["default", "Codex-Pro"]
    assert records[0].input_price == 1 and records[0].output_price == 6
    assert records[1].input_price is None and records[1].output_price is None
    assert records[1].metadata["pricing_rules"]["groups"][0]["name"] == "Codex-Pro"


def test_ai_rule_only_status_is_preserved_for_grouped_prices():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",)))
    result = {
        "models": [{
            "model": "demo-model",
            "input_price": None,
            "output_price": None,
            "unit": "USD/1M tokens",
            "status": "rule_only",
            "pricing_rules": {"groups": [{"name": "discount", "tiers": [{"input_price": 1, "output_price": 5}]}]},
            "network_evidence": [],
            "page_evidence": [],
        }],
        "cross_validation": {"status": "none", "conflicts": []},
    }

    records = extractor._records(
        SiteSpec(id="demo", adapter="browser", network={"url": "https://demo.test/pricing"}),
        result,
        "demo-model",
        "hash",
        set(),
        {},
        ["demo-model"],
    )

    assert records[0].price_status == "rule_only"
    assert records[0].metadata["pricing_kind"] == "tiered_expr"


def test_ai_extractor_rejects_unseen_api_evidence_url():
    result = {
        "models": [{"model": "demo-model", "input_price": 1, "output_price": 2, "unit": "USD/1M tokens", "status": "confirmed", "network_evidence": [{"url": "https://invented.test/price", "quote": "1 / 2"}], "page_evidence": ["demo-model 1 2"], "confidence": 1}],
        "cross_validation": {"status": "matched", "conflicts": []}
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="secret"))
    records = extractor.extract(SiteSpec(id="demo", adapter="browser", network={"url": "https://demo.test/pricing"}, models=(ModelTarget("demo-model"),)), "demo-model 1 2", [{"url": "https://demo.test/api/price", "status": 200, "content_type": "application/json", "payload": {"model_name": "demo-model"}}], client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert records[0].price_status == "candidate"


def test_ai_extractor_does_not_treat_unrelated_json_as_price_evidence():
    result = {
        "models": [{
            "model": "demo-model",
            "input_price": 1,
            "output_price": 2,
            "unit": "USD/1M tokens",
            "status": "confirmed",
            "network_evidence": [{"url": "https://demo.test/api/announcement", "quote": "success"}],
            "page_evidence": ["demo-model Input $1/M Output $2/M"],
            "confidence": 1,
        }],
        "cross_validation": {"status": "matched", "conflicts": []},
    }
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",)))

    records = extractor._records(
        SiteSpec(id="demo", adapter="browser", network={"url": "https://demo.test/pricing"}),
        result,
        "demo-model Input $1/M Output $2/M",
        "result-hash",
        {"https://demo.test/api/announcement"},
        {"https://demo.test/api/announcement": '{"code":0,"message":"success"}'},
    )

    assert records[0].price_status == "candidate"
    assert "未同时包含模型列表中的模型和输入/输出价格" in records[0].metadata["notes"]


def _write_config(tmp_path: Path, value: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_summary_row_flattens_standard_tier_and_keeps_ladder():
    from llm_price_monitor.report import summary_row as _summary_row

    row = {
        "site_id": "demo",
        "model": "gpt-5.6-sol",
        "input_price": None,
        "output_price": None,
        "unit": "CNY/1M tokens",
        "price_status": "rule_only",
        "requires_auth": False,
        "metadata": {
            "group": "openai-premium",
            "pricing_rules": {"groups": [{"name": "openai-premium", "tiers": [
                {"name": "standard", "context_min": None, "context_max": 272000,
                 "input_price": 4.355, "output_price": 26.13,
                 "cache_read_price": 0.4355, "cache_create_price": 5.44375, "unit": "CNY/1M tokens"},
                {"name": "long_context", "context_min": 272001, "context_max": None,
                 "input_price": 8.71, "output_price": 39.195, "unit": "CNY/1M tokens"},
            ]}]},
        },
    }
    summary = _summary_row(row)
    assert summary["input_price"] == round(4.355, 2) and summary["output_price"] == round(26.13, 2)
    assert summary["group"] == "openai-premium"
    assert [tier["name"] for tier in summary["tiers"]] == ["standard", "long_context"]
    assert summary["tiers"][0]["context_max"] == 272000
    assert summary["tiers"][1]["context_min"] == 272001
    assert summary["price_status"] == "rule_only"


def test_summary_row_wraps_flat_price_as_single_tier():
    from llm_price_monitor.report import summary_row as _summary_row

    row = {
        "site_id": "demo",
        "model": "claude-fable-5",
        "input_price": 33.5,
        "output_price": 167.5,
        "unit": "CNY/1M tokens",
        "price_status": "confirmed",
        "requires_auth": False,
        "metadata": {"group": "claude-premium", "cache_read_price": 3.35},
    }
    summary = _summary_row(row)
    assert summary["input_price"] == 33.5 and summary["output_price"] == 167.5
    assert summary["tiers"] == [{
        "group": "claude-premium",
        "name": "default",
        "context_min": None,
        "context_max": None,
        "input_price": 33.5,
        "output_price": 167.5,
        "cache_read_price": 3.35,
        "cache_create_price": None,
        "unit": "CNY/1M tokens",
    }]


def test_summary_row_skips_cross_group_tiers_and_normalizes_per_token_unit():
    from llm_price_monitor.report import summary_row as _summary_row

    row = {
        "site_id": "demo",
        "model": "gpt-5.6-sol",
        "input_price": None,
        "output_price": None,
        "unit": "USD/1M tokens",
        "price_status": "rule_only",
        "requires_auth": False,
        "metadata": {
            "group": "default",
            "pricing_rules": {"groups": [{"name": "gpt-plus-优惠", "tiers": [
                {"context_min": 0, "context_max": None,
                 "input_price": 8.9e-07, "output_price": 5.34e-06, "unit": "USD/token"},
            ]}]},
        },
    }
    summary = _summary_row(row)
    # 分组型多档（与记录分组不一致）不平铺到顶层，避免张冠李戴
    assert summary["input_price"] is None and summary["output_price"] is None
    # per-token 单位统一换算成 /1M tokens
    tier = summary["tiers"][0]
    assert tier["unit"] == "USD/1M tokens"
    assert tier["input_price"] == pytest.approx(0.89)
    assert tier["output_price"] == pytest.approx(5.34)


def test_ai_empty_pricing_rules_skeleton_becomes_unavailable():
    from llm_price_monitor.ai import AIPriceExtractor
    from llm_price_monitor.config import AIConfig

    extractor = AIPriceExtractor(AIConfig())
    result = {
        "models": [{
            "model": "gpt-5.6-sol",
            "observed_model": "GPT-5.5",
            "aliases": [],
            "input_price": None,
            "output_price": None,
            "unit": "CNY/1M tokens",
            "status": "rule_only",
            "pricing_rules": {"groups": []},
            "network_evidence": [],
            "page_evidence": [],
            "notes": "未找到目标模型",
        }],
        "cross_validation": {"status": "none", "conflicts": []},
    }
    spec = SiteSpec(id="demo", models=["gpt-5.6-sol"])
    records = extractor._records(spec, result, "", "hash", set(), {}, ["gpt-5.6-sol"])
    assert len(records) == 1
    assert records[0].price_status == "unavailable"


def test_ai_multi_group_pricing_rules_split_into_per_group_records():
    from llm_price_monitor.ai import AIPriceExtractor
    from llm_price_monitor.config import AIConfig

    extractor = AIPriceExtractor(AIConfig())
    result = {
        "models": [{
            "model": "gpt-5.6-sol",
            "observed_model": "GPT-5.6 Sol",
            "aliases": [],
            "input_price": None,
            "output_price": None,
            "unit": "USD/1M tokens",
            "status": "rule_only",
            "pricing_rules": {"groups": [
                {"name": "gpt-plus-优惠", "tiers": [
                    {"context_min": 0, "context_max": None, "input_price": 8.9e-07,
                     "output_price": 5.34e-06, "unit": "USD/token"},
                ]},
                {"name": "pro-企业专用", "tiers": [
                    {"context_min": 0, "context_max": None, "input_price": 5e-06,
                     "output_price": 3e-05, "unit": "USD/token"},
                ]},
            ]},
            "network_evidence": [],
            "page_evidence": [],
            "notes": "",
        }],
        "cross_validation": {"status": "none", "conflicts": []},
    }
    spec = SiteSpec(id="demo", models=["gpt-5.6-sol"])
    records = extractor._records(spec, result, "", "hash", set(), {}, ["gpt-5.6-sol"])
    assert [record.metadata["group"] for record in records] == ["gpt-plus-优惠", "pro-企业专用"]
    assert all(record.price_status == "rule_only" for record in records)
    from llm_price_monitor.report import summary_row as _summary_row
    summary = _summary_row(_record_dict_for_test(records[0]))
    assert summary["input_price"] == pytest.approx(0.89)
    assert summary["tiers"][0]["unit"] == "USD/1M tokens"


def _record_dict_for_test(record):
    from dataclasses import asdict
    return asdict(record)


def test_fingerprint_ignores_ai_noise_and_detects_real_price_changes():
    """指纹只含价格口径字段：AI 抽取的上下文边界/备注漂移不产生"价格变化"事件。"""
    base = {
        "model": "claude-fable-5",
        "input_price": 2.5,
        "output_price": 12.5,
        "unit": "USD/1M tokens",
        "price_status": "confirmed",
        "requires_auth": False,
        "metadata": {
            "group": "default",
            "context_max": 1000000.0,
            "notes": "第一轮备注",
            "pricing_rules": {"groups": [{"name": "default", "tiers": [
                {"context_min": 0, "context_max": 1000000, "input_price": 2.5, "output_price": 12.5},
            ]}]},
        },
    }
    noisy = {
        **base,
        "metadata": {
            **base["metadata"],
            "context_max": None,  # AI 有时给上下文上限有时不给
            "notes": "第二轮换了说法",
            "pricing_rules": {"groups": [{"name": "default", "tiers": [
                {"context_min": 0, "context_max": None, "input_price": 2.5, "output_price": 12.5},
            ]}]},
        },
    }
    assert classify(base, noisy) == "unchanged"

    # 价格真的变了（含 int/float 表示差异归一后仍要能识别）：发 changed
    raised = {**base, "input_price": 3.0}
    assert classify(base, raised) == "changed"
    assert fingerprint({**base, "output_price": 12.50}) == fingerprint(base)

    # 缓存价属于价格口径：变了要发事件
    assert classify(base, {**base, "metadata": {**base["metadata"], "cache_read_price": 0.3}}) == "changed"

    # 价格变了但 price_status 同时变（如确认价→推断价）：按真实价格变化发 changed，不能当状态抖动吞掉
    assert classify(base, {**base, "input_price": 3.0, "price_status": "rule_only"}) == "changed"
    # 只有状态抖动、价格没变：仍判 status_changed（事件层过滤，不发事件）
    assert classify(base, {**base, "price_status": "rule_only"}) == "status_changed"


def test_fingerprint_treats_null_value_as_missing_key():
    """tier 里显式写 null 的键与整键省略是同一价格口径：AI 抽取轮次间的表示漂移不得刷"变更"事件
    （DaiTuAI 2026-10-03 实测：前后有效价完全一致，仅 cache_create_price 从 null 键变成省略键，被误判 changed）。"""
    base = {
        "model": "gpt-6-astra",
        "input_price": 1.5,
        "output_price": 7.5,
        "unit": "CNY/1M tokens",
        "price_status": "candidate",
        "requires_auth": False,
        "metadata": {
            "group": "lite",
            "cache_read_price": 0.15,
            "cache_create_price": None,
            "cache_create_1h_price": None,
            "pricing_rules": {"groups": [{"name": "lite", "tiers": [
                {"context_min": 0, "context_max": None, "input_price": 1.5, "output_price": 7.5,
                 "cache_read_price": 0.15, "cache_create_price": None, "cache_create_1h_price": None,
                 "unit": "CNY/1M tokens"},
            ]}]},
        },
    }
    # 下一轮抽取直接省略了值为 null 的两个键，其余价格字段一字未动
    omitted = {
        **base,
        "metadata": {
            **base["metadata"],
            "cache_create_price": None,
            "cache_create_1h_price": None,
            "pricing_rules": {"groups": [{"name": "lite", "tiers": [
                {"context_min": 0, "context_max": None, "input_price": 1.5, "output_price": 7.5,
                 "cache_read_price": 0.15, "unit": "CNY/1M tokens"},
            ]}]},
        },
    }
    assert fingerprint(base) == fingerprint(omitted)
    assert classify(base, omitted) == "unchanged"

    # 归一只统一"空"的两种写法，不代表丢检测：缓存价从 null/缺失变成有值仍是真变更
    gained = {
        **omitted,
        "metadata": {**omitted["metadata"], "cache_create_price": 1.5},
    }
    assert classify(base, gained) == "changed"
    # 有值变缺失同理
    assert classify(gained, omitted) == "changed"


def test_parse_scalar_keeps_non_json_js_array_as_text():
    """压缩 JS 的数组常不是合法 JSON（.15 前导点、单引号）：原样保留文本，别崩掉整条解析链。"""
    from llm_price_monitor.adapters import _parse_scalar

    assert _parse_scalar("[1, 0.15]") == [1, 0.15]  # 合法 JSON 照常解析
    assert _parse_scalar("[1,.15]") == "[1,.15]"  # 非法 JSON 原样返回，不抛异常
    assert _parse_scalar("[1, 'x']") == "[1, 'x']"


def test_site_status_from_records_flags_auth_and_unavailable():
    from llm_price_monitor.report import site_status_from_records

    authed = PriceRecord("demo-model", None, None, "USD/1M tokens", "https://demo.test", 0, {"error": "HTTP 401"}, "unavailable", True)
    assert site_status_from_records([authed]) == {"status": "auth_required", "error": "HTTP 401"}

    missing = PriceRecord("demo-model", None, None, "USD/1M tokens", "https://demo.test", 0, {"notes": "模型不在在售列表"}, "unavailable")
    result = site_status_from_records([missing])
    assert result["status"] == "no_data" and "不在在售列表" in (result["error"] or "")

    inferred = PriceRecord("demo-model", None, None, "USD/1M tokens", "https://demo.test", 0, {"pricing_rules": {"groups": []}}, "rule_only")
    assert site_status_from_records([inferred]) == {"status": "inferred", "error": None}

    priced = PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test", 0, {})
    assert site_status_from_records([priced]) == {"status": "ok", "error": None}


def test_backfill_rule_price_fills_top_level_from_representative_tier():
    from llm_price_monitor.report import _backfill_rule_price

    row = {
        "site_id": "totokens",
        "model": "gpt-5.6-sol",
        "input_price": None,
        "output_price": None,
        "unit": "USD/1M tokens",
        "price_status": "rule_only",
        "requires_auth": False,
        "metadata": {
            "group": "pro-企业专用",
            "pricing_rules": {"groups": [{"name": "pro-企业专用", "tiers": [
                {"context_min": 0, "context_max": None, "input_price": 2.5, "output_price": 15.0, "unit": "USD/1M tokens"},
            ]}]},
        },
    }
    filled = _backfill_rule_price(row)
    assert filled["input_price"] == 2.5 and filled["output_price"] == 15.0
    assert filled["price_status"] == "rule_only"

    # 无推断价的占位行保持无价，不进快照
    empty = _backfill_rule_price({**row, "metadata": {}})
    assert empty["input_price"] is None and empty["output_price"] is None

    # 已有价格的行为不受影响
    priced = {**row, "input_price": 1.0, "output_price": 2.0}
    assert _backfill_rule_price(priced) == priced


def test_run_once_persists_per_site_collect_status(tmp_path: Path, monkeypatch):
    def collect(*_args):
        return [PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {})]

    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    config = load_config(_write_config(tmp_path, {
        "settings": {"history_file": str(tmp_path / "history.jsonl"), "latest_file": str(tmp_path / "latest.json"), "event_file": str(tmp_path / "events.jsonl"), "monitor_models": ["demo-model"]},
        "sites": [
            {"id": "good", "enabled": True},
            {"id": "bad", "adapter": "missing", "enabled": True},
            {"id": "off", "enabled": False},
        ],
    }))
    store = Store(tmp_path / "monitor.db")
    run_once(config, store=store, client=httpx.Client())

    status = store.get_document("collect_status")
    assert status["good"]["status"] == "ok" and status["good"]["checked_at"] > 0
    assert status["bad"]["status"] == "error" and "未知适配器" in status["bad"]["error"]
    assert status["off"]["status"] == "disabled"

    # 单站点采集（如 /api/collect?site_id=…）不应冲掉其他站点的状态
    from dataclasses import replace as _dc_replace

    single = _dc_replace(config, sites=tuple(spec for spec in config.sites if spec.id == "good"))
    run_once(single, store=store, client=httpx.Client())
    status = store.get_document("collect_status")
    assert status["good"]["status"] == "ok"
    assert status["bad"]["status"] == "error" and "未知适配器" in status["bad"]["error"]
    assert status["off"]["status"] == "disabled"


def test_site_collect_health_tracks_latest_round(tmp_path: Path, monkeypatch):
    """三类采集的逐站异常进 site_collect_health：失败红、需认证黄，下一轮正常自动清除。"""
    import llm_price_monitor.report as report_module

    state = {"fail_price": True, "fail_status": True, "auth_price": False}

    def collect(adapter, spec, *_args):
        if spec.id == "bad" and state["fail_price"]:
            raise PriceMonitorError("网络价格接口返回 HTTP 500")
        if spec.id == "good" and state["auth_price"]:
            return [PriceRecord(
                "demo-model", None, None, "CNY/1M tokens", "https://demo.test/pricing", 0,
                {"pricing_kind": "auth_required", "error": "HTTP 401，可能需要认证"}, "unavailable", True,
            )]
        return [PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {})]

    monkeypatch.setattr(NetworkAdapter, "collect", collect)

    def fake_fetch_status(spec, *_args, **_kwargs):
        if spec.id == "bad" and state["fail_status"]:
            raise PriceMonitorError("渠道状态地址返回 HTTP 500")
        return {"site_id": spec.id, "captured_at": 1.0, "source_url": "https://demo.test/status",
                "http_status": 200, "parse": "ok", "data": {}}

    monkeypatch.setattr(report_module, "fetch_site_status", fake_fetch_status)
    monkeypatch.setattr(report_module, "fetch_site_notice", lambda *_args, **_kwargs: None)

    config = load_config(_write_config(tmp_path, {
        "settings": {"history_file": str(tmp_path / "history.jsonl"), "latest_file": str(tmp_path / "latest.json"), "event_file": str(tmp_path / "events.jsonl")},
        "sites": [
            {"id": "good", "models": ["demo-model"], "status": {"url": "https://demo.test/status"}},
            {"id": "bad", "models": ["demo-model"], "status": {"url": "https://demo.test/status"}},
            {"id": "off", "enabled": False, "models": ["demo-model"]},
        ],
    }))
    store = Store(tmp_path / "monitor.db")

    # 第一轮：bad 价格与渠道状态都失败，good 一切正常，停用站点不进档案
    run_once(config, store=store, client=httpx.Client())
    health = store.get_document("site_collect_health")
    assert health["bad"]["price"]["level"] == "error" and "HTTP 500" in health["bad"]["price"]["message"]
    assert health["bad"]["status"]["level"] == "error"
    assert "good" not in health and "off" not in health

    # 第二轮：bad 恢复即整站摘除；good 价格变需认证，黄色提示带着原因
    state.update(fail_price=False, fail_status=False, auth_price=True)
    run_once(config, store=store, client=httpx.Client())
    health = store.get_document("site_collect_health")
    assert "bad" not in health
    assert health["good"]["price"]["level"] == "warn" and "401" in health["good"]["price"]["message"]
    assert "status" not in health["good"] and "notice" not in health["good"]

    # 第三轮：good 也恢复，档案清空
    state.update(auth_price=False)
    run_once(config, store=store, client=httpx.Client())
    assert store.get_document("site_collect_health") == {}


def test_rate_base_site_normalizes_to_standard_on_load():
    raw = {
        "id": "legacy",
        "adapter": "rate_base",
        "models": ["gpt-x"],
        "network": {
            "url": {"url": "https://example.com/api/rates", "method": "POST"},
            "base_price_url": "https://example.com/pricing",
        },
    }
    (spec,) = sites_from_raw([raw])
    assert spec.adapter == "standard"
    assert spec.network["url"] == "https://example.com/api/rates"
    assert "base_price_url" not in spec.network


def test_sites_from_raw_accepts_extra_networks():
    raw = {
        "id": "multi",
        "models": ["gpt-x"],
        "network": {"url": "https://example.com/api/prices"},
        "networks": [
            {"url": "https://example.com/api/pricing", "params": {"page": "1"}},
        ],
    }
    (spec,) = sites_from_raw([raw])
    assert spec.adapter == "standard"
    assert len(spec.networks) == 1
    assert spec.networks[0]["url"] == "https://example.com/api/pricing"
    assert spec.networks[0]["params"] == {"page": "1"}


def test_standard_adapter_discovers_prices_in_referenced_js_chunk():
    page = '<html><head><script src="/static/js/prices-abc123.js"></script></head><body>定价页</body></html>'
    chunk = '{category:"openai",provider:"openAI",name:"GPT-5.6 Sol",models:["gpt-5.6-sol"],input:5,output:30}'
    (spec,) = sites_from_raw([
        {"id": "chunky", "network": {"url": "https://example.com/dashboard/pricing"}},
    ], models=(ModelTarget("gpt-5.6-sol"),))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".js"):
            return httpx.Response(200, text=chunk)
        return httpx.Response(200, text=page)

    records = ADAPTERS["standard"].collect(spec, httpx.Client(transport=httpx.MockTransport(handler)), 5, BROWSER_USER_AGENTS[0])
    by_model = {record.model: record for record in records}
    assert by_model["gpt-5.6-sol"].input_price == 5
    assert by_model["gpt-5.6-sol"].output_price == 30
    assert by_model["gpt-5.6-sol"].price_status == "confirmed"


def test_standard_adapter_applies_ratio_url_to_base_prices():
    page = '<html><head><script src="/static/js/prices-abc123.js"></script></head><body>定价页</body></html>'
    chunk = '{category:"openai",provider:"openAI",name:"GPT-5.6 Sol",models:["gpt-5.6-sol"],input:5,output:30}'
    rates = '{"pricing":[{"provider":"openAI","model_display":"GPT-5.5","rate":0.3,"input_rmb":1.5,"output_rmb":9}]}'
    (spec,) = sites_from_raw([
        {
            "id": "rated",
            "network": {
                "url": "https://example.com/dashboard/pricing",
                "ratio_url": "https://example.com/api/public/model-pricing",
            },
        },
    ], models=(ModelTarget("gpt-5.6-sol"),))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/model-pricing":
            return httpx.Response(200, text=rates)
        if request.url.path.endswith(".js"):
            return httpx.Response(200, text=chunk)
        return httpx.Response(200, text=page)

    records = ADAPTERS["standard"].collect(spec, httpx.Client(transport=httpx.MockTransport(handler)), 5, BROWSER_USER_AGENTS[0])
    (record,) = records
    assert (record.input_price, record.output_price) == (1.5, 9.0)
    assert record.unit == "CNY/1M tokens"
    assert record.source_url == "https://example.com/api/public/model-pricing"
    metadata = record.metadata or {}
    assert metadata["pricing_kind"] == "base_times_rate"
    assert metadata["rate"] == 0.3
    assert metadata["rate_source"] == "provider"
    assert metadata["network_evidence"][0]["source"] == "rate_api"


def test_ai_ping_model_sends_minimal_request_and_returns_reply():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": " ok "}}]})

    reply = ping_model(AIConfig(base_url="https://ai.test/v1", api_key="sk-x"), "m-a", client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert reply == "ok"
    assert seen["url"] == "https://ai.test/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-x"
    assert seen["body"] == {
        "model": "m-a",
        "messages": [{"role": "user", "content": "连接测试，请只回复 ok"}],
        "max_tokens": 8,
        "temperature": 0,
        "enable_thinking": False,
    }


def test_ai_ping_model_maps_http_errors_for_caller():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    with pytest.raises(httpx.HTTPStatusError):
        ping_model(AIConfig(base_url="https://ai.test/v1"), "m-a", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_ai_ping_model_retries_with_thinking_enabled_when_restricted(monkeypatch):
    """思考不可关的模型拒收 enable_thinking=false：连接测试翻参重试一次，返回模型回复。"""
    import llm_price_monitor.ai as ai_mod

    monkeypatch.setattr(ai_mod, "_MODEL_THINKING_REQUIRED", set())  # 学习态隔离，不受其他测试污染
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("enable_thinking") is False:
            return httpx.Response(400, json={"error": {"message": "The value of the enable_thinking parameter is restricted to True."}})
        seen["body"] = body
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    reply = ping_model(AIConfig(base_url="https://ai.test/v1"), "glm-5.3", client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert reply == "ok"
    assert seen["body"]["enable_thinking"] is True



def test_group_removed_event_after_six_misses(tmp_path: Path, monkeypatch):
    """分组连续 GROUP_REMOVED_MISSES 轮没出现才记 group_removed 并从快照摘除；中途恢复则不报。"""
    groups = {"default", "vip"}

    def collect(*_args):
        return [
            PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {"group": group})
            for group in sorted(groups)
        ]

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config(tmp_path)), encoding="utf-8")
    config = load_config(config_path)
    store = Store(tmp_path / "monitor.db")
    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    run_once(config, store=store, client=httpx.Client())
    assert len(store.latest_all()) == 2

    groups.discard("vip")
    for _ in range(GROUP_REMOVED_MISSES - 1):
        report = run_once(config, store=store, client=httpx.Client())
        assert [event["kind"] for event in report.events] == []  # 阈值内只计数不报事件
    assert len(store.latest_all()) == 2

    final = run_once(config, store=store, client=httpx.Client())
    assert [event["kind"] for event in final.events] == ["group_removed"]
    assert list(store.latest_all()) == ["demo:demo-model:default"]
    removed = next(event for event in store.read_events(limit=10)[0] if event["kind"] == "group_removed")
    assert removed["previous"]["metadata"]["group"] == "vip"


def _sanity_catalog() -> dict:
    return {
        "usd_cny_rate": 6.74,
        "models": {
            "demomodel": {"found": True, "currency": "USD", "list": {"input": 5.0, "output": 30.0}, "source_url": "https://demo.test"},
            "sanemodel": {"found": True, "currency": "USD", "list": {"input": 5.0, "output": 30.0}, "source_url": "https://demo.test"},
        },
    }


def test_price_sanity_voids_absurd_extraction(tmp_path: Path, monkeypatch):
    """站点价对厂商价离谱时两轮确认：首轮只挂待复核标记照常入库，复现才作废，
    错误数值不得长期留在快照与历史；正常价不受影响。"""
    def collect(*_args):
        return [
            PriceRecord("demo-model", 2_000_000, 6_000_000, "CNY/1M tokens", "https://demo.test/pricing", 0, {"group": "default"}),
            PriceRecord("sane-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {"group": "default"}),
        ]

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config(tmp_path)), encoding="utf-8")
    config = load_config(config_path)
    store = Store(tmp_path / "monitor.db")
    store.set_document("catalog", _sanity_catalog())
    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    run_once(config, store=store, client=httpx.Client())
    run_once(config, store=store, client=httpx.Client())

    # 首轮异常挂标记入库、第二轮复现后被作废：无价行被落库层清理，整行摘除
    assert "demo:demo-model:default" not in store.latest_all()
    # 正常价（0.2 折）原样入库
    sane = store.latest_all()["demo:sane-model:default"]
    assert sane["input_price"] == 1 and sane["price_status"] == "confirmed"


def test_price_sanity_first_violation_kept_with_flag(tmp_path: Path, monkeypatch):
    """首轮异常不作废：这次采集到的价照常展示，只挂 sanity_suspect 待复核标记，
    官方价目录自身带错（如厂商页提取列错位）时不误杀真数据。"""
    rounds = [
        [PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {"group": "default"})],
        [PriceRecord("demo-model", 2_000_000, 6_000_000, "CNY/1M tokens", "https://demo.test/pricing", 0, {"group": "default"})],
    ]

    def collect(*_args):
        return rounds.pop(0)

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config(tmp_path)), encoding="utf-8")
    config = load_config(config_path)
    store = Store(tmp_path / "monitor.db")
    store.set_document("catalog", _sanity_catalog())
    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    run_once(config, store=store, client=httpx.Client())
    run_once(config, store=store, client=httpx.Client())

    suspect = store.latest_all()["demo:demo-model:default"]
    assert suspect["input_price"] == 2_000_000  # 本轮采集到的价原样展示
    assert suspect["price_status"] == "confirmed"
    assert "可信区间" in (suspect.get("metadata") or {}).get("sanity_suspect", "")


def test_price_sanity_rereads_catalog_each_site(tmp_path: Path, monkeypatch):
    """sanity 判据每站现读：一轮采集中途目录修正后，后续站点不再被启动时的坏判据误杀。"""
    bad_catalog = {
        "usd_cny_rate": 6.74,
        "models": {"demomodel": {"found": True, "currency": "USD",
                                 "list": {"input": 0.003, "output": 0.006}, "source_url": "https://demo.test"}},
    }
    good_catalog = {
        "usd_cny_rate": 6.74,
        "models": {"demomodel": {"found": True, "currency": "USD",
                                 "list": {"input": 0.446429, "output": 0.892857}, "source_url": "https://demo.test"}},
    }
    store = Store(tmp_path / "monitor.db")
    calls = {"n": 0}

    def collect(*_args):
        # 两个站返回同样的真实价 0.6 元（厂商 3 元 × 站点 0.2 折）；
        # 首站采完、二站开始前目录修正，二站应按新判据放行同一价格
        calls["n"] += 1
        if calls["n"] >= 2:
            store.set_document("catalog", good_catalog)
        return [PriceRecord("demo-model", 0.6, 1.2, "CNY/1M tokens", "https://demo.test/pricing", 0, {"group": "default"})]

    config = dict(_config(tmp_path))
    config["sites"] = [
        {"id": "first", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": ["demo-model"]},
        {"id": "second", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": ["demo-model"]},
    ]
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    store.set_document("catalog", bad_catalog)
    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    run_once(load_config(config_path), store=store, client=httpx.Client())

    # 首站 0.6 对坏判据 0.02 = 30 倍：首轮只挂待复核标记不作废，价照常入库
    first = store.latest_all()["first:demo-model:default"]
    assert first["input_price"] == 0.6
    assert "可信区间" in (first.get("metadata") or {}).get("sanity_suspect", "")
    # 目录修正后二站同价放行，不带标记
    second = store.latest_all()["second:demo-model:default"]
    assert second["input_price"] == 0.6
    assert not (second.get("metadata") or {}).get("sanity_suspect")


def test_group_removed_requires_consecutive_misses(tmp_path: Path, monkeypatch):
    """缺失中途恢复一次就清零计数：累计而非连续的缺失不得累积成下线。"""
    groups = {"default", "vip"}
    absent = {"on": False}

    def collect(*_args):
        if absent["on"]:
            return [PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {"group": "default"})]
        return [
            PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {"group": group})
            for group in sorted(groups)
        ]

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config(tmp_path)), encoding="utf-8")
    config = load_config(config_path)
    store = Store(tmp_path / "monitor.db")
    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    run_once(config, store=store, client=httpx.Client())

    for round_index in range(GROUP_REMOVED_MISSES * 2):
        absent["on"] = round_index % 2 == 0  # 缺一轮、恢复一轮交替，计数每次被清零
        report = run_once(config, store=store, client=httpx.Client())
        assert [event["kind"] for event in report.events] == []
    assert len(store.latest_all()) == 2


def test_group_added_event_for_known_model(tmp_path: Path, monkeypatch):
    """已监控模型冒出新分组记 group_added；全新模型首次出现仍记 new。"""
    groups = {"default"}

    def collect(*_args):
        return [
            PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {"group": group})
            for group in sorted(groups)
        ]

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config(tmp_path)), encoding="utf-8")
    config = load_config(config_path)
    store = Store(tmp_path / "monitor.db")
    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    run_once(config, store=store, client=httpx.Client())
    run_once(config, store=store, client=httpx.Client())  # 第二轮无变化，事件被过滤

    groups.add("vip")
    added = run_once(config, store=store, client=httpx.Client())
    assert [event["kind"] for event in added.events] == ["group_added"]
    assert added.events[0]["current"]["metadata"]["group"] == "vip"
    assert added.events[0]["previous"] is None


def test_established_site_still_records_new_model(tmp_path: Path, monkeypatch):
    """建档轮静默只针对全新站点：站点建立后再冒出的全新模型仍发"新增"事件。"""
    models = {"demo-model"}

    def collect(*_args):
        return [
            PriceRecord(name, 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {"group": "default"})
            for name in sorted(models)
        ]

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config(tmp_path)), encoding="utf-8")
    config = load_config(config_path)
    store = Store(tmp_path / "monitor.db")
    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    first = run_once(config, store=store, client=httpx.Client())
    assert first.events == []  # 首轮建档：静默入库
    models.add("extra-model")
    second = run_once(config, store=store, client=httpx.Client())
    assert [(event["kind"], event["model"]) for event in second.events] == [("new", "extra-model")]


def test_concurrent_persist_dedupes_events_and_respects_touched(tmp_path: Path):
    """并行采集复查去重：测试采集与全量采集对同一份旧快照各自检出同一变化时，
    后落库一路在锁内复查最新快照后丢弃重复事件；本轮没碰过的站点行（touched 之外）
    不得拿开始时的旧值回写覆盖并发轮次的更新。"""

    def row(site_id: str, model: str, input_price: float, captured_at: float) -> dict:
        record = PriceRecord(model, input_price, input_price * 5, "USD/1M tokens", "https://demo.test/pricing", captured_at, {"group": "default"})
        base = record_dict(site_id, record)
        base["price_status"] = "confirmed"
        base["requires_auth"] = False
        base["fingerprint"] = fingerprint(base)
        return base

    store = Store(tmp_path / "monitor.db")
    old_demo = row("demo", "demo-model", 1.0, 1000.0)
    old_other = row("other", "other-model", 2.0, 1000.0)
    store.replace_latest({"demo:demo-model:default": old_demo, "other:other-model:default": old_other})

    # 两路扫描共同的旧基线；各自采集到同一份新价（captured_at 不同、指纹相同）
    fresh_demo = row("demo", "demo-model", 3.0, 2000.0)
    fresh_demo_later = row("demo", "demo-model", 3.0, 2500.0)
    event = {"site_id": "demo", "model": "demo-model", "kind": "changed", "previous": old_demo, "current": fresh_demo, "detected_at": 2000.0}
    duplicate = {**event, "current": fresh_demo_later, "detected_at": 2500.0}

    _persist_scan_results(
        store, latest={"demo:demo-model:default": fresh_demo, "other:other-model:default": old_other},
        history_rows=[], events=[event], removed_keys=set(), touched_keys={"demo:demo-model:default"},
    )
    assert store.read_events(limit=10)[1] == 1

    # 并行路（单站测试）在第一路落库后写同一变化：事件被复查丢弃；
    # 它基线里的 other 旧值不在 touched 内，不得覆盖期间 other 被并发更新的价格
    concurrent_other = row("other", "other-model", 9.0, 2200.0)
    store.replace_latest({"other:other-model:default": concurrent_other})
    _persist_scan_results(
        store, latest={"demo:demo-model:default": fresh_demo_later, "other:other-model:default": old_other},
        history_rows=[], events=[duplicate], removed_keys=set(), touched_keys={"demo:demo-model:default"},
    )
    assert store.read_events(limit=10)[1] == 1  # 同一变化只入库一次
    assert store.latest_all()["other:other-model:default"]["input_price"] == 9.0  # 不回写覆盖
    assert store.latest_all()["demo:demo-model:default"]["input_price"] == 3.0

    # 分组下线同口径：并发轮次已把分组摘除时，后到一路的下线事件被丢弃
    store.remove_latest(["demo:demo-model:default"])
    remove_event = {"site_id": "demo", "model": "demo-model", "kind": "group_removed", "previous": fresh_demo_later, "current": None, "detected_at": 3000.0}
    _persist_scan_results(
        store, latest={"other:other-model:default": concurrent_other},
        history_rows=[], events=[remove_event], removed_keys={"demo:demo-model:default"}, touched_keys=set(),
    )
    assert store.read_events(limit=10)[1] == 1
    assert "demo:demo-model:default" not in store.latest_all()  # 摘除结果不被复活


def test_persist_false_scan_does_not_pollute_group_miss(tmp_path: Path, monkeypatch):
    """测试采集（persist=False）不计入分组缺失：不完整的测试轮次不得加速"分组下线"判定。"""
    groups = {"default", "vip"}

    def collect(*_args):
        return [
            PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://demo.test/pricing", 0, {"group": group})
            for group in sorted(groups)
        ]

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config(tmp_path)), encoding="utf-8")
    config = load_config(config_path)
    store = Store(tmp_path / "monitor.db")
    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    run_once(config, store=store, client=httpx.Client())

    groups.discard("vip")
    for _ in range(2):  # 两次不完整测试轮次：group_miss 计数必须保持不动
        run_once(config, store=store, client=httpx.Client(), persist=False)
    assert not store.get_document("group_miss")

    for round_index in range(GROUP_REMOVED_MISSES):
        report = run_once(config, store=store, client=httpx.Client())
        expected = ["group_removed"] if round_index == GROUP_REMOVED_MISSES - 1 else []
        assert [event["kind"] for event in report.events] == expected  # 测试轮次未计数，完整轮次按真实阈值判定

def _refresh_site_config(tmp_path: Path, **site: object) -> Path:
    """带 token 续签配置的单站点 config：续签端点与价格接口都由 MockTransport 模拟。"""
    path = tmp_path / "refresh-config.json"
    path.write_text(json.dumps({
        "settings": {
            "history_file": str(tmp_path / "history.jsonl"),
            "latest_file": str(tmp_path / "latest.json"),
            "event_file": str(tmp_path / "events.jsonl"),
        },
        "sites": [{
            "id": "totokens",
            "adapter": "standard",
            "network": {"url": "https://totokens.test/api/pricing"},
            "models": ["demo-model"],
            "token_refresh": {"url": "https://totokens.test/api/v1/auth/refresh", "refresh_token": "rt_old"},
            **site,
        }],
    }), encoding="utf-8")
    return path


def _refresh_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/refresh":
            return httpx.Response(200, json={"data": {"access_token": "at_new", "refresh_token": "rt_new"}})
        # run_once 还会顺带拉公告接口（由 network.url 推导），这里只需给出可解析的空公告
        return httpx.Response(200, json={"data": {"content": ""}})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _auth_placeholder() -> PriceRecord:
    return PriceRecord("demo-model", None, None, "USD/1M tokens", "https://totokens.test/api/pricing", 0, {"error": "HTTP 401"}, "unavailable", True)


def _priced(group: str = "default") -> PriceRecord:
    return PriceRecord("demo-model", 1, 2, "USD/1M tokens", "https://totokens.test/api/pricing", 0, {"group": group})


def test_refresh_and_recollect_keeps_records_and_address_errors(monkeypatch):
    """重采结果按 (记录, 地址错误) 契约返回：曾把 collect 回调返回的元组整个当成记录列表。"""
    from llm_price_monitor import token_refresh

    monkeypatch.setattr(token_refresh, "refresh_site_token", lambda *args, **kwargs: ("at_new", "rt_new"))
    (spec,) = sites_from_raw([{
        "id": "rt",
        "models": ["m"],
        "network": {"url": "https://rt.test/api/pricing"},
        "token_refresh": {"url": "https://rt.test/auth/refresh", "refresh_token": "rt_old"},
    }])
    record = PriceRecord("m", 1, 2, "USD/1M tokens", "https://rt.test/pricing", 0, {})

    def collect(_spec):  # 与 report.collect_all 同契约
        return [record], ["附加地址1: 超时"]

    collected, errors = token_refresh.refresh_and_recollect(
        spec, [], collect=collect, client=httpx.Client(), timeout=5, user_agent="ua", store=None
    )
    assert isinstance(collected, list) and collected == [record]
    assert errors == ["附加地址1: 超时"]  # 重采阶段的地址失败不再被静默吞掉


def test_refresh_and_recollect_falls_back_to_placeholder_when_recollect_empty(monkeypatch):
    """续签成功但一条都没采到时退回占位记录：空列表会被下游当成"分组真的下线"。"""
    from llm_price_monitor import token_refresh

    monkeypatch.setattr(token_refresh, "refresh_site_token", lambda *args, **kwargs: ("at_new", "rt_new"))
    (spec,) = sites_from_raw([{
        "id": "rt",
        "models": ["m"],
        "network": {"url": "https://rt.test/api/pricing"},
        "token_refresh": {"url": "https://rt.test/auth/refresh", "refresh_token": "rt_old"},
    }])
    placeholder = PriceRecord("m", None, None, "CNY/1M tokens", "https://rt.test/pricing", 0, {"error": "HTTP 401"}, "unavailable", True)

    def collect(_spec):
        return [], ["附加地址1: 超时"]

    collected, errors = token_refresh.refresh_and_recollect(
        spec, [placeholder], collect=collect, client=httpx.Client(), timeout=5, user_agent="ua", store=None
    )
    assert collected == [placeholder]
    assert "附加地址1: 超时" in errors
    assert any("未取到任何价格" in message for message in errors)


def test_refresh_site_token_rotates_set_cookie_credential(tmp_path: Path):
    """new-api 型轮换凭据：旧值一次有效、新值只在 Set-Cookie；回写后下一轮续签必须带上新值。"""
    from llm_price_monitor import token_refresh

    state = {"current": "sid1.secret1"}
    seen_cookies: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cookie = request.headers.get("cookie")
        seen_cookies.append(cookie)
        if cookie != f"new_api_refresh={state['current']}":
            return httpx.Response(401, json={"success": False, "code": "AUTH_SESSION_REVOKED"})
        next_value = f"sid1.secret{len(seen_cookies) + 1}"
        state["current"] = next_value
        return httpx.Response(
            200,
            json={"success": True, "data": {"access_token": f"at_{len(seen_cookies)}"}},
            headers=[("Set-Cookie", f"new_api_refresh={next_value}; Path=/api/user/auth; HttpOnly")],
        )

    raw_site = {
        "id": "aihub",
        "models": ["m"],
        "network": {"url": "https://aihub.test/api/pricing"},
        "token_refresh": {
            "url": "https://aihub.test/api/user/auth/refresh",
            "method": "POST",
            "headers": {"cookie": "new_api_refresh=${refresh_token}"},
            "refresh_token": "sid1.secret1",
            "refresh_cookie_name": "new_api_refresh",
        },
    }
    store = Store(tmp_path / "monitor.db")
    store.upsert_site("aihub", raw_site)
    (spec,) = sites_from_raw([raw_site])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        access, refresh = token_refresh.refresh_site_token(spec, client, timeout=5, user_agent="ua")
        assert (access, refresh) == ("at_1", "sid1.secret2")
        assert seen_cookies == ["new_api_refresh=sid1.secret1"]  # 请求带旧值，headers 占位符正确展开

        token_refresh.persist_refreshed_config(store, "aihub", access, refresh)
        (reloaded,) = sites_from_raw([store.get_site_config("aihub")])
        access2, refresh2 = token_refresh.refresh_site_token(reloaded, client, timeout=5, user_agent="ua")

    assert seen_cookies == ["new_api_refresh=sid1.secret1", "new_api_refresh=sid1.secret2"]
    assert (access2, refresh2) == ("at_2", "sid1.secret3")


def test_refresh_site_token_body_sends_json_content_type_alongside_extra_headers(tmp_path: Path):
    """配了站点请求头的 body 型续签也要带 content-type: application/json（回归：配任何头时默认头曾整体丢失）。"""
    from llm_price_monitor import token_refresh

    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type")
        return httpx.Response(200, json={"data": {"access_token": "at_body"}})

    raw_site = {
        "id": "body-site",
        "models": ["m"],
        "network": {"url": "https://body.test/api/pricing"},
        "token_refresh": {
            "url": "https://body.test/api/user/auth/refresh",
            "method": "POST",
            "headers": {"user-agent": "ua-custom"},
            "body": '{"refresh_token": "${refresh_token}"}',
            "refresh_token": "rt_old",
        },
    }
    (spec,) = sites_from_raw([raw_site])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        access, refresh = token_refresh.refresh_site_token(spec, client, timeout=5, user_agent="ua")

    assert (access, refresh) == ("at_body", "rt_old")
    assert seen["content_type"] == "application/json"


def test_refresh_site_token_without_rotation_keeps_old_credential():
    """不配 refresh_cookie_name 时行为不变：body 里没有新 refresh_token 就沿用旧值（不轮换的站点）。"""
    from llm_price_monitor import token_refresh

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {"access_token": "at_new"}})

    (spec,) = sites_from_raw([{
        "id": "plain",
        "models": ["m"],
        "network": {"url": "https://plain.test/api/pricing"},
        "token_refresh": {"url": "https://plain.test/auth/refresh", "refresh_token": "rt_old"},
    }])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        access, refresh = token_refresh.refresh_site_token(spec, client, timeout=5, user_agent="ua")

    assert (access, refresh) == ("at_new", "rt_old")


def test_auth_inject_expands_credentials_per_target():
    """凭证注入按目标展开：价格带 Authorization: Bearer ${access_token}，公告带 Cookie 里的 ${refresh_token}，
    渠道状态不注入；值里的凭证变量续签换新后自动跟随。"""
    from llm_price_monitor.adapters import auth_inject_headers

    (spec,) = sites_from_raw([{
        "id": "inject",
        "models": ["m"],
        "auth_token": "at_now",
        "network": {"url": "https://demo.test/pricing"},
        "token_refresh": {"url": "https://demo.test/auth/refresh", "refresh_token": "rt_now"},
        "auth_inject": {
            "price": {"header": "Authorization", "value": "Bearer ${access_token}"},
            "notice": {"header": "cookie", "value": "new_api_refresh=${refresh_token}"},
        },
    }])

    assert auth_inject_headers(spec, "price") == {"Authorization": "Bearer at_now"}
    assert auth_inject_headers(spec, "notice") == {"cookie": "new_api_refresh=rt_now"}
    assert auth_inject_headers(spec, "status") == {}  # 没配的处不注入

    # 续签换新：同一份规则展开成新凭证
    from dataclasses import replace

    refreshed = replace(spec, auth_token="at_new", token_refresh={**spec.token_refresh, "refresh_token": "rt_new"})
    assert auth_inject_headers(refreshed, "price") == {"Authorization": "Bearer at_new"}
    assert auth_inject_headers(refreshed, "notice") == {"cookie": "new_api_refresh=rt_new"}


def test_auth_inject_without_credential_reports_error():
    """规则引用拿不到的凭证：Refresh Token 缺失明确报错（不静默发空 Cookie）；
    Access Token 缺失则不注入——让请求照常发出、由 401 触发续签补上。"""
    from llm_price_monitor.adapters import auth_inject_headers

    (spec,) = sites_from_raw([{
        "id": "fixed",
        "models": ["m"],
        "auth_token": "sk_fixed",
        "network": {"url": "https://demo.test/pricing"},
        "auth_inject": {"notice": {"header": "cookie", "value": "session=${refresh_token}"}},
    }])
    with pytest.raises(PriceMonitorError, match="没有 Refresh Token"):
        auth_inject_headers(spec, "notice")

    (no_token,) = sites_from_raw([{
        "id": "none",
        "models": ["m"],
        "network": {"url": "https://demo.test/pricing"},
        "auth_inject": {"price": {"header": "Authorization", "value": "Bearer ${access_token}"}},
    }])
    assert auth_inject_headers(no_token, "price") == {}  # 空凭证先不注入，等续签补上


def test_legacy_config_injects_authorization_and_ignores_endpoint_auth():
    """没配 auth_inject 的存量站点按老行为注入 Authorization（auth_header/auth_prefix 可改），
    接口里写死的 Authorization 让位、非认证头照常带上；没配 auth_token 的站点不受影响。"""
    from llm_price_monitor.adapters import build_request_kwargs, resolve_endpoint

    (unified,) = sites_from_raw([{
        "id": "unified",
        "models": ["m"],
        "auth_token": "at_site",
        "network": {"url": "https://demo.test/pricing", "headers": {"Authorization": "Bearer at_stale", "referer": "https://demo.test"}},
    }])
    entry = resolve_endpoint(unified.network, spec=unified, label="network")
    merged = build_request_kwargs(entry, unified, "ua/1", 5, target="price")["headers"]
    assert merged["Authorization"] == "Bearer at_site"  # 站点令牌为准
    assert merged["referer"] == "https://demo.test"  # 非认证头照常带上

    (custom,) = sites_from_raw([{
        "id": "custom",
        "models": ["m"],
        "auth_token": "at_site",
        "auth_header": "X-Api-Key",
        "auth_prefix": "",
        "network": {"url": "https://demo.test/pricing"},
    }])
    entry = resolve_endpoint(custom.network, spec=custom, label="network")
    assert build_request_kwargs(entry, custom, "ua/1", 5, target="price")["headers"]["X-Api-Key"] == "at_site"

    (legacy,) = sites_from_raw([{
        "id": "legacy",
        "models": ["m"],
        "network": {"url": "https://demo.test/pricing", "headers": {"Authorization": "Bearer at_own"}},
    }])
    entry = resolve_endpoint(legacy.network, spec=legacy, label="network")
    assert build_request_kwargs(entry, legacy, "ua/1", 5, target="price")["headers"]["Authorization"] == "Bearer at_own"


def test_ratio_endpoint_headers_do_not_override_injected_auth():
    """倍率接口自带的请求头盖不过凭证注入的头，其余自定义头照常生效。"""
    from llm_price_monitor.adapters import auth_inject_headers, build_request_kwargs, resolve_endpoint

    (spec,) = sites_from_raw([{
        "id": "ratio",
        "models": ["m"],
        "auth_token": "at_site",
        "network": {"url": "https://demo.test/pricing"},
    }])
    entry = resolve_endpoint(spec.network, spec=spec, label="network")
    base = build_request_kwargs(entry, spec, "ua/1", 5, target="price")["headers"]
    injected_names = {name.casefold() for name in auth_inject_headers(spec, "price")}
    extra = {"Authorization": "Bearer at_stale", "x-ratio": "1"}
    merged = {**base, **{k: v for k, v in extra.items() if k.casefold() not in injected_names}}
    assert merged["Authorization"] == "Bearer at_site"
    assert merged["x-ratio"] == "1"


def test_token_refresh_recollect_replaces_records_without_crashing(tmp_path: Path, monkeypatch):
    """回归：续签成功后重采。老代码在此抛 'list' object has no attribute 'requires_auth'，整轮采集白跑。"""
    seen_auth_tokens: list[str | None] = []

    def collect(self, spec, *_args):  # 适配器契约：只返回记录列表，(记录, 错误) 由 collect_all 组装
        seen_auth_tokens.append(spec.auth_token)
        return [_priced()] if spec.auth_token == "at_new" else [_auth_placeholder()]

    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    config = load_config(_refresh_site_config(tmp_path))
    store = Store(tmp_path / "monitor.db")
    report = run_once(config, store=store, client=_refresh_client())

    assert seen_auth_tokens == [None, "at_new"]  # 先用旧凭证，续签后带新 token 重采
    assert report.errors == []
    assert store.latest_all()["totokens:demo-model:default"]["input_price"] == 1
    assert store.get_document("collect_status")["totokens"]["status"] == "ok"


def test_token_refresh_with_empty_recollect_keeps_status_and_skips_group_removal(tmp_path: Path, monkeypatch):
    """续签成功但重采无数据：保住"需认证"状态、沿用上次价格，且不得把分组误判成下线。"""
    calls = {"n": 0}

    def collect(self, spec, *_args):
        calls["n"] += 1
        if calls["n"] == 1:
            return [_priced()]
        if spec.auth_token == "at_new":
            return []  # 续签成功，但重采依然取不到任何价格
        return [_auth_placeholder()]

    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    config = load_config(_refresh_site_config(tmp_path))
    store = Store(tmp_path / "monitor.db")
    run_once(config, store=store, client=_refresh_client())
    assert store.latest_all()["totokens:demo-model:default"]["input_price"] == 1

    for _ in range(GROUP_REMOVED_MISSES):  # 连续多轮"续签成功但无数据"：这些轮次不完整，不得累计成下线
        report = run_once(config, store=store, client=_refresh_client())
        assert [event["kind"] for event in report.events] == []

    latest = store.latest_all()["totokens:demo-model:default"]
    assert latest["input_price"] == 1 and latest["requires_auth"] is True
    assert store.get_document("collect_status")["totokens"]["status"] == "auth_required"
    assert not any(event["kind"] == "group_removed" for event in store.read_events(limit=20)[0])


def test_partial_address_failure_skips_group_removal(tmp_path: Path, monkeypatch):
    """附加地址采集失败时本轮记录不完整，没采到的分组不得计入缺失（两轮就会误报下线）。"""
    failing = {"on": False}

    def collect(self, spec, *_args):
        if spec.network["url"].endswith("/alt"):
            if failing["on"]:
                raise PriceMonitorError("附加地址超时")
            return [PriceRecord("demo-model", 3, 4, "USD/1M tokens", "https://totokens.test/api/alt", 0, {"group": "vip"})]
        return [_priced()]

    monkeypatch.setattr(NetworkAdapter, "collect", collect)
    config = load_config(_refresh_site_config(tmp_path, networks=[{"url": "https://totokens.test/api/alt"}]))
    store = Store(tmp_path / "monitor.db")
    run_once(config, store=store, client=_refresh_client())
    assert set(store.latest_all()) == {"totokens:demo-model:default", "totokens:demo-model:vip"}

    failing["on"] = True
    for _ in range(GROUP_REMOVED_MISSES):  # 多轮都缺 vip：没有防护时早就记 group_removed 并把 vip 摘掉
        run_once(config, store=store, client=_refresh_client())
    assert "totokens:demo-model:vip" in store.latest_all()
    assert not any(event["kind"] == "group_removed" for event in store.read_events(limit=20)[0])


def test_platform_pricing_records_parses_final_prices():
    """totokens 新版结构（platforms/supported_models/final_prices）：确定性直读每 token 单价并 ×1e6 换算。"""
    from llm_price_monitor.adapters import platform_pricing_records

    payload = {
        "code": "success",
        "data": [{
            "name": "demo",
            "platforms": [{
                "platform": "openai",
                "groups": [{"id": 3, "name": "plus-优惠", "rate_multiplier": 0.178}],
                "supported_models": [{
                    "name": "gpt-5.6-sol",
                    "platform": "openai",
                    "pricing": {
                        "billing_mode": "token",
                        "final_prices": [{
                            "group_id": 3,
                            "group_name": "plus-优惠",
                            "rate_multiplier": 0.178,
                            "billing_mode": "token",
                            "input_price": 8.9e-7,
                            "output_price": 5.34e-6,
                            "cache_read_price": 8.9e-8,
                            "cache_write_price": None,
                        }],
                    },
                }],
            }],
        }],
    }
    spec = SiteSpec(id="demo", models=[ModelTarget("gpt-5.6-sol")], network={"url": "https://demo.test/api/models"})
    records = platform_pricing_records(spec, [{"url": "https://demo.test/api/models", "status": 200, "resource_type": "fetch", "payload": payload}])
    assert len(records) == 1
    record = records[0]
    assert record.model == "gpt-5.6-sol"
    assert record.metadata["group"] == "plus-优惠"
    assert record.price_status == "confirmed"
    assert record.input_price == pytest.approx(0.89)
    assert record.output_price == pytest.approx(5.34)
    assert record.metadata["group_ratio"] == 0.178


def test_carry_last_price_keeps_auth_label_only_for_auth_placeholders():
    """占位沿用上次价格时：接口 401/403 才标需认证，AI/解析没映射出的占位不再误标。"""
    from llm_price_monitor.report import _carry_last_price

    previous = {"model": "gpt-5.6-sol", "input_price": 0.89, "output_price": 5.34, "captured_at": 1.0, "metadata": {"group": "gpt-plus-稳定"}}
    ai_missed = _carry_last_price(
        {"price_status": "unavailable", "captured_at": 2.0, "metadata": {"group": "gpt-plus-稳定", "pricing_kind": "unavailable", "notes": "AI 未返回"}},
        previous,
    )
    assert ai_missed["requires_auth"] is False
    assert ai_missed["input_price"] == 0.89
    auth = _carry_last_price(
        {"price_status": "unavailable", "captured_at": 2.0, "requires_auth": True, "metadata": {"group": "gpt-plus-稳定", "pricing_kind": "auth_required", "error": "HTTP 401"}},
        previous,
    )
    assert auth["requires_auth"] is True


def _fake_jwt(exp: int) -> str:
    """构造只有 exp 有效负载的假 JWT：base64url 三段式，足够 canonical_site_config 读取 exp。"""
    import base64
    import json

    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'HS256'})}.{part({'exp': exp})}.sig"


def test_canonical_site_config_slims_and_unifies_auth():
    """整理存量配置：瘦身＋把最新的手写 JWT 收编为站点级 auth_token，其余手写认证头移除。"""
    from llm_price_monitor.config import canonical_site_config

    old_token, new_token = _fake_jwt(exp=1000), _fake_jwt(exp=2000)
    config = {
        "id": "x",
        "adapter": "standard",
        "enabled": True,
        "auth_token": None,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "cookie": None,
        "cookies": {},
        "network": {"url": "https://x.test/api/pricing", "params": {}, "headers": {"Authorization": f"Bearer {old_token}", "referer": "https://x.test"}},
        "status": {"url": "https://x.test/api/metrics", "headers": {"Authorization": f"Bearer {new_token}"}},
        "notice": {"url": "https://x.test/api/notice"},
    }
    canonical, notes = canonical_site_config(config)
    assert canonical == {
        "id": "x",
        "enabled": True,  # 启用态始终落库：管理台行内判断直接读它
        "auth_token": new_token,  # 两份手写 JWT 里 exp 新的那份收编
        "network": {"url": "https://x.test/api/pricing", "headers": {"referer": "https://x.test"}},
        "status": {"url": "https://x.test/api/metrics"},
        "notice": {"url": "https://x.test/api/notice"},
    }
    assert any("收编" in note for note in notes)

    # 已有 auth_token 时以它为准：手写头只删不收编
    config["auth_token"] = "site-token"
    canonical, notes = canonical_site_config(config)
    assert canonical["auth_token"] == "site-token"
    assert any("以它为准" in note for note in notes)


def test_canonical_site_config_pops_response_sample_residue():
    """token_refresh 里残留的 response_sample（只在保存时供 AI 分析）整理时清掉；清空后整个键移除。"""
    from llm_price_monitor.config import canonical_site_config

    config = {
        "id": "x",
        "token_refresh": {"url": "https://x.test/auth/refresh", "response_sample": '{"data": {"access_token": "t"}}'},
    }
    canonical, notes = canonical_site_config(config)
    assert canonical == {"id": "x", "token_refresh": {"url": "https://x.test/auth/refresh"}}
    assert any("response_sample" in note for note in notes)

    canonical, _ = canonical_site_config({"id": "x", "token_refresh": {"response_sample": "{}"}})
    assert canonical == {"id": "x"}


def test_monitor_models_wildcard_rejected():
    from llm_price_monitor.config import settings_from_raw

    with pytest.raises(ValueError, match='不再支持通配符'):
        settings_from_raw({"monitor_models": ["*"]}, resolve_env=False)
    with pytest.raises(ValueError, match='不再支持通配符'):
        settings_from_raw({"monitor_models": ["*", "demo-model"]}, resolve_env=False)


def test_monitor_model_max_age_validation():
    from llm_price_monitor.config import settings_from_raw

    # 缺省 3 个月；合法输入原样保留，非法输入报错
    assert settings_from_raw({}, resolve_env=False).monitor_model_max_age_months == 3
    assert settings_from_raw({"monitor_model_max_age_months": 0}, resolve_env=False).monitor_model_max_age_months == 0
    assert settings_from_raw({"monitor_model_max_age_months": 6}, resolve_env=False).monitor_model_max_age_months == 6
    with pytest.raises(ValueError, match="monitor_model_max_age_months"):
        settings_from_raw({"monitor_model_max_age_months": -1}, resolve_env=False)
    with pytest.raises(ValueError, match="monitor_model_max_age_months"):
        settings_from_raw({"monitor_model_max_age_months": "半年"}, resolve_env=False)


def test_settings_fallback_proxy_validation():
    from llm_price_monitor.config import settings_from_raw

    parsed = settings_from_raw({"fallback_proxy": " http://172.17.0.1:7890 "}, resolve_env=False)
    assert parsed.fallback_proxy == "http://172.17.0.1:7890"
    # 带认证的写法合法（账号密码随后按所在消费方拆分）
    assert settings_from_raw({"fallback_proxy": "http://user:pass@host:7890"}, resolve_env=False).fallback_proxy == "http://user:pass@host:7890"
    assert settings_from_raw({"fallback_proxy": None}, resolve_env=False).fallback_proxy is None
    assert settings_from_raw({"fallback_proxy": ""}, resolve_env=False).fallback_proxy is None
    with pytest.raises(ValueError, match="fallback_proxy"):
        settings_from_raw({"fallback_proxy": "socks5://127.0.0.1:7890"}, resolve_env=False)
    with pytest.raises(ValueError, match="fallback_proxy"):
        settings_from_raw({"fallback_proxy": "172.17.0.1:7890"}, resolve_env=False)


def test_ai_extract_without_models_still_fails():
    spec = SiteSpec(id="demo", network={"url": "https://demo.test/pricing"})
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="k"))
    with pytest.raises(AIExtractionError, match="未配置目标模型"):
        extractor.extract(spec, "", [])


def _unavailable_result(names: list[str]) -> dict:
    return {
        "models": [
            {
                "model": name, "observed_model": name, "aliases": [],
                "input_price": None, "output_price": None,
                "unit": "CNY/1M tokens", "currency": "CNY",
                "status": "unavailable", "confidence": 0.0, "group": "default",
                "network_evidence": [], "page_evidence": [], "notes": "",
            }
            for name in names
        ],
        "cross_validation": {"status": "none", "conflicts": []},
    }


def _ai_extract_handler(seen_batches: list[list[str]], *, fail_second: bool = False):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if fail_second and calls["n"] == 2:
            return httpx.Response(500, json={"error": {"message": "boom"}})
        body = json.loads(request.read().decode())
        user = body["messages"][1]["content"]
        models_line = user.split("expected_models：\n", 1)[1].split("\n", 1)[0]
        batch = json.loads(models_line)
        seen_batches.append(batch)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(_unavailable_result(batch))}}]})

    return handler


def test_ai_extract_splits_expected_models_into_batches_of_four():
    spec = SiteSpec(id="demo", network={"url": "https://demo.test/pricing"})
    targets = [f"model-{index:02d}" for index in range(17)]
    seen_batches: list[list[str]] = []
    handler = _ai_extract_handler(seen_batches)
    # max_tokens=4000 → 4 条/批（旧默认口径，批大小随 max_tokens 折算）
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="k", max_tokens=4000))
    records = extractor.extract(
        spec, "", [],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        expected_models=targets,
    )
    # 批次 4 路并行发起，完成顺序不定；切批边界与最终记录覆盖不变
    assert sorted(seen_batches) == sorted([targets[0:4], targets[4:8], targets[8:12], targets[12:16], targets[16:17]])
    assert sorted(record.model for record in records) == targets


def test_ai_extract_batch_failure_fails_whole_round():
    spec = SiteSpec(id="demo", network={"url": "https://demo.test/pricing"})
    targets = [f"model-{index:02d}" for index in range(5)]
    seen_batches: list[list[str]] = []
    handler = _ai_extract_handler(seen_batches, fail_second=True)
    # max_tokens=4000 → 4 条/批，5 个模型才拆成 2 批，第二批失败才会发生
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="k", max_tokens=4000))
    with pytest.raises(AIExtractionError):
        extractor.extract(
            spec, "", [],
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            expected_models=targets,
        )
    # 第一批已成功也不返回半份数据；并行下第二批可能已同时开工，但结果整体丢弃
    assert 1 <= len(seen_batches) <= 2


def test_ai_cache_key_ignores_order_and_noise_fields():
    """new-api 系接口逐请求洗牌模型数组/分组列表、随机挂 pricing_version（cun 实测），
    缓存键必须只看内容：顺序无关、噪声字段剔除、整数浮点归一；内容真变键必须变。"""
    spec = SiteSpec(id="demo", network={"url": "https://demo.test/pricing"})
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="k"))
    url = "https://demo.test/api/pricing"

    def evidence(models: list[dict]) -> str:
        return json.dumps({"page": [], "network": [{"source": "model_list", "url": url, "quote": json.dumps(models)}]})

    first = [
        {"model_name": "m-1", "enable_groups": ["dev", "nrm"], "model_ratio": "0.5"},
        {"model_name": "m-2", "enable_groups": ["default"], "model_ratio": 1.0},
    ]
    # 同内容不同序：模型数组倒序、分组列表倒序、1.0 写成 1、多挂一个随机 pricing_version
    second = [
        {"model_name": "m-2", "enable_groups": ["default"], "model_ratio": 1, "pricing_version": "5a90f2b8"},
        {"model_name": "m-1", "enable_groups": ["nrm", "dev"], "model_ratio": "0.5"},
    ]
    changed = [dict(first[0], model_ratio="0.6"), first[1]]
    key = extractor._cache_key(spec, ["m-1"], evidence(first))
    assert key == extractor._cache_key(spec, ["m-1"], evidence(second))
    assert key != extractor._cache_key(spec, ["m-1"], evidence(changed))


class _FakeAliasCache:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}

    def cache_get(self, key: str) -> dict | None:
        return self.data.get(key)

    def cache_put(self, key: str, result: dict) -> None:
        self.data[key] = result


def test_ai_extract_aliases_single_call_filtered_and_cached():
    """轻量对照：单次调用出全量映射；别名收紧到站点名单，名单外/目标外的丢弃；同输入二走缓存。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"mappings": [
            {"model": "gpt-6-luna", "observed_model": "gpt-6-luna", "aliases": ["openai/gpt-6-luna", "幻觉名", "gpt-6-luna"]},
            {"model": "ghost-model", "observed_model": "名单外的名字", "aliases": ["也是幻觉"]},
            {"model": "gpt-6-sol", "observed_model": "gpt-6-sol", "aliases": []},
            {"model": "kimi-k3", "observed_model": "", "aliases": []},
        ]})}}]})

    spec = SiteSpec(id="demo", network={"url": "https://demo.test/api/pricing"})
    responses = [{
        "url": "https://demo.test/api/pricing",
        "resource_type": "fetch",
        "payload": {"data": [{"model_name": "gpt-6-luna"}, {"model_name": "openai/gpt-6-luna"}, {"model_name": "gpt-6-sol"}]},
    }]
    expected = ["gpt-6-luna", "ghost-model", "gpt-6-sol", "kimi-k3"]
    cache = _FakeAliasCache()
    extractor = AIPriceExtractor(AIConfig(
        enabled=True, base_url="https://ai.test/v1", models=("test-model",), api_key="k", cache=cache,
    ))
    details = extractor.extract_aliases(
        spec, responses, client=httpx.Client(transport=httpx.MockTransport(handler)), expected_models=expected,
    )
    assert calls["n"] == 1
    # 幻觉名（不在站点名单）被剔掉，名单内的重复项去重保留
    assert details["gpt-6-luna"] == {"observed_model": "gpt-6-luna", "aliases": ["openai/gpt-6-luna", "gpt-6-luna"]}
    # 原始名与别名全在名单外的条目、全空条目丢弃；原始名在名单内的正常保留
    assert set(details) == {"gpt-6-luna", "gpt-6-sol"}
    assert details["gpt-6-sol"] == {"observed_model": "gpt-6-sol", "aliases": []}
    # 同输入第二次直接命中缓存，不再发请求
    again = extractor.extract_aliases(
        spec, responses, client=httpx.Client(transport=httpx.MockTransport(handler)), expected_models=expected,
    )
    assert calls["n"] == 1
    assert again == details


def test_ai_extract_aliases_empty_site_names_skips_ai():
    """接口里没有模型名单时无从对照：返回空表且不发起 AI 调用。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    spec = SiteSpec(id="demo", network={"url": "https://demo.test/api/pricing"})
    extractor = AIPriceExtractor(AIConfig(enabled=True, base_url="https://ai.test/v1", models=("test-model",), api_key="k"))
    details = extractor.extract_aliases(
        spec,
        [{"url": "https://demo.test/api/pricing", "resource_type": "fetch", "payload": {"data": []}}],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        expected_models=["gpt-6-luna"],
    )
    assert details == {}
    assert calls["n"] == 0


def test_ai_config_rejects_max_tokens_below_4000():
    from llm_price_monitor.config import ai_from_raw

    with pytest.raises(ValueError, match="最低 4000"):
        ai_from_raw({"base_url": "https://ai.test/v1", "models": ["m"], "api_key": "k", "max_tokens": 3999}, cache=None)
    config = ai_from_raw({"base_url": "https://ai.test/v1", "models": ["m"], "api_key": "k", "max_tokens": 4000}, cache=None)
    assert config.max_tokens == 4000


def test_request_fallback_switches_model_on_invalid_output(monkeypatch):
    import llm_price_monitor.ai as ai_mod
    from llm_price_monitor.ai import json_content

    # 模型池是随机洗牌的，钉死顺序断言才稳定
    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)
    config = AIConfig(base_url="https://ai.test/v1", models=("bad-model", "good-model"), api_key="k")
    called_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        called_models.append(body["model"])
        content = "思考过程耗尽了全部 token" if body["model"] == "bad-model" else json.dumps({"models": []})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    model, response = request_with_model_fallback(
        config, "", "",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        validate=json_content,
    )
    assert model == "good-model"
    assert called_models == ["bad-model", "good-model"]


def test_request_fallback_budget_retry_on_truncated_output(monkeypatch):
    """正文非空且 token 用量顶格 = 预算截断：同模型放大 max_tokens 重试一次，而不是换模型。"""
    import llm_price_monitor.ai as ai_mod
    from llm_price_monitor.ai import json_content

    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)
    rows: list[dict] = []
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: rows.append(fields))

    config = AIConfig(base_url="https://ai.test/v1", models=("thinker",), api_key="k", max_tokens=4000)
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        sent.append(body)
        if body["max_tokens"] == 4000:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": '{"models": [{"model": "写了一半'}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 4000, "total_tokens": 5000},
            })
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps({"models": []})}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 1500, "total_tokens": 2500},
        })

    model, _ = request_with_model_fallback(
        config, "", "",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        validate=json_content,
    )
    assert model == "thinker"
    assert [item["max_tokens"] for item in sent] == [4000, 16000]
    assert [row["status"] for row in rows] == ["param_retry", "ok"]
    assert "已放大到 16000" in rows[0]["error"]
    assert rows[0]["completion_tokens"] == 4000


def test_request_fallback_no_budget_retry_below_limit(monkeypatch):
    """用量没顶格（模型纯粹输出了坏 JSON）不是预算问题：直接换下一个模型。"""
    import llm_price_monitor.ai as ai_mod
    from llm_price_monitor.ai import json_content

    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)

    config = AIConfig(base_url="https://ai.test/v1", models=("bad-model", "good-model"), api_key="k", max_tokens=4000)
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        sent.append(body)
        content = "不是 JSON 的内容" if body["model"] == "bad-model" else json.dumps({"models": []})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 500, "total_tokens": 510},
        })

    model, _ = request_with_model_fallback(
        config, "", "",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        validate=json_content,
    )
    assert model == "good-model"
    assert [item["model"] for item in sent] == ["bad-model", "good-model"]
    assert all(item["max_tokens"] == 4000 for item in sent)


def test_request_fallback_empty_answer_with_full_budget_skips_budget_retry(monkeypatch):
    """空正文 + 用量顶格是思考烧光预算：放大只会让它想得更久，直接换下一个模型。"""
    import llm_price_monitor.ai as ai_mod
    from llm_price_monitor.ai import json_content

    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)

    config = AIConfig(base_url="https://ai.test/v1", models=("blank-thinker", "good-model"), api_key="k", max_tokens=4000)
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        sent.append(body)
        content = "" if body["model"] == "blank-thinker" else json.dumps({"models": []})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4000, "total_tokens": 4010},
        })

    model, _ = request_with_model_fallback(
        config, "", "",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        validate=json_content,
    )
    assert model == "good-model"
    assert all(item["max_tokens"] == 4000 for item in sent)


def test_fallback_log_records_usage_and_finish_reason(monkeypatch):
    """校验失败的 fallback 日志要带 token 用量，报错尾部带停止原因，截断才定位得了。"""
    import llm_price_monitor.ai as ai_mod
    from llm_price_monitor.ai import json_content

    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)
    rows: list[dict] = []
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: rows.append(fields))

    config = AIConfig(base_url="https://ai.test/v1", models=("bad-model", "good-model"), api_key="k")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        content = "不是 JSON 的内容" if body["model"] == "bad-model" else json.dumps({"models": []})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3210, "completion_tokens": 980, "total_tokens": 4190},
        })

    request_with_model_fallback(
        config, "", "",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        validate=json_content,
    )
    fallback_row = rows[0]
    assert fallback_row["status"] == "fallback"
    assert fallback_row["prompt_tokens"] == 3210
    assert fallback_row["completion_tokens"] == 980
    assert fallback_row["total_tokens"] == 4190
    assert fallback_row["error"].endswith("｜finish=stop")


def test_validation_failure_cools_model_for_subsequent_calls(monkeypatch):
    """产出质量差（用量没顶格还坏 JSON）的模型进短期冷却，后续调用直接跳过。"""
    import llm_price_monitor.ai as ai_mod
    from llm_price_monitor.ai import json_content

    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)
    config = AIConfig(base_url="https://ai.test/v1", models=("bad-model", "good-model"), api_key="k")

    def garbage_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        content = "不是 JSON" if body["model"] == "bad-model" else json.dumps({"models": []})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 500, "total_tokens": 510},
        })

    request_with_model_fallback(
        config, "", "",
        client=httpx.Client(transport=httpx.MockTransport(garbage_handler)),
        validate=json_content,
    )
    assert ai_mod._MODEL_COOLDOWN["bad-model"] > time.time()
    assert "good-model" not in ai_mod._MODEL_COOLDOWN

    called_models: list[str] = []

    def ok_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        called_models.append(body["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"models": []})}}]})

    model, _ = request_with_model_fallback(
        config, "", "",
        client=httpx.Client(transport=httpx.MockTransport(ok_handler)),
        validate=json_content,
    )
    assert called_models == ["good-model"]
    assert model == "good-model"


def test_budget_truncation_does_not_cool_model(monkeypatch):
    """非空截断是预算问题不是模型的错：放大重试路径不进冷却名单。"""
    import llm_price_monitor.ai as ai_mod
    from llm_price_monitor.ai import json_content

    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)
    config = AIConfig(base_url="https://ai.test/v1", models=("thinker",), api_key="k")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        content = (
            '{"models": [{"model": "写一半'
            if body["max_tokens"] == 4000
            else json.dumps({"models": []})
        )
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": body["max_tokens"], "total_tokens": body["max_tokens"] + 10},
        })

    request_with_model_fallback(
        config, "", "",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        validate=json_content,
    )
    assert ai_mod._MODEL_COOLDOWN == {}


def test_request_fallback_amplify_skips_when_learned_ceiling_too_close(monkeypatch):
    """已学上限与当前预算几乎持平（如 qwen-turbo 上限 16384 对预算 16000）：放大救不了截断，
    直接换模型，不再白发 64000 的 400 和注定再截断的降额重试。"""
    import llm_price_monitor.ai as ai_mod
    from llm_price_monitor.ai import json_content

    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)
    monkeypatch.setattr(ai_mod, "_MODEL_MAX_TOKENS_LIMIT", {"qwen-turbo": 16384})
    rows: list[dict] = []
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: rows.append(fields))
    config = AIConfig(base_url="https://ai.test/v1", models=("qwen-turbo", "next-model"), api_key="k", max_tokens=16000)
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        sent.append(body)
        content = '{"models": [{"model": "写一半' if body["model"] == "qwen-turbo" else json.dumps({"models": []})
        usage = {"prompt_tokens": 1000, "completion_tokens": 16000, "total_tokens": 17000} if body["model"] == "qwen-turbo" else {"prompt_tokens": 10, "completion_tokens": 500, "total_tokens": 510}
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "length"}], "usage": usage})

    model, _ = request_with_model_fallback(
        config, "", "",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        validate=json_content,
    )
    assert model == "next-model"
    assert [item["max_tokens"] for item in sent] == [16000, 16000]  # 没有第二次对 qwen-turbo 的请求
    assert [(row["model"], row["status"]) for row in rows] == [("qwen-turbo", "fallback"), ("next-model", "ok")]
    assert "放大无余量" in rows[0]["error"]
    # 预算问题不是模型的错：不进冷却
    assert "qwen-turbo" not in ai_mod._MODEL_COOLDOWN


def test_request_fallback_detects_truncation_at_learned_ceiling(monkeypatch):
    """预算已被学到的上限钳小后顶格输出依旧算截断：不再误判成质量失败进冷却。"""
    import llm_price_monitor.ai as ai_mod
    from llm_price_monitor.ai import json_content

    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)
    monkeypatch.setattr(ai_mod, "_MODEL_MAX_TOKENS_LIMIT", {"capped": 8192})
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: None)
    config = AIConfig(base_url="https://ai.test/v1", models=("capped", "good-model"), api_key="k", max_tokens=16000)
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        sent.append(body)
        content = '{"models": [{"model": "写一半' if body["model"] == "capped" else json.dumps({"models": []})
        usage = {"prompt_tokens": 1000, "completion_tokens": 8192, "total_tokens": 9192} if body["model"] == "capped" else {"prompt_tokens": 10, "completion_tokens": 500, "total_tokens": 510}
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "length"}], "usage": usage})

    model, _ = request_with_model_fallback(
        config, "", "",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        validate=json_content,
    )
    assert model == "good-model"
    assert sent[0]["max_tokens"] == 8192  # 首次请求就被 ai_request 按学到的上限钳小
    assert [item["model"] for item in sent] == ["capped", "good-model"]  # 顶格截断没触发放大重试
    assert "capped" not in ai_mod._MODEL_COOLDOWN


def test_request_fallback_quota_error_cools_model(monkeypatch):
    """403 免费额度耗尽这类配额失败是模型整体不可用：短期冷却，后续调用不再每轮白付一次 403。"""
    import llm_price_monitor.ai as ai_mod
    from llm_price_monitor.ai import json_content

    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)
    monkeypatch.setattr(ai_mod, "_MODEL_COOLDOWN", {})
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: None)
    config = AIConfig(base_url="https://ai.test/v1", models=("quota-model", "good-model"), api_key="k")
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        called.append(body["model"])
        if body["model"] == "quota-model":
            return httpx.Response(403, json={"error": {"message": "Free quota exhausted. To continue accessing the model on a paid basis, please add funds."}})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"models": []})}}]})

    model, _ = request_with_model_fallback(
        config, "", "",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        validate=json_content,
    )
    assert model == "good-model"
    assert called == ["quota-model", "good-model"]
    assert ai_mod._MODEL_COOLDOWN["quota-model"] > time.time()

    # 冷却生效：下一轮直接从 good-model 开始，不再向 quota-model 白付 403
    model, _ = request_with_model_fallback(
        config, "", "",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        validate=json_content,
    )
    assert model == "good-model"
    assert called == ["quota-model", "good-model", "good-model"]


def test_fallback_log_saves_raw_response_tail(monkeypatch):
    """fallback 日志要存模型实际回复的原文尾部，坏在哪一眼可见。"""
    import llm_price_monitor.ai as ai_mod
    from llm_price_monitor.ai import json_content

    monkeypatch.setattr(ai_mod.random, "shuffle", lambda value: None)
    rows: list[dict] = []
    monkeypatch.setattr(ai_mod, "ai_log_hook", lambda **fields: rows.append(fields))

    bad_answer = '{"models": [{"model": "demo", "input_price": 0, "output_price": 0, "note": "写到一半没了'
    config = AIConfig(base_url="https://ai.test/v1", models=("bad-model", "good-model"), api_key="k")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        content = bad_answer if body["model"] == "bad-model" else json.dumps({"models": []})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    request_with_model_fallback(
        config, "", "",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        validate=json_content,
    )
    assert rows[0]["status"] == "fallback"
    from llm_price_monitor.store import _AI_LOG_TEXT_CHARS

    assert rows[0]["response_excerpt"] == bad_answer[-_AI_LOG_TEXT_CHARS:]


def test_json_content_empty_answer_has_explicit_message():
    from llm_price_monitor.ai import AIExtractionError, json_content

    with pytest.raises(AIExtractionError, match="回复正文为空"):
        json_content("   ")


def test_prompt_too_long_recognizes_dashscope_input_length_error():
    from llm_price_monitor.ai import _prompt_too_long

    exc = httpx.HTTPStatusError(
        "client error",
        request=httpx.Request("POST", "https://ai.test/v1/chat/completions"),
        response=httpx.Response(400, json={"error": {
            "code": "InvalidParameter",
            "message": "<400> InternalError.Algo.InvalidParameter: Range of input length should be [1, 129024]",
        }}),
    )
    assert _prompt_too_long(exc) is True


def test_evidence_ladder_shrinks_on_dashscope_input_length_error():
    """证据超长：供应商报"Range of input length"也要触发降档，而不是整轮失败。"""
    spec = SiteSpec(id="demo", models=(ModelTarget("gpt-5"),), network={"url": "https://demo.test/pricing"})
    big_page = ("模型卡片 gpt-5 输入 1 输出 2 元 " * 30 + "\n") * 1200  # ~60 万字符，任何一档都装不下之前的完整原文
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        content = body["messages"][-1]["content"]
        sizes.append(len(content))
        if len(content) > 120_000:
            return httpx.Response(400, json={"error": {
                "code": "InvalidParameter",
                "message": "<400> InternalError.Algo.InvalidParameter: Range of input length should be [1, 129024]",
            }})
        result = {
            "models": [{
                "model": "gpt-5", "observed_model": "gpt-5", "aliases": [],
                "input_price": 1, "output_price": 2, "unit": "CNY/1M tokens", "currency": "CNY",
                "status": "confirmed", "confidence": 0.9, "group": "default",
                "network_evidence": [], "page_evidence": [], "notes": "",
            }],
            "cross_validation": {"status": "none", "conflicts": []},
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="k"))
    records = extractor.extract(spec, big_page, [], client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert [record.model for record in records] == ["gpt-5"]
    assert len(sizes) >= 2
    assert sizes[0] > 120_000
    assert sizes[-1] <= 120_000


def test_ai_extract_batch_size_scales_with_max_tokens():
    """批大小随 max_tokens 折算：16000 预算 → 12 条/批（70 模型 6 批而非 18 批）。"""
    spec = SiteSpec(id="demo", network={"url": "https://demo.test/pricing"})
    targets = [f"model-{index:02d}" for index in range(17)]
    seen_batches: list[list[str]] = []
    handler = _ai_extract_handler(seen_batches)
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="k"))
    records = extractor.extract(
        spec, "", [],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        expected_models=targets,
    )
    assert sorted(seen_batches) == sorted([targets[0:12], targets[12:17]])
    assert sorted(record.model for record in records) == targets
