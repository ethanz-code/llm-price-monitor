import json
from pathlib import Path

import httpx
import pytest

from llm_price_monitor.adapters import BrowserAdapter, headers as _headers, network_pricing_records as _network_pricing_records
from llm_price_monitor.ai import AIDryRun, AIPriceExtractor, NEWAPI_ONEAPI_PRICING_GUIDANCE
from llm_price_monitor.config import AIConfig, ModelTarget, PriceMonitorError, SiteSpec, load_config
from llm_price_monitor.evidence import decode_response_body as _decode_response_body, is_preferred_response_url as _is_preferred_response_url, repair_mojibake as _repair_mojibake, target_page_text as _target_page_text
from llm_price_monitor.report import run_once, summary_row as _summary_row
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
            "adapter": "browser",
            "model_list_url": "https://demo.test/pricing",
            "models": ["demo-model"]
        }]
    }


def test_monitor_writes_snapshot_and_detects_price_change(tmp_path: Path, monkeypatch):
    payload = {"data": [{"model_name": "demo-model", "official": {"input": 1, "output": 2, "unit": "USD/1M tokens"}}]}

    def collect(*_args):
        item = payload["data"][0]
        return [PriceRecord("demo-model", item["official"]["input"], item["official"]["output"], item["official"]["unit"], "https://demo.test/pricing", 0, {})]

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config(tmp_path)), encoding="utf-8")
    config = load_config(config_path)
    store = Store(tmp_path / "monitor.db")
    monkeypatch.setattr(BrowserAdapter, "collect", collect)
    first = run_once(config, store=store, client=httpx.Client())
    assert [event["kind"] for event in first.events] == ["new"]
    assert first.records[0]["price_status"] == "confirmed"

    payload["data"][0]["official"]["output"] = 3
    second = run_once(config, store=store, client=httpx.Client())
    assert [event["kind"] for event in second.events] == ["changed"]
    assert store.count_history() == 2
    assert len(store.latest_all()) == 1


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
        model_list_url="https://demo.test/pricing",
        models=(ModelTarget("demo-model", group="gpt pro"),),
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
    records = BrowserAdapter().collect(spec, client, 5, BROWSER_USER_AGENTS[0], ai)
    assert records[0].price_status == "confirmed"
    assert records[0].metadata["observed_model"] == "openai/gpt-5.6-sol"


