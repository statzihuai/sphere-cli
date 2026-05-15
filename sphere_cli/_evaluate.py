"""Evaluate fidelity and privacy of a synthetic dataset.

Primary path: shell out to the sphere-eval sidecar binary (same one used by
SPHERE.app) for bit-exact agreement between CLI and app.

Fallback path: run evaluation in-process using the bundled anonymeter/numba
when sphere-eval cannot be located (standalone install without SPHERE.app).

Sidecar discovery order (first match wins):
  1. SPHERE_EVAL_BIN environment variable
  2. Frozen bundle co-location (_MEIPASS or exe directory)
  3. Dev tree: release/mac-arm64/SPHERE.app  (freshly-built release app)
  4. Dev tree: python-sidecar/dist/sphere-eval/sphere-eval
  5. /Applications/SPHERE.app
  6. ~/Applications/SPHERE.app
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

import numpy as np
import pandas as pd

from ._core import (
    _detect_id_columns,
    encode_pair,
    fidelity_metrics,
    column_shuffle,
    normalize,
    _run_so,
    _run_lk,
    _run_inf,
)

Progress = Callable[[float, str], None]
_MAX_FIDELITY_N = 50_000
_SIDECAR_REL = Path("Contents") / "Resources" / "sidecar" / "sphere-eval" / "sphere-eval"


# ── Sidecar binary discovery ──────────────────────────────────────────────────

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
    roots: list[Path] = []
    here = Path(__file__).parent          # sphere_cli/ (or _internal/sphere_cli/ frozen)
    roots += [here.parent.parent, here.parent.parent.parent]
    if getattr(sys, "frozen", False):
        exe_root = Path(sys.executable).parent.parent.parent
        roots += [exe_root, exe_root.parent]

    for root in roots:
        root = root.resolve()
        # 3. Freshly-built release app (canonical reference)
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

    raise FileNotFoundError("sphere-eval not found")


# ── Evaluation via sidecar subprocess ────────────────────────────────────────

def _evaluate_via_sidecar(
    binary: Path,
    real_path: Path,
    synth_path: Path,
    n_attacks: int,
    n_secrets: int,
    n_atk_cap: int,
    n_neighbors: int,
    n_aux_cols: int,
    seed: int | None,
    skip_privacy: bool,
    on_progress: Progress | None,
) -> dict:
    cmd = [
        str(binary),
        "--real",        str(real_path),
        "--synth",       str(synth_path),
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


# ── Built-in evaluation (fallback when sphere-eval is not installed) ──────────

def _evaluate_builtin(
    real_path: Path,
    synth_path: Path,
    n_attacks: int,
    n_secrets: int,
    n_atk_cap: int,
    n_neighbors: int,
    n_aux_cols: int,
    seed: int | None,
    skip_privacy: bool,
    on_progress: Progress | None,
) -> dict:
    def prog(frac: float, msg: str) -> None:
        if on_progress:
            on_progress(frac, msg)

    # ── Load ──────────────────────────────────────────────────────────────────
    prog(0.0, "loading")
    try:
        import pyarrow.csv as _pa_csv
        real  = _pa_csv.read_csv(str(real_path)).to_pandas()
        synth = _pa_csv.read_csv(str(synth_path)).to_pandas()
    except Exception:
        real  = pd.read_csv(real_path,  low_memory=False)
        synth = pd.read_csv(synth_path, low_memory=False)

    # ── Header check ──────────────────────────────────────────────────────────
    if list(real.columns) != list(synth.columns):
        raise ValueError(
            f"Header mismatch:\n  real:  {list(real.columns[:8])}\n"
            f"  synth: {list(synth.columns[:8])}"
        )

    # ── Drop ID columns ───────────────────────────────────────────────────────
    id_col_set = _detect_id_columns(real)
    id_col_names: list[str] = []
    if id_col_set:
        id_col_names = [real.columns[i] for i in sorted(id_col_set)]
        real  = real.drop(columns=id_col_names)
        synth = synth.drop(columns=id_col_names)

    # ── Column-set validation ─────────────────────────────────────────────────
    real_cols, synth_cols = set(real.columns), set(synth.columns)
    if real_cols != synth_cols:
        only_real  = sorted(real_cols  - synth_cols)
        only_synth = sorted(synth_cols - real_cols)
        parts = ["Column mismatch after ID removal — are these a matched pair?"]
        if only_real:
            parts.append(f"  Only in real  ({len(only_real)}): {only_real[:8]}")
        if only_synth:
            parts.append(f"  Only in synth ({len(only_synth)}): {only_synth[:8]}")
        raise ValueError("\n".join(parts))

    # ── Align numeric dtypes ──────────────────────────────────────────────────
    for col in real.columns:
        if pd.api.types.is_numeric_dtype(real[col]) or pd.api.types.is_numeric_dtype(synth[col]):
            real[col]  = pd.to_numeric(real[col],  errors='coerce').astype(float)
            synth[col] = pd.to_numeric(synth[col], errors='coerce').astype(float)

    seed_used = seed if seed is not None else int(
        np.random.SeedSequence().entropy & 0xFFFFFFFF
    )

    # ── Fidelity ──────────────────────────────────────────────────────────────
    if len(real) > _MAX_FIDELITY_N:
        sub       = np.random.RandomState(seed_used).choice(len(real), _MAX_FIDELITY_N, replace=False)
        real_fid  = real.iloc[sub].reset_index(drop=True)
        synth_fid = synth.iloc[sub].reset_index(drop=True)
        prog(0.04, f"subsampled {_MAX_FIDELITY_N:,} / {len(real):,} rows for fidelity")
    else:
        real_fid, synth_fid = real, synth

    prog(0.05, "fidelity")
    fid = fidelity_metrics(real_fid, synth_fid)
    real_fid = synth_fid = None
    prog(0.15, "fidelity done")

    re_, se_, enc_cols = encode_pair(real, synth)
    base_result = {
        "nReal":           len(real),
        "nSynth":          len(synth),
        "pOrig":           real.shape[1],
        "pEnc":            re_.shape[1],
        "numericCols":     sum(1 for c in real.columns if pd.api.types.is_numeric_dtype(real[c])),
        "categoricalCols": sum(1 for c in real.columns if not pd.api.types.is_numeric_dtype(real[c])),
        "idColsExcluded":  id_col_names,
        "fidelity":        fid,
    }

    if skip_privacy:
        return {**base_result, "privacy": None}

    # ── Privacy — pass original DataFrames so Anonymeter uses Gower distance ──
    rng  = np.random.RandomState(seed_used)
    shuf = column_shuffle(real, rng)

    prog(0.20, "singling out …")
    np.random.seed(seed_used)
    so_real  = _run_so(real, real,  n_attacks, n_atk_cap)
    np.random.seed(seed_used)
    so_shuf  = _run_so(real, shuf,  n_attacks, n_atk_cap)
    np.random.seed(seed_used)
    so_synth = _run_so(real, synth, n_attacks, n_atk_cap)

    prog(0.45, "linkability …")
    np.random.seed(seed_used)
    lk_real  = _run_lk(real, real,  n_attacks, n_atk_cap, n_neighbors, n_aux_cols, np.random.RandomState(seed_used))
    np.random.seed(seed_used)
    lk_shuf  = _run_lk(real, shuf,  n_attacks, n_atk_cap, n_neighbors, n_aux_cols, np.random.RandomState(seed_used))
    np.random.seed(seed_used)
    lk_synth = _run_lk(real, synth, n_attacks, n_atk_cap, n_neighbors, n_aux_cols, np.random.RandomState(seed_used))

    prog(0.65, "inference …")
    np.random.seed(seed_used)
    inf_real  = _run_inf(real, real,  n_attacks, n_atk_cap, np.random.RandomState(seed_used), n_secrets)
    np.random.seed(seed_used)
    inf_shuf  = _run_inf(real, shuf,  n_attacks, n_atk_cap, np.random.RandomState(seed_used), n_secrets)
    np.random.seed(seed_used)
    inf_synth = _run_inf(real, synth, n_attacks, n_atk_cap, np.random.RandomState(seed_used), n_secrets)

    privacy = {
        "singlingOut": {
            "rReal": so_real, "rShuffle": so_shuf, "rSynth": so_synth,
            "score": normalize(so_synth, so_real, so_shuf),
        },
        "linkability": {
            "rReal": lk_real, "rShuffle": lk_shuf, "rSynth": lk_synth,
            "score": normalize(lk_synth, lk_real, lk_shuf),
        },
        "inference": {
            "rReal": inf_real, "rShuffle": inf_shuf, "rSynth": inf_synth,
            "score": normalize(inf_synth, inf_real, inf_shuf),
        },
    }
    privacy["composite"] = (
        privacy["singlingOut"]["score"]
        + privacy["linkability"]["score"]
        + privacy["inference"]["score"]
    ) / 3
    prog(1.0, "done")

    return {
        **base_result,
        "privacy": privacy,
        "params": {
            "nAttacks":   n_attacks,
            "nSecrets":   n_secrets,
            "nAtkCap":    n_atk_cap,
            "nNeighbors": n_neighbors,
            "nAuxCols":   n_aux_cols,
            "seed":       seed_used,
        },
    }


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
    """Evaluate a real/synthetic CSV pair.

    Prefers the sphere-eval sidecar binary (bit-exact match with SPHERE.app)
    and falls back to built-in Anonymeter evaluation when the binary is not
    available (standalone install without SPHERE.app).

    Returns a result dict with keys: nReal, nSynth, pOrig, pEnc, fidelity,
    privacy (or None), params.

    Raises ValueError for user-visible problems (header mismatch, etc.).
    """
    real_abs  = Path(real_path).resolve()
    synth_abs = Path(synth_path).resolve()

    try:
        binary = _find_sidecar()
        return _evaluate_via_sidecar(
            binary, real_abs, synth_abs,
            n_attacks, n_secrets, n_atk_cap, n_neighbors, n_aux_cols,
            seed, skip_privacy, on_progress,
        )
    except FileNotFoundError:
        # sphere-eval not installed — run evaluation in-process
        return _evaluate_builtin(
            real_abs, synth_abs,
            n_attacks, n_secrets, n_atk_cap, n_neighbors, n_aux_cols,
            seed, skip_privacy, on_progress,
        )
