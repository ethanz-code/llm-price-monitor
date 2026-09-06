"""SQLite 存储层与首次种子导入的行为测试。"""
import json
from pathlib import Path

import pytest

from llm_price_monitor.config import config_from_store
from llm_price_monitor.store import Store
from llm_price_monitor.webapi.app import create_app


def _site_config(site_id: str, url: str) -> dict:
    return {
        "id": site_id,
        "adapter": "browser",
        "model_list_url": url,
        "models": ["demo-model"],
        "network": {"url": url},
    }


def test_site_crud_round_trip(tmp_path: Path):
    store = Store(tmp_path / "monitor.db")
    assert store.count_sites() == 0

    store.upsert_site("a", _site_config("a", "https://a.test/api"))
    store.upsert_site("b", _site_config("b", "https://b.test/api"))
    assert [site["id"] for site in store.list_site_configs()] == ["a", "b"]

    store.upsert_site("a", _site_config("a", "https://a2.test/api"))
    updated = store.get_site_config("a")
    assert updated is not None and updated["model_list_url"] == "https://a2.test/api"
    assert store.list_site_configs()[0]["id"] == "a"  # 更新不改变排序

    assert store.delete_site("a") is True
    assert store.delete_site("a") is False
    assert store.get_site_config("a") is None


def test_config_from_store_builds_typed_config(tmp_path: Path):
    store = Store(tmp_path / "monitor.db")
    store.replace_sites([_site_config("demo", "https://demo.test/api")])
    store.set_document("settings", {"timeout": 9.5, "tavily_api_key": "tvly-x"})
    store.set_document("ai", {"enabled": True, "base_url": "https://ai.test/v1", "models": ["m-a"], "api_key": "sk-x"})

    config = config_from_store(store)
    assert config.settings.timeout == 9.5
    assert config.settings.tavily_api_key == "tvly-x"
    assert config.ai.api_key == "sk-x"
    assert config.ai.cache is store
    assert config.sites[0].id == "demo"


def test_config_from_store_allows_empty_sites(tmp_path: Path):
    """新装状态：站点表为空是合法的，等管理面板添加第一个站点。"""
    store = Store(tmp_path / "monitor.db")
    config = config_from_store(store)
    assert config.sites == ()


def test_store_history_and_events_filters(tmp_path: Path):
    store = Store(tmp_path / "monitor.db")
    store.append_history([
        {"site_id": "a", "model": "m1", "captured_at": 1.0, "price_status": "confirmed"},
        {"site_id": "a", "model": "m2", "captured_at": 2.0, "price_status": "confirmed"},
        {"site_id": "b", "model": "m1", "captured_at": 3.0, "price_status": "confirmed"},
    ])
    store.append_events([
        {"site_id": "a", "model": "m1", "kind": "new", "detected_at": 1.0},
        {"site_id": "a", "model": "m1", "kind": "changed", "detected_at": 2.0},
    ])

    rows, total = store.read_history(limit=10, model="m1")
    assert total == 2 and [row["site_id"] for row in rows] == ["a", "b"]  # 按写入顺序返回
    rows, total = store.read_history(limit=1, site_id="a")
    assert total == 2 and len(rows) == 1 and rows[0]["model"] == "m2"  # 取最近一条

    events, total = store.read_events(limit=10, kind="new")
    assert total == 1 and events[0]["kind"] == "new"


def test_seed_imports_var_files_once(tmp_path: Path, monkeypatch):
    """首启种子：配置文件 + var/ 存量全部入库；二次启动不重复导入。"""
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config" / "price-monitor.json"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps({
        "settings": {"timeout": 15.0},
        "ai": {"enabled": False},
        "sites": [_site_config("demo", "https://demo.test/api")],
    }), encoding="utf-8")
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "price-latest.json").write_text(json.dumps({
        "demo:m:": {"site_id": "demo", "model": "m", "price_status": "confirmed"},
    }), encoding="utf-8")
    (tmp_path / "var" / "price-history.jsonl").write_text(
        json.dumps({"site_id": "demo", "model": "m", "captured_at": 1.0}) + "\n", encoding="utf-8"
    )
    (tmp_path / "var" / "price-events.jsonl").write_text(
        json.dumps({"site_id": "demo", "model": "m", "kind": "new", "detected_at": 1.0}) + "\n", encoding="utf-8"
    )
    (tmp_path / "var" / "official-prices.json").write_text(json.dumps({
        "models": {"m": {"found": True}}, "usd_cny_rate": 7.0,
    }), encoding="utf-8")

    first = create_app(config_path)
    store: Store = first.state.store
    assert store.count_history() == 1
    assert store.count_events() == 1
    assert len(store.latest_all()) == 1
    assert store.get_document("official_prices") is not None
    assert config_from_store(store).settings.timeout == 15.0

    # 二次启动：seeded 标记阻止重复导入
    second = create_app(config_path)
    store2: Store = second.state.store
    assert store2.count_history() == 1
    assert store2.count_events() == 1
