#!/usr/bin/env python3
"""将 channel_monitor 的 Token 统计与价格监控快照关联，估算请求成本。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from llm_price_monitor.units import unit_scale


def calculate_cost(state: dict[str, Any], price: dict[str, Any]) -> dict[str, Any]:
    samples = state.get("samples", [])
    input_tokens = sum(int(item.get("prompt_tokens") or 0) for item in samples if item.get("ok"))
    output_tokens = sum(int(item.get("completion_tokens") or 0) for item in samples if item.get("ok"))
    result: dict[str, Any] = {
        "model": price.get("model"),
        "price_status": price.get("price_status"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated_cost": None,
        "currency": None,
        "cost_status": "unavailable",
    }
    scale = unit_scale(str(price.get("unit", "")))
    if price.get("price_status") == "confirmed" and scale and price.get("input_price") is not None and price.get("output_price") is not None:
        currency, divisor = scale
        result["estimated_cost"] = input_tokens / divisor * float(price["input_price"]) + output_tokens / divisor * float(price["output_price"])
        result["currency"] = currency
        result["cost_status"] = "estimated"
    elif (price.get("metadata") or {}).get("pricing_kind") == "quota_multiplier":
        result["cost_status"] = "rule_only"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="关联渠道 Token 统计和价格快照")
    parser.add_argument("--state-file", required=True, help="channel_monitor.py 的 --state-file")
    parser.add_argument("--latest-file", default="var/price-latest.json")
    parser.add_argument("--site-id", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    state = json.loads(Path(args.state_file).read_text(encoding="utf-8"))
    latest = json.loads(Path(args.latest_file).read_text(encoding="utf-8"))
    key = f"{args.site_id}:{args.model}"
    price = latest.get(key)
    if not isinstance(price, dict):
        raise SystemExit(f"价格快照中不存在 {key}")
    print(json.dumps(calculate_cost(state, price), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
