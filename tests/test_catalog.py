"""官方价目录同步与折扣计算（llm_price_monitor/catalog/）的单元测试。"""
import json

import httpx
import pytest

from llm_price_monitor.catalog import modelsdev
from llm_price_monitor.catalog.discount import build_discount, sanity_violation
from llm_price_monitor.catalog.modelsdev import fetch_catalog
from llm_price_monitor.catalog.intros import attach_ai_intros
from llm_price_monitor.catalog.translate import (
    attach_zh_descriptions,
    description_fingerprint,
    description_fingerprint_text,
)
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


def test_build_discount_carries_region():
    entry, _ = build_discount(_row(), OFFICIAL_MODELS, 6.74)
    assert entry.as_dict()["region"] is None  # 手工目录条目没写 region
    official = {**OFFICIAL_MODELS, "glm5.3flash": {
        "found": True, "currency": "USD", "region": "cn",
        "list": {"input": 0.119, "output": 0.417},
        "source_url": "https://docs.bigmodel.cn/cn/guide/start/pricing.md",
    }}
    entry, _ = build_discount(_row(model="glm-5.3-flash", input_price=0.035294, output_price=0.123529), official, 6.74)
    assert entry.as_dict()["region"] == "cn"


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


def test_sanity_violation_flags_only_absurd_ratios():
    # 正常折扣（约 0.03 折）与在售站实测上限附近（约 2.8 倍）都放行
    assert sanity_violation(_row(), OFFICIAL_MODELS, 6.74) is None
    assert sanity_violation(_row(input_price=94.0, output_price=564.0), OFFICIAL_MODELS, 6.74) is None
    # 百万级错价（hao 站 OCR 提取事故同款）拦下
    reason = sanity_violation(_row(input_price=2_000_000, output_price=6_000_000), OFFICIAL_MODELS, 6.74)
    assert reason and "输入" in reason and "可信区间" in reason
    # 高到刚出上界（约 20.2 倍）拦下，报哪个方向
    reason = sanity_violation(_row(input_price=680.0, output_price=680.0), OFFICIAL_MODELS, 6.74)
    assert reason and "输入" in reason
    # 目录没收录该模型：没有判据，不拦
    assert sanity_violation(_row(model="unknown-model"), OFFICIAL_MODELS, 6.74) is None


# ---------- modelsdev.fetch_catalog ----------

def _provider(pid: str, name: str, doc: str, models: dict) -> dict:
    return {"id": pid, "name": name, "doc": doc, "models": models}


def _snapshot() -> dict:
    return {
        "openai": _provider("openai", "OpenAI", "https://platform.openai.com/docs/models", {
            "gpt-5.6-sol": {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol", "description": "top-tier model",
                            "release_date": "2026-08-01",
                            "cost": {"input": 5.0, "output": 30.0, "cache_read": 0.5, "cache_write": 6.0,
                                     "input_audio": 6.0,
                                     "tiers": [{"input": 10.0, "output": 60.0, "cache_read": 1.0,
                                                "tier": {"type": "context", "size": 200000}}]}},
            "gpt-5.5": {"id": "gpt-5.5", "name": "GPT-5.5", "release_date": "2026-01-01",
                        "cost": {"input": 2.5, "output": 10.0}},  # 无缓存价：应为 null
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
        "volcengine": _provider("volcengine", "Volcengine Ark", "https://www.volcengine.com/docs/82379", {
            "doubao-pro": {"id": "doubao-pro", "name": "Doubao Pro", "release_date": "2026-03-01",
                           "cost": {"input": 0.6, "output": 3.0}},
        }),  # 国内平台渠道（无 -cn 后缀）：全量目录 region 应标 cn 且厂商置顶
    }


@pytest.fixture
def catalog_fetch(monkeypatch):
    monkeypatch.setattr(modelsdev.fx, "get_usd_cny_rate", lambda client, fallback=None: (6.74, "test"))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=_snapshot()))
    return lambda **kwargs: fetch_catalog(transport=transport, **kwargs)


