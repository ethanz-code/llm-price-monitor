"""厂商定价源：覆盖检测、目录合并与抓取链路的单元测试。"""
from pathlib import Path

import httpx
import pytest

from llm_price_monitor.catalog import vendor_sources as vs
from llm_price_monitor.catalog.translate import description_fingerprint_text
from llm_price_monitor.catalog.vendor_sources import (
    detect_vendor_coverage,
    load_sources,
    merge_sources_into_catalog,
    merge_sources_into_channel_catalog,
    refresh_and_merge,
    refresh_source,
    upsert_source,
    validate_region,
    validate_source,
)
from llm_price_monitor.store import Store


def _provider(pid: str, name: str = "X", doc: str = "https://x.dev", total: int = 3, priced: int = 3) -> dict:
    return {"id": pid, "name": name, "doc": doc, "models_total": total, "models_priced": priced}


def _catalog() -> dict:
    return {
        "usd_cny_rate": 7.0,
        "models": {
            "glm5.3flash": {
                "found": True, "model": "glm-5.3-flash", "name": "GLM-5.3 Flash", "vendor": "Zhipu AI",
                "region": "global", "currency": "USD",
                "list": {"input": 0.075, "output": 0.25},
                "list_cny": {"input": 0.53, "output": 1.75},
                "cache": {"read": None, "write": None},
                "cache_cny": {"read": None, "write": None},
                "source_url": "https://docs.z.ai",
            },
            "qwen3max": {
                "found": True, "model": "qwen3-max", "vendor": "Alibaba Cloud", "region": "cn",
                "currency": "USD", "list": {"input": 1.291, "output": 7.749},
                "list_cny": {"input": 9.04, "output": 54.24}, "source_url": "https://models.dev",
            },
        },
    }


# ---------- 覆盖检测 ----------

def test_detect_verdicts_for_known_brands():
    providers = [
        _provider("zhipuai", name="Zhipu AI", doc="https://docs.z.ai/guides/overview/pricing", total=15, priced=15),
        _provider("alibaba-cn", name="Alibaba (China)", doc="https://alibabacloud.com", total=89, priced=80),
        _provider("alibaba", name="Alibaba", doc="https://alibabacloud.com", total=56, priced=50),
    ]
    records = {record["vendor"]: record for record in detect_vendor_coverage(providers)}
    assert records["Zhipu AI"]["verdict"] == "missing_cn"  # 只有 z.ai 国际站口径
    assert records["Zhipu AI"]["suggestions"] == [
        {"kind": "web", "url": "https://docs.bigmodel.cn/cn/guide/start/pricing.md"}
    ]
    assert records["Alibaba Cloud"]["verdict"] == "has_cn"
    assert records["Alibaba Cloud"]["providers"] == ["alibaba", "alibaba-cn"]
    assert records["Baidu"]["verdict"] == "not_listed"
    # verdict 只描述 models.dev 收录情况，不做优先级排序：任何品牌都需要配国内定价源
    order = [record["vendor"] for record in detect_vendor_coverage(providers)]
    assert order[:3] == ["Zhipu AI", "DeepSeek", "Moonshot AI"]  # 保持品牌表顺序


def test_detect_keyword_fallback_and_cn_suffix_heuristic():
    """models.dev 以后新增的渠道靠名称/文档关键词识别；-cn 后缀视为国内站渠道。"""
    providers = [
        _provider("qianfan", name="Baidu Qianfan", doc="https://cloud.baidu.com/doc/QIANFAN", total=10, priced=8),
        _provider("ernie-cn", name="Some (China)", doc="https://baidu.com/doc", total=2, priced=2),
    ]
    records = {record["vendor"]: record for record in detect_vendor_coverage(providers)}
    assert records["Baidu"]["verdict"] == "has_cn"
    assert set(records["Baidu"]["providers"]) == {"qianfan", "ernie-cn"}


def test_detect_marks_added_source():
    providers = [_provider("zhipuai", name="Zhipu AI", doc="https://docs.z.ai", total=15, priced=15)]
    records = {record["vendor"]: record for record in detect_vendor_coverage(
        providers, {"Zhipu AI": {"url": "https://docs.bigmodel.cn", "enabled": True}})}
    assert records["Zhipu AI"]["source_added"] is True and records["Zhipu AI"]["source_enabled"] is True


