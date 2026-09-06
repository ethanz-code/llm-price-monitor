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
from pathlib import Path
from typing import Any

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

    def delete_site(self, site_id: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        return cursor.rowcount > 0

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

    # ---------- 设置与文档（settings / ai / official_prices / ai_cache / seeded） ----------

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

    def count_latest(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM latest").fetchone()[0])

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

    def count_events(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM price_events").fetchone()[0])

    def _read_rows(
        self, table: str, *, limit: int, site_id: str | None, kind: str | None, kind_column: str, payload_column: str
    ) -> tuple[list[dict[str, Any]], int]:
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