def test_fetch_catalog_maps_whitelist_and_skips_priceless(catalog_fetch):
    doc = catalog_fetch()
    models = doc["models"]
    # 白名单外（openrouter）不出现；无定价模型不出现；国内渠道（moonshotai-cn）不出现
    assert set(models) == {"gpt5.6sol", "gpt5.5", "kimik2.7"}
    entry = models["gpt5.6sol"]
    assert entry["found"] is True
    assert entry["vendor"] == "OpenAI"
    assert entry["currency"] == "USD"
    assert entry["list"] == {"input": 5.0, "output": 30.0}
    assert entry["list_cny"] == {"input": 33.7, "output": 202.2}
    # 缓存价：原币与折算两份，供花费计算器自动带出
    assert entry["cache"] == {"read": 0.5, "write": 6.0}
    assert entry["cache_cny"] == {"read": 3.37, "write": 40.44}
    # 长上下文分档（tier 条件原样保留）与音频价：同样原币/折算两份
    assert entry["list_tiers"] == [{"input": 10.0, "output": 60.0, "cache_read": 1.0,
                                    "tier": {"type": "context", "size": 200000}}]
    assert entry["list_tiers_cny"] == [{"input": 67.4, "output": 404.4, "cache_read": 6.74,
                                        "tier": {"type": "context", "size": 200000}}]
    assert entry["list_audio"] == {"input": 6.0, "output": None}
    assert entry["list_audio_cny"] == {"input": 40.44, "output": None}
    assert entry["description"] == "top-tier model"
    assert entry["source_url"] == "https://platform.openai.com/docs/models"
    # meta：来源与汇率
    assert doc["source"] == "models.dev"
    assert doc["source_url"] == "https://models.dev"
    assert doc["usd_cny_rate"] == 6.74 and doc["rate_source"] == "test"


def test_fetch_catalog_missing_cache_cost_falls_back_to_null(catalog_fetch):
    """上游没有 cache_read/cache_write 时（占三到四成模型）应为 null，而不是 0 或 KeyError。"""
    doc = catalog_fetch()
    entry = doc["models"]["gpt5.5"]
    assert entry["cache"] == {"read": None, "write": None}
    assert entry["cache_cny"] == {"read": None, "write": None}
    # 上游没带分档/音频时同口径为 null
    assert entry["list_tiers"] is None and entry["list_tiers_cny"] is None
    assert entry["list_audio"] is None and entry["list_audio_cny"] is None


def test_fetch_catalog_orders_vendors_and_newest_models_first(catalog_fetch):
    doc = catalog_fetch()
    # 厂商权威序（openai → moonshot），厂商内按发布时间倒序（gpt-5.6-sol 新于 gpt-5.5）；
    # moonshotai-cn 的 kimi-k2.5 不再进目录（models.dev 国内价不取，基准由定价源产出）
    keys = [entry["model"] for entry in doc["models"].values()]
    assert keys == ["gpt-5.6-sol", "gpt-5.5", "kimi-k2.7"]


def test_fetch_catalog_excludes_domestic_channels(catalog_fetch):
    """models.dev 的国内渠道条目不进官方目录：国内基准只来自厂商定价源。

    同厂商国际站条目直接作为基准（配源合并后才退居 list_global 参考价），
    只有国内渠道才有的模型（kimi-k2.5）在配源前不出现。
    """
    doc = catalog_fetch()
    kimi = doc["models"]["kimik2.7"]
    assert kimi["region"] == "global"
    assert kimi["list"] == {"input": 1.9, "output": 8.0}
    assert kimi["source_url"] == "https://platform.moonshot.ai/docs/api/chat"
    assert "list_global" not in kimi
    assert "kimik2.5" not in doc["models"]
    # 海外厂商条目标 global
    assert doc["models"]["gpt5.6sol"]["region"] == "global"