def test_detect_suggestions_carry_kind(monkeypatch):
    """品牌表同时配置网页页与 JSON 接口时，推荐项按采集类型并列透出。"""
    brand = vs.DomesticBrand(
        vendor="Test", aliases=(), cn_provider_ids=(), keywords=("test",),
        suggested_url="https://x.cn/pricing", suggested_json_url="https://x.cn/pricing.json",
    )
    monkeypatch.setattr(vs, "DOMESTIC_BRANDS", (brand,))
    records = detect_vendor_coverage([_provider("testx", name="TestX", doc="https://x.cn")])
    assert records[0]["suggestions"] == [
        {"kind": "web", "url": "https://x.cn/pricing"},
        {"kind": "json", "url": "https://x.cn/pricing.json"},
    ]


# ---------- 合并 ----------

def test_merge_cny_page_overrides_global_baseline():
    catalog = _catalog()
    sources = {"Zhipu AI": {
        "url": "https://docs.bigmodel.cn/cn/guide/start/pricing.md", "enabled": True,
        "models": [{"model": "GLM-5.3-Flash", "input_price": 0.8, "output_price": 2.8,
                    "cache_read_price": 0.23, "currency": "CNY", "unit": "CNY/1M tokens"}],
    }}
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    assert summary["matched"] == 1 and summary["added"] == 0 and summary["skipped"] == []
    entry = merged["models"]["glm5.3flash"]
    assert entry["region"] == "cn"
    # 目录统一 USD 口径：list 按快照汇率折算，list_cny 是页面标价的精确人民币
    assert entry["list"] == {"input": round(0.8 / 7.0, 6), "output": round(2.8 / 7.0, 6)}
    assert entry["list_cny"] == {"input": 0.8, "output": 2.8}
    assert entry["cache"]["read"] == round(0.23 / 7.0, 6) and entry["cache_cny"]["read"] == 0.23
    # 原国际基准退居参考价
    assert entry["list_global"] == {"input": 0.075, "output": 0.25}
    assert entry["list_global_cny"] == {"input": 0.53, "output": 1.75}
    assert entry["source_url"].startswith("https://docs.bigmodel.cn")


def test_merge_adds_new_entry_with_source_vendor():
    catalog = _catalog()
    sources = {"Zhipu AI": {"url": "https://docs.bigmodel.cn/cn/guide/start/pricing.md", "enabled": True, "models": [
        {"model": "GLM-5.3-FlashX", "input_price": 2.0, "output_price": 7.0, "cache_read_price": None,
         "currency": "CNY", "price_status": "candidate"},
    ]}}
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    assert summary["added"] == 1 and summary["matched"] == 0
    entry = merged["models"]["glm5.3flashx"]
    assert entry["vendor"] == "Zhipu AI" and entry["region"] == "cn"
    assert entry["list_cny"] == {"input": 2.0, "output": 7.0}
    assert entry["cache"] == {"read": None, "write": None}
    assert entry["price_status"] == "candidate"  # AI 兜底来源标记 candidate，透传给前端


def test_merge_skips_column_misaligned_prices():
    """价目设定自校验：输入/输出价低于缓存读价视为列错位，整条跳过不入目录。"""
    catalog = _catalog()
    sources = {"Zhipu AI": {
        "url": "https://docs.bigmodel.cn/cn/guide/start/pricing.md", "enabled": True, "models": [
            # 事故形态：定价页"缓存读/输入/输出"三列被错位，缓存读价填进了输入价
            {"model": "GLM-5.3-Flash", "input_price": 0.025, "output_price": 3.0,
             "cache_read_price": 6.0, "currency": "CNY"},
            {"model": "GLM-5.2", "input_price": 3.0, "output_price": 0.025,
             "cache_read_price": 0.25, "currency": "CNY"},
            {"model": "GLM-5.3-Air", "input_price": 3.0, "output_price": 6.0,
             "cache_read_price": 0.025, "currency": "CNY"},
            # 没有缓存读价就没有判据，照常收录
            {"model": "GLM-5.3-Lite", "input_price": 0.5, "output_price": 2.0,
             "cache_read_price": None, "currency": "CNY"},
        ],
    }}
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    assert summary["skipped"] == [
        "Zhipu AI/GLM-5.3-Flash（输入价低于缓存读价，判定为价目列错位）",
        "Zhipu AI/GLM-5.2（输出价低于缓存读价，判定为价目列错位）",
    ]
    assert merged["models"]["glm5.3flash"]["list"] == {"input": 0.075, "output": 0.25}  # 坏价不覆盖已有条目
    assert merged["models"]["glm5.3air"]["list_cny"] == {"input": 3.0, "output": 6.0}
    assert merged["models"]["glm5.3lite"]["list_cny"] == {"input": 0.5, "output": 2.0}


