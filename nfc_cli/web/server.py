"""Local web server: static UI plus ``/api/`` generated from the verb registry.

Cited from embodiment ``http/server.py`` (ThreadingHTTPServer, loopback-only bind). Stdlib only.
Every POST goes through :func:`nfc_cli.verbs.invoke` with ``surface="api"`` so the trailer gate
and audit log behave exactly as over MCP.
"""

from __future__ import annotations

import html
import ipaddress
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from nfc_cli import verbs
from nfc_cli.nfc import ops
from nfc_cli.nfc import reader as reader_mod
from nfc_cli.nfc.errors import NfcError, ReaderBusy
from nfc_cli.registry import (
    REGISTRY,
    ArgumentError,
    Context,
    Verb,
    api_route,
    validate_arguments,
)
from nfc_cli.web.guard import Guard, GuardError, load_secret

__all__ = ["BindRefused", "DEFAULT_HOST", "DEFAULT_PORT", "make_server", "resolve_bind", "serve"]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770  # avoids 8765/8791/8796/8823 used by sibling repos
MAX_BODY = 1_000_000
_INDEX_HTML = "index.html"
_HEAD_CLOSE = "</head>"
POLL_INTERVAL = 1.0
_USER_ERRORS = (ArgumentError, ops.InvalidInput, verbs.TrailerFlagRequired)


class BindRefused(NfcError):
    default_message = "refusing to bind a non-loopback address"
    default_remediation = "use 127.0.0.1 / ::1 / localhost, or pass --bind-public to expose it"


def resolve_bind(host: str | None, bind_public: bool = False) -> str:
    """Return the host to bind; a routable host needs ``bind_public``. Opens no socket."""
    host = host or os.environ.get("NFC_CLI_HOST") or DEFAULT_HOST
    if host == "localhost":
        return "127.0.0.1"
    try:
        loopback = ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        loopback = False
    if not loopback and not bind_public:
        raise BindRefused(
            f"refusing to bind {host!r}: the reader would be reachable from the network",
            "bind a loopback address, or pass --bind-public if you really mean it "
            "(the install secret still guards writes)",
        )
    return host


def _resolve_port(port: int | None) -> int:
    if port is not None:
        return port
    env = os.environ.get("NFC_CLI_PORT")
    if env:
        try:
            return int(env)
        except ValueError:
            raise NfcError(f"NFC_CLI_PORT={env!r} is not a port number", "unset it") from None
    return DEFAULT_PORT


def _dist_dir() -> Path | None:
    for cand in (Path(__file__).parent / "dist", Path(__file__).resolve().parents[2] / "web/dist"):
        if (cand / _INDEX_HTML).is_file():
            return cand
    return None


def _error_body(exc: Exception, kind: str) -> dict:
    return {
        "ok": False,
        "error": {
            "message": getattr(exc, "message", None) or str(exc),
            "remediation": getattr(exc, "remediation", "see `nfc-cli explain`"),
            "kind": kind,
        },
    }