def test_browser_adapter_requires_ai_for_non_newapi_json():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"pricing": [{"model_display": "GPT-5.5", "input_rmb": 1.5, "output_rmb": 9, "cache_read_rmb": 0.15}]})

    spec = SiteSpec(
        id="mapped",
        model_list_url="https://mapped.test/pricing",
        models=(ModelTarget("GPT-5.5"),),
        network={"url": "https://mapped.test/api/public-models"},
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(PriceMonitorError, match="未配置可用 AI"):
        BrowserAdapter().collect(spec, client, 5, BROWSER_USER_AGENTS[0])
    assert requests


def test_browser_adapter_passes_custom_request_configuration():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.params["locale"] == "zh-CN"
        assert request.headers["x-client"] == "price-monitor"
        assert json.loads(request.content) == {"models": ["demo-model"]}
        return httpx.Response(200, json={"result": {"data": [{"model_name": "demo-model", "input_price": 1, "output_price": 2}]}})

    spec = SiteSpec(
        id="custom-request",
        models=(ModelTarget("demo-model"),),
        network={
            "url": "https://demo.test/api/pricing",
            "method": "POST",
            "params": {"locale": "zh-CN"},
            "headers": {"x-client": "price-monitor"},
            "body_type": "json",
            "body": {"models": ["demo-model"]},
            "response_path": "result",
        },
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    records = BrowserAdapter().collect(spec, client, 5, BROWSER_USER_AGENTS[0])
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
    records = BrowserAdapter().collect(spec, client, 5, BROWSER_USER_AGENTS[0], ai)
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
    records = BrowserAdapter().collect(spec, client, 5, BROWSER_USER_AGENTS[0])
    assert records[0].input_price == 1


def test_ai_request_always_includes_newapi_oneapi_guidance():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model", dry_run=True))
    spec = SiteSpec(id="demo", models=(ModelTarget("demo-model"),))
    with pytest.raises(AIDryRun) as raised:
        extractor.extract(spec, "", [{"url": "https://demo.test/api/price", "resource_type": "fetch", "status": 200, "payload": {"items": [{"name": "demo-model", "in": 1, "out": 2}]}}])
    system = raised.value.preview["ai_request"]["request_body"]["messages"][0]["content"]
    assert NEWAPI_ONEAPI_PRICING_GUIDANCE in system


def test_site_config_requires_positive_ratio_base_price(tmp_path: Path):
    with pytest.raises(ValueError, match="ratio_base_price 必须是正数"):
        load_config(_write_config(tmp_path, {
            "settings": {},
            "sites": [{
                "id": "demo",
                "model_list_url": "https://demo.test/pricing",
                "models": ["demo-model"],
                "ratio_base_price": 0,
            }],
        }))


def test_site_currency_defaults_to_cny_and_validates_supported_values(tmp_path: Path):
    default = load_config(_write_config(tmp_path, {
        "settings": {},
        "sites": [{"id": "demo", "model_list_url": "https://demo.test/pricing", "models": ["demo-model"]}],
    }))
    assert default.sites[0].currency == "CNY"

    usd = load_config(_write_config(tmp_path, {
        "settings": {},
        "sites": [{"id": "demo", "model_list_url": "https://demo.test/pricing", "models": ["demo-model"], "currency": "usd"}],
    }))
    assert usd.sites[0].currency == "USD"

    with pytest.raises(ValueError, match="currency 必须是 CNY 或 USD"):
        load_config(_write_config(tmp_path, {
            "settings": {},
            "sites": [{"id": "demo", "model_list_url": "https://demo.test/pricing", "models": ["demo-model"], "currency": "EUR"}],
        }))


def test_preferred_response_url_matches_press_model_variants_and_loads_config(tmp_path: Path):
    assert _is_preferred_response_url("https://demo.test/api/press-model-pricing", ("price", "model"))
    assert _is_preferred_response_url("https://demo.test/api/press_model", ("price", "model"))
    assert _is_preferred_response_url("https://demo.test/api/pricing", ("price", "model"))
    assert _is_preferred_response_url("https://demo.test/api/model-list", ("price", "model"))
    assert not _is_preferred_response_url("https://demo.test/api/status", ("price", "model"))
    assert not _is_preferred_response_url("https://modelflare.test/api/setup", ("price", "model"))
    config = load_config(_write_config(tmp_path, {
        "settings": {},
        "sites": [{"id": "demo", "model_list_url": "https://demo.test/pricing", "models": ["demo-model"], "preferred_response_url_patterns": ["pricing-data"]}],
    }))
    assert config.sites[0].preferred_response_url_patterns == ("pricing-data",)


def test_preferred_response_bypasses_price_text_heuristic_and_is_kept_for_ai():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model", dry_run=True))
    spec = SiteSpec(id="demo", model_list_url="https://demo.test/pricing", models=(ModelTarget("demo-model"),))
    response = {
        "source": "model_list",
        "url": "https://demo.test/api/model-config",
        "resource_type": "xhr",
        "payload": {"data": [{"model_name": "demo-model", "tier": "large-context", "value": 278000}]},
    }
    with pytest.raises(AIDryRun) as raised:
        extractor.extract(spec, "", [response])
    evidence = json.loads(
        raised.value.preview["ai_request"]["request_body"]["messages"][1]["content"].split("网页证据：\n", 1)[1]
    )["network_evidence"]
    assert len(evidence) == 1
    assert "278000" in evidence[0]["quote"]


def test_dom_source_is_kept_even_when_target_model_card_is_not_detected():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model", dry_run=True))
    spec = SiteSpec(id="demo", model_list_url="https://demo.test/pricing", models=(ModelTarget("demo-model"),))
    with pytest.raises(AIDryRun) as raised:
        extractor.extract(spec, "站点动态内容尚未识别，但这里有原始 DOM 文本", [])
    content = raised.value.preview["ai_request"]["request_body"]["messages"][1]["content"]
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
    config = load_config(_write_config(tmp_path, {"ai": {"base_url": "", "model": ""}, "settings": {}, "sites": [{"id": "demo", "adapter": "browser", "model_list_url": "https://demo.test/pricing", "models": []}]}))
    assert config.ai.base_url == ""
    assert config.ai.model == ""

    explicit = load_config(_write_config(tmp_path, {"ai": {"base_url": "https://config-ai.test/v1", "model": "config-model"}, "settings": {}, "sites": [{"id": "demo", "adapter": "browser", "model_list_url": "https://demo.test/pricing", "models": []}]}))
    assert explicit.ai.base_url == "https://config-ai.test/v1"
    assert explicit.ai.model == "config-model"


def test_ai_models_list_is_parsed_and_pick_model_randomly_chooses_one(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {"ai": {"base_url": "https://ai.test/v1", "models": ["m-a", "m-b", "m-a", " "], "model": "m-c"}, "settings": {}, "sites": [{"id": "demo", "adapter": "browser", "model_list_url": "https://demo.test/pricing", "models": []}]}))
    assert config.ai.models == ("m-a", "m-b")
    for _ in range(20):
        assert config.ai.pick_model() in {"m-a", "m-b", "m-c"}


def test_ai_models_rejects_non_string_entries(tmp_path: Path):
    with pytest.raises(ValueError, match="ai.models 必须是字符串数组"):
        load_config(_write_config(tmp_path, {"ai": {"base_url": "https://ai.test/v1", "models": ["m-a", 1]}, "settings": {}, "sites": [{"id": "demo", "adapter": "browser", "model_list_url": "https://demo.test/pricing", "models": []}]}))


def test_ai_request_uses_random_model_from_models_list():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", models=("m-a", "m-b"), dry_run=True))
    spec = SiteSpec(id="demo", models=(ModelTarget("demo-model"),))
    for _ in range(20):
        with pytest.raises(AIDryRun) as raised:
            extractor.extract(spec, "", [{"url": "https://demo.test/api/price", "resource_type": "fetch", "status": 200, "payload": {"items": [{"name": "demo-model", "in": 1, "out": 2}]}}])
        assert raised.value.preview["ai_request"]["request_body"]["model"] in {"m-a", "m-b"}


def test_ai_api_key_comes_from_config(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {
        "ai": {
            "base_url": "https://config-ai.test/v1",
            "model": "config-model",
            "api_key": "secret",
        },
        "settings": {},
        "sites": [{"id": "demo", "adapter": "browser", "model_list_url": "https://demo.test/pricing", "models": []}],
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
        "sites": [{"id": "demo", "adapter": "browser", "model_list_url": "https://demo.test/pricing", "models": []}],
    }))

    assert config.ai.max_tokens == 1234
    assert config.ai.enable_thinking is False