def test_merge_overrides_update_vendor_attribution():
    """命中已有条目时 vendor 同步为当前源，价格、来源与厂商归属保持同渠道。"""
    catalog = _catalog()
    sources = {"Alibaba Cloud": {
        "url": "https://help.aliyun.com/zh/model-studio/model-pricing", "enabled": True,
        "models": [{"model": "Qwen3-Max", "input_price": 2.4, "output_price": 9.6,
                    "cache_read_price": None, "currency": "CNY"}],
    }}
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    assert summary["matched"] == 1
    entry = merged["models"]["qwen3max"]
    assert entry["vendor"] == "Alibaba Cloud"
    assert entry["region"] == "cn"
    assert entry["source_url"].startswith("https://help.aliyun.com")
    assert entry["list_cny"] == {"input": 2.4, "output": 9.6}


def test_merge_skips_hosted_resale_models():
    """官方自研闸门：国内源页面上归属他家的托管/转售模型不进官方目录，自家模型照常入库。"""
    catalog = _catalog()
    sources = {"Baidu": {"url": "https://cloud.baidu.com/doc/qianfan/s/wmh4sv6ya", "enabled": True, "models": [
        {"model": "DeepSeek-V4-Pro", "input_price": 1.79, "output_price": 3.57,
         "cache_read_price": None, "currency": "CNY"},
        {"model": "GLM-5.2", "input_price": 1.0, "output_price": 3.0,
         "cache_read_price": None, "currency": "CNY"},
        {"model": "ERNIE-5.1", "input_price": 2.0, "output_price": 8.0,
         "cache_read_price": None, "currency": "CNY"},
    ]}}
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    assert summary["added"] == 1 and summary["matched"] == 0
    assert "deepseekv4pro" not in merged["models"]
    assert "glm5.2" not in merged["models"]
    assert merged["models"]["ernie5.1"]["vendor"] == "Baidu" and merged["models"]["ernie5.1"]["region"] == "cn"
    hosted = [reason for reason in summary["skipped"] if "托管/转售" in reason]
    assert len(hosted) == 2 and any("DeepSeek-V4-Pro" in reason for reason in hosted)


def test_merge_official_source_claims_own_model_over_hosted_channel():
    """官方 lab 源对自家模型有归属权：托管渠道源（字母序在前）不再能抢注模型键。"""
    catalog = _catalog()
    sources = {
        "Baidu": {"url": "https://cloud.baidu.com/doc/qianfan/s/wmh4sv6ya", "enabled": True, "models": [
            {"model": "DeepSeek-V4-Pro", "input_price": 1.79, "output_price": 3.57,
             "cache_read_price": None, "currency": "CNY"},
        ]},
        "DeepSeek": {"url": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing", "enabled": True, "models": [
            {"model": "deepseek-v4-pro", "input_price": 9.0, "output_price": 27.0,
             "cache_read_price": 0.3, "currency": "CNY"},
        ]},
    }
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    assert summary["added"] == 1 and summary["matched"] == 0
    entry = merged["models"]["deepseekv4pro"]
    assert entry["vendor"] == "DeepSeek"
    assert entry["list_cny"] == {"input": 9.0, "output": 27.0}
    assert entry["source_url"].startswith("https://api-docs.deepseek.com")


def test_merge_channel_catalog_keeps_each_vendor_entry():
    """国内源按 厂商:模型 一家一条进全量渠道目录；海外源不进（models.dev 已覆盖）。"""
    full = {"models": {}}
    sources = {
        "DeepSeek": {"url": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing", "enabled": True, "region": "cn",
                     "models": [{"model": "deepseek-v4-pro", "input_price": 9.0, "output_price": 27.0,
                                 "cache_read_price": 0.3, "currency": "CNY", "price_status": "candidate"}]},
        "Alibaba Cloud": {"url": "https://help.aliyun.com/zh/model-studio/model-pricing", "enabled": True, "region": "cn",
                          "models": [{"model": "deepseek-v4-pro", "input_price": 12.0, "output_price": 24.0,
                                      "cache_read_price": None, "currency": "CNY"}]},
        "OpenAI": {"url": "https://platform.openai.com/pricing", "enabled": True, "region": "global",
                   "models": [{"model": "gpt-5", "input_price": 1.25, "output_price": 10.0, "currency": "USD"}]},
        "Baichuan": {"url": "https://platform.baichuan-ai.com/prices", "enabled": False, "region": "cn",
                     "models": [{"model": "baichuan-m3", "input_price": 1.0, "output_price": 1.0, "currency": "CNY"}]},
    }
    merged, summary = merge_sources_into_channel_catalog(full, sources, 7.0)
    assert summary == {"added": 2, "replaced": 0, "skipped": []}
    assert merged["models"]["deepseek:deepseekv4pro"]["list_cny"] == {"input": 9.0, "output": 27.0}
    assert merged["models"]["deepseek:deepseekv4pro"]["cache_cny"]["read"] == 0.3
    assert merged["models"]["deepseek:deepseekv4pro"]["price_status"] == "candidate"
    assert merged["models"]["alibabacloud:deepseekv4pro"]["list_cny"] == {"input": 12.0, "output": 24.0}
    assert merged["models"]["deepseek:deepseekv4pro"]["vendor"] == "DeepSeek"
    assert not any(k.startswith("openai:") or k.startswith("baichuan:") for k in merged["models"])


