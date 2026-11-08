"""hatch_build.py -- wheel build hook that ships the built web UI (web/dist).

Cited (cite-don't-import) from the embodiment repo's ``hatch_build.py``
(realtime-embodiment-app, task t19): the staleness check, the bounded
``npm ci`` / ``npm run build`` timeouts and the named-failure behaviour are
copied and adapted here.

Behaviour (wheel target only; see ``[tool.hatch.build.targets.wheel.hooks.custom]``):

* ``web/`` absent -> skip cleanly (nothing to build, nothing to ship).
* ``NFC_CLI_SKIP_WEB_BUILD=1`` -> never run npm; ship ``web/dist`` if it exists.
* ``web/dist/index.html`` present and newer than every tracked input -> no-op.
* otherwise run ``npm ci`` then ``npm run build`` in ``web/``; a missing npm, a
  non-zero exit, a timeout, or a build that leaves no ``index.html`` raises a
  RuntimeError naming the step.

When ``web/dist`` exists it is force-included as ``nfc_cli/web/dist``.

Importable without hatchling so tests can exercise the logic directly.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - fixed argv, no shell, npm resolved via PATH
from pathlib import Path
from typing import Any

try:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface
except ModuleNotFoundError:  # pragma: no cover - hatchling-less test venv
    BuildHookInterface = object  # type: ignore[assignment,misc]

__all__ = ["WebDistBuildHook", "NPM_CI_TIMEOUT_S", "NPM_BUILD_TIMEOUT_S", "SKIP_ENV"]

SKIP_ENV = "NFC_CLI_SKIP_WEB_BUILD"

_REPO_ROOT = Path(__file__).resolve().parent
_WEB_DIR = _REPO_ROOT / "web"
_DIST_DIR = _WEB_DIR / "dist"
_DIST_INDEX = _DIST_DIR / "index.html"

#: Inputs a Vite build reads; any newer than dist/index.html means stale.
_STALENESS_SOURCES = (
    _WEB_DIR / "src",
    _WEB_DIR / "index.html",
    _WEB_DIR / "package.json",
    _WEB_DIR / "vite.config.ts",
)

#: Bound on ``npm ci``; sized for a cold registry (minutes), not a warm cache.
NPM_CI_TIMEOUT_S = 600

#: Bound on ``npm run build``.
NPM_BUILD_TIMEOUT_S = 120


def _first_newer_than(reference_mtime: float) -> Path | None:
    """The first staleness-source file newer than *reference_mtime*, if any."""
    for source in _STALENESS_SOURCES:
        if source.is_file():
            if source.stat().st_mtime > reference_mtime:
                return source
        elif source.is_dir():
            for candidate in sorted(source.rglob("*")):
                if candidate.is_file() and candidate.stat().st_mtime > reference_mtime:
                    return candidate
    return None


def _run_npm_build(npm: str) -> None:
    """Run ``npm ci`` then ``npm run build`` in ``web/``, bounded and named on failure."""
    for argv, timeout in (
        ([npm, "ci"], NPM_CI_TIMEOUT_S),
        ([npm, "run", "build"], NPM_BUILD_TIMEOUT_S),
    ):
        step = "npm " + " ".join(argv[1:])
        try:
            subprocess.run(  # nosec B603 - fixed argv, no shell
                argv, cwd=_WEB_DIR, check=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"`{step}` in web/ did not finish within {timeout}s; aborting the wheel build."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"`{step}` in web/ failed (exit {exc.returncode}); aborting the wheel build."
            ) from exc


class WebDistBuildHook(BuildHookInterface):
    """Builds ``web/dist`` (when needed) and force-includes it in the wheel."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version

        if not _WEB_DIR.is_dir():
            print("hatch_build: web/ is absent; skipping web UI build.")
            return

        if os.environ.get(SKIP_ENV) == "1":
            print(f"hatch_build: {SKIP_ENV}=1; skipping npm build.")
        else:
            self._ensure_dist()

        if _DIST_INDEX.is_file():
            build_data.setdefault("force_include", {})[str(_DIST_DIR)] = "nfc_cli/web/dist"

    @staticmethod
    def _ensure_dist() -> None:
        if _DIST_INDEX.is_file():
            newer = _first_newer_than(_DIST_INDEX.stat().st_mtime)
            if newer is None:
                print(f"hatch_build: {_DIST_INDEX} is up to date; skipping npm build.")
                return
            print(f"hatch_build: {newer} is newer than {_DIST_INDEX}; rebuilding web/dist.")
        else:
            print(f"hatch_build: {_DIST_INDEX} is missing; running npm ci && npm run build.")

        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError(
                "nfc-cli wheel build needs web/dist/index.html and npm is not on PATH to "
                f"build it. Install Node.js/npm, or set {SKIP_ENV}=1 to build without the UI."
            )
        _run_npm_build(npm)
        if not _DIST_INDEX.is_file():
            raise RuntimeError(
                "npm run build exited 0 but web/dist/index.html is missing; "
                "refusing to ship an empty web UI."
            )
