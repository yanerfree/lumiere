"""OAuth2 Mock 授权服务器 — 真实 RSA 签发 + 验签 + Introspection"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from joserfc import jwt
from joserfc.jwk import RSAKey
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("oauth2_mock")

_STATE_DIR = Path(__file__).resolve().parent.parent.parent / ".mock_state"
_STATE_FILE = _STATE_DIR / "oauth2_mock.json"
_CLIENTS_FILE = _STATE_DIR / "oauth2_mock_clients.json"
_RSA_KEY_FILE = _STATE_DIR / "oauth2_mock_rsa_key.json"

DEFAULT_CLIENTS = [
    {
        "client_id": "demo-client",
        "client_secret": "demo-secret",
        "name": "Demo Client",
        "scope": "read write",
        "audience": "api",
        "token_ttl": 3600,
        "enabled": True,
    },
]


class OAuth2MockServerManager:
    def __init__(self):
        self.port: int = 28800
        self.host: str = "0.0.0.0"
        self._server = None
        self._task: asyncio.Task | None = None
        self._rsa_key: RSAKey | None = None
        self._kid: str = "mock-rsa-key-1"
        self._clients: list[dict] = []
        self._logs: deque[dict] = deque(maxlen=500)
        self._load_state()
        self._load_clients()
        self._ensure_rsa_key()

    # ── RSA 密钥管理 ──

    def _ensure_rsa_key(self):
        if _RSA_KEY_FILE.exists():
            try:
                key_data = json.loads(_RSA_KEY_FILE.read_text())
                self._rsa_key = RSAKey.import_key(key_data)
                return
            except Exception:
                logger.warning("加载 RSA 密钥失败，重新生成")
        self._rsa_key = RSAKey.generate_key(2048)
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _RSA_KEY_FILE.write_text(json.dumps(self._rsa_key.as_dict(private=True)))
        logger.info("已生成新的 RSA 密钥对")

    def get_jwks(self) -> dict:
        pub = self._rsa_key.as_dict(private=False)
        pub["kid"] = self._kid
        pub["use"] = "sig"
        pub["alg"] = "RS256"
        return {"keys": [pub]}

    # ── Client 管理 ──

    def _load_clients(self):
        if _CLIENTS_FILE.exists():
            try:
                self._clients = json.loads(_CLIENTS_FILE.read_text())
                return
            except Exception:
                pass
        self._clients = list(DEFAULT_CLIENTS)
        self._save_clients()

    def _save_clients(self):
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _CLIENTS_FILE.write_text(json.dumps(self._clients, ensure_ascii=False, indent=2))

    def get_clients(self) -> list[dict]:
        return [self._safe_client(c) for c in self._clients]

    def _safe_client(self, c: dict) -> dict:
        # locked 是后加的字段，老 JSON 里没有 —— 补默认值，别让前端拿到 undefined
        return {
            **c,
            "locked": c.get("locked", False),
            "client_secret_masked": c["client_secret"][:4] + "****" if c.get("client_secret") else "",
        }

    def get_client(self, client_id: str) -> dict | None:
        return next((c for c in self._clients if c["client_id"] == client_id), None)

    def add_client(self, data: dict) -> dict:
        client = {
            "client_id": data.get("client_id") or str(uuid.uuid4())[:8],
            "client_secret": data.get("client_secret") or secrets.token_urlsafe(32),
            "name": data.get("name", ""),
            "scope": data.get("scope", ""),
            "audience": data.get("audience", "api"),
            "token_ttl": data.get("token_ttl", 3600),
            "enabled": True,
            "locked": False,
        }
        if self.get_client(client["client_id"]):
            raise ValueError(f"Client '{client['client_id']}' already exists")
        self._clients.append(client)
        self._save_clients()
        return client

    def update_client(self, client_id: str, data: dict) -> dict | None:
        client = self.get_client(client_id)
        if not client:
            return None
        for k in ("name", "scope", "audience", "token_ttl", "enabled", "locked", "client_secret"):
            if k in data:
                client[k] = data[k]
        self._save_clients()
        return client

    def toggle_lock(self, client_id: str) -> dict | None:
        client = self.get_client(client_id)
        if not client:
            return None
        # 老的 oauth2_mock_clients.json 里没有 locked 键，取默认值再翻转
        client["locked"] = not client.get("locked", False)
        self._save_clients()
        return client

    def delete_client(self, client_id: str) -> bool:
        idx = next((i for i, c in enumerate(self._clients) if c["client_id"] == client_id), None)
        if idx is None:
            return False
        self._clients.pop(idx)
        self._save_clients()
        return True

    # ── Token 签发 ──

    def issue_token(self, client: dict, scope: str, issuer: str) -> tuple[str, dict]:
        now = int(time.time())
        ttl = client.get("token_ttl", 3600)
        claims = {
            "iss": issuer,
            "sub": client["client_id"],
            "aud": client.get("audience", "api"),
            "iat": now,
            "exp": now + ttl,
            "scope": scope or client.get("scope", ""),
            "client_id": client["client_id"],
            "jti": str(uuid.uuid4()),
        }
        header = {"alg": "RS256", "kid": self._kid}
        token_str = jwt.encode(header, claims, self._rsa_key)
        return token_str, claims

    # ── Token 验证（Introspection） ──

    def introspect_token(self, token_str: str) -> dict:
        if not token_str:
            return {"active": False}
        try:
            token_obj = jwt.decode(token_str, self._rsa_key)
            claims = token_obj.claims
        except Exception as e:
            return {"active": False, "_reason": f"签名验证失败: {e}"}

        now = int(time.time())
        exp = claims.get("exp", 0)
        if exp and now > exp:
            return {"active": False, "_reason": f"Token 已过期 (exp={exp}, now={now})"}

        iat = claims.get("iat", 0)
        if iat and iat > now + 60:
            return {"active": False, "_reason": "iat 在未来"}

        client_id = claims.get("client_id", claims.get("sub", ""))
        client = self.get_client(client_id)
        if client and not client.get("enabled", True):
            return {"active": False, "_reason": f"Client '{client_id}' 已禁用"}

        return {
            "active": True,
            "sub": claims.get("sub"),
            "client_id": client_id,
            "scope": claims.get("scope", ""),
            "iss": claims.get("iss"),
            "aud": claims.get("aud"),
            "iat": iat,
            "exp": exp,
            "token_type": "Bearer",
            "jti": claims.get("jti"),
        }

    # ── 日志 ──

    def _add_log(self, endpoint: str, client_id: str, status: str, detail: str, extra: dict | None = None):
        entry = {
            "id": str(uuid.uuid4())[:8],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "endpoint": endpoint,
            "client_id": client_id,
            "status": status,
            "detail": detail,
            **(extra or {}),
        }
        self._logs.appendleft(entry)

    def get_logs(self, limit: int = 50, offset: int = 0) -> tuple[list, int]:
        logs = list(self._logs)
        return logs[offset:offset + limit], len(logs)

    def clear_logs(self) -> int:
        count = len(self._logs)
        self._logs.clear()
        return count

    # ── 服务管理 ──

    def _save_state(self, running: bool):
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps({"running": running, "port": self.port}))
        except Exception:
            pass

    def _load_state(self):
        try:
            data = json.loads(_STATE_FILE.read_text())
            self.port = data.get("port", 28800)
        except Exception:
            pass

    def _prev_running(self) -> bool:
        try:
            return json.loads(_STATE_FILE.read_text()).get("running", False)
        except Exception:
            return False

    @property
    def running(self) -> bool:
        if self._server is None:
            return False
        if self._task is not None and self._task.done():
            logger.warning("OAuth2 Mock 服务 task 已意外退出，清理状态")
            self._server = None
            self._task = None
            return False
        return getattr(self._server, 'started', False)

    async def start(self) -> None:
        if self.running:
            return
        app = self._create_app()
        import uvicorn
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning")
        server = uvicorn.Server(config)
        from app.services._mock_server_util import guarded_serve
        task = asyncio.create_task(guarded_serve(server, "OAuth2 Mock"))
        self._task = task
        task.add_done_callback(self._on_task_done)
        self._server = server
        for _ in range(50):
            if server.started:
                break
            if task.done():
                self._server = None
                self._task = None
                raise RuntimeError(f"OAuth2 Mock 启动失败，端口 {self.port} 可能被占用")
            await asyncio.sleep(0.1)
        logger.info("OAuth2 Mock 服务已启动 %s:%d", self.host, self.port)
        self._save_state(True)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            if self._task:
                try:
                    await asyncio.wait_for(self._task, timeout=5)
                except (asyncio.TimeoutError, Exception):
                    pass
            self._server = None
            self._task = None
            logger.info("OAuth2 Mock 服务已停止")
            self._save_state(False)

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("OAuth2 Mock 服务异常退出: %s", exc)
        self._server = None
        self._task = None

    def _create_app(self):
        app = FastAPI(title="OAuth2 Mock Authorization Server")
        app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
        mgr = self

        @app.get("/.well-known/openid-configuration")
        async def openid_config(request: Request):
            base = f"http://{request.headers.get('host', f'localhost:{mgr.port}')}"
            return {
                "issuer": base,
                "token_endpoint": f"{base}/oauth2/token",
                "introspection_endpoint": f"{base}/oauth2/introspect",
                "jwks_uri": f"{base}/oauth2/jwks",
                "grant_types_supported": ["client_credentials"],
                "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
                "introspection_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
                "response_types_supported": ["token"],
            }

        @app.get("/oauth2/jwks")
        async def jwks():
            return mgr.get_jwks()

        @app.post("/oauth2/token")
        async def token_endpoint(request: Request):
            data = await _parse_body(request)
            grant_type = data.get("grant_type", "")
            client_id = data.get("client_id", "")
            client_secret = data.get("client_secret", "")
            scope = data.get("scope", "")

            if not client_id:
                client_id, client_secret = _parse_basic_auth(request)

            if grant_type != "client_credentials":
                mgr._add_log("/oauth2/token", client_id, "error", f"不支持的 grant_type: {grant_type}")
                return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

            client = mgr.get_client(client_id)
            if not client:
                mgr._add_log("/oauth2/token", client_id, "error", "Client 不存在")
                return JSONResponse({"error": "invalid_client", "error_description": "Client not found"}, status_code=401)

            if not client.get("enabled", True):
                mgr._add_log("/oauth2/token", client_id, "error", "Client 已禁用")
                return JSONResponse({"error": "invalid_client", "error_description": "Client disabled"}, status_code=401)

            if client.get("client_secret") and client["client_secret"] != client_secret:
                mgr._add_log("/oauth2/token", client_id, "error", "client_secret 错误")
                return JSONResponse({"error": "invalid_client", "error_description": "Bad credentials"}, status_code=401)

            issuer = f"http://{request.headers.get('host', f'localhost:{mgr.port}')}"
            token_str, claims = mgr.issue_token(client, scope, issuer)
            ttl = client.get("token_ttl", 3600)
            mgr._add_log("/oauth2/token", client_id, "success", f"签发 Token，有效期 {ttl}s")

            return {
                "access_token": token_str,
                "token_type": "Bearer",
                "expires_in": ttl,
                "scope": scope or client.get("scope", ""),
            }

        @app.post("/oauth2/introspect")
        async def introspect_endpoint(request: Request):
            data = await _parse_body(request)
            token_str = data.get("token", "")
            result = mgr.introspect_token(token_str)
            reason = result.pop("_reason", "")
            status = "active" if result.get("active") else "inactive"
            detail = reason if reason else (f"有效，client={result.get('client_id', '?')}" if result.get("active") else "无效 token")
            mgr._add_log("/oauth2/introspect", result.get("client_id", ""), status, detail)
            return result

        async def _parse_body(request: Request) -> dict:
            ct = request.headers.get("content-type", "")
            if "json" in ct:
                try:
                    return await request.json()
                except Exception:
                    return {}
            try:
                form = await request.form()
                return dict(form)
            except Exception:
                return {}

        def _parse_basic_auth(request: Request) -> tuple[str, str]:
            auth = request.headers.get("authorization", "")
            if not auth.startswith("Basic "):
                return "", ""
            try:
                decoded = base64.b64decode(auth[6:]).decode()
                parts = decoded.split(":", 1)
                return parts[0], parts[1] if len(parts) > 1 else ""
            except Exception:
                return "", ""

        return app


oauth2_mock_server = OAuth2MockServerManager()
