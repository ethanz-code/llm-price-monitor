"""store/status.py：渠道状态时序（status_records / status_events）、写入期增量裁剪与历史清理。"""
from __future__ import annotations

import json
from typing import Any

from llm_price_monitor import store as _store_pkg
from llm_price_monitor.timeline import (
    change_matches_groups,
    is_timeline_path,
    prune_status_groups,
    strip_status_delta,
)

from .base import StoreBase, _dumps


class StatusStoreMixin(StoreBase):
    # ---------- 渠道状态时序与变化事件 ----------

    def purge_status(self, before_ts: float) -> int:
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM status_records WHERE captured_at < ?", (before_ts,))
            return int(cursor.rowcount)

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
        per_site = max(1, min(per_site, _store_pkg._MAX_ROW_LIMIT))
        since_clause, since_params = ("WHERE captured_at >= ?", [since]) if since is not None else ("", [])
        with self._conn() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM status_records {since_clause}", since_params).fetchone()[0])
            rows = conn.execute(
                f"""
                SELECT site_id, id, payload FROM (
                    SELECT site_id, id, payload, ROW_NUMBER() OVER (PARTITION BY site_id ORDER BY id DESC) AS rn
                    FROM status_records {since_clause}
                )
                WHERE rn <= ?
                ORDER BY id
                """,
                (*since_params, per_site),
            ).fetchall()
        if max_records is not None and len(rows) > max_records:
            max_records = max(1, min(max_records, _store_pkg._MAX_ROW_LIMIT))
            # 与 _read_rows 同口径按 id 取模均匀抽样（stride 由行数推出，保留分布形状）；
            # 每站最新一条无条件保留——首页/详情页的「最新时段」直接消费它，不能被抽走
            stride = -(-len(rows) // max_records)
            keep: dict[int, Any] = {row["id"]: row for row in rows if row["id"] % stride == 0}
            seen_sites: set[str] = set()
            for row in reversed(rows):
                if row["site_id"] not in seen_sites:
                    seen_sites.add(row["site_id"])
                    keep.setdefault(row["id"], row)
            rows = sorted(keep.values(), key=lambda row: row["id"])
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
        self, *, limit: int = 200, site_id: str | None = None, kind: str | None = None, since: float | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        return self._read_rows(
            "status_events",
            limit=limit,
            site_id=site_id,
            kind=kind,
            kind_column="kind",
            payload_column="payload",
            since=since,
            since_column="detected_at",
        )
