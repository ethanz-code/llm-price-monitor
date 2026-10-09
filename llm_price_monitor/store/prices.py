"""store/prices.py：价格趋势点、价格事件与按分组清理价格数据。"""
from __future__ import annotations

import json
from typing import Any

from llm_price_monitor import store as _store_pkg

from .base import StoreBase, _dumps
from .schema import latest_site_prefix, split_latest_key


def _price_event_group(payload: dict[str, Any]) -> str:
    """价格事件的分组归属：current/previous 里的 metadata.group，都没有视为 default。"""
    for part in (payload.get("current"), payload.get("previous")):
        if isinstance(part, dict):
            group = (part.get("metadata") or {}).get("group")
            if group:
                return str(group)
    return "default"


class PriceStoreMixin(StoreBase):
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
        limit = max(1, min(limit, _store_pkg._MAX_ROW_LIMIT))
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

    def purge_price_models(self, keep_models: set[str]) -> int:
        """清掉不在监控清单里的模型的价格数据：latest 快照、趋势点与价格事件。

        目录刷新的清单维护后对账调用——模型被移出清单（手动删、超期、滚出前 N）后
        不再采集，残留的快照会让数据列表出现"早已不存在"的幽灵模型。返回清除的
        latest 行数。keep_models 为空时不删（清单还没初始化，不误伤）。
        """
        if not keep_models:
            return 0
        placeholders = ",".join("?" * len(keep_models))
        args = tuple(keep_models)
        with self._conn() as conn:
            cursor = conn.execute(
                f"DELETE FROM latest WHERE json_extract(record, '$.model') NOT IN ({placeholders})",
                args,
            )
            removed = int(cursor.rowcount)
            conn.execute(f"DELETE FROM price_trend WHERE model NOT IN ({placeholders})", args)
            conn.execute(f"DELETE FROM price_events WHERE model NOT IN ({placeholders})", args)
            return removed

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
        self,
        *,
        limit: int,
        site_id: str | None = None,
        kind: str | None = None,
        exclude_kind: str | None = None,
        since: float | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        return self._read_rows(
            "price_events",
            limit=limit,
            site_id=site_id,
            kind=kind,
            exclude_kind=exclude_kind,
            kind_column="kind",
            payload_column="payload",
            since=since,
            since_column="detected_at",
        )

    def read_price_event_rows(self) -> list[dict[str, Any]]:
        """管理端全量读取价格事件（行 id + payload 合并）：维护类命令复判/清理用，不走公开接口的行数钳制。"""
        with self._conn() as conn:
            rows = conn.execute("SELECT id, payload FROM price_events ORDER BY id").fetchall()
        return [{"id": row["id"], **json.loads(row["payload"])} for row in rows]

    def delete_price_events(self, ids: list[int]) -> int:
        """按行 id 删除价格事件，返回实际删除条数；仅供维护命令调用，公开端点不得触达。"""
        if not ids:
            return 0
        with self._conn() as conn:
            cursor = conn.executemany("DELETE FROM price_events WHERE id = ?", [(int(i),) for i in ids])
            return int(cursor.rowcount)

    def count_events(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM price_events").fetchone()[0])

    def prune_price_groups(self, site_id: str, groups: list[str]) -> dict[str, int]:
        """按分组过滤口径清理该站点已入库的价格数据：最新快照、历史趋势点与价格事件里
        未选中分组的行直接删除，分组缺失计数同步清掉。供保存分组配置时调用，幂等；返回清理统计。"""
        stats = {"latest": 0, "trend": 0, "events": 0}
        targets = {group.strip().casefold() for group in groups if group.strip()}
        if not targets:
            return stats
        site_prefix = latest_site_prefix(site_id)

        def in_targets(group: Any) -> bool:
            return str(group or "default").strip().casefold() in targets

        with self._conn() as conn:
            # 快照：分组是 key 的最后一段（格式见 latest_key）
            stale_keys = [
                row["key"]
                for row in conn.execute(
                    "SELECT key FROM latest WHERE substr(key, 1, ?) = ?", (len(site_prefix), site_prefix)
                ).fetchall()
                if not in_targets(split_latest_key(row["key"], site_id)[1])
            ]
            if stale_keys:
                conn.executemany("DELETE FROM latest WHERE key = ?", [(key,) for key in stale_keys])
                stats["latest"] = len(stale_keys)

            # 历史趋势点：group_name 列直接比对
            for row in conn.execute(
                "SELECT DISTINCT group_name FROM price_trend WHERE site_id = ?", (site_id,)
            ).fetchall():
                if in_targets(row["group_name"]):
                    continue
                cursor = conn.execute(
                    "DELETE FROM price_trend WHERE site_id = ? AND group_name = ?", (site_id, row["group_name"])
                )
                stats["trend"] += cursor.rowcount

            # 价格事件：分组藏在 payload 的 previous/current.metadata.group 里
            stale_event_ids = [
                row["id"]
                for row in conn.execute("SELECT id, payload FROM price_events WHERE site_id = ?", (site_id,)).fetchall()
                if not in_targets(_price_event_group(json.loads(row["payload"])))
            ]
            if stale_event_ids:
                conn.execute(
                    f"DELETE FROM price_events WHERE id IN ({','.join('?' * len(stale_event_ids))})", stale_event_ids
                )
                stats["events"] = len(stale_event_ids)

        # 分组缺失计数：被过滤分组的 key 一并清掉，避免重新启用白名单时带着旧计数
        watch = self.get_document("group_miss")
        if isinstance(watch, dict) and any(key.startswith(site_prefix) for key in watch):
            pruned_watch = {
                key: count
                for key, count in watch.items()
                if not key.startswith(site_prefix) or in_targets(split_latest_key(key, site_id)[1])
            }
            if pruned_watch != watch:
                self.set_document("group_miss", pruned_watch)
        return stats

    def distinct_price_groups(self, site_id: str) -> list[str]:
        """该站点已入库价格数据里出现过的分组名（去重升序），供管理台分组白名单下拉勾选。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT group_name FROM price_trend WHERE site_id = ? ORDER BY group_name", (site_id,)
            ).fetchall()
        return [row["group_name"] for row in rows if row["group_name"]]
