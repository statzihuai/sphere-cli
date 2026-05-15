"""Evaluate fidelity and privacy by delegating to the sphere-eval sidecar binary.

sphere-eval is bundled inside the sphere-cli distribution so the CLI is fully
self-contained — no SPHERE.app or separate installation required.

Runtime discovery order (first match wins):
  1. SPHERE_EVAL_BIN environment variable — explicit override / testing
  2. Bundled binary next to this executable: <exe-dir>/sphere-eval/sphere-eval
  3. Dev tree: ../python-sidecar/dist/sphere-eval/sphere-eval
  4. /Applications/SPHERE.app  (fallback for users who have the app)
  5. sphere-eval on PATH
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
    """Return path to sphere-eval, or raise FileNotFoundError."""

    def _ok(p: Path) -> bool:
        return p.is_file() and os.access(p, os.X_OK)

    # 1. Explicit override
    env = os.environ.get("SPHERE_EVAL_BIN")
    if env and _ok(p := Path(env)):
        return p

    # 2. Bundled alongside this binary (onedir bundle: exe lives in sphere-cli/)
    #    sphere-eval is copied to sphere-cli/sphere-eval/sphere-eval at build time.
    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).parent / "sphere-eval" / "sphere-eval"
        if _ok(bundled):
            return bundled

    # 3. Dev tree: sphere-cli/../python-sidecar/dist/sphere-eval/sphere-eval
    here = Path(__file__).parent          # sphere_cli/ (or _internal/sphere_cli/)
    for root in [here.parent.parent, here.parent.parent.parent]:
        root = root.resolve()
        # release app (canonical)
        c = root / "release" / "mac-arm64" / "SPHERE.app" / _SIDECAR_REL
        if _ok(c):
            return c
        # raw sidecar build
        c = root / "python-sidecar" / "dist" / "sphere-eval" / "sphere-eval"
        if _ok(c):
            return c

    # 4. Installed macOS app bundles
    for app_dir in [Path("/Applications"), Path.home() / "Applications"]:
        c = app_dir / "SPHERE.app" / _SIDECAR_REL
        if _ok(c):
            return c

    # 5. PATH
    found = shutil.which("sphere-eval")
    if found:
        return Path(found)

    raise FileNotFoundError(
        "sphere-eval binary not found.\n"
        "  Reinstall sphere-cli, or set SPHERE_EVAL_BIN=/path/to/sphere-eval."
    )


# ── Public API ────────────────────────────────────────────────────────────────

def evaluate(
    real_path:    Path | str,
    synth_path:   Path | str,
    *,
    n_attacks:    int        = 500,
    n_secrets:    int        = 5,
    n_atk_cap:    int        = 2000,
    n_neighbors:  int        = 1,
    n_aux_cols:   int        = 20,
    seed:         int | None = None,
    skip_privacy: bool       = False,
    on_progress:  Progress | None = None,
) -> dict:
    """Evaluate a real/synthetic CSV pair via the bundled sphere-eval binary.

    Returns a result dict with keys: nReal, nSynth, pOrig, pEnc, fidelity,
    privacy (or None), params, engine.

    Raises FileNotFoundError if sphere-eval cannot be located.
    Raises ValueError for user-visible problems (header mismatch, etc.).
    """
    binary    = _find_sidecar()
    real_abs  = Path(real_path).resolve()
    synth_abs = Path(synth_path).resolve()

    cmd = [
        str(binary),
        "--real",        str(real_abs),
        "--synth",       str(synth_abs),
        "--n-attacks",   str(n_attacks),
        "--n-secrets",   str(n_secrets),
        "--n-atk-cap",   str(n_atk_cap),
        "--n-neighbors", str(n_neighbors),
        "--n-aux-cols",  str(n_aux_cols),
    ]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    if skip_privacy:
        cmd.append("--skip-privacy")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

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
                pass

    t = threading.Thread(target=_drain_stderr, daemon=True)
    t.start()
    assert proc.stdout is not None
    stdout_bytes = proc.stdout.read()
    t.join()
    proc.wait()

    if not stdout_bytes.strip():
        raise RuntimeError(
            f"sphere-eval exited {proc.returncode} with no output."
        )

    result = json.loads(stdout_bytes.decode(errors="replace"))

    if "error" in result:
        raise ValueError(result["error"])
    if proc.returncode != 0:
        raise RuntimeError(f"sphere-eval exited {proc.returncode}.")

    result.setdefault("idColsExcluded", [])
    return result
