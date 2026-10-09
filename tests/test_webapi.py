import json
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from llm_price_monitor import wxpusher
from llm_price_monitor.catalog import fx
from llm_price_monitor.webapi.app import create_app


def _config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "settings": {
            "history_file": str(tmp_path / "var" / "history.jsonl"),
            "latest_file": str(tmp_path / "var" / "latest.json"),
            "event_file": str(tmp_path / "var" / "events.jsonl"),
            # 测试默认关闭后台调度，避免定时线程在用例间隙发起真实采集；
            # 调度行为由 test_scheduler_* 用例单独打开验证
            "schedule": {"price": 0, "status": 0, "notice": 0, "catalog": 0, "rankings": 0},
            "monitor_models": ["demo-model"],
        },
        "ai": {"enabled": False},
        "sites": [{
            "id": "demo",
            "adapter": "standard",
            "model_list_url": "https://demo.test/pricing",
            "request_headers": {"Authorization": "Bearer ${SECRET_TOKEN}"},
        }],
    }), encoding="utf-8")
    return config_path


def _latest_record() -> dict:
    return {
        "site_id": "demo",
        "model": "demo-model",
        "input_price": 5.0,
        "output_price": 25.0,
        "unit": "USD/1M tokens",
        "price_status": "confirmed",
        "requires_auth": False,
        "source_url": "https://demo.test/pricing",
        "captured_at": 1000.0,
        "fingerprint": "a" * 64,
        "metadata": {"group": "default"},
    }


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "latest.json").write_text(json.dumps({"demo:demo-model:default": _latest_record()}), encoding="utf-8")
    (tmp_path / "var" / "history.jsonl").write_text(
        json.dumps(_latest_record()) + "\n" + json.dumps({**_latest_record(), "input_price": 6.0}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "var" / "events.jsonl").write_text(json.dumps({
        "site_id": "demo", "model": "demo-model", "kind": "new", "detected_at": 1000.0,
    }) + "\n", encoding="utf-8")
    (tmp_path / "var" / "catalog.json").write_text(json.dumps({
        # generated_at 用当前时间：目录被视为新鲜，启动自动同步线程才不会触发网络请求
        "generated_at": time.time(),
        "generated_at_iso": "2026-08-31T00:00:00+0800",
        "usd_cny_rate": 7.0,
        "rate_source": "test",
        "models": {
            "demomodel": {
                "found": True, "model": "demo-model", "vendor": "Demo", "currency": "USD",
                "list": {"input": 10.0, "output": 50.0},
                "source_url": "https://demo.test/official",
                "description": "演示用模型简介",
            },
        },
    }), encoding="utf-8")
    monkeypatch.setattr(fx, "get_usd_cny_rate", lambda client, fallback=None: (7.0, "test"))
    return tmp_path


def test_health_and_meta_hide_secrets(workspace: Path):
    client = TestClient(create_app(_config(workspace)))
    assert client.get("/api/health").json() == {"status": "ok"}
    meta = client.get("/api/meta").json()
    assert meta["sites"][0]["id"] == "demo"
    assert meta["sites"][0]["models"] == ["demo-model"]
    body = json.dumps(meta)
    assert "SECRET_TOKEN" not in body and "request_headers" not in body


def test_latest_history_events_official_endpoints(workspace: Path):
    client = TestClient(create_app(_config(workspace)))
    assert "demo:demo-model:default" in client.get("/api/latest").json()
    history = client.get("/api/history", params={"model": "demo-model"}).json()
    assert history["total"] == 2 and len(history["records"]) == 2
    assert client.get("/api/history", params={"model": "other"}).json()["records"] == []
    # 价格事件与公告事件合并进统一事件流：总数分开计，列表按时间倒序
    feed = client.get("/api/feed", params={"events_limit": 10, "notice_limit": 10}).json()
    assert feed["price_total"] == 1 and feed["notice_total"] == 0
    assert [e["kind"] for e in feed["events"]] == ["new"]
    catalog = client.get("/api/catalog").json()
    assert catalog["models"]["demomodel"]["vendor"] == "Demo"


def test_feed_hides_group_removed_events(workspace: Path):
    """分组下线事件只在库里留档：/api/feed 为访客页与管理台共用的事件出口，在此一并隐藏。"""
    from llm_price_monitor.store import Store

    client = TestClient(create_app(_config(workspace)))
    store = Store(workspace / "var" / "monitor.db")
    store.append_events([
        {"site_id": "demo", "model": "demo-model", "kind": "changed", "detected_at": 1.0,
         "previous": {"input_price": 1.0}, "current": {"input_price": 2.0}},
        {"site_id": "demo", "model": "demo-model", "kind": "group_removed", "detected_at": 2.0,
         "previous": {"metadata": {"group": "vip"}}, "current": None},
    ])

    feed = client.get("/api/feed").json()
    assert all(event["kind"] != "group_removed" for event in feed["events"])
    removed_total = store.read_events(limit=1, kind="group_removed")[1]
    assert feed["price_total"] == store.read_events(limit=1)[1] - removed_total  # 总数与可见列表同口径
    assert "group_removed" in [event["kind"] for event in store.read_events(limit=50)[0]]  # 库里仍留档


def test_overview_attaches_discount_and_catalog_context(workspace: Path):
    client = TestClient(create_app(_config(workspace)))
    data = client.get("/api/overview").json()
    assert data["catalog"]["enabled"] is True
    assert data["records"][0]["discount"]["input"] == 0.5
    # 信息完整度：监控模型走通用清单不再计分，demo 认证/续签/附加地址都没配，得 0 分
    assert data["site_completeness"] == {"demo": 0}


def test_catalog_missing_returns_404(workspace: Path):
    (workspace / "var" / "catalog.json").unlink()
    # 调度已在 _config 里关闭，目录缺失时不会触发自动同步，保持"无数据"状态
    client = TestClient(create_app(_config(workspace)))
    assert client.get("/api/catalog").status_code == 404


def test_scheduler_submits_due_jobs_periodically(workspace: Path, monkeypatch):
    """调度器按 settings.schedule 的间隔周期提交任务；间隔为 0 的项不提交。"""
    import llm_price_monitor.webapi.jobs as jobs
    import llm_price_monitor.webapi.scheduler as scheduler_mod
    from llm_price_monitor.webapi import tasks

    def _fail_sync(**kwargs):
        raise ValueError("offline")

    monkeypatch.setattr(jobs, "fetch_catalogs", _fail_sync)
    monkeypatch.setattr(scheduler_mod, "CHECK_INTERVAL_SECONDS", 0.05)
    config_path = _config(workspace)
    doc = json.loads(config_path.read_text(encoding="utf-8"))
    doc["settings"]["schedule"] = {"price": 0, "status": 0, "notice": 0, "catalog": 0.001, "rankings": 0}
    config_path.write_text(json.dumps(doc), encoding="utf-8")

    # 等上一个测试留下的 catalog-refresh 线程退出，避免计数被在途任务干扰
    for _ in range(100):
        if not any(t["kind"] == "catalog-refresh" and t["status"] == "running" for t in tasks.recent(100)):
            break
        time.sleep(0.02)
    before = sum(1 for t in tasks.recent(100) if t["kind"] == "catalog-refresh")

    client = TestClient(create_app(config_path))
    time.sleep(0.6)
    after = sum(1 for t in tasks.recent(100) if t["kind"] == "catalog-refresh")
    assert client.get("/api/health").json() == {"status": "ok"}
    assert after - before >= 3


def test_catalog_auto_syncs_on_first_start(workspace: Path, monkeypatch):
    """空库首次启动自动从 models.dev 同步目录，无需手动触发。"""
    import time

    import llm_price_monitor.webapi.jobs as jobs
    from llm_price_monitor.webapi import tasks

    # 等上一个测试留下的后台采集线程全部退出：任务注册表是进程级共享的，
    # 残留的 running 任务会让本测试启动时的 catalog-refresh 提交撞互斥被 409 拒绝
    for _ in range(500):
        if not any(t["status"] == "running" for t in tasks.recent(100)):
            break
        time.sleep(0.02)

    (workspace / "var" / "catalog.json").unlink()
    doc = {
        "generated_at": 1000.0,
        "generated_at_iso": "2026-09-05T00:00:00+0800",
        "usd_cny_rate": 7.0,
        "rate_source": "test",
        "models": {
            "demomodel": {
                "found": True, "model": "demo-model", "vendor": "Demo", "currency": "USD",
                "list": {"input": 10.0, "output": 50.0},
            },
        },
    }
    monkeypatch.setattr(jobs, "fetch_catalogs", lambda **kwargs: (doc, {"models": {}}))
    # 只打开厂商定价调度（首轮 schedule_state 为空即视为到期，立即补一次），其余保持关闭
    config_path = _config(workspace)
    config_doc = json.loads(config_path.read_text(encoding="utf-8"))
    config_doc["settings"]["schedule"] = {"price": 0, "status": 0, "notice": 0, "catalog": 1440, "rankings": 0}
    config_path.write_text(json.dumps(config_doc), encoding="utf-8")
    client = TestClient(create_app(config_path))
    catalog_status = 0
    for _ in range(500):
        catalog_status = client.get("/api/catalog").status_code
        if catalog_status == 200:
            break
        time.sleep(0.02)
    # 轮询窗口拉长到 10s：负载高时后台任务可能晚于旧窗口完成，最后一次 GET 会 404
    assert catalog_status == 200, "轮询窗口内目录任务未完成"
    assert client.get("/api/catalog").json()["models"]["demomodel"]["vendor"] == "Demo"


def test_collect_runs_in_background_without_persist(workspace: Path, monkeypatch):
    from llm_price_monitor.report import MonitorReport
    import llm_price_monitor.webapi.routes.collect as collect_routes

    def fake_run_once(config, *, persist=True, **_kwargs):
        assert persist is False
        return MonitorReport(
            0.0,
            1.0,
            [{"model": "demo-model"}],
            [],
            [],
            status_events=[{"site_id": "demo", "kind": "status_changed", "changes": [{"op": "edit", "path": "$.a"}]}],
            notice_records=[{"site_id": "demo", "kind": "notice_init", "content": "维护公告"}],
            site_status={"demo": {"status": "auth_required", "error": "HTTP 401", "checked_at": 1.0}},
        )

    monkeypatch.setattr(collect_routes, "run_once", fake_run_once)
    client = _admin_client(workspace)
    task_id = client.post("/api/collect", json={"persist": False}).json()["task_id"]
    for _ in range(50):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] != "running":
            break
        threading.Event().wait(0.05)
    assert task["status"] == "done"
    assert task["result"]["events"] == []
    assert task["result"]["errors"] == []
    assert task["result"]["persisted"] is False
    assert [row["model"] for row in task["result"]["records"]] == ["demo-model"]
    # 公告与渠道状态结果随任务带回：公告含正文，状态只回传有变化的事件
    assert task["result"]["notices"] == [{"site_id": "demo", "kind": "notice_init", "content": "维护公告"}]
    assert task["result"]["statuses"] == [{"site_id": "demo", "kind": "status_changed", "changes": [{"op": "edit", "path": "$.a"}]}]
    # 逐站价格采集状态随任务带回：测试弹窗靠它把 401 这类原因报给用户
    assert task["result"]["site_price_status"] == [
        {"site_id": "demo", "status": "auth_required", "error": "HTTP 401"}
    ]


def test_collect_rejects_unknown_site_and_parallel_runs(workspace: Path, monkeypatch):
    import llm_price_monitor.webapi.routes.collect as collect_routes

    release = threading.Event()

    def slow_run_once(config, *, persist=True, **_kwargs):
        release.wait(2)
        return MonitorReport(0.0, 1.0, [], [], [])

    monkeypatch.setattr(collect_routes, "run_once", slow_run_once)
    client = _admin_client(workspace)
    assert client.post("/api/collect", json={"site_id": "nope"}).status_code == 400
    client.post("/api/collect", json={})
    conflict = client.post("/api/collect", json={})
    assert conflict.status_code == 409
    release.set()


