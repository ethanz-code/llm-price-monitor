"""store/schema.py：SQLite DDL、旧库迁移与 latest 快照 key 的编码规则。

key 编码放在这里是因为它和 latest 表结构是同一份契约：key 格式 "{site_id}:{model}:{group}"，
建表、迁移与读写两侧都依赖这个格式，放一起避免口径漂移。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def latest_site_prefix(site_id: str) -> str:
    """该站点全部快照 key 的共同前缀（含结尾冒号）。"""
    return f"{site_id}:"


def latest_key(site_id: str, model: str, group: str) -> str:
    """latest 表快照 key 的唯一编码入口，格式 "{site_id}:{model}:{group}"。

    模型/分组名含 ":" 时仍可编码，但解码（split_latest_key）会把多出的段并进
    model（group 恒取最后一段）；编码侧的告警在 report._event_snapshot_key。
    """
    return f"{site_id}:{model}:{group}"


def split_latest_key(key: str, site_id: str) -> tuple[str, str]:
    """从快照 key 解出 (model, group)：剥掉站点前缀后 group 取最后一段。"""
    rest = key[len(site_id) + 1:]
    model, _, group = rest.rpartition(":")
    return (model or rest), group


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
