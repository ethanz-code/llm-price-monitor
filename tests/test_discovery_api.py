"""站点发现接口：/api/discovery 以候选池为主表、探测档案联表，标注入库状态与空数据引导；
管理操作（刷新任务、勾选导入）走 POST。"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from llm_price_monitor import discover
from llm_price_monitor.webapi.app import create_app
from tests.test_webapi import _config, workspace  # noqa: F401  workspace 为共享 fixture


def _write_discovery(workspace: Path, results: list[dict], candidates: list[dict] | None = None) -> None:
    """candidates 是候选池（主表，刷新只拉站点与简介），results 是探测档案（CLI probe 产出，联表）。"""
    out = workspace / "discovery"
    out.mkdir(parents=True, exist_ok=True)
    (out / "candidates.json").write_text(
        json.dumps(
            {"generated_at": "2026-10-06 10:00:00", "count": len(candidates or []), "candidates": candidates or []},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (out / "probed.json").write_text(
        json.dumps({"generated_at": "2026-10-06 10:00:00", "results": results}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_discovery_404_when_never_ran(workspace: Path, monkeypatch):
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")
    client = TestClient(create_app(_config(workspace)))
    assert client.get("/api/discovery").status_code == 404


def test_discovery_lists_pool_and_joins_probe_archive(workspace: Path, monkeypatch):
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")
    # 池子里的站没探过也进列表（标 unknown）；探过但失联的不进列表只留计数；
    # _config 种子里有站点 id=demo（demo.test），按归一化域名标已监控
    _write_discovery(
        workspace,
        [
            {"name": "公开站", "url": "https://demo.test", "sources": ["zuiquanapi"], "online": True, "new_api": True, "pricing_ok": True, "models": 42, "auth_required": False},
            {"name": "要登录", "url": "https://auth.example.net", "sources": ["zuiquanapi"], "online": True, "new_api": True, "pricing_ok": False, "models": 0, "auth_required": True},
            {"name": "没价格接口", "url": "https://noprice.example.org", "sources": ["zuiquanapi"], "online": True, "new_api": False, "pricing_ok": False, "models": 0, "auth_required": False},
            {"name": "死站", "url": "https://dead.example.org", "sources": ["zuiquanapi"], "online": False, "new_api": False, "pricing_ok": False, "models": 0, "auth_required": False},
        ],
        candidates=[
            {"host": "demo.test", "url": "https://demo.test", "name": "公开站", "sources": ["zuiquanapi"], "note": "", "meta": {"description": "老牌站"}},
            {"host": "auth.example.net", "url": "https://auth.example.net", "name": "要登录", "sources": ["zuiquanapi"], "note": "", "meta": {}},
            {"host": "noprice.example.org", "url": "https://noprice.example.org", "name": "没价格接口", "sources": ["zuiquanapi"], "note": "", "meta": {}},
            {"host": "dead.example.org", "url": "https://dead.example.org", "name": "死站", "sources": ["zuiquanapi"], "note": "", "meta": {}},
            {"host": "fresh.example.org", "url": "https://fresh.example.org", "name": "新收录", "sources": ["zuiquanapi"], "note": "", "meta": {"description": "刚收录还没探测"}},
        ],
    )
    client = TestClient(create_app(_config(workspace)))
    resp = client.get("/api/discovery")
    assert resp.status_code == 200
    body = resp.json()
    assert body["generated_at"] == "2026-10-06 10:00:00"
    assert body["summary"] == {
        "total": 5, "online": 3, "unprobed": 1, "pricing_public": 1, "pricing_auth": 1, "dead": 1, "imported": 1,
    }
    by_host = {row["host"]: row for row in body["stations"]}
    assert set(by_host) == {"demo.test", "auth.example.net", "noprice.example.org", "fresh.example.org"}
    assert by_host["demo.test"]["pricing_state"] == "public"
    assert by_host["demo.test"]["imported_id"] == "demo"  # 归一化域名比对标注已监控
    assert by_host["demo.test"]["description"] == "老牌站"
    assert by_host["noprice.example.org"]["pricing_state"] == "none"
    assert by_host["auth.example.net"]["pricing_state"] == "auth"
    assert by_host["fresh.example.org"]["pricing_state"] == "unknown"  # 没探过照常展示
    # 未监控的排前面
    assert body["stations"][0]["host"] != "demo.test"


def _admin_client(workspace: Path, monkeypatch) -> TestClient:
    """建号登录的管理员客户端（POST 管理操作需要会话）。"""
    client = TestClient(create_app(_config(workspace)), client=("testclient", 50000))
    assert client.post("/api/setup", json={"username": "admin", "password": "password123"}).status_code == 200
    return client


def test_discovery_import_writes_minimal_disabled_sites(workspace: Path, monkeypatch):
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")
    _write_discovery(
        workspace,
        [],
        candidates=[
            {"host": "new.example.com", "url": "https://new.example.com", "name": "新站", "sources": ["zuiquanapi"], "note": "", "meta": {}},
            {"host": "www.demo.test", "url": "https://www.demo.test", "name": "库里已有", "sources": ["zuiquanapi"], "note": "", "meta": {}},
            {"host": "plain.example.com", "url": "https://plain.example.com", "name": "非 new-api", "sources": ["zuiquanapi"], "note": "", "meta": {}},
        ],
    )
    client = _admin_client(workspace, monkeypatch)
    resp = client.post("/api/discovery/import", json={"hosts": ["new.example.com", "www.demo.test", "ghost.example.io", "plain.example.com"], "enabled": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == ["new-example", "plain-example"]
    assert body["skipped"] == ["www.demo.test"]  # www 前缀归一后与库内 demo.test 视为同站
    assert body["missing"] == ["ghost.example.io"]
    config = client.app.state.store.get_site_config("new-example")
    assert config["network"]["url"] == "https://new.example.com/api/pricing"
    # 最小导入：只写站点入口地址与停用态，不预配公告（采集时自动推导 /api/status、/api/notice）
    assert "notice" not in config
    assert config["enabled"] is False


def test_discovery_refresh_submits_task(workspace: Path, monkeypatch):
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")

    stats = {"pool": 4300, "pool_added": 12}

    async def fake_refresh(proxy=None):
        return stats

    monkeypatch.setattr(discover, "refresh_pool", fake_refresh)
    # 任务注册表改为同步执行：任务体在测试里当场跑完，不依赖后台线程时序
    from llm_price_monitor.webapi import tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "submit", lambda kind, fn: (fn(), f"sync-{kind}")[1])
    client = _admin_client(workspace, monkeypatch)
    resp = client.post("/api/discovery/refresh")
    assert resp.status_code == 200
    assert resp.json()["task_id"] == "sync-discovery-refresh"


def test_scheduler_submits_discovery_job_when_due(tmp_path: Path, monkeypatch):
    """discovery 间隔到期时调度器提交 discovery-refresh；置 0 关闭不提交。"""
    from llm_price_monitor.store import Store
    from llm_price_monitor.webapi import scheduler as scheduler_mod, tasks

    submitted: list[str] = []
    monkeypatch.setattr(tasks, "submit", lambda kind, fn: submitted.append(kind) or "t")
    store = Store(tmp_path / "monitor.db")
    store.set_document(
        "settings", {"schedule": {"price": 0, "status": 0, "notice": 0, "catalog": 0, "rankings": 0, "discovery": 0.001}}
    )
    scheduler_mod._run_due(store)
    assert "discovery-refresh" in submitted
    # discovery 单独关闭时不提交
    submitted.clear()
    store.set_document(
        "settings", {"schedule": {"price": 0, "status": 0, "notice": 0, "catalog": 0, "rankings": 0, "discovery": 0}}
    )
    store.set_document("schedule_state", {})
    scheduler_mod._run_due(store)
    assert submitted == []