def test_collect_test_not_blocked_by_global_collect(workspace: Path, monkeypatch):
    """站点测试采集不进采集互斥组：全局采集在跑时单站测试照常提交，同类测试之间仍互斥。"""
    from llm_price_monitor.report import MonitorReport
    import llm_price_monitor.webapi.routes.collect as collect_routes

    release = threading.Event()

    def slow_run_once(config, *, persist=True, **_kwargs):
        release.wait(2)
        return MonitorReport(0.0, 1.0, [], [], [])

    monkeypatch.setattr(collect_routes, "run_once", slow_run_once)
    client = _admin_client(workspace)
    assert client.post("/api/collect", json={}).status_code == 200
    # 全局采集运行中：单站测试不再被 409 拒绝，照常提交
    started = client.post("/api/collect", json={"site_id": "demo"})
    assert started.status_code == 200
    test_task_id = started.json()["task_id"]
    # 同类测试仍互斥：第二个测试要等第一个结束
    conflict = client.post("/api/collect", json={"site_id": "demo"})
    assert conflict.status_code == 409
    assert client.get(f"/api/tasks/{test_task_id}").json()["kind"] == "collect-test"
    release.set()


def test_collect_single_site_tests_disabled_site(workspace: Path, monkeypatch):
    """带 site_id 的显式单站请求视作手动测试：停用中的站点也要照常拉取。"""
    from llm_price_monitor.report import MonitorReport
    import llm_price_monitor.webapi.routes.collect as collect_routes

    captured = {}

    def fake_run_once(config, *, persist=True, **_kwargs):
        captured["sites"] = config.sites
        return MonitorReport(0.0, 1.0, [], [], [])

    monkeypatch.setattr(collect_routes, "run_once", fake_run_once)
    client = _admin_client(workspace)
    sites = client.get("/api/sites").json()["sites"]
    client.put("/api/sites/demo", json={"config": {**sites[0], "enabled": False}})

    task_id = client.post("/api/collect", json={"site_id": "demo", "persist": False}).json()["task_id"]
    for _ in range(50):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] != "running":
            break
        threading.Event().wait(0.05)
    assert task["status"] == "done"
    assert [site.id for site in captured["sites"]] == ["demo"]
    assert captured["sites"][0].enabled is True


def test_collect_site_ids_only_targets_enabled_sites(workspace: Path, monkeypatch):
    """批量采集只针对启用中的站点：停用或未知站点不进入本轮，全部无效时 400。"""
    from llm_price_monitor.report import MonitorReport
    import llm_price_monitor.webapi.routes.collect as collect_routes

    captured = {}

    def fake_run_once(config, *, persist=True, **_kwargs):
        captured["sites"] = config.sites
        captured["persist"] = persist
        return MonitorReport(0.0, 1.0, [], [], [])

    monkeypatch.setattr(collect_routes, "run_once", fake_run_once)
    client = _admin_client(workspace)
    demo = client.get("/api/sites").json()["sites"][0]
    client.post("/api/sites", json={"config": {**demo, "id": "off-site", "enabled": False}})

    task_id = client.post(
        "/api/collect", json={"site_ids": ["demo", "off-site", "nope"], "persist": True}
    ).json()["task_id"]
    for _ in range(50):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] != "running":
            break
        threading.Event().wait(0.05)
    assert task["status"] == "done"
    assert [site.id for site in captured["sites"]] == ["demo"]
    assert captured["sites"][0].enabled is True
    assert captured["persist"] is True

    assert client.post("/api/collect", json={"site_ids": ["off-site"]}).status_code == 400
    assert client.post("/api/collect", json={"site_ids": []}).status_code == 400


def _admin_client(
    workspace: Path, username: str = "admin", password: str = "s3cret", peer: tuple[str, int] | None = None
) -> TestClient:
    """创建应用并完成首次设置，返回已登录管理员的客户端（会话 cookie 自动保持）。
    peer 模拟直连来源地址：默认 testclient（非回环），传 ("127.0.0.1", …) 模拟本机反代转发。"""
    client = TestClient(create_app(_config(workspace)), client=peer or ("testclient", 50000))
    assert client.post("/api/setup", json={"username": username, "password": password}).status_code == 200
    return client


def test_setup_login_and_gate(workspace: Path):
    client = TestClient(create_app(_config(workspace)))

    # 首次启动无账号：needs_setup，写接口与管理接口 401，读接口公开
    meta = client.get("/api/meta").json()
    assert meta["needs_setup"] is True and meta["is_admin"] is False
    assert client.post("/api/collect", json={}).status_code == 401
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/health").status_code == 200

    # 弱密码拒绝；setup 成功后即登录；重复 setup 409
    assert client.post("/api/setup", json={"username": "ethan", "password": "123"}).status_code == 400
    assert client.post("/api/setup", json={"username": "ethan", "password": "s3cret"}).json() == {"is_admin": True}
    assert client.post("/api/setup", json={"username": "x", "password": "s3cret"}).status_code == 409
    assert client.get("/api/meta").json()["is_admin"] is True
    assert client.get("/api/settings").status_code == 200

    # 登出后恢复游客态；错误密码 401，正确密码重新登录
    client.post("/api/auth/logout")
    assert client.get("/api/meta").json()["is_admin"] is False
    assert client.post("/api/auth/login", json={"username": "ethan", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "ethan", "password": "s3cret"}).json() == {"is_admin": True}
    meta = client.get("/api/meta").json()
    assert meta["is_admin"] is True and meta["needs_setup"] is False


def test_read_gate_with_internal_token(workspace: Path, monkeypatch):
    """设置 PRICE_WEB_INTERNAL_TOKEN 后：匿名读接口一律 401，
    令牌请求与管理员会话放行；健康检查、登录态探测、助手状态保持公开。"""
    monkeypatch.setenv("PRICE_WEB_INTERNAL_TOKEN", "internal-secret")
    client = TestClient(create_app(_config(workspace)))

    # 匿名：数据读接口与接口文档全部关闭
    assert client.get("/api/meta").status_code == 401
    assert client.get("/api/overview").status_code == 401
    assert client.get("/openapi.json").status_code == 401
    # 访客功能例外保持公开
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/state").json() == {"needs_setup": True, "is_admin": False}

    # 内网令牌放行服务端渲染取数
    assert client.get("/api/meta", headers={"x-internal-token": "internal-secret"}).status_code == 200
    assert client.get("/api/overview", headers={"x-internal-token": "internal-secret"}).status_code == 200

    # 管理员会话同样放行（管理端浏览器走同源代理靠 cookie）
    assert client.post("/api/setup", json={"username": "ethan", "password": "s3cret"}).status_code == 200
    assert client.get("/api/meta").status_code == 200

    # 令牌未设置时不启用封锁，行为与旧版一致
    monkeypatch.delenv("PRICE_WEB_INTERNAL_TOKEN")
    open_client = TestClient(create_app(_config(workspace)))
    assert open_client.get("/api/meta").status_code == 200


def test_login_rate_limit_blocks_after_repeated_failures(workspace: Path):
    """同一来源连续失败达上限后 429 限流；成功登录清零计数。"""
    client = _admin_client(workspace)
    client.post("/api/auth/logout")
    for _ in range(4):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    # 成功登录清零失败计数，之后重新数满 5 次失败才触发限流
    assert client.post("/api/auth/login", json={"username": "admin", "password": "s3cret"}).status_code == 200
    client.post("/api/auth/logout")
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "s3cret"}).status_code == 429


def test_collect_status_admin_only_and_tasks_gated(workspace: Path):
    """collect_status（需要关注的站点）仅随管理员会话返回；任务读取接口仅管理员可用。"""
    from llm_price_monitor.store import Store

    Store(workspace / "var" / "monitor.db").set_document(
        "collect_status", {"demo": {"status": "error", "error": "boom"}}
    )
    guest = TestClient(create_app(_config(workspace)))
    assert guest.get("/api/overview").json()["collect_status"] == {}
    assert guest.get("/api/tasks").status_code == 401
    assert guest.get("/api/tasks/abc").status_code == 401

    admin = _admin_client(workspace)
    status = admin.get("/api/overview").json()["collect_status"]
    assert status["demo"]["error"] == "boom"
    assert admin.get("/api/tasks").status_code == 200


def test_settings_roundtrip_and_validation(workspace: Path):
    client = _admin_client(workspace)
    saved = client.put("/api/settings", json={
        "settings": {"timeout": 30},
        "ai": {"base_url": "https://ai.test/v1", "models": ["m-a", "m-b"], "api_key": "sk-test-1234"},
    })
    assert saved.status_code == 200
    # 响应里只回掩码，完整密钥只存在库里
    assert saved.json()["ai"]["api_key"] == "••••1234"
    assert client.get("/api/settings").json()["ai"]["api_key"] == "••••1234"
    store = client.app.state.store
    assert store.get_document("ai")["api_key"] == "sk-test-1234"

    # 原样回传掩码 = 保持不变；显式传 null = 清除
    assert client.put("/api/settings", json={"ai": {"api_key": "••••1234"}}).status_code == 200
    assert store.get_document("ai")["api_key"] == "sk-test-1234"
    assert client.put("/api/settings", json={"ai": {"api_key": None}}).status_code == 200
    assert not store.get_document("ai").get("api_key")

    reloaded = client.get("/api/settings").json()
    assert reloaded["ai"]["models"] == ["m-a", "m-b"]

    bad = client.put("/api/settings", json={"ai": {"timeout": "abc"}})
    assert bad.status_code == 400


def test_sites_crud_requires_admin_and_validates(workspace: Path):
    client = TestClient(create_app(_config(workspace)))
    assert client.get("/api/sites").status_code == 401
    assert client.post("/api/sites", json={"config": {"id": "x"}}).status_code == 401
    client = _admin_client(workspace)

    sites = client.get("/api/sites").json()["sites"]
    assert [site["id"] for site in sites] == ["demo"]

    created = client.post("/api/sites", json={
        "config": {"id": "x", "network": {"url": "https://x.test/api"}, "models": ["m1"]}
    })
    assert created.status_code == 200

    duplicate = client.post("/api/sites", json={
        "config": {"id": "x", "network": {"url": "https://x.test/api"}, "models": ["m1"]}
    })
    assert duplicate.status_code == 409

    unknown_field = client.post("/api/sites", json={"config": {"id": "y", "oops": 1}})
    assert unknown_field.status_code == 400

    bad_url = client.post("/api/sites", json={
        "config": {"id": "y", "network": {"url": "notaurl"}, "models": ["m1"]}
    })
    assert bad_url.status_code == 400

    updated = client.put("/api/sites/demo", json={"config": {**sites[0], "enabled": False}})
    assert updated.status_code == 200 and updated.json()["site"]["enabled"] is False

    renamed = client.put("/api/sites/demo", json={"config": {**sites[0], "id": "demo2"}})
    assert renamed.status_code == 200
    ids = [site["id"] for site in client.get("/api/sites").json()["sites"]]
    assert "demo2" in ids and "demo" not in ids

    assert client.delete("/api/sites/x").status_code == 200
    assert client.delete("/api/sites/x").status_code == 404


def test_site_update_with_group_filter_cleans_status_history(workspace: Path):
    """编辑站点设置分组过滤时，库里未选中分组的历史状态与价格数据一并清理；口径不变或清空则不动。"""
    from llm_price_monitor.store import Store

    client = _admin_client(workspace)
    config = client.get("/api/sites").json()["sites"][0]
    store = Store(workspace / "var" / "monitor.db")
    store.append_status_records([
        {
            "site_id": "demo",
            "captured_at": 1.0,
            "data": {"channels": [{"name": "svip", "state": "ok"}, {"name": "vip", "state": "down"}]},
        },
    ])
    store.replace_latest({
        "demo:demo-model:svip": {"site_id": "demo", "model": "demo-model", "input_price": 1.0, "metadata": {"group": "svip"}},
        "demo:demo-model:default": {"site_id": "demo", "model": "demo-model", "input_price": 2.0, "metadata": {"group": "default"}},
    })
    store.append_events([
        {"site_id": "demo", "model": "demo-model", "kind": "changed", "detected_at": 1.0,
         "previous": {"metadata": {"group": "default"}}, "current": {"metadata": {"group": "default"}}},
    ])

    status = {"url": "https://demo.test/status", "groups": ["svip"]}
    _, events_total = store.read_events(site_id="demo", limit=100)
    saved = client.put("/api/sites/demo", json={"config": {**config, "status": status}})
    assert saved.status_code == 200 and saved.json()["cleaned"]["records"] == 1
    cleaned = saved.json()["cleaned"]
    assert cleaned["price_latest"] >= 1 and cleaned["price_events"] >= 1
    records, _ = store.read_status(site_id="demo")
    assert records[0]["data"]["channels"] == [{"name": "svip", "state": "ok"}]
    assert {key for key in store.latest_all() if key.startswith("demo:")} == {"demo:demo-model:svip"}
    _, remaining_events = store.read_events(site_id="demo", limit=100)
    assert cleaned["price_events"] == events_total - remaining_events

    # 相同分组重复保存不再清理；去掉过滤恢复全量采集，也不动数据
    again = client.put("/api/sites/demo", json={"config": {**config, "status": status}})
    assert "cleaned" not in again.json()
    cleared = client.put("/api/sites/demo", json={"config": {**config, "status": {"url": "https://demo.test/status"}}})
    assert cleared.status_code == 200 and "cleaned" not in cleared.json()


