"""可选的服务端访问认证。"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Request
from starlette.responses import JSONResponse

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
TOKEN_COOKIE = "quill_access_token"
TOKEN_QUERY = "access_token"
REDACTED = "••••••"


@dataclass(frozen=True)
class AuthConfig:
    host: str
    token: str = ""

    @property
    def required(self) -> bool:
        return self.host not in LOOPBACK_HOSTS

    def valid(self, supplied: str | None) -> bool:
        if not self.required:
            return True
        return bool(self.token and supplied and secrets.compare_digest(supplied, self.token))


def supplied_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value
    return request.cookies.get(TOKEN_COOKIE) or request.query_params.get(TOKEN_QUERY)


def unauthorized() -> JSONResponse:
    return JSONResponse({"detail": "需要访问令牌"}, status_code=401)


def redact(value: str, *, configured: bool = False) -> str:
    """只向客户端返回是否配置及短掩码，不回传凭据原文。"""
    if not value and not configured:
        return ""
    return REDACTED


def redact_mapping(values: dict[str, str]) -> dict[str, str]:
    return {key: REDACTED for key in values}
