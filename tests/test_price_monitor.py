import json
from pathlib import Path

import httpx
import pytest

from llm_price_monitor.adapters import ADAPTERS, NetworkAdapter, headers as _headers, network_pricing_records as _network_pricing_records
from llm_price_monitor.ai import AIExtractionError, AIPriceExtractor, NEWAPI_ONEAPI_PRICING_GUIDANCE, ai_content, ai_request, ping_model
from llm_price_monitor.config import AIConfig, ModelTarget, PriceMonitorError, SiteSpec, load_config, sites_from_raw
from llm_price_monitor.evidence import decode_response_body as _decode_response_body, is_preferred_response_url as _is_preferred_response_url, repair_mojibake as _repair_mojibake, target_page_text as _target_page_text
from llm_price_monitor.report import classify, fingerprint, run_once, summary_row as _summary_row
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

    extractor = AIPriceExtractor(config or AIConfig(base_url="https://ai.test/v1", model="test-model", api_key="k"))
    extractor.extract(spec, page_text, responses, client=httpx.Client(transport=httpx.MockTransport(handler)), page_sources=page_sources)
    return captured["body"]


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
    assert [event["kind"] for event in first.events] == ["new"]
    assert first.records[0]["price_status"] == "confirmed"

    payload["data"][0]["official"]["output"] = 3
    second = run_once(config, store=store, client=httpx.Client())
    assert [event["kind"] for event in second.events] == ["changed"]
    assert store.count_history() == 2
    assert len(store.latest_all()) == 1


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
    # 遗留占位在上一轮持久化时已被清理，这里 previous 为空，按新增建档
    assert [event["kind"] for event in recovered.events] == ["new"]

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


def test_network_adapter_uses_ai_alias_before_newapi_calculation(monkeypatch):
    def fake_extract(self, spec, page_text, responses, **kwargs):
        return [PriceRecord(
            "informal-gpt", None, None, "CNY/1M tokens", "", 0,
            {"observed_model": "openai/gpt-5.6-sol", "aliases": ["GPT-5.6 Sol"]},
            "candidate",
        )]

    monkeypatch.setattr("llm_price_monitor.ai.AIPriceExtractor.extract", fake_extract)
    spec = SiteSpec(
        id="demo",
        models=(ModelTarget("informal-gpt"),),
        network={"url": "https://demo.test/api/pricing"},
    )
    ai = AIConfig(enabled=True, base_url="https://ai.test/v1", model="test-model", api_key="key")
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
    ai = AIConfig(enabled=True, base_url="https://ai.test/v1", model="test-model", api_key="key")
    records = NetworkAdapter().collect(spec, client, 5, BROWSER_USER_AGENTS[0], ai)
    assert records[0].input_price == 1
    assert "demo-model" in seen["page_text"]
    assert seen["payload"] is None


def test_browser_adapter_expands_environment_variables_in_headers(monkeypatch):
    monkeypatch.setenv("PRICE_TOKEN", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"data": [{"model_name": "demo-model", "input_price": 1, "output_price": 2}]})

    spec = SiteSpec(
        id="env-header",
        models=(ModelTarget("demo-model"),),
        network={"url": "https://demo.test/api/pricing", "headers": {"Authorization": "Bearer ${PRICE_TOKEN}"}},
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


def test_ai_config_always_uses_config_values(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRICE_MONITOR_AI_BASE_URL", "https://env-ai.test/v1")
    monkeypatch.setenv("PRICE_MONITOR_AI_MODEL", "env-model")
    config = load_config(_write_config(tmp_path, {"ai": {"base_url": "", "model": ""}, "settings": {}, "sites": [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}]}))
    assert config.ai.base_url == ""
    assert config.ai.model == ""

    explicit = load_config(_write_config(tmp_path, {"ai": {"base_url": "https://config-ai.test/v1", "model": "config-model"}, "settings": {}, "sites": [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}]}))
    assert explicit.ai.base_url == "https://config-ai.test/v1"
    assert explicit.ai.model == "config-model"


def test_ai_models_list_is_parsed_and_pick_model_randomly_chooses_one(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {"ai": {"base_url": "https://ai.test/v1", "models": ["m-a", "m-b", "m-a", " "], "model": "m-c"}, "settings": {}, "sites": [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}]}))
    assert config.ai.models == ("m-a", "m-b")
    for _ in range(20):
        assert config.ai.pick_model() in {"m-a", "m-b", "m-c"}


def test_ai_models_rejects_non_string_entries(tmp_path: Path):
    with pytest.raises(ValueError, match="ai.models 必须是字符串数组"):
        load_config(_write_config(tmp_path, {"ai": {"base_url": "https://ai.test/v1", "models": ["m-a", 1]}, "settings": {}, "sites": [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}]}))


def test_ai_api_format_is_validated(tmp_path: Path):
    sites = [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}]
    config = load_config(_write_config(tmp_path, {"ai": {"base_url": "https://ai.test/v1", "api_format": "anthropic"}, "settings": {}, "sites": sites}))
    assert config.ai.api_format == "anthropic"

    with pytest.raises(ValueError, match="ai.api_format"):
        load_config(_write_config(tmp_path, {"ai": {"base_url": "https://ai.test/v1", "api_format": "bogus"}, "settings": {}, "sites": sites}))