def test_merge_channel_catalog_replaces_existing_key():
    """同名键已存在（models.dev 快照遗留）时以官方定价页抓取结果覆盖。"""
    full = {"models": {"deepseek:deepseekv4pro": {"found": True, "model": "deepseek-v4-pro", "vendor": "DeepSeek",
                                                  "list_cny": {"input": 4.5, "output": 13.5}}}}
    sources = {"DeepSeek": {"url": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing", "enabled": True, "region": "cn",
                            "models": [{"model": "deepseek-v4-pro", "input_price": 9.0, "output_price": 27.0,
                                        "currency": "CNY"}]}}
    merged, summary = merge_sources_into_channel_catalog(full, sources, 7.0)
    assert summary == {"added": 0, "replaced": 1, "skipped": []}
    assert merged["models"]["deepseek:deepseekv4pro"]["list_cny"] == {"input": 9.0, "output": 27.0}


def test_merge_channel_catalog_skips_misaligned_prices():
    """渠道目录同样拦列错位价目：整条不进全量目录，跳过原因随摘要透出。"""
    full = {"models": {}}
    sources = {"Zhipu AI": {
        "url": "https://docs.bigmodel.cn/cn/guide/start/pricing.md", "enabled": True, "region": "cn",
        "models": [{"model": "glm-5.3-flash", "input_price": 0.025, "output_price": 3.0,
                    "cache_read_price": 6.0, "currency": "CNY"}],
    }}
    merged, summary = merge_sources_into_channel_catalog(full, sources, 7.0)
    assert summary == {"added": 0, "replaced": 0,
                       "skipped": ["Zhipu AI/glm-5.3-flash（输入价低于缓存读价，判定为价目列错位）"]}
    assert merged["models"] == {}


def test_merge_fills_limit_and_description_only_when_missing():
    """cn 源补充上下文/简介：条目缺就填，已有 models.dev 元数据不覆盖。"""
    catalog = _catalog()
    catalog["models"]["glm5.3flash"]["limit"] = {"context": 262144, "output": 65536}
    catalog["models"]["glm5.3flash"]["description_zh"] = "已有中文简介"
    sources = {"Zhipu AI": {"url": "https://docs.bigmodel.cn/cn/guide/start/pricing.md", "enabled": True, "models": [
        {"model": "GLM-5.3-Flash", "input_price": 0.8, "output_price": 2.8, "currency": "CNY",
         "context": "1M", "description": "轻量快速档"},
        {"model": "GLM-5.2", "input_price": 6.0, "output_price": 24.0, "currency": "CNY",
         "context": "256K；最大输出 32K", "description": "旗舰主力"},
    ]}}
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    assert summary["matched"] == 1 and summary["added"] == 1
    # 已有元数据的条目不被源抓取结果覆盖
    flash = merged["models"]["glm5.3flash"]
    assert flash["limit"] == {"context": 262144, "output": 65536}
    assert flash["description_zh"] == "已有中文简介"
    glm52 = merged["models"]["glm5.2"]
    assert glm52["limit"] == {"context": 262144, "output": 32768}
    assert glm52["description_zh"] == "旗舰主力"
    # desc_fp 与简介指纹一致 → translate 视为已有译文，跳过翻译队列
    assert glm52["desc_fp"] == description_fingerprint_text("旗舰主力")