def test_id_only_site_is_an_disabled_placeholder(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {
        "settings": {},
        "sites": [{"id": "placeholder"}],
    }))

    assert config.sites[0].id == "placeholder"
    assert config.sites[0].adapter == "browser"
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
        BrowserAdapter().collect(
            SiteSpec(id="demo", adapter="browser", model_list_url="https://demo.test/pricing"),
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
            "adapter": "browser",
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
    config = load_config(_write_config(tmp_path, {"settings": {}, "sites": [{"id": "demo", "adapter": "browser", "model_list_url": "https://demo.test/pricing", "models": []}]}))
    assert choose_user_agent(config) == BROWSER_USER_AGENTS[0]
    monkeypatch.setattr("llm_price_monitor.useragent.secrets.choice", lambda values: values[-1])
    randomized = load_config(_write_config(tmp_path, {"settings": {"random_user_agent": True}, "sites": [{"id": "demo", "adapter": "browser", "model_list_url": "https://demo.test/pricing", "models": []}]}))
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
        "sites": [{"id": "demo", "adapter": "browser", "model_list_url": "https://demo.test/pricing", "models": []}],
    }))
    selected = choose_user_agent(config)
    assert "Windows NT 10.0; Win64; x64" in selected
    assert any(f"Chrome/{version}" in selected for version in ("150.0.0.0", "151.0.0.0", "152.0.0.0"))


def test_random_user_agent_rejects_invalid_platform(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {
        "settings": {"random_user_agent": True, "user_agent_platforms": ["android"]},
        "sites": [{"id": "demo", "adapter": "browser", "model_list_url": "https://demo.test/pricing", "models": []}],
    }))
    with pytest.raises(ValueError, match="user_agent_platforms"):
        choose_user_agent(config)


def test_browser_probe_reports_no_discoverable_price_data(tmp_path: Path, monkeypatch):
    config = load_config(_write_config(tmp_path, {
        "settings": {"history_file": str(tmp_path / "history.jsonl"), "latest_file": str(tmp_path / "latest.json"), "event_file": str(tmp_path / "events.jsonl")},
        "sites": [{"id": "demo", "adapter": "browser", "model_list_url": "https://demo.test/pricing", "models": []}]
    }))
    monkeypatch.setattr("llm_price_monitor.adapters.BrowserAdapter.collect", lambda *_args: (_ for _ in ()).throw(PriceMonitorError("页面已打开，但没有捕获到可识别的价格 JSON/模型")))
    report = run_once(config, client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))))
    assert report.errors[0]["site_id"] == "demo"


