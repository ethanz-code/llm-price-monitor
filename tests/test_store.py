"""SQLite 存储层与首次种子导入的行为测试。"""
import json
import sqlite3
import time
from pathlib import Path

import pytest

from llm_price_monitor.config import config_from_store
from llm_price_monitor.store import Store
from llm_price_monitor.webapi.app import create_app


def _site_config(site_id: str, url: str) -> dict:
    return {
        "id": site_id,
        "adapter": "standard",
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

def test_delete_site_purge_cleans_related_data(tmp_path: Path):
    """purge 删除同时清理该站点的历史/事件/状态/快照；默认删除只删配置保留数据。"""
    store = Store(tmp_path / "monitor.db")
    store.upsert_site("a", _site_config("a", "https://a.test/api"))
    store.append_history([
        {"site_id": "a", "model": "m1", "captured_at": 1.0},
        {"site_id": "b", "model": "m1", "captured_at": 2.0},
    ])
    store.append_events([{"site_id": "a", "model": "m1", "kind": "new", "detected_at": 1.0}])
    store.append_status_records([{"site_id": "a", "captured_at": 1.0, "ok": True}])
    store.append_status_events([{"site_id": "a", "kind": "channel_up", "detected_at": 1.0}])
    store.replace_latest({"a:m1:default": {"site_id": "a"}, "b:m1:default": {"site_id": "b"}})

    # 默认删除：历史数据全部保留
    assert store.delete_site("a") is True
    assert store.get_site_config("a") is None
    assert store.read_history(limit=10, site_id="a")[1] == 1
    assert "a:m1:default" in store.latest_all()

    # purge 删除：五类站点数据一并清理，其他站点不受影响
    store.upsert_site("a", _site_config("a", "https://a.test/api"))
    assert store.delete_site("a", purge=True) is True
    assert store.read_history(limit=10, site_id="a")[1] == 0
    assert store.read_events(limit=10, site_id="a")[1] == 0
    assert store.read_status(limit=10, site_id="a")[1] == 0
    assert store.read_status_events(limit=10, site_id="a")[1] == 0
    assert "a:m1:default" not in store.latest_all()
    assert store.read_history(limit=10, site_id="b")[1] == 1
    assert "b:m1:default" in store.latest_all()


def test_config_from_store_builds_typed_config(tmp_path: Path):
    store = Store(tmp_path / "monitor.db")
    store.replace_sites([_site_config("demo", "https://demo.test/api")])
    store.set_document("settings", {"timeout": 9.5})
    store.set_document("ai", {"enabled": True, "base_url": "https://ai.test/v1", "models": ["m-a"], "api_key": "sk-x"})

    config = config_from_store(store)
    assert config.settings.timeout == 9.5
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


def test_read_rows_clamps_limit(tmp_path: Path, monkeypatch):
    """公开读接口的 limit 统一钳制到 _MAX_ROW_LIMIT：超大入参不会一次拖全表。"""
    import llm_price_monitor.store as store_module

    monkeypatch.setattr(store_module, "_MAX_ROW_LIMIT", 2)
    store = Store(tmp_path / "monitor.db")
    store.append_history([
        {"site_id": "a", "model": "m1", "captured_at": 1.0},
        {"site_id": "a", "model": "m2", "captured_at": 2.0},
        {"site_id": "a", "model": "m3", "captured_at": 3.0},
    ])
    rows, total = store.read_history(limit=10**9)
    assert total == 3 and len(rows) == 2  # COUNT 反映全量，返回行数被钳制


def test_seed_imports_var_files_once(tmp_path: Path, monkeypatch):
    """首启种子：配置文件 + var/ 存量全部入库；二次启动不重复导入。"""
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config" / "default-seed.json"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps({
        # timeout 断言种子导入；schedule 全 0 关闭后台调度，测试环境不触网
        "settings": {"timeout": 15.0, "schedule": {"price": 0, "status": 0, "notice": 0, "catalog": 0}},
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
    (tmp_path / "var" / "catalog.json").write_text(json.dumps({
        "generated_at": time.time(),  # 新鲜目录：启动自动同步线程不触发网络请求
        "models": {"m": {"found": True}}, "usd_cny_rate": 7.0,
    }), encoding="utf-8")

    first = create_app(config_path)
    store: Store = first.state.store
    assert store.count_history() == 1
    assert store.count_events() == 1
    assert len(store.latest_all()) == 1
    assert store.get_document("catalog") is not None
    assert config_from_store(store).settings.timeout == 15.0

    # 二次启动：seeded 标记阻止重复导入
    second = create_app(config_path)
    store2: Store = second.state.store
    assert store2.count_history() == 1
    assert store2.count_events() == 1


def test_price_records_migrates_to_price_trend(tmp_path: Path):
    """旧库升级：price_records 整行 JSON 压平成 price_trend 精简列，旧表删除。"""
    import sqlite3

    db = tmp_path / "monitor.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE price_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id TEXT NOT NULL,
            model TEXT NOT NULL,
            captured_at REAL NOT NULL,
            record TEXT NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO price_records (site_id, model, captured_at, record) VALUES (?, ?, ?, ?)",
        ("a", "m1", 1.0, json.dumps({"site_id": "a", "model": "m1", "input_price": 1, "output_price": 2,
                                     "unit": "USD/1M tokens", "metadata": {"group": "vip"}})),
    )
    conn.commit()
    conn.close()

    store = Store(db)
    rows, total = store.read_history(limit=10)
    assert total == 1
    assert rows[0]["group"] == "vip"
    assert rows[0]["input_price"] == 1 and rows[0]["output_price"] == 2
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "price_records" not in tables and "price_trend" in tables


