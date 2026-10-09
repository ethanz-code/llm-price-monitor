"""官方价目录同步与折扣计算（llm_price_monitor/catalog/）的单元测试。"""
import json

import httpx
import pytest

from llm_price_monitor.catalog import modelsdev
from llm_price_monitor.catalog.classify import BATCH_SIZE, attach_ai_tiers
from llm_price_monitor.catalog.discount import build_discount, compute_discounts, summarize
from llm_price_monitor.catalog.modelsdev import fetch_catalog
from llm_price_monitor.catalog.translate import attach_zh_descriptions, description_fingerprint
from llm_price_monitor.config import AIConfig
from llm_price_monitor.report import attach_catalog_discounts

# 官方价只保留列表价口径，折扣基准即列表价
OFFICIAL_MODELS = {
    "gpt5.6sol": {
        "found": True,
        "model": "gpt-5.6-sol",
        "vendor": "OpenAI",
        "currency": "USD",
        "list": {"input": 5.0, "output": 30.0},
        "source_url": "https://openai.com/index/gpt-5-6",
    }
}


def _row(**overrides):
    row = {
        "site_id": "demo",
        "model": "gpt-5.6-sol",
        "group": "default",
        "input_price": 1.0,
        "output_price": 6.0,
        "unit": "CNY/1M tokens",
        "tiers": [],
    }
    row.update(overrides)
    return row


def test_build_discount_converts_to_cny_and_computes_ratio():
    entry, reason = build_discount(_row(), OFFICIAL_MODELS, 6.74)
    assert reason is None
    data = entry.as_dict()
    assert data["official_input_cny"] == pytest.approx(33.7)
    assert data["input"] == pytest.approx(round(1.0 / (5.0 * 6.74), 2))


def test_build_discount_native_cny_official():
    official = {"deepseekv4": {
        "found": True, "currency": "CNY",
        "list": {"input": 2.0, "output": 8.0},
        "source_url": "https://api-docs.deepseek.com",
    }}
    entry, reason = build_discount(_row(model="deepseek-v4"), official, 6.74)
    assert reason is None
    data = entry.as_dict()
    assert data["official_input_cny"] == 2.0
    assert data["input"] == 0.5


def test_build_discount_uses_first_tier_when_top_level_missing():
    row = _row(input_price=None, output_price=None, tiers=[
        {"name": "standard", "input_price": 4.36, "output_price": 26.13, "unit": "CNY/1M tokens"},
    ])
    entry, reason = build_discount(row, OFFICIAL_MODELS, 6.74)
    assert reason is None
    assert entry.as_dict()["input"] == pytest.approx(round(4.36 / (5.0 * 6.74), 2))


def test_build_discount_usd_site_normalizes_via_rate():
    row = _row(input_price=0.6, output_price=3.0, unit="USD/1M tokens")
    entry, _ = build_discount(row, OFFICIAL_MODELS, 6.74)
    # 折扣率是同币种比值，汇率在分子分母同时出现应互相抵消
    assert entry.as_dict()["input"] == pytest.approx(round(0.6 / 5.0, 2))


def test_build_discount_skips_rows_without_official_or_price():
    entry, reason = build_discount(_row(model="unknown-model"), OFFICIAL_MODELS, 6.74)
    assert entry is None and "厂商价" in reason
    entry, reason = build_discount(_row(input_price=None, output_price=None), OFFICIAL_MODELS, 6.74)
    assert entry is None and "站点未拿到可用价格" in reason


def test_compute_discounts_and_summarize():
    rows = [_row(), _row(site_id="demo2")]
    discounts, skipped = compute_discounts(rows, OFFICIAL_MODELS, 6.74)
    assert len(discounts) == 2 and skipped == []
    summary = summarize(discounts)
    assert summary["gpt5.6sol"]["model"] == "gpt-5.6-sol"
    assert summary["gpt5.6sol"]["sites_compared"] == 2
    assert summary["gpt5.6sol"]["input_discount"]["min"] == summary["gpt5.6sol"]["input_discount"]["max"]


# ---------- modelsdev.fetch_catalog ----------

def _provider(pid: str, name: str, doc: str, models: dict) -> dict:
    return {"id": pid, "name": name, "doc": doc, "models": models}


