"""账号与会话端点：首次设置、登录、登出。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from llm_price_monitor.store import Store
from llm_price_monitor.webapi import auth

SETUP_PATH = "/api/setup"
# 登录/登出/首次设置与访客行为本身必须是公开写接口，否则永远进不了门；
# 中间件（app.py）按这个集合放行
PUBLIC_WRITE_PATHS = {SETUP_PATH, "/api/auth/login", "/api/auth/logout", "/api/feedback", "/api/analytics/track"}


class CredentialsBody(BaseModel):
    username: str
    password: str


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.post(SETUP_PATH)
    def setup(body: CredentialsBody, response: Response) -> dict[str, bool]:
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
        )
        return {"is_admin": True}

    @router.post("/api/auth/login")
    def login(body: CredentialsBody, response: Response) -> dict[str, bool]:
        """账号密码登录，签发 30 天有效期的 HttpOnly 会话 cookie。"""
        admin = auth.get_admin(store)
        if admin is None:
            raise HTTPException(status_code=400, detail="尚未创建管理员账号，请先完成首次设置")
        if body.username.strip() != admin["username"] or not auth.verify_password(body.password, admin["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码不正确")
        response.set_cookie(
            auth.SESSION_COOKIE,
            auth.issue_session(store, admin["username"]),
            max_age=auth.SESSION_TTL,
            httponly=True,
            samesite="lax",
        )
        return {"is_admin": True}

    @router.post("/api/auth/logout")
    def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return {"is_admin": False}

    return router
