"""MCP tools derived from the verb registry (SDK-free).

Cited (cite-don't-import) from culture-rules ``culture_rules/mcp/tools.py``. Every call goes
through :func:`nfc_cli.verbs.invoke` with ``surface="mcp"`` so the trailer gate and the audit
log apply. Arguments are validated strictly (a string ``"true"`` is never a boolean); a
mutating verb is a dry-run unless ``apply`` is exactly JSON ``true``; key bytes are masked
unless the caller passes ``include_keys=true``.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Callable

from nfc_cli import verbs
from nfc_cli.nfc import ops
from nfc_cli.nfc.errors import NfcError
from nfc_cli.registry import REGISTRY, ArgumentError, Context, mcp_tool_spec, validate_arguments

__all__ = ["ToolError", "call_tool", "make_transport_factory", "tool_specs"]

_READER_SCHEMA = {
    "type": ["string", "integer"],
    "description": "reader index or BUS:ADDR (default: the first ACR1552)",
}
_USER_ERRORS = (ArgumentError, ops.InvalidInput, verbs.TrailerFlagRequired)


class ToolError(Exception):
    """A tool call failed; ``kind`` is ``user`` or ``env``. Carries message + remediation."""

    def __init__(self, message: str, remediation: str, kind: str):
        super().__init__(message)
        self.message = message
        self.remediation = remediation
        self.kind = kind

    def __str__(self) -> str:
        return f"{self.message}\nhint: {self.remediation}"

    def payload(self) -> dict[str, str]:
        return {"message": self.message, "remediation": self.remediation, "kind": self.kind}


def tool_specs() -> list[dict[str, Any]]:
    """One MCP tool per registry verb (``noun_verb``); ``reader`` is added to every schema."""
    specs = []
    for verb in REGISTRY.verbs():
        spec = mcp_tool_spec(verb)
        schema = spec["inputSchema"]
        schema["properties"] = {**schema["properties"], "reader": dict(_READER_SCHEMA)}
        specs.append(spec)
    return specs


_fake_lock = threading.Lock()
_fake_cache: dict[str, Any] = {}


def make_transport_factory(reader: int | str | None = None) -> Callable[[], Any]:
    """``NFC_CLI_FAKE`` -> one shared fake per process; otherwise a real USB CCID transport."""
    scenario = os.environ.get("NFC_CLI_FAKE")
    if scenario:

        def fake() -> Any:
            with _fake_lock:
                if scenario not in _fake_cache:
                    from nfc_cli.nfc.fake import load_scenario

                    _fake_cache[scenario] = load_scenario(scenario)
                return _fake_cache[scenario]

        return fake

    def usb() -> Any:
        from nfc_cli.nfc.transport import UsbCcidTransport

        return UsbCcidTransport(reader=reader, trace=None)

    return usb


def call_tool(
    name: str,
    arguments: dict[str, Any] | None,
    transport_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Run tool ``name``; returns the JSON-serialisable result or raises :class:`ToolError`."""
    verb = REGISTRY.by_tool_name(name)
    if verb is None:
        raise ToolError(
            f"unknown tool {name!r}",
            "list the available tools with tools/list",
            "user",
        )
    try:
        args = dict(arguments or {})
        reader = args.pop("reader", None)
        if reader is not None and (isinstance(reader, bool) or not isinstance(reader, (str, int))):
            raise ArgumentError("reader must be a string or integer")
        params, apply = validate_arguments(verb, args)
        ctx = Context(
            apply=apply,
            transport_factory=transport_factory or make_transport_factory(reader),
            include_keys=False,
        )
        return verbs.invoke(verb, params, ctx, surface="mcp")
    except _USER_ERRORS as exc:
        remediation = getattr(exc, "remediation", "check the tool's inputSchema and retry")
        raise ToolError(str(exc), remediation, "user") from None
    except NfcError as exc:
        raise ToolError(exc.message, exc.remediation, "env") from None
    except Exception as exc:  # noqa: BLE001 - never leak a traceback over MCP
        raise ToolError(
            f"{type(exc).__name__}: {exc}", "file a bug with the tool name and arguments", "env"
        ) from None


def dumps(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)
