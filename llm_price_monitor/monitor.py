#!/usr/bin/env python3
"""多站点模型价格监控器。

价格监控和渠道请求探测保持分离：本模块只负责读取价格源、保存快照、识别变化和发送告警。
价格源统一通过可配置的 HTTP JSON 接口读取，不启动浏览器或读取 DOM。

实现已按职责拆分；本文件只保留 CLI 入口和向后兼容的 re-export：
- config.py    强类型配置与校验
- useragent.py 浏览器 UA 构造与轮换
- matching.py  目标模型别名匹配
- evidence.py  证据捕获、脱敏与筛选
- units.py     价格数值与单位归一化
- ai.py        AI 价格抽取
- adapters.py  价格采集适配器
- report.py    运行编排、事件分类、持久化与汇总
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

import httpx

from llm_price_monitor.adapters import (
    ADAPTERS,
    BrowserAdapter,
    NetworkAdapter,
    Adapter,
    headers as _headers,
    network_pricing_records as _network_pricing_records,
)
from llm_price_monitor.ai import (
    AIDryRun,
    AIExtractionError,
    AIPriceExtractor,
    NEWAPI_ONEAPI_PRICING_GUIDANCE,
    ai_endpoint,
    chat_content,
    json_content,
)
from llm_price_monitor.config import (
    AIConfig,
    ChangeKind,
    ModelTarget,
    MonitorConfig,
    MonitorSettings,
    PriceMonitorError,
    PriceStatus,
    SiteSpec,
    load_config,
)
from llm_price_monitor.evidence import (
    decode_response_body as _decode_response_body,
    is_preferred_response_url as _is_preferred_response_url,
    payload_hash as _payload_hash,
    repair_mojibake as _repair_mojibake,
    target_page_text as _target_page_text,
)
from llm_price_monitor.matching import (
    canonical_target as _canonical_target,
    compact_model_text as _compact_model_text,
    contains_model_alias as _contains_model_alias,
    model_alias_pattern as _model_alias_pattern,
    model_aliases as _model_aliases,
)
from llm_price_monitor.env import load_env_files
from llm_price_monitor.report import (
    MonitorReport,
    PriceEvent,
    attach_official_discounts,
    run_once,
    summary_row as _summary_row,
)
from llm_price_monitor.useragent import (
    BROWSER_USER_AGENTS,
    DEFAULT_BROWSER_USER_AGENT,
    DEFAULT_CHROME_VERSION,
    choose_user_agent,
)


def main() -> None:
    load_env_files()
    parser = argparse.ArgumentParser(description="批量监控多个站点的模型价格")
    parser.add_argument("--config", required=True, help="价格监控配置文件；站点、目标模型、分组和倍率基准统一从这里读取")
    parser.add_argument("--site-id", help="只测试配置中的一个站点")
    parser.add_argument("--no-write", action="store_true", help="只测试采集，不写入历史、快照或事件")
    parser.add_argument("--dry-run", action="store_true", help="输出 AI 请求预览，不发起 AI HTTP 请求")
    parser.add_argument("--json", action="store_true", help="只输出 JSON 报告")
    parser.add_argument("--summary", action="store_true", help="只输出精简价格结果")
    parser.add_argument("--official-file", default="var/official-prices.json", help="官方价总结 JSON（fetch_official_prices.py 产出）；存在时每条记录附带折扣率，缺失时跳过折扣计算")
    args = parser.parse_args()
    try:
        config = load_config(Path(args.config))
        if args.site_id:
            sites = tuple(site for site in config.sites if site.id == args.site_id)
            if not sites:
                raise ValueError(f"配置中不存在站点: {args.site_id}")
            config = replace(config, sites=sites)
        if args.dry_run:
            config = replace(config, ai=replace(config.ai, dry_run=True))
        report = run_once(config, persist=not args.no_write)
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(2) from exc
    output = attach_official_discounts(asdict(report), Path(args.official_file))
    if args.summary or not args.json:
        print(json.dumps({
            "records": [_summary_row(row) for row in output["records"]],
            "official_prices": output["official_prices"],
            "errors": output["errors"],
        }, ensure_ascii=False, indent=2))
    elif args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
