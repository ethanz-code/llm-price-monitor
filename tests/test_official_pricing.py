"""官方价结构规范化与折扣计算（llm_price_monitor/official/）的单元测试。"""
import pytest

from llm_price_monitor.official.discount import build_discount, compute_discounts, summarize
from llm_price_monitor.official.extraction import build_entry

# 新结构：原币价格 + effective 选用口径，换算由折扣计算用汇率完成
OFFICIAL_MODELS = {
    "gpt5.6sol": {
        "found": True,
        "model": "gpt-5.6-sol",
        "vendor": "OpenAI",
        "currency": "USD",
        "list": {"input": 5.0, "output": 30.0},
        "promo": {"input": 4.0, "output": 20.0, "ends_at": "2099-01-01", "valid": True},
        "effective": {"input": 4.0, "output": 20.0, "basis": "promo"},
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


def test_build_entry_uses_native_currency_with_list_promo_effective():
    entry = build_entry({
        "model": "gemini-3.6-flash", "currency": "USD",
        "list_input_price": 1.5, "list_output_price": 7.5,
        "promo_input_price": 0.75, "promo_output_price": 3.75, "promo_ends_at": "2099-12-31",
    }, "Google", "https://ai.google.dev")
    assert entry["currency"] == "USD"
    assert entry["list"] == {"input": 1.5, "output": 7.5}
    assert entry["promo"]["valid"] is True
    assert entry["effective"] == {"input": 0.75, "output": 3.75, "basis": "promo"}


def test_build_entry_expired_promo_falls_back_to_list():
    entry = build_entry({
        "model": "m", "currency": "USD",
        "list_input_price": 5.0, "list_output_price": 30.0,
        "promo_input_price": 1.0, "promo_output_price": 5.0, "promo_ends_at": "2000-01-01",
    }, "OpenAI", "https://example.com")
    assert entry["promo"]["valid"] is False
    assert entry["effective"] == {"input": 5.0, "output": 30.0, "basis": "list"}


def test_build_discount_converts_to_cny_and_computes_ratio():
    entry, reason = build_discount(_row(), OFFICIAL_MODELS, 6.74)
    assert reason is None
    data = entry.as_dict()
    assert data["official_input_cny"] == pytest.approx(26.96)
    assert data["input"] == pytest.approx(round(1.0 / (4.0 * 6.74), 2))
    assert data["basis"] == "promo"


def test_build_discount_native_cny_official():
    official = {"deepseekv4": {
        "found": True, "currency": "CNY",
        "effective": {"input": 2.0, "output": 8.0, "basis": "list"},
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
    assert entry.as_dict()["input"] == pytest.approx(round(4.36 / (4.0 * 6.74), 2))


def test_build_discount_usd_site_normalizes_via_rate():
    row = _row(input_price=0.6, output_price=3.0, unit="USD/1M tokens")
    entry, _ = build_discount(row, OFFICIAL_MODELS, 6.74)
    # 折扣率是同币种比值，汇率在分子分母同时出现应互相抵消
    assert entry.as_dict()["input"] == pytest.approx(round(0.6 / 4.0, 2))


def test_build_discount_skips_rows_without_official_or_price():
    entry, reason = build_discount(_row(model="unknown-model"), OFFICIAL_MODELS, 6.74)
    assert entry is None and "官方价" in reason
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