def test_merge_drops_unparseable_context():
    """时段列、输入分档这些不是上下文窗口的文本不进 limit，也不造脏数据。"""
    catalog = _catalog()
    sources = {"Zhipu AI": {"url": "https://docs.bigmodel.cn/cn/guide/start/pricing.md", "enabled": True, "models": [
        {"model": "GLM-5.3-Flash", "input_price": 0.8, "output_price": 2.8, "currency": "CNY",
         "context": "输入长度 [0, 32K)", "description": None},
    ]}}
    merged, _summary = merge_sources_into_catalog(catalog, sources, 7.0)
    entry = merged["models"]["glm5.3flash"]
    assert "limit" not in entry and "description_zh" not in entry and "desc_fp" not in entry


def test_merge_new_entry_carries_limit_and_description():
    catalog = _catalog()
    sources = {"Baichuan": {"url": "https://platform.baichuan-ai.com/prices", "enabled": True, "models": [
        {"model": "Baichuan-M3", "input_price": 5.0, "output_price": 9.0, "currency": "CNY",
         "context": "128K", "description": "百川新一代主力"},
    ]}}
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    assert summary["added"] == 1
    entry = merged["models"]["baichuanm3"]
    assert entry["limit"] == {"context": 131072, "output": None}
    assert entry["description_zh"] == "百川新一代主力"
    assert entry["desc_fp"] == description_fingerprint_text("百川新一代主力")


def test_merge_usd_page_keeps_list_and_converts_cny_reference():
    catalog = _catalog()
    sources = {"Zhipu AI": {"url": "https://docs.z.ai/pricing", "enabled": True, "models": [
        {"model": "glm-5.3-flash", "input_price": 0.075, "output_price": 0.25,
         "cache_read_price": None, "currency": "USD"},
    ]}}
    merged, _ = merge_sources_into_catalog(catalog, sources, 7.0)
    entry = merged["models"]["glm5.3flash"]
    assert entry["list"] == {"input": 0.075, "output": 0.25}
    assert entry["list_cny"] == {"input": 0.53, "output": 1.75}


def test_merge_skips_disabled_and_resolves_collision_first_wins():
    catalog = _catalog()
    sources = {
        "A Source": {"url": "u-a", "enabled": False, "models": [
            {"model": "GLM-5.3-Flash", "input_price": 9, "output_price": 9, "currency": "CNY"}]},
        "Zhipu AI": {"url": "u-b", "enabled": True, "models": [
            {"model": "GLM-5.3-Flash", "input_price": 0.8, "output_price": 2.8, "currency": "CNY"},
            {"model": "glm-5.3-flash", "input_price": 5, "output_price": 5, "currency": "CNY"}]},
    }
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    assert summary["matched"] == 1
    assert any("已停用" in item for item in summary["skipped"])
    assert any("撞模型键" in item for item in summary["skipped"])
    # 同源重复键先到先得：首条胜出；基准替换不污染 list_global（原基准已是 cn 时）
    assert merged["models"]["glm5.3flash"]["list_cny"] == {"input": 0.8, "output": 2.8}


# ---------- 海外源（region=global）只作国际参考 ----------

def test_merge_global_source_adds_reference_without_touching_baseline():
    catalog = _catalog()
    sources = {"Anthropic": {"url": "https://docs.anthropic.com/pricing", "enabled": True, "region": "global",
                             "models": [{"model": "qwen3-max", "input_price": 1.4, "output_price": 8.0,
                                         "cache_read_price": None, "currency": "USD"}]}}
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    assert summary["matched"] == 0 and summary["added"] == 0 and summary["referenced"] == 1
    entry = merged["models"]["qwen3max"]
    # 国内基准与 region 不动，海外页价只补 list_global 国际参考
    assert entry["region"] == "cn" and entry["source_url"] == "https://models.dev"
    assert entry["list"] == {"input": 1.291, "output": 7.749}
    assert entry["list_cny"] == {"input": 9.04, "output": 54.24}
    assert entry["list_global"] == {"input": 1.4, "output": 8.0}
    assert entry["list_global_cny"] == {"input": 9.8, "output": 56.0}


def test_merge_global_source_skips_global_entry_and_unlisted():
    catalog = _catalog()
    sources = {"Anthropic": {"url": "https://u", "enabled": True, "region": "global", "models": [
        {"model": "GLM-5.3-Flash", "input_price": 0.09, "output_price": 0.3, "currency": "USD"},
        {"model": "brand-new-model", "input_price": 1, "output_price": 2, "currency": "USD"},
    ]}}
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    assert summary["referenced"] == 0 and summary["added"] == 0 and summary["matched"] == 0
    assert any("已是国际口径" in item for item in summary["skipped"])
    assert any("目录未收录" in item for item in summary["skipped"])
    assert "brandnewmodel" not in merged["models"]  # 海外源不新增条目


