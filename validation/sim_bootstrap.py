"""Bootstrap for the SmartPI room-coupling validation harness.

These validation scripts drive the REAL SmartPI coupling code against an
independent third-party thermal-physics engine,
``caiusseverus/heating-simulator``, used as ground truth.

That simulator has **no license file**, so we must not vendor (copy) its code
into this repository, and it is not published on PyPI. Instead we fetch it at
runtime into a local cache *outside* the repo (a shallow git clone pinned to a
known commit). Nothing third-party is committed here.

Resolution order for the simulator source:
  1. ``$HEATING_SIMULATOR_PATH`` — point this at a local checkout to use it as-is.
  2. A cached clone under ``$XDG_CACHE_HOME`` (or ``~/.cache``).
  3. A fresh shallow clone of the pinned commit (requires git + network once).

If none of those work (e.g. offline with no cache), a RuntimeError explains how
to provide the simulator manually.  Set ``HEATING_SIMULATOR_ALLOW_UNPINNED=1``
to allow a last-resort fallback to the unversioned default-branch tip (not
recommended; results may differ from the pinned validated configuration).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SIMULATOR_URL = "https://github.com/caiusseverus/heating-simulator"
# Pinned for reproducibility. Bump deliberately if you re-validate against a
# newer simulator revision.
SIMULATOR_PINNED_SHA = "97d46172b43f38db749a88e6c52a781e0a2fa375"

REPO_ROOT = Path(__file__).resolve().parent.parent


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "vtherm-smartpi-validation" / "heating-simulator"


def _looks_like_simulator(path: Path) -> bool:
    return (path / "thermal_model.py").is_file()


def _run(cmd: list[str], cwd: Path | None = None) -> bool:
    try:
        subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _fetch_pinned(dest: Path) -> bool:
    """Shallow-fetch the exact pinned commit (GitHub allows fetch-by-SHA)."""
    dest.mkdir(parents=True, exist_ok=True)
    ok = (
        _run(["git", "init", "-q"], cwd=dest)
        and _run(["git", "remote", "add", "origin", SIMULATOR_URL], cwd=dest)
        and _run(["git", "fetch", "-q", "--depth", "1", "origin", SIMULATOR_PINNED_SHA], cwd=dest)
        and _run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=dest)
    )
    return ok and _looks_like_simulator(dest)


def _fetch_tip(dest: Path) -> bool:
    """Fallback: shallow clone the default branch (unpinned)."""
    if dest.exists():
        return _looks_like_simulator(dest)
    if _run(["git", "clone", "-q", "--depth", "1", SIMULATOR_URL, str(dest)]):
        return _looks_like_simulator(dest)
    return False


def simulator_path() -> Path:
    override = os.environ.get("HEATING_SIMULATOR_PATH")
    if override:
        p = Path(override).expanduser().resolve()
        if _looks_like_simulator(p):
            return p
        raise RuntimeError(
            f"HEATING_SIMULATOR_PATH={override} does not contain thermal_model.py"
        )

    cache = _cache_dir()
    if _looks_like_simulator(cache):
        return cache

    # Try a pinned shallow fetch; fail closed on failure (supply-chain risk).
    # Set HEATING_SIMULATOR_ALLOW_UNPINNED=1 to allow the unpinned-tip fallback.
    import shutil

    if _fetch_pinned(cache):
        return cache
    shutil.rmtree(cache, ignore_errors=True)
    if os.environ.get("HEATING_SIMULATOR_ALLOW_UNPINNED", "").strip() not in ("", "0"):
        if _fetch_tip(cache):
            sys.stderr.write(
                "[validation] WARNING: could not fetch pinned simulator commit "
                f"{SIMULATOR_PINNED_SHA[:10]}; using the latest default-branch tip "
                "(results may differ slightly; set HEATING_SIMULATOR_ALLOW_UNPINNED=0 "
                "to disable this fallback).\n"
            )
            return cache
    else:
        raise RuntimeError(
            f"Could not fetch pinned heating-simulator commit {SIMULATOR_PINNED_SHA[:10]}.\n"
            "Refusing to fall back to the unpinned default-branch tip (supply-chain risk).\n"
            "Fix it one of these ways:\n"
            f"  • git clone {SIMULATOR_URL} somewhere, check out {SIMULATOR_PINNED_SHA},\n"
            "    then set HEATING_SIMULATOR_PATH=/that/path\n"
            f"  • or place a checkout of that commit at {cache}\n"
            "To allow an unpinned fallback (not recommended): "
            "HEATING_SIMULATOR_ALLOW_UNPINNED=1 python validation/..."
        )

    raise RuntimeError(
        "Could not obtain the heating-simulator (no cache, and git/network "
        "fetch failed).\n"
        "Fix it one of these ways:\n"
        f"  • git clone {SIMULATOR_URL} somewhere, then set "
        "HEATING_SIMULATOR_PATH=/that/path\n"
        f"  • or place a checkout at {cache}\n"
        "The simulator is pure-Python (stdlib only) and is fetched at runtime "
        "because it has no license for redistribution and is not on PyPI."
    )


def setup() -> Path:
    """Put the repo root and the simulator on sys.path; return the simulator path.

    Call this once at the top of a validation script, before importing either
    ``custom_components.vtherm_smartpi`` or ``thermal_model``.
    """
    sim = simulator_path()
    for p in (str(REPO_ROOT), str(sim)):
        if p not in sys.path:
            sys.path.insert(0, p)
    return sim


if __name__ == "__main__":
    print(f"repo root        : {REPO_ROOT}")
    print(f"simulator source : {setup()}")
    import thermal_model  # noqa: E402  (verifies the path works)
    print("thermal_model OK :", hasattr(thermal_model, "SimpleThermalModel"))