def _snapshot() -> dict:
    return {
        "openai": _provider("openai", "OpenAI", "https://platform.openai.com/docs/models", {
            "gpt-5.6-sol": {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol", "description": "flagship model",
                            "release_date": "2026-08-01", "cost": {"input": 5.0, "output": 30.0}},
            "gpt-5.5": {"id": "gpt-5.5", "name": "GPT-5.5", "release_date": "2026-01-01",
                        "cost": {"input": 2.5, "output": 10.0}},
            "gpt-5.6-mini": {"id": "gpt-5.6-mini", "name": "GPT-5.6 Mini", "cost": {}},  # 无定价，跳过
        }),
        "moonshotai": _provider("moonshotai", "Moonshot AI", "https://platform.moonshot.ai/docs/api/chat", {
            "kimi-k2.7": {"id": "kimi-k2.7", "name": "Kimi K2.7", "release_date": "2026-06-01",
                          "cost": {"input": 1.9, "output": 8.0}},
        }),
        "moonshotai-cn": _provider("moonshotai-cn", "Moonshot AI (China)", "https://platform.moonshot.cn/docs/api/chat", {
            "kimi-k2.7": {"id": "kimi-k2.7", "name": "Kimi K2.7", "release_date": "2026-06-01",
                          "cost": {"input": 1.2, "output": 6.0}},  # 同模型：主条目应胜出
            "kimi-k2.5": {"id": "kimi-k2.5", "name": "Kimi K2.5", "release_date": "2026-05-01",
                          "cost": {"input": 0.6, "output": 3.0}},  # 主条目没有，-cn 补缺
        }),
        "openrouter": _provider("openrouter", "OpenRouter", "https://openrouter.ai/docs", {
            "gpt-5.6-sol": {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol", "cost": {"input": 9.9, "output": 9.9}},
        }),  # 白名单外的转售平台，整体忽略
    }


@pytest.fixture
def catalog_fetch(monkeypatch):
    monkeypatch.setattr(modelsdev.fx, "get_usd_cny_rate", lambda client, fallback=None: (6.74, "test"))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=_snapshot()))
    return lambda **kwargs: fetch_catalog(transport=transport, **kwargs)


def test_fetch_catalog_maps_whitelist_and_skips_priceless(catalog_fetch):
    doc = catalog_fetch()
    models = doc["models"]
    # 白名单外（openrouter）不出现；无定价模型不出现
    assert set(models) == {"gpt5.6sol", "gpt5.5", "kimik2.7", "kimik2.5"}
    entry = models["gpt5.6sol"]
    assert entry["found"] is True
    assert entry["vendor"] == "OpenAI"
    assert entry["currency"] == "USD"
    assert entry["list"] == {"input": 5.0, "output": 30.0}
    assert entry["list_cny"] == {"input": 33.7, "output": 202.2}
    assert entry["description"] == "flagship model"
    assert entry["source_url"] == "https://platform.openai.com/docs/models"
    # meta：来源与汇率
    assert doc["source"] == "models.dev"
    assert doc["source_url"] == "https://models.dev"
    assert doc["usd_cny_rate"] == 6.74 and doc["rate_source"] == "test"


def test_fetch_catalog_orders_vendors_and_newest_models_first(catalog_fetch):
    doc = catalog_fetch()
    # 厂商权威序（openai → moonshot），厂商内按发布时间倒序（gpt-5.6-sol 新于 gpt-5.5）
    keys = [entry["model"] for entry in doc["models"].values()]
    assert keys == ["gpt-5.6-sol", "gpt-5.5", "kimi-k2.7", "kimi-k2.5"]


def test_fetch_catalog_main_entry_wins_over_cn(catalog_fetch):
    doc = catalog_fetch()
    # 主条目优先：kimi-k2.7 取国际站价，vendor 不分渠道；kimi-k2.5 由 -cn 补缺
    assert doc["models"]["kimik2.7"]["list"] == {"input": 1.9, "output": 8.0}
    assert doc["models"]["kimik2.7"]["source_url"] == "https://platform.moonshot.ai/docs/api/chat"
    assert doc["models"]["kimik2.7"]["vendor"] == "Moonshot AI"
    assert doc["models"]["kimik2.5"]["list"] == {"input": 0.6, "output": 3.0}
    assert doc["models"]["kimik2.5"]["source_url"] == "https://platform.moonshot.cn/docs/api/chat"


def test_fetch_catalog_rejects_empty_payload(monkeypatch):
    monkeypatch.setattr(modelsdev.fx, "get_usd_cny_rate", lambda client, fallback=None: (6.74, "test"))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=json.dumps({})))
    with pytest.raises(ValueError):
        fetch_catalog(transport=transport)


