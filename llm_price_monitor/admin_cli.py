"""管理员命令行工具：重置管理员账号、整理存量站点配置、清理假变更事件。

在仓库根目录运行（数据库路径 var/monitor.db 相对当前目录解析）：

    uv run price-admin                        # 交互式：输入新密码并确认，重置管理员
    uv run price-admin --username ethan --password s3cret
    uv run price-admin tidy-sites             # 把库内站点配置整理成当前结构（瘦身＋认证收口）
    uv run price-admin prune-noop-events      # 预览：按当前指纹口径复判出"价格没变的变更"事件
    uv run price-admin prune-noop-events --apply  # 确认预览无误后真正删除这些假事件
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from pathlib import Path

from llm_price_monitor.config import canonical_site_config
from llm_price_monitor.store import Store
from llm_price_monitor.webapi import auth

# 与 webapi.app 一致：PRICE_MONITOR_DB 可把库指到别处（测试隔离用它），避免"以为是副本、实际写了真库"
DB_PATH = Path(os.getenv("PRICE_MONITOR_DB") or "var/monitor.db")


def reset_admin(args: argparse.Namespace) -> None:
    store = Store(DB_PATH)
    admin = auth.get_admin(store)
    if admin is None:
        print(f"数据库 {DB_PATH} 中还没有管理员账号，此命令将创建一个（也可以启动服务后在页面完成首次设置）。")
    else:
        print(f"当前管理员：{admin['username']}")

    password = args.password
    if password is None:
        password = getpass.getpass("新密码（至少 6 位）: ")
        confirm = getpass.getpass("再输一遍: ")
        if password != confirm:
            sys.exit("两次输入不一致，未做任何修改。")
    try:
        username = auth.validate_username(args.username or (admin["username"] if admin else "admin"))
        auth.validate_password(password)
    except ValueError as exc:
        sys.exit(str(exc))

    auth.set_admin(store, username, password)
    auth.reset_sessions(store)
    print(f"已重置管理员账号：{username}")
    print("用该账号在 /login 页面登录即可；此前签发的旧会话已全部失效。")


def tidy_sites() -> None:
    """把库内每个站点配置整理成当前结构：瘦身＋认证收口，有改动的直接写回。"""
    store = Store(DB_PATH)
    changed = 0
    for config in store.list_site_configs():
        canonical, notes = canonical_site_config(config)
        if canonical == config:
            continue
        store.upsert_site(str(config["id"]), canonical)
        changed += 1
        print(f"已整理 {config['id']}：")
        for note in notes:
            print(f"  - {note}")
    print(f"完成：{changed} 个站点有改动，其余已是当前结构。")


def prune_noop_events(store: Store, apply: bool) -> None:
    """按当前指纹口径复判历史价格事件，找出"前后价格完全一致"的假变更事件。

    指纹口径升级（如把 null 值键与键缺失归一）后，库里已落的假事件不会自愈，
    需要此命令复判清理。默认只预览，--apply 才真删——删事件属于不可逆操作。
    """
    from llm_price_monitor.report import fingerprint  # 指纹依赖采集侧模块，避免不带子命令时也拉起整条依赖链

    stale = []
    for event in store.read_price_event_rows():
        previous, current = event.get("previous"), event.get("current")
        if previous is None or current is None:
            continue  # 新增/下线类事件没有双记录可比，天然不是"假变更"
        if fingerprint(previous) == fingerprint(current):
            stale.append(event)
    if not stale:
        print("没有需要清理的事件：所有变更事件的前后价格口径都确有差异。")
        return
    print(f"发现 {len(stale)} 条假变更事件（前后价格口径完全一致）：")
    for event in stale:
        detected = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(event.get("detected_at") or 0)))
        print(f"  #{event['id']} [{detected}] {event.get('site_id')} {event.get('model')} kind={event.get('kind')}")
    if not apply:
        print("预览模式，未做任何改动；确认以上列表无真实变更后，加 --apply 执行删除。")
        return
    deleted = store.delete_price_events([int(event["id"]) for event in stale])
    print(f"已删除 {deleted} 条假变更事件。")


def main() -> None:
    parser = argparse.ArgumentParser(description="llm-price-monitor 管理员工具（在仓库根目录运行）")
    sub = parser.add_subparsers(dest="command")
    reset = sub.add_parser("reset-admin", help="重置管理员账号")
    reset.add_argument("--username", help="新用户名（默认保留现有用户名，无账号时默认 admin）")
    reset.add_argument("--password", help="新密码；省略时交互式输入并确认")
    sub.add_parser("tidy-sites", help="把库内站点配置整理成当前结构（瘦身＋认证收口），改动直接写库")
    prune = sub.add_parser("prune-noop-events", help="按当前指纹口径复判并清理'价格没变的变更'事件（默认预览）")
    prune.add_argument("--apply", action="store_true", help="真正执行删除；省略时只预览")
    args = parser.parse_args()

    if args.command == "tidy-sites":
        tidy_sites()
    elif args.command == "prune-noop-events":
        prune_noop_events(Store(DB_PATH), apply=args.apply)
    else:
        reset_admin(args)  # 不带子命令时保持原行为：重置管理员


if __name__ == "__main__":
    main()