def test_site_groups_endpoint_lists_collected_groups(workspace: Path):
    """分组白名单下拉的数据源接口：该站点已入库价格数据的去重分组名；读路径按管理台口径鉴权。"""
    from llm_price_monitor.store import Store

    assert TestClient(create_app(_config(workspace))).get("/api/sites/demo/groups").status_code == 401

    client = _admin_client(workspace)
    # 工作区夹具预置的采集历史里已带 default 分组
    assert client.get("/api/sites/demo/groups").json() == {"groups": ["default"]}

    store = Store(workspace / "var" / "monitor.db")
    store.append_history(
        [
            {"site_id": "demo", "model": "m1", "metadata": {"group": "svip"}, "input_price": 1.0, "captured_at": 1.0},
            {"site_id": "demo", "model": "m3", "metadata": {"group": "svip"}, "input_price": 3.0, "captured_at": 3.0},
        ]
    )
    assert client.get("/api/sites/demo/groups").json() == {"groups": ["default", "svip"]}
    assert client.get("/api/sites/unknown/groups").json() == {"groups": []}


def test_site_delete_purge_optionally_cleans_history(workspace: Path):
    """DELETE ?purge=true 同时清理站点相关数据；默认删除保留历史与事件。"""
    client = _admin_client(workspace)
    config = client.get("/api/sites").json()["sites"][0]

    assert client.delete("/api/sites/demo").status_code == 200
    assert client.get("/api/history", params={"site_id": "demo"}).json()["total"] == 2
    assert "demo:demo-model:default" in client.get("/api/latest").json()

    # 重新建站后带 purge 删除：历史、事件与最新快照一并清理
    assert client.post("/api/sites", json={"config": config}).status_code == 200
    assert client.delete("/api/sites/demo", params={"purge": "true"}).status_code == 200
    assert client.get("/api/history", params={"site_id": "demo"}).json()["total"] == 0
    assert client.get("/api/feed").json()["price_total"] == 0
    assert "demo:demo-model:default" not in client.get("/api/latest").json()
    assert client.delete("/api/sites/demo").status_code == 404


def test_tasks_listed_after_collect(workspace: Path, monkeypatch):
    from llm_price_monitor.report import MonitorReport
    import llm_price_monitor.webapi.routes.collect as collect_routes

    monkeypatch.setattr(collect_routes, "run_once", lambda config, **kwargs: MonitorReport(0.0, 1.0, [], [], []))
    client = _admin_client(workspace)
    task_id = client.post("/api/collect", json={}).json()["task_id"]
    listed = client.get("/api/tasks").json()["tasks"]
    assert task_id in [task["id"] for task in listed]


def test_feedback_submit_validates_and_rate_limits(workspace: Path) -> None:
    client = _admin_client(workspace)
    for _ in range(3):
        assert client.post("/api/feedback", json={"content": "希望支持导出", "contact": "a@b.c"}).status_code == 200
    assert client.post("/api/feedback", json={"content": "第 4 条"}).status_code == 429
    assert client.post("/api/feedback", json={"content": "   "}).status_code == 400
    with sqlite3.connect(workspace / "var" / "monitor.db") as conn:
        rows = conn.execute("SELECT content, contact FROM feedback ORDER BY id").fetchall()
    assert rows == [("希望支持导出", "a@b.c")] * 3


def test_feedback_pushes_wxpusher_when_configured(workspace: Path, monkeypatch) -> None:
    client = _admin_client(workspace)
    sent: dict = {}

    def fake_send(*, app_token: str, content: str, summary: str, uid: str | None, timeout: float = 10.0) -> None:
        sent.update(app_token=app_token, content=content, uid=uid)

    monkeypatch.setattr(wxpusher, "send_wxpusher", fake_send)
    assert client.put(
        "/api/settings", json={"settings": {"wxpusher_app_token": "AT_x", "wxpusher_uid": "UID_y"}}
    ).status_code == 200
    assert client.post("/api/feedback", json={"content": "建议加个深色模式"}).status_code == 200
    assert sent["app_token"] == "AT_x" and sent["uid"] == "UID_y"
    assert "建议加个深色模式" in sent["content"]


def test_feedback_push_failure_does_not_block_save(workspace: Path, monkeypatch) -> None:
    client = _admin_client(workspace)
    monkeypatch.setattr(wxpusher, "send_wxpusher", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("网络故障")))
    assert client.put("/api/settings", json={"settings": {"wxpusher_app_token": "AT_x"}}).status_code == 200
    assert client.post("/api/feedback", json={"content": "推送失败也要入库"}).status_code == 200
    with sqlite3.connect(workspace / "var" / "monitor.db") as conn:
        rows = conn.execute("SELECT content FROM feedback").fetchall()
    assert rows == [("推送失败也要入库",)]



def test_site_submission_saves_pushes_and_admin_manages(workspace: Path, monkeypatch) -> None:
    sent: dict = {}

    def fake_send(*, app_token: str, content: str, summary: str, uid: str | None, timeout: float = 10.0) -> None:
        sent.update(app_token=app_token, content=content, uid=uid)

    monkeypatch.setattr(wxpusher, "send_wxpusher", fake_send)
    anon = TestClient(create_app(_config(workspace)))
    # 未登录也能提交（公开写接口），但看不到列表
    assert anon.post(
        "/api/site-submissions",
        json={"name": "Example 中转", "url": "https://example.com", "models": "gpt-5.6", "contact": "a@b.c"},
    ).status_code == 200
    assert anon.get("/api/admin/site-submissions").status_code == 401
    # 校验：地址必须 http(s) 开头
    assert anon.post("/api/site-submissions", json={"name": "x", "url": "ftp://bad"}).status_code == 400

    assert not sent  # 未配置 WxPusher 时不应推送
    sent.clear()
    client = _admin_client(workspace)
    client.put("/api/settings", json={"settings": {"wxpusher_app_token": "AT_x", "wxpusher_uid": "UID_y"}})
    assert client.post(
        "/api/site-submissions", json={"name": "第二家", "url": "https://two.example.com"}
    ).status_code == 200
    assert "第二家" in sent["content"] and sent["uid"] == "UID_y"

    listed = client.get("/api/admin/site-submissions").json()
    assert listed["total"] == 2
    first_id = listed["submissions"][-1]["id"]  # 倒序，最早一条在末尾
    assert listed["submissions"][-1]["status"] == "new"
    assert client.post(f"/api/admin/site-submissions/{first_id}/status", json={"status": "done"}).status_code == 200
    assert client.get("/api/admin/site-submissions", params={"status": "done"}).json()["total"] == 1
    assert client.post("/api/admin/site-submissions/999/status", json={"status": "done"}).status_code == 404
    with sqlite3.connect(workspace / "var" / "monitor.db") as conn:
        rows = conn.execute("SELECT name, status FROM site_submissions ORDER BY id").fetchall()
    assert rows == [("Example 中转", "done"), ("第二家", "new")]


def test_site_submission_rate_limits_per_ip(workspace: Path) -> None:
    client = TestClient(create_app(_config(workspace)))
    for _ in range(3):
        assert client.post("/api/site-submissions", json={"name": "x", "url": "https://x.test"}).status_code == 200
    assert client.post("/api/site-submissions", json={"name": "x", "url": "https://x.test"}).status_code == 429


def test_settings_test_probes_external_services_with_form_values(workspace: Path, monkeypatch) -> None:
    import llm_price_monitor.webapi.routes.settings as settings_routes

    client = _admin_client(workspace)

    # 未配置 AI / WxPusher 时给出可读的 400
    missing_ai = client.post("/api/settings/test", json={"target": "ai"})
    assert missing_ai.status_code == 400 and "Base URL" in missing_ai.json()["detail"]
    missing_wx = client.post("/api/settings/test", json={"target": "wxpusher"})
    assert missing_wx.status_code == 400 and "Token" in missing_wx.json()["detail"]

    # 先保存一套配置；测试仍以表单当前值为准（先测后存）
    assert client.put("/api/settings", json={
        "settings": {},
        "ai": {"base_url": "https://saved.test/v1", "models": ["saved-model"]},
    }).status_code == 200

    seen: dict = {}

    def fake_ping(config, model):
        seen["ai_base_url"] = config.base_url
        seen["ai_model"] = model
        return "ok"

    def fake_send(*, app_token, content, summary, uid, timeout=10.0):
        seen["wx_token"] = app_token
        seen["wx_uid"] = uid

    monkeypatch.setattr(settings_routes, "ping_model", fake_ping)
    monkeypatch.setattr(wxpusher, "send_wxpusher", fake_send)

    # AI：表单值覆盖已存值，模型取列表第一个
    ai_probe = client.post("/api/settings/test", json={
        "target": "ai",
        "ai": {"base_url": "https://form.test/v1", "models": ["form-model-a", "form-model-b"], "api_key": "sk-form"},
    }).json()
    assert ai_probe["ok"] is True and ai_probe["model"] == "form-model-a" and ai_probe["reply"] == "ok"
    assert ai_probe["elapsed_ms"] >= 0
    assert seen["ai_base_url"] == "https://form.test/v1"

    # WxPusher：表单值直达推送
    wx_probe = client.post("/api/settings/test", json={
        "target": "wxpusher",
        "settings": {"wxpusher_app_token": "AT_form", "wxpusher_uid": "UID_form"},
    }).json()
    assert wx_probe["ok"] is True
    assert seen["wx_token"] == "AT_form" and seen["wx_uid"] == "UID_form"

    # 未知 target 走 pydantic 校验拒绝
    assert client.post("/api/settings/test", json={"target": "nope"}).status_code == 422


def test_settings_test_proxy_checks_connectivity_and_exit_ip(workspace: Path, monkeypatch) -> None:
    import llm_price_monitor.webapi.routes.settings as settings_routes

    client = _admin_client(workspace)

    # 未填地址时给出可读的 400
    missing = client.post("/api/settings/test", json={"target": "proxy"})
    assert missing.status_code == 400 and "备用代理" in missing.json()["detail"]

    seen: dict = {}

    def fake_probe(proxy_url: str, *, transport=None):
        seen["proxy_url"] = proxy_url
        return {"ok": True, "elapsed_ms": 123, "exit_ip": "45.67.89.10"}

    monkeypatch.setattr(settings_routes, "probe_proxy", fake_probe)

    # 先测后存：表单当前值直达，不依赖已保存配置
    probe = client.post("/api/settings/test", json={
        "target": "proxy",
        "settings": {"fallback_proxy": "http://172.17.0.1:7890"},
    }).json()
    assert probe["ok"] is True and probe["exit_ip"] == "45.67.89.10" and probe["elapsed_ms"] == 123
    assert seen["proxy_url"] == "http://172.17.0.1:7890"

    # 探测失败 → 400 带可读原因
    def boom(proxy_url: str, *, transport=None):
        raise ValueError("经代理连不通外网（检查代理客户端是否在跑、地址是否可达）：boom")

    monkeypatch.setattr(settings_routes, "probe_proxy", boom)
    failed = client.post("/api/settings/test", json={
        "target": "proxy",
        "settings": {"fallback_proxy": "http://172.17.0.1:7890"},
    })
    assert failed.status_code == 400 and "连不通" in failed.json()["detail"]