def test_browser_adapter_requires_model_list_url(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {
        "settings": {},
        "sites": [{"id": "demo", "adapter": "browser", "models": []}],
    }))
    with pytest.raises(PriceMonitorError, match="model_list_url"):
        BrowserAdapter().collect(config.sites[0], httpx.Client(), 1, BROWSER_USER_AGENTS[0], config.ai)


def test_browser_config_uses_model_list_url_without_source_url(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {
        "settings": {},
        "sites": [{"id": "hao", "adapter": "browser", "model_list_url": "https://hao.test/zh/models", "models": ["gpt-5.6-sol"]}],
    }))

    assert config.sites[0].model_list_url == "https://hao.test/zh/models"


def test_site_config_rejects_base_url(tmp_path: Path):
    with pytest.raises(ValueError, match="不再支持 base_url"):
        load_config(_write_config(tmp_path, {
            "settings": {},
            "sites": [{"id": "demo", "adapter": "browser", "base_url": "https://demo.test", "model_list_url": "https://demo.test/pricing"}],
        }))


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
                "adapter": "browser",
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
    import os
    os.environ["PRICE_MONITOR_AI_API_KEY"] = "ai-secret"
    try:
            records = extractor.extract(
                SiteSpec(id="demo", adapter="browser", model_list_url="https://demo.test/pricing?token=page-secret", models=(ModelTarget("demo-model"),)),
                "demo-model Input 5 Output 30 USD/1M tokens",
                [
                    {"url": "https://demo.test/api/v9/price?token=api-secret", "status": 200, "resource_type": "fetch", "content_type": "application/json", "payload": {"model_name": "demo-model", "input": 5, "output": 30, "Authorization": "auth-secret", "Cookie": "session=cookie-secret"}},
                    {"url": "https://demo.test/api/v9/price-details", "status": 200, "resource_type": "xhr", "content_type": "text/html", "text": "demo-model official_input_price_per_1m_micro_usd=5000000"},
                ],
                client=client,
            )
    finally:
        os.environ.pop("PRICE_MONITOR_AI_API_KEY", None)
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


def test_ai_dry_run_keeps_full_model_list_response_for_ai():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model", dry_run=True))
    spec = SiteSpec(
        id="hao",
        adapter="browser",
        model_list_url="https://hao.test/zh/models",
        models=(ModelTarget("gpt-5.6-sol"),),
    )
    page_text = "openai/gpt-5.6-sol 输入 Token $1/M 输出 Token $2/M anthropic/claude-4 输入 Token $3/M 输出 Token $4/M"
    responses = [
        {"url": "https://hao.test/api/login", "status": 200, "resource_type": "fetch", "text": "login success"},
        {"url": "https://hao.test/api/prices", "status": 200, "resource_type": "fetch", "payload": {"models": [{"id": "openai/gpt-5.6-sol", "input": 1, "output": 2}, {"id": "anthropic/claude-4", "input": 3, "output": 4}]}},
        {"url": "https://hao.test/api/price-details", "status": 200, "resource_type": "xhr", "text": "openai/gpt-5.6-sol official_input_price=1 official_output_price=2"},
    ]

    with pytest.raises(AIDryRun) as raised:
        extractor.extract(spec, page_text, responses)

    preview = raised.value.preview
    summary = preview["evidence_summary"]
    assert summary["target_models"] == ["gpt-5.6-sol"]
    assert summary["verification_mode"] == "model_list_only"
    assert summary["network_capture_scope"] == ["fetch", "xhr"]
    assert summary["page_evidence_count"] == 1
    assert "filtered_page_text" not in json.dumps(preview, ensure_ascii=False)
    assert summary["network_evidence_count"] == 1
    request_content = preview["ai_request"]["request_body"]["messages"][1]["content"]
    assert "login success" not in request_content
    assert "page_evidence" in request_content
    assert "network_evidence" in request_content
    assert preview["ai_request"]["request_body"]["messages"][0]["content"]
    assert "system_prompt" not in preview
    request_evidence = json.loads(request_content.split("网页证据：\n", 1)[1])
    model_list_quote = next(item["quote"] for item in request_evidence["network_evidence"] if item["url"] == "https://hao.test/api/prices")
    assert '"anthropic/claude-4"' in model_list_quote
    assert '"input":3' in model_list_quote


