import argparse
import io

import pytest

from nfc_cli.registry import (
    ArgumentError,
    Context,
    DuplicateVerb,
    Param,
    Registry,
    Verb,
    add_subparser,
    api_route,
    mcp_tool_spec,
    validate_arguments,
)


def _handler(ctx, **kw):
    return {"apply": ctx.apply, **kw}


def _verb(mutating=False):
    return Verb(
        "tag",
        "write",
        "Write a tag",
        _handler,
        params=(
            Param("block", "integer", "block no", required=True),
            Param("data", "string", "hex data"),
            Param("verify", "boolean", "read back"),
        ),
        mutating=mutating,
    )


def test_register_and_tool_name():
    reg = Registry()
    v = reg.add(_verb(True))
    assert reg.get("tag", "write") is v
    assert v.tool_name == "tag_write"
    assert reg.nouns() == ["tag"]
    with pytest.raises(DuplicateVerb):
        reg.add(_verb())


def test_params_schema_apply_only_when_mutating():
    ro, rw = _verb(False).params_schema(), _verb(True).params_schema()
    assert "apply" not in ro["properties"]
    assert rw["properties"]["apply"] == {
        "type": "boolean",
        "default": False,
        "description": rw["properties"]["apply"]["description"],
    }
    for s in (ro, rw):
        assert s["type"] == "object" and s["required"] == ["block"]
        assert s["additionalProperties"] is False


def test_context_defaults_and_handler_result():
    trace = io.StringIO()
    factory = object
    ctx = Context(apply=True, trace=trace, transport_factory=factory, include_keys=True)
    assert (ctx.apply, ctx.trace, ctx.transport_factory, ctx.include_keys) == (
        True,
        trace,
        factory,
        True,
    )
    d = Context()
    assert d.apply is False and d.include_keys is False and d.trace is None
    assert _verb().handler(ctx, block=1) == {"apply": True, "block": 1}


def test_single_source_across_three_surfaces():
    v = _verb(True)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="verb")
    add_subparser(sub, v)
    ns = parser.parse_args(["write", "--block", "4", "--data", "ab", "--verify", "--apply"])
    spec = mcp_tool_spec(v)
    props = spec["inputSchema"]["properties"]
    # every schema property is an argparse dest, and vice versa
    assert set(props) == {k for k in vars(ns) if k not in ("verb", "registry_verb")}
    assert (ns.block, ns.data, ns.verify, ns.apply) == (4, "ab", True, True)
    assert ns.registry_verb is v
    assert spec["name"] == v.tool_name == "tag_write"
    assert api_route(v) == "/api/tag/write"
    assert spec["inputSchema"] == v.params_schema()


def test_cli_required_param_enforced():
    parser = argparse.ArgumentParser()
    add_subparser(parser.add_subparsers(dest="verb"), _verb())
    with pytest.raises(SystemExit):
        parser.parse_args(["write"])


def test_validate_refuses_string_booleans():
    v = _verb(True)
    with pytest.raises(ArgumentError):
        validate_arguments(v, {"block": 1, "verify": "false"})
    with pytest.raises(ArgumentError):
        validate_arguments(v, {"block": 1, "apply": "false"})
    with pytest.raises(ArgumentError):
        validate_arguments(v, {"block": True})
    with pytest.raises(ArgumentError):
        validate_arguments(v, {})
    with pytest.raises(ArgumentError):
        validate_arguments(v, {"block": 1, "bogus": 1})
    assert validate_arguments(v, {"block": 1, "apply": True}) == ({"block": 1}, True)
    assert validate_arguments(_verb(False), {"block": 1, "verify": True})[1] is False
