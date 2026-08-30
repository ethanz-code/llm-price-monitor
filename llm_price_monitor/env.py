"""CLI 运行环境的 .env 自动加载（dotenvx）。

通过 python-dotenvx 读取 dotenvx 加密的 .env（解密需要 DOTENV_PRIVATE_KEY，
来自环境变量或本机 .env.keys，两者都不进仓库）；明文 .env 也兼容。
"""
from __future__ import annotations

from dotenvx import load_dotenv


def load_env_files() -> None:
    load_dotenv()
