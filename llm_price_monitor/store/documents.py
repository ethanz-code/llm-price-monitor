"""store/documents.py：documents 文档表（settings / ai / catalog / ai_cache / seeded 等的唯一 KV 层）
与 latest 最新快照表。"""
from __future__ import annotations

import json
import time
from typing import Any

from .base import StoreBase, _dumps


class DocumentStoreMixin(StoreBase):
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

