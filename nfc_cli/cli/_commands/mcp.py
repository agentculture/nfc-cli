"""``nfc-cli mcp`` — serve every registry verb as an MCP tool over stdio (lazy import)."""

from __future__ import annotations

import argparse

from nfc_cli.cli._commands._nfcgroup import to_cli_error
from nfc_cli.nfc.errors import NfcError


def cmd_mcp(args: argparse.Namespace) -> int:
    try:
        from nfc_cli.mcp.server import run_stdio

        run_stdio()
    except NfcError as err:  # McpExtraMissing and friends -> exit 2 with its hint
        raise to_cli_error(err) from None
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("mcp", help="Run the MCP server on stdio (needs the [mcp] extra).")
    p.add_argument("--json", action="store_true", help="Emit structured JSON errors.")
    p.set_defaults(func=cmd_mcp)