def test_fetch_catalogs_shared_snapshot_covers_all_providers(monkeypatch):
    """全量目录与官方目录共享同一次快照拉取；白名单外渠道按 provider:model 键进全量表。"""
    monkeypatch.setattr(modelsdev.fx, "get_usd_cny_rate", lambda client, fallback=None: (6.74, "test"))
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_snapshot())

    official, full = modelsdev.fetch_catalogs(transport=httpx.MockTransport(handler))
    assert calls["n"] == 1  # 一次快照，两份目录
    # 官方目录保持纯净键；全量目录收录 openrouter 渠道条目
    assert all(":" not in key for key in official["models"])
    assert set(full["models"]) >= {"openrouter:gpt5.6sol", "openai:gpt5.6sol"}
    entry = full["models"]["openrouter:gpt5.6sol"]
    assert entry["vendor"] == "OpenRouter"
    assert entry["list"] == {"input": 9.9, "output": 9.9}
    assert entry["release_date"] is None  # 该渠道条目无发布日期
    # 官方条目补发布日期，供 AI 档位判定与指纹使用
    assert official["models"]["gpt5.6sol"]["release_date"] == "2026-08-01"
    # 全量目录与官方目录同构（同一套 meta 字段）
    assert full["source"] == "models.dev" and full["usd_cny_rate"] == 6.74
    assert full["rate_source"] == "test" and full["generated_at_iso"] == official["generated_at_iso"]


def test_fetch_catalog_propagates_http_errors(monkeypatch):
    monkeypatch.setattr(modelsdev.fx, "get_usd_cny_rate", lambda client, fallback=None: (6.74, "test"))
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_catalog(transport=transport)


# ---------- report.attach_catalog_discounts ----------


def test_fetch_catalog_tolerates_partial_cost_keys(monkeypatch):
    """上游 cost 只有 output 没有 input 键时不应 KeyError 冲垮整个目录刷新。"""
    monkeypatch.setattr(modelsdev.fx, "get_usd_cny_rate", lambda client, fallback=None: (7.0, "test"))
    snapshot = {"openai": {"doc": "https://platform.openai.com/docs/models", "models": {
        "partial": {"id": "partial", "name": "Partial", "release_date": "2026-01-01", "cost": {"output": 4.0}},
    }}}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=snapshot))
    doc = fetch_catalog(transport=transport)
    assert doc["models"]["partial"]["list"] == {"input": None, "output": 4.0}


def test_attach_catalog_discounts_prefers_snapshot_rate(monkeypatch):
    """collect 附加折扣优先用目录快照汇率，不做实时网络请求。"""
    def _fail(client, fallback=None):
        raise AssertionError("快照汇率存在时不应实时拉取")

    monkeypatch.setattr("llm_price_monitor.report.catalog_fx.get_usd_cny_rate", _fail)
    catalog = {
        "generated_at_iso": "2026-09-05T00:00:00+0800",
        "usd_cny_rate": 7.0,
        "models": {
            "demomodel": {
                "found": True, "model": "demo-model", "vendor": "Demo", "currency": "USD",
                "list": {"input": 10.0, "output": 50.0},
                "source_url": "https://demo.test/official",
            },
        },
    }
    output = {"records": [{
        "site_id": "demo", "model": "demo-model", "input_price": 5.0, "output_price": 25.0,
        "unit": "USD/1M tokens", "tiers": [],
    }]}
    result = attach_catalog_discounts(output, catalog)
    assert result["catalog"]["enabled"] is True
    assert result["catalog"]["usd_cny_rate"] == 7.0
    assert result["catalog"]["rate_source"] == "厂商价快照"
    # USD 口径下汇率在分子分母同时出现，折扣率与汇率无关
    assert result["records"][0]["discount"]["input"] == 0.5


def test_attach_catalog_discounts_falls_back_to_live_rate(monkeypatch):
    """目录没有快照汇率（旧数据）时回退实时拉取。"""
    monkeypatch.setattr(
        "llm_price_monitor.report.catalog_fx.get_usd_cny_rate",
        lambda client, fallback=None: (6.5, "test-live"),
    )
    catalog = {
        "models": {"demomodel": {"found": True, "model": "demo-model", "currency": "USD",
                                  "list": {"input": 10.0, "output": 50.0}}},
    }
    output = {"records": [{
        "site_id": "demo", "model": "demo-model", "input_price": 5.0, "output_price": 25.0,
        "unit": "USD/1M tokens", "tiers": [],
    }]}
    result = attach_catalog_discounts(output, catalog)
    assert result["catalog"]["enabled"] is True
    assert result["catalog"]["rate_source"] == "test-live"
    assert result["records"][0]["discount"]["input"] == 0.5


