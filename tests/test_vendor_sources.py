"""厂商定价源：覆盖检测、目录合并与抓取链路的单元测试。"""
from pathlib import Path

import httpx
import pytest

from llm_price_monitor.catalog import vendor_sources as vs
from llm_price_monitor.catalog.vendor_sources import (
    detect_vendor_coverage,
    merge_sources_into_catalog,
    refresh_and_merge,
    refresh_source,
    upsert_source,
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
    assert records["Zhipu AI"]["suggested_url"].startswith("https://docs.bigmodel.cn")
    assert records["Alibaba Cloud"]["verdict"] == "has_cn"
    assert records["Alibaba Cloud"]["providers"] == ["alibaba", "alibaba-cn"]
    assert records["Baidu"]["verdict"] == "not_listed"
    # 推荐添加的排前面：未收录 > 仅国际口径 > 已覆盖
    order = [record["verdict"] for record in detect_vendor_coverage(providers)]
    priority = {"not_listed": 0, "missing_cn": 1, "has_cn": 2}
    assert order == sorted(order, key=lambda verdict: priority[verdict])


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
        "B Source": {"url": "u-b", "enabled": True, "models": [
            {"model": "GLM-5.3-Flash", "input_price": 0.8, "output_price": 2.8, "currency": "CNY"}]},
        "C Source": {"url": "u-c", "enabled": True, "models": [
            {"model": "GLM-5.3-Flash", "input_price": 5, "output_price": 5, "currency": "CNY"}]},
    }
    merged, summary = merge_sources_into_catalog(catalog, sources, 7.0)
    assert summary["matched"] == 1
    assert any("已停用" in item for item in summary["skipped"])
    assert any("撞模型键" in item for item in summary["skipped"])
    # 厂商名排序先到先得：B 胜出；B 的基准替换不污染 list_global（原基准已是 cn 时）
    assert merged["models"]["glm5.3flash"]["list_cny"] == {"input": 0.8, "output": 2.8}


def test_validate_source():
    assert validate_source(" 智谱 ", " https://x.cn/a ") == ("智谱", "https://x.cn/a")
    with pytest.raises(ValueError):
        validate_source("", "https://x.cn")
    with pytest.raises(ValueError):
        validate_source("智谱", "ftp://x.cn")
    with pytest.raises(ValueError):
        validate_source("智谱", "not-a-url")


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
