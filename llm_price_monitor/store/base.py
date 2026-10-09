"""store/base.py：连接管理、JSON 序列化与公开读接口共用的行读取助手。

_MAX_ROW_LIMIT 定义在包 __init__（tests/test_store.py 对 `llm_price_monitor.store._MAX_ROW_LIMIT`
做 monkeypatch），这里通过包属性运行时回查，保证打补丁对子模块生效。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from llm_price_monitor import store as _store_pkg

from .schema import _SCHEMA, _migrate_price_records


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


class StoreBase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            self._migrate_price_records(conn)

    _migrate_price_records = staticmethod(_migrate_price_records)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _read_rows(
        self,
        table: str,
        *,
        limit: int,
        site_id: str | None,
        kind: str | None,
        kind_column: str,
        payload_column: str,
        exclude_kind: str | None = None,
        since: float | None = None,
        since_column: str = "captured_at",
        max_records: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        # limit 来自公开端点的查询参数，钳制上限防止一次请求把整张表读进内存
        limit = max(1, min(limit, _store_pkg._MAX_ROW_LIMIT))
        max_records = max(1, min(max_records, _store_pkg._MAX_ROW_LIMIT)) if max_records is not None else None
        clauses: list[str] = []
        params: list[Any] = []
        if site_id:
            clauses.append("site_id = ?")
            params.append(site_id)
        if kind:
            clauses.append(f"{kind_column} = ?")
            params.append(kind)
        if exclude_kind:
            clauses.append(f"{kind_column} != ?")
            params.append(exclude_kind)
        if since is not None:
            clauses.append(f"{since_column} >= ?")
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
