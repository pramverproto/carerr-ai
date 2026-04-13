"""
auth.py 单元测试。

覆盖内容：
1. hash_password / verify_password 正常流程
2. verify_password 错误密码
3. create_token / decode_token 正常流程
4. decode_token 过期 Token
5. decode_token 无效 Token
"""

import os
import sys
import time

import pytest

os.environ.setdefault("LLM_MODEL",    "gpt-4o-mini")
os.environ.setdefault("LLM_API_KEY",  "test-key")
os.environ.setdefault("LLM_BASE_URL", "http://localhost:9999")
os.environ.setdefault("DB_HOST",      "localhost")
os.environ.setdefault("DB_USER",      "test")
os.environ.setdefault("DB_PASSWORD",  "test")
os.environ.setdefault("DB_NAME",      "test")

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from auth import hash_password, verify_password, create_token, decode_token


# ── 密码哈希 ────────────────────────────────────────────────────────

class TestPassword:
    def test_hash_and_verify(self):
        pwd = "myS3cretP@ss"
        hashed = hash_password(pwd)
        assert hashed != pwd
        assert verify_password(pwd, hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_hash_produces_different_salts(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # bcrypt 每次生成不同 salt

    def test_empty_password(self):
        hashed = hash_password("")
        assert verify_password("", hashed) is True
        assert verify_password("notempty", hashed) is False


# ── JWT Token ────────────────────────────────────────────────────────

class TestJWT:
    def test_create_and_decode(self):
        token = create_token(42, "testuser")
        payload = decode_token(token)
        assert payload["user_id"] == 42
        assert payload["username"] == "testuser"
        assert "exp" in payload

    def test_decode_invalid_token(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            decode_token("this.is.invalid")
        assert exc_info.value.status_code == 401

    def test_decode_expired_token(self):
        import jwt as pyjwt
        from auth import SECRET_KEY, ALGORITHM
        from datetime import datetime, timedelta, timezone
        from fastapi import HTTPException

        payload = {
            "user_id": 1,
            "username": "expired_user",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        expired_token = pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
        with pytest.raises(HTTPException) as exc_info:
            decode_token(expired_token)
        assert exc_info.value.status_code == 401
        assert "过期" in exc_info.value.detail

    def test_token_contains_expected_fields(self):
        token = create_token(99, "alice")
        payload = decode_token(token)
        assert set(payload.keys()) >= {"user_id", "username", "exp"}
