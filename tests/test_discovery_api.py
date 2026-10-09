"""站点发现接口：/api/discovery 读取 discover 产出、标注入库状态与空数据引导；
管理操作（刷新任务、勾选导入）走 POST。"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from llm_price_monitor import discover
from llm_price_monitor.webapi.app import create_app
from tests.test_webapi import _config, workspace  # noqa: F401  workspace 为共享 fixture


def _write_discovery(workspace: Path, results: list[dict], candidates: list[dict] | None = None) -> None:
    out = workspace / "discovery"
    out.mkdir(parents=True, exist_ok=True)
    (out / "probed.json").write_text(
        json.dumps({"generated_at": "2026-10-05 10:00:00", "results": results}, ensure_ascii=False),
        encoding="utf-8",
    )
    (out / "candidates.json").write_text(
        json.dumps({"generated_at": "x", "count": len(candidates or []), "candidates": candidates or []}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_discovery_404_when_never_ran(workspace: Path, monkeypatch):
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")
    client = TestClient(create_app(_config(workspace)))
    assert client.get("/api/discovery").status_code == 404


def test_discovery_lists_online_and_marks_imported(workspace: Path, monkeypatch):
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")
    # _config 种子里有站点 id=demo（demo.test）；旧格式行（只有 pricing_ok）也要按在线识别
    _write_discovery(
        workspace,
        [
            {"name": "公开站", "url": "https://demo.test", "sources": ["zuiquanapi"], "new_api": True, "pricing_ok": True, "models": 42, "auth_required": False, "error": ""},
            {"name": "要登录", "url": "https://auth.example.net", "sources": ["zuiquanapi"], "new_api": True, "online": True, "pricing_ok": False, "models": 0, "auth_required": True, "error": ""},
            {"name": "没价格接口", "url": "https://noprice.example.org", "sources": ["zuiquanapi"], "new_api": False, "online": True, "pricing_ok": False, "models": 0, "auth_required": False, "error": ""},
            {"name": "死站", "url": "https://dead.example.org", "sources": ["zuiquanapi"], "new_api": False, "online": False, "pricing_ok": False, "models": 0, "auth_required": False, "error": "ConnectTimeout"},
        ],
    )
    client = TestClient(create_app(_config(workspace)))
    resp = client.get("/api/discovery")
    assert resp.status_code == 200
    body = resp.json()
    # 在线即可用：没价格接口的站也计入在线；死站只留计数
    assert body["summary"] == {"total": 4, "online": 3, "pricing_public": 1, "pricing_auth": 1, "dead": 1, "imported": 1}
    assert len(body["stations"]) == 3
    by_host = {row["host"]: row for row in body["stations"]}
    assert by_host["demo.test"]["imported_id"] == "demo"  # 归一化域名比对标注已监控
    assert by_host["demo.test"]["pricing_state"] == "public"
    assert by_host["noprice.example.org"]["pricing_state"] == "none"
    assert by_host["auth.example.net"]["pricing_state"] == "auth"
    # 未监控的排前面
    assert body["stations"][0]["host"] != "demo.test"


def _admin_client(workspace: Path, monkeypatch) -> TestClient:
    """建号登录的管理员客户端（POST 管理操作需要会话）。"""
    client = TestClient(create_app(_config(workspace)), client=("testclient", 50000))
    assert client.post("/api/setup", json={"username": "admin", "password": "password123"}).status_code == 200
    return client


def test_discovery_import_writes_disabled_sites(workspace: Path, monkeypatch):
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")
    _write_discovery(
        workspace,
        [
            {"name": "新站", "url": "https://new.example.com", "sources": ["zuiquanapi"], "new_api": True, "online": True, "pricing_ok": True, "models": 9, "auth_required": False, "error": ""},
            {"name": "库里已有", "url": "https://www.demo.test", "sources": ["zuiquanapi"], "new_api": True, "online": True, "pricing_ok": True, "models": 1, "auth_required": False, "error": ""},
            {"name": "非 new-api", "url": "https://plain.example.com", "sources": ["zuiquanapi"], "new_api": False, "online": True, "pricing_ok": False, "models": 0, "auth_required": False, "error": ""},
        ],
        candidates=[
            {"host": "new.example.com", "url": "https://new.example.com", "name": "新站", "sources": ["zuiquanapi"], "note": "", "meta": {}},
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
    # new-api 站导入即配好公告口径；非 new-api 站不乱配
    assert config["notice"] == {"url": "https://new.example.com/api/status", "parse": "status"}
    assert client.app.state.store.get_site_config("plain-example")["notice"] == {"url": "https://plain.example.com/api/status"}
    assert config["enabled"] is False


def test_discovery_refresh_submits_task(workspace: Path, monkeypatch):
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")

    stats = {"pool": 10, "pool_added": 2, "probed_now": 5, "online_now": 3, "online_total": 8, "importable": 6}

    async def fake_refresh(concurrency=16, timeout=8.0, proxy=None, progress=None):
        return stats

    monkeypatch.setattr(discover, "refresh_online", fake_refresh)
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
