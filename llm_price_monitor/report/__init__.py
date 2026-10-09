"""运行编排与报告（原 report.py 拆分为包）：监控运行、事件分类、持久化、汇总输出。

采集拆成三类独立扫描：价格（scan_prices）、渠道状态（scan_statuses）、站点公告（scan_notices），
可分别按各自周期定时执行；run_once 是全量入口，共享一个连接串行跑三类。
每类扫描：选 UA → 逐站点采集 → 与上次快照比对生成事件 → 写历史/快照 → 输出摘要。

包结构（对外 API 与旧单文件 report.py 完全一致）：
- `events`：报告数据模型（MonitorReport / SectionScan）、价格口径指纹与事件分类
- `summary`：阶梯平铺、汇总行与官方目录折扣附加（仅输出层）
- `pricing`：价格行采集侧变换（沿用上次价、规则价回填、分组白名单、合理性作废）
- `timeouts`：单站采集硬上限保护（daemon 子线程 + 超时放弃）
- `health`：并发锁、传输抖动容忍与 collect_status / site_collect_health 健康档案合并
- `persist`：采集结果落库（复查-写入原子锁、并发轮次去重）
- `scans`：三类扫描主体与分组下线检测
- `entry`：scan_prices / scan_statuses / scan_notices 独立入口与 run_once 全量编排

公有接口与旧单文件模块完全一致：`from llm_price_monitor.report import X` 与
`llm_price_monitor.report.X`（含测试对 SITE_HARD_TIMEOUT_SECONDS / fetch_site_status /
fetch_site_notice 的 monkeypatch）继续可用；子模块对打桩目标经包命名空间运行时回查。
json / re / time / threading / httpx / tasklog / catalog_fx 等模块对象也保留在包
命名空间上（各自是共享模块对象，打桩全局生效）。
"""
from __future__ import annotations

# 旧 report.py 的模块级导入，作为包属性保留（测试可能经包命名空间打桩这些共享模块对象）。
import hashlib
import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path  # noqa: F401  旧单文件可访问的再导出
from typing import Any, Callable  # noqa: F401

import httpx

from llm_price_monitor import tasklog
from llm_price_monitor.adapters import ADAPTERS  # noqa: F401
from llm_price_monitor.config import (  # noqa: F401
    AuthRequiredError,
    ChangeKind,
    MonitorConfig,
    PriceMonitorError,
    SiteSpec,
)
from llm_price_monitor.http_retry import build_client
from llm_price_monitor.tracker import PriceRecord
from llm_price_monitor.units import round2, tier_unit_per_1m  # noqa: F401
from llm_price_monitor.useragent import choose_user_agent
from llm_price_monitor.catalog import discount as catalog_discount, fx as catalog_fx
from llm_price_monitor.notice import fetch_site_notice
from llm_price_monitor.status import diff_status, fetch_site_status
from llm_price_monitor.store import Store, latest_key, latest_site_prefix, split_latest_key
from llm_price_monitor.token_refresh import (  # noqa: F401
    needs_refresh,
    refresh_and_recollect,
    refresh_and_retry_once,
)

from .events import (
    FINGERPRINT_METADATA_KEYS,
    MonitorReport,
    PriceEvent,
    SectionScan,
    classify,
    fingerprint,
    record_dict,
    site_status_from_records,
)
from .summary import (
    attach_catalog_discounts,
    representative_tier,
    summary_row,
    summary_tiers,
)
from .pricing import (
    _apply_price_sanity,
    _backfill_rule_price,
    _carry_last_price,
    _filter_price_groups,
    _has_price,
    _sanity_context,
)
from .timeouts import (
    SITE_HARD_TIMEOUT_SECONDS,
    _run_network_phase,
    _run_site_fetch,
)
from .health import (
    STATUS_TRANSPORT_TOLERANCE,
    _MERGE_LOCK,
    _PERSIST_LOCK,
    _bump_transport_streak,
    _merge_collect_status,
    _merge_price_health,
    _merge_site_health,
    _reset_transport_streak,
    _section_health_entries,
    _TRANSPORT_ERROR_RE,
    _TRANSPORT_STREAK_DOC,
    is_transport_error,
)
from .persist import (
    _COLON_KEY_WARNED,
    _drop_persisted_events,
    _drop_persisted_notice_events,
    _event_snapshot_key,
    _persist_latest,
    _persist_scan_results,
)
from .scans import (
    GROUP_REMOVED_MISSES,
    _detect_removed_groups,
    _scan_notices,
    _scan_prices,
    _scan_statuses,
)
from .entry import run_once, scan_notices, scan_prices, scan_statuses
