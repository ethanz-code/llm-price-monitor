"""管理员命令行工具：重置管理员账号、整理存量站点配置。

在仓库根目录运行（数据库路径 var/monitor.db 相对当前目录解析）：

    uv run price-admin                        # 交互式：输入新密码并确认，重置管理员
    uv run price-admin --username ethan --password s3cret
    uv run price-admin tidy-sites             # 把库内站点配置整理成当前结构（瘦身＋认证收口）
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
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


def main() -> None:
    parser = argparse.ArgumentParser(description="llm-price-monitor 管理员工具（在仓库根目录运行）")
    sub = parser.add_subparsers(dest="command")
    reset = sub.add_parser("reset-admin", help="重置管理员账号")
    reset.add_argument("--username", help="新用户名（默认保留现有用户名，无账号时默认 admin）")
    reset.add_argument("--password", help="新密码；省略时交互式输入并确认")
    sub.add_parser("tidy-sites", help="把库内站点配置整理成当前结构（瘦身＋认证收口），改动直接写库")
    args = parser.parse_args()

    if args.command == "tidy-sites":
        tidy_sites()
    else:
        reset_admin(args)  # 不带子命令时保持原行为：重置管理员


if __name__ == "__main__":
    main()
