"""Web UI channel: a local browser chat interface served over HTTP + WebSocket.

Implemented as a channel so it reuses the existing MessageBus/AgentLoop routing.
"""

from __future__ import annotations

import asyncio
import importlib.resources as pkgres
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import WebUIConfig


@dataclass(frozen=True)
class _ClientKey:
    chat_id: str
    sender_id: str


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in ("127.0.0.1", "localhost", "::1")


class WebUIChannel(BaseChannel):
    """A minimal, high-polish browser UI for chatting with nanobot."""

    name = "webui"
    max_message_chars = None

    def __init__(self, config: WebUIConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: WebUIConfig = config

        self._server: Any | None = None
        self._started = asyncio.Event()

        self._clients_lock = asyncio.Lock()
        self._clients: dict[Any, _ClientKey] = {}
        self._by_chat: dict[str, set[Any]] = {}

        self._host = (config.host or "127.0.0.1").strip()
        self._port = int(config.port or 0)
        self._bound_port: int | None = None

    @property
    def bound_port(self) -> int | None:
        """The actual port bound by the server (useful when port=0)."""
        return self._bound_port

    async def wait_started(self, timeout_s: float = 5.0) -> None:
        """Wait until the HTTP/WS server is listening (mainly for tests)."""
        await asyncio.wait_for(self._started.wait(), timeout=timeout_s)

    def _require_token(self) -> bool:
        if (self.config.auth_token or "").strip():
            return True
        return not _is_loopback_host(self._host)

    def _token_ok(self, token: str | None) -> bool:
        needed = (self.config.auth_token or "").strip()
        if not needed:
            return _is_loopback_host(self._host)
        return secrets.compare_digest(needed, (token or "").strip())

    def _read_asset_bytes(self, relpath: str) -> bytes:
        try:
            p = pkgres.files("nanobot.webui").joinpath(relpath)
            return p.read_bytes()
        except Exception:
            return b""

    def _mime_for(self, path: str) -> str:
        if path.endswith(".html"):
            return "text/html; charset=utf-8"
        if path.endswith(".css"):
            return "text/css; charset=utf-8"
        if path.endswith(".js"):
            return "text/javascript; charset=utf-8"
        if path.endswith(".svg"):
            return "image/svg+xml"
        return "application/octet-stream"

    def _extract_request_path_and_headers(self, *args: Any) -> tuple[str, Any]:
        """
        websockets has two different process_request signatures across versions:
        - legacy: (path: str, request_headers: Headers)
        - current: (connection: ServerConnection, request: Request)
        """
        if len(args) >= 2 and isinstance(args[0], str):
            return args[0], args[1]

        # websockets>=12: (connection, request)
        if len(args) >= 2:
            req = args[1]
            path = getattr(req, "path", None) or getattr(args[0], "path", None) or "/"
            headers = getattr(req, "headers", None) or getattr(req, "header", None) or {}
            return str(path), headers

        # Fallback
        return "/", {}

    async def _process_request(self, *args: Any) -> Any:
        """Serve a tiny static web app from the same port as the WebSocket server."""
        path, _request_headers = self._extract_request_path_and_headers(*args)

        parsed = urlparse(path)
        qs = parse_qs(parsed.query or "")
        token = (qs.get("token") or [""])[0]

        if parsed.path == "/ws":
            return None

        if parsed.path in ("/healthz", "/health"):
            body = b"ok\n"
            return (
                200,
                [
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                ],
                body,
            )

        if self._require_token() and not self._token_ok(token):
            body = b"Unauthorized. Provide ?token=... (channels.webui.authToken)\n"
            return (
                401,
                [
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                ],
                body,
            )

        route = parsed.path
        if route in ("", "/"):
            route = "/index.html"

        if route in ("/index.html", "/app.css", "/app.js"):
            body = self._read_asset_bytes(route.lstrip("/"))
            if not body:
                body = b"missing asset\n"
                return (
                    500,
                    [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body)))],
                    body,
                )
            csp = (
                "default-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "connect-src 'self' ws: wss:; "
                "base-uri 'none'; "
                "frame-ancestors 'none'"
            )
            headers = [
                ("Content-Type", self._mime_for(route)),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
                ("Content-Security-Policy", csp),
            ]
            return (200, headers, body)

        body = b"Not found\n"
        return (
            404,
            [
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ],
            body,
        )

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}:{secrets.token_hex(8)}"

    async def start(self) -> None:
        """Start the Web UI server and keep it running."""
        import websockets

        self._running = True
        self._started.clear()

        if self._require_token() and not (self.config.auth_token or "").strip():
            logger.error(
                "WebUI enabled but no authToken is configured while binding to a non-loopback host. "
                "Set channels.webui.authToken or bind to 127.0.0.1."
            )
            self._running = False
            return

        host, port = self._host, self._port
        logger.info(f"Starting WebUI on http://{host}:{port or 0}/ (ws /ws)")

        async def handler(ws: Any) -> None:
            await self._handle_client(ws)

        self._server = await websockets.serve(
            handler,
            host,
            port,
            process_request=self._process_request,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=2**20,  # 1 MiB
        )

        try:
            socks = getattr(self._server, "sockets", None)
            self._bound_port = int(socks[0].getsockname()[1]) if socks else int(port)
        except Exception:
            self._bound_port = int(port)

        logger.info(f"WebUI listening on http://{host}:{self._bound_port}/")
        self._started.set()

        try:
            await self._server.wait_closed()
        finally:
            self._running = False

    async def stop(self) -> None:
        """Stop the Web UI server and close all clients."""
        self._running = False

        async with self._clients_lock:
            clients = list(self._clients.keys())

        for ws in clients:
            try:
                await ws.close()
            except Exception:
                pass

        if self._server is not None:
            try:
                self._server.close()
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

        async with self._clients_lock:
            self._clients.clear()
            self._by_chat.clear()

    async def send(self, msg: OutboundMessage) -> None:
        """Send an assistant message to all browser clients in the chat."""
        if msg.channel != self.name:
            return

        payload = json.dumps(
            {"type": "assistant", "chat_id": str(msg.chat_id), "content": msg.content or "", "ts": time.time()}
        )

        async with self._clients_lock:
            targets = list(self._by_chat.get(str(msg.chat_id), set()))

        for ws in targets:
            try:
                await ws.send(payload)
            except Exception:
                # Best-effort broadcast; dead sockets will be cleaned up on disconnect.
                pass

    async def _handle_client(self, ws: Any) -> None:
        # websockets>=12 provides request information on ws.request
        req = getattr(ws, "request", None)
        path = getattr(req, "path", None) or getattr(ws, "path", None) or ""
        parsed = urlparse(path)
        qs = parse_qs(parsed.query or "")

        token = (qs.get("token") or [""])[0]
        if self._require_token() and not self._token_ok(token):
            await ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
            await ws.close(code=4401, reason="unauthorized")
            return

        chat_id = (qs.get("chat_id") or qs.get("chat") or [""])[0].strip() or self._new_id("c")
        sender_id = (qs.get("sender_id") or [""])[0].strip() or self._new_id("u")

        key = _ClientKey(chat_id=str(chat_id), sender_id=str(sender_id))

        async with self._clients_lock:
            self._clients[ws] = key
            self._by_chat.setdefault(key.chat_id, set()).add(ws)

        await ws.send(json.dumps({"type": "session", "chat_id": key.chat_id, "sender_id": key.sender_id}))
        logger.info(f"WebUI client connected chat_id={key.chat_id} sender_id={key.sender_id}")

        try:
            async for raw in ws:
                await self._handle_ws_message(ws, raw)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"WebUI client error: {e}")
        finally:
            await self._drop_client(ws)

    async def _drop_client(self, ws: Any) -> None:
        async with self._clients_lock:
            key = self._clients.pop(ws, None)
            if key is None:
                return
            s = self._by_chat.get(key.chat_id)
            if s is not None:
                s.discard(ws)
                if not s:
                    self._by_chat.pop(key.chat_id, None)

    async def _handle_ws_message(self, ws: Any, raw: Any) -> None:
        try:
            data = json.loads(raw)
        except Exception:
            return

        msg_type = (data.get("type") or "").strip().lower()
        if msg_type == "ping":
            await ws.send(json.dumps({"type": "pong", "ts": time.time()}))
            return

        if msg_type in ("hello",):
            return

        if msg_type in ("new_chat", "new_session", "new-session", "new-session"):
            new_chat_id = self._new_id("c")
            async with self._clients_lock:
                old = self._clients.get(ws)
                if old is not None:
                    self._by_chat.get(old.chat_id, set()).discard(ws)
                    self._clients[ws] = _ClientKey(chat_id=new_chat_id, sender_id=old.sender_id)
                    self._by_chat.setdefault(new_chat_id, set()).add(ws)
            await ws.send(json.dumps({"type": "session", "chat_id": new_chat_id}))
            return

        if msg_type != "message":
            return

        content = data.get("content")
        if not isinstance(content, str):
            return
        content = content.strip("\n")
        if not content.strip():
            return

        async with self._clients_lock:
            key = self._clients.get(ws)
        if key is None:
            return

        await self._handle_message(
            sender_id=key.sender_id,
            chat_id=key.chat_id,
            content=content,
            metadata={"client": "webui"},
        )