def test_proxy_status_probes_saved_config_each_call(workspace: Path, monkeypatch) -> None:
    import llm_price_monitor.webapi.routes.settings as settings_routes

    client = _admin_client(workspace)

    # 未配置备用代理：直接 configured False，不触发探测
    calls: list[str] = []
    monkeypatch.setattr(settings_routes, "probe_proxy", lambda url, **kw: calls.append(url))
    assert client.get("/api/settings/proxy-status").json() == {"configured": False}
    assert calls == []

    # 已配置：每次调用都实发探测（不做缓存），走状态档专用短超时
    assert client.put("/api/settings", json={"settings": {"fallback_proxy": "http://172.17.0.1:7890"}}).status_code == 200

    def fake_probe(url: str, *, transport=None, timeout=None):
        calls.append(url)
        assert timeout == settings_routes.PROXY_STATUS_TIMEOUT_SECONDS
        return {"ok": True, "elapsed_ms": 321, "exit_ip": "203.10.99.36"}

    monkeypatch.setattr(settings_routes, "probe_proxy", fake_probe)
    assert client.get("/api/settings/proxy-status").json() == {
        "configured": True,
        "ok": True,
        "elapsed_ms": 321,
        "exit_ip": "203.10.99.36",
    }
    assert calls == ["http://172.17.0.1:7890"]
    assert client.get("/api/settings/proxy-status").json()["elapsed_ms"] == 321
    assert len(calls) == 2

    # 探测失败：返回 ok False + 可读原因，而不是 500
    def boom(url: str, **kw):
        raise ValueError("经代理连不通外网：boom")

    monkeypatch.setattr(settings_routes, "probe_proxy", boom)
    status = client.get("/api/settings/proxy-status").json()
    assert status["configured"] is True and status["ok"] is False and "连不通" in status["error"]


def test_probe_proxy_connectivity_with_best_effort_exit_ip():
    """probe_proxy 本体：204 判连通；出口 IP 源挂掉只降级为 None，不影响结论。"""
    import httpx

    from llm_price_monitor.webapi.routes import settings as settings_routes

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.gstatic.com":
            return httpx.Response(204)
        raise httpx.ConnectError("no dns", request=request)  # ipify 不可达

    result = settings_routes.probe_proxy("http://127.0.0.1:1", transport=httpx.MockTransport(handler))
    assert result["ok"] is True and result["exit_ip"] is None and result["elapsed_ms"] >= 0


def test_settings_test_models_probes_pool_and_persists(workspace: Path, monkeypatch) -> None:
    """模型池批量体检：逐模型返回可用状态与报错原文，结果落库可回看；reset 开新轮。"""
    import llm_price_monitor.webapi.routes.settings as settings_routes

    client = _admin_client(workspace)

    # 未配置 Base URL 时给出可读的 400；空列表同理
    assert client.post("/api/settings/test-models", json={"models": ["m1"]}).status_code == 400
    assert client.put("/api/settings", json={
        "settings": {}, "ai": {"base_url": "https://saved.test/v1", "models": ["a"]},
    }).status_code == 200
    assert client.post("/api/settings/test-models", json={"models": []}).status_code == 400
    assert client.post("/api/settings/test-models", json={"models": [f"m{i}" for i in range(41)]}).status_code == 400

    seen: list[tuple[str, float | None]] = []

    def fake_ping(config, model, *, client=None, timeout=None):
        seen.append((model, timeout))
        if model == "bad-model":
            raise RuntimeError("boom")
        return "ok"

    monkeypatch.setattr(settings_routes, "ping_model", fake_ping)

    # 第一批 reset 开新轮：好模型带回复，坏模型带报错原文
    first = client.post("/api/settings/test-models", json={"models": ["good-a", "bad-model"], "reset": True}).json()
    assert [item["model"] for item in first["results"]] == ["good-a", "bad-model"]
    assert first["results"][0]["ok"] is True and first["results"][0]["reply"] == "ok"
    assert first["results"][1]["ok"] is False and "boom" in first["results"][1]["error"]
    # 用的是已保存配置 + 短超时（不拖住整轮）
    assert seen == [("good-a", settings_routes.PROBE_TIMEOUT_SECONDS), ("bad-model", settings_routes.PROBE_TIMEOUT_SECONDS)]

    # 第二批不 reset：结果合并进同一轮，落库可回看
    client.post("/api/settings/test-models", json={"models": ["good-b"]}).json()
    report = client.get("/api/settings/test-models").json()
    assert set(report["results"].keys()) == {"good-a", "bad-model", "good-b"}
    assert report["tested_at"] is not None

    # 再开一轮 reset：旧结果清空
    client.post("/api/settings/test-models", json={"models": ["fresh"], "reset": True}).json()
    report = client.get("/api/settings/test-models").json()
    assert set(report["results"].keys()) == {"fresh"}


def test_analytics_track_public_with_dedup_and_validation(workspace: Path):
    """访问埋点公开可写：记录 IP/UA 并解析设备；30 秒内同 IP 同路径去重；非法路径 400。"""
    # summary 为管理员接口，统一用已登录客户端发起；track 本身公开。
    # peer 模拟本机反代（回环）转发：只有回环直连才信任转发头，对应线上 Nginx→Next→FastAPI 链路
    client = _admin_client(workspace, peer=("127.0.0.1", 50000))
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    headers = {"x-forwarded-for": "203.0.113.7", "user-agent": ua}

    assert client.post("/api/analytics/track", json={"path": "/overview"}, headers=headers).json() == {"ok": True}
    assert client.post("/api/analytics/track", json={"path": "/overview"}, headers=headers).json() == {"ok": True}
    # 同 IP 不同路径不去重；去掉 XFF 头时回退到直连地址，视为另一个访客
    client.post("/api/analytics/track", json={"path": "/history"}, headers=headers)
    client.post("/api/analytics/track", json={"path": "/overview"})

    summary = client.get("/api/analytics/summary").json()
    assert summary["total_pv"] == 3 and summary["total_ip"] == 2
    assert summary["today_pv"] == 3
    assert {"name": "desktop", "pv": 1} in summary["devices"]
    assert {"name": "mobile", "pv": 2} in summary["devices"]
    assert {"name": "Safari", "pv": 2} in summary["browsers"]
    assert {"path": "/overview", "pv": 2, "uv": 2} in summary["top_paths"]

    for bad in {"path": "/api/overview"}, {"path": "https://evil.com"}, {"path": ""}:
        assert client.post("/api/analytics/track", json=bad, headers=headers).status_code == 400

    # 非回环直连的客户端不能借 XFF 伪造来源 IP：按直连地址记录
    outsider = TestClient(create_app(_config(workspace)))
    outsider.post("/api/analytics/track", json={"path": "/overview"}, headers={"x-forwarded-for": "198.51.100.9"})
    latest = client.get("/api/analytics/logs?limit=1").json()["visits"][0]
    assert latest["ip"] == "testclient"


def test_analytics_track_per_ip_rate_limit(workspace: Path):
    """单 IP 每分钟最多写入 TRACK_MAX_PER_MINUTE 条访问记录，超量静默丢弃（响应仍为 ok）。"""
    from llm_price_monitor.webapi.routes.analytics import TRACK_MAX_PER_MINUTE

    client = TestClient(create_app(_config(workspace)))
    for i in range(TRACK_MAX_PER_MINUTE + 5):
        assert client.post("/api/analytics/track", json={"path": f"/p{i}"}).status_code == 200
    with sqlite3.connect(workspace / "var" / "monitor.db") as conn:
        total = conn.execute("SELECT COUNT(*) FROM visit_logs").fetchone()[0]
    assert total == TRACK_MAX_PER_MINUTE


def test_analytics_admin_gate_summary_logs_clear(workspace: Path):
    """统计读取与清空仅管理员可用；明细按时间倒序，清空后归零。"""
    plain = TestClient(create_app(_config(workspace)))
    assert plain.post("/api/analytics/track", json={"path": "/overview"}).status_code == 200
    assert plain.get("/api/analytics/summary").status_code == 401
    assert plain.get("/api/analytics/logs").status_code == 401
    assert plain.post("/api/analytics/clear").status_code == 401

    admin = _admin_client(workspace)
    assert admin.post("/api/analytics/track", json={"path": "/overview"}) .status_code == 200
    assert admin.post("/api/analytics/track", json={"path": "/history"}) .status_code == 200
    logs = admin.get("/api/analytics/logs?limit=1").json()
    # plain 实例在门禁验证时已写入 1 条 /overview，加管理员的 2 条共 3 条
    assert logs["total"] == 3 and len(logs["visits"]) == 1
    row = logs["visits"][0]
    assert set(row) == {"ts", "path", "ip", "user_agent", "browser", "os", "device"}

    assert admin.post("/api/analytics/clear").json() == {"ok": True}
    assert admin.get("/api/analytics/summary").json()["total_pv"] == 0
    assert admin.get("/api/analytics/logs").json()["total"] == 0


def test_ai_logs_summary_endpoint(workspace: Path):
    """AI 调用统计：仅管理员可读；返回 KPI 与 7 天按天序列，空库时补零。"""
    from llm_price_monitor.store import Store

    plain = TestClient(create_app(_config(workspace)))
    assert plain.get("/api/ai-logs/summary").status_code == 401

    admin = _admin_client(workspace)
    store = Store(workspace / "var" / "monitor.db")
    store.add_ai_log(
        scene="助手问答", model="m1", status="ok", duration_ms=800,
        prompt_tokens=10, completion_tokens=20, total_tokens=30,
    )
    body = admin.get("/api/ai-logs/summary").json()
    assert body["total"] == 1 and body["ok"] == 1 and body["success_rate"] == 100.0
    assert (body["prompt_tokens"], body["completion_tokens"], body["total_tokens"]) == (10, 20, 30)
    assert len(body["daily"]) == 7 and body["daily"][-1]["ok"] == 1
    assert body["scenes"] == [{"name": "助手问答", "calls": 1}]


def test_ai_logs_endpoint_clamps_limit_and_offset(workspace: Path):
    """limit 负数按 1、offset 负数按 0：SQLite 的 LIMIT -1 是不限量，不能放行。"""
    from llm_price_monitor.store import Store

    admin = _admin_client(workspace)
    store = Store(workspace / "var" / "monitor.db")
    store.add_ai_log(scene="助手问答", model="m1", status="ok", duration_ms=100)
    body = admin.get("/api/ai-logs?limit=-1&offset=-5").json()
    assert body["total"] == 1 and len(body["logs"]) == 1


def test_catalog_tasks_are_mutually_exclusive():
    """目录刷新与单厂商源刷新都读-改-写同一份 catalog 文档，必须互相冲突（与采集组无关）。"""
    from llm_price_monitor.webapi import tasks

    tasks._RUNNING_KINDS.clear()
    try:
        tasks._RUNNING_KINDS["catalog-refresh"] = "task-1"
        assert tasks._conflict_of("vendor-source-refresh") == "task-1"
        assert tasks._conflict_of("collect-price") is None
        tasks._RUNNING_KINDS.clear()
        tasks._RUNNING_KINDS["vendor-source-refresh"] = "task-2"
        assert tasks._conflict_of("catalog-refresh") == "task-2"
    finally:
        tasks._RUNNING_KINDS.clear()


def test_collect_test_runs_parallel_to_global_collect():
    """单站测试采集不进采集互斥组：与全局采集双向不冲突；同类测试之间仍互斥。"""
    from llm_price_monitor.webapi import tasks

    tasks._RUNNING_KINDS.clear()
    try:
        tasks._RUNNING_KINDS["collect-price"] = "task-1"
        assert tasks._conflict_of("collect-test") is None
        tasks._RUNNING_KINDS.clear()
        tasks._RUNNING_KINDS["collect-test"] = "task-2"
        assert tasks._conflict_of("collect-price") is None
        assert tasks._conflict_of("collect-test") == "task-2"
    finally:
        tasks._RUNNING_KINDS.clear()