def test_fetch_catalog_skips_resale_entries_under_platform_providers(monkeypatch):
    """官方价目录只收各 lab 自研模型：白名单平台渠道下挂着他家模型（转售）不进目录。

    models.dev 的 alibaba 渠道下有 deepseek/kimi 转售条目，此前靠厂商块顺序
    先到先得兜底（moonshotai 在前能赢 kimi），deepseek 没有白名单渠道就会被
    转售条目占位；品牌闸门按模型名归属直接拦下。
    """
    snapshot = {
        "alibaba": _provider("alibaba", "Alibaba Cloud", "https://models.dev", {
            "qwen3-max": {"id": "qwen3-max", "name": "Qwen3 Max", "release_date": "2026-07-01",
                          "cost": {"input": 1.2, "output": 6.0}},
            "deepseek-v4-pro": {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro",
                                "cost": {"input": 0.27, "output": 1.11}},  # 转售条目：闸
            "kimi-k3": {"id": "kimi-k3", "name": "Kimi K3", "cost": {"input": 0.6, "output": 3.0}},  # 转售条目：闸
        }),
        "moonshotai": _provider("moonshotai", "Moonshot AI", "https://platform.moonshot.ai", {
            "kimi-k3": {"id": "kimi-k3", "name": "Kimi K3", "release_date": "2026-08-01",
                        "cost": {"input": 0.6, "output": 3.0}},
        }),
    }
    monkeypatch.setattr(modelsdev.fx, "get_usd_cny_rate", lambda client, fallback=None: (6.74, "test"))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=snapshot))
    doc = fetch_catalog(transport=transport)
    assert set(doc["models"]) == {"qwen3max", "kimik3"}
    assert doc["models"]["kimik3"]["vendor"] == "Moonshot AI"
    assert doc["models"]["qwen3max"]["vendor"] == "Alibaba Cloud"


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
    # models.dev 国内渠道条目（-cn 与国内平台）两份目录都不进：国内价只认厂商定价源
    assert set(full["models"]) == {
        "openai:gpt5.6sol", "openai:gpt5.5", "moonshotai:kimik2.7", "openrouter:gpt5.6sol",
    }
    assert all(item["region"] == "global" for item in full["models"].values())
    # 官方条目补发布日期，供展示与自动补监控模型使用
    assert official["models"]["gpt5.6sol"]["release_date"] == "2026-08-01"
    # 全量目录与官方目录同构（同一套 meta 字段）
    assert full["source"] == "models.dev" and full["usd_cny_rate"] == 6.74
    assert full["rate_source"] == "test" and full["generated_at_iso"] == official["generated_at_iso"]
    # meta 附带厂商清单（含未带价厂商），供厂商定价源覆盖检测
    inventory = {item["id"]: item for item in official["providers"]}
    assert inventory["openai"]["models_total"] == 3 and inventory["openai"]["models_priced"] == 2
    assert inventory["openrouter"]["doc"] == "https://openrouter.ai/docs"
    assert "volcengine" in inventory  # 国内平台渠道仍在厂商清单里，覆盖检测要看得见
    assert full["providers"] == official["providers"]


def test_fetch_catalog_propagates_http_errors(monkeypatch):
    monkeypatch.setattr(modelsdev.fx, "get_usd_cny_rate", lambda client, fallback=None: (6.74, "test"))
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_catalog(transport=transport)


def test_fetch_catalogs_official_excludes_snapshots_and_specials(monkeypatch):
    """官方目录只收通用对话大模型：日期后缀快照变体与特殊领域模型不进，全量渠道目录不受限。"""
    monkeypatch.setattr(modelsdev.fx, "get_usd_cny_rate", lambda client, fallback=None: (6.74, "test"))
    snapshot = {"openai": _provider("openai", "OpenAI", "https://platform.openai.com/docs/models", {
        "gpt-5.6-sol": {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol", "release_date": "2026-08-01",
                        "cost": {"input": 5.0, "output": 30.0}},
        # 日期后缀快照变体：官方基准只认主线名
        "gpt-4o-2024-05-13": {"id": "gpt-4o-2024-05-13", "name": "GPT-4o", "release_date": "2024-05-13",
                              "cost": {"input": 2.5, "output": 10.0}},
        "gpt-4o-1120": {"id": "gpt-4o-1120", "name": "GPT-4o 1120", "cost": {"input": 2.5, "output": 10.0}},
        # 特殊领域：生图与向量（modalities 标注与对话模型无异，靠名字识别）
        "gpt-image-2": {"id": "gpt-image-2", "name": "GPT Image 2",
                        "modalities": {"input": ["text", "image"], "output": ["image"]},
                        "cost": {"input": 3.0, "output": 12.0}},
        "text-embedding-3-small": {"id": "text-embedding-3-small", "name": "Embedding",
                                   "modalities": {"input": ["text"], "output": ["text"]},
                                   "cost": {"input": 0.02, "output": None}},
    })}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=snapshot))
    official, full = modelsdev.fetch_catalogs(transport=transport)
    assert set(official["models"]) == {"gpt5.6sol"}
    # 全量渠道目录原样保留上游条目（仅供展示的比价数据不裁剪）
    assert set(full["models"]) >= {"openai:gpt4o20240513", "openai:gptimage2", "openai:textembedding3small"}


