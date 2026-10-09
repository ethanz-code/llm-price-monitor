"""store/ai.py：AI 请求日志、模型 max_tokens 上限记忆、报错分组与 AI 结果缓存（AIResultCache 协议实现）。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

# 整份文档读-改-写（ai_cache / ai_model_limits / ai_thinking_models）进程内加锁，
# 避免并发任务互相覆盖丢条目：批间 4 路并行 × 站点 4 路并行时，两个线程同时
# 各学到一个模型的上限/思考标记是真实场景，无锁时后写覆盖先写
_AI_CACHE_LOCK = threading.Lock()

# 缓存保留：单条结果几 KB 到几十 KB，条目键随页面内容变，旧键自然失效却永远留在文档里；
# 写入时按时间戳淘汰过期条目，并限制总条数（保留最新），防止整份文档无限膨胀
_AI_CACHE_TTL_SECONDS = 30 * 86400
_AI_CACHE_MAX_ENTRIES = 300

# AI 日志里 prompt / 回复 / 错误原文的入库截断长度：完整证据可能几十万字符，整段入库会撑爆库；
# 上限取 4000 是详情弹窗够看的折中——太长的 prompt（整页证据）仍只留开头，以「…」标注
_AI_LOG_TEXT_CHARS = 4000

# 报错分组的正文截取长度：同一种报错开头一致（403 免费额度、enable_thinking 拒收等），
# 请求 ID、具体数值等差异都在更靠后的位置，取头部即可稳定归组
_AI_ERROR_KIND_HEAD = 60


def ai_error_kind(error: str | None) -> str:
    """报错分组键：HTTP 状态码 + 报错正文开头（空白归一后截 60 字符）；没有报错文案返回空串。

    「报错判定」按这个键把历史报错归组勾选，键会存进 settings.ai_ignored_errors，
    所以规则必须只依赖入库文案本身，不随查询时间变化。
    """
    if not error:
        return ""
    text = " ".join(error.strip().split())
    prefix = ""
    if text.startswith("HTTP ") and len(text) > 8 and text[5:8].isdigit():
        prefix = text[:8]
        text = text[9:]
    return f"{prefix} {text[:_AI_ERROR_KIND_HEAD]}".strip()


def _clip_ai_log_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:_AI_LOG_TEXT_CHARS] + ("…" if len(value) > _AI_LOG_TEXT_CHARS else "")


class AIStoreMixin:
    # ---------- AI 请求日志 ----------

    # 日志保留天数兜底值：settings.retention_ai_log_days 未配置时生效；写入时顺带清理，超过即淘汰
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
        error = _clip_ai_log_text(error)
        prompt_excerpt = _clip_ai_log_text(prompt_excerpt)
        response_excerpt = _clip_ai_log_text(response_excerpt)
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
            conn.execute("DELETE FROM ai_logs WHERE ts < ?", (now - self._ai_log_retention_days(conn) * 86400,))

    def get_model_limits(self) -> dict[str, int]:
        """已学到的各模型 max_tokens 上限（ai_model_limits 文档）：AI 层启动预载，重启不重学。"""
        doc = self.get_document("ai_model_limits")
        return {str(k): int(v) for k, v in doc.items() if isinstance(v, int) and v > 0} if isinstance(doc, dict) else {}

    def save_model_limit(self, model: str, limit: int) -> None:
        """增量记录一个模型学到的 max_tokens 上限：学习事件极低频（每模型至多一次 400），不缓存。"""
        with _AI_CACHE_LOCK:  # 整档读-改-写，批并行下多线程同时学习不能互相覆盖
            doc = self.get_document("ai_model_limits")
            doc = dict(doc) if isinstance(doc, dict) else {}
            doc[model] = limit
            self.set_document("ai_model_limits", doc)

    def get_thinking_models(self) -> list[str]:
        """已学到思考不可关的模型名（ai_thinking_models 文档）：AI 层启动预载，重启不重学。"""
        doc = self.get_document("ai_thinking_models")
        return [str(name) for name in doc if name] if isinstance(doc, list) else []

    def remember_thinking_model(self, model: str) -> None:
        """增量记录一个拒收 enable_thinking=false 的模型：学习事件极低频（每模型至多一次 400），不缓存。"""
        with _AI_CACHE_LOCK:  # 整档读-改-写，批并行下多线程同时学习不能互相覆盖
            doc = self.get_document("ai_thinking_models")
            items = [str(name) for name in doc if name] if isinstance(doc, list) else []
            if model not in items:
                items.append(model)
                self.set_document("ai_thinking_models", items)

    def _ai_log_retention_days(self, conn: sqlite3.Connection) -> int:
        """AI 日志保留天数：settings.retention_ai_log_days（保存期已校验不小于 1），未配置回默认 7。"""
        row = conn.execute("SELECT content FROM documents WHERE name = 'settings'").fetchone()
        if row:
            value = (json.loads(row[0]) or {}).get("retention_ai_log_days")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                return value
        return self.AI_LOG_RETENTION_DAYS

    def _ai_ignored_error_kinds(self, conn: sqlite3.Connection) -> frozenset[str]:
        """「报错判定」里被勾成不算失败的报错组：settings.ai_ignored_errors（ai_error_kind 的键）。"""
        row = conn.execute("SELECT content FROM documents WHERE name = 'settings'").fetchone()
        if row:
            value = (json.loads(row[0]) or {}).get("ai_ignored_errors")
            if isinstance(value, list):
                return frozenset(item for item in value if isinstance(item, str) and item)
        return frozenset()

    # 列表查询不带回的重量列：摘要正文（最长 4000 字符×2）只走单条详情接口，避免列表全量拉取时响应爆炸
    _AI_LOG_LIST_COLUMNS = (
        "id, ts, scene, model, status, duration_ms,"
        " prompt_tokens, completion_tokens, total_tokens, error"
    )

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
                f"SELECT {self._AI_LOG_LIST_COLUMNS} FROM ai_logs{where} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [dict(row) for row in rows], total

    def read_ai_log(self, log_id: int) -> dict[str, Any] | None:
        """单条 AI 日志全文（含 prompt/回复摘要）：详情弹窗按需取用。"""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM ai_logs WHERE id = ?", (log_id,)).fetchone()
        return dict(row) if row else None

    def ai_logs_summary(self, *, trend_days: int = 7) -> dict[str, Any]:
        """AI 调用统计聚合：全部保留记录的 KPI、按天趋势与场景/模型分布。

        成功率 = ok / (total - transport - ignored)，即成功尝试占「有效尝试」的比例。每次请求尝试各记
        一条日志：报错后换模型（fallback）或换参数（param_retry）重试的尝试是失败的一种，重试成功的
        另记一条 ok；模型池全部失败的整次失败记 error 行，同样计入失败。两类东西不算失败也从分母剔除：
        连接抖动（transport，超时/SSL 断开等未收到响应），以及「报错判定」里被勾掉不算的报错组
        （settings.ai_ignored_errors，分组键见 ai_error_kind）。
        按天序列补零对齐，日期统一用本地时区（与 visit_summary 的口径一致）。
        """
        today0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        cutoff = today0 - (trend_days - 1) * 86400
        with self._conn() as conn:
            ignored_kinds = self._ai_ignored_error_kinds(conn)
            kind_counts: dict[str, int] = {}
            kind_samples: dict[str, str] = {}
            ignored_rows = 0
            for row in conn.execute("SELECT status, error FROM ai_logs").fetchall():
                if row["status"] in ("ok", "transport"):
                    continue
                kind = ai_error_kind(row["error"])
                if not kind:
                    continue
                kind_counts[kind] = kind_counts.get(kind, 0) + 1
                kind_samples.setdefault(kind, str(row["error"] or "")[:120])
                if kind in ignored_kinds:
                    ignored_rows += 1
            error_kinds = [
                {"key": key, "count": count, "sample": kind_samples[key], "ignored": key in ignored_kinds}
                for key, count in sorted(kind_counts.items(), key=lambda item: (-item[1], item[0]))
            ]
            totals = conn.execute(
                "SELECT COUNT(*) AS total,"
                " COALESCE(SUM(status = 'ok'), 0) AS ok,"
                " COALESCE(SUM(status = 'fallback'), 0) AS fallback,"
                " COALESCE(SUM(status = 'param_retry'), 0) AS param_retry,"
                " COALESCE(SUM(status = 'transport'), 0) AS transport,"
                " COALESCE(SUM(status = 'error'), 0) AS error,"
                " COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,"
                " COALESCE(SUM(completion_tokens), 0) AS completion_tokens,"
                " COALESCE(SUM(total_tokens), 0) AS total_tokens,"
                " COALESCE(AVG(duration_ms), 0) AS avg_duration_ms"
                " FROM ai_logs"
            ).fetchone()
            by_day = {
                row["day"]: (
                    int(row["ok"]),
                    int(row["fallback"]),
                    int(row["param_retry"]),
                    int(row["transport"]),
                    int(row["error"]),
                    int(row["prompt_tokens"]),
                    int(row["completion_tokens"]),
                )
                for row in conn.execute(
                    "SELECT date(ts, 'unixepoch', 'localtime') AS day,"
                    " COALESCE(SUM(status = 'ok'), 0) AS ok,"
                    " COALESCE(SUM(status = 'fallback'), 0) AS fallback,"
                    " COALESCE(SUM(status = 'param_retry'), 0) AS param_retry,"
                    " COALESCE(SUM(status = 'transport'), 0) AS transport,"
                    " COALESCE(SUM(status = 'error'), 0) AS error,"
                    " COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,"
                    " COALESCE(SUM(completion_tokens), 0) AS completion_tokens"
                    " FROM ai_logs WHERE ts >= ? GROUP BY day",
                    (cutoff,),
                )
            }
            scenes = [
                {"name": row["scene"], "calls": int(row["calls"])}
                for row in conn.execute(
                    "SELECT scene, COUNT(*) AS calls FROM ai_logs GROUP BY scene ORDER BY calls DESC, scene LIMIT 10"
                )
            ]
            models = [
                {"name": row["model"], "calls": int(row["calls"])}
                for row in conn.execute(
                    "SELECT model, COUNT(*) AS calls FROM ai_logs GROUP BY model ORDER BY calls DESC, model LIMIT 10"
                )
            ]
        today = date.fromtimestamp(time.time())
        zeros = (0, 0, 0, 0, 0, 0, 0)
        daily = [
            {
                "day": (today - timedelta(days=offset)).isoformat()[5:],
                "ok": values[0],
                "fallback": values[1],
                "param_retry": values[2],
                "transport": values[3],
                "error": values[4],
                "prompt_tokens": values[5],
                "completion_tokens": values[6],
            }
            for offset in range(trend_days - 1, -1, -1)
            for values in [by_day.get((today - timedelta(days=offset)).isoformat(), zeros)]
        ]
        total = int(totals["total"])
        answered = total - int(totals["transport"]) - ignored_rows
        return {
            "total": total,
            # 成功率口径见 docstring：ok / (total - transport - ignored)，没有有效尝试时为 None
            "success_rate": round(int(totals["ok"]) / answered * 100, 1) if answered else None,
            "ok": int(totals["ok"]),
            "fallback": int(totals["fallback"]),
            "param_retry": int(totals["param_retry"]),
            "transport": int(totals["transport"]),
            "error": int(totals["error"]),
            # 「报错判定」里被勾成不算失败的报错组涉及的尝试条数：不计失败、不进成功率分母
            "ignored": ignored_rows,
            "error_kinds": error_kinds,
            "prompt_tokens": int(totals["prompt_tokens"]),
            "completion_tokens": int(totals["completion_tokens"]),
            "total_tokens": int(totals["total_tokens"]),
            "avg_duration_ms": round(float(totals["avg_duration_ms"])),
            "daily": daily,
            "scenes": scenes,
            "models": models,
        }

    # ---------- AI 结果缓存（AIResultCache 协议实现） ----------

    def cache_get(self, key: str) -> dict[str, Any] | None:
        cache = self.get_document("ai_cache")
        value = cache.get(key) if isinstance(cache, dict) else None
        if not isinstance(value, dict):
            return None
        # 新条目包一层 {"cached_at", "result"}；旧条目本身就是结果，直接透传
        result = value.get("result", value)
        return result if isinstance(result, dict) else None

    def cache_put(self, key: str, result: dict[str, Any]) -> None:
        # 整份 ai_cache 文档是读-改-写，进程内加锁避免并发任务互相覆盖丢条目
        with _AI_CACHE_LOCK:
            cache = self.get_document("ai_cache")
            cache = cache if isinstance(cache, dict) else {}
            now = time.time()
            cache[key] = {"cached_at": now, "result": result}
            kept: dict[str, dict[str, Any]] = {}
            for entry_key, entry in cache.items():
                if not isinstance(entry, dict):
                    continue
                cached_at = entry.get("cached_at")
                if cached_at is None:
                    # 旧条目没有时间戳：补当前时间从本轮起算，直接清掉会让下轮白跑一次 AI
                    kept[entry_key] = {"cached_at": now, "result": entry}
                elif now - float(cached_at) <= _AI_CACHE_TTL_SECONDS:
                    kept[entry_key] = entry
            if len(kept) > _AI_CACHE_MAX_ENTRIES:
                newest = sorted(kept.items(), key=lambda pair: float(pair[1].get("cached_at") or 0.0), reverse=True)
                kept = dict(newest[:_AI_CACHE_MAX_ENTRIES])
            self.set_document("ai_cache", kept)