def test_merge_global_source_defaults_to_usd_when_currency_missing():
    """海外页没标注货币时按美元折算（国内页默认人民币），不能拿美元价当人民币除汇率。"""
    catalog = _catalog()
    sources = {"Anthropic": {"url": "https://u", "enabled": True, "region": "global",
                             "models": [{"model": "qwen3-max", "input_price": 1.4, "output_price": 8.0,
                                         "currency": None}]}}
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    entry = merged["models"]["qwen3max"]
    assert summary["referenced"] == 1
    assert entry["list_global"] == {"input": 1.4, "output": 8.0}
    assert entry["list_global_cny"] == {"input": 9.8, "output": 56.0}


def test_merge_cn_source_wins_baseline_over_global_reference():
    catalog = _catalog()
    sources = {
        # 厂商名序 Anthropic 在先：先给 cn 基准条目补国际参考；Alibaba Cloud 随后替换基准
        "Anthropic": {"url": "https://u-a", "enabled": True, "region": "global", "models": [
            {"model": "qwen3-max", "input_price": 1.4, "output_price": 8.0, "currency": "USD"}]},
        "Alibaba Cloud": {"url": "https://u-b", "enabled": True, "region": "cn", "models": [
            {"model": "qwen3-max", "input_price": 1.2, "output_price": 7.0, "currency": "CNY"}]},
    }
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    entry = merged["models"]["qwen3max"]
    assert entry["region"] == "cn"
    assert entry["list_cny"] == {"input": 1.2, "output": 7.0}
    assert entry["list"] == {"input": round(1.2 / 7.0, 6), "output": round(7.0 / 7.0, 6)}
    assert entry["list_global"] == {"input": 1.4, "output": 8.0}  # 海外源补的参考价保留
    assert summary["matched"] == 1 and summary["referenced"] == 1


def test_validate_source():
    assert validate_source(" 智谱 ", " https://x.cn/a ") == ("智谱", "https://x.cn/a")
    with pytest.raises(ValueError):
        validate_source("", "https://x.cn")
    with pytest.raises(ValueError):
        validate_source("智谱", "ftp://x.cn")
    with pytest.raises(ValueError):
        validate_source("智谱", "not-a-url")


def test_validate_region():
    assert validate_region("") == "cn" and validate_region("CN") == "cn"  # 缺省国内
    assert validate_region("global") == "global"
    with pytest.raises(ValueError):
        validate_region("eu")


def test_upsert_region_defaults_cn_and_change_resets_results(tmp_path: Path):
    store = Store(tmp_path / "monitor.db")
    record = upsert_source(store, "Anthropic", "https://docs.anthropic.com/pricing")
    assert record["region"] == "cn"  # 未指定区域时缺省国内
    vs._record_fetch(store, "Anthropic", last_fetched_at=1.0, last_status="ok", last_error=None,
                     last_method="static-md", model_count=2, models=[{"model": "x"}])
    # 换区域清空上次抓取结果（合并口径变了，必须重抓）
    record = upsert_source(store, "Anthropic", "https://docs.anthropic.com/pricing", region="global")
    assert record["region"] == "global"
    assert record["last_status"] is None and record["models"] is None
    # 同区域再保存：配置字段照常更新，结果不复活
    record = upsert_source(store, "Anthropic", "https://docs.anthropic.com/pricing", region="global", enabled=False)
    assert record["enabled"] is False and record["region"] == "global" and record["last_status"] is None


# ---------- 抓取链路（真实 Store） ----------

def test_refresh_and_merge_updates_record_and_catalog(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "monitor.db")
    upsert_source(store, "Zhipu AI", "https://docs.bigmodel.cn/cn/guide/start/pricing.md")
    store.set_document("catalog", _catalog())
    seen_urls: list[str] = []

    def fake_fetch(url: str, **kwargs):
        seen_urls.append(url)
        return {"url": url, "final_url": url, "method": "static-md",
                "models": [{"model": "GLM-5.3-Flash", "input_price": 0.8, "output_price": 2.8,
                            "cache_read_price": 0.23, "currency": "CNY"}],
                "warnings": []}

    monkeypatch.setattr(vs, "fetch_page_prices", fake_fetch)
    summary = refresh_and_merge(store, "Zhipu AI", timeout=10, ai_config=None)
    assert summary["status"] == "ok" and summary["model_count"] == 1
    assert seen_urls == ["https://docs.bigmodel.cn/cn/guide/start/pricing.md"]
    record = vs.load_sources(store)["Zhipu AI"]
    assert record["last_status"] == "ok" and record["last_method"] == "static-md"
    merged = store.get_document("catalog")
    assert merged["models"]["glm5.3flash"]["list_cny"] == {"input": 0.8, "output": 2.8}


