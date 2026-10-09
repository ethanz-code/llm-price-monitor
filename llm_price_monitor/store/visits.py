"""store/visits.py：访问日志（visit_logs）、访客 IP 归属地缓存（ip_geo）与访问统计聚合。"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from typing import Any


class VisitStoreMixin:
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
