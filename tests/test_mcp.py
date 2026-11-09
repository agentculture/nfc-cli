"""t13: MCP tools (SDK-free) and the in-process server (needs the mcp extra)."""

from __future__ import annotations

import builtins
import json
import sys

import pytest

from nfc_cli.mcp import server as mcp_server
from nfc_cli.mcp import tools
from nfc_cli.nfc.fake import load_scenario


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("NFC_CLI_FAKE", raising=False)
    tools._fake_cache.clear()


def _call(name, args=None, fake=None):
    fake = fake or load_scenario("x3")
    return tools.call_tool(name, args, lambda: fake), fake


def test_tag_info_via_tools():
    res, _ = _call("tag_info")
    assert res["uid"]


def test_unknown_tool_is_user_error():
    with pytest.raises(tools.ToolError) as e:
        tools.call_tool("nope_nope", {})
    assert e.value.kind == "user"
    assert e.value.remediation


def test_string_boolean_refused_without_apdu():
    fake = load_scenario("x3")
    with pytest.raises(tools.ToolError) as e:
        tools.call_tool("ndef_format", {"apply": "true"}, lambda: fake)
    assert e.value.kind == "user"
    assert fake.log == []
    with pytest.raises(tools.ToolError):
        tools.call_tool("ndef_format", {"apply": True, "include_trailers": "true"}, lambda: fake)
    assert fake.log == []


def test_mutating_without_apply_is_dry_run():
    fake = load_scenario("x3")
    res = tools.call_tool("tag_write", {"block": 4, "data": "00" * 16}, lambda: fake)
    assert not res.get("applied")
    assert not [1 for k, b in fake.log if k == "apdu" and b[:2] == b"\xff\xd6"]


def test_trailer_gate_zero_apdus_and_no_audit(tmp_path):
    fake = load_scenario("x3")
    with pytest.raises(tools.ToolError) as e:
        tools.call_tool("ndef_format", {"apply": True}, lambda: fake)
    assert e.value.kind == "user"
    assert "include_trailers" in e.value.message
    assert fake.log == []
    assert not (tmp_path / "state").exists() or not list((tmp_path / "state").rglob("*audit*"))


def test_trailer_write_with_flag_appends_audit():
    res, _ = _call("ndef_format", {"apply": True, "include_trailers": True})
    line = json.loads(open(res["audit_log"]).read().splitlines()[-1])
    assert line["surface"] == "mcp"
    assert line["blocks"]
    assert "ts" in line
    assert "uid" in line


def test_keys_redacted_unless_include_keys():
    res, _ = _call("tag_read", {"block": 3})
    assert "ffffffffffff" not in json.dumps(res)
    res2, _ = _call("tag_read", {"block": 3, "include_keys": True})
    assert "ffffffffffff" in json.dumps(res2)


def test_env_error_mapped(monkeypatch):
    def boom():
        from nfc_cli.nfc.errors import ReaderNotFound

        raise ReaderNotFound()

    with pytest.raises(tools.ToolError) as e:
        tools.call_tool("tag_info", {}, boom)
    assert e.value.kind == "env"
    assert e.value.remediation


def test_env_fake_shared_instance(monkeypatch):
    monkeypatch.setenv("NFC_CLI_FAKE", "x3")
    f = tools.make_transport_factory()
    assert f() is f()
    assert tools.call_tool("tag_info", {})["uid"]


def test_run_stdio_without_extra(monkeypatch):
    real = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "mcp" or name.startswith("mcp.") or name == "anyio":
            raise ImportError(name)
        return real(name, *a, **k)

    for m in [m for m in sys.modules if m == "mcp" or m.startswith("mcp.")]:
        monkeypatch.delitem(sys.modules, m)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(mcp_server.McpExtraMissing) as e:
        mcp_server.run_stdio()
    assert "pip install 'nfc-cli[mcp]'" in e.value.remediation


def test_inprocess_server_lists_and_calls_tag_info():
    pytest.importorskip("mcp")
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session as connect

    fake = load_scenario("x3")
    server = mcp_server.build_server(lambda: fake)

    async def run():
        async with connect(server) as client:
            listed = await client.list_tools()
            names = {t.name for t in listed.tools}
            ok = await client.call_tool("tag_info", {})
            bad = await client.call_tool("ndef_format", {"apply": True})
            return names, ok, bad

    names, ok, bad = anyio.run(run)
    assert "tag_info" in names
    assert len(names) == 15
    assert not ok.isError
    assert json.loads(ok.content[0].text)["uid"]
    assert bad.isError
    assert "include_trailers" in bad.content[0].text