def test_merge_site_health_concurrent_no_lost_update(tmp_path: Path):
    """测试采集与全局采集并行写健康档案：合并读-改-写加锁后并发合并不丢站点条目。"""
    from llm_price_monitor.report import _merge_site_health
    from llm_price_monitor.store import Store

    store = Store(tmp_path / "monitor.db")
    barrier = threading.Barrier(2)

    def merge(site_id: str) -> None:
        barrier.wait(2)
        _merge_site_health(store, "price", {site_id: {"level": "warn", "message": "m", "time": 1.0}})

    threads = [threading.Thread(target=merge, args=(site_id,)) for site_id in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    doc = store.get_document("site_collect_health")
    assert set(doc) == {"a", "b"}


def test_store_purge_visits_keeps_recent(workspace: Path):
    """purge_visits 只删窗口之前的记录，供 90 天保留策略调用。"""
    from llm_price_monitor.store import Store

    store = Store(workspace / "var" / "monitor.db")
    store.add_visit(path="/old", ip="1.1.1.1", user_agent="", browser="其他", os="其他", device="desktop")
    old = __import__("time").time() - 100 * 86400
    with store._conn() as conn:
        conn.execute("UPDATE visit_logs SET ts = ? WHERE path = '/old'", (old,))
    store.add_visit(path="/new", ip="1.1.1.2", user_agent="", browser="其他", os="其他", device="desktop")

    assert store.purge_visits(old + 1) == 1
    paths = [row["path"] for row in store.list_visits()[0]]
    assert paths == ["/new"]


def test_collect_section_endpoints_submit_split_tasks(workspace: Path, monkeypatch):
    """价格/渠道状态/公告拆分端点各自提交独立任务，摘要与对应扫描结果一致。"""
    import llm_price_monitor.webapi.jobs as jobs
    from llm_price_monitor.report import MonitorReport, SectionScan

    # scan_prices 真实返回 MonitorReport（带逐站采集状态），这里按真实类型替换
    monkeypatch.setattr(jobs, "scan_prices", lambda _config, **_kw: MonitorReport(0.0, 1.0, records=[{"model": "demo-model"}]))
    monkeypatch.setattr(jobs, "scan_statuses", lambda _config, **_kw: SectionScan(records=[{"site_id": "demo"}], events=[{"kind": "status_init"}]))
    monkeypatch.setattr(jobs, "scan_notices", lambda _config, **_kw: SectionScan(records=[]))

    client = _admin_client(workspace)
    expected = {
        "/api/collect/price": "collect-price",
        "/api/collect/status": "collect-status",
        "/api/collect/notice": "collect-notice",
    }
    task_ids: dict[str, str] = {}
    for path in expected:
        task_ids[path] = client.post(path, json={"persist": True}).json()["task_id"]
    for path, kind in expected.items():
        for _ in range(50):
            task = client.get(f"/api/tasks/{task_ids[path]}").json()
            if task["status"] != "running":
                break
            time.sleep(0.02)
        assert task["kind"] == kind and task["status"] == "done", path
    price_task = client.get(f"/api/tasks/{task_ids['/api/collect/price']}").json()
    assert [row["model"] for row in price_task["result"]["records"]] == ["demo-model"]
    status_task = client.get(f"/api/tasks/{task_ids['/api/collect/status']}").json()
    assert status_task["result"]["events"] == ["status_init"]


def test_settings_validates_schedule(workspace: Path):
    """schedule 间隔必须是 >= 0 的数字；合法值保存后可读回。"""
    client = _admin_client(workspace)
    assert client.put("/api/settings", json={"settings": {"schedule": {"price": -5}}}).status_code == 400
    assert client.put("/api/settings", json={"settings": {"schedule": {"price": True}}}).status_code == 400
    ok = client.put("/api/settings", json={"settings": {"schedule": {"price": 30, "status": 0, "notice": 10, "catalog": 720, "rankings": 0}}})
    assert ok.status_code == 200
    assert ok.json()["settings"]["schedule"] == {"price": 30, "status": 0, "notice": 10, "catalog": 720, "rankings": 0}


def test_scheduler_run_due_submits_due_jobs_and_persists_state(tmp_path: Path, monkeypatch):
    """到期项提交任务并记入 schedule_state；未到期与关闭项跳过。"""
    import llm_price_monitor.webapi.jobs as jobs
    import llm_price_monitor.webapi.scheduler as scheduler_mod
    from llm_price_monitor.store import Store
    from llm_price_monitor.webapi import tasks

    tasks.reset()  # 任务注册表进程内共享，先清场再用精确计数断言
    monkeypatch.setattr(jobs, "fetch_catalogs", lambda **_kwargs: ({"models": {}}, {"models": {}}))
    store = Store(tmp_path / "monitor.db")
    store.set_document("settings", {"schedule": {"price": 0, "status": 0, "notice": 0, "catalog": 60, "rankings": 0}})

    scheduler_mod._run_due(store)
    kinds = [task["kind"] for task in tasks.recent(100)]
    assert kinds.count("catalog-refresh") == 1
    assert "collect-price" not in kinds  # 间隔 0 = 关闭该项定时
    assert store.get_document("schedule_state")["catalog"] > 0

    # 刚提交过、间隔未到：不重复提交
    scheduler_mod._run_due(store)
    assert sum(1 for task in tasks.recent(100) if task["kind"] == "catalog-refresh") == 1


def test_scheduler_run_due_disabled_all_and_invalid_config(tmp_path: Path):
    """全部间隔为 0 时不提交任何任务；非法 schedule 抛 ValueError（由调度线程兜底）。"""
    import llm_price_monitor.webapi.scheduler as scheduler_mod
    from llm_price_monitor.store import Store
    from llm_price_monitor.webapi import tasks

    tasks.reset()
    store = Store(tmp_path / "monitor.db")
    store.set_document("settings", {"schedule": {"price": 0, "status": 0, "notice": 0, "catalog": 0, "rankings": 0}})
    scheduler_mod._run_due(store)
    assert tasks.recent(100) == []

    store.set_document("settings", {"schedule": {"price": "每分钟"}})
    with pytest.raises(ValueError):
        scheduler_mod._run_due(store)


def test_reseed_modes(workspace: Path):
    """手动种子导入：skip_existing 只补缺失项，overwrite 覆盖同名项；空站点列表不清空站点。"""
    admin = _admin_client(workspace)
    seed_path = workspace / "config.json"  # _config 写入、create_app 记住的种子路径

    # skip_existing：启动时默认值已把 settings 全字段落库，种子声明值不再视为"缺失"；
    # 手动删掉 timeout 后才重新可补
    seed_path.write_text(json.dumps({
        "settings": {"timeout": 99, "schedule": {"price": 123, "status": 0, "notice": 0, "catalog": 0, "rankings": 0}},
        "ai": {"enabled": True},
        "sites": [],
    }), encoding="utf-8")
    result = admin.post("/api/seed", json={"mode": "skip_existing"}).json()
    assert result["settings_written"] == []
    assert admin.get("/api/settings").json()["settings"]["timeout"] == 20  # 保持启动默认值
    store = admin.app.state.store
    settings_doc = store.get_document("settings")
    del settings_doc["timeout"]
    store.set_document("settings", settings_doc)
    result = admin.post("/api/seed", json={"mode": "skip_existing"}).json()
    assert set(result["settings_written"]) == {"timeout"}
    assert admin.get("/api/settings").json()["settings"]["timeout"] == 99
    assert admin.get("/api/settings").json()["ai"]["enabled"] is False
    assert len(admin.get("/api/sites").json()["sites"]) == 1

    # overwrite：种子里的键逐项覆盖；sites 为空数组时受保护不清空
    seed_path.write_text(json.dumps({
        "settings": {"timeout": 20, "schedule": {"price": 30, "status": 5, "notice": 10, "catalog": 720, "rankings": 0}},
        "ai": {"enabled": True},
        "sites": [],
    }), encoding="utf-8")
    result = admin.post("/api/seed", json={"mode": "overwrite"}).json()
    assert set(result["settings_written"]) == {"timeout", "schedule"}
    assert result["ai_written"] == ["enabled"]
    assert result["sites_replaced"] is False
    settings = admin.get("/api/settings").json()["settings"]
    assert settings["timeout"] == 20 and settings["schedule"]["price"] == 30
    assert admin.get("/api/settings").json()["ai"]["enabled"] is True
    assert len(admin.get("/api/sites").json()["sites"]) == 1

    # overwrite 列出站点 → 整表替换；种子未声明的 settings 不动
    seed_path.write_text(json.dumps({
        "sites": [{"id": "other", "models": ["m"], "network": {"url": "https://x.test/api/pricing"}}],
    }), encoding="utf-8")
    result = admin.post("/api/seed", json={"mode": "overwrite"}).json()
    assert result["sites_replaced"] is True and result["sites_written"] == 1
    assert [site["id"] for site in admin.get("/api/sites").json()["sites"]] == ["other"]
    assert admin.get("/api/settings").json()["settings"]["timeout"] == 20

    # 非法种子 → 400 且不写一半；未知 mode → 422
    seed_path.write_text(json.dumps({"settings": {"schedule": {"price": -5}}}), encoding="utf-8")
    assert admin.post("/api/seed", json={"mode": "overwrite"}).status_code == 400
    assert admin.get("/api/settings").json()["settings"]["timeout"] == 20
    assert admin.post("/api/seed", json={"mode": "nope"}).status_code == 422


def test_reseed_missing_seed_file(workspace: Path):
    """种子文件不存在时手动导入返回 400，提示路径。"""
    admin = _admin_client(workspace)
    (workspace / "config.json").unlink()
    response = admin.post("/api/seed", json={"mode": "skip_existing"})
    assert response.status_code == 400 and "种子文件不存在" in response.json()["detail"]


def test_default_seed_ships_ai_without_key(workspace: Path):
    """仓库自带种子：预置了 AI base_url 与模型列表、不含任何密钥；空库首次启动即写入。"""
    seed = Path(__file__).resolve().parent.parent / "config" / "default-seed.json"
    client = TestClient(create_app(seed))
    assert client.post("/api/setup", json={"username": "admin", "password": "s3cret"}).status_code == 200
    data = client.get("/api/settings").json()
    assert isinstance(data["ai"].get("base_url"), str) and data["ai"]["base_url"]
    assert isinstance(data["ai"].get("models"), list) and len(data["ai"]["models"]) >= 3
    assert "api_key" not in data["ai"]  # 种子不含密钥，Key 由向导/设置页填写后存数据库
    assert data["settings"]["schedule"]["price"] == 180
    assert data["settings"]["schedule"]["catalog"] == 1440 and data["settings"]["schedule"]["rankings"] == 1440


def test_settings_defaults_written_to_db(workspace: Path):
    """启动时把 settings 里缺失的字段按默认值落库：设置页每项都有数可显，老库升级也能补齐。"""
    from llm_price_monitor.config import MonitorSettings

    client = _admin_client(workspace)
    # 库里只存过一个键：启动后其余字段全部补上默认值
    client.app.state.store.set_document("settings", {"timeout": 30})
    from llm_price_monitor.webapi.app import _ensure_settings_defaults
    _ensure_settings_defaults(client.app.state.store)

    settings = client.app.state.store.get_document("settings")
    for key in MonitorSettings.__dataclass_fields__:
        assert key in settings, f"settings 缺字段 {key}"
    assert settings["timeout"] == 30  # 已有值不被覆盖
    assert settings["assistant_hourly_limit"] == 10
    assert settings["retention_ai_log_days"] == 7
    assert settings["random_user_agent"] is False
    assert settings["user_agent_platforms"] == []
    assert settings["wxpusher_app_token"] is None


def test_first_startup_seeds_full_settings_defaults(workspace: Path):
    """空库首次启动：种子文件的 settings 连同缺失默认值一起写入，assistant_hourly_limit 等新键可直接读出。"""
    seed_path = workspace / "config.json"
    seed_path.write_text(json.dumps({"settings": {}, "ai": {"enabled": False}, "sites": []}), encoding="utf-8")
    client = TestClient(create_app(seed_path))
    assert client.post("/api/setup", json={"username": "admin", "password": "s3cret"}).status_code == 200
    settings = client.get("/api/settings").json()["settings"]
    assert settings["assistant_hourly_limit"] == 10
    assert settings["assistant_daily_limit"] == 10
    assert settings["retention_ai_log_days"] == 7


def test_collect_task_logs_in_detail_and_list_summary(workspace: Path, monkeypatch):
    """采集过程日志随详情接口下发；列表只带 log_count 不带 logs。"""
    import llm_price_monitor.webapi.routes.collect as collect_routes
    from llm_price_monitor import tasklog
    from llm_price_monitor.report import MonitorReport

    def fake_run_once(_config, **_kwargs):
        tasklog.emit("[demo] 价格采集成功：3 条价格，0 处变化，0.4s")
        tasklog.emit("[demo] 价格采集失败：连接超时", "error")
        return MonitorReport(0.0, 1.0, [], [], [])

    monkeypatch.setattr(collect_routes, "run_once", fake_run_once)
    client = _admin_client(workspace)
    task_id = client.post("/api/collect", json={}).json()["task_id"]
    for _ in range(50):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] != "running":
            break
        time.sleep(0.02)
    assert task["status"] == "done"
    messages = [log["message"] for log in task["logs"]]
    assert any("价格采集成功" in message for message in messages)
    assert any("价格采集失败" in message for message in messages)
    assert sum(1 for log in task["logs"] if log["level"] == "error") >= 1
    assert all("任务失败" not in message for message in messages)  # 正常完成不产生失败日志
    listed = client.get("/api/tasks").json()["tasks"]
    row = next(item for item in listed if item["id"] == task_id)
    assert "logs" not in row and row["log_count"] == len(task["logs"])