# ---------- catalog.classify（AI 档位判定）----------


def _ai_config(**overrides) -> AIConfig:
    values: dict = {"enabled": True, "base_url": "https://ai.test/v1", "models": ("test-ai",), "api_key": "sk-test"}
    values.update(overrides)
    return AIConfig(**values)


def _tier_output() -> dict:
    return {
        "models": {
            "gpt6": {
                "found": True, "model": "gpt-6", "name": "GPT-6", "vendor": "OpenAI",
                "description": "most capable model", "list": {"input": 10.0, "output": 40.0},
                "family": "gpt", "modalities": {"output": ["text"]},
            },
            "gpt56mini": {
                "found": True, "model": "gpt-5.6-mini", "name": "GPT-5.6 Mini", "vendor": "OpenAI",
                "description": "small fast model", "list": {"input": 0.25, "output": 2.0},
                "family": "gpt-mini", "modalities": {"output": ["text"]},
            },
        }
    }


def _recorder(verdict_pages: list[list[dict]]):
    """按调用次序回放 AI 响应的 MockTransport，并记录调用次数。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = verdict_pages[calls["n"]] if calls["n"] < len(verdict_pages) else []
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"verdicts": page})}}]})

    return httpx.MockTransport(handler), calls


def test_attach_ai_tiers_classifies_new_models():
    transport, calls = _recorder([
        [{"model": "gpt-6", "tier": "flagship"}, {"model": "gpt-5.6-mini", "tier": "mainstream"}],
    ])
    output = _tier_output()
    with httpx.Client(transport=transport) as client:
        assert attach_ai_tiers(output, None, _ai_config(), client) == 2
    assert calls["n"] == 1  # 同厂商一批
    models = output["models"]
    assert models["gpt6"]["tier"] == "flagship" and models["gpt6"]["tier_fp"]
    assert models["gpt56mini"]["tier"] == "mainstream"


def test_attach_ai_tiers_reuses_fingerprints_without_calls():
    output = _tier_output()
    transport, _ = _recorder([[{"model": "gpt-6", "tier": "flagship"}, {"model": "gpt-5.6-mini", "tier": "mainstream"}]])
    with httpx.Client(transport=transport) as client:
        attach_ai_tiers(output, None, _ai_config(), client)
    previous = {"models": {key: dict(entry) for key, entry in output["models"].items()}}

    # 上一轮结果作为 previous：指纹未变的条目沿用档位，不再发请求
    frozen = _tier_output()
    reuse_transport, calls = _recorder([])  # 只要发生请求就会取到空 verdict 页并留下无 tier，方便断言
    with httpx.Client(transport=reuse_transport) as client:
        assert attach_ai_tiers(frozen, previous, _ai_config(), client) == 0
    assert calls["n"] == 0
    assert frozen["models"]["gpt6"]["tier"] == "flagship"
    assert frozen["models"]["gpt56mini"]["tier"] == "mainstream"


def test_attach_ai_tiers_drops_invalid_verdicts():
    transport, _ = _recorder([[
        {"model": "gpt-6", "tier": "flagship"},
        {"model": "not-in-list", "tier": "flagship"},  # 清单外模型
        {"model": "gpt-5.6-mini", "tier": "ultra"},  # 非法档位
    ]])
    output = _tier_output()
    with httpx.Client(transport=transport) as client:
        assert attach_ai_tiers(output, None, _ai_config(), client) == 1
    assert output["models"]["gpt6"]["tier"] == "flagship"
    assert "tier" not in output["models"]["gpt56mini"]  # 丢弃后下一轮重试


def test_attach_ai_tiers_silent_when_unavailable_or_failing():
    # AI 未配置：不调用、无档位
    untouched = _tier_output()
    assert attach_ai_tiers(untouched, None, _ai_config(enabled=False), None) == 0
    assert "tier" not in untouched["models"]["gpt6"]
    # AI 失败：静默跳过，条目保持无 tier
    failing = _tier_output()
    broken = httpx.MockTransport(lambda request: httpx.Response(500))
    with httpx.Client(transport=broken) as client:
        assert attach_ai_tiers(failing, None, _ai_config(), client) == 0
    assert "tier" not in failing["models"]["gpt6"]


def test_attach_ai_tiers_batches_large_vendors():
    models = {
        f"m{i}": {
            "found": True, "model": f"m-{i}", "name": f"M{i}", "vendor": "V",
            "description": "x", "list": {"input": 1.0, "output": 2.0},
            "family": "f", "modalities": {"output": ["text"]},
        }
        for i in range(BATCH_SIZE + 5)
    }
    verdicts = [{"model": f"m-{i}", "tier": "mainstream"} for i in range(BATCH_SIZE + 5)]
    transport, calls = _recorder([verdicts, verdicts])
    output = {"models": models}
    with httpx.Client(transport=transport) as client:
        assert attach_ai_tiers(output, None, _ai_config(), client) == BATCH_SIZE + 5
    assert calls["n"] == 2  # 超过单批上限分两次请求


def test_attach_ai_tiers_demotes_stale_models_when_newer_generation_exists():
    from datetime import date, timedelta

    old_date = (date.today() - timedelta(days=800)).isoformat()
    recent_date = (date.today() - timedelta(days=30)).isoformat()
    models = {
        "old": {
            "found": True, "model": "m-old", "name": "M Old", "vendor": "V",
            "description": "x", "list": {"input": 1.0, "output": 2.0},
            "family": "f", "release_date": old_date, "modalities": {"output": ["text"]},
        },
        "recent": {
            "found": True, "model": "m-new", "name": "M New", "vendor": "V",
            "description": "x", "list": {"input": 1.0, "output": 2.0},
            "family": "f", "release_date": recent_date, "modalities": {"output": ["text"]},
        },
    }
    transport, _ = _recorder([[{"model": "m-old", "tier": "mainstream"}, {"model": "m-new", "tier": "flagship"}]])
    output = {"models": models}
    with httpx.Client(transport=transport) as client:
        assert attach_ai_tiers(output, None, _ai_config(), client) == 2
    assert output["models"]["old"]["tier"] is None  # 新一代在售，旧代硬降为 null
    assert output["models"]["recent"]["tier"] == "flagship"


def test_attach_ai_tiers_keeps_old_mainstream_without_newer_generation():
    from datetime import date, timedelta

    old_date = (date.today() - timedelta(days=800)).isoformat()
    models = {
        "old": {
            "found": True, "model": "m-old", "name": "M Old", "vendor": "V",
            "description": "x", "list": {"input": 1.0, "output": 2.0},
            "family": "f", "release_date": old_date, "modalities": {"output": ["text"]},
        },
    }
    transport, _ = _recorder([[{"model": "m-old", "tier": "mainstream"}]])
    output = {"models": models}
    with httpx.Client(transport=transport) as client:
        assert attach_ai_tiers(output, None, _ai_config(), client) == 1
    assert output["models"]["old"]["tier"] == "mainstream"  # 厂商无新一代在售，保留 AI 判定


# ---------- catalog.translate（简介中译）----------


def _desc_output() -> dict:
    return {
        "models": {
            "m1": {"found": True, "model": "m-1", "vendor": "V", "description": "Fast model for chat."},
            "m2": {"found": True, "model": "m-2", "vendor": "V", "description": "Flagship reasoning model."},
            "m3": {"found": True, "model": "m-3", "vendor": "V"},  # 无简介，不进待翻清单
        }
    }


def _zh_recorder(pages: list[list[dict]]):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = pages[calls["n"]] if calls["n"] < len(pages) else []
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"translations": page})}}]})

    return httpx.MockTransport(handler), calls


def test_attach_zh_descriptions_translates_and_caches():
    transport, calls = _zh_recorder([
        [{"model": "m1", "zh": "面向对话的快速模型。"}, {"model": "m2", "zh": "旗舰推理模型。"}],
    ])
    output = _desc_output()
    with httpx.Client(transport=transport) as client:
        assert attach_zh_descriptions(output, None, _ai_config(), client) == 2
    assert calls["n"] == 1
    models = output["models"]
    assert models["m1"]["description_zh"] == "面向对话的快速模型。"
    assert models["m1"]["desc_fp"]
    assert "description_zh" not in models["m3"]

    # 简介未变：沿用上一轮译文，不再发请求
    previous = {"models": {key: dict(entry) for key, entry in models.items()}}
    frozen = _desc_output()
    reuse_transport, reuse_calls = _zh_recorder([])
    with httpx.Client(transport=reuse_transport) as client:
        assert attach_zh_descriptions(frozen, previous, _ai_config(), client) == 0
    assert reuse_calls["n"] == 0
    assert frozen["models"]["m1"]["description_zh"] == "面向对话的快速模型。"


def test_attach_zh_descriptions_budget_limits_new_work():
    transport, calls = _zh_recorder([
        [{"model": "m1", "zh": "第一条。"}],
        [{"model": "m2", "zh": "第二条。"}],
    ])
    output = _desc_output()
    with httpx.Client(transport=transport) as client:
        # budget=1：本轮只翻一条，剩余条目留给下一轮
        assert attach_zh_descriptions(output, None, _ai_config(), client, budget=1) == 1
    assert calls["n"] == 1
    assert output["models"]["m1"]["description_zh"] == "第一条。"
    assert "description_zh" not in output["models"]["m2"]


def test_attach_zh_descriptions_skips_bad_translation_and_off_list():
    transport, _ = _zh_recorder([[
        {"model": "m1", "zh": "   "},  # 空译文：丢弃，下一轮重试
        {"model": "nope", "zh": "清单外条目"},
        {"model": "m2", "zh": "旗舰推理模型。"},
    ]])
    output = _desc_output()
    with httpx.Client(transport=transport) as client:
        assert attach_zh_descriptions(output, None, _ai_config(), client) == 1
    assert "description_zh" not in output["models"]["m1"]
    assert output["models"]["m2"]["description_zh"] == "旗舰推理模型。"


def test_attach_zh_descriptions_silent_when_unavailable_or_failing():
    output = _desc_output()
    assert attach_zh_descriptions(output, None, _ai_config(enabled=False), None) == 0
    assert "description_zh" not in output["models"]["m1"]
    broken = httpx.MockTransport(lambda request: httpx.Response(500))
    with httpx.Client(transport=broken) as client:
        assert attach_zh_descriptions(output, None, _ai_config(), client) == 0
    assert "description_zh" not in output["models"]["m1"]


def test_attach_zh_descriptions_dedupes_same_description():
    transport, calls = _zh_recorder([
        [{"model": "m1", "zh": "面向对话的快速模型。"}, {"model": "m2", "zh": "旗舰推理模型。"}],
    ])
    output = _desc_output()
    output["models"]["m4"] = {"found": True, "model": "m-4", "vendor": "W", "description": "Fast model for chat."}
    with httpx.Client(transport=transport) as client:
        # 同简介的 m1/m4 只翻一次，计数按新翻译的指纹算 2
        assert attach_zh_descriptions(output, None, _ai_config(), client) == 2
    assert calls["n"] == 1
    assert output["models"]["m4"]["description_zh"] == "面向对话的快速模型。"
    assert output["models"]["m4"]["desc_fp"] == output["models"]["m1"]["desc_fp"]


def test_attach_zh_descriptions_seed_reuses_other_catalog():
    transport, calls = _zh_recorder([[{"model": "m2", "zh": "旗舰推理模型。"}]])
    output = _desc_output()
    seed = {"x" * 16: "面向对话的快速模型。"}
    seed[description_fingerprint(output["models"]["m1"])] = "面向对话的快速模型。"
    with httpx.Client(transport=transport) as client:
        assert attach_zh_descriptions(output, None, _ai_config(), client, seed=seed) == 1
    assert calls["n"] == 1  # m1 命中 seed 不发请求，只有 m2 需要翻
    assert output["models"]["m1"]["description_zh"] == "面向对话的快速模型。"


def test_attach_zh_descriptions_continues_after_batch_failure():
    output = _desc_output()
    for i in range(2, 62):  # 60 条待翻，两批
        output["models"][f"x{i}"] = {"found": True, "model": f"x-{i}", "vendor": "V", "description": f"Model {i}."}
    # 第一批失败，第二批成功：其余批次不受影响
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"translations": [{"model": "x31", "zh": "第三十一个。"}]})}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        # 第二批翻出 x31；第三批虽成功但响应里没有它的条目，不计新翻译
        assert attach_zh_descriptions(output, None, _ai_config(), client) == 1
    assert calls["n"] == 3
    assert output["models"]["x31"]["description_zh"] == "第三十一个。"
