"""``nfc-cli ui`` — the local web UI server (lazy import of :mod:`nfc_cli.web.server`)."""

from __future__ import annotations

import argparse

from nfc_cli.cli._commands._nfcgroup import to_cli_error
from nfc_cli.cli._commands.overview import emit_overview
from nfc_cli.cli._errors import EXIT_USER_ERROR, CliError
from nfc_cli.nfc.errors import NfcError


def ui_sections() -> list[dict[str, object]]:
    return [
        {
            "title": "Verbs",
            "items": [
                "ui serve — serve the web UI and /api (binds 127.0.0.1 unless --bind-public)",
                "ui overview — this snapshot",
            ],
        },
        {
            "title": "Conventions",
            "items": [
                "--fake SCENARIO serves a fake reader (no hardware), e.g. --fake x3",
                "exit codes: 0 success, 1 user error (refused bind), 2 environment error",
            ],
        },
    ]


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        from nfc_cli.web.server import BindRefused, serve  # noqa: F401
    except ImportError as exc:
        raise to_cli_error(
            NfcError(f"web UI server unavailable: {exc}", "reinstall nfc-cli")
        ) from None
    try:
        serve(
            host=args.host,
            port=args.port,
            bind_public=bool(args.bind_public),
            fake=args.fake,
        )
    except BindRefused as err:
        raise CliError(EXIT_USER_ERROR, err.message, err.remediation) from None
    except NfcError as err:
        raise to_cli_error(err) from None
    return 0


def cmd_overview(args: argparse.Namespace) -> int:
    emit_overview("nfc-cli ui", ui_sections(), json_mode=bool(getattr(args, "json", False)))
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("ui", help="Local web UI (see 'nfc-cli ui overview').")
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_overview, json=False)
    usub = p.add_subparsers(dest="ui_command", parser_class=type(p))
    sv = usub.add_parser("serve", help="Serve the web UI and /api on localhost.")
    sv.add_argument("--host", help="Bind address (default 127.0.0.1).")
    sv.add_argument("--port", type=int, help="Port (default: server default).")
    sv.add_argument(
        "--bind-public", action="store_true", help="Allow binding a non-loopback address."
    )
    sv.add_argument("--fake", metavar="SCENARIO", help="Serve a fake reader scenario (e.g. x3).")
    sv.add_argument("--json", action="store_true", help="Emit structured JSON errors.")
    sv.set_defaults(func=cmd_serve)
    ov = usub.add_parser("overview", help="Describe the ui surface.")
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_overview)