def test_ai_request_builds_openai_responses_payload():
    config = AIConfig(base_url="https://api.openai.com", model="gpt-x", api_key="sk-oai", api_format="openai_responses")
    url, headers, body = ai_request(config, "gpt-x", "系统提示", "用户内容")
    assert url == "https://api.openai.com/v1/responses"
    assert headers["authorization"] == "Bearer sk-oai"
    assert body["input"] == [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "用户内容"},
    ]
    assert body["max_output_tokens"] == 4000
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
    config = AIConfig(base_url="https://api.anthropic.com", model="claude-x", api_key="sk-ant", api_format="anthropic")
    url, headers, body = ai_request(config, "claude-x", "系统提示", "用户内容")
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "sk-ant"
    assert headers["anthropic-version"] == "2023-06-01"
    assert body == {
        "model": "claude-x",
        "max_tokens": 4000,
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
    assert body["generationConfig"]["maxOutputTokens"] == 4000

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
    config = AIConfig(base_url="https://api.anthropic.com", model="claude-x", api_key="k", api_format="anthropic")
    extractor = AIPriceExtractor(config)
    records = extractor.extract(spec, "", [{"url": "https://demo.test/api/price", "resource_type": "fetch", "status": 200, "payload": {"items": [{"name": "demo-model", "in": 1, "out": 2}]}}], client=httpx.Client(transport=httpx.MockTransport(handler)))

    # AI 响应未含目标模型时按 unavailable 落一条记录
    assert [record.model for record in records] == ["demo-model"]
    assert records[0].price_status == "unavailable"
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["body"]["model"] == "claude-x"
    assert "system" in seen["body"]


def test_ai_request_uses_random_model_from_models_list():
    spec = SiteSpec(id="demo", models=(ModelTarget("demo-model"),))
    config = AIConfig(base_url="https://ai.test/v1", models=("m-a", "m-b"), api_key="k")
    for _ in range(20):
        body = _ai_request_body(spec, "", [{"url": "https://demo.test/api/price", "resource_type": "fetch", "status": 200, "payload": {"items": [{"name": "demo-model", "in": 1, "out": 2}]}}], config=config)
        assert body["model"] in {"m-a", "m-b"}


def test_ai_api_key_comes_from_config(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {
        "ai": {
            "base_url": "https://config-ai.test/v1",
            "model": "config-model",
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
            "model": "config-model",
            "max_tokens": 1234,
            "enable_thinking": False,
        },
        "settings": {},
        "sites": [{"id": "demo", "adapter": "standard", "model_list_url": "https://demo.test/pricing", "models": []}],
    }))

    assert config.ai.max_tokens == 1234
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
    with pytest.raises(PriceMonitorError, match="不会自动检测所有模型"):
        NetworkAdapter().collect(
            SiteSpec(id="demo", adapter="browser", network={"url": "https://demo.test/pricing"}),
            httpx.Client(),
            1,
            BROWSER_USER_AGENTS[0],
            AIConfig(base_url="https://ai.test/v1", model="test-model"),
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
        "settings": {},
        "sites": [{"id": "demo", "adapter": "standard", "models": ["demo-model"]}],
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

    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model", api_key="ai-secret"))
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
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model", api_key="secret"))
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
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model"))
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


def test_ai_result_merges_context_tiers_and_keeps_unified_default_rule():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model"))
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
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model"))
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

    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model", api_key="secret"))
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
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model"))

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
        "settings": {"history_file": str(tmp_path / "history.jsonl"), "latest_file": str(tmp_path / "latest.json"), "event_file": str(tmp_path / "events.jsonl")},
        "sites": [
            {"id": "good", "models": ["demo-model"]},
            {"id": "bad", "adapter": "missing", "models": ["demo-model"]},
            {"id": "off", "enabled": False, "models": ["demo-model"]},
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
        {"id": "chunky", "models": ["gpt-5.6-sol"], "network": {"url": "https://example.com/dashboard/pricing"}},
    ])

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
            "models": ["gpt-5.6-sol"],
            "network": {
                "url": "https://example.com/dashboard/pricing",
                "ratio_url": "https://example.com/api/public/model-pricing",
            },
        },
    ])

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



def test_group_removed_event_after_two_misses(tmp_path: Path, monkeypatch):
    """分组连续两轮没出现才记 group_removed 并从快照摘除；单轮缺失不报（容忍抖动）。"""
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
    first = run_once(config, store=store, client=httpx.Client())
    assert [event["kind"] for event in first.events] == []  # 第一轮缺失只计数
    assert len(store.latest_all()) == 2

    second = run_once(config, store=store, client=httpx.Client())
    assert [event["kind"] for event in second.events] == ["group_removed"]
    assert list(store.latest_all()) == ["demo:demo-model:default"]
    removed = next(event for event in store.read_events(limit=10)[0] if event["kind"] == "group_removed")
    assert removed["previous"]["metadata"]["group"] == "vip"


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

    first = run_once(config, store=store, client=httpx.Client())
    assert [event["kind"] for event in first.events] == []  # 真实缺失第一轮只计数
    second = run_once(config, store=store, client=httpx.Client())
    assert [event["kind"] for event in second.events] == ["group_removed"]

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

    for _ in range(2):  # 连续两轮"续签成功但无数据"，正好踩到 group_removed 的判定阈值
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
    for _ in range(2):  # 两轮都缺 vip：没有防护时第二轮就记 group_removed 并把 vip 摘掉
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
