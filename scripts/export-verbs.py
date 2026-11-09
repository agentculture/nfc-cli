#!/usr/bin/env python3
"""Write web/src/api/verbs.json from the verb registry (nfc_cli.registry.REGISTRY).

The web console renders its verb forms from this file, so the UI and the CLI/MCP/API surfaces
share one declaration. CI's web job has no uv, so the output is committed and
tests/test_export_verbs.py fails when it is stale.

    uv run python scripts/export-verbs.py            # regenerate
    uv run python scripts/export-verbs.py --check    # exit 1 if the committed file is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "web" / "src" / "api" / "verbs.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def export() -> dict:
    from nfc_cli import verbs  # noqa: F401 - registers every verb at import
    from nfc_cli.registry import REGISTRY, api_route

    return {
        "generated_by": "scripts/export-verbs.py",
        "verbs": [
            {
                "noun": v.noun,
                "verb": v.verb,
                "summary": v.summary,
                "route": api_route(v),
                "mutating": v.mutating,
                "trailer_flag": v.extra.get("trailer_flag"),
                "writes_trailers": bool(v.extra.get("writes_trailers")),
                "schema": v.params_schema(),
            }
            for v in REGISTRY.verbs()
        ],
    }


def render() -> str:
    return json.dumps(export(), indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--check", action="store_true", help="exit 1 if --output is stale")
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
        if current != text:
            print(f"{args.output} is stale: run scripts/export-verbs.py", file=sys.stderr)
            return 1
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
