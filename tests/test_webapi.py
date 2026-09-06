import json
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from llm_price_monitor.official import fx
from llm_price_monitor.webapi.app import create_app


def _config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "settings": {
            "history_file": str(tmp_path / "var" / "history.jsonl"),
            "latest_file": str(tmp_path / "var" / "latest.json"),
            "event_file": str(tmp_path / "var" / "events.jsonl"),
        },
        "ai": {"enabled": False},
        "sites": [{
            "id": "demo",
            "adapter": "browser",
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
    (tmp_path / "var" / "official-prices.json").write_text(json.dumps({
        "generated_at": 1000.0,
        "generated_at_iso": "2026-08-31T00:00:00+0800",
        "usd_cny_rate": 7.0,
        "rate_source": "test",
        "models": {
            "demomodel": {
                "found": True, "model": "demo-model", "vendor": "Demo", "currency": "USD",
                "list": {"input": 10.0, "output": 50.0}, "promo": None,
                "effective": {"input": 10.0, "output": 50.0, "basis": "list"},
                "source_url": "https://demo.test/official",
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
    official = client.get("/api/official").json()
    assert official["models"]["demomodel"]["vendor"] == "Demo"


def test_discount_computes_ratio_with_site_and_model(workspace: Path):
    client = TestClient(create_app(_config(workspace)))
    data = client.get("/api/discount").json()
    assert data["usd_cny_rate"] == 7.0
    assert len(data["discounts"]) == 1
    entry = data["discounts"][0]
    assert entry["site_id"] == "demo" and entry["model"] == "demo-model"
    assert entry["input"] == 0.5 and entry["output"] == 0.5
    assert data["skipped"] == []


def test_overview_attaches_discount_and_official_context(workspace: Path):
    client = TestClient(create_app(_config(workspace)))
    data = client.get("/api/overview").json()
    assert data["official"]["enabled"] is True
    assert data["records"][0]["discount"]["input"] == 0.5


def test_official_missing_returns_404(workspace: Path):
    (workspace / "var" / "official-prices.json").unlink()
    client = TestClient(create_app(_config(workspace)))
    assert client.get("/api/official").status_code == 404
    assert client.get("/api/discount").status_code == 404


def test_collect_runs_in_background_without_persist(workspace: Path, monkeypatch):
    from llm_price_monitor.report import MonitorReport
    import llm_price_monitor.webapi.app as app_module

    def fake_run_once(config, *, persist=True, **_kwargs):
        assert persist is False
        return MonitorReport(0.0, 1.0, [{"model": "demo-model"}], [], [], [])

    monkeypatch.setattr(app_module, "run_once", fake_run_once)
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
    import llm_price_monitor.webapi.app as app_module

    release = threading.Event()

    def slow_run_once(config, *, persist=True, **_kwargs):
        release.wait(2)
        return MonitorReport(0.0, 1.0, [], [], [], [])

    monkeypatch.setattr(app_module, "run_once", slow_run_once)
    client = _admin_client(workspace)
    assert client.post("/api/collect", json={"site_id": "nope"}).status_code == 400
    client.post("/api/collect", json={})
    conflict = client.post("/api/collect", json={})
    assert conflict.status_code == 409
    release.set()


def _admin_client(workspace: Path, username: str = "admin", password: str = "s3cret") -> TestClient:
    """创建应用并完成首次设置，返回已登录管理员的客户端（会话 cookie 自动保持）。"""
    client = TestClient(create_app(_config(workspace)))
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
        "settings": {"webhook": "https://hook.test", "tavily_api_key": "tvly-x"},
        "ai": {"base_url": "https://ai.test/v1", "models": ["m-a", "m-b"], "api_key": "sk-x"},
    })
    assert saved.status_code == 200
    assert saved.json()["settings"]["webhook"] == "https://hook.test"
    assert saved.json()["ai"]["api_key"] == "sk-x"

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


def test_tasks_listed_after_collect(workspace: Path, monkeypatch):
    from llm_price_monitor.report import MonitorReport
    import llm_price_monitor.webapi.app as app_module

    monkeypatch.setattr(app_module, "run_once", lambda config, **kwargs: MonitorReport(0.0, 1.0, [], [], [], []))
    client = _admin_client(workspace)
    task_id = client.post("/api/collect", json={}).json()["task_id"]
    listed = client.get("/api/tasks").json()["tasks"]
    assert task_id in [task["id"] for task in listed]
