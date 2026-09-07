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
            "schedule": {"price": 0, "status": 0, "notice": 0, "catalog": 0},
        },
        "ai": {"enabled": False},
        "sites": [{
            "id": "demo",
            "adapter": "standard",
            "model_list_url": "https://demo.test/pricing",
            "models": ["demo-model"],
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
    events = client.get("/api/events", params={"kind": "new"}).json()
    assert events["total"] == 1
    catalog = client.get("/api/catalog").json()
    assert catalog["models"]["demomodel"]["vendor"] == "Demo"


def test_discount_computes_ratio_with_site_and_model(workspace: Path):
    client = TestClient(create_app(_config(workspace)))
    data = client.get("/api/discount").json()
    assert data["usd_cny_rate"] == 7.0
    assert len(data["discounts"]) == 1
    entry = data["discounts"][0]
    assert entry["site_id"] == "demo" and entry["model"] == "demo-model"
    assert entry["input"] == 0.5 and entry["output"] == 0.5
    assert data["skipped"] == []


def test_overview_attaches_discount_and_catalog_context(workspace: Path):
    client = TestClient(create_app(_config(workspace)))
    data = client.get("/api/overview").json()
    assert data["catalog"]["enabled"] is True
    assert data["records"][0]["discount"]["input"] == 0.5


def test_catalog_missing_returns_404(workspace: Path):
    (workspace / "var" / "catalog.json").unlink()
    # 调度已在 _config 里关闭，目录缺失时不会触发自动同步，保持"无数据"状态
    client = TestClient(create_app(_config(workspace)))
    assert client.get("/api/catalog").status_code == 404
    assert client.get("/api/discount").status_code == 404


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
    doc["settings"]["schedule"] = {"price": 0, "status": 0, "notice": 0, "catalog": 0.001}
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

    # 等上一个测试留下的 catalog-refresh 线程退出，避免启动时的 submit 撞上运行中同名任务
    for _ in range(100):
        if not any(t["kind"] == "catalog-refresh" and t["status"] == "running" for t in tasks.recent(100)):
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
    config_doc["settings"]["schedule"] = {"price": 0, "status": 0, "notice": 0, "catalog": 1440}
    config_path.write_text(json.dumps(config_doc), encoding="utf-8")
    client = TestClient(create_app(config_path))
    for _ in range(100):
        if client.get("/api/catalog").status_code == 200:
            break
        time.sleep(0.02)
    assert client.get("/api/catalog").json()["models"]["demomodel"]["vendor"] == "Demo"


def test_collect_runs_in_background_without_persist(workspace: Path, monkeypatch):
    from llm_price_monitor.report import MonitorReport
    import llm_price_monitor.webapi.routes.collect as collect_routes

    def fake_run_once(config, *, persist=True, **_kwargs):
        assert persist is False
        return MonitorReport(0.0, 1.0, [{"model": "demo-model"}], [], [])

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


def test_env_password_seeds_admin_for_existing_deployments(workspace: Path, monkeypatch):
    monkeypatch.setenv("PRICE_WEB_PASSWORD", "s3cret")
    monkeypatch.setenv("PRICE_WEB_USERNAME", "ethan")
    client = TestClient(create_app(_config(workspace)))
    meta = client.get("/api/meta").json()
    assert meta["needs_setup"] is False and meta["is_admin"] is False
    assert client.post("/api/auth/login", json={"username": "ethan", "password": "s3cret"}).status_code == 200
    assert client.get("/api/meta").json()["is_admin"] is True


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
    assert client.get("/api/events", params={"site_id": "demo"}).json()["total"] == 0
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
    from llm_price_monitor.report import SectionScan

    monkeypatch.setattr(jobs, "scan_prices", lambda _config, **_kw: SectionScan(records=[{"model": "demo-model"}]))
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
    ok = client.put("/api/settings", json={"settings": {"schedule": {"price": 30, "status": 0, "notice": 10, "catalog": 720}}})
    assert ok.status_code == 200
    assert ok.json()["settings"]["schedule"] == {"price": 30, "status": 0, "notice": 10, "catalog": 720}


def test_scheduler_run_due_submits_due_jobs_and_persists_state(tmp_path: Path, monkeypatch):
    """到期项提交任务并记入 schedule_state；未到期与关闭项跳过。"""
    import llm_price_monitor.webapi.jobs as jobs
    import llm_price_monitor.webapi.scheduler as scheduler_mod
    from llm_price_monitor.store import Store
    from llm_price_monitor.webapi import tasks

    tasks.reset()  # 任务注册表进程内共享，先清场再用精确计数断言
    monkeypatch.setattr(jobs, "fetch_catalogs", lambda **_kwargs: ({"models": {}}, {"models": {}}))
    store = Store(tmp_path / "monitor.db")
    store.set_document("settings", {"schedule": {"price": 0, "status": 0, "notice": 0, "catalog": 60}})

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
    store.set_document("settings", {"schedule": {"price": 0, "status": 0, "notice": 0, "catalog": 0}})
    scheduler_mod._run_due(store)
    assert tasks.recent(100) == []

    store.set_document("settings", {"schedule": {"price": "每分钟"}})
    with pytest.raises(ValueError):
        scheduler_mod._run_due(store)


def test_reseed_modes(workspace: Path):
    """手动种子导入：skip_existing 只补缺失项，overwrite 覆盖同名项；空站点列表不清空站点。"""
    admin = _admin_client(workspace)
    seed_path = workspace / "config.json"  # _config 写入、create_app 记住的种子路径

    # skip_existing：库中没有 timeout → 写入；schedule 已存在 → 跳过；ai.enabled 已存在 → 跳过
    seed_path.write_text(json.dumps({
        "settings": {"timeout": 99, "schedule": {"price": 123, "status": 0, "notice": 0, "catalog": 0}},
        "ai": {"enabled": True},
        "sites": [],
    }), encoding="utf-8")
    result = admin.post("/api/seed", json={"mode": "skip_existing"}).json()
    assert set(result["settings_written"]) == {"timeout"}
    assert result["ai_written"] == [] and result["sites_written"] == 0 and result["sites_replaced"] is False
    settings = admin.get("/api/settings").json()["settings"]
    assert settings["timeout"] == 99
    assert settings["schedule"]["price"] == 0  # 已存在，未被种子覆盖
    assert admin.get("/api/settings").json()["ai"]["enabled"] is False
    assert len(admin.get("/api/sites").json()["sites"]) == 1

    # overwrite：种子里的键逐项覆盖；sites 为空数组时受保护不清空
    seed_path.write_text(json.dumps({
        "settings": {"timeout": 20, "schedule": {"price": 30, "status": 5, "notice": 10, "catalog": 720}},
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
    assert data["settings"]["schedule"]["price"] == 60 and data["settings"]["schedule"]["catalog"] == 1440


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
