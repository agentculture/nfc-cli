"""Verb registrations for the reader / tag / ndef surface (c35).

Every verb is declared once here and added to :data:`nfc_cli.registry.REGISTRY`; the CLI
(t12), the MCP server (t13) and the web /api/ (t14) are all generated from it. Handlers have
the signature ``handler(ctx, **params) -> dict`` and raise only
:class:`~nfc_cli.nfc.errors.NfcError` subclasses.

Trailer safety across surfaces: verbs that can write MIFARE Classic trailers carry
``extra["trailer_flag"] = "include_trailers"``. For ``restore``/``write``/``apdu`` the flag
itself gates trailer writes inside :mod:`nfc_cli.nfc.ops`. ``ndef format`` writes trailers by
design (``extra["writes_trailers"] = True``), so over MCP / the web API :func:`invoke` also
demands ``include_trailers=true`` next to ``apply``; the CLI implies it. Any call that did
write protected blocks is appended to the audit log (:func:`nfc_cli.nfc.ops.append_audit`).
"""

from __future__ import annotations

import contextlib
import functools
from pathlib import Path
from typing import Any, Callable, Iterator

from nfc_cli.nfc import classic, ops
from nfc_cli.nfc import reader as reader_mod
from nfc_cli.nfc.errors import NfcError
from nfc_cli.registry import REGISTRY, Context, Param, Registry, Verb

__all__ = [
    "TrailerFlagRequired",
    "build_verbs",
    "check_trailer_gate",
    "invoke",
    "open_transport",
    "register",
]

GATED_SURFACES = ("mcp", "api")


class TrailerFlagRequired(NfcError):
    default_message = "this call writes sector trailers and needs include_trailers=true"
    default_remediation = "pass include_trailers=true together with apply=true to format the tag"


# ===================================================================== plumbing


