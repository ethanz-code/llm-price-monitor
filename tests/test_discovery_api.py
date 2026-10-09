"""站点发现接口：/api/discovery 读取 discover 产出、标注入库状态与空数据引导。"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from llm_price_monitor import discover
from llm_price_monitor.webapi.app import create_app
from tests.test_webapi import _config, workspace  # noqa: F401  workspace 为共享 fixture


def _write_discovery(workspace: Path, results: list[dict]) -> None:
    out = workspace / "discovery"
    out.mkdir(parents=True, exist_ok=True)
    (out / "probed.json").write_text(
        json.dumps({"generated_at": "2026-10-05 10:00:00", "results": results}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_discovery_404_when_never_ran(workspace: Path, monkeypatch):
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")
    client = TestClient(create_app(_config(workspace)))
    assert client.get("/api/discovery").status_code == 404


def test_discovery_lists_available_and_marks_imported(workspace: Path, monkeypatch):
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")
    # _config 种子里有站点 id=demo（demo.test），域名对得上的候选站要标注成已入库
    _write_discovery(
        workspace,
        [
            {"name": "可用站", "url": "https://demo.test", "sources": ["zuiquanapi"], "new_api": True, "pricing_ok": True, "models": 42, "auth_required": False, "error": ""},
            {"name": "要登录", "url": "https://auth.example.net", "sources": ["zuiquanapi"], "new_api": True, "pricing_ok": False, "models": 0, "auth_required": True, "error": ""},
            {"name": "死站", "url": "https://dead.example.org", "sources": ["zuiquanapi"], "new_api": False, "pricing_ok": False, "models": 0, "auth_required": False, "error": "ConnectTimeout"},
        ],
    )
    client = TestClient(create_app(_config(workspace)))
    resp = client.get("/api/discovery")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == {"total": 3, "available": 1, "auth": 1, "dead": 1, "imported": 1}
    assert len(body["stations"]) == 1  # 只列可用站，失联站只留计数
    row = body["stations"][0]
    assert row["host"] == "demo.test" and row["models"] == 42 and row["state"] == "available"
    assert row["imported_id"] == "demo"


def test_discovery_normalizes_www_host_for_import_match(workspace: Path, monkeypatch):
    monkeypatch.setattr(discover, "OUT_DIR", workspace / "discovery")
    _write_discovery(
        workspace,
        [
            {"name": "带 www 的同站", "url": "https://www.demo.test", "sources": ["apisou"], "new_api": True, "pricing_ok": True, "models": 7, "auth_required": False, "error": ""},
        ],
    )
    client = TestClient(create_app(_config(workspace)))
    body = client.get("/api/discovery").json()
    assert body["stations"][0]["imported_id"] == "demo"
