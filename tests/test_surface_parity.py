"""t13: MCP tool set == REGISTRY verb set (CLI == REGISTRY is asserted in t12)."""

from __future__ import annotations

from nfc_cli import verbs  # noqa: F401  (registers the verbs)
from nfc_cli.mcp import tools
from nfc_cli.registry import REGISTRY


def test_mcp_tool_names_equal_registry_verbs():
    assert {s["name"] for s in tools.tool_specs()} == {v.tool_name for v in REGISTRY.verbs()}
    assert len(REGISTRY.verbs()) == 14


def test_input_schemas_come_from_params_schema():
    by_name = {s["name"]: s for s in tools.tool_specs()}
    for v in REGISTRY.verbs():
        props = by_name[v.tool_name]["inputSchema"]["properties"]
        assert set(v.params_schema()["properties"]) <= set(props)
        assert ("apply" in props) == v.mutating
