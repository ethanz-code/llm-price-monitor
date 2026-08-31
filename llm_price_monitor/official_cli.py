"""官方价搜索与折扣率计算的 CLI 入口。

实现细节在 `official` 子包与 `monitor` 模块中；本模块只保留命令行编排。
由 pyproject `[project.scripts]` 注册为 fetch-official-prices / price-discount。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import httpx

from llm_price_monitor.config import MonitorConfig, load_config
from llm_price_monitor.env import load_env_files
from llm_price_monitor.report import run_once, summary_row
from llm_price_monitor.official import fx, jsonio, normalize, search, tavily
from llm_price_monitor.official.discount import compute_discounts, summarize

DEFAULT_OUTPUT = Path("var/official-prices.json")
DEFAULT_OFFICIAL_FILE = Path("var/official-prices.json")


def _error_line(message: str) -> str:
    return json.dumps({"status": "error", "error": message}, ensure_ascii=False)


def _pretty_line(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _parse_vendor_specs(raw: str) -> list[search.VendorSpec]:
    return [search.VendorSpec(name.strip()) for name in raw.split(",") if name.strip()]


def official_main() -> None:
    load_env_files()
    parser = argparse.ArgumentParser(description="Tavily 搜索各厂商官方模型原价，生成总结 JSON")
    parser.add_argument("--config", default="config/price-monitor.json", help="仅用于复用 AI 设置（base_url/model/api_key），默认价格监控配置")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="官方价总结 JSON 输出路径")
    parser.add_argument("--tavily-key", help="Tavily API key；缺省读 TAVILY_API_KEY 或 tvly 登录态")
    parser.add_argument("--vendors", help="逗号分隔的厂商名清单（按厂商搜官方定价页）；缺省用内置国内外知名厂商列表")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    if not config.ai.enabled or not config.ai.base_url or not config.ai.pick_model():
        print(_error_line("配置文件 ai 段未启用或未配置，无法提取官方价"))
        raise SystemExit(2)

    vendors = _parse_vendor_specs(args.vendors) if args.vendors else None
    try:
        output = fetch_official(config, Path(args.output), tavily_key=args.tavily_key, vendors=vendors)
    except ValueError as exc:
        print(_error_line(str(exc)))
        raise SystemExit(2) from exc

    models = output["models"]
    found_count = sum(1 for entry in models.values() if entry.get("found"))
    print(f'{{"status": "ok", "output": "{args.output}", "models_found": {found_count}, "models_total": {len(models)}}}')


def fetch_official(
    config: MonitorConfig,
    output_path: Path,
    *,
    tavily_key: str | None = None,
    vendors: list[search.VendorSpec] | None = None,
) -> dict:
    """Tavily 搜索各厂商官方价并写入 output_path，返回完整输出 dict；供 CLI 与 Web API 复用。"""
    key = tavily.resolve_tavily_key(tavily_key)
    if not key:
        raise ValueError("未找到 Tavily key（--tavily-key / TAVILY_API_KEY / tvly 登录态）")

    previous = jsonio.read_json_object(output_path).get("models", {})

    with httpx.Client(follow_redirects=True) as client:
        rate, rate_source = fx.get_usd_cny_rate(client)

    models: dict[str, dict] = {}
    for spec in vendors if vendors is not None else search.DEFAULT_VENDORS:
        result = search.search_vendor(config.ai, key, rate, vendor=spec.vendor, domains=spec.domains)
        for entry in result.entries:
            model_key = normalize.model_key(str(entry.get("model") or ""))
            if model_key:
                models[model_key] = entry

    # 全局补齐：本次没搜到、但上一轮已有的模型原样保留，避免一次波动丢数据。
    for old_key, old_entry in previous.items():
        if old_key not in models and isinstance(old_entry, dict) and old_entry.get("found"):
            models[old_key] = {**old_entry, "from_previous_run": True}

    output = {
        "generated_at": time.time(),
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "search_engine": "tavily",
        "usd_cny_rate": normalize.round2(rate),
        "rate_source": rate_source,
        "models": models,
    }
    jsonio.write_json(output_path, output)
    return output


def discount_main() -> None:
    load_env_files()
    parser = argparse.ArgumentParser(description="对比各站点价格与官方原价（来自官方价总结 JSON），计算折扣率")
    parser.add_argument("--input", help="llm_price_monitor 的输出 JSON；缺省时按 --config 现场采集")
    parser.add_argument("--config", help="价格监控配置；仅在未提供 --input、需要现场采集站点价格时使用")
    parser.add_argument("--official-file", default=str(DEFAULT_OFFICIAL_FILE), help="官方价总结 JSON，默认 var/official-prices.json")
    parser.add_argument("--site-id", help="只分析配置中的一个站点")
    parser.add_argument("--usd-cny", type=float, help="USD->CNY 汇率兜底值；仅在在线汇率获取失败时使用")
    args = parser.parse_args()

    official_report = jsonio.read_json(Path(args.official_file))
    official_models = official_report.get("models", {})
    if not isinstance(official_models, dict) or not official_models:
        print(_error_line(f"官方价文件 {args.official_file} 为空或格式不正确"))
        raise SystemExit(2)

    rows = _collect_site_rows(
        Path(args.config) if args.config else None,
        Path(args.input) if args.input else None,
        args.site_id,
    )

    with httpx.Client(follow_redirects=True) as client:
        rate, rate_source = fx.get_usd_cny_rate(client, args.usd_cny)

    discounts, skipped = compute_discounts(rows, official_models, rate)
    print(_pretty_line({
        "usd_cny_rate": normalize.round2(rate),
        "rate_source": rate_source,
        "official_file": args.official_file,
        "official_generated_at": official_report.get("generated_at_iso"),
        "discounts": [entry.as_dict() for entry in discounts],
        "summary": summarize(discounts),
        "skipped": skipped,
    }))


def _collect_site_rows(config_path: Path | None, input_path: Path | None, site_id: str | None) -> list[dict]:
    if input_path is not None:
        rows = jsonio.read_json(input_path).get("records", [])
    else:
        if config_path is None:
            raise ValueError("不传 --input 时必须提供 --config 以现场采集站点价格")
        print("提示: 未提供 --input，将现场请求配置中的全部站点采集价格（不写历史）；只读官方价与已有监控输出时请传 --input", file=sys.stderr)
        config = load_config(config_path)
        if site_id:
            sites = tuple(site for site in config.sites if site.id == site_id)
            if not sites:
                raise ValueError(f"配置中不存在站点: {site_id}")
            config = replace(config, sites=sites)
        rows = run_once(config, persist=False).records
    return [row if "tiers" in row else summary_row(row) for row in rows if isinstance(row, dict)]


if __name__ == "__main__":
    official_main()
