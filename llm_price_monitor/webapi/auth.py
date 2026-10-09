"""管理员账号与会话：密码 scrypt 哈希存 SQLite，登录态为 HMAC 签名的 HttpOnly cookie。

账号存 documents 表的 `admin` 文档（username / password_hash / created_at），
会话签名密钥存 `session_secret` 文档，服务重启后已登录会话不失效。
Cookie 有效期 30 天（SESSION_TTL）。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any

from llm_price_monitor.store import Store

SESSION_COOKIE = "ppm_session"
SESSION_TTL = 30 * 24 * 3600

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def validate_username(username: str) -> str:
    username = username.strip()
    if not username:
        raise ValueError("用户名不能为空")
    if len(username) > 64 or not all(ch.isalnum() or ch in "_-." for ch in username):
        raise ValueError("用户名只能包含字母、数字、点、下划线和中划线（≤64 字符）")
    return username


def validate_password(password: str) -> str:
    if len(password) < 6:
        raise ValueError("密码至少需要 6 位")
    return password


def get_admin(store: Store) -> dict[str, Any] | None:
    admin = store.get_document("admin")
    if admin and isinstance(admin.get("username"), str) and isinstance(admin.get("password_hash"), str):
        return admin
    return None


def set_admin(store: Store, username: str, password: str) -> None:
    store.set_document(
        "admin",
        {"username": username, "password_hash": hash_password(password), "created_at": time.time()},
    )


def _secret(store: Store) -> str:
    doc = store.get_document("session_secret")
    if doc and isinstance(doc.get("secret"), str):
        return doc["secret"]
    secret = secrets.token_hex(32)
    store.set_document("session_secret", {"secret": secret})
    return secret


def _sign(store: Store, payload: str) -> str:
    return hmac.new(_secret(store).encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_session(store: Store, username: str) -> str:
    expires = int(time.time()) + SESSION_TTL
    payload = f"{username}|{expires}"
    return f"{payload}|{_sign(store, payload)}"


def session_username(store: Store, token: str | None) -> str | None:
    """校验会话 cookie，返回管理员用户名；无效或过期返回 None。"""
    if not token:
        return None
    admin = get_admin(store)
    if admin is None:
        return None
    try:
        username, expires, signature = token.split("|")
    except ValueError:
        return None
    if username != admin["username"] or not username:
        return None
    try:
        if int(expires) < time.time():
            return None
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(store, f"{username}|{expires}")):
        return None
    return username