def test_refresh_source_filters_specials_and_snapshots_at_storage(tmp_path: Path, monkeypatch):
    """根源过滤：页面标注尾巴清洗、整行文案与并排共价行丢弃，特殊领域模型与日期后缀快照变体不落库。"""
    store = Store(tmp_path / "monitor.db")
    upsert_source(store, "Alibaba Cloud", "https://help.aliyun.com/zh/model-studio/models")

    def fake_fetch(url: str, **kwargs):
        return {"url": url, "final_url": url, "method": "static-md", "warnings": [], "models": [
            {"model": "qwen3.8-max", "input_price": 2.4, "output_price": 9.6, "currency": "CNY"},
            # 日期后缀快照变体：不落库
            {"model": "qwen3.8-max-0902", "input_price": 2.4, "output_price": 9.6, "currency": "CNY"},
            {"model": "qwen-plus-2025-07-28", "input_price": 0.8, "output_price": 2.0, "currency": "CNY"},
            # 特殊领域：语音合成与角色扮演
            {"model": "qwen3-tts-flash", "input_price": None, "output_price": 2.0, "currency": "CNY"},
            {"model": "qwen-flash-character", "input_price": 0.5, "output_price": 1.5, "currency": "CNY"},
            # 万相媒体生成：按名字拦
            {"model": "wan2.7-t2v", "input_price": 1.0, "output_price": 4.0, "currency": "CNY"},
            # 页面标注尾巴：剥掉还原成模型 id 后照常落库
            {"model": "kimi-k3上下文缓存享有折扣", "input_price": 20.0, "output_price": 100.0, "currency": "CNY"},
            # 整行页面文案与一行并排多模型共价：丢弃
            {"model": "参见模型列表", "input_price": None, "output_price": None, "currency": "CNY"},
            {"model": "MiMo-v2.6-Pro、MiMo-v2.5-Pro", "input_price": 1.0, "output_price": 4.0, "currency": "CNY"},
        ]}

    monkeypatch.setattr(vs, "fetch_page_prices", fake_fetch)
    summary = refresh_source(store, "Alibaba Cloud", timeout=5, ai_config=None)
    assert summary["status"] == "ok" and summary["model_count"] == 2
    stored = vs.load_sources(store)["Alibaba Cloud"]["models"]
    assert [item["model"] for item in stored] == ["qwen3.8-max", "kimi-k3"]
    assert [item["model_key"] for item in stored] == ["qwen3.8max", "kimik3"]


def test_merge_cleans_stale_cache_names_and_blocks_media(tmp_path: Path):
    """合并防御：旧缓存里的页面标注尾巴清洗后再合并，万相媒体模型不进任何目录。"""
    store = Store(tmp_path / "monitor.db")
    upsert_source(store, "Alibaba Cloud", "https://help.aliyun.com/zh/model-studio/model-pricing")
    vs._record_fetch(store, "Alibaba Cloud", last_fetched_at=1.0, last_status="ok", last_error=None,
                     last_method="static-md", model_count=3, models=[
                         {"model": "qwen3.8-max上下文缓存享有折扣", "input_price": 12.0, "output_price": 36.0, "currency": "CNY"},
                         {"model": "wan2.7-t2v", "input_price": 1.0, "output_price": 4.0, "currency": "CNY"},
                         {"model": "deepseek-v4-pro正式版", "input_price": 9.0, "output_price": 27.0, "currency": "CNY"},
                     ])
    sources = load_sources(store)
    merged, _summary = merge_sources_into_catalog({"usd_cny_rate": 7.0, "models": {}}, sources, 7.0)
    # qwen 清洗后进官方目录；deepseek 是托管他厂模型被品牌闸门拦；万相媒体模型被准入拦
    assert set(merged["models"]) == {"qwen3.8max"}
    assert merged["models"]["qwen3.8max"]["model"] == "qwen3.8-max"
    full_merged, _summary = merge_sources_into_channel_catalog({"usd_cny_rate": 7.0, "models": {}}, sources, 7.0)
    # 全量渠道目录：qwen 与托管的 deepseek 都在（干净名），万相不进
    assert set(full_merged["models"]) == {"alibabacloud:qwen3.8max", "alibabacloud:deepseekv4pro"}
    assert full_merged["models"]["alibabacloud:deepseekv4pro"]["model"] == "deepseek-v4-pro"