def test_purge_history_and_events(tmp_path: Path):
    """保留清理：只删截止时间之前的数据，之后的保留。"""
    store = Store(tmp_path / "monitor.db")
    store.append_history([
        {"site_id": "a", "model": "m1", "captured_at": 1.0},
        {"site_id": "a", "model": "m1", "captured_at": 2.0},
    ])
    store.append_events([
        {"site_id": "a", "model": "m1", "kind": "new", "detected_at": 1.0},
        {"site_id": "a", "model": "m1", "kind": "new", "detected_at": 2.0},
    ])
    assert store.purge_history(1.5) == 1
    # 价格事件属于变更记录：不参与保留清理，全量保留
    assert store.read_events(limit=10)[0][0]["detected_at"] == 1.0
    assert store.count_history() == 1
    assert store.read_history(limit=10)[0][0]["captured_at"] == 2.0


def test_prune_price_groups_keeps_only_selected_groups(tmp_path: Path):
    """分组白名单保存时：快照/历史/事件里未选中分组的行被删（分组名忽略大小写），
    group_miss 计数同步清理；未选中分组的判定只影响目标站点，别站数据不动；留空是幂等空操作。"""
    store = Store(tmp_path / "monitor.db")
    store.replace_latest({
        "demo:m1:svip": {"site_id": "demo", "model": "m1", "input_price": 1.0, "metadata": {"group": "svip"}},
        "demo:m1:default": {"site_id": "demo", "model": "m1", "input_price": 2.0, "metadata": {"group": "default"}},
        "other:m1:default": {"site_id": "other", "model": "m1", "input_price": 3.0, "metadata": {"group": "default"}},
    })
    store.append_history([
        {"site_id": "demo", "model": "m1", "input_price": 1.0, "unit": "USD/1M tokens", "captured_at": 1.0, "metadata": {"group": "svip"}},
        {"site_id": "demo", "model": "m1", "input_price": 2.0, "unit": "USD/1M tokens", "captured_at": 1.0, "metadata": {"group": "default"}},
        {"site_id": "other", "model": "m1", "input_price": 3.0, "unit": "USD/1M tokens", "captured_at": 1.0, "metadata": {"group": "default"}},
    ])
    store.append_events([
        {"site_id": "demo", "model": "m1", "kind": "changed", "detected_at": 1.0,
         "previous": {"metadata": {"group": "default"}}, "current": {"metadata": {"group": "default"}}},
        {"site_id": "demo", "model": "m1", "kind": "changed", "detected_at": 1.0,
         "previous": {"metadata": {"group": "SVIP"}}, "current": None},
    ])
    store.set_document("group_miss", {"demo:m1:default": 1, "demo:m1:svip": 2, "other:m1:default": 1})

    stats = store.prune_price_groups("demo", ["svip"])
    assert stats == {"latest": 1, "trend": 1, "events": 1}
    assert set(store.latest_all()) == {"demo:m1:svip", "other:m1:default"}
    rows, total = store.read_history(site_id="demo", limit=10)
    assert total == 1 and rows[0]["group"] == "svip"
    events, _ = store.read_events(site_id="demo", limit=10)
    assert [event["previous"]["metadata"]["group"] for event in events] == ["SVIP"]
    assert store.get_document("group_miss") == {"demo:m1:svip": 2, "other:m1:default": 1}

    assert store.prune_price_groups("demo", []) == {"latest": 0, "trend": 0, "events": 0}


def test_ai_logs_summary(tmp_path: Path):
    """AI 调用汇总：KPI 合计、按天补零（本地时区）与场景/模型分布；fallback/param_retry 是失败尝试不算成功。"""
    store = Store(tmp_path / "monitor.db")
    store.add_ai_log(scene="助手问答", model="m1", status="ok", duration_ms=1000, prompt_tokens=100, completion_tokens=50, total_tokens=150)
    store.add_ai_log(scene="助手问答", model="m2", status="fallback", duration_ms=2000, error="限流")
    store.add_ai_log(scene="价格抽取", model="m1", status="ok", duration_ms=3000, prompt_tokens=200, completion_tokens=100, total_tokens=300)
    store.add_ai_log(scene="价格抽取", model="m1", status="error", duration_ms=4000, error="超时")
    store.add_ai_log(scene="价格抽取", model="m3", status="param_retry", duration_ms=500, error="enable_thinking 受限")
    # 把前两条挪到昨天，验证按天聚合与补零
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE ai_logs SET ts = ts - 86400 WHERE id <= 2")

    summary = store.ai_logs_summary()
    assert summary["total"] == 5
    assert (summary["ok"], summary["fallback"], summary["param_retry"], summary["error"]) == (2, 1, 1, 1)
    assert (summary["prompt_tokens"], summary["completion_tokens"], summary["total_tokens"]) == (300, 150, 450)
    assert summary["avg_duration_ms"] == 2100

    today = time.strftime("%m-%d")
    yesterday = time.strftime("%m-%d", time.localtime(time.time() - 86400))
    assert [d["day"] for d in summary["daily"]] == [
        time.strftime("%m-%d", time.localtime(time.time() - offset * 86400)) for offset in range(6, -1, -1)
    ]
    assert summary["daily"][-2]["day"] == yesterday and summary["daily"][-1]["day"] == today
    assert summary["daily"][-2]["ok"] == 1 and summary["daily"][-2]["fallback"] == 1
    assert summary["daily"][-1]["ok"] == 1 and summary["daily"][-1]["error"] == 1 and summary["daily"][-1]["param_retry"] == 1

    assert {s["name"]: s["calls"] for s in summary["scenes"]} == {"助手问答": 2, "价格抽取": 3}
    assert {m["name"]: m["calls"] for m in summary["models"]} == {"m1": 3, "m2": 1, "m3": 1}