def test_collect_failure_writes_error_log(workspace: Path, monkeypatch):
    """任务体抛异常时任务失败，且失败原因写进日志行。"""
    import llm_price_monitor.webapi.routes.collect as collect_routes

    def boom(_config, **_kwargs):
        raise RuntimeError("网络不可达")

    monkeypatch.setattr(collect_routes, "run_once", boom)
    client = _admin_client(workspace)
    task_id = client.post("/api/collect", json={}).json()["task_id"]
    for _ in range(50):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] != "running":
            break
        time.sleep(0.02)
    assert task["status"] == "failed" and "网络不可达" in task["error"]
    assert any("网络不可达" in log["message"] and log["level"] == "error" for log in task["logs"])


def test_task_logs_persist_and_restore_after_restart(tmp_path: Path):
    """任务记录连同日志持久化；重启加载后运行中的任务标记为失败。"""
    from llm_price_monitor.store import Store
    from llm_price_monitor.webapi import tasks

    tasks.reset()
    db = tmp_path / "monitor.db"
    tasks.attach_store(Store(db))
    release = threading.Event()
    started = threading.Event()

    def job() -> dict:
        started.set()
        release.wait(2)
        return {}

    task_id = tasks.submit("collect", job)
    assert started.wait(2)
    assert tasks.get(task_id)["status"] == "running"

    tasks.reset()  # 模拟重启：内存清空，持久化文档保留
    tasks.attach_store(Store(db))
    restored = tasks.get(task_id)
    assert restored is not None
    assert restored["status"] == "failed" and restored["error"] == "服务重启，任务中断"
    release.set()


def test_tasklog_emit_without_binding_is_noop():
    """CLI 直跑采集函数时没有绑定日志出口，emit 静默跳过。"""
    from llm_price_monitor import tasklog

    tasklog.emit("无任务上下文的日志应被忽略")
    tasklog.emit("错误也应被忽略", "error")


def test_submit_conflict_error_names_blocking_task(tmp_path: Path):
    """互斥报错要报占用方任务的 kind 与开始时间：占用方和请求方是两个任务，
    只报请求方 kind 会让用户误以为是上一次同类操作没结束。"""
    from llm_price_monitor.store import Store
    from llm_price_monitor.webapi import tasks

    tasks.reset()
    tasks.attach_store(Store(tmp_path / "monitor.db"))
    release = threading.Event()
    started = threading.Event()

    def job() -> dict:
        started.set()
        release.wait(2)
        return {}

    first = tasks.submit("collect", job)
    assert started.wait(2)
    with pytest.raises(RuntimeError) as exc_info:
        tasks.submit("collect-price", lambda: {})
    message = str(exc_info.value)
    started_at = time.strftime("%m-%d %H:%M", time.localtime(tasks.get(first)["started_at"]))
    assert "collect 任务" in message and started_at in message
    # 请求方的 kind 不能再出现在报错里，否则又会误导为"上一次价格采集没结束"
    assert "collect-price" not in message
    release.set()


def test_task_error_stream_dismiss_and_clear(workspace: Path, monkeypatch):
    """概览页异常卡片：跨任务汇总警告/错误日志；逐条移除与清空只影响卡片展示，任务日志本体不动。"""
    import llm_price_monitor.webapi.routes.collect as collect_routes
    from llm_price_monitor import tasklog
    from llm_price_monitor.report import MonitorReport
    from llm_price_monitor.webapi import tasks

    def flaky(_config, **_kwargs):
        tasklog.emit("[demo] 渠道状态被跳过：站点未开放", "warn")
        tasklog.emit(
            "传输层抖动（[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error (_ssl.c:1032)），1s 后重试（1/2）",
            "warn",
        )
        tasklog.emit("[demo] 价格采集失败：连接超时", "error")
        return MonitorReport(0.0, 1.0, [], [], [])

    monkeypatch.setattr(collect_routes, "run_once", flaky)
    tasks.reset()  # 任务注册表进程内共享，先清场再用精确条数断言
    assert TestClient(create_app(_config(workspace))).get("/api/tasks/errors").status_code == 401  # 未登录不可见

    client = _admin_client(workspace)

    def run_collect() -> str:
        task_id = client.post("/api/collect", json={}).json()["task_id"]
        for _ in range(50):
            if client.get(f"/api/tasks/{task_id}").json()["status"] != "running":
                break
            time.sleep(0.02)
        return task_id

    run_collect()
    entries = client.get("/api/tasks/errors").json()["entries"]
    # 传输层抖动的 warn 已自愈，与健康档案同口径不进卡片；业务 warn 与 error 照常
    assert [entry["level"] for entry in entries] == ["error", "warn"]  # 新日志在前，info 不进卡片
    assert all("价格采集失败" in entry["message"] or "渠道状态被跳过" in entry["message"] for entry in entries)

    warn_key = next(entry["key"] for entry in entries if entry["level"] == "warn")
    assert client.delete(f"/api/tasks/errors/{warn_key}").status_code == 200
    remaining = client.get("/api/tasks/errors").json()["entries"]
    assert [entry["level"] for entry in remaining] == ["error"]
    detail = client.get(f"/api/tasks/{next(iter(client.get('/api/tasks').json()['tasks']))['id']}").json()
    assert any("渠道状态被跳过" in log["message"] for log in detail["logs"])  # 任务日志本体不受影响
    assert any("传输层抖动" in log["message"] for log in detail["logs"])  # 抖动只在任务日志里留痕

    assert client.delete("/api/tasks/errors").json()["remaining"] == 0
    assert client.get("/api/tasks/errors").json()["entries"] == []
    run_collect()  # 清空后旧日志被时间点挡住，新产生的仍然可见
    entries_after = client.get("/api/tasks/errors").json()["entries"]
    assert [entry["level"] for entry in entries_after] == ["error", "warn"]


def test_site_geo_endpoint_mocked(workspace: Path, monkeypatch):
    """站点 IP 定位端点：定位结果原样透传，网络定位函数被 mock，不真正联网。"""
    import llm_price_monitor.webapi.routes.geo as geo_route

    monkeypatch.setattr(
        geo_route,
        "resolve_site_geo",
        lambda configs: {"demo": {"ip": "1.2.3.4", "lat": 35.0, "lon": 110.0, "country": "中国", "city": "测试市"}},
    )
    client = TestClient(create_app(_config(workspace)))
    body = client.get("/api/geo").json()
    assert body["geo"]["demo"]["lat"] == 35.0 and body["geo"]["demo"]["country"] == "中国"


def test_site_token_refresh_sample_analyzed_on_save(workspace: Path, monkeypatch):
    """保存站点时响应案例交给 AI 分析字段路径：成功写入路径，失败降级保存；案例本身不落库。"""
    import llm_price_monitor.webapi.routes.sites as sites_routes

    client = _admin_client(workspace)
    demo = client.get("/api/sites").json()["sites"][0]
    config = {
        **demo,
        "id": "rt",
        "token_refresh": {
            "url": "https://rt.test/auth/refresh",
            "refresh_token": "rt_x",
            "response_sample": '{"data": {"access_token": "at", "refresh_token": "rt2"}}',
        },
    }

    monkeypatch.setattr(
        sites_routes, "infer_token_fields",
        lambda _ai, _sample: {"access_token_field": "data.access_token", "refresh_token_field": "data.refresh_token"},
    )
    saved = client.post("/api/sites", json={"config": config})
    assert saved.status_code == 200 and "warning" not in saved.json()
    stored = {site["id"]: site for site in client.get("/api/sites").json()["sites"]}["rt"]
    assert stored["token_refresh"]["access_token_field"] == "data.access_token"
    assert "response_sample" not in stored["token_refresh"]

    monkeypatch.setattr(
        sites_routes, "infer_token_fields",
        lambda _ai, _sample: (_ for _ in ()).throw(RuntimeError("AI 挂了")),
    )
    updated = client.put("/api/sites/rt", json={"config": {**stored, "token_refresh": {**stored["token_refresh"], "response_sample": "{}"}}})
    assert updated.status_code == 200 and "warning" in updated.json()
    reloaded = {site["id"]: site for site in client.get("/api/sites").json()["sites"]}["rt"]
    assert "access_token_field" not in reloaded["token_refresh"]
    assert "response_sample" not in reloaded["token_refresh"]


def test_persist_refresh_updates_hardcoded_auth_header(workspace: Path):
    """续签落盘时，写死在 network/networks/status/notice headers 里的 Authorization 要换成新 token（保留前缀）；Cookie 不动，手动维护。"""
    from llm_price_monitor.store import Store
    from llm_price_monitor.token_refresh import persist_refreshed_config

    store = Store(workspace / "persist-refresh.db")
    store.upsert_site(
        "hard",
        {
            "id": "hard",
            "models": ["m1"],
            "network": {
                "url": "https://hard.test/api/pricing",
                "headers": {"Authorization": "Bearer old_access", "referer": "https://hard.test"},
                "ratio_url": {
                    "url": "https://hard.test/api/ratio",
                    "headers": {"Authorization": "Bearer old_ratio", "x-ratio": "1"},
                },
            },
            "networks": [
                {"url": "https://hard.test/api/alt", "headers": {"authorization": "Bearer old_alt"}},
                {"url": "https://hard.test/api/cookie", "headers": {"Cookie": "session=old_cookie"}},
            ],
            "status": {"url": "https://hard.test/api/monitor", "headers": {"Authorization": "Bearer old_status"}},
            "notice": {"url": "https://hard.test/api/notice", "headers": {"authorization": "Bearer old_notice"}},
        },
    )
    persist_refreshed_config(store, "hard", "new_access", "new_refresh")
    config = store.get_site_config("hard")
    assert config["auth_token"] == "new_access"
    assert config["network"]["headers"]["Authorization"] == "Bearer new_access"
    assert config["network"]["headers"]["referer"] == "https://hard.test"
    assert config["network"]["ratio_url"]["headers"]["Authorization"] == "Bearer new_access"
    assert config["network"]["ratio_url"]["headers"]["x-ratio"] == "1"
    assert config["networks"][0]["headers"]["authorization"] == "Bearer new_access"
    assert config["networks"][1]["headers"]["Cookie"] == "session=old_cookie"
    assert config["status"]["headers"]["Authorization"] == "Bearer new_access"
    assert config["notice"]["headers"]["authorization"] == "Bearer new_access"
    assert config["token_refresh"]["refresh_token"] == "new_refresh"