def test_is_general_llm_and_snapshot_variant():
    from llm_price_monitor.catalog.general import is_general_llm, is_snapshot_variant

    # 日期后缀快照变体：全日期 / 8 位全数字 / YYMMDD / MMDD 都算；YYMM 版本号不算
    assert is_snapshot_variant("gpt-4o-2024-05-13")
    assert is_snapshot_variant("claude-sonnet-4-5-20250929")
    assert is_snapshot_variant("doubao-seed-1-6-250615")
    assert is_snapshot_variant("deepseek-v4-pro-0813")
    assert is_snapshot_variant("qwen3.8-max-0902")
    assert not is_snapshot_variant("step-3.5-flash-2603")  # YYMM 版本号，不是日期
    assert not is_snapshot_variant("glm-5.3")
    # 特殊领域：音频/图像输出、纯音频输入、向量、角色扮演、全模态生成系列
    assert not is_general_llm({"model": "m-lyric", "modalities": {"input": ["text"], "output": ["text", "audio"]}})
    assert not is_general_llm({"model": "gpt-image-2", "modalities": {"input": ["text"], "output": ["image"]}})
    assert not is_general_llm({"model": "qwen3-asr-flash", "modalities": {"input": ["audio"], "output": ["text"]}})
    assert not is_general_llm({"model": "text-embedding-3-small"})
    assert not is_general_llm({"model": "doubao-seed-character"})
    assert not is_general_llm({"model": "MiniMax-H3"})
    # 媒体生成（国内定价页不标 modalities，按名字拦）：万相系整族 + 形态标记 + 定向档
    assert not is_general_llm({"model": "wan2.7-t2v"})
    assert not is_general_llm({"model": "wanx2.1-t2i-plus"})
    assert not is_general_llm({"model": "wanx-v1"})
    assert not is_general_llm({"model": "happyhorse-1.1-t2v"})
    assert not is_general_llm({"model": "pixverse/pixverse-v6-r2v"})
    assert not is_general_llm({"model": "vidu/viduq3-pro_text2video"})
    assert not is_general_llm({"model": "wanx2.1-vace-plus"})
    assert not is_general_llm({"model": "tongyi-intent-detect-v3"})
    assert not is_general_llm({"model": "tongyi-xiaomi-analysis-flash"})
    # 通用对话模型（含未标 modalities 的国内条目）照常通过
    assert is_general_llm({"model": "glm-5.3-flash", "modalities": {"input": ["text", "image"], "output": ["text"]}})
    assert is_general_llm({"model": "doubao-seed-2.1-pro"})
    assert is_general_llm({"model": "qwen3.8-omni-flash"})  # 输出纯文本的全模态理解模型
    assert is_general_llm({"model": "kimi-k3"})


def test_sanitize_model_name():
    from llm_price_monitor.catalog.normalize import sanitize_model_name

    # 页面标注尾巴（版本标注/缓存提示）剥掉还原成模型 id
    assert sanitize_model_name("deepseek-v4-pro正式版") == ("deepseek-v4-pro", None)
    assert sanitize_model_name("deepseek-v4-pro预览版") == ("deepseek-v4-pro", None)
    assert sanitize_model_name("kimi-k3上下文缓存享有折扣") == ("kimi-k3", None)
    # 整行页面文案与中文产品位：剥完为空，丢弃
    assert sanitize_model_name("参见模型列表") == ("", "整行页面文案，非模型")
    assert sanitize_model_name("腾讯元器") == ("", "整行页面文案，非模型")
    # 一行并排两个模型共价：拆开必错一边，整行丢弃
    assert sanitize_model_name("MiMo-v2.6-Pro、MiMo-v2.5-Pro")[1] is not None
    # 干净名字原样通过
    assert sanitize_model_name(" qwen3.8-max ") == ("qwen3.8-max", None)


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


# ---------- 测试用 AI 配置 ----------


def _ai_config(**overrides) -> AIConfig:
    values: dict = {"enabled": True, "base_url": "https://ai.test/v1", "models": ("test-ai",), "api_key": "sk-test"}
    values.update(overrides)
    return AIConfig(**values)


# ---------- catalog.translate（简介中译）----------


def _desc_output() -> dict:
    return {
        "models": {
            "m1": {"found": True, "model": "m-1", "vendor": "V", "description": "Fast model for chat."},
            "m2": {"found": True, "model": "m-2", "vendor": "V", "description": "Top reasoning model."},
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
        [{"model": "m1", "zh": "面向对话的快速模型。"}, {"model": "m2", "zh": "最新推理模型。"}],
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
        {"model": "m2", "zh": "最新推理模型。"},
    ]])
    output = _desc_output()
    with httpx.Client(transport=transport) as client:
        assert attach_zh_descriptions(output, None, _ai_config(), client) == 1
    assert "description_zh" not in output["models"]["m1"]
    assert output["models"]["m2"]["description_zh"] == "最新推理模型。"


