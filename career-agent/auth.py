"""
JWT 认证模块：用户注册/登录、Token 签发与验证、FastAPI 依赖注入。
"""

import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# ── 配置 ─────────────────────────────────────────────────────────────

SECRET_KEY = os.getenv("JWT_SECRET")
if not SECRET_KEY:
    raise EnvironmentError("缺少必填环境变量：JWT_SECRET，请在 .env 文件中配置")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 72  # 3 天

security = HTTPBearer(auto_error=False)

# ── 密码工具 ─────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ── JWT 工具 ─────────────────────────────────────────────────────────

def create_token(user_id: int, username: str) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token 已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "无效的 Token")


# ── FastAPI 依赖 ─────────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    强制认证依赖：从 Authorization: Bearer <token> 提取用户信息。
    返回 {"user_id": int, "username": str}。
    """
    if credentials is None:
        raise HTTPException(401, "未提供认证信息")
    payload = decode_token(credentials.credentials)
    return {"user_id": payload["user_id"], "username": payload["username"]}


async def optional_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict | None:
    """
    可选认证依赖：未登录返回 None（用于兼容过渡期）。
    """
    if credentials is None:
        return None
    try:
        payload = decode_token(credentials.credentials)
        return {"user_id": payload["user_id"], "username": payload["username"]}
    except HTTPException:
        return None
