"""The wheel ships nfc_cli/web/dist/index.html; hook logic unit tests."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess  # nosec B404 - fixed argv, builds this repo's own wheel
import sys
import time
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hatch_build_ut", REPO_ROOT / "hatch_build.py")
    assert spec and spec.loader  # nosec B101
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _hook(module: ModuleType) -> Any:
    return object.__new__(module.WebDistBuildHook)


def _fake_web(root: Path) -> tuple[Path, Path]:
    web = root / "web"
    (web / "src").mkdir(parents=True)
    (web / "src" / "App.tsx").write_text("//\n", encoding="utf-8")
    for name in ("index.html", "package.json", "vite.config.ts"):
        (web / name).write_text("x\n", encoding="utf-8")
    (web / "dist").mkdir()
    index = web / "dist" / "index.html"
    index.write_text("<built/>\n", encoding="utf-8")
    return web, index


def _patch(module: ModuleType, mp: pytest.MonkeyPatch, web: Path) -> None:
    mp.setattr(module, "_WEB_DIR", web)
    mp.setattr(module, "_DIST_DIR", web / "dist")
    mp.setattr(module, "_DIST_INDEX", web / "dist" / "index.html")
    mp.setattr(
        module,
        "_STALENESS_SOURCES",
        (web / "src", web / "index.html", web / "package.json", web / "vite.config.ts"),
    )


def test_hook_skips_when_web_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load()
    _patch(module, monkeypatch, tmp_path / "web")
    data: dict[str, Any] = {}
    _hook(module).initialize("0", data)
    assert data == {}


def test_hook_skips_npm_when_fresh_and_force_includes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    web, _ = _fake_web(tmp_path)
    time.sleep(1.1)
    (web / "dist" / "index.html").write_text("<built/>\n", encoding="utf-8")
    _patch(module, monkeypatch, web)
    monkeypatch.delenv(module.SKIP_ENV, raising=False)

    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("npm must not run")

    monkeypatch.setattr(module.subprocess, "run", boom)
    data: dict[str, Any] = {}
    _hook(module).initialize("0", data)
    assert data["force_include"] == {str(web / "dist"): "nfc_cli/web/dist"}


def test_hook_rebuilds_when_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load()
    web, index = _fake_web(tmp_path)
    time.sleep(1.1)
    (web / "src" / "App.tsx").write_text("// edited\n", encoding="utf-8")
    _patch(module, monkeypatch, web)
    monkeypatch.delenv(module.SKIP_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _n: "/fake/npm")
    calls: list[str] = []
    monkeypatch.setattr(module, "_run_npm_build", lambda npm: calls.append(npm))
    _hook(module).initialize("0", {})
    assert calls == ["/fake/npm"]


def test_hook_skip_env_never_runs_npm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load()
    web, _ = _fake_web(tmp_path)
    time.sleep(1.1)
    (web / "src" / "App.tsx").write_text("// edited\n", encoding="utf-8")
    _patch(module, monkeypatch, web)
    monkeypatch.setenv(module.SKIP_ENV, "1")
    monkeypatch.setattr(module, "_run_npm_build", lambda npm: pytest.fail("npm ran"))
    _hook(module).initialize("0", {})


def test_hook_missing_npm_names_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load()
    web, index = _fake_web(tmp_path)
    index.unlink()
    _patch(module, monkeypatch, web)
    monkeypatch.delenv(module.SKIP_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _n: None)
    with pytest.raises(RuntimeError, match="npm is not on PATH"):
        _hook(module).initialize("0", {})


def test_run_npm_build_names_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load()
    monkeypatch.setattr(module, "_WEB_DIR", tmp_path)

    def timeout(*a: Any, **k: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="npm", timeout=1)

    monkeypatch.setattr(module.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match=r"npm ci.*did not finish"):
        module._run_npm_build("npm")


@pytest.mark.skipif(not (REPO_ROOT / "web").is_dir(), reason="web/ is not present")
@pytest.mark.skipif(shutil.which("npm") is None, reason="npm is not on PATH")
@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not on PATH")
def test_wheel_contains_ui_index(tmp_path: Path) -> None:
    module = _load()
    timeout = module.NPM_CI_TIMEOUT_S + module.NPM_BUILD_TIMEOUT_S + 60
    result = subprocess.run(  # nosec B603
        [shutil.which("uv"), "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, result.stderr
    wheel = sorted(tmp_path.glob("*.whl"))[-1]
    with zipfile.ZipFile(wheel) as zf:
        assert "nfc_cli/web/dist/index.html" in zf.namelist()