def test_merge_skips_specials_and_snapshots_from_stale_cache(tmp_path: Path):
    """合并防御：源缓存里若还存着旧的特殊领域/快照条目（未重抓），合并不收进官方目录。"""
    store = Store(tmp_path / "monitor.db")
    upsert_source(store, "Zhipu AI", "https://docs.bigmodel.cn/cn/guide/start/pricing.md")
    vs._record_fetch(store, "Zhipu AI", last_fetched_at=1.0, last_status="ok", last_error=None,
                     last_method="static-md", model_count=3, models=[
                         {"model": "GLM-5.3", "input_price": 8.0, "output_price": 32.0, "currency": "CNY"},
                         {"model": "glm-4-air-250414", "input_price": 0.5, "output_price": 2.0, "currency": "CNY"},
                         {"model": "GLM-OCR", "input_price": 1.0, "output_price": 4.0, "currency": "CNY"},
                     ])
    sources = load_sources(store)
    catalog = {"usd_cny_rate": 7.0, "models": {}}
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    assert set(merged["models"]) == {"glm5.3"}
    assert any("glm-4-air-250414" in line for line in summary["skipped"])


def test_refresh_source_failure_records_error_without_raising(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "monitor.db")
    upsert_source(store, "Zhipu AI", "https://down.test/pricing")

    def boom(url: str, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(vs, "fetch_page_prices", boom)
    summary = refresh_source(store, "Zhipu AI", timeout=5, ai_config=None)
    assert summary["status"] == "failed" and "connection refused" in summary["error"]
    record = vs.load_sources(store)["Zhipu AI"]
    assert record["last_status"] == "failed" and "connection refused" in str(record["last_error"])


def test_upsert_source_resets_result_on_url_change(tmp_path: Path):
    store = Store(tmp_path / "monitor.db")
    upsert_source(store, "Zhipu AI", "https://old.cn/pricing")
    vs._record_fetch(store, "Zhipu AI", last_fetched_at=1.0, last_status="ok", last_error=None,
                     last_method="static-md", model_count=3, models=[{"model": "x"}])
    record = upsert_source(store, "Zhipu AI", "https://new.cn/pricing")
    assert record["last_status"] is None and record["models"] is None and record["model_count"] is None
    # 同 URL 再保存：结果保留
    record = upsert_source(store, "Zhipu AI", "https://new.cn/pricing", enabled=False)
    assert record["enabled"] is False and record["last_status"] is None


def test_upsert_source_note_and_detection_source_note(tmp_path: Path):
    """note 写进源记录、随检测记录回传；不传时保留原值。"""
    store = Store(tmp_path / "monitor.db")
    record = upsert_source(store, "DeepSeek", "https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
                           note="官方中文定价页，人民币标价")
    assert record["note"] == "官方中文定价页，人民币标价"
    # 同 URL 再保存不传 note：保留；传空串：清空
    assert upsert_source(store, "DeepSeek", "https://api-docs.deepseek.com/zh-cn/quick_start/pricing")["note"] == "官方中文定价页，人民币标价"
    assert upsert_source(store, "DeepSeek", "https://api-docs.deepseek.com/zh-cn/quick_start/pricing", note="  ")["note"] is None

    providers = [_provider("deepseek", name="DeepSeek", doc="https://api-docs.deepseek.com")]
    upsert_source(store, "DeepSeek", "https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
                  note="官方中文定价页，人民币标价")
    record = next(r for r in detect_vendor_coverage(providers, vs.load_sources(store)) if r["vendor"] == "DeepSeek")
    assert record["source_added"] is True and record["source_note"] == "官方中文定价页，人民币标价"
    # 未配置的厂商：source_note 为空、source_added 为 False，note 仍是品牌提示
    pending = {r["vendor"]: r for r in detect_vendor_coverage(
        [_provider("zhipuai", name="Zhipu", doc="https://docs.z.ai")], vs.load_sources(store))}
    assert pending["Zhipu AI"]["source_note"] is None and pending["Zhipu AI"]["source_added"] is False
    assert pending["Zhipu AI"]["note"]
