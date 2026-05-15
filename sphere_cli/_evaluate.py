"""Evaluate fidelity and privacy by delegating to the sphere-eval sidecar binary.

The CLI shells out to the same PyInstaller-bundled sphere-eval binary that the
SPHERE.app uses, guaranteeing bit-exact results between the CLI and the app.

Binary discovery order (first match wins):
  1. SPHERE_EVAL_BIN environment variable — explicit override
  2. Next to the running executable (frozen bundle co-location)
  3. Dev tree: release/mac-arm64/SPHERE.app  (freshly built, canonical reference)
  4. Dev tree: python-sidecar/dist/sphere-eval/sphere-eval  (raw sidecar build)
  5. /Applications/SPHERE.app  (standard macOS install)
  6. ~/Applications/SPHERE.app  (user-level macOS install)
  7. sphere-eval on PATH
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

Progress = Callable[[float, str], None]

_SIDECAR_REL = Path("Contents") / "Resources" / "sidecar" / "sphere-eval" / "sphere-eval"


# ── Binary discovery ──────────────────────────────────────────────────────────

def _find_sidecar() -> Path:
    """Return the path to the sphere-eval binary, or raise FileNotFoundError."""

    def _ok(p: Path) -> bool:
        return p.is_file() and os.access(p, os.X_OK)

    # 1. Explicit env-var override
    env = os.environ.get("SPHERE_EVAL_BIN")
    if env and _ok(p := Path(env)):
        return p

    # 2. Frozen bundle: check _MEIPASS and the directory holding the sphere binary
    if getattr(sys, "frozen", False):
        for base in [Path(sys._MEIPASS), Path(sys.executable).parent]:
            c = base / "sidecar" / "sphere-eval" / "sphere-eval"
            if _ok(c):
                return c

    # 3 & 4. Dev tree — search up from __file__ and from the sphere binary
    # Candidate roots: the sphere project root sits two or three levels above
    # this file (from source) or one to two levels above the binary.
    roots: list[Path] = []
    here = Path(__file__).parent          # sphere_cli/ (or _internal/sphere_cli/ frozen)
    roots += [here.parent.parent, here.parent.parent.parent]
    if getattr(sys, "frozen", False):
        # exe = sphere-cli/dist/sphere-cli/sphere → go up 3 to reach sphere-cli/,
        # then one more to reach the sphere/ project root.
        exe_root = Path(sys.executable).parent.parent.parent
        roots += [exe_root, exe_root.parent]

    for root in roots:
        root = root.resolve()
        # 3. Freshly-built release app (canonical reference, same binary the app ships)
        rel_app = root / "release" / "mac-arm64" / "SPHERE.app" / _SIDECAR_REL
        if _ok(rel_app):
            return rel_app
        # 4. Raw sidecar build output
        raw = root / "python-sidecar" / "dist" / "sphere-eval" / "sphere-eval"
        if _ok(raw):
            return raw

    # 5–6. Installed macOS app bundles
    for app_dir in [Path("/Applications"), Path.home() / "Applications"]:
        c = app_dir / "SPHERE.app" / _SIDECAR_REL
        if _ok(c):
            return c

    # 7. PATH
    found = shutil.which("sphere-eval")
    if found:
        return Path(found)

    raise FileNotFoundError(
        "sphere-eval binary not found.\n"
        "  • Install SPHERE.app in /Applications, or\n"
        "  • Set SPHERE_EVAL_BIN=/path/to/sphere-eval, or\n"
        "  • Build the sidecar: cd python-sidecar && ./build.sh"
    )


# ── Public API ────────────────────────────────────────────────────────────────

def evaluate(
    real_path:    Path | str,
    synth_path:   Path | str,
    *,
    n_attacks:    int       = 500,
    n_secrets:    int       = 5,
    n_atk_cap:    int       = 2000,
    n_neighbors:  int       = 1,
    n_aux_cols:   int       = 20,
    seed:         int | None = None,
    skip_privacy: bool      = False,
    on_progress:  Progress | None = None,
) -> dict:
    """Evaluate a real/synthetic CSV pair via the sphere-eval binary.

    Delegates entirely to the same PyInstaller-bundled sphere-eval binary used
    by the SPHERE.app, guaranteeing identical results between CLI and app.

    Returns a result dict with keys: nReal, nSynth, pOrig, pEnc, fidelity,
    privacy (or None), params, engine.

    Raises FileNotFoundError if sphere-eval cannot be located.
    Raises ValueError for user-visible problems reported by the binary
    (header mismatch, column mismatch, etc.).
    Raises RuntimeError for unexpected binary failures.
    """
    binary    = _find_sidecar()
    real_abs  = Path(real_path).resolve()
    synth_abs = Path(synth_path).resolve()

    cmd = [
        str(binary),
        "--real",         str(real_abs),
        "--synth",        str(synth_abs),
        "--n-attacks",    str(n_attacks),
        "--n-secrets",    str(n_secrets),
        "--n-atk-cap",    str(n_atk_cap),
        "--n-neighbors",  str(n_neighbors),
        "--n-aux-cols",   str(n_aux_cols),
    ]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    if skip_privacy:
        cmd.append("--skip-privacy")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Read stderr in a background thread so progress lines are forwarded
    # immediately without blocking the stdout read.
    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            if not on_progress:
                continue
            try:
                obj = json.loads(raw.decode(errors="replace"))
                if obj.get("type") == "progress":
                    on_progress(float(obj["frac"]), str(obj.get("msg", "")))
            except Exception:
                pass  # non-JSON stderr lines (warnings, etc.) are silently ignored

    t = threading.Thread(target=_drain_stderr, daemon=True)
    t.start()

    assert proc.stdout is not None
    stdout_bytes = proc.stdout.read()
    t.join()
    proc.wait()

    if not stdout_bytes.strip():
        raise RuntimeError(
            f"sphere-eval exited with code {proc.returncode} and produced no output."
        )

    try:
        result = json.loads(stdout_bytes.decode(errors="replace"))
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"sphere-eval produced invalid JSON: {e}\n"
            f"Raw output: {stdout_bytes[:500]!r}"
        ) from e

    # The binary emits {"error": "…"} on stderr and exits non-zero on failure.
    if "error" in result:
        msg = result["error"]
        # Re-raise as ValueError so the CLI shows a clean error (not a traceback).
        raise ValueError(msg)

    if proc.returncode != 0:
        raise RuntimeError(
            f"sphere-eval exited with code {proc.returncode}."
        )

    # Normalise: older sidecar builds may omit idColsExcluded; default to []
    result.setdefault("idColsExcluded", [])

    return result