class AppState:
    """Per-server reader plumbing: one lock so polls never fight a running verb."""

    def __init__(self, factory: Callable[[], Any]):
        self.factory = factory
        self.lock = threading.Lock()
        self.firmware: str | None = None
        self.last: dict = {
            "reader": {"connected": False, "firmware": None, "error": "not polled yet"},
            "card": {"present": False, "uid": None, "family": None},
        }

    def snapshot(self) -> dict:
        """Current reader/card state. The reader claim is held only for this one poll."""
        if not self.lock.acquire(blocking=False):
            return self.last  # a verb is using the reader; report what we last saw
        try:
            self.last = self._poll()
        finally:
            self.lock.release()
        return self.last

    def _poll(self) -> dict:
        reader: dict = {"connected": False, "firmware": self.firmware, "error": None}
        card: dict = {"present": False, "uid": None, "family": None}
        t = None
        try:
            t = self.factory()
            with t.claim() as c:
                reader["connected"] = True
                if self.firmware is None:
                    try:
                        self.firmware = reader["firmware"] = reader_mod.firmware(c)
                    except NfcError:
                        pass
                if c.present():
                    card["present"] = True
                    try:
                        info = ops.uid(c)
                        card["uid"], card["family"] = info.get("uid"), info.get("family")
                    except NfcError:
                        pass
        except ReaderBusy as exc:
            reader.update(connected=True, error=exc.message)
        except NfcError as exc:
            reader["error"] = exc.message
        except Exception as exc:  # noqa: BLE001
            # never leak a traceback to the UI
            reader["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            closer = getattr(t, "close", None)
            if closer is not None:
                try:
                    closer()
                except Exception:  # noqa: BLE001  # nosec B110
                    pass
        return {"reader": reader, "card": card}


class Handler(BaseHTTPRequestHandler):
    server_version = "nfc-cli"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    # -- plumbing
    @property
    def app(self) -> "NfcHTTPServer":
        return self.server  # type: ignore[return-value]

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _guard(self, need_secret: bool) -> bool:
        try:
            self.app.guard.check_host_origin(self.headers)
            if need_secret:
                self.app.guard.check_secret(self.headers)
        except GuardError as exc:
            self._json(
                exc.status,
                {
                    "ok": False,
                    "error": {
                        "message": exc.message,
                        "remediation": exc.remediation,
                        "kind": "user",
                    },
                },
            )
            return False
        return True

    # -- GET
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not self._guard(need_secret=False):
            return
        if path == "/api/state":
            return self._json(200, self.app.state.snapshot())
        if path == "/api/events":
            return self._events()
        if path.startswith("/api/"):
            verb = self._verb_for(path)
            if verb is not None:
                return self._json(
                    405, _error_body(NfcError("use POST", "POST JSON params"), "user")
                )
            return self._json(404, _error_body(NfcError("no such route", "see /api"), "user"))
        self._static(path)

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        last = None
        try:
            while not self.app.stopping.is_set():
                snap = self.app.state.snapshot()
                cur = {"present": snap["card"]["present"], "uid": snap["card"]["uid"]}
                if cur != last:
                    last = cur
                    self.wfile.write(f"event: card\ndata: {json.dumps(cur)}\n\n".encode())
                else:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                self.app.stopping.wait(self.app.poll_interval)
        except OSError:
            pass

    def _static(self, path: str) -> None:
        dist = self.app.dist
        if dist is None:
            msg = "nfc-cli web UI is not built (run `npm run build` in web/); the /api/ is live.\n"
            return self._send(200, msg.encode(), "text/plain; charset=utf-8")
        # Resolve the request path strictly inside dist; anything else is the SPA fallback.
        # ``..`` and absolute segments are stripped before joining, and containment is then
        # re-checked on the resolved path, so a request can never read outside dist.
        root = dist.resolve()
        safe_parts = [
            part
            for part in PurePosixPath(path.lstrip("/")).parts
            if part not in ("", ".", "..") and ":" not in part
        ]
        target = root.joinpath(*safe_parts).resolve() if safe_parts else root / _INDEX_HTML
        if target != root and root not in target.parents:
            target = root / _INDEX_HTML  # escaped containment -> SPA fallback
        if not target.is_file():
            target = root / _INDEX_HTML  # SPA fallback for unknown routes
        data = target.read_bytes()
        if target.name == _INDEX_HTML and self.app.embed_secret:
            meta = f'<meta name="nfc-cli-secret" content="{html.escape(self.app.secret)}">'
            text = data.decode("utf-8")
            text = (
                text.replace(_HEAD_CLOSE, meta + _HEAD_CLOSE, 1)
                if _HEAD_CLOSE in text
                else meta + text
            )
            data = text.encode("utf-8")
        import mimetypes

        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, data, ctype)

    # -- POST
    @staticmethod
    def _verb_for(path: str):
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api":
            return REGISTRY.get(parts[1], parts[2])
        return None

    def _body_length(self) -> int | None:
        """Parse Content-Length; send a 400 and return None if bad or oversized."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self._json(400, _error_body(NfcError("bad or oversized body", "send JSON"), "user"))
            return None
        return length

    def _run_verb(self, verb: Verb, raw: bytes) -> Any:
        """Parse the JSON body, validate arguments, and invoke the verb (may raise)."""
        body = json.loads(raw) if raw.strip() else {}
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
        params, apply = validate_arguments(verb, body)
        ctx = Context(apply=apply, transport_factory=self.app.factory)
        with self.app.state.lock:
            return verbs.invoke(verb, params, ctx, surface="api")

    def _handle_verb_error(self, exc: Exception) -> None:
        """Map an exception from _run_verb to the right HTTP status + error body."""
        if isinstance(exc, (json.JSONDecodeError, ValueError)):
            kind = exc if isinstance(exc, ArgumentError) else ArgumentError(str(exc))
            return self._json(400, _error_body(kind, "user"))
        if isinstance(exc, _USER_ERRORS):
            return self._json(400, _error_body(exc, "user"))
        if isinstance(exc, ReaderBusy):
            return self._json(409, _error_body(exc, "env"))
        if isinstance(exc, NfcError):
            return self._json(503, _error_body(exc, "env"))
        err = NfcError(f"{type(exc).__name__}: {exc}", "file a bug with the request details")
        return self._json(500, _error_body(err, "env"))

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not self._guard(need_secret=True):
            return
        verb = self._verb_for(path)
        if verb is None or api_route(verb) != path:
            return self._json(404, _error_body(NfcError("no such route", "see /api"), "user"))
        length = self._body_length()
        if length is None:
            return
        raw = self.rfile.read(length) if length else b""
        try:
            result = self._run_verb(verb, raw)
        except Exception as exc:  # noqa: BLE001
            # map every error to a clean body; never leak a traceback
            return self._handle_verb_error(exc)
        self._json(200, {"ok": True, "result": result})


class NfcHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], factory: Callable[[], Any], secret: str):
        self.address_family = _family(addr[0])
        super().__init__(addr, Handler)
        self.factory = factory
        self.secret = secret
        # Only hand the write secret to the page on a loopback bind. On a public bind,
        # anyone who can load the page would otherwise read the secret and defeat the
        # guard, so there the operator passes it deliberately (printed by serve()).
        self.embed_secret = _is_loopback(addr[0])
        self.state = AppState(factory)
        self.dist = _dist_dir()
        self.stopping = threading.Event()
        self.poll_interval = POLL_INTERVAL
        self.guard = Guard(secret, addr[0], self.server_address[1])

    def server_close(self) -> None:
        self.stopping.set()
        super().server_close()


def _family(host: str):
    import socket

    return socket.AF_INET6 if ":" in host.strip("[]") else socket.AF_INET


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return host in ("localhost", "")


def _make_factory(fake: str | bool | None) -> Callable[[], Any]:
    scenario = "x3" if fake is True else (fake or os.environ.get("NFC_CLI_FAKE"))
    if scenario:
        from nfc_cli.nfc.fake import load_scenario

        shared = load_scenario(str(scenario))
        return lambda: shared

    def make() -> Any:
        from nfc_cli.nfc.transport import UsbCcidTransport

        return UsbCcidTransport()

    return make


def make_server(
    host: str | None = None,
    port: int | None = None,
    bind_public: bool = False,
    fake: str | bool | None = None,
    secret: str | None = None,
) -> NfcHTTPServer:
    """Build (and bind) the server without serving; ``port=0`` picks an ephemeral port."""
    bind = resolve_bind(host, bind_public)
    factory = _make_factory(fake)
    try:
        return NfcHTTPServer((bind, _resolve_port(port)), factory, secret or load_secret())
    except OSError as exc:
        raise NfcError(
            f"cannot listen on {bind}: {exc}", "pick another port with --port or NFC_CLI_PORT"
        ) from None


def serve(
    host: str | None = None,
    port: int | None = None,
    bind_public: bool = False,
    fake: str | bool | None = None,
) -> None:
    srv = make_server(host, port, bind_public, fake)
    h, p = srv.server_address[:2]
    print(f"nfc-cli web UI on http://{h}:{p}/  (Ctrl-C to stop)", flush=True)
    if not srv.embed_secret:
        # Public bind: the page does not carry the secret, so give it to the operator here.
        print(
            f"public bind: writes require this secret (paste it in the UI): {srv.secret}",
            file=sys.stderr,
            flush=True,
        )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
