"""站点发现接口：/api/discovery 直接下发候选池（站点、简介与监测源监控指标），
监控判离线的站忽略；管理操作（刷新任务、勾选导入）走 POST。"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from llm_price_monitor import discover
from llm_price_monitor.webapi.app import create_app
from tests.test_webapi import _config, workspace  # noqa: F401  workspace 为共享 fixture


def _write_pool(workspace: Path, candidates: list[dict]) -> None:
    out = workspace / "discovery"
    out.mkdir(parents=True, exist_ok=True)
    (out / "candidates.json").write_text(
        json.dumps(
            {"generated_at": "2026-10-06 10:00:00", "count": len(candidates), "candidates": candidates},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_discovery_404_when_never_ran(workspace: Path, monkeypatch):
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")
    client = TestClient(create_app(_config(workspace)))
    assert client.get("/api/discovery").status_code == 404


def test_discovery_lists_pool_with_monitor_stats(workspace: Path, monkeypatch):
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")
    # _config 种子里有站点 id=demo（demo.test），按归一化域名标已监控；
    # 监控判离线的站直接忽略（计数不占列表），没监控数据的站照常展示
    _write_pool(
        workspace,
        [
            {
                "host": "demo.test", "url": "https://demo.test", "name": "老牌站", "sources": ["zuiquanapi"], "note": "",
                "meta": {"description": "老牌中转站", "monitor_online": True, "uptime_7d": 99.5, "avg_ms": 1174, "last_ms": 900, "checked_at": "2026-10-06T07:37:04Z"},
            },
            {
                "host": "offline.example.org", "url": "https://offline.example.org", "name": "挂了站", "sources": ["zuiquanapi"], "note": "",
                "meta": {"monitor_online": False, "uptime_7d": 12.0, "avg_ms": None, "last_ms": None, "checked_at": "2026-10-06T07:40:00Z"},
            },
            {"host": "plain.example.com", "url": "https://plain.example.com", "name": "裸收录", "sources": ["zuiquanapi"], "note": "", "meta": {}},
        ],
    )
    client = TestClient(create_app(_config(workspace)))
    resp = client.get("/api/discovery")
    assert resp.status_code == 200
    body = resp.json()
    assert body["generated_at"] == "2026-10-06 10:00:00"
    assert body["summary"] == {"total": 3, "offline": 1, "imported": 1, "ignored": 0}
    by_host = {row["host"]: row for row in body["stations"]}
    assert set(by_host) == {"demo.test", "plain.example.com"}
    demo = by_host["demo.test"]
    assert demo["description"] == "老牌中转站"
    assert demo["uptime_7d"] == 99.5
    assert demo["avg_ms"] == 1174
    assert demo["last_ms"] == 900
    assert demo["imported_id"] == "demo"  # 归一化域名比对标注已监控
    assert by_host["plain.example.com"]["uptime_7d"] is None
    # 未监控的排前面；同组里可用率高的在前
    assert body["stations"][-1]["host"] == "demo.test"


def _admin_client(workspace: Path, monkeypatch) -> TestClient:
    """建号登录的管理员客户端（POST 管理操作需要会话）。"""
    client = TestClient(create_app(_config(workspace)), client=("testclient", 50000))
    assert client.post("/api/setup", json={"username": "admin", "password": "password123"}).status_code == 200
    return client


def test_discovery_import_writes_minimal_disabled_sites(workspace: Path, monkeypatch):
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")
    _write_pool(
        workspace,
        [
            {"host": "new.example.com", "url": "https://new.example.com", "name": "新站", "sources": ["zuiquanapi"], "note": "", "meta": {}},
            {"host": "www.demo.test", "url": "https://www.demo.test", "name": "库里已有", "sources": ["zuiquanapi"], "note": "", "meta": {}},
        ],
    )
    client = _admin_client(workspace, monkeypatch)
    resp = client.post("/api/discovery/import", json={"hosts": ["new.example.com", "www.demo.test", "ghost.example.io"], "enabled": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == ["new-example"]
    assert body["skipped"] == ["www.demo.test"]  # www 前缀归一后与库内 demo.test 视为同站
    assert body["missing"] == ["ghost.example.io"]
    config = client.app.state.store.get_site_config("new-example")
    assert config["network"]["url"] == "https://new.example.com/api/pricing"
    # 最小导入：只写站点入口地址与停用态，不预配公告（采集时自动推导 /api/status、/api/notice）
    assert "notice" not in config
    assert config["enabled"] is False


def test_discovery_ignore_marks_sink_and_unmarks(workspace: Path, monkeypatch):
    """标记「不看」：列表带 ignored 标记并沉底、不占 imported 计数；取消标记恢复原样。"""
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")
    _write_pool(
        workspace,
        [
            {"host": "plain.example.com", "url": "https://plain.example.com", "name": "裸收录", "sources": ["zuiquanapi"], "note": "", "meta": {}},
            {"host": "meh.example.net", "url": "https://meh.example.net", "name": "不想要的站", "sources": ["zuiquanapi"], "note": "", "meta": {}},
        ],
    )
    client = _admin_client(workspace, monkeypatch)
    resp = client.post("/api/discovery/ignore", json={"hosts": ["meh.example.net"], "ignored": True, "reason": "注册关闭"})
    assert resp.status_code == 200
    assert resp.json()["ignored"] == ["meh.example.net"]

    body = client.get("/api/discovery").json()
    assert body["summary"]["ignored"] == 1
    assert [row["ignored"] for row in body["stations"]] == [False, True]  # 标记的沉底
    assert body["stations"][-1]["host"] == "meh.example.net"
    assert body["stations"][-1]["ignore_reason"] == "注册关闭"

    # 取消标记后恢复原排序与计数，原因一并清掉
    assert client.post("/api/discovery/ignore", json={"hosts": ["meh.example.net"], "ignored": False}).status_code == 200
    body = client.get("/api/discovery").json()
    assert body["summary"]["ignored"] == 0
    assert all(not row["ignored"] for row in body["stations"])
    assert all(row["ignore_reason"] is None for row in body["stations"])

    # 再标记不带原因：原因留空
    client.post("/api/discovery/ignore", json={"hosts": ["meh.example.net"], "ignored": True})
    body = client.get("/api/discovery").json()
    assert body["stations"][-1]["ignored"] is True
    assert body["stations"][-1]["ignore_reason"] is None

    # 候选池里不存在的域名进 unknown，不误入标记清单
    resp = client.post("/api/discovery/ignore", json={"hosts": ["ghost.example.io"], "ignored": True})
    assert resp.json()["unknown"] == ["ghost.example.io"]
    # ghost 不在候选池进 unknown；meh 的忽略不受影响
    assert client.get("/api/discovery").json()["summary"]["ignored"] == 1


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
