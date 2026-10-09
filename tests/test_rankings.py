"""AA 模型榜单抓取、任务链路与只读端点（llm_price_monitor/catalog/rankings.py 等）的单元测试。"""
import json
import time
from pathlib import Path

import httpx
import pytest

from llm_price_monitor.catalog.rankings import fetch_rankings, parse_leaderboard, rank_for_key
from llm_price_monitor.webapi.app import create_app
from llm_price_monitor.webapi.jobs import rankings_refresh_job
from llm_price_monitor.webapi.scheduler import _run_due
from llm_price_monitor.store import Store


def _leaderboard_html() -> str:
    """按 AA 真实页面结构造一份最小 HTML：2 个表头行 + 3 个数据行（9 列）。"""
    def row(cells: list[str], slug: str | None = None) -> str:
        first = f'<a href="/models/{slug}">{cells[0]}</a>' if slug else cells[0]
        tds = f"<td>{first}</td>" + "".join(f"<td>{c}</td>" for c in cells[1:])
        return f"<tr>{tds}</tr>"

    header = "<tr><th>Model</th><th>Context Window</th><th>Creator</th><th>AA Index</th><th>Cost</th><th>Speed</th><th>Latency</th><th>Total</th><th>More</th></tr>"
    rows = [
        row(["GPT-6.5 Sol (max with fallback)", "1M", "OpenAI", "58", "$5.98", "--", "--", "--", "Model Providers"], "gpt-6-5-sol"),
        row(["GPT-6.5 Sol (high)", "1M", "OpenAI", "54", "$1.82", "81", "39.13", "45.31", "Model Providers"], "gpt-6-5-sol-high"),
        row(["Kimi K3 (low)", "1.05M", "Kimi", "34 *", "--", "1,565", "3.70", "71.34", "Model Providers"], "kimi-k3-low"),
    ]
    return f"<html><table>{header}{''.join(rows)}</table></html>"


# ---------- parse_leaderboard / rank_for_key ----------


def test_parse_leaderboard_maps_columns_and_rank_order():
    doc = parse_leaderboard(_leaderboard_html())
    assert [m["rank"] for m in doc["models"]] == [1, 2, 3]
    first = doc["models"][0]
    assert first["slug"] == "gpt-6-5-sol" and first["name"] == "GPT-6.5 Sol (max with fallback)"
    assert first["creator"] == "OpenAI" and first["context_window"] == "1M"
    assert first["intelligence_index"] == 58 and first["cost_per_task_usd"] == 5.98
    assert first["median_output_tokens_per_second"] is None  # "--" 是数据缺失，不是 0
    assert doc["models"][2]["median_output_tokens_per_second"] == 1565  # 千分位逗号
    assert doc["models"][2]["intelligence_index"] == 34  # 脚注标记 "*" 剥离
    assert doc["models"][1]["latency_first_chunk_seconds"] == 39.13
    assert first["url"].endswith("/models/gpt-6-5-sol")


def test_parse_leaderboard_rejects_no_rows_and_structural_change():
    with pytest.raises(ValueError, match="未解析到任何模型行"):
        parse_leaderboard("<html><p>empty</p></html>")
    # 数据行缺列：页面结构变化必须报错，不能静默产出残缺榜单
    broken = _leaderboard_html().replace("<td>Kimi</td><td>34 *</td>", "<td>Kimi</td>")
    with pytest.raises(ValueError, match="结构异常"):
        parse_leaderboard(broken)


def test_rank_for_key_matches_slug_and_dotted_catalog_ids():
    doc = parse_leaderboard(_leaderboard_html())
    # 目录 id 的版本点号（gpt-6.5-sol）与 AA slug 的连字符写法归一到同一个键
    hit = rank_for_key(doc, "gpt-6.5-sol")
    assert hit is not None and hit["rank"] == 1
    assert rank_for_key(doc, "gpt-6-5-sol")["rank"] == 1
    # 对不上的模型不硬凑
    assert rank_for_key(doc, "not-exist-model") is None
    assert rank_for_key(doc, "") is None
    assert rank_for_key(None, "gpt-6.5-sol") is None


# ---------- fetch_rankings ----------


def test_fetch_rankings_uses_httpx_and_parses_page():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=_leaderboard_html()))
    doc = fetch_rankings(transport=transport)
    assert len(doc["models"]) == 3
    assert doc["source"] == "artificialanalysis.ai"


# ---------- rankings_refresh_job / 调度 / 端点 ----------


def test_rankings_refresh_job_persists_document(tmp_path: Path, monkeypatch):
    # jobs.py 以 `from ... import fetch_rankings` 持有本地引用，须补丁 jobs 命名空间
    monkeypatch.setattr(
        "llm_price_monitor.webapi.jobs.fetch_rankings", lambda **kwargs: parse_leaderboard(_leaderboard_html())
    )
    store = Store(tmp_path / "monitor.db")
    summary = rankings_refresh_job(store)()
    assert summary["models_total"] == 3
    doc = store.get_document("rankings")
    assert doc is not None and len(doc["models"]) == 3
    # 上次刷新时间写入后，interval 内重复提交由调度器按间隔判断（这里只验证文档可读）
    assert time.time() - doc["generated_at"] < 60


def test_scheduler_submits_rankings_job_when_due(tmp_path: Path, monkeypatch):
    import llm_price_monitor.webapi.scheduler as scheduler_mod
    from llm_price_monitor.webapi import tasks

    submitted: list[str] = []
    monkeypatch.setattr(tasks, "submit", lambda kind, job, **kwargs: submitted.append(kind) or "t")
    store = Store(tmp_path / "monitor.db")
    store.set_document("settings", {"schedule": {"price": 0, "status": 0, "notice": 0, "catalog": 0, "rankings": 0.001}})
    _run_due(store)
    assert "rankings-refresh" in submitted
    # rankings 单独关闭时不提交
    submitted.clear()
    store.set_document("settings", {"schedule": {"price": 0, "status": 0, "notice": 0, "catalog": 0, "rankings": 0}})
    store.set_document("schedule_state", {})
    _run_due(store)
    assert submitted == []


def test_rankings_endpoints_public_read_admin_refresh(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "llm_price_monitor.webapi.jobs.fetch_rankings", lambda **kwargs: parse_leaderboard(_leaderboard_html())
    )
    app = create_app(_minimal_config(tmp_path))
    client = _admin_client(app)

    # 未同步前 404 带指引文案；刷新后公开可读
    assert client.get("/api/rankings").status_code == 404
    client.post("/api/rankings/refresh")
    for _ in range(200):
        if client.get("/api/rankings").status_code == 200:
            break
        time.sleep(0.02)
    doc = client.get("/api/rankings").json()
    assert len(doc["models"]) == 3 and doc["models"][0]["rank"] == 1


def _minimal_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "settings": {
            "history_file": str(tmp_path / "var" / "history.jsonl"),
            "latest_file": str(tmp_path / "var" / "latest.json"),
            "event_file": str(tmp_path / "var" / "events.jsonl"),
            "schedule": {"price": 0, "status": 0, "notice": 0, "catalog": 0, "rankings": 0},
        },
        "ai": {"enabled": False},
        "sites": [],
    }), encoding="utf-8")
    (tmp_path / "var").mkdir(exist_ok=True)
    return config_path


def _admin_client(app) -> "TestClient":
    """创建应用并完成首次设置，返回已登录管理员的客户端（会话 cookie 自动保持）。"""
    from fastapi.testclient import TestClient

    client = TestClient(app, client=("testclient", 50000))
    assert client.post("/api/setup", json={"username": "admin", "password": "password123"}).status_code == 200
    return client
