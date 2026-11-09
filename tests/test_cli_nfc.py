"""CLI wiring for the reader / tag / ndef / mcp / ui surface."""

from __future__ import annotations

import argparse
import json
import sys
import types

import pytest

import nfc_cli.verbs  # noqa: F401
from nfc_cli.cli import _build_parser, main
from nfc_cli.cli._commands import _nfcgroup
from nfc_cli.explain import resolve
from nfc_cli.nfc import errors as E
from nfc_cli.nfc import ops
from nfc_cli.registry import REGISTRY
from nfc_cli.verbs import TrailerFlagRequired


@pytest.fixture(autouse=True)
def _fresh_fake(monkeypatch):
    monkeypatch.setattr(_nfcgroup, "_fake_instance", {})
    monkeypatch.setenv("NFC_CLI_FAKE", "x3")


def _walk(parser, prefix=()):
    for act in parser._actions:
        if isinstance(act, argparse._SubParsersAction):
            for name, sp in act.choices.items():
                yield prefix + (name,), sp
                yield from _walk(sp, prefix + (name,))


def test_cli_verb_paths_equal_registry():
    paths = {p for p, _ in _walk(_build_parser())}
    cli_verbs = {p for p in paths if len(p) == 2 and p[0] in ("reader", "tag", "ndef")}
    assert {p for p in cli_verbs if p[1] != "overview"} == {v.path for v in REGISTRY.verbs()}


def test_every_parser_path_resolves_via_explain(capsys):
    for path, _ in _walk(_build_parser()):
        assert resolve(path), path
        assert main(["explain", *path]) == 0
    capsys.readouterr()


def test_every_registry_verb_accepts_common_flags():
    parser = _build_parser()
    for path, sp in _walk(parser):
        if len(path) == 2 and path[0] in ("reader", "tag", "ndef") and path[1] != "overview":
            opts = {o for a in sp._actions for o in a.option_strings}
            assert {"--json", "--trace", "--reader"} <= opts, path
            verb = REGISTRY.get(*path)
            if verb.mutating:
                assert "--apply" in opts


