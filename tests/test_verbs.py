"""t11: verb registrations (c35 surface) and their handlers against FakeTransport."""

from __future__ import annotations

import json

import pytest

from nfc_cli import verbs
from nfc_cli.nfc import ops
from nfc_cli.nfc.errors import NfcError
from nfc_cli.nfc.fake import load_scenario
from nfc_cli.registry import REGISTRY, Context, Registry, mcp_tool_spec, validate_arguments

C35 = {
    ("reader", "list"),
    ("reader", "info"),
    ("tag", "info"),
    ("tag", "uid"),
    ("tag", "wait"),
    ("tag", "read"),
    ("tag", "write"),
    ("tag", "dump"),
    ("tag", "restore"),
    ("tag", "apdu"),
    ("ndef", "read"),
    ("ndef", "write"),
    ("ndef", "erase"),
    ("ndef", "format"),
}
MUTATING = {
    ("tag", "write"),
    ("tag", "restore"),
    ("tag", "apdu"),
    ("ndef", "write"),
    ("ndef", "erase"),
    ("ndef", "format"),
}


@pytest.fixture(autouse=True)
def _xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def ctx_for(t, apply=False, include_keys=False) -> Context:
    return Context(apply=apply, transport_factory=lambda: t, include_keys=include_keys)


def writes(t) -> int:
    return sum(1 for kind, a in t.log if kind == "apdu" and a[1] == 0xD6)


def call(noun, verb, ctx, surface="cli", **params):
    return verbs.invoke(REGISTRY.get(noun, verb), params, ctx, surface)


def test_registered_set_matches_c35_surface():
    assert {v.path for v in REGISTRY.verbs()} >= C35
    fresh = verbs.register(Registry())
    assert {v.path for v in fresh.verbs()} == C35
    verbs.register(fresh)  # idempotent
    assert {v.path for v in fresh.verbs() if v.mutating} == MUTATING
    assert fresh.nouns() == ["reader", "tag", "ndef"]


def test_params_schemas_are_cli_friendly_and_valid():
    for v in verbs.build_verbs():
        spec = mcp_tool_spec(v)
        assert spec["name"] == f"{v.noun}_{v.verb}"
        for p in v.params:
            assert p.name.isidentifier() and p.name == p.name.lower()
        assert ("apply" in spec["inputSchema"]["properties"]) is v.mutating
    trailer_verbs = {v.path for v in verbs.build_verbs() if "trailer_flag" in v.extra}
    assert trailer_verbs == {
        ("tag", "write"),
        ("tag", "restore"),
        ("tag", "apdu"),
        ("ndef", "format"),
    }
    for path in trailer_verbs:
        v = REGISTRY.get(*path)
        assert v.extra["trailer_flag"] in {p.name for p in v.params}


def test_read_only_verbs_on_fake():
    t = load_scenario("x3")
    c = ctx_for(t)
    assert call("tag", "info", c)["uid"] == "cb30c560"
    assert call("tag", "uid", c, wait=1)["uid"] == "cb30c560"
    assert call("tag", "wait", c, timeout=1)["present"] is True
    assert call("tag", "read", c, block=4)["blocks"]["4"] == "00" * 16
    assert call("tag", "read", c, block=3)["blocks"]["3"].startswith("**")
    keys = call("tag", "read", ctx_for(t, include_keys=True), block=3)["blocks"]["3"]
    assert keys.startswith("ffffffffffff")
    assert call("reader", "list", c)["readers"][0]["injected"] is True
    assert writes(t) == 0


def test_reader_info_reports_firmware_and_tolerates_missing_answers():
    t = load_scenario("x3")
    t.escape_responses[bytes.fromhex("E000001800")] = (
        bytes.fromhex("E100000014") + b"ACR1552 R FW 1.06.02"
    )
    out = call("reader", "info", ctx_for(t))
    assert out["firmware"] == "ACR1552 R FW 1.06.02"
    assert out["polling"] is None and out["slot"]["present"] is True
    assert out["notes"]