def test_network_evidence_requires_price_like_number_not_model_metadata():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model", dry_run=True))
    spec = SiteSpec(id="hao", adapter="browser", model_list_url="https://hao.test/zh/models", models=(ModelTarget("gpt-5.6-sol"),))

    with pytest.raises(AIDryRun) as raised:
        extractor.extract(
            spec,
            "openai/gpt-5.6-sol 输入 Token $1/M 输出 Token $2/M",
            [
                {"url": "https://hao.test/api/model", "status": 200, "resource_type": "fetch", "text": "gpt-5.6-sol context_length=372000 release_date=2026-07-09"},
                {"url": "https://hao.test/_next/static/chunks/app/page.rsc", "status": 200, "resource_type": "fetch", "text": 'gpt-5.6-sol metadata "$1" "$5"'},
                {"url": "https://hao.test/api/price", "status": 200, "resource_type": "fetch", "text": "gpt-5.6-sol input_price=1 output_price=2"},
            ],
        )

    request_content = raised.value.preview["ai_request"]["request_body"]["messages"][1]["content"]
    evidence = json.loads(request_content.split("网页证据：\n", 1)[1])["network_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["url"] == "https://hao.test/api/price"


def test_model_list_response_keeps_all_target_model_objects():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model", dry_run=True))
    spec = SiteSpec(
        id="sudocode",
        adapter="browser",
        model_list_url="https://sudocode.test/pricing",
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

    with pytest.raises(AIDryRun) as raised:
        extractor.extract(spec, "", [response])

    evidence = json.loads(
        raised.value.preview["ai_request"]["request_body"]["messages"][1]["content"].split("网页证据：\n", 1)[1]
    )["network_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["target_model"] == "unresolved"
    assert '"input":5' in evidence[0]["quote"]
    assert '"input":10' in evidence[0]["quote"]


def test_page_text_stops_at_next_display_model_heading():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model", dry_run=True))
    spec = SiteSpec(id="hao", adapter="browser", model_list_url="https://hao.test/zh/models", models=(ModelTarget("gpt-5.6-sol"),))
    page_text = (
        "openai/gpt-5.6-sol 上下文 372K 输入 Token $0.75/M 输出 Token $4.5/M "
        "OpenAI: GPT-5.6 Terra 1.5折 OpenAI: GPT-5.6 Terra"
    )

    with pytest.raises(AIDryRun) as raised:
        extractor.extract(spec, page_text, [])

    request_content = raised.value.preview["ai_request"]["request_body"]["messages"][1]["content"]
    filtered = " ".join(item["quote"] for item in json.loads(request_content.split("网页证据：\n", 1)[1])["page_evidence"])
    assert "openai/gpt-5.6-sol" in filtered
    assert "输入 Token $0.75/M" in filtered
    assert "输出 Token $4.5/M" in filtered
    assert "OpenAI: GPT-5.6 Terra" not in filtered


def test_page_evidence_preserves_raw_quote_without_inventing_price_mapping():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model", dry_run=True))
    spec = SiteSpec(id="hao", adapter="browser", model_list_url="https://hao.test/zh/models", models=(ModelTarget("gpt-5.6-sol"),))

    with pytest.raises(AIDryRun) as raised:
        extractor.extract(
            spec,
            "",
            [],
            page_sources=[{
                "source": "model_list",
                "url": "https://hao.test/zh/models",
                "text": "openai/gpt-5.6-sol 定价 HaoAI(0.15x) 官方 输入 Token $0.75/M $5/M 输出 Token $4.5/M $30/M 缓存读取 $0.075/M $0.5/M 缓存创建 $0.938/M $6.25/M",
            }],
        )

    request_content = raised.value.preview["ai_request"]["request_body"]["messages"][1]["content"]
    evidence = json.loads(request_content.split("网页证据：\n", 1)[1])["page_evidence"][0]
    assert evidence == {
        "quote": "openai/gpt-5.6-sol 定价 HaoAI(0.15x) 官方 输入 Token $0.75/M $5/M 输出 Token $4.5/M $30/M 缓存读取 $0.075/M $0.5/M 缓存创建 $0.938/M $6.25/M",
        "source": "model_list",
        "target_model": "gpt-5.6-sol",
        "url": "https://hao.test/zh/models",
    }


def test_ai_dry_run_uses_model_list_only():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model", dry_run=True))
    spec = SiteSpec(
        id="hao",
        adapter="browser",
        model_list_url="https://hao.test/zh/models",
        models=(ModelTarget("gpt-5.6-sol"),),
    )
    page_sources = [
        {"source": "model_list", "url": "https://hao.test/zh/models", "text": "openai/gpt-5.6-sol 输入 Token $1/M 输出 Token $2/M anthropic/claude-4 $3/M"},
    ]
    responses = [
        {"source": "model_list", "url": "https://hao.test/api/models", "status": 200, "resource_type": "fetch", "payload": {"data": [{"id": "openai/gpt-5.6-sol", "input": 1, "output": 2}, {"id": "anthropic/claude-4", "input": 3, "output": 4}]}},
    ]

    with pytest.raises(AIDryRun) as raised:
        extractor.extract(spec, "", responses, page_sources=page_sources)

    preview = raised.value.preview
    summary = preview["evidence_summary"]
    assert summary["verification_mode"] == "model_list_only"
    assert summary["verification_sources"] == ["model_list"]
    assert summary["page_evidence_count"] == 1
    assert summary["network_evidence_count"] == 1
    assert "filtered_page_text" not in json.dumps(preview, ensure_ascii=False)
    request_content = preview["ai_request"]["request_body"]["messages"][1]["content"]
    request_evidence = json.loads(request_content.split("网页证据：\n", 1)[1])
    assert {item["source"] for item in request_evidence["page_evidence"]} == {"model_list"}
    assert len(request_evidence["network_evidence"]) == 1
    assert request_evidence["network_evidence"][0]["source"] == "model_list"
    model_list_responses = [item for item in request_evidence["network_evidence"] if item["source"] == "model_list"]
    assert '"anthropic/claude-4"' in model_list_responses[0]["quote"]


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
        SiteSpec(id="hao", adapter="browser", model_list_url="https://hao.test/zh/models"),
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
        SiteSpec(id="demo", adapter="browser", model_list_url="https://demo.test/pricing"),
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
        SiteSpec(id="demo", adapter="browser", model_list_url="https://demo.test/pricing"),
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
        SiteSpec(id="demo", adapter="browser", model_list_url="https://demo.test/pricing"),
        result,
        "demo-model",
        "hash",
        set(),
        {},
        ["demo-model"],
    )

    assert records[0].price_status == "rule_only"
    assert records[0].metadata["pricing_kind"] == "tiered_expr"


def test_ai_dry_run_does_not_require_api_key_or_call_http():
    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model", dry_run=True))
    client = httpx.Client(transport=httpx.MockTransport(lambda _: (_ for _ in ()).throw(AssertionError("dry-run 不应发 HTTP"))))

    with pytest.raises(AIDryRun):
        extractor.extract(
            SiteSpec(id="demo", adapter="browser", model_list_url="https://demo.test/pricing", models=(ModelTarget("demo-model"),)),
            "demo-model Input $1/M Output $2/M",
            [{"url": "https://demo.test/api/price", "status": 200, "resource_type": "fetch", "text": "demo-model Input $1/M Output $2/M"}],
            client=client,
        )


def test_ai_extractor_rejects_unseen_api_evidence_url():
    result = {
        "models": [{"model": "demo-model", "input_price": 1, "output_price": 2, "unit": "USD/1M tokens", "status": "confirmed", "network_evidence": [{"url": "https://invented.test/price", "quote": "1 / 2"}], "page_evidence": ["demo-model 1 2"], "confidence": 1}],
        "cross_validation": {"status": "matched", "conflicts": []}
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    extractor = AIPriceExtractor(AIConfig(base_url="https://ai.test/v1", model="test-model", api_key="secret"))
    import os
    os.environ["PRICE_MONITOR_AI_API_KEY"] = "secret"
    try:
        records = extractor.extract(SiteSpec(id="demo", adapter="browser", model_list_url="https://demo.test/pricing", models=(ModelTarget("demo-model"),)), "demo-model 1 2", [{"url": "https://demo.test/api/price", "status": 200, "content_type": "application/json", "payload": {"model_name": "demo-model"}}], client=httpx.Client(transport=httpx.MockTransport(handler)))
    finally:
        os.environ.pop("PRICE_MONITOR_AI_API_KEY", None)
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
        SiteSpec(id="demo", adapter="browser", model_list_url="https://demo.test/pricing"),
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