def test_noun_overviews(capsys):
    for noun in ("reader", "tag", "ndef", "ui"):
        assert main([noun, "overview", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["subject"] == f"nfc-cli {noun}"
        assert main([noun]) == 0
        capsys.readouterr()


def test_tag_read_text_and_json(capsys):
    assert main(["tag", "read", "--block", "0", "--count", "1"]) == 0
    assert "uid:" in capsys.readouterr().out
    assert main(["tag", "uid", "--json"]) == 0
    assert "uid" in json.loads(capsys.readouterr().out)


def test_apply_dry_run_default(capsys, monkeypatch):
    monkeypatch.setenv("NFC_CLI_FAKE", "ntag215")
    assert main(["ndef", "write", "--text", "hi", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out.get("applied") in (False, None) or out.get("dry_run") in (True, None)


@pytest.mark.parametrize(
    "exc,code",
    [
        (E.UsbUnavailable(), 2),
        (E.ReaderNotFound(), 2),
        (E.ReaderPermission(), 2),
        (E.ReaderBusy(), 2),
        (E.NoCard(), 2),
        (E.CardRemoved(), 2),
        (E.AuthFailed(), 2),
        (E.ApduError(0x6A82), 2),
        (ops.SnapshotFailed(), 2),
        (ops.VerifyFailed(), 2),
        (ops.NotNdefFormatted(), 2),
        (ops.WriteInterrupted("j.json", [1], 0, "card left"), 2),
        (ops.InvalidInput("bad hex"), 1),
        (TrailerFlagRequired(), 1),
    ],
)
def test_nfc_errors_map_to_exit_codes(monkeypatch, capsys, exc, code):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(nfc_cli.verbs, "invoke", boom)
    assert main(["tag", "uid"]) == code
    cap = capsys.readouterr()
    assert cap.out == ""
    assert cap.err.startswith("error: ")
    assert "\nhint: " in cap.err
    assert "Traceback" not in cap.err


def test_real_no_card_is_exit_2(monkeypatch, capsys):
    monkeypatch.setenv("NFC_CLI_FAKE", "empty")

    assert main(["tag", "uid"]) == 2
    assert "hint:" in capsys.readouterr().err


def test_missing_usb_extra_is_exit_2(monkeypatch, capsys):
    monkeypatch.delenv("NFC_CLI_FAKE")
    monkeypatch.setitem(sys.modules, "usb", None)
    assert main(["reader", "list"]) == 2
    assert "hint:" in capsys.readouterr().err


def test_json_error_shape(monkeypatch, capsys):
    monkeypatch.setattr(nfc_cli.verbs, "invoke", lambda *a, **k: (_ for _ in ()).throw(E.NoCard()))
    assert main(["tag", "uid", "--json"]) == 2
    err = json.loads(capsys.readouterr().err)
    assert err["code"] == 2
    assert err["remediation"]


def test_argparse_errors_stay_exit_1(capsys):
    for argv in (["tag", "bogus"], ["tag", "write"], ["tag", "uid", "--nope"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 1
        assert "hint:" in capsys.readouterr().err


# --- mcp / ui entry points (module stubs) ---------------------------------------------


def _stub(monkeypatch, name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)
    parent, _, child = name.rpartition(".")
    pkg = sys.modules.get(parent) or types.ModuleType(parent)
    pkg.__path__ = []
    monkeypatch.setitem(sys.modules, parent, pkg)
    setattr(pkg, child, mod)
    return mod


def test_mcp_lazy_calls_run_stdio(monkeypatch):
    calls = []
    _stub(monkeypatch, "nfc_cli.mcp.server", run_stdio=lambda: calls.append(1))
    assert main(["mcp"]) == 0
    assert calls == [1]


def test_mcp_extra_missing_exit_2(monkeypatch, capsys):
    class McpExtraMissing(E.NfcError):
        pass

    def run():
        raise McpExtraMissing("mcp extra missing", "pip install 'nfc-cli[mcp]'")

    _stub(monkeypatch, "nfc_cli.mcp.server", run_stdio=run, McpExtraMissing=McpExtraMissing)
    assert main(["mcp"]) == 2
    err = capsys.readouterr().err
    assert "error: mcp extra missing" in err
    assert "hint: pip install 'nfc-cli[mcp]'" in err


def test_ui_serve_passes_args(monkeypatch):
    got = {}

    class BindRefused(E.NfcError):
        pass

    _stub(
        monkeypatch,
        "nfc_cli.web.server",
        serve=lambda **kw: got.update(kw),
        BindRefused=BindRefused,
    )
    argv = ["ui", "serve", "--host", "h", "--port", "9", "--bind-public", "--fake", "x3"]
    assert main(argv) == 0
    assert got == {"host": "h", "port": 9, "bind_public": True, "fake": "x3"}


def test_ui_bind_refused_exit_1(monkeypatch, capsys):
    class BindRefused(E.NfcError):
        pass

    def serve(**kw):
        raise BindRefused("refusing public bind", "pass --bind-public")

    _stub(monkeypatch, "nfc_cli.web.server", serve=serve, BindRefused=BindRefused)
    assert main(["ui", "serve", "--host", "0.0.0.0"]) == 1
    err = capsys.readouterr().err
    assert "error: refusing public bind" in err
    assert "hint: pass --bind-public" in err


# --- learn / description ----------------------------------------------------------------


def test_learn_lists_new_surfaces(capsys):
    assert main(["learn"]) == 0
    text = capsys.readouterr().out
    for needle in ("reader", "tag", "ndef", "nfc-cli mcp", "ui serve"):
        assert needle in text
    assert "clonable template" not in text
    assert main(["learn", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    paths = {tuple(c["path"]) for c in payload["commands"]}
    assert {
        ("reader", "list"),
        ("tag", "read"),
        ("ndef", "format"),
        ("mcp",),
        ("ui", "serve"),
    } <= paths
    assert {"mcp", "web-ui"} <= set(payload["surfaces"])


def test_no_clonable_template_wording():
    assert "clonable template" not in _build_parser().description
    assert "clonable template" not in resolve(())
