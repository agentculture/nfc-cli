"""Verb registry: every verb is declared once; CLI, MCP and /api/ surfaces derive from it.

Cited (cite-don't-import) from culture-rules ``culture_rules/cli/registry.py`` and
``culture_rules/mcp/tools.py``. Deliberately imports nothing from ``nfc_cli.cli`` so surfaces
can depend on it; the CLI wiring registers verbs and calls :func:`add_subparser`.
Handlers receive a :class:`Context` and return JSON-serialisable dicts.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, TextIO

__all__ = [
    "REGISTRY",
    "ArgumentError",
    "Context",
    "DuplicateVerb",
    "Param",
    "Registry",
    "Verb",
    "add_subparser",
    "api_route",
    "mcp_tool_spec",
    "validate_arguments",
]

_JSON_TYPES = {"string", "integer", "boolean", "object", "array", "any"}


class DuplicateVerb(ValueError):
    """A (noun, verb) pair was registered twice."""


class ArgumentError(ValueError):
    """Arguments to a verb do not match its declared schema."""


@dataclass(frozen=True)
class Param:
    """One verb parameter; ``type`` is a JSON Schema type (``any`` carries no type)."""

    name: str
    type: str = "string"
    help: str = ""
    required: bool = False
    positional: bool = False
    default: Any = None

    def __post_init__(self) -> None:
        if self.type not in _JSON_TYPES:
            raise ValueError(f"unsupported param type {self.type!r}")

    def schema(self) -> dict[str, Any]:
        out: dict[str, Any] = {"description": self.help}
        if self.type != "any":
            out = {"type": self.type, **out}
        if self.default is not None:
            out["default"] = self.default
        return out


@dataclass
class Context:
    """What a handler runs against."""

    apply: bool = False
    trace: TextIO | None = None
    transport_factory: Callable[[], Any] | None = None
    include_keys: bool = False


Handler = Callable[..., dict[str, Any]]


@dataclass
class Verb:
    noun: str
    verb: str
    summary: str
    handler: Handler
    params: tuple[Param, ...] = ()
    mutating: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> tuple[str, str]:
        return (self.noun, self.verb)

    @property
    def tool_name(self) -> str:
        return f"{self.noun}_{self.verb}"

    def params_schema(self) -> dict[str, Any]:
        """JSON Schema of the arguments; only a mutating verb gains ``apply`` (default off)."""
        props = {p.name: p.schema() for p in self.params}
        if self.mutating:
            props["apply"] = {
                "type": "boolean",
                "default": False,
                "description": "false = dry-run (change nothing); true = commit the write",
            }
        return {
            "type": "object",
            "properties": props,
            "required": [p.name for p in self.params if p.required],
            "additionalProperties": False,
        }


class Registry:
    def __init__(self) -> None:
        self._verbs: dict[tuple[str, str], Verb] = {}

    def add(self, verb: Verb) -> Verb:
        if verb.path in self._verbs:
            raise DuplicateVerb(f"verb {' '.join(verb.path)!r} is already registered")
        self._verbs[verb.path] = verb
        return verb

    def get(self, noun: str, verb: str) -> Verb | None:
        return self._verbs.get((noun, verb))

    def by_tool_name(self, name: str) -> Verb | None:
        return next((v for v in self._verbs.values() if v.tool_name == name), None)

    def verbs(self, noun: str | None = None) -> list[Verb]:
        return [v for v in self._verbs.values() if noun is None or v.noun == noun]

    def nouns(self) -> list[str]:
        return list(dict.fromkeys(v.noun for v in self._verbs.values()))

    def extend(self, verbs: Iterable[Verb]) -> None:
        for v in verbs:
            self.add(v)


REGISTRY = Registry()


# --- surface helpers: all derived from the same Verb -------------------------------------


def mcp_tool_spec(verb: Verb) -> dict[str, Any]:
    note = " Writes are a dry-run unless apply=true." if verb.mutating else ""
    return {
        "name": verb.tool_name,
        "description": f"{verb.summary}{note}",
        "inputSchema": verb.params_schema(),
    }


def api_route(verb: Verb) -> str:
    return f"/api/{verb.noun}/{verb.verb}"


def _flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def add_subparser(subparsers: Any, verb: Verb) -> argparse.ArgumentParser:
    """Add ``verb.verb`` to a (noun-level) subparsers action; params become args/flags.

    Boolean params are store_true flags; ``object``/``array``/``any`` take a JSON string.
    Mutating verbs gain ``--apply``. ``func`` is left to the CLI wiring.
    """
    sp = subparsers.add_parser(verb.verb, help=verb.summary, description=verb.summary)
    for p in verb.params:
        dest = p.name
        if p.positional:
            sp.add_argument(dest, help=p.help, **_argtype(p))
        elif p.type == "boolean":
            sp.add_argument(_flag(p.name), dest=dest, action="store_true", help=p.help)
        else:
            sp.add_argument(
                _flag(p.name),
                dest=dest,
                required=p.required,
                default=p.default,
                help=p.help,
                **_argtype(p),
            )
    if verb.mutating:
        sp.add_argument(
            "--apply", action="store_true", help="commit the write (default is a dry-run)"
        )
    sp.set_defaults(registry_verb=verb)
    return sp


def _argtype(p: Param) -> dict[str, Any]:
    if p.type == "integer":
        return {"type": int}
    if p.type in ("object", "array", "any"):
        return {"type": json.loads}
    return {}


_SCALARS: dict[str, type] = {"boolean": bool, "integer": int, "string": str}


def validate_arguments(verb: Verb, arguments: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Strictly validate MCP/API arguments; returns ``(params, apply)``.

    Booleans must be JSON booleans (``"false"`` is refused, never truthy); ``apply`` only
    takes effect when exactly ``True`` on a mutating verb.
    """
    args = dict(arguments or {})
    raw_apply = args.pop("apply", False)
    if raw_apply is not False and not isinstance(raw_apply, bool):
        raise ArgumentError("apply must be a JSON boolean")
    apply = raw_apply is True and verb.mutating
    known = {p.name for p in verb.params}
    extra = sorted(set(args) - known)
    if extra:
        raise ArgumentError(f"unexpected argument(s) {', '.join(extra)}")
    missing = [p.name for p in verb.params if p.required and args.get(p.name) is None]
    if missing:
        raise ArgumentError(f"missing required argument(s) {', '.join(missing)}")
    for p in verb.params:
        v = args.get(p.name)
        want = _SCALARS.get(p.type)
        if v is None or want is None:
            continue
        if not isinstance(v, want) or (p.type == "integer" and isinstance(v, bool)):
            raise ArgumentError(f"{p.name} must be a JSON {p.type}")
    return {k: v for k, v in args.items() if v is not None}, apply
