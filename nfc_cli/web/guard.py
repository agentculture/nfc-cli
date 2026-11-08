"""Request guard for the local web server (cited from embodiment ``http/guard.py``).

Loopback is not authentication: any web page in the user's browser can reach 127.0.0.1. So
mutating requests need an install secret (kept in a 0600 file, sent as ``Authorization:
Bearer``) and every request must carry an allow-listed ``Host`` (DNS-rebinding defence) and,
when present, an allow-listed ``Origin``.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from nfc_cli.nfc.errors import NfcError

__all__ = ["Guard", "GuardError", "load_secret", "secret_path"]


class GuardError(Exception):
    def __init__(self, status: int, message: str, remediation: str):
        super().__init__(message)
        self.status = status
        self.message = message
        self.remediation = remediation


def secret_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "nfc-cli" / "web-secret"


def load_secret(path: Path | None = None) -> str:
    """Return the install secret, creating it (0600, in a 0700 dir) on first use."""
    path = path or secret_path()
    try:
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                path.chmod(0o600)
                return value
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        value = secrets.token_urlsafe(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value + "\n")
        path.chmod(0o600)
        return value
    except OSError as exc:
        raise NfcError(
            f"cannot read or create the web install secret at {path}: {exc}",
            "make sure $XDG_CONFIG_HOME (or ~/.config) is writable",
        ) from None


class Guard:
    def __init__(self, secret: str, bound_host: str, port: int):
        self.secret = secret
        names = {"127.0.0.1", "localhost", "[::1]", bound_host}
        if ":" in bound_host and not bound_host.startswith("["):
            names.add(f"[{bound_host}]")
        self.hosts = {f"{n}:{port}" for n in names}
        self.origins = {f"http://{h}" for h in self.hosts}

    def check_host_origin(self, headers: dict) -> None:
        host = (headers.get("Host") or "").lower()
        if host not in {h.lower() for h in self.hosts}:
            raise GuardError(
                403, f"host {host!r} is not allowed", "browse to http://127.0.0.1:<port>/"
            )
        origin = headers.get("Origin")
        if origin is not None and origin.lower() not in {o.lower() for o in self.origins}:
            raise GuardError(
                403, f"cross-origin request from {origin!r} refused", "use the local UI itself"
            )

    def check_secret(self, headers: dict) -> None:
        auth = headers.get("Authorization") or ""
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            token.strip().encode(), self.secret.encode()
        ):
            raise GuardError(
                401,
                "missing or wrong install secret",
                "send 'Authorization: Bearer <secret>' (the secret is in "
                f"{secret_path()}; the served UI embeds it as <meta name=nfc-cli-secret>)",
            )