def _nfc_only(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Make sure a handler surfaces nothing but NfcError (t12 maps those to exit 2)."""

    @functools.wraps(fn)
    def wrapper(ctx: Context, **params: Any) -> dict:
        try:
            return fn(ctx, **params)
        except NfcError:
            raise
        except FileNotFoundError as exc:
            raise ops.InvalidInput(f"file not found: {exc.filename}") from None
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            raise NfcError(
                f"{type(exc).__name__}: {exc}", "re-run with --trace; if it persists file a bug"
            ) from None

    return wrapper


def _default_factory(ctx: Context) -> Callable[[], Any]:
    if ctx.transport_factory is not None:
        return ctx.transport_factory

    def make() -> Any:
        from nfc_cli.nfc.transport import UsbCcidTransport

        return UsbCcidTransport(trace=ctx.trace)

    return make


@contextlib.contextmanager
def open_transport(ctx: Context, wait: int | float | None = None) -> Iterator[Any]:
    """Build a transport from ``ctx``, claim it, optionally wait for a card, close after."""
    t = _default_factory(ctx)()
    try:
        with t.claim() as claimed:
            if wait:
                ops.wait_for_card(claimed, float(wait))
            yield claimed
    finally:
        closer = getattr(t, "close", None)
        if closer is not None:
            closer()


def _keys(key: str | None, key_type: str | None, keys_file: str | None) -> dict:
    out: dict[str, Any] = {}
    if key:
        k = ops._hex(key, "key")
        if len(k) != 6:
            raise ops.InvalidInput("a MIFARE Classic key is 6 bytes (12 hex digits)")
        out["key"] = k
        out["key_type"] = (key_type or "A").upper()
        if out["key_type"] not in ("A", "B"):
            raise ops.InvalidInput("key_type must be A or B")
    if keys_file:
        out["keys"] = classic.load_keys_file(keys_file)
    return out


def _incl(ctx: Context, include_keys: bool | None) -> bool:
    return bool(include_keys) or bool(ctx.include_keys)


# ===================================================================== params

P_WAIT = Param("wait", "integer", "seconds to wait for a card before failing (default: no wait)")
P_KEY = Param("key", "string", "MIFARE Classic key to try first (12 hex digits)")
P_KEY_TYPE = Param("key_type", "string", "key type for --key: A or B", default="A")
P_KEYS_FILE = Param("keys_file", "string", "file of extra Classic keys, one per line")
P_INCLUDE_KEYS = Param("include_keys", "boolean", "show key bytes instead of masking them")
P_EXPECT_UID = Param(
    "expect_uid", "string", "only write if the tag's UID matches this (the UID you previewed)"
)
P_TRAILERS = Param(
    "include_trailers", "boolean", "also write sector trailers and block 0 (dangerous)"
)
KEY_PARAMS = (P_KEY, P_KEY_TYPE, P_KEYS_FILE)


# ===================================================================== handlers


@_nfc_only
def reader_list(ctx: Context) -> dict:
    if ctx.transport_factory is not None:
        t = ctx.transport_factory()
        name = type(t).__name__
        with contextlib.suppress(Exception):
            t.close()
        return {"readers": [{"index": 0, "name": name, "injected": True}]}
    from nfc_cli.nfc.transport import find_devices

    readers = []
    for i, dev in enumerate(find_devices()):
        bus, addr = getattr(dev, "bus", None), getattr(dev, "address", None)
        readers.append({"index": i, "bus": bus, "address": addr, "name": "ACS ACR1552 (072f:2303)"})
    return {"readers": readers}


@_nfc_only
def reader_info(ctx: Context) -> dict:
    out: dict[str, Any] = {}
    notes = []
    with open_transport(ctx) as t:
        for field, fn in (
            ("firmware", reader_mod.firmware),
            ("polling", reader_mod.polling_type),
            ("slot", reader_mod.slot_status),
        ):
            try:
                out[field] = fn(t)
            except NfcError as exc:
                if type(exc) is not NfcError:  # busy / permission / missing: real failures
                    raise
                out[field] = None
                notes.append(f"{field}: {exc}")
    if notes:
        out["notes"] = notes
    return out


@_nfc_only
def tag_info(ctx: Context, wait: int | None = None) -> dict:
    with open_transport(ctx, wait) as t:
        return ops.info(t)


@_nfc_only
def tag_uid(ctx: Context, wait: int | None = None) -> dict:
    with open_transport(ctx, wait) as t:
        return ops.uid(t)


@_nfc_only
def tag_magic_check(
    ctx: Context,
    key: str | None = None,
    key_type: str | None = None,
    keys_file: str | None = None,
    wait: int | None = None,
) -> dict:
    kw = _keys(key, key_type, keys_file)
    with open_transport(ctx, wait) as t:
        return ops.magic_check(t, **kw)


@_nfc_only
def tag_wait(ctx: Context, timeout: int = 30) -> dict:
    with open_transport(ctx) as t:
        res = ops.wait_for_card(t, float(timeout))
        res.update(ops.uid(t))
        return res


@_nfc_only
def tag_read(
    ctx: Context,
    block: int | None = None,
    count: int = 1,
    key: str | None = None,
    key_type: str | None = None,
    keys_file: str | None = None,
    include_keys: bool = False,
    wait: int | None = None,
) -> dict:
    kw = _keys(key, key_type, keys_file)
    with open_transport(ctx, wait) as t:
        return ops.read(t, block, count or 1, include_keys=_incl(ctx, include_keys), **kw)


@_nfc_only
def tag_write(
    ctx: Context,
    block: int,
    data: str,
    key: str | None = None,
    key_type: str | None = None,
    keys_file: str | None = None,
    include_trailers: bool = False,
    include_keys: bool = False,
    expect_uid: str | None = None,
    wait: int | None = None,
) -> dict:
    kw = _keys(key, key_type, keys_file)
    if expect_uid:
        kw["expect_uid"] = expect_uid
    with open_transport(ctx, wait) as t:
        return ops.write(
            t,
            block,
            data,
            apply=ctx.apply,
            include_trailers=bool(include_trailers),
            include_keys=_incl(ctx, include_keys),
            **kw,
        )


@_nfc_only
def tag_dump(
    ctx: Context,
    output: str | None = None,
    format: str = "json",
    resume: str | None = None,
    key: str | None = None,
    key_type: str | None = None,
    keys_file: str | None = None,
    include_keys: bool = False,
    wait: int | None = None,
) -> dict:
    kw = _keys(key, key_type, keys_file)
    with open_transport(ctx, wait) as t:
        return ops.dump(
            t,
            output=output,
            fmt=format or "json",
            resume_from=resume,
            include_keys=_incl(ctx, include_keys),
            **kw,
        )


@_nfc_only
def tag_restore(
    ctx: Context,
    file: str,
    resume: bool = False,
    format: str = "json",
    key: str | None = None,
    key_type: str | None = None,
    keys_file: str | None = None,
    include_trailers: bool = False,
    include_keys: bool = False,
    expect_uid: str | None = None,
    wait: int | None = None,
) -> dict:
    kw = _keys(key, key_type, keys_file)
    if expect_uid:
        kw["expect_uid"] = expect_uid
    common = dict(
        apply=ctx.apply,
        include_trailers=bool(include_trailers),
        include_keys=_incl(ctx, include_keys),
        **kw,
    )
    with open_transport(ctx, wait) as t:
        if resume:
            return ops.resume(t, file, **common)
        return ops.restore(t, file, fmt=format or "json", **common)


@_nfc_only
def tag_apdu(
    ctx: Context,
    apdu: str,
    include_trailers: bool = False,
    wait: int | None = None,
) -> dict:
    cmds = [c for c in apdu.replace(";", ",").split(",") if c.strip()]
    if not ctx.apply:
        return ops.raw_apdu(None, cmds, apply=False, include_trailers=bool(include_trailers))
    with open_transport(ctx, wait) as t:
        return ops.raw_apdu(t, cmds, apply=True, include_trailers=bool(include_trailers))


@_nfc_only
def ndef_read(
    ctx: Context,
    key: str | None = None,
    key_type: str | None = None,
    keys_file: str | None = None,
    wait: int | None = None,
) -> dict:
    kw = _keys(key, key_type, keys_file)
    with open_transport(ctx, wait) as t:
        return ops.ndef_read(t, **kw)


@_nfc_only
def ndef_write(
    ctx: Context,
    text: str | None = None,
    lang: str = "en",
    uri: str | None = None,
    mime: str | None = None,
    payload: str | None = None,
    payload_file: str | None = None,
    raw_file: str | None = None,
    key: str | None = None,
    key_type: str | None = None,
    keys_file: str | None = None,
    include_keys: bool = False,
    expect_uid: str | None = None,
    wait: int | None = None,
) -> dict:
    data: str | bytes | None = payload
    if payload_file:
        data = Path(payload_file).read_bytes()
    raw = Path(raw_file).read_bytes() if raw_file else None
    message = ops.build_message(text=text, lang=lang, uri=uri, mime=mime, payload=data, raw=raw)
    kw = _keys(key, key_type, keys_file)
    if expect_uid:
        kw["expect_uid"] = expect_uid
    with open_transport(ctx, wait) as t:
        return ops.ndef_write(
            t, message, apply=ctx.apply, include_keys=_incl(ctx, include_keys), **kw
        )


@_nfc_only
def ndef_erase(
    ctx: Context,
    key: str | None = None,
    key_type: str | None = None,
    keys_file: str | None = None,
    include_keys: bool = False,
    expect_uid: str | None = None,
    wait: int | None = None,
) -> dict:
    kw = _keys(key, key_type, keys_file)
    if expect_uid:
        kw["expect_uid"] = expect_uid
    with open_transport(ctx, wait) as t:
        return ops.ndef_erase(t, apply=ctx.apply, include_keys=_incl(ctx, include_keys), **kw)


@_nfc_only
def ndef_format(
    ctx: Context,
    include_trailers: bool = False,
    key: str | None = None,
    key_type: str | None = None,
    keys_file: str | None = None,
    include_keys: bool = False,
    expect_uid: str | None = None,
    wait: int | None = None,
) -> dict:
    kw = _keys(key, key_type, keys_file)
    if expect_uid:
        kw["expect_uid"] = expect_uid
    with open_transport(ctx, wait) as t:
        return ops.ndef_format(t, apply=ctx.apply, include_keys=_incl(ctx, include_keys), **kw)


# ===================================================================== registry


def build_verbs() -> list[Verb]:
    trailer = {"trailer_flag": "include_trailers"}
    return [
        Verb("reader", "list", "List attached ACR1552 readers", reader_list),
        Verb("reader", "info", "Reader firmware, polling types and slot status", reader_info),
        Verb(
            "tag", "info", "Identify the tag on the reader (ATR, UID, family)", tag_info, (P_WAIT,)
        ),
        Verb("tag", "uid", "Print the tag UID", tag_uid, (P_WAIT,)),
        Verb(
            "tag",
            "magic-check",
            "Test (non-destructively) whether a Classic tag's UID block can be rewritten",
            tag_magic_check,
            (*KEY_PARAMS, P_WAIT),
        ),
        Verb(
            "tag",
            "wait",
            "Wait until a tag is on the reader",
            tag_wait,
            (Param("timeout", "integer", "seconds to wait", default=30),),
        ),
        Verb(
            "tag",
            "read",
            "Read blocks (or the whole tag) without saving",
            tag_read,
            (
                Param("block", "integer", "first block/page (default: whole tag)"),
                Param("count", "integer", "number of blocks", default=1),
                *KEY_PARAMS,
                P_INCLUDE_KEYS,
                P_WAIT,
            ),
        ),
        Verb(
            "tag",
            "write",
            "Write whole blocks starting at BLOCK (verified by read-back)",
            tag_write,
            (
                P_EXPECT_UID,
                Param("block", "integer", "first block/page", required=True, positional=True),
                Param(
                    "data",
                    "string",
                    "hex data, a multiple of the block size",
                    required=True,
                    positional=True,
                ),
                *KEY_PARAMS,
                P_TRAILERS,
                P_INCLUDE_KEYS,
                P_WAIT,
            ),
            mutating=True,
            extra=dict(trailer),
        ),
        Verb(
            "tag",
            "dump",
            "Dump the whole tag to a 0600 file (resumable)",
            tag_dump,
            (
                Param("output", "string", "dump file (default: under the user data dir)"),
                Param("format", "string", "json or bin", default="json"),
                Param("resume", "string", "continue a partial dump file"),
                *KEY_PARAMS,
                P_INCLUDE_KEYS,
                P_WAIT,
            ),
        ),
        Verb(
            "tag",
            "restore",
            "Write a dump back (trailers/block 0 skipped unless --include-trailers)",
            tag_restore,
            (
                P_EXPECT_UID,
                Param(
                    "file",
                    "string",
                    "dump file, or with --resume the journal to finish",
                    required=True,
                    positional=True,
                ),
                Param("resume", "boolean", "FILE is a write journal: finish its pending blocks"),
                Param("format", "string", "dump format: json or bin", default="json"),
                *KEY_PARAMS,
                P_TRAILERS,
                P_INCLUDE_KEYS,
                P_WAIT,
            ),
            mutating=True,
            extra=dict(trailer),
        ),
        Verb(
            "tag",
            "apdu",
            "Send raw APDUs (comma-separated hex) to the tag",
            tag_apdu,
            (
                Param(
                    "apdu", "string", "hex APDU(s), comma-separated", required=True, positional=True
                ),
                P_TRAILERS,
                P_WAIT,
            ),
            mutating=True,
            extra=dict(trailer),
        ),
        Verb("ndef", "read", "Read and decode the NDEF message", ndef_read, (*KEY_PARAMS, P_WAIT)),
        Verb(
            "ndef",
            "write",
            "Write an NDEF record (text, URI, MIME or raw message)",
            ndef_write,
            (
                P_EXPECT_UID,
                Param("text", "string", "text record content"),
                Param("lang", "string", "text record language", default="en"),
                Param("uri", "string", "URI record"),
                Param("mime", "string", "MIME type for a MIME record"),
                Param("payload", "string", "MIME record payload (UTF-8 text)"),
                Param("payload_file", "string", "MIME record payload file"),
                Param("raw_file", "string", "file holding a complete encoded NDEF message"),
                *KEY_PARAMS,
                P_INCLUDE_KEYS,
                P_WAIT,
            ),
            mutating=True,
        ),
        Verb(
            "ndef",
            "erase",
            "Replace the NDEF message with an empty one",
            ndef_erase,
            (P_EXPECT_UID, *KEY_PARAMS, P_INCLUDE_KEYS, P_WAIT),
            mutating=True,
        ),
        Verb(
            "ndef",
            "format",
            "NDEF-format a MIFARE Classic tag (MAD + NDEF keys; writes trailers from the "
            "fixed NFC Forum template)",
            ndef_format,
            (
                P_EXPECT_UID,
                Param(
                    "include_trailers",
                    "boolean",
                    "confirm the template trailer writes (required over MCP / web API)",
                ),
                *KEY_PARAMS,
                P_INCLUDE_KEYS,
                P_WAIT,
            ),
            mutating=True,
            extra={**trailer, "writes_trailers": True},
        ),
    ]


def register(registry: Registry = REGISTRY) -> Registry:
    """Add every verb to ``registry`` (idempotent)."""
    for v in build_verbs():
        if registry.get(v.noun, v.verb) is None:
            registry.add(v)
    return registry


def check_trailer_gate(verb: Verb, params: dict, apply: bool, surface: str) -> None:
    """Refuse an applied trailer-writing call over MCP/API without ``include_trailers=true``.

    Raises before any transport is opened, so a refused call sends no APDU at all.
    """
    if surface not in GATED_SURFACES or not apply:
        return
    if verb.extra.get("writes_trailers") and params.get(verb.extra["trailer_flag"]) is not True:
        raise TrailerFlagRequired(
            f"{verb.noun} {verb.verb} writes sector trailers: over {surface} it needs "
            f"{verb.extra['trailer_flag']}=true as well as apply=true"
        )


def _confine_output(params: dict, surface: str) -> None:
    """Over MCP/web, keep a file-writing ``output`` inside the dumps dir (no arbitrary
    path a remote caller could overwrite). The CLI (a local operator) may write anywhere."""
    if surface not in GATED_SURFACES:
        return
    out = params.get("output")
    if not out:
        return
    base = ops.dumps_dir().resolve()
    target = (base / out).resolve() if not Path(out).is_absolute() else Path(out).resolve()
    if base != target and base not in target.parents:
        raise ops.InvalidInput(
            f"over {surface} the dump output must stay inside {base}",
            "omit output to use the default dir, or pass a plain filename",
        )
    params["output"] = str(target)


def invoke(verb: Verb, params: dict, ctx: Context, surface: str = "cli") -> dict:
    """Run a verb the way every surface should: trailer gate, handler, audit log."""
    check_trailer_gate(verb, params, ctx.apply, surface)
    _confine_output(params, surface)
    result = verb.handler(ctx, **params)
    written = result.get("protected_blocks_written") if isinstance(result, dict) else None
    if written:
        result["audit_log"] = str(ops.append_audit(surface, result.get("uid", ""), written))
    return result


register()
