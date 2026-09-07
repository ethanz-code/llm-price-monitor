"""SQLite 存储层：站点配置、系统设置、价格历史、事件、快照与文档的唯一真相源。

连接策略：每次操作开短连接并在用后关闭，天然跨线程安全（采集任务在后台线程写库）。
库文件路径相对启动工作目录解析，默认 var/monitor.db，与 webapi 其余路径语义一致。
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

_MAX_ROW_LIMIT = 2000  # 公开读接口单次返回行数上限：limit 入参统一在 _read_rows 钳制，防单请求拖全表

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    config TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    model TEXT NOT NULL,
    captured_at REAL NOT NULL,
    record TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_records_site_model ON price_records(site_id, model);
CREATE TABLE IF NOT EXISTS price_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    model TEXT NOT NULL,
    kind TEXT NOT NULL,
    detected_at REAL NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_events_kind ON price_events(kind);
CREATE TABLE IF NOT EXISTS latest (
    key TEXT PRIMARY KEY,
    record TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    name TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    contact TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS status_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    captured_at REAL NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_status_records_site ON status_records(site_id);
CREATE TABLE IF NOT EXISTS status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    detected_at REAL NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_status_events_site ON status_events(site_id);
CREATE TABLE IF NOT EXISTS notice_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    captured_at REAL NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notice_records_site ON notice_records(site_id);
CREATE TABLE IF NOT EXISTS notice_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    detected_at REAL NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notice_events_site ON notice_events(site_id);
CREATE TABLE IF NOT EXISTS visit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    path TEXT NOT NULL,
    ip TEXT NOT NULL,
    user_agent TEXT NOT NULL,
    browser TEXT NOT NULL,
    os TEXT NOT NULL,
    device TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_visit_logs_ts ON visit_logs(ts);
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ---------- 站点配置 ----------

    def count_sites(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0])

    def list_site_configs(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT config FROM sites ORDER BY seq, id").fetchall()
        return [json.loads(row["config"]) for row in rows]

    def get_site_config(self, site_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT config FROM sites WHERE id = ?", (site_id,)).fetchone()
        return json.loads(row["config"]) if row else None

    def upsert_site(self, site_id: str, config: dict[str, Any]) -> None:
        with self._conn() as conn:
            exists = conn.execute("SELECT 1 FROM sites WHERE id = ?", (site_id,)).fetchone()
            if exists:
                conn.execute("UPDATE sites SET config = ? WHERE id = ?", (_dumps(config), site_id))
            else:
                seq = int(conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM sites").fetchone()[0])
                conn.execute(
                    "INSERT INTO sites (id, seq, config) VALUES (?, ?, ?)", (site_id, seq, _dumps(config))
                )

    def delete_site(self, site_id: str, *, purge: bool = False) -> bool:
        """删除站点配置；purge 为真时一并清理该站点的历史价格、事件、状态时序与最新快照。"""
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
            deleted = cursor.rowcount > 0
            if purge and deleted:
                for table in ("price_records", "price_events", "status_records", "status_events"):
                    conn.execute(f"DELETE FROM {table} WHERE site_id = ?", (site_id,))
                # latest 的 key 形如 "{site_id}:{model}:{group}"，用前缀精确匹配避免 LIKE 通配符歧义
                conn.execute("DELETE FROM latest WHERE substr(key, 1, ?) = ?", (len(site_id) + 1, f"{site_id}:"))
        return deleted

    def replace_sites(self, configs: list[dict[str, Any]]) -> None:
        """种子导入：整体替换站点表，seq 按数组顺序。"""
        with self._conn() as conn:
            conn.execute("DELETE FROM sites")
            for seq, config in enumerate(configs, start=1):
                site_id = str(config.get("id") or f"site-{seq}")
                conn.execute(
                    "INSERT OR REPLACE INTO sites (id, seq, config) VALUES (?, ?, ?)",
                    (site_id, seq, _dumps(config)),
                )

    # ---------- 设置与文档（settings / ai / catalog / ai_cache / seeded） ----------

    def get_document(self, name: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT content FROM documents WHERE name = ?", (name,)).fetchone()
        return json.loads(row["content"]) if row else None

    def set_document(self, name: str, content: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO documents (name, content, updated_at) VALUES (?, ?, ?)",
                (name, _dumps(content), time.time()),
            )

    # ---------- 最新快照 ----------

    def latest_all(self) -> dict[str, dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, record FROM latest").fetchall()
        return {row["key"]: json.loads(row["record"]) for row in rows}

    def replace_latest(self, values: dict[str, dict[str, Any]]) -> None:
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO latest (key, record) VALUES (?, ?)",
                [(key, _dumps(record)) for key, record in values.items()],
            )

    def remove_latest(self, keys: list[str]) -> None:
        """按 key 删除最新快照行；replace_latest 只做 upsert，清理遗留行必须走这里。"""
        if not keys:
            return
        with self._conn() as conn:
            conn.executemany("DELETE FROM latest WHERE key = ?", [(key,) for key in keys])

    # ---------- 历史与事件 ----------

    def append_history(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO price_records (site_id, model, captured_at, record) VALUES (?, ?, ?, ?)",
                [
                    (str(row.get("site_id") or ""), str(row.get("model") or ""), float(row.get("captured_at") or 0.0), _dumps(row))
                    for row in rows
                ],
            )

    def read_history(
        self, *, limit: int, site_id: str | None = None, model: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        return self._read_rows(
            "price_records", limit=limit, site_id=site_id, kind=model, kind_column="model", payload_column="record"
        )

    def count_history(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM price_records").fetchone()[0])

    def append_events(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO price_events (site_id, model, kind, detected_at, payload) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        str(row.get("site_id") or ""),
                        str(row.get("model") or ""),
                        str(row.get("kind") or ""),
                        float(row.get("detected_at") or 0.0),
                        _dumps(row),
                    )
                    for row in rows
                ],
            )

    def read_events(
        self, *, limit: int, site_id: str | None = None, kind: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        return self._read_rows(
            "price_events", limit=limit, site_id=site_id, kind=kind, kind_column="kind", payload_column="payload"
        )

    def append_feedback(self, content: str, contact: str | None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO feedback (content, contact, created_at) VALUES (?, ?, ?)",
                (content, contact, time.time()),
            )

    # ---------- 访问统计 ----------

    def add_visit(self, *, path: str, ip: str, user_agent: str, browser: str, os: str, device: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO visit_logs (ts, path, ip, user_agent, browser, os, device) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), path, ip, user_agent, browser, os, device),
            )

    def list_visits(self, *, limit: int = 100, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        with self._conn() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM visit_logs").fetchone()[0])
            rows = conn.execute(
                "SELECT ts, path, ip, user_agent, browser, os, device FROM visit_logs"
                " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows], total

    def clear_visits(self) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM visit_logs")

    def purge_visits(self, before_ts: float) -> int:
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM visit_logs WHERE ts < ?", (before_ts,))
            return int(cursor.rowcount)

    def visit_summary(self, *, trend_days: int = 30, top: int = 10) -> dict[str, Any]:
        """访问统计聚合：今日/累计 KPI、按天 PV/UV、设备/浏览器/系统分布、热门页面与 IP。

        分布与榜单取近 trend_days 天窗口，KPI 中"累计"为全量；按天序列补零对齐，
        日期统一用本地时区（与 dayKey 的前端语义一致）。
        """
        now = time.time()
        today0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        cutoff = today0 - (trend_days - 1) * 86400
        with self._conn() as conn:

            def scalar(sql: str, params: tuple[float | str | int, ...] = ()) -> int:
                return int(conn.execute(sql, params).fetchone()[0])

            daily_rows = conn.execute(
                "SELECT date(ts, 'unixepoch', 'localtime') AS day, COUNT(*) AS pv, COUNT(DISTINCT ip) AS uv"
                " FROM visit_logs WHERE ts >= ? GROUP BY day",
                (cutoff,),
            ).fetchall()
            by_day = {row["day"]: (int(row["pv"]), int(row["uv"])) for row in daily_rows}
            today = date.fromtimestamp(now)
            daily = [
                {"day": (today - timedelta(days=offset)).isoformat()[5:], "pv": by_day.get(day, (0, 0))[0], "uv": by_day.get(day, (0, 0))[1]}
                for offset in range(trend_days - 1, -1, -1)
                for day in [(today - timedelta(days=offset)).isoformat()]
            ]
            top_paths = [
                {"path": row["path"], "pv": int(row["pv"]), "uv": int(row["uv"])}
                for row in conn.execute(
                    "SELECT path, COUNT(*) AS pv, COUNT(DISTINCT ip) AS uv FROM visit_logs WHERE ts >= ?"
                    " GROUP BY path ORDER BY pv DESC LIMIT ?",
                    (cutoff, top),
                ).fetchall()
            ]
            top_ips = [
                {"ip": row["ip"], "pv": int(row["pv"]), "last_seen": float(row["last_seen"])}
                for row in conn.execute(
                    "SELECT ip, COUNT(*) AS pv, MAX(ts) AS last_seen FROM visit_logs WHERE ts >= ?"
                    " GROUP BY ip ORDER BY pv DESC LIMIT ?",
                    (cutoff, top),
                ).fetchall()
            ]
            kpis = {
                "today_pv": scalar("SELECT COUNT(*) FROM visit_logs WHERE ts >= ?", (today0,)),
                "today_uv": scalar("SELECT COUNT(DISTINCT ip) FROM visit_logs WHERE ts >= ?", (today0,)),
                "total_pv": scalar("SELECT COUNT(*) FROM visit_logs"),
                "total_ip": scalar("SELECT COUNT(DISTINCT ip) FROM visit_logs"),
            }

            def dist(column: str) -> list[dict[str, Any]]:
                rows = conn.execute(
                    f"SELECT {column} AS name, COUNT(*) AS pv FROM visit_logs WHERE ts >= ?"
                    " GROUP BY name ORDER BY pv DESC",
                    (cutoff,),
                ).fetchall()
                return [{"name": row["name"], "pv": int(row["pv"])} for row in rows]

            return {
                **kpis,
                "daily": daily,
                "devices": dist("device"),
                "browsers": dist("browser"),
                "oses": dist("os"),
                "top_paths": top_paths,
                "top_ips": top_ips,
            }

    def count_events(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM price_events").fetchone()[0])

    # ---------- 渠道状态时序与变化事件 ----------

    def append_status_records(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO status_records (site_id, captured_at, payload) VALUES (?, ?, ?)",
                [
                    (str(row.get("site_id") or ""), float(row.get("captured_at") or 0.0), _dumps(row))
                    for row in rows
                ],
            )

    def read_status(self, *, limit: int = 200, site_id: str | None = None) -> tuple[list[dict[str, Any]], int]:
        return self._read_rows(
            "status_records", limit=limit, site_id=site_id, kind=None, kind_column="kind", payload_column="payload"
        )

    def latest_status_all(self) -> dict[str, dict[str, Any]]:
        """各站点最新一次状态快照，键为 site_id；没采过状态的站点不出现。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT payload FROM status_records WHERE id IN (SELECT MAX(id) FROM status_records GROUP BY site_id)"
            ).fetchall()
        return {str(row_dict["site_id"]): row_dict for row_dict in (json.loads(row["payload"]) for row in rows)}

    def latest_status(self, site_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload FROM status_records WHERE site_id = ? ORDER BY id DESC LIMIT 1", (site_id,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def append_status_events(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO status_events (site_id, kind, detected_at, payload) VALUES (?, ?, ?, ?)",
                [
                    (
                        str(row.get("site_id") or ""),
                        str(row.get("kind") or ""),
                        float(row.get("detected_at") or 0.0),
                        _dumps(row),
                    )
                    for row in rows
                ],
            )

    def read_status_events(
        self, *, limit: int = 200, site_id: str | None = None, kind: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        return self._read_rows(
            "status_events", limit=limit, site_id=site_id, kind=kind, kind_column="kind", payload_column="payload"
        )

    # ---------- 站点公告版本与变化事件 ----------

    def append_notice_records(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO notice_records (site_id, captured_at, payload) VALUES (?, ?, ?)",
                [
                    (str(row.get("site_id") or ""), float(row.get("captured_at") or 0.0), _dumps(row))
                    for row in rows
                ],
            )

    def read_notice(self, *, limit: int = 200, site_id: str | None = None) -> tuple[list[dict[str, Any]], int]:
        return self._read_rows(
            "notice_records", limit=limit, site_id=site_id, kind=None, kind_column="kind", payload_column="payload"
        )

    def latest_notice(self, site_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload FROM notice_records WHERE site_id = ? ORDER BY id DESC LIMIT 1", (site_id,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def append_notice_events(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO notice_events (site_id, kind, detected_at, payload) VALUES (?, ?, ?, ?)",
                [
                    (
                        str(row.get("site_id") or ""),
                        str(row.get("kind") or ""),
                        float(row.get("detected_at") or 0.0),
                        _dumps(row),
                    )
                    for row in rows
                ],
            )

    def read_notice_events(
        self, *, limit: int = 200, site_id: str | None = None, kind: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        return self._read_rows(
            "notice_events", limit=limit, site_id=site_id, kind=kind, kind_column="kind", payload_column="payload"
        )

    def _read_rows(
        self, table: str, *, limit: int, site_id: str | None, kind: str | None, kind_column: str, payload_column: str
    ) -> tuple[list[dict[str, Any]], int]:
        # limit 来自公开端点的查询参数，钳制上限防止一次请求把整张表读进内存
        limit = max(1, min(limit, _MAX_ROW_LIMIT))
        clauses: list[str] = []
        params: list[Any] = []
        if site_id:
            clauses.append("site_id = ?")
            params.append(site_id)
        if kind:
            clauses.append(f"{kind_column} = ?")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM {table} {where}", params).fetchone()[0])
            rows = conn.execute(
                f"SELECT {payload_column} AS payload FROM {table} {where} ORDER BY id DESC LIMIT ?",
                (*params, max(limit, 0)),
            ).fetchall()
        return [json.loads(row["payload"]) for row in reversed(rows)], total

    # ---------- AI 结果缓存（AIResultCache 协议实现） ----------

    def cache_get(self, key: str) -> dict[str, Any] | None:
        cache = self.get_document("ai_cache")
        value = cache.get(key) if isinstance(cache, dict) else None
        return value if isinstance(value, dict) else None

    def cache_put(self, key: str, result: dict[str, Any]) -> None:
        cache = self.get_document("ai_cache")
        cache = cache if isinstance(cache, dict) else {}
        cache[key] = result
        self.set_document("ai_cache", cache)
