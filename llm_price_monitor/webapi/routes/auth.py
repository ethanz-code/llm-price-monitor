"""账号与会话端点：首次设置、登录、登出。"""
from __future__ import annotations

import threading
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from llm_price_monitor.store import Store
from llm_price_monitor.webapi import auth
from llm_price_monitor.webapi.deps import client_ip

SETUP_PATH = "/api/setup"
# 登录/登出/首次设置与访客行为本身必须是公开写接口，否则永远进不了门；
# 中间件（app.py）按这个集合放行
PUBLIC_WRITE_PATHS = {SETUP_PATH, "/api/auth/login", "/api/auth/logout", "/api/feedback", "/api/analytics/track", "/api/site-submissions", "/api/assistant/ask", "/api/assistant/ask/stream"}

# 登录失败限流：同一来源窗口期内最多 LOGIN_MAX_FAILURES 次失败，成功登录即清零
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 300.0


class CredentialsBody(BaseModel):
    username: str
    password: str


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    # 限流状态挂在 router 闭包上：每个应用实例独立一份，重启自动清零
    failed_logins: dict[str, list[float]] = {}
    attempts_lock = threading.Lock()

    def login_throttled(key: str) -> bool:
        now = time.time()
        with attempts_lock:
            recent = [ts for ts in failed_logins.get(key, []) if now - ts < LOGIN_WINDOW_SECONDS]
            failed_logins[key] = recent
            return len(recent) >= LOGIN_MAX_FAILURES

    def record_login_failure(key: str) -> None:
        with attempts_lock:
            failed_logins.setdefault(key, []).append(time.time())

    def clear_login_failures(key: str) -> None:
        with attempts_lock:
            failed_logins.pop(key, None)

    @router.post(SETUP_PATH)
    def setup(body: CredentialsBody, request: Request, response: Response) -> dict[str, bool]:
        """首次设置：创建管理员账号并直接登录；账号已存在时拒绝。"""
        if auth.get_admin(store) is not None:
            raise HTTPException(status_code=409, detail="管理员账号已存在，请直接登录")
        try:
            username = auth.validate_username(body.username)
            auth.validate_password(body.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        auth.set_admin(store, username, body.password)
        response.set_cookie(
            auth.SESSION_COOKIE,
            auth.issue_session(store, username),
            max_age=auth.SESSION_TTL,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
        return {"is_admin": True}

    @router.post("/api/auth/login")
    def login(body: CredentialsBody, request: Request, response: Response) -> dict[str, bool]:
        """账号密码登录，签发 30 天有效期的 HttpOnly 会话 cookie；连续失败触发限流。"""
        key = client_ip(request)
        if login_throttled(key):
            raise HTTPException(status_code=429, detail="失败次数过多，请 5 分钟后再试")
        admin = auth.get_admin(store)
        if admin is None:
            raise HTTPException(status_code=400, detail="尚未创建管理员账号，请先完成首次设置")
        if body.username.strip() != admin["username"] or not auth.verify_password(body.password, admin["password_hash"]):
            record_login_failure(key)
            raise HTTPException(status_code=401, detail="用户名或密码不正确")
        clear_login_failures(key)
        response.set_cookie(
            auth.SESSION_COOKIE,
            auth.issue_session(store, admin["username"]),
            max_age=auth.SESSION_TTL,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
        return {"is_admin": True}

    @router.post("/api/auth/logout")
    def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return {"is_admin": False}

    return router
