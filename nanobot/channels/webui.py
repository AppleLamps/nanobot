"""Web UI channel: a local browser chat interface served over HTTP + WebSocket.

Implemented as a channel so it reuses the existing MessageBus/AgentLoop routing.
"""

from __future__ import annotations

import asyncio
import importlib.resources as pkgres
import base64
import json
import secrets
import time
from dataclasses import dataclass
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import WebUIConfig
from nanobot.session.manager import SessionManager
from nanobot.utils.helpers import safe_filename


@dataclass(frozen=True)
class _ClientKey:
    chat_id: str
    sender_id: str
    session_key: str


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in ("127.0.0.1", "localhost", "::1")

def _token_is_weak(token: str) -> bool:
    """
    Heuristic token strength check for network-exposed WebUI.

    Goal: catch obviously weak/predictable tokens, not prove cryptographic strength.
    """
    t = (token or "").strip()
    if not t:
        return True
    low = t.lower()
    if low in ("token", "changeme", "password", "admin", "nanobot", "secret"):
        return True
    # Require enough length to make guessing impractical.
    if len(t) < 24:
        return True
    # Reject trivially repetitive tokens.
    if len(set(t)) <= 3:
        return True
    return False


class WebUIChannel(BaseChannel):
    """A minimal, high-polish browser UI for chatting with nanobot."""

    name = "webui"
    max_message_chars = None

    _log_sink_id: int | None = None
    _log_buffer: "deque[str]" = deque(maxlen=2000)
    _models_cache: bytes | None = None

    def __init__(self, config: WebUIConfig, bus: MessageBus, *, workspace: Path):
        super().__init__(config, bus)
        self.config: WebUIConfig = config
        self.workspace = Path(workspace)
        self._ensure_log_sink()
        self._sessions = SessionManager(self.workspace)
        self._uploads_dir = (self.workspace / "uploads")
        self._uploads_dir.mkdir(parents=True, exist_ok=True)

        self._server: Any | None = None
        self._started = asyncio.Event()

        self._clients_lock = asyncio.Lock()
        self._clients: dict[Any, _ClientKey] = {}
        self._by_chat: dict[str, set[Any]] = {}
        self._uploads: dict[Any, dict[str, dict[str, Any]]] = {}

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

    def _ensure_log_sink(self) -> None:
        if WebUIChannel._log_sink_id is not None:
            return

        def _sink(message: Any) -> None:
            try:
                r = message.record
                ts = r.get("time").strftime("%Y-%m-%d %H:%M:%S")
                level = r.get("level").name
                name = r.get("name")
                msg = r.get("message")
                line = f"{ts} | {level:<7} | {name} | {msg}"
            except Exception:
                line = str(message)
            WebUIChannel._log_buffer.append(line)

        WebUIChannel._log_sink_id = logger.add(_sink, level="DEBUG")

    def _get_logs_text(self) -> str:
        if not WebUIChannel._log_buffer:
            return "(log buffer empty)\n"
        text = "\n".join(WebUIChannel._log_buffer) + "\n"
        # Cap output to ~200k to keep responses lightweight.
        max_len = 200_000
        if len(text) > max_len:
            text = text[-max_len:]
            text = "[truncated]\n" + text
        return text

    @classmethod
    def _get_models_json(cls) -> bytes:
        """Return a slim JSON list of models from openrouter-models.json (cached)."""
        if cls._models_cache is not None:
            return cls._models_cache
        models_path = Path(__file__).resolve().parent.parent / "providers" / "openrouter-models.json"
        try:
            raw = json.loads(models_path.read_text(encoding="utf-8"))
        except Exception:
            cls._models_cache = b"[]"
            return cls._models_cache
        items = raw.get("data") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            cls._models_cache = b"[]"
            return cls._models_cache
        slim = []
        for m in items:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            pricing = m.get("pricing") or {}
            top = m.get("top_provider") or {}
            arch = m.get("architecture") or {}
            params = m.get("supported_parameters") or []
            slim.append({
                "id": m["id"],
                "name": m.get("name", m["id"]),
                "ctx": m.get("context_length", 0),
                "prompt": pricing.get("prompt", "0"),
                "completion": pricing.get("completion", "0"),
                "maxOut": top.get("max_completion_tokens", 0),
                "tools": "tools" in params,
                "vision": "image" in (arch.get("input_modalities") or []),
            })
        cls._models_cache = json.dumps(slim, separators=(",", ":")).encode("utf-8")
        return cls._models_cache

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
        is_legacy_signature = len(args) >= 2 and isinstance(args[0], str)
        path, _request_headers = self._extract_request_path_and_headers(*args)

        parsed = urlparse(path)
        qs = parse_qs(parsed.query or "")
        token = (qs.get("token") or [""])[0]

        def _reply(status: int, headers: list[tuple[str, str]], body: bytes) -> Any:
            """
            websockets>=12 expects websockets.http11.Response from process_request when using
            the (connection, request) signature. Older versions / legacy signature accept a tuple.
            """
            if is_legacy_signature:
                return (status, headers, body)
            try:
                from websockets.datastructures import Headers
                from websockets.http11 import Response

                reasons = {
                    200: "OK",
                    401: "Unauthorized",
                    404: "Not Found",
                    500: "Internal Server Error",
                }
                return Response(status, reasons.get(status, ""), Headers(headers), body)
            except Exception:
                # Best-effort fallback.
                return (status, headers, body)

        if parsed.path == "/ws":
            return None

        if parsed.path in ("/healthz", "/health"):
            body = b"ok\n"
            return _reply(
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
            return _reply(
                401,
                [
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                ],
                body,
            )

        if parsed.path == "/logs":
            text = self._get_logs_text()
            body = text.encode("utf-8", errors="replace")
            headers = [
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
                ("Content-Disposition", "attachment; filename=nanobot.log"),
            ]
            return _reply(200, headers, body)

        if parsed.path == "/api/models":
            body = self._get_models_json()
            return _reply(
                200,
                [
                    ("Content-Type", "application/json; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "max-age=3600"),
                ],
                body,
            )

        # Serve uploaded files (images, PDFs) from workspace/uploads/
        if parsed.path.startswith("/uploads/"):
            relpath = parsed.path.lstrip("/")
            if ".." not in relpath:
                _UPLOAD_MIMES: dict[str, str] = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                    ".svg": "image/svg+xml",
                    ".pdf": "application/pdf",
                }
                ext = ("." + relpath.rsplit(".", 1)[-1]).lower() if "." in relpath else ""
                mime = _UPLOAD_MIMES.get(ext)
                if mime:
                    try:
                        fpath = (self.workspace / relpath).resolve()
                        # Security: must be inside the uploads directory
                        if self._uploads_dir.resolve() in fpath.parents or fpath == self._uploads_dir.resolve():
                            if fpath.is_file():
                                body = fpath.read_bytes()
                                return _reply(
                                    200,
                                    [
                                        ("Content-Type", mime),
                                        ("Content-Length", str(len(body))),
                                        ("Cache-Control", "public, max-age=86400, immutable"),
                                    ],
                                    body,
                                )
                    except Exception:
                        pass
            body = b"Not found\n"
            return _reply(
                404,
                [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body)))],
                body,
            )

        route = parsed.path
        if route in ("", "/"):
            route = "/index.html"

        _ALLOWED_EXTS = {".html", ".css", ".js", ".svg"}
        relpath = route.lstrip("/")
        ext = ("." + relpath.rsplit(".", 1)[-1]).lower() if "." in relpath else ""
        if ".." not in relpath and ext in _ALLOWED_EXTS:
            body = self._read_asset_bytes(relpath)
            if not body:
                body = b"missing asset\n"
                return _reply(
                    500,
                    [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body)))],
                    body,
                )
            csp = (
                "default-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; "
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
            return _reply(200, headers, body)

        body = b"Not found\n"
        return _reply(
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

    async def _send_settings(self, ws: Any, *, session_key: str) -> None:
        """Send per-session settings (currently just model) to a client."""
        model = ""
        verbosity = ""
        restrict_workspace: bool | None = None
        try:
            session = self._sessions.get_or_create(session_key)
            m = session.metadata.get("model")
            if isinstance(m, str):
                model = m.strip()
            v = session.metadata.get("verbosity")
            if isinstance(v, str):
                verbosity = v.strip()
            rw = session.metadata.get("restrict_workspace")
            if isinstance(rw, bool):
                restrict_workspace = rw
        except Exception:
            model = ""
        await ws.send(
            json.dumps(
                {
                    "type": "settings",
                    "session_key": session_key,
                    "model": model,
                    "verbosity": verbosity,
                    "restrict_workspace": restrict_workspace,
                }
            )
        )

    async def _broadcast_settings(self, *, session_key: str) -> None:
        """Broadcast settings to all connected clients currently bound to session_key."""
        async with self._clients_lock:
            targets = [ws for ws, key in self._clients.items() if key.session_key == session_key]
        for ws in targets:
            try:
                await self._send_settings(ws, session_key=session_key)
            except Exception:
                pass

    async def _send_history(self, ws: Any, *, chat_id: str, session_key: str) -> None:
        try:
            session = self._sessions.get_or_create(session_key, force_reload=True)
            history = session.get_history(max_messages=200)
        except Exception:
            history = []
        await ws.send(json.dumps({"type": "history", "chat_id": chat_id, "session_key": session_key, "messages": history}))

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
        if not _is_loopback_host(self._host):
            tok = (self.config.auth_token or "").strip()
            if _token_is_weak(tok):
                logger.error(
                    "WebUI authToken is too weak for non-loopback binding. "
                    "Use a long, random token (suggestion: 32+ chars from a password manager)."
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

        msg_type = "assistant"
        extra_data: dict[str, Any] | None = None
        try:
            if isinstance(msg.metadata, dict) and isinstance(msg.metadata.get("type"), str):
                msg_type = str(msg.metadata.get("type"))
                extra_data = msg.metadata.get("data") if isinstance(msg.metadata.get("data"), dict) else None
        except Exception:
            msg_type = "assistant"

        payload = {
            "type": msg_type,
            "chat_id": str(msg.chat_id),
            "content": msg.content or "",
            "ts": time.time(),
        }
        if extra_data:
            payload["data"] = extra_data

        payload_json = json.dumps(payload)

        async with self._clients_lock:
            targets = list(self._by_chat.get(str(msg.chat_id), set()))

        for ws in targets:
            try:
                await ws.send(payload_json)
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
        session_key = (qs.get("session") or qs.get("session_key") or [""])[0].strip()
        if not session_key:
            session_key = f"{self.name}:{chat_id}"

        key = _ClientKey(chat_id=str(chat_id), sender_id=str(sender_id), session_key=str(session_key))

        # Disconnect duplicate clients for the same session+sender.
        # Remove old entries from tracking BEFORE closing, so _drop_client
        # on the old ws is a no-op and the client-side close handler doesn't
        # race with a half-cleaned state.
        to_close: list[Any] = []
        async with self._clients_lock:
            for old_ws, old_key in list(self._clients.items()):
                if old_key.session_key == key.session_key and old_key.sender_id == key.sender_id:
                    to_close.append(old_ws)
                    del self._clients[old_ws]
                    s = self._by_chat.get(old_key.chat_id)
                    if s:
                        s.discard(old_ws)
                        if not s:
                            self._by_chat.pop(old_key.chat_id, None)
                    self._uploads.pop(old_ws, None)
            self._clients[ws] = key
            self._by_chat.setdefault(key.chat_id, set()).add(ws)
            self._uploads.setdefault(ws, {})

        if to_close:
            logger.warning(
                f"Duplicate WebUI client detected for session={key.session_key} sender={key.sender_id}; "
                f"disconnecting {len(to_close)} older connection(s)."
            )
            for old_ws in to_close:
                try:
                    await old_ws.close(code=4400, reason="duplicate session")
                except Exception:
                    pass

        await ws.send(json.dumps({"type": "session", "chat_id": key.chat_id, "sender_id": key.sender_id, "session_key": key.session_key}))
        await self._send_history(ws, chat_id=key.chat_id, session_key=key.session_key)
        await self._send_settings(ws, session_key=key.session_key)
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
            uploads = self._uploads.pop(ws, None) or {}

        # Best-effort close any in-flight upload handles.
        for st in uploads.values():
            try:
                fh = st.get("fh")
                if fh:
                    fh.close()
            except Exception:
                pass
            # Remove partial files when the client disconnects mid-upload.
            try:
                expected = int(st.get("expected") or 0)
                received = int(st.get("received") or 0)
                path = st.get("path")
                if expected > 0 and received < expected and path:
                    Path(path).unlink(missing_ok=True)
            except Exception:
                pass

    async def _handle_ws_message(self, ws: Any, raw: Any) -> None:
        if not isinstance(raw, (str, bytes, bytearray)):
            return

        if isinstance(raw, (bytes, bytearray)):
            # Binary frames not used by the current client; ignore for safety.
            return

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

        if msg_type in ("new_chat", "new_session", "new-session"):
            new_chat_id = self._new_id("c")
            async with self._clients_lock:
                old = self._clients.get(ws)
                if old is not None:
                    self._by_chat.get(old.chat_id, set()).discard(ws)
                    self._clients[ws] = _ClientKey(chat_id=new_chat_id, sender_id=old.sender_id, session_key=f"{self.name}:{new_chat_id}")
                    self._by_chat.setdefault(new_chat_id, set()).add(ws)
            await ws.send(json.dumps({"type": "session", "chat_id": new_chat_id, "session_key": f"{self.name}:{new_chat_id}"}))
            await self._send_history(ws, chat_id=new_chat_id, session_key=f"{self.name}:{new_chat_id}")
            await self._send_settings(ws, session_key=f"{self.name}:{new_chat_id}")
            return

        if msg_type in ("list_sessions", "sessions"):
            items = self._sessions.list_sessions()
            await ws.send(json.dumps({"type": "sessions", "sessions": items}))
            return

        if msg_type in ("switch_session", "switch", "load_session"):
            target = (data.get("session_key") or data.get("session") or "").strip()
            if not isinstance(target, str) or not target:
                return
            async with self._clients_lock:
                old = self._clients.get(ws)
                if old is None:
                    return
                # Keep chat_id stable by default, but allow clients to opt into chat_id=session_key.
                new_chat_id = (data.get("chat_id") or "").strip()
                if not isinstance(new_chat_id, str) or not new_chat_id:
                    new_chat_id = old.chat_id

                if new_chat_id != old.chat_id:
                    self._by_chat.get(old.chat_id, set()).discard(ws)
                    self._by_chat.setdefault(new_chat_id, set()).add(ws)

                self._clients[ws] = _ClientKey(chat_id=new_chat_id, sender_id=old.sender_id, session_key=target)

            await ws.send(json.dumps({"type": "session", "chat_id": new_chat_id, "session_key": target}))
            await self._send_history(ws, chat_id=new_chat_id, session_key=target)
            await self._send_settings(ws, session_key=target)
            return

        if msg_type in ("set_model", "model", "setmodel"):
            model = data.get("model")
            if model is None:
                model = ""
            if not isinstance(model, str):
                return
            model = model.strip()
            if len(model) > 160:
                await ws.send(json.dumps({"type": "error", "error": "model name too long"}))
                return

            async with self._clients_lock:
                key = self._clients.get(ws)
            if key is None:
                return

            # Persist per-session preference.
            try:
                session = self._sessions.get_or_create(key.session_key)
                if model:
                    session.metadata["model"] = model
                else:
                    session.metadata.pop("model", None)
                await self._sessions.save_async(session)
            except Exception as e:
                await ws.send(json.dumps({"type": "error", "error": f"failed to save model: {e}"}))
                return

            await self._broadcast_settings(session_key=key.session_key)
            return

        if msg_type in ("set_verbosity", "verbosity"):
            verbosity = data.get("verbosity")
            if verbosity is None:
                verbosity = ""
            if not isinstance(verbosity, str):
                return
            verbosity = verbosity.strip().lower()
            if verbosity and verbosity not in ("low", "normal", "high"):
                await ws.send(json.dumps({"type": "error", "error": "invalid verbosity"}))
                return

            async with self._clients_lock:
                key = self._clients.get(ws)
            if key is None:
                return

            try:
                session = self._sessions.get_or_create(key.session_key)
                if verbosity:
                    session.metadata["verbosity"] = verbosity
                else:
                    session.metadata.pop("verbosity", None)
                await self._sessions.save_async(session)
            except Exception as e:
                await ws.send(json.dumps({"type": "error", "error": f"failed to save verbosity: {e}"}))
                return

            await self._broadcast_settings(session_key=key.session_key)
            return

        if msg_type in ("set_restrict_workspace", "restrict_workspace"):
            rw = data.get("restrict_workspace")
            if isinstance(rw, str):
                rw = rw.strip().lower()
                if rw in ("true", "1", "yes", "on"):
                    rw = True
                elif rw in ("false", "0", "no", "off"):
                    rw = False
                else:
                    rw = None
            if not isinstance(rw, bool):
                await ws.send(json.dumps({"type": "error", "error": "invalid restrict_workspace"}))
                return

            async with self._clients_lock:
                key = self._clients.get(ws)
            if key is None:
                return

            try:
                session = self._sessions.get_or_create(key.session_key)
                session.metadata["restrict_workspace"] = rw
                await self._sessions.save_async(session)
            except Exception as e:
                await ws.send(
                    json.dumps({"type": "error", "error": f"failed to save restrict_workspace: {e}"})
                )
                return

            await self._broadcast_settings(session_key=key.session_key)
            return

        if msg_type in ("subagent_list", "subagents"):
            async with self._clients_lock:
                key = self._clients.get(ws)
            if key is None:
                return
            await self._handle_message(
                sender_id=key.sender_id,
                chat_id=key.chat_id,
                content="",
                metadata={
                    "client": "webui",
                    "session_key": key.session_key,
                    "control": {"action": "subagent_list"},
                },
            )
            return

        if msg_type in ("subagent_spawn", "spawn_subagent"):
            task = str(data.get("task") or "").strip()
            label = str(data.get("label") or "").strip() if isinstance(data.get("label"), str) else ""
            if not task:
                await ws.send(json.dumps({"type": "error", "error": "missing task"}))
                return
            async with self._clients_lock:
                key = self._clients.get(ws)
            if key is None:
                return
            await self._handle_message(
                sender_id=key.sender_id,
                chat_id=key.chat_id,
                content="",
                metadata={
                    "client": "webui",
                    "session_key": key.session_key,
                    "control": {"action": "subagent_spawn", "task": task, "label": label},
                },
            )
            return

        if msg_type in ("subagent_cancel", "cancel_subagent"):
            task_id = str(data.get("task_id") or "").strip()
            if not task_id:
                await ws.send(json.dumps({"type": "error", "error": "missing task_id"}))
                return
            async with self._clients_lock:
                key = self._clients.get(ws)
            if key is None:
                return
            await self._handle_message(
                sender_id=key.sender_id,
                chat_id=key.chat_id,
                content="",
                metadata={
                    "client": "webui",
                    "session_key": key.session_key,
                    "control": {"action": "subagent_cancel", "task_id": task_id},
                },
            )
            return

        if msg_type in ("upload_init",):
            client_id = data.get("client_id")
            filename = data.get("filename")
            mime = data.get("mime")
            size = data.get("size")
            if client_id is not None and not isinstance(client_id, str):
                client_id = None
            if not isinstance(filename, str) or not filename:
                return
            if not isinstance(mime, str) or not mime:
                return
            try:
                size_i = int(size)
            except Exception:
                return
            if size_i <= 0 or size_i > 15 * 1024 * 1024:
                await ws.send(json.dumps({"type": "error", "error": "upload too large (max 15MB)"}))
                return
            if not (mime.startswith("image/") or mime == "application/pdf"):
                await ws.send(json.dumps({"type": "error", "error": f"unsupported upload type: {mime}"}))
                return

            upload_id = self._new_id("up").replace("up:", "")
            safe_name = safe_filename(filename)
            ext = Path(safe_name).suffix
            if not ext:
                if mime == "application/pdf":
                    ext = ".pdf"
                elif mime == "image/png":
                    ext = ".png"
                elif mime in ("image/jpeg", "image/jpg"):
                    ext = ".jpg"
                elif mime == "image/gif":
                    ext = ".gif"
                else:
                    ext = ""

            dest = self._uploads_dir / f"{upload_id}_{safe_name}"
            if ext and not str(dest).lower().endswith(ext.lower()):
                dest = Path(str(dest) + ext)

            try:
                f = open(dest, "wb")
            except Exception as e:
                await ws.send(json.dumps({"type": "error", "error": f"failed to open upload file: {e}"}))
                return

            async with self._clients_lock:
                self._uploads.setdefault(ws, {})[upload_id] = {
                    "path": dest,
                    "fh": f,
                    "expected": size_i,
                    "received": 0,
                    "mime": mime,
                    "filename": filename,
                    "client_id": client_id or "",
                }

            rel = str(dest.relative_to(self.workspace)).replace("\\", "/")
            await ws.send(json.dumps({"type": "upload_ready", "client_id": client_id or "", "upload_id": upload_id, "path": rel}))
            return

        if msg_type in ("upload_chunk",):
            upload_id = (data.get("upload_id") or "").strip()
            b64 = data.get("data")
            if not isinstance(upload_id, str) or not upload_id:
                return
            if not isinstance(b64, str) or not b64:
                return

            async with self._clients_lock:
                st = self._uploads.get(ws, {}).get(upload_id)
                if not st:
                    st = None
                else:
                    # Keep a reference to the stored dict so updates persist.
                    pass

            if st is None:
                await ws.send(json.dumps({"type": "error", "error": "unknown upload id"}))
                return

            try:
                chunk = base64.b64decode(b64.encode("ascii"), validate=False)
            except Exception:
                await ws.send(json.dumps({"type": "error", "error": "invalid base64 chunk"}))
                return

            try:
                fh = st.get("fh")
                if fh:
                    fh.write(chunk)
                    fh.flush()
            except Exception as e:
                await ws.send(json.dumps({"type": "error", "error": f"failed writing upload: {e}"}))
                return

            async with self._clients_lock:
                # st is the stored dict; mutate under lock.
                st["received"] = int(st.get("received") or 0) + len(chunk)
                received = int(st.get("received") or 0)
                expected = int(st.get("expected") or 0)
                path = st.get("path")
                fh2 = st.get("fh")

            if received > expected:
                try:
                    if fh2:
                        fh2.close()
                except Exception:
                    pass
                try:
                    if path:
                        Path(path).unlink(missing_ok=True)
                except Exception:
                    pass
                await ws.send(json.dumps({"type": "error", "error": "upload exceeded expected size"}))
                return

            if received == expected:
                try:
                    if fh2:
                        fh2.close()
                except Exception:
                    pass
                rel = str(Path(path).relative_to(self.workspace)).replace("\\", "/") if path else ""
                client_id = ""
                try:
                    client_id = str(st.get("client_id") or "")
                except Exception:
                    client_id = ""
                await ws.send(json.dumps({"type": "upload_done", "client_id": client_id, "upload_id": upload_id, "path": rel}))
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

        model = data.get("model")
        model_s: str | None = None
        if isinstance(model, str) and model.strip():
            model_s = model.strip()

        media = data.get("media")
        media_paths: list[str] = []
        if isinstance(media, list):
            for item in media:
                if not isinstance(item, str) or not item:
                    continue
                # Only allow files within the workspace to be attached.
                try:
                    p = (self.workspace / item).resolve() if not Path(item).is_absolute() else Path(item).resolve()
                    if self.workspace.resolve() not in p.parents and p != self.workspace.resolve():
                        continue
                    if p.is_file():
                        media_paths.append(str(p))
                except Exception:
                    continue

        await self._handle_message(
            sender_id=key.sender_id,
            chat_id=key.chat_id,
            content=content,
            media=media_paths,
            metadata={
                "client": "webui",
                "session_key": key.session_key,
                **({"model": model_s} if model_s else {}),
            },
        )
