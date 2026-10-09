"""管理员账号命令行工具：忘记密码时的找回入口，直接重置数据库里的管理员账号。

在仓库根目录运行（数据库路径 var/monitor.db 相对当前目录解析）：

    uv run price-admin                    # 交互式：输入新密码并确认
    uv run price-admin --username ethan --password s3cret
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from llm_price_monitor.store import Store
from llm_price_monitor.webapi import auth

DB_PATH = Path("var/monitor.db")


def main() -> None:
    parser = argparse.ArgumentParser(description="重置 llm-price-monitor 管理员账号（在仓库根目录运行）")
    parser.add_argument("--username", help="新用户名（默认保留现有用户名，无账号时默认 admin）")
    parser.add_argument("--password", help="新密码；省略时交互式输入并确认")
    args = parser.parse_args()

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
    print(f"已重置管理员账号：{username}")
    print("用该账号在 /login 页面登录即可；此前签发的旧会话已因用户名校验而失效。")
