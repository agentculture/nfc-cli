"""The MCP server: low-level ``mcp`` SDK server whose tools come from the registry.

Cited (cite-don't-import) from culture-rules ``culture_rules/mcp/server.py``. Needs
``pip install 'nfc-cli[mcp]'``; the SDK is imported lazily so ``import nfc_cli.mcp`` never
pulls it in.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nfc_cli.mcp import tools
from nfc_cli.nfc.errors import NfcError

__all__ = ["McpExtraMissing", "build_server", "run_stdio"]

_HINT = "pip install 'nfc-cli[mcp]'"


class McpExtraMissing(NfcError):
    default_message = "the MCP server needs the 'mcp' extra"
    default_remediation = _HINT

    def __init__(self, message: str | None = None, remediation: str | None = None):
        super().__init__(message, remediation or _HINT)


def _sdk() -> tuple[Any, Any]:
    try:
        import mcp.types as types  # noqa: PLC0415 - optional extra
        from mcp.server.lowlevel import Server  # noqa: PLC0415
    except ImportError as exc:
        raise McpExtraMissing() from exc
    return Server, types


def build_server(transport_factory: Any = None):
    """An MCP ``Server`` exposing every registry verb."""
    server_cls, types = _sdk()
    server = server_cls("nfc-cli")

    @server.list_tools()
    async def _list() -> list[Any]:
        return [types.Tool(**spec) for spec in tools.tool_specs()]

    @server.call_tool(validate_input=False)
    async def _call(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        try:
            result = await asyncio.to_thread(tools.call_tool, name, arguments, transport_factory)
        except tools.ToolError as exc:
            raise ValueError(str(exc)) from exc  # the SDK turns this into isError
        return [types.TextContent(type="text", text=tools.dumps(result))]

    return server


def run_stdio() -> None:
    """Serve over stdio until the client disconnects (raises McpExtraMissing without the SDK)."""
    try:
        import anyio  # noqa: PLC0415 - optional extra (an mcp dependency)
        from mcp.server.stdio import stdio_server  # noqa: PLC0415
    except ImportError as exc:
        raise McpExtraMissing() from exc
    server = build_server()

    async def main() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(main)
