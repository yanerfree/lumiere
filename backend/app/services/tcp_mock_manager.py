"""TCP Mock 服务管理器 — 管理独立端口的 Mock TCP 服务"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from app.deps.db import async_session_factory
from app.services import tcp_mock_service as svc
from app.services.tcp_match import is_match
from app.models.protocol_mock import TcpMockHandler

logger = logging.getLogger("tcp_mock")

_STATE_FILE = Path(__file__).resolve().parent.parent.parent / ".mock_state" / "tcp_mock.json"


class TcpMockServerManager:
    def __init__(self):
        self.port: int = 28500
        self.host: str = "0.0.0.0"
        self.max_log_count: int = 1000
        self._server: asyncio.AbstractServer | None = None
        self._task: asyncio.Task | None = None

    def _save_state(self, running: bool):
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps({"running": running, "port": self.port}))
        except Exception:
            pass

    def _load_state(self) -> bool:
        try:
            data = json.loads(_STATE_FILE.read_text())
            self.port = data.get("port", self.port)
            return data.get("running", False)
        except Exception:
            return False

    @property
    def running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    async def start(self) -> None:
        if self.running:
            return
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port,
        )
        logger.info("TCP Mock 服务已启动 %s:%d", self.host, self.port)
        self._save_state(True)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("TCP Mock 服务已停止")
            self._save_state(False)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peername = writer.get_extra_info("peername")
        client_ip = peername[0] if peername else ""
        client_port = peername[1] if peername and len(peername) > 1 else None

        # Log connect event
        await self._log_event(
            handler_id=None,
            event_type="connect",
            client_ip=client_ip,
            client_port=client_port,
        )

        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break

                # Match handler
                handler = await self._match_handler(data)

                # Log incoming data
                await self._log_event(
                    handler_id=handler.id if handler else None,
                    event_type="data_in",
                    client_ip=client_ip,
                    client_port=client_port,
                    data_preview=data[:2000].decode("utf-8", errors="replace"),
                    data_size=len(data),
                )

                if handler is None:
                    # 没有匹配的处理器：不做任何应答，直接断开连接（配了才有，没配就没有）
                    break

                # Apply delay
                if handler.delay_ms > 0:
                    await asyncio.sleep(handler.delay_ms / 1000.0)

                response_mode = handler.response_mode

                if response_mode == "echo":
                    response_data = data

                elif response_mode == "fixed" or response_mode == "custom":
                    if handler.response_hex and handler.response_data:
                        try:
                            response_data = bytes.fromhex(handler.response_data)
                        except ValueError:
                            response_data = (handler.response_data or "").encode("utf-8")
                    else:
                        response_data = (handler.response_data or "").encode("utf-8")

                elif response_mode == "close":
                    # 断开事件由 finally 统一记录一次，这里只计命中并退出循环
                    await self._increment_hit(handler.id)
                    break

                else:
                    # Unknown mode — echo
                    response_data = data

                writer.write(response_data)
                await writer.drain()

                # Log outgoing data
                await self._log_event(
                    handler_id=handler.id,
                    event_type="data_out",
                    client_ip=client_ip,
                    client_port=client_port,
                    data_preview=response_data[:2000].decode("utf-8", errors="replace"),
                    data_size=len(response_data),
                )

                # Increment hit count
                await self._increment_hit(handler.id)

        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            logger.exception("TCP Mock 连接处理异常")
        finally:
            # Log disconnect event
            await self._log_event(
                handler_id=None,
                event_type="disconnect",
                client_ip=client_ip,
                client_port=client_port,
            )
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _match_handler(self, data: bytes) -> TcpMockHandler | None:
        try:
            async with async_session_factory() as session:
                handlers = await svc.list_handlers(session)
                for h in handlers:
                    if h.enabled and is_match(h.match_mode, h.match_pattern, data):
                        return h

        except Exception:
            logger.exception("Failed to match TCP handler")
        return None

    async def _increment_hit(self, handler_id) -> None:
        try:
            async with async_session_factory() as session:
                await svc.increment_hit(session, handler_id)
                await session.commit()
        except Exception:
            logger.exception("Failed to increment TCP hit count")

    async def _log_event(self, *, handler_id, event_type, client_ip,
                         client_port=None, data_preview=None, data_size=None) -> None:
        log_data = {
            "handler_id": handler_id,
            "event_type": event_type,
            "client_ip": client_ip,
            "client_port": client_port,
            "data_preview": data_preview,
            "data_size": data_size,
        }
        try:
            async with async_session_factory() as session:
                await svc.create_log(session, log_data)
                await svc.trim_logs(session, self.max_log_count)
                await session.commit()
        except Exception:
            logger.exception("Failed to save TCP mock log")


tcp_mock_server = TcpMockServerManager()
