"""SQLite 存储层：站点配置、系统设置、价格历史、事件、快照与文档的唯一真相源。

连接策略：每次操作开短连接并在用后关闭，天然跨线程安全（采集任务在后台线程写库）。
库文件路径相对启动工作目录解析，默认 var/monitor.db，与 webapi 其余路径语义一致。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# ai_cache 是整份文档读-改-写，进程内加锁避免并发任务互相覆盖丢条目
_AI_CACHE_LOCK = threading.Lock()

from llm_price_monitor.timeline import (
    TIMELINE_KEYS,
    change_matches_groups,
    is_timeline_path,
    prune_status_groups,
    strip_status_delta,
)

_MAX_ROW_LIMIT = 2000  # 公开读接口单次返回行数上限：limit 入参统一在 _read_rows 钳制，防单请求拖全表

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    config TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_trend (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    model TEXT NOT NULL,
    group_name TEXT NOT NULL DEFAULT 'default',
    input_price REAL,
    output_price REAL,
    unit TEXT NOT NULL DEFAULT '',
    captured_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_trend_site_model ON price_trend(site_id, model);
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
CREATE TABLE IF NOT EXISTS ip_geo (
    ip TEXT PRIMARY KEY,
    province TEXT,
    country TEXT,
    city TEXT,
    ok INTEGER NOT NULL DEFAULT 1,
    resolved_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS site_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    models TEXT,
    contact TEXT,
    ip TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS assistant_usage (
    ip TEXT NOT NULL,
    day TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (ip, day)
);
CREATE TABLE IF NOT EXISTS ai_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    scene TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    error TEXT,
    prompt_excerpt TEXT,
    response_excerpt TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_logs_ts ON ai_logs(ts);
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            self._migrate_price_records(conn)

    @staticmethod
    def _migrate_price_records(conn: sqlite3.Connection) -> None:
        """旧库迁移：price_records 整行 JSON 只为画趋势图服务，压平成 price_trend 精简列后删掉旧表。"""
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "price_records" not in tables:
            return
        rows = conn.execute("SELECT site_id, model, captured_at, record FROM price_records").fetchall()
        migrated = []
        for row in rows:
            try:
                record = json.loads(row["record"])
            except (TypeError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            migrated.append((
                row["site_id"],
                row["model"],
                str((record.get("metadata") or {}).get("group") or "default"),
                record.get("input_price"),
                record.get("output_price"),
                str(record.get("unit") or ""),
                row["captured_at"],
            ))
        conn.executemany(
            "INSERT INTO price_trend (site_id, model, group_name, input_price, output_price, unit, captured_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            migrated,
        )
        conn.execute("DROP TABLE price_records")

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
        """删除站点配置；purge 为真时一并清理该站点的历史价格、事件、状态、公告时序与最新快照。"""
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
            deleted = cursor.rowcount > 0
            if purge and deleted:
                for table in ("price_trend", "price_events", "status_records", "status_events", "notice_records", "notice_events"):
                    conn.execute(f"DELETE FROM {table} WHERE site_id = ?", (site_id,))
                # latest 的 key 形如 "{site_id}:{model}:{group}"，用前缀精确匹配避免 LIKE 通配符歧义
                conn.execute("DELETE FROM latest WHERE substr(key, 1, ?) = ?", (len(site_id) + 1, f"{site_id}:"))
                conn.execute("DELETE FROM documents WHERE name = ?", (f"status_ref:{site_id}",))
                row = conn.execute("SELECT content FROM documents WHERE name = 'collect_status'").fetchone()
                if row:
                    merged = {k: v for k, v in (json.loads(row[0]) or {}).items() if k != site_id}
                    conn.execute(
                        "UPDATE documents SET content = ? WHERE name = 'collect_status'",
                        (json.dumps(merged, ensure_ascii=False),),
                    )
        return deleted

    def rename_site(self, old_id: str, new_id: str) -> bool:
        """站点 id 变更时，把旧 id 名下的全部数据迁移到新 id，保持历史完整。"""
        with self._conn() as conn:
            cursor = conn.execute("UPDATE sites SET id = ? WHERE id = ?", (new_id, old_id))
            renamed = cursor.rowcount > 0
            if renamed:
                for table in ("price_trend", "price_events", "status_records", "status_events", "notice_records", "notice_events"):
                    conn.execute(f"UPDATE {table} SET site_id = ? WHERE site_id = ?", (new_id, old_id))
                # latest 的 key 形如 "{site_id}:{model}:{group}"
                prefix_len = len(old_id) + 1
                conn.execute(
                    "UPDATE latest SET key = ? || substr(key, ?) WHERE substr(key, 1, ?) = ?",
                    (new_id, prefix_len + 1, prefix_len, f"{old_id}:"),
                )
                conn.execute("UPDATE documents SET name = ? WHERE name = ?", (f"status_ref:{new_id}", f"status_ref:{old_id}"))
                row = conn.execute("SELECT content FROM documents WHERE name = 'collect_status'").fetchone()
                if row:
                    merged = json.loads(row[0]) or {}
                    if old_id in merged:
                        merged[new_id] = merged.pop(old_id)
                        conn.execute(
                            "UPDATE documents SET content = ? WHERE name = 'collect_status'",
                            (json.dumps(merged, ensure_ascii=False),),
                        )
        return renamed

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
        """趋势点入库：只存画图需要的精简列，不再整行 JSON（采集记录页已下线）。"""
        if not rows:
            return
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO price_trend (site_id, model, group_name, input_price, output_price, unit, captured_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        str(row.get("site_id") or ""),
                        str(row.get("model") or ""),
                        str((row.get("metadata") or {}).get("group") or "default"),
                        row.get("input_price"),
                        row.get("output_price"),
                        str(row.get("unit") or ""),
                        float(row.get("captured_at") or 0.0),
                    )
                    for row in rows
                ],
            )

    def read_history(
        self, *, limit: int, site_id: str | None = None, model: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        limit = max(1, min(limit, _MAX_ROW_LIMIT))
        clauses: list[str] = []
        params: list[Any] = []
        if site_id:
            clauses.append("site_id = ?")
            params.append(site_id)
        if model:
            clauses.append("model = ?")
            params.append(model)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM price_trend {where}", params).fetchone()[0])
            rows = conn.execute(
                f"SELECT site_id, model, group_name, input_price, output_price, unit, captured_at"
                f" FROM price_trend {where} ORDER BY id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        records = [
            {
                "site_id": row["site_id"],
                "model": row["model"],
                "group": row["group_name"],
                "input_price": row["input_price"],
                "output_price": row["output_price"],
                "unit": row["unit"],
                "captured_at": row["captured_at"],
            }
            for row in reversed(rows)
        ]
        return records, total

    def count_history(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM price_trend").fetchone()[0])

    def purge_history(self, before_ts: float) -> int:
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM price_trend WHERE captured_at < ?", (before_ts,))
            return int(cursor.rowcount)

    def purge_status(self, before_ts: float) -> int:
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM status_records WHERE captured_at < ?", (before_ts,))
            return int(cursor.rowcount)

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

    # ---------- 站点提交 ----------

    def add_site_submission(
        self, *, name: str, url: str, models: str | None, contact: str | None, ip: str
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO site_submissions (name, url, models, contact, ip, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, 'new', ?)",
                (name, url, models, contact, ip, time.time()),
            )

    def list_site_submissions(
        self, *, limit: int = 100, offset: int = 0, status: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        limit = max(1, min(limit, _MAX_ROW_LIMIT))
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM site_submissions {where}", params).fetchone()[0])
            rows = conn.execute(
                f"SELECT * FROM site_submissions {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows], total

    def set_site_submission_status(self, submission_id: int, status: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE site_submissions SET status = ? WHERE id = ?", (status, submission_id)
            )
        return cursor.rowcount > 0

    # ---------- AI 助手配额（SQLite 计数：重启不丢、并发不互相覆盖） ----------

    def quota_used(self, ip: str, day: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT count FROM assistant_usage WHERE ip = ? AND day = ?", (ip, day)
            ).fetchone()
        return int(row["count"]) if row else 0

    def record_quota(self, ip: str, day: str) -> None:
        """计数 +1；顺带清理历史日期的行，避免表随天数无限增长。"""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO assistant_usage (ip, day, count) VALUES (?, ?, 1)"
                " ON CONFLICT(ip, day) DO UPDATE SET count = count + 1",
                (ip, day),
            )
            conn.execute("DELETE FROM assistant_usage WHERE day < ?", (day,))

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

    # ---------- AI 请求日志 ----------

    # 日志保留天数：写入时顺带清理，超过即淘汰
    AI_LOG_RETENTION_DAYS = 7

    def add_ai_log(
        self,
        *,
        scene: str,
        model: str,
        status: str,
        duration_ms: int,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        error: str | None = None,
        prompt_excerpt: str | None = None,
        response_excerpt: str | None = None,
    ) -> None:
        with self._conn() as conn:
            now = time.time()
            conn.execute(
                "INSERT INTO ai_logs (ts, scene, model, status, duration_ms, prompt_tokens, completion_tokens,"
                " total_tokens, error, prompt_excerpt, response_excerpt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now,
                    scene,
                    model,
                    status,
                    duration_ms,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    error,
                    prompt_excerpt,
                    response_excerpt,
                ),
            )
            conn.execute("DELETE FROM ai_logs WHERE ts < ?", (now - self.AI_LOG_RETENTION_DAYS * 86400,))

    def read_ai_logs(
        self, *, limit: int = 100, offset: int = 0, scene: str | None = None, status: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = []
        params: list[Any] = []
        if scene:
            conditions.append("scene = ?")
            params.append(scene)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._conn() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM ai_logs{where}", params).fetchone()[0])
            rows = conn.execute(
                f"SELECT * FROM ai_logs{where} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [dict(row) for row in rows], total

    def purge_ai_logs(self, before_ts: float) -> int:
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM ai_logs WHERE ts < ?", (before_ts,))
            return int(cursor.rowcount)

    # ---------- 访客 IP 归属地 ----------

    def pending_geo_ips(self, *, cutoff: float, limit: int = 100, fail_ttl: float = 86400.0) -> list[str]:
        """取近窗口内还没有归属地缓存（或上次解析失败超过 fail_ttl）的 IP，供批量解析。"""
        now = time.time()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT v.ip FROM visit_logs v"
                " LEFT JOIN ip_geo g ON g.ip = v.ip"
                " WHERE v.ts >= ? AND v.ip NOT LIKE '%:%'"
                " AND (g.ip IS NULL OR (g.ok = 0 AND g.resolved_at < ?))"
                " LIMIT ?",
                (cutoff, now - fail_ttl, limit),
            ).fetchall()
        return [row["ip"] for row in rows]

    def save_ip_geo(self, records: dict[str, dict[str, Any]], failed: list[str]) -> None:
        """写入归属地缓存：成功记录带省份；失败只记 ok=0，等 fail_ttl 过后重试。"""
        now = time.time()
        with self._conn() as conn:
            for ip, rec in records.items():
                conn.execute(
                    "INSERT INTO ip_geo (ip, province, country, city, ok, resolved_at) VALUES (?, ?, ?, ?, 1, ?)"
                    " ON CONFLICT(ip) DO UPDATE SET province=excluded.province, country=excluded.country,"
                    " city=excluded.city, ok=1, resolved_at=excluded.resolved_at",
                    (ip, rec.get("province"), rec.get("country"), rec.get("city"), now),
                )
            for ip in failed:
                conn.execute(
                    "INSERT INTO ip_geo (ip, province, country, city, ok, resolved_at) VALUES (?, NULL, NULL, NULL, 0, ?)"
                    " ON CONFLICT(ip) DO UPDATE SET ok=0, resolved_at=excluded.resolved_at",
                    (ip, now),
                )

    def region_dist(self, *, cutoff: float, top: int = 60) -> list[dict[str, Any]]:
        """近窗口内按国家聚合的访问分布（PV/UV），供世界地图着色；无归属地的 IP 计入"未知"。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT COALESCE(NULLIF(g.country, ''), '未知') AS name, COUNT(*) AS pv, COUNT(DISTINCT v.ip) AS uv"
                " FROM visit_logs v LEFT JOIN ip_geo g ON g.ip = v.ip"
                " WHERE v.ts >= ? GROUP BY name ORDER BY pv DESC LIMIT ?",
                (cutoff, top),
            ).fetchall()
        return [{"name": row["name"], "pv": int(row["pv"]), "uv": int(row["uv"])} for row in rows]

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
        """状态快照入库前先做增量裁剪：与该站点上一条原始快照（documents 里的参照）比对，
        把各分组时间线里已存过的检测点剔除，只留新增部分。时间线是站点原样返回的滚动窗口，
        原样入库会让每条快照都拖着整份历史（实测九成以上体积是重复），库和读取接口一起膨胀。
        参照快照始终保留未裁剪原文，采集期 diff 与下轮裁剪都以它为基准。"""
        if not rows:
            return
        refs: dict[str, Any] = {}
        dirty: set[str] = set()
        prepared: list[tuple[str, float, str]] = []
        for row in sorted(rows, key=lambda item: float(item.get("captured_at") or 0.0)):
            site_id = str(row.get("site_id") or "")
            data = row.get("data")
            if site_id not in refs:
                reference = self.status_reference(site_id)
                refs[site_id] = reference.get("data") if isinstance(reference, dict) else None
            stripped = strip_status_delta(refs[site_id], data)
            if stripped is not data:
                row = {**row, "data": stripped}
            refs[site_id] = data
            dirty.add(site_id)
            prepared.append((site_id, float(row.get("captured_at") or 0.0), _dumps(row)))
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO status_records (site_id, captured_at, payload) VALUES (?, ?, ?)",
                prepared,
            )
        for site_id in dirty:
            self.set_document(f"status_ref:{site_id}", {"data": refs[site_id]})

    def status_reference(self, site_id: str) -> dict[str, Any] | None:
        """该站点上一条原始（未裁剪）状态 data：采集期 diff 的基准，也是写入期裁剪的对照。"""
        document = self.get_document(f"status_ref:{site_id}")
        return document if isinstance(document, dict) and "data" in document else None

    def compact_status_history(self) -> dict[str, int]:
        """一次性压缩既有 status_records：逐站点按时间正序，把每条快照裁剪成相对上一条的增量；
        同步清洗 status_events 里时间线错位产生的噪音 change（清洗后无变化的事件删除）。
        供旧库升级用，新库走 append_status_records 的写入期裁剪即可；重复执行安全（最多少裁几条，不会丢数据）。"""
        stats = {"records": 0, "stripped": 0, "events": 0, "events_removed": 0}
        refs: dict[str, Any] = {}
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, site_id, payload FROM status_records ORDER BY site_id, captured_at, id"
            ).fetchall()
            updates: list[tuple[str, int]] = []
            for row in rows:
                site_id = row["site_id"]
                record = json.loads(row["payload"])
                data = record.get("data")
                stripped = strip_status_delta(refs.get(site_id), data)
                if stripped is not data:
                    record["data"] = stripped
                    stats["stripped"] += 1
                updates.append((_dumps(record), row["id"]))
                refs[site_id] = data
                stats["records"] += 1
            conn.executemany("UPDATE status_records SET payload = ? WHERE id = ?", updates)
            for row in conn.execute("SELECT id, payload FROM status_events").fetchall():
                event = json.loads(row["payload"])
                changes = [c for c in event.get("changes", []) if not is_timeline_path(c.get("path"))]
                if len(changes) != len(event.get("changes", [])):
                    stats["events"] += 1
                    if changes:
                        event["changes"] = changes
                        conn.execute("UPDATE status_events SET payload = ? WHERE id = ?", (_dumps(event), row["id"]))
                    else:
                        conn.execute("DELETE FROM status_events WHERE id = ?", (row["id"],))
                        stats["events_removed"] += 1
        for site_id, data in refs.items():
            if data is not None:
                self.set_document(f"status_ref:{site_id}", {"data": data})
        return stats

    def prune_status_history(self, site_id: str, groups: list[str]) -> dict[str, int]:
        """按分组过滤口径清理该站点已入库的状态历史：快照与参照快照里未选中分组的渠道条目
        直接裁掉；状态事件里指向已裁剪渠道的变化逐条剔除，剔完无变化的整条删除。
        供保存站点分组过滤配置时调用，幂等；返回清理统计。"""
        stats = {"records": 0, "ref": 0, "events": 0, "events_removed": 0}
        # 事件变化的路径指向裁剪前的数据结构，先留一份未裁剪参照用于路径归因
        reference = self.status_reference(site_id)
        old_ref_data = reference.get("data") if isinstance(reference, dict) else None
        with self._conn() as conn:
            updates: list[tuple[str, int]] = []
            for row in conn.execute(
                "SELECT id, payload FROM status_records WHERE site_id = ? ORDER BY id", (site_id,)
            ).fetchall():
                record = json.loads(row["payload"])
                pruned, _ = prune_status_groups(record.get("data"), groups)
                record["data"] = pruned
                if (dumped := _dumps(record)) != row["payload"]:
                    stats["records"] += 1
                    updates.append((dumped, row["id"]))
            conn.executemany("UPDATE status_records SET payload = ? WHERE id = ?", updates)

            event_updates: list[tuple[str, int]] = []
            event_deletes: list[int] = []
            for row in conn.execute(
                "SELECT id, payload FROM status_events WHERE site_id = ? ORDER BY id", (site_id,)
            ).fetchall():
                event = json.loads(row["payload"])
                kept = [
                    change
                    for change in event.get("changes", [])
                    if change_matches_groups(change, old_ref_data, groups)
                ]
                if len(kept) == len(event.get("changes", [])):
                    continue
                stats["events"] += 1
                if kept:
                    event["changes"] = kept
                    event_updates.append((_dumps(event), row["id"]))
                else:
                    event_deletes.append(row["id"])
                    stats["events_removed"] += 1
            conn.executemany("UPDATE status_events SET payload = ? WHERE id = ?", event_updates)
            if event_deletes:
                conn.execute(
                    f"DELETE FROM status_events WHERE id IN ({','.join('?' * len(event_deletes))})", event_deletes
                )

        if isinstance(reference, dict) and "data" in reference:
            pruned, _ = prune_status_groups(reference["data"], groups)
            if pruned != reference["data"]:
                reference["data"] = pruned
                self.set_document(f"status_ref:{site_id}", reference)
                stats["ref"] = 1
        return stats

    def read_status(
        self,
        *,
        limit: int = 200,
        site_id: str | None = None,
        per_site: int | None = None,
        since: float | None = None,
        max_records: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """渠道状态时序；per_site 按站点分组各取最近 N 条，since 只取该时间之后的快照（时间条件下推到 SQL）。
        max_records 给出希望返回的行数上限：窗口内行数超出时按 id 取模均匀抽样，响应体积保持可控。"""
        if per_site is None or site_id:
            return self._read_rows(
                "status_records",
                limit=limit,
                site_id=site_id,
                kind=None,
                kind_column="kind",
                payload_column="payload",
                since=since,
                max_records=max_records,
            )
        per_site = max(1, min(per_site, _MAX_ROW_LIMIT))
        since_clause, since_params = ("WHERE captured_at >= ?", [since]) if since is not None else ("", [])
        with self._conn() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM status_records {since_clause}", since_params).fetchone()[0])
            rows = conn.execute(
                f"""
                SELECT payload FROM (
                    SELECT id, payload, ROW_NUMBER() OVER (PARTITION BY site_id ORDER BY id DESC) AS rn
                    FROM status_records {since_clause}
                )
                WHERE rn <= ?
                ORDER BY id
                """,
                (*since_params, per_site),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows], total

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
        self,
        table: str,
        *,
        limit: int,
        site_id: str | None,
        kind: str | None,
        kind_column: str,
        payload_column: str,
        since: float | None = None,
        max_records: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        # limit 来自公开端点的查询参数，钳制上限防止一次请求把整张表读进内存
        limit = max(1, min(limit, _MAX_ROW_LIMIT))
        max_records = max(1, min(max_records, _MAX_ROW_LIMIT)) if max_records is not None else None
        clauses: list[str] = []
        params: list[Any] = []
        if site_id:
            clauses.append("site_id = ?")
            params.append(site_id)
        if kind:
            clauses.append(f"{kind_column} = ?")
            params.append(kind)
        if since is not None:
            clauses.append("captured_at >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM {table} {where}", params).fetchone()[0])
            # 窗口内行数超过 max_records 时按 id 取模均匀抽样：stride 由服务端算出，不是用户输入
            stride = 1
            if max_records is not None and total > max_records:
                stride = -(-total // max_records)
            sampled = clauses + ([f"(id % {stride}) = 0"] if stride > 1 else [])
            where = f"WHERE {' AND '.join(sampled)}" if sampled else ""
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
        # 整份 ai_cache 文档是读-改-写，进程内加锁避免并发任务互相覆盖丢条目
        with _AI_CACHE_LOCK:
            cache = self.get_document("ai_cache")
            cache = cache if isinstance(cache, dict) else {}
            cache[key] = result
            self.set_document("ai_cache", cache)
