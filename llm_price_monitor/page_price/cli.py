"""命令行入口：price-page 脚本的参数解析、AI 配置回落与结果输出。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

import llm_price_monitor.page_price as _pp
from llm_price_monitor.config import AIConfig


def resolve_ai_config(args: argparse.Namespace) -> tuple[AIConfig | None, str | None]:
    """CLI AI 配置解析：命令行参数优先，回落 PRICE_MONITOR_DB 库里的 ai document。

    返回 (配置, 说明)；说明非空时写进 warnings。
    """
    if args.ai_base_url or args.ai_api_key or args.ai_model:
        config = AIConfig(
            base_url=args.ai_base_url or "",
            models=(args.ai_model,) if args.ai_model else (),
            api_key=args.ai_api_key,
        )
        if not config.base_url:
            return None, "命令行只给了密钥/模型，没有 --ai-base-url，AI 兜底已跳过"
        return config, None
    if args.no_ai:
        return None, None
    db_path = Path(os.getenv("PRICE_MONITOR_DB") or "var/monitor.db")
    if not db_path.is_file():
        return None, f"找不到数据库 {db_path}（PRICE_MONITOR_DB），AI 兜底已跳过；可用 --ai-base-url/--ai-api-key 显式配置"
    try:
        from llm_price_monitor.config import config_from_store
        from llm_price_monitor.store import Store

        ai = config_from_store(Store(db_path)).ai
    except Exception as exc:  # 库损坏或配置非法：不阻断拉取，只放弃 AI 兜底
        return None, f"读取 AI 配置失败（{exc}），AI 兜底已跳过"
    if not ai.enabled:
        return None, "AI 配置处于禁用状态，AI 兜底已跳过"
    return ai, None


def main() -> None:
    parser = argparse.ArgumentParser(description="通用定价页模型价格拉取：输入 URL，输出结构化模型价格")
    parser.add_argument("url", help="定价页 URL（Markdown / HTML 表格 / JSON 价格表均可）")
    parser.add_argument("--out", help="结果写入的 JSON 文件；省略时打印到 stdout")
    parser.add_argument("--timeout", type=float, default=_pp.DEFAULT_TIMEOUT, help="抓取超时秒数（默认 45）")
    parser.add_argument("--headless", action="store_true", help="静态解析为空时尝试Headless 渲染（默认仅 JS 空壳页自动触发）")
    parser.add_argument("--no-ai", action="store_true", help="禁用 AI 兜底")
    parser.add_argument("--ai-base-url", help="AI 兜底的接口地址（默认读 PRICE_MONITOR_DB 库里的 ai 配置）")
    parser.add_argument("--ai-api-key", help="AI 兜底的密钥")
    parser.add_argument("--ai-model", help="AI 兜底使用的模型")
    args = parser.parse_args()

    ai_config, ai_note = _pp.resolve_ai_config(args)
    try:
        result = _pp.fetch_page_prices(args.url, ai_config=ai_config, timeout=args.timeout, headless=args.headless)
    except httpx.HTTPError as exc:
        result = {"url": args.url, "final_url": args.url, "method": "none", "models": [], "warnings": [f"抓取失败：{exc}"]}
    if ai_note:
        result["warnings"].append(ai_note)

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
        print(f"已写入 {args.out}：{len(result['models'])} 个模型（{result['method']}）")
    else:
        print(payload)
    for warning in result["warnings"]:
        print(f"警告：{warning}", file=sys.stderr)
    if not result["models"]:
        sys.exit(1)
