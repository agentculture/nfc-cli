"""Shared machinery for the registry-generated noun groups (reader / tag / ndef).

Subcommands are *derived* from :data:`nfc_cli.registry.REGISTRY` via
:func:`nfc_cli.registry.add_subparser`; nothing here lists verbs by hand. Each handler
collects the parsed params, calls :func:`nfc_cli.verbs.invoke` (``surface="cli"``, so the
trailer gate + audit always run) and renders the result dict.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from nfc_cli.cli._commands.overview import emit_overview
from nfc_cli.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from nfc_cli.cli._output import emit_result
from nfc_cli.nfc.errors import NfcError
from nfc_cli.registry import REGISTRY, Context, Verb, add_subparser

NOUN_HELP = {
    "reader": "ACR1552 reader operations (list, info).",
    "tag": "Tag operations (info, uid, wait, read, write, dump, restore, apdu).",
    "ndef": "NDEF operations (read, write, erase, format).",
}

_fake_instance: dict[str, Any] = {}


def _user_error_types() -> tuple[type[BaseException], ...]:
    from nfc_cli.nfc import ops
    from nfc_cli.verbs import TrailerFlagRequired

    return (ops.InvalidInput, TrailerFlagRequired)


def to_cli_error(err: NfcError) -> CliError:
    """Map an NfcError onto the CLI exit-code policy (user error 1, environment error 2)."""
    code = EXIT_USER_ERROR if isinstance(err, _user_error_types()) else EXIT_ENV_ERROR
    return CliError(code=code, message=err.message, remediation=err.remediation)


def make_transport_factory(reader: str | None, trace_stream: Any) -> Any:
    """NFC_CLI_FAKE=<scenario> -> one shared fake per process, else raw USB CCID."""
    scenario = os.environ.get("NFC_CLI_FAKE")
    if scenario:

        def fake() -> Any:
            if scenario not in _fake_instance:
                from nfc_cli.nfc.fake import load_scenario

                _fake_instance[scenario] = load_scenario(scenario)
            return _fake_instance[scenario]

        return fake

    def usb() -> Any:
        from nfc_cli.nfc.transport import UsbCcidTransport

        sel: int | str | None = reader
        if reader is not None and reader.isdigit():
            sel = int(reader)
        return UsbCcidTransport(reader=sel, trace=trace_stream)

    return usb


# --- rendering ----------------------------------------------------------------------------


def _scalar(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def _table(rows: list[dict[str, Any]], indent: str) -> list[str]:
    cols = list(dict.fromkeys(k for r in rows for k in r))
    cells = [[_scalar(r.get(c)) for c in cols] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    fmt = indent + "  ".join(f"{{:<{w}}}" for w in widths)
    out = [fmt.format(*cols).rstrip(), indent + "  ".join("-" * w for w in widths)]
    out += [fmt.format(*row).rstrip() for row in cells]
    return out


def _kv_lines(k: str, v: Any, indent: str) -> list[str]:
    """Render one ``key: value`` entry (recursing for nested dicts / tables)."""
    if isinstance(v, dict) and v:
        return [f"{indent}{k}:", *_lines(v, indent + "  ")]
    if isinstance(v, list) and v and all(isinstance(i, dict) for i in v):
        return [f"{indent}{k}:", *_table(v, indent + "  ")]
    if isinstance(v, list) and v:
        return [f"{indent}{k}: " + ", ".join(_scalar(i) for i in v)]
    return [f"{indent}{k}: {_scalar(v) if v not in ({}, []) else '-'}"]


def _lines(value: Any, indent: str = "") -> list[str]:
    if not isinstance(value, dict):
        return [f"{indent}{_scalar(value)}"]
    out: list[str] = []
    for k, v in value.items():
        out += _kv_lines(k, v, indent)
    return out


def render_text(result: Any) -> str:
    return "\n".join(_lines(result))


# --- handlers -----------------------------------------------------------------------------


def run_verb(args: argparse.Namespace) -> int:
    from nfc_cli.verbs import invoke

    verb: Verb = args.registry_verb
    params = {
        p.name: getattr(args, p.name)
        for p in verb.params
        if getattr(args, p.name, None) is not None
    }
    trace = sys.stderr if getattr(args, "trace", False) else None
    ctx = Context(
        apply=bool(getattr(args, "apply", False)),
        trace=trace,
        transport_factory=make_transport_factory(getattr(args, "reader", None), trace),
    )
    json_mode = bool(getattr(args, "json", False))
    try:
        result = invoke(verb, params, ctx, surface="cli")
    except NfcError as err:
        raise to_cli_error(err) from None
    emit_result(result if json_mode else render_text(result), json_mode=json_mode)
    return 0


def noun_sections(noun: str) -> list[dict[str, object]]:
    verbs = [f"{noun} {v.verb} — {v.summary}" for v in REGISTRY.verbs(noun)]
    return [
        {"title": "Verbs", "items": verbs + [f"{noun} overview — this snapshot"]},
        {
            "title": "Conventions",
            "items": [
                "every verb accepts --json, --trace (APDU trace on stderr), --reader",
                "mutating verbs are a dry-run unless --apply is given",
                "exit codes: 0 success, 1 user error, 2 environment error",
            ],
        },
    ]


def _add_common(sp: argparse.ArgumentParser, *, hardware: bool = True) -> None:
    sp.add_argument("--json", action="store_true", help="Emit structured JSON.")
    if hardware:
        sp.add_argument("--trace", action="store_true", help="Trace reader APDUs to stderr.")
        sp.add_argument("--reader", help="Reader index or BUS:ADDR (default: first ACR1552).")


def register_noun(sub: argparse._SubParsersAction, noun: str) -> None:
    """Register ``noun`` with one subcommand per REGISTRY verb, plus ``overview``."""

    def overview(args: argparse.Namespace) -> int:
        emit_overview(
            f"nfc-cli {noun}", noun_sections(noun), json_mode=bool(getattr(args, "json", False))
        )
        return 0

    p = sub.add_parser(noun, help=NOUN_HELP.get(noun, f"{noun} operations."))
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=overview, json=False)
    nsub = p.add_subparsers(dest=f"{noun}_command", parser_class=type(p))
    for verb in REGISTRY.verbs(noun):
        sp = add_subparser(nsub, verb)
        _add_common(sp)
        sp.set_defaults(func=run_verb)
    ov = nsub.add_parser("overview", help=f"Describe the {noun} surface.")
    _add_common(ov, hardware=False)
    ov.set_defaults(func=overview)