def test_mutating_verbs_dry_run_by_default(tmp_path):
    t = load_scenario("blank-classic")
    c = ctx_for(t)
    dump = call("tag", "dump", c, output=str(tmp_path / "d.json"))
    assert dump["complete"]
    assert call("tag", "write", c, block=4, data="11" * 16)["applied"] is False
    assert call("tag", "restore", c, file=dump["path"])["applied"] is False
    assert call("ndef", "format", c)["applied"] is False
    assert call("tag", "apdu", c, apdu="FFD6000410" + "00" * 16)["applied"] is False
    assert writes(t) == 0


def test_write_apply_and_restore_and_resume_through_verbs(tmp_path):
    t = load_scenario("x3")
    orig = call("tag", "dump", ctx_for(t), output=str(tmp_path / "o.json"))
    out = call("tag", "write", ctx_for(t, apply=True), block=4, data="22" * 16)
    assert out["verified"] == 1 and t.card.blocks[4] == b"\x22" * 16
    back = call("tag", "restore", ctx_for(t, apply=True), file=orig["path"])
    assert back["verified"] == 1 and t.card.blocks[4] == bytes(16)
    # resume of a finished journal is a no-op
    again = call("tag", "restore", ctx_for(t, apply=True), file=back["journal"], resume=True)
    assert again["summary"]["write"] == 0


def test_ndef_verbs_round_trip():
    t = load_scenario("blank-classic")
    out = call("ndef", "format", ctx_for(t, apply=True))
    assert out["protected_blocks_written"]
    assert "audit_log" in out  # every protected write is audited, any surface
    call("ndef", "write", ctx_for(t, apply=True), uri="https://crosspoint.local/")
    assert call("ndef", "read", ctx_for(t))["records"][0]["uri"] == "https://crosspoint.local/"
    call("ndef", "erase", ctx_for(t, apply=True))
    assert call("ndef", "read", ctx_for(t))["empty"] is True


def test_trailer_gate_over_mcp_and_api_sends_no_apdu():
    for surface in ("mcp", "api"):
        t = load_scenario("blank-classic")
        verb = REGISTRY.get("ndef", "format")
        params, apply = validate_arguments(verb, {"apply": True})
        with pytest.raises(verbs.TrailerFlagRequired):
            verbs.invoke(verb, params, ctx_for(t, apply=apply), surface)
        assert t.log == []


def test_trailer_gate_permitted_call_appends_audit_line():
    t = load_scenario("blank-classic")
    verb = REGISTRY.get("ndef", "format")
    params, apply = validate_arguments(verb, {"apply": True, "include_trailers": True})
    out = verbs.invoke(verb, params, ctx_for(t, apply=apply), "mcp")
    assert out["applied"] and writes(t) > 0
    line = json.loads(ops.audit_log_path().read_text().splitlines()[-1])
    assert line["surface"] == "mcp" and line["uid"] == "01020304"
    assert line["blocks"] == [s * 4 + 3 for s in range(16)]


def test_cli_surface_does_not_need_the_flag_for_format():
    t = load_scenario("blank-classic")
    out = call("ndef", "format", ctx_for(t, apply=True), surface="cli")
    assert out["applied"]


@pytest.mark.parametrize(
    "noun,verb,params",
    [
        ("tag", "write", {"block": 4, "data": "zz"}),
        ("tag", "write", {"block": 4, "data": "11"}),
        ("tag", "restore", {"file": "/nonexistent/dump.json"}),
        ("tag", "restore", {"file": "/nonexistent/j.journal", "resume": True}),
        ("tag", "read", {"block": 4, "key": "123"}),
        ("tag", "read", {"block": 4, "keys_file": "/nonexistent/keys.txt"}),
        ("tag", "dump", {"format": "xml"}),
        ("ndef", "write", {}),
        ("ndef", "write", {"raw_file": "/nonexistent/msg.bin"}),
        ("tag", "apdu", {"apdu": "FF"}),
        ("ndef", "format", {}),
    ],
)
def test_handlers_raise_only_nfc_errors(noun, verb, params):
    t = load_scenario("ntag215" if verb == "format" else "x3")
    with pytest.raises(NfcError):
        call(noun, verb, ctx_for(t, apply=True), **params)
    assert writes(t) == 0


def test_no_card_is_an_nfc_error():
    with pytest.raises(NfcError):
        call("tag", "info", ctx_for(load_scenario("empty")))