def test_attach_zh_descriptions_silent_when_unavailable_or_failing():
    output = _desc_output()
    assert attach_zh_descriptions(output, None, _ai_config(enabled=False), None) == 0
    assert "description_zh" not in output["models"]["m1"]
    broken = httpx.MockTransport(lambda request: httpx.Response(500))
    with httpx.Client(transport=broken) as client:
        assert attach_zh_descriptions(output, None, _ai_config(), client) == 0
    assert "description_zh" not in output["models"]["m1"]


# ---------- catalog.intros（AI 生成简介）----------


def _intro_output() -> dict:
    return {
        "models": {
            "m1": {"found": True, "model": "m-1", "vendor": "V"},  # 缺简介，归 AI 生成
            "m2": {"found": True, "model": "m-2", "vendor": "V", "description": "EN desc"},  # 有英文原文，归翻译管
            "m3": {"found": True, "model": "m-3", "vendor": "V", "description_zh": "已有中文"},  # 已有中文，不动
        }
    }


def _intro_recorder(pages: list[list[dict]]):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = pages[calls["n"]] if calls["n"] < len(pages) else []
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"intros": page})}}]})

    return httpx.MockTransport(handler), calls


def test_attach_ai_intros_generates_and_marks():
    transport, calls = _intro_recorder([[{"model": "m1", "zh": "面向对话的轻量模型。"}]])
    output = _intro_output()
    with httpx.Client(transport=transport) as client:
        assert attach_ai_intros(output, None, _ai_config(), client) == 1
    assert calls["n"] == 1
    entry = output["models"]["m1"]
    assert entry["description_zh"] == "面向对话的轻量模型。"
    assert entry["desc_source"] == "ai"
    assert entry["desc_fp"] == description_fingerprint_text("m1")
    # 有英文原文 / 已有中文的条目都不归它管
    assert "description_zh" not in output["models"]["m2"]
    assert output["models"]["m3"]["description_zh"] == "已有中文"


def test_attach_ai_intros_reuses_previous_generation():
    """模型名未变的条目沿用上一轮生成的简介，不再发请求。"""
    output = _intro_output()
    previous = {"models": {
        "m1": {**output["models"]["m1"], "description_zh": "上轮生成",
               "desc_fp": description_fingerprint_text("m1"), "desc_source": "ai"},
    }}
    transport, calls = _intro_recorder([])
    with httpx.Client(transport=transport) as client:
        assert attach_ai_intros(output, previous, _ai_config(), client) == 0
    assert calls["n"] == 0
    assert output["models"]["m1"]["description_zh"] == "上轮生成"
    assert output["models"]["m1"]["desc_source"] == "ai"


def test_attach_ai_intros_silent_when_unavailable_or_failing():
    output = _intro_output()
    assert attach_ai_intros(output, None, _ai_config(enabled=False), None) == 0
    assert "description_zh" not in output["models"]["m1"]
    broken = httpx.MockTransport(lambda request: httpx.Response(500))
    with httpx.Client(transport=broken) as client:
        assert attach_ai_intros(output, None, _ai_config(), client) == 0
    assert "description_zh" not in output["models"]["m1"]


def test_attach_zh_descriptions_replaces_ai_placeholder():
    """models.dev 后来补了英文简介：翻译覆盖 AI 占位简介，AI 标一并清掉。"""
    output = {"models": {"m1": {
        "found": True, "model": "m-1", "vendor": "V", "description": "Fast model for chat.",
        "description_zh": "AI 生成的占位", "desc_fp": description_fingerprint_text("m1"), "desc_source": "ai",
    }}}
    transport, _ = _zh_recorder([[{"model": "m1", "zh": "翻译后的正式简介。"}]])
    with httpx.Client(transport=transport) as client:
        assert attach_zh_descriptions(output, None, _ai_config(), client) == 1
    assert output["models"]["m1"]["description_zh"] == "翻译后的正式简介。"
    assert "desc_source" not in output["models"]["m1"]


def test_attach_zh_descriptions_dedupes_same_description():
    transport, calls = _zh_recorder([
        [{"model": "m1", "zh": "面向对话的快速模型。"}, {"model": "m2", "zh": "最新推理模型。"}],
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
    transport, calls = _zh_recorder([[{"model": "m2", "zh": "最新推理模型。"}]])
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