def test_site_auth_inject_round_trips_through_api(workspace: Path):
    """凭证注入走完整保存链路：前端写的 auth_inject 结构能被校验、落库、原样读回并驱动注入。"""
    from llm_price_monitor.adapters import auth_inject_headers
    from llm_price_monitor.config import sites_from_raw

    client = _admin_client(workspace)
    demo = client.get("/api/sites").json()["sites"][0]
    config = {
        **demo,
        "id": "inject",
        "auth_token": "at_now",
        "token_refresh": {"url": "https://inject.test/auth/refresh", "refresh_token": "rt_now"},
        # 前端「认证与续签 → 凭证注入」三行写出来的形状
        "auth_inject": {
            "price": {"header": "Authorization", "value": "Bearer ${access_token}"},
            "status": {"header": "cookie", "value": "new_api_refresh=${refresh_token}"},
            "notice": {"header": "Authorization", "value": "Bearer ${access_token}"},
        },
    }
    assert client.post("/api/sites", json={"config": config}).status_code == 200
    stored = next(site for site in client.get("/api/sites").json()["sites"] if site["id"] == "inject")
    assert stored["auth_inject"]["status"] == {"header": "cookie", "value": "new_api_refresh=${refresh_token}"}

    (spec,) = sites_from_raw([stored])
    assert auth_inject_headers(spec, "price") == {"Authorization": "Bearer at_now"}
    assert auth_inject_headers(spec, "status") == {"cookie": "new_api_refresh=rt_now"}

    # 结构不对的规则被拦下（不写库）
    bad = {**config, "auth_inject": {"price": {"header": "Authorization"}}}
    failed = client.post("/api/sites", json={"config": bad})
    assert failed.status_code == 400 and "auth_inject.price.value" in failed.json()["detail"]

    # 空 auth_inject 保存时瘦身掉，不落库
    slimmed = client.put("/api/sites/inject", json={"config": {**stored, "auth_inject": {}}})
    assert slimmed.status_code == 200
    reloaded = next(site for site in client.get("/api/sites").json()["sites"] if site["id"] == "inject")
    assert "auth_inject" not in reloaded


def test_site_config_slimmed_on_save(workspace: Path):
    """保存时去掉空值与默认值字段：null / 空容器 / 默认 auth_header 等不落库，读取时由 SiteSpec 默认值兜底；非默认值原样保留。enabled 例外：始终落库，管理台行内启用判断直接读它。"""
    client = _admin_client(workspace)
    fat = {
        "id": "slim",
        "adapter": "standard",
        "auth_token": None,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "cookie": None,
        "cookies": {},
        "request_headers": {},
        "enabled": True,
        "network": {"url": "https://slim.test/api/pricing", "params": {}, "headers": {}},
    }
    created = client.post("/api/sites", json={"config": fat})
    assert created.status_code == 200
    assert created.json()["site"] == {
        "id": "slim",
        "enabled": True,  # 启用态始终落库：管理台行内判断直接读它
        "network": {"url": "https://slim.test/api/pricing"},
    }

    updated = client.put("/api/sites/slim", json={"config": {**fat, "auth_header": "X-Token", "enabled": False}})
    assert updated.status_code == 200
    site = next(s for s in client.get("/api/sites").json()["sites"] if s["id"] == "slim")
    assert site["auth_header"] == "X-Token"
    assert site["enabled"] is False
    assert "auth_prefix" not in site and "auth_token" not in site


def test_token_refresh_test_endpoint(workspace: Path, monkeypatch):
    """测试续签端点：成功返回完整新 token 与换新标记，失败转 400 可读提示，未填地址 400。"""
    import llm_price_monitor.webapi.routes.sites as sites_routes
    from llm_price_monitor.tracker import PriceRecord  # noqa: F401 - 仅确认模块可用

    client = _admin_client(workspace)
    demo = client.get("/api/sites").json()["sites"][0]
    config = {**demo, "id": "rt", "token_refresh": {"url": "https://rt.test/auth/refresh", "refresh_token": "rt_old"}}

    monkeypatch.setattr(sites_routes, "refresh_site_token", lambda spec, http, timeout, ua: ("at_1234567890abcd", "rt_old"))
    ok = client.post("/api/sites/test-token-refresh", json={"config": config})
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True and body["access_token"] == "at_1234567890abcd" and body["refresh_token"] == "rt_old" and body["refresh_token_rotated"] is False

    def _boom(*_args):
        raise RuntimeError("HTTP 401")

    monkeypatch.setattr(sites_routes, "refresh_site_token", _boom)
    failed = client.post("/api/sites/test-token-refresh", json={"config": config})
    assert failed.status_code == 400 and "续签测试失败" in failed.json()["detail"]

    missing = client.post("/api/sites/test-token-refresh", json={"config": {"id": "rt"}})
    assert missing.status_code == 400


def test_token_refresh_test_endpoint_persists_refreshed_tokens(workspace: Path, monkeypatch):
    """轮换型凭据测试一次就作废旧值：换新的 refresh_token 要立即落库，编辑中途取消也不丢凭证链；
    access_token 一并落库，取消后站点上的采集请求头也还是刚验过有效的那个。"""
    import llm_price_monitor.webapi.routes.sites as sites_routes

    client = _admin_client(workspace)
    demo = client.get("/api/sites").json()["sites"][0]
    stored = {
        **demo,
        "id": "rotate",
        "auth_token": "at_old",
        "token_refresh": {"url": "https://rotate.test/auth/refresh", "refresh_token": "rt_old"},
    }
    assert client.post("/api/sites", json={"config": stored}).status_code == 200

    monkeypatch.setattr(sites_routes, "refresh_site_token", lambda spec, http, timeout, ua: ("at_rotated0000", "rt_new"))
    ok = client.post("/api/sites/test-token-refresh", json={"config": stored})
    assert ok.status_code == 200 and ok.json()["refresh_token_rotated"] is True
    persisted = client.get("/api/sites").json()["sites"]
    rotate = next(site for site in persisted if site["id"] == "rotate")
    assert rotate["token_refresh"]["refresh_token"] == "rt_new"
    assert rotate["auth_token"] == "at_rotated0000"

    # 未换新的站点：access_token 照样落库，其余字段保持不动
    monkeypatch.setattr(sites_routes, "refresh_site_token", lambda spec, http, timeout, ua: ("at_x", "rt_new"))
    client.post("/api/sites/test-token-refresh", json={"config": {**stored, "token_refresh": {**stored["token_refresh"], "refresh_token": "rt_new"}}})
    rotate = next(site for site in client.get("/api/sites").json()["sites"] if site["id"] == "rotate")
    assert rotate["token_refresh"]["refresh_token"] == "rt_new"
    assert rotate["auth_token"] == "at_x"


def test_task_logs_roll_oldest_when_full():
    """日志满上限后滚动保留最新，而不是丢弃新日志。"""
    from llm_price_monitor.webapi import tasks as tasks_mod

    tasks_mod.reset()
    max_lines = tasks_mod.DEFAULT_MAX_LOG_LINES
    tasks_mod._tasks["t1"] = {"id": "t1", "logs": [{"time": 0, "message": f"old-{i}", "level": "info"} for i in range(max_lines)]}
    for i in range(3):
        tasks_mod._append_log("t1", f"new-{i}", "info")
    logs = tasks_mod._tasks["t1"]["logs"]
    assert len(logs) == max_lines
    assert logs[-1]["message"] == "new-2"
    assert any("滚动覆盖" in log["message"] and log["level"] == "warn" for log in logs)
    tasks_mod.reset()


def test_assistant_daily_ip_limit(workspace: Path, monkeypatch):
    """AI 助手按 IP 每日限次：发出去就计数（含被 AI 拒答），超限 429；0 表示不限制。"""
    import llm_price_monitor.webapi.routes.assistant as assistant_routes

    class _FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "答"}}]}

    def fake_events(*_args: object, **_kwargs: object):
        yield {"type": "delta", "text": "答"}
        yield {"type": "finish", "finish_reason": "stop", "tool_calls": [], "usage": None}

    class _FakeGateClient:
        def post(self, *args: object, **kwargs: object) -> _FakeResponse:
            return _FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr(assistant_routes.httpx, "Client", lambda *args, **kwargs: _FakeGateClient())
    monkeypatch.setattr(assistant_routes, "ai_stream_messages_fallback", fake_events)
    config_path = _config(workspace)
    doc = json.loads(config_path.read_text(encoding="utf-8"))
    doc["ai"] = {"enabled": True, "base_url": "https://ai.test/v1", "models": ["m-a"], "api_key": "sk-x"}
    config_path.write_text(json.dumps(doc), encoding="utf-8")
    client = TestClient(create_app(config_path))
    client.app.state.store.set_document("settings", {"assistant_daily_limit": 2})

    def ask(**kwargs) -> object:
        return client.post("/api/assistant/ask", json={"question": "demo-model 现在多少钱？"}, **kwargs)

    assert ask().status_code == 200
    assert ask().status_code == 200
    limited = ask()
    assert limited.status_code == 429 and "明天" in limited.json()["detail"]

    # 限额按 IP 独立：回环对端才信任转发头（见 deps.client_ip），换一个真实 IP 不受影响
    loopback = TestClient(create_app(config_path), client=("127.0.0.1", 50000))
    assert loopback.post(
        "/api/assistant/ask",
        json={"question": "demo-model 现在多少钱？"},
        headers={"x-forwarded-for": "198.51.100.9"},
    ).status_code == 200

    # 0 = 不限制
    client.app.state.store.set_document("settings", {"assistant_daily_limit": 0})
    assert ask().status_code == 200


# ---------- 厂商定价源 ----------


def _fake_catalog_doc() -> dict:
    return {
        "generated_at": time.time(), "generated_at_iso": "2026-09-18T00:00:00+0800",
        "usd_cny_rate": 7.0, "rate_source": "test", "source": "models.dev", "models": {}, "providers": [],
    }


def _wait_task(client: TestClient, task_id: str, attempts: int = 200) -> dict:
    detail: dict = {}
    for _ in range(attempts):
        detail = client.get(f"/api/tasks/{task_id}").json()
        if detail.get("status") != "running":
            break
        time.sleep(0.05)
    return detail


def test_vendor_sources_crud_requires_admin_and_validates(workspace: Path, monkeypatch):
    from llm_price_monitor.webapi import jobs as web_jobs
    from llm_price_monitor.webapi import tasks

    # 目录刷新被自动触发（停用/删除后恢复 models.dev 基准），替身避免真实网络
    monkeypatch.setattr(web_jobs, "fetch_catalogs", lambda *a, **k: (_fake_catalog_doc(), _fake_catalog_doc()))
    # 等上一个测试留下的任务线程退出，否则停用/删除的自动恢复提交会被互斥拒绝
    for _ in range(200):
        if not any(t["status"] == "running" for t in tasks.recent(100)):
            break
        time.sleep(0.05)
    client = _admin_client(workspace)

    anon = TestClient(create_app(_config(workspace)))
    assert anon.get("/api/vendor-sources").status_code == 401
    assert anon.post("/api/vendor-sources", json={"vendor": "X", "url": "https://x.cn/p"}).status_code == 401

    assert client.post("/api/vendor-sources", json={"vendor": "", "url": "https://x.cn"}).status_code == 400
    assert client.post("/api/vendor-sources", json={"vendor": "智谱", "url": "ftp://x.cn"}).status_code == 400
    created = client.post("/api/vendor-sources", json={
        "vendor": "ZhipuAI", "url": "https://docs.bigmodel.cn/cn/guide/start/pricing.md"}).json()["source"]
    assert created["url"].endswith("pricing.md") and created["enabled"] is True and created["region"] == "cn"
    assert client.post("/api/vendor-sources", json={"vendor": "ZhipuAI", "url": "https://x.cn/p"}).status_code == 409
    assert client.post("/api/vendor-sources", json={
        "vendor": "Y", "url": "https://y.cn/p", "region": "eu"}).status_code == 400  # 非法 region 拒绝

    # PUT：停用触发目录刷新任务恢复基准
    updated = client.put("/api/vendor-sources/ZhipuAI", json={
        "vendor": "ZhipuAI", "url": "https://docs.bigmodel.cn/cn/guide/start/pricing.md", "enabled": False}).json()
    assert updated["source"]["enabled"] is False
    assert client.put("/api/vendor-sources/ZhipuAI", json={
        "vendor": "Other", "url": "https://x.cn/p"}).status_code == 400
    assert client.put("/api/vendor-sources/Nope", json={
        "vendor": "Nope", "url": "https://x.cn/p"}).status_code == 404
    if updated.get("revert_task_id"):
        _wait_task(client, updated["revert_task_id"])

    deleted = client.delete("/api/vendor-sources/ZhipuAI").json()
    assert deleted["deleted"] == "ZhipuAI"
    if deleted.get("revert_task_id"):
        _wait_task(client, deleted["revert_task_id"])
    assert client.delete("/api/vendor-sources/ZhipuAI").status_code == 404
    assert client.get("/api/vendor-sources").json() == {"sources": []}


