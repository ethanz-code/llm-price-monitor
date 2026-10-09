"""SQLite 存储层：站点配置、系统设置、价格历史、事件、快照与文档的唯一真相源。

连接策略：每次操作开短连接并在用后关闭，天然跨线程安全（采集任务在后台线程写库）。
库文件路径相对启动工作目录解析，默认 var/monitor.db，与 webapi 其余路径语义一致。

包结构（按职责拆分，Store 由各 Mixin 组装，对外 API 与旧单文件 store.py 完全一致）：
- `schema`：DDL、旧库迁移与 latest 快照 key 编码规则（本次独立的核心目标）
- `base`：连接管理、JSON 序列化与公开读接口共用的行读取助手
- `sites`：站点配置、站点提交、反馈与 AI 助手配额
- `documents`：documents KV 文档与 latest 最新快照
- `prices`：价格趋势点与价格事件
- `status`：渠道状态时序与增量裁剪
- `notices`：站点公告版本与变化事件
- `ai`：AI 请求日志、模型上限记忆与结果缓存
- `visits`：访问日志、IP 归属地与访问统计
"""
from __future__ import annotations

# 公开读接口单次返回行数上限：limit 入参统一在 _read_rows 钳制，防单请求拖全表。
# 必须定义在子模块导入之前：base/prices/status/sites 通过包属性运行时回查这里，
# tests/test_store.py 对本值的 monkeypatch 才能对子模块生效。
_MAX_ROW_LIMIT = 2000

from .schema import (
    _SCHEMA,
    _migrate_price_records,
    latest_key,
    latest_site_prefix,
    split_latest_key,
)
from .base import StoreBase, _dumps
from .sites import SiteStoreMixin
from .documents import DocumentStoreMixin
from .prices import PriceStoreMixin, _price_event_group
from .status import StatusStoreMixin
from .notices import NoticeStoreMixin
from .ai import (
    AIStoreMixin,
    _AI_CACHE_LOCK,
    _AI_ERROR_KIND_HEAD,
    _AI_LOG_TEXT_CHARS,
    _clip_ai_log_text,
    ai_error_kind,
)
from .visits import VisitStoreMixin
from llm_price_monitor.timeline import TIMELINE_KEYS  # noqa: F401  旧单文件可访问的再导出


class Store(
    SiteStoreMixin,
    DocumentStoreMixin,
    PriceStoreMixin,
    StatusStoreMixin,
    NoticeStoreMixin,
    AIStoreMixin,
    VisitStoreMixin,
    StoreBase,
):
    """存储门面：方法体分布在各职责 Mixin 中，连接与 DDL 初始化在 StoreBase。"""


__all__ = [
    "Store",
    "StoreBase",
    "SiteStoreMixin",
    "DocumentStoreMixin",
    "PriceStoreMixin",
    "StatusStoreMixin",
    "NoticeStoreMixin",
    "AIStoreMixin",
    "VisitStoreMixin",
    "latest_site_prefix",
    "latest_key",
    "split_latest_key",
    "ai_error_kind",
    "TIMELINE_KEYS",
]
