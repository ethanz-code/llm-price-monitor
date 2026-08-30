"""Tavily 搜索：key 解析（参数 > 环境变量 > tvly 登录态）与搜索执行。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import httpx

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
_SESSION_FILE = Path.home() / ".tavily" / "session.json"


def resolve_tavily_key(explicit: str | None) -> str | None:
    """按 `--tavily-key` > `TAVILY_API_KEY` 环境变量 > tvly CLI 登录态 的顺序解析。"""
    import os

    if explicit:
        return explicit
    env_key = os.getenv("TAVILY_API_KEY")
    if env_key:
        return env_key
    if _SESSION_FILE.exists():
        try:
            session = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        for value in session.values():
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _search_via_cli(query: str, domains: list[str] | None) -> list[dict[str, Any]]:
    """tvly CLI 兜底（其鉴权走会话，与原始 API key 不同）。"""
    command = ["tvly", "search", query, "--json", "--max-results", "8", "--depth", "advanced"]
    if domains:
        command.extend(["--include-domains", ",".join(domains)])
    proc = subprocess.run(command, capture_output=True, text=True, timeout=90)
    if proc.returncode != 0:
        raise RuntimeError(f"tvly CLI 搜索失败: {proc.stderr.strip() or proc.stdout.strip()[:200]}")
    data = json.loads(proc.stdout)
    results = data.get("results", []) if isinstance(data, dict) else []
    return [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []


def tavily_search(
    client: httpx.Client,
    key: str | None,
    query: str,
    domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    """执行一次搜索；有 key 时走 HTTP API，失败或无 key 时落到 tvly CLI。"""
    if key:
        try:
            body: dict[str, Any] = {"api_key": key, "query": query, "search_depth": "advanced", "max_results": 8}
            if domains:
                body["include_domains"] = domains
            response = client.post(TAVILY_SEARCH_URL, json=body, timeout=30)
            response.raise_for_status()
            results = response.json().get("results", [])
            return [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []
        except httpx.HTTPError:
            pass
    return _search_via_cli(query, domains)