def test_vendor_sources_detection_endpoint(workspace: Path):
    client = _admin_client(workspace)
    # 厂商清单未生成：提示先刷新目录
    assert client.get("/api/vendor-sources/detection").status_code == 404
    store = client.app.state.store
    store.set_document("catalog_all", {"usd_cny_rate": 7.0, "providers": [
        {"id": "zhipuai", "name": "Zhipu AI", "doc": "https://docs.z.ai", "models_total": 15, "models_priced": 15},
        {"id": "alibaba-cn", "name": "Alibaba (China)", "doc": "https://alibabacloud.com", "models_total": 89, "models_priced": 80},
    ]})
    records = {r["vendor"]: r for r in client.get("/api/vendor-sources/detection").json()["records"]}
    assert records["Zhipu AI"]["verdict"] == "missing_cn" and records["Zhipu AI"]["suggestions"]
    assert records["Alibaba Cloud"]["verdict"] == "has_cn"


def test_vendor_source_refresh_task_merges_into_catalog(workspace: Path, monkeypatch):
    from llm_price_monitor.catalog import vendor_sources as vs_mod

    client = _admin_client(workspace)
    assert client.post("/api/vendor-sources/Nope/refresh").status_code == 404
    client.post("/api/vendor-sources", json={
        "vendor": "ZhipuAI", "url": "https://docs.bigmodel.cn/cn/guide/start/pricing.md"})

    def fake_fetch(url: str, **kwargs):
        return {"url": url, "final_url": url, "method": "static-md",
                "models": [{"model": "GLM-5.3-Flash", "input_price": 0.8, "output_price": 2.8,
                            "cache_read_price": 0.23, "currency": "CNY"}],
                "warnings": []}

    monkeypatch.setattr(vs_mod, "fetch_page_prices", fake_fetch)
    task_id = client.post("/api/vendor-sources/ZhipuAI/refresh").json()["task_id"]
    detail = _wait_task(client, task_id)
    assert detail["status"] == "done" and detail["result"]["model_count"] == 1

    # 源记录带抓取结果；国内价合并进官方目录（新条目 vendor 用源厂商名）
    record = client.get("/api/vendor-sources/ZhipuAI").json()
    assert record["last_status"] == "ok" and record["model_count"] == 1
    listing = client.get("/api/vendor-sources").json()["sources"][0]
    assert "models" not in listing  # 列表摘要不含模型明细
    catalog = client.get("/api/catalog").json()
    entry = catalog["models"]["glm5.3flash"]
    assert entry["region"] == "cn" and entry["vendor"] == "ZhipuAI"
    assert entry["list_cny"] == {"input": 0.8, "output": 2.8}


def test_monitor_model_removals_tracked_on_save(workspace: Path):
    """保存监控清单时：移除项进 dismissed（自动补模型时跳过），重新加回则解除。"""
    client = _admin_client(workspace)
    # workspace 初始清单是 ["demo-model"]，第一次保存就把它的移除记进 dismissed
    assert client.put("/api/settings", json={"settings": {"monitor_models": ["m-a", "m-b"]}}).status_code == 200
    assert client.put("/api/settings", json={"settings": {"monitor_models": ["m-a"]}}).status_code == 200
    settings = client.get("/api/settings").json()["settings"]
    assert settings["monitor_models"] == ["m-a"]
    assert settings["monitor_models_dismissed"] == ["demo-model", "m-b"]

    # 重新加回（含曾被移除的 demo-model）即解除忽略
    assert client.put("/api/settings", json={"settings": {"monitor_models": ["m-a", "m-b", "demo-model"]}}).status_code == 200
    settings = client.get("/api/settings").json()["settings"]
    assert settings["monitor_models_dismissed"] == []


def test_auto_append_latest_models(tmp_path: Path):
    from llm_price_monitor.store import Store
    from llm_price_monitor.webapi.jobs import auto_append_latest_models

    store = Store(tmp_path / "monitor.db")
    store.set_document("settings", {"monitor_models": ["m-old"], "monitor_models_dismissed": ["m-gone"]})
    # 目录键是匹配用归一化形式，条目的 model 字段才是标准显示名：自动补必须取显示名
    catalog = {"models": {
        # 白名单厂商（models.dev 官方 lab）：按发布日期取最新一批，同日并列全收
        "aold": {"vendor": "OpenAI", "release_date": "2026-08-01", "model": "m-old"},
        "anew": {"vendor": "OpenAI", "release_date": "2026-09-30", "model": "m-new"},
        "anew2": {"vendor": "OpenAI", "release_date": "2026-09-30", "model": "m-new-2"},
        # 同日发布的特殊领域模型不进清单：音乐生成（音频输出）、生图（图像输出）、
        # 纯音频输入的 ASR、与对话模型同标注的 embedding、全模态生成系列（h3 段）
        "alyric": {"vendor": "OpenAI", "release_date": "2026-09-30", "model": "m-lyric",
                   "modalities": {"input": ["text", "image"], "output": ["text", "audio"]}},
        "aimage": {"vendor": "OpenAI", "release_date": "2026-09-30", "model": "m-image",
                   "modalities": {"input": ["text", "image"], "output": ["image"]}},
        "aasr": {"vendor": "OpenAI", "release_date": "2026-09-30", "model": "m-asr",
                 "modalities": {"input": ["audio"], "output": ["text"]}},
        "aembed": {"vendor": "OpenAI", "release_date": "2026-09-30", "model": "m-embedding",
                   "modalities": {"input": ["text"], "output": ["text"]}},
        "ah3": {"vendor": "OpenAI", "release_date": "2026-09-30", "model": "m-h3"},
        "aolder": {"vendor": "OpenAI", "release_date": "2026-07-01", "model": "m-older"},
        "bold": {"vendor": "Anthropic", "release_date": "2025-01-01", "model": "m-gone"},
        "cmonth": {"vendor": "Google", "release_date": "2026-09", "model": "m-c"},
        # 国内定价源厂商（非白名单）：条目无发布日期，按目录插入序每家取前 N 滚动；
        # 名字黑名单挡特殊领域（vl 视觉/翻译专用），黑名单条目不占前 N 名额
        "d1": {"vendor": "DeepSeek", "model": "d-first"},
        "dvl": {"vendor": "DeepSeek", "model": "d-vl"},
        "d2": {"vendor": "DeepSeek", "model": "d-second"},
        "d3": {"vendor": "DeepSeek", "model": "d-third"},
        "d4": {"vendor": "DeepSeek", "model": "d-fourth"},
        "d5": {"vendor": "DeepSeek", "model": "d-fifth"},
        "b1": {"vendor": "Baidu", "model": "b-first"},
    }}

    # 白名单线：OpenAI 家 9-30 两个通用模型都进、特殊领域同日发布不进、旧日期不进；
    # Anthropic 家最新是被移除的 m-gone，跳过；Google 家月粒度日期照常参与。
    # 国内线：DeepSeek 取插入序前 4（vl 被名字黑名单挡掉且不占名额，d-fifth 排第 5 滚出）、
    # Baidu 取 1。清单落库按厂商分组排序：OpenAI 在前（发布倒序），Google、DeepSeek（目录序）、
    # Baidu 依次随后。
    assert auto_append_latest_models(store, catalog) == [
        "b-first", "d-first", "d-fourth", "d-second", "d-third", "m-c", "m-new", "m-new-2",
    ]
    assert store.get_document("settings")["monitor_models"] == [
        "m-new", "m-new-2", "m-old", "m-c", "d-first", "d-second", "d-third", "d-fourth", "b-first",
    ]
    # 下一轮：没有新模型就不再写库
    assert auto_append_latest_models(store, catalog) == []


def test_prune_stale_monitor_models(tmp_path: Path):
    from llm_price_monitor.store import Store
    from llm_price_monitor.webapi.jobs import prune_stale_monitor_models

    store = Store(tmp_path / "monitor.db")
    store.set_document("settings", {
        "monitor_models": ["fresh", "stale", "monthly-old", "no-date", "custom", "cn-1", "cn-5"],
    })
    catalog = {"models": {
        "fresh": {"model": "fresh", "release_date": "2026-09-30"},
        "stale": {"model": "stale", "release_date": "2024-01-01"},
        "monthlyold": {"model": "monthly-old", "release_date": "2025-01"},
        "nodate": {"model": "no-date"},
        # 国内厂商条目：cn-1 在前 4 内保留，cn-5 排第 5 滚出
        "cn1": {"model": "cn-1", "vendor": "DeepSeek"},
        "cn2": {"model": "cn-2", "vendor": "DeepSeek"},
        "cn3": {"model": "cn-3", "vendor": "DeepSeek"},
        "cn4": {"model": "cn-4", "vendor": "DeepSeek"},
        "cn5": {"model": "cn-5", "vendor": "DeepSeek"},
    }}

    # 超期的按发布日期清；国内厂商条目滚出前 N 的清；无日期无归属（自定义）与白名单厂商的不动
    assert prune_stale_monitor_models(store, catalog, 12) == ["stale", "monthly-old", "cn-5"]
    # 保留项落库按厂商分组排序：DeepSeek 在前，无厂商归属的按目录序垫底，自定义最后
    assert store.get_document("settings")["monitor_models"] == ["cn-1", "fresh", "no-date", "custom"]
    # 时限设 0 表示关闭自动清理
    assert prune_stale_monitor_models(store, catalog, 0) == []


def test_canonicalize_monitor_models(tmp_path: Path):
    from llm_price_monitor.store import Store
    from llm_price_monitor.webapi.jobs import canonicalize_monitor_models

    store = Store(tmp_path / "monitor.db")
    store.set_document("settings", {
        # 历史遗留：清单里存过目录的归一化键（丢横线），与手动加的标准名/自定义名混在一起
        "monitor_models": ["deepseekchat", "gpt5", "deepseek-chat", "my-own-model"],
        "monitor_models_dismissed": ["kimik2"],
    })
    catalog = {"models": {
        "deepseekchat": {"model": "deepseek-chat"},
        "gpt5": {"model": "GPT-5"},
        "kimik2": {"model": "kimi-k2"},
    }}

    renamed = canonicalize_monitor_models(store, catalog)
    assert renamed == ["deepseekchat → deepseek-chat", "gpt5 → GPT-5", "kimik2 → kimi-k2"]
    settings = store.get_document("settings")
    # 换名后去重、自定义模型保留原样；移除名单同步换名，继续拦住自动补模型
    assert settings["monitor_models"] == ["deepseek-chat", "GPT-5", "my-own-model"]
    assert settings["monitor_models_dismissed"] == ["kimi-k2"]
    # 没有变化就不再写库
    assert canonicalize_monitor_models(store, catalog) == []


def test_vendor_sources_seeded_on_first_boot(workspace: Path, monkeypatch):
    """种子文件里的 vendor_sources 首次启动导入（含描述）；坏条目跳过不阻断启动。"""
    from llm_price_monitor.webapi import jobs as web_jobs

    config_path = _config(workspace)
    seed = json.loads(config_path.read_text(encoding="utf-8"))
    seed["vendor_sources"] = [
        {"vendor": "Zhipu AI", "url": "https://docs.bigmodel.cn/cn/guide/start/pricing.md", "region": "cn",
         "note": "bigmodel.cn 官方定价页，人民币标价直接作为国内基准"},
        {"vendor": "坏条目", "url": "ftp://x.cn"},  # 非法 URL：导入时跳过
    ]
    config_path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(web_jobs, "fetch_catalogs", lambda *a, **k: (_fake_catalog_doc(), _fake_catalog_doc()))

    # 不能用 _admin_client：它内部再调一次 _config 会把种子改动覆盖回原样
    from fastapi.testclient import TestClient

    client = TestClient(create_app(config_path), client=("testclient", 50000))
    assert client.post("/api/setup", json={"username": "admin", "password": "s3cret"}).status_code == 200
    sources = client.get("/api/vendor-sources").json()["sources"]
    assert [s["vendor"] for s in sources] == ["Zhipu AI"]
    assert sources[0]["note"] == "bigmodel.cn 官方定价页，人民币标价直接作为国内基准"
    assert sources[0]["enabled"] is True and sources[0]["region"] == "cn"
