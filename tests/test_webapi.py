import json
import threading
import base64
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
    client = TestClient(create_app(_config(workspace)))
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
    client = TestClient(create_app(_config(workspace)))
    assert client.post("/api/collect", json={"site_id": "nope"}).status_code == 400
    client.post("/api/collect", json={})
    conflict = client.post("/api/collect", json={})
    assert conflict.status_code == 409
    release.set()


def test_admin_gate_gates_writes_only_when_password_configured(workspace: Path, monkeypatch):
    monkeypatch.setenv("PRICE_WEB_PASSWORD", "s3cret")
    monkeypatch.setenv("PRICE_WEB_USERNAME", "ethan")
    client = TestClient(create_app(_config(workspace)))

    # 读接口公开浏览
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/meta").json()["is_admin"] is False

    # 写接口需要管理员凭据
    denied = client.post("/api/collect", json={})
    assert denied.status_code == 401
    assert "Basic" in denied.headers["WWW-Authenticate"]
    wrong = client.post(
        "/api/auth/verify", headers={"Authorization": "Basic " + base64.b64encode(b"ethan:nope").decode()}
    )
    assert wrong.status_code == 401

    # 管理员凭据通过验证，meta 也识别为管理员
    creds = {"Authorization": "Basic " + base64.b64encode(b"ethan:s3cret").decode()}
    assert client.post("/api/auth/verify", headers=creds).json() == {"is_admin": True}
    assert client.get("/api/meta", headers=creds).json()["is_admin"] is True


def test_no_auth_by_default(workspace: Path, monkeypatch):
    monkeypatch.delenv("PRICE_WEB_PASSWORD", raising=False)
    client = TestClient(create_app(_config(workspace)))
    assert client.get("/api/health").status_code == 200
