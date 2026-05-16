"""Evaluate fidelity and privacy of a synthetic dataset.

Runs entirely in-process using the bundled anonymeter/numba — the CLI is a
fully self-contained standalone tool with no dependency on SPHERE.app or the
sphere-eval binary.

Privacy attacks pass the original mixed-type DataFrames to Anonymeter so it
uses its native Gower distance (matching the sidecar and reproduce.py).
The global numpy RNG is re-seeded immediately before each of the 9 Anonymeter
calls so all three baselines (real / col-shuffle / synth) attack the exact same
rows — ensuring a fair apples-to-apples comparison and reproducible results.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

Progress = Callable[[float, str], None]
_MAX_FIDELITY_N = 50_000


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

    Returns a result dict matching the structure produced by the SPHERE.app
    sidecar (nReal, nSynth, pOrig, pEnc, fidelity, privacy, …).

    Raises ValueError for user-visible problems (header mismatch, etc.).
    """
    def prog(frac: float, msg: str) -> None:
        if on_progress:
            on_progress(frac, msg)

    real_path  = Path(real_path)
    synth_path = Path(synth_path)

    # ── Load ──────────────────────────────────────────────────────────────────
    import time as _time
    import threading as _th
    _t_start = _time.perf_counter()

    _state = [0.0, "loading pandas"]   # [frac, msg] — shared with spinner
    _stop  = _th.Event()
    def _spin():
        j = 0
        while not _stop.wait(0.3):
            prog(_state[0], _state[1] + " ." * (j % 4 + 1))
            j += 1
    _th.Thread(target=_spin, daemon=True).start()

    _t = _time.perf_counter()
    import pandas as pd
    prog(0.015, f"✓ pandas  ({(_time.perf_counter()-_t)*1000:.0f} ms)")
    _state[0] = 0.015; _state[1] = "loading pyarrow"

    _t = _time.perf_counter()
    _pa_csv_mod = None; _pa_available = False
    try:
        import pyarrow.csv as _pa_csv_mod
        _pa_available = True
    except ImportError:
        pass
    prog(0.03, f"✓ pyarrow  ({(_time.perf_counter()-_t)*1000:.0f} ms)")
    _state[0] = 0.03; _state[1] = "loading sphere core"

    _t = _time.perf_counter()
    from ._core import (
        _detect_id_columns,
        encode_pair,
        fidelity_metrics,
        column_shuffle,
        normalize,
        _patch_anonymeter_nn,
        _run_so,
        _run_lk,
        _run_inf,
    )
    _stop.set()
    _t_load1_end = _time.perf_counter()
    prog(0.045, f"✓ sphere core  ({(_t_load1_end-_t)*1000:.0f} ms)")

    try:
        if _pa_available:
            real  = _pa_csv_mod.read_csv(str(real_path)).to_pandas()
            synth = _pa_csv_mod.read_csv(str(synth_path)).to_pandas()
        else:
            real  = pd.read_csv(real_path,  low_memory=False)
            synth = pd.read_csv(synth_path, low_memory=False)
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
        _load_ms = int((_t_load1_end - _t_start) * 1000)
        _run_ms  = int((_time.perf_counter() - _t_load1_end) * 1000)
        return {**base_result, "privacy": None,
                "loadMs": _load_ms, "runMs": _run_ms}

    # ── Warm up anonymeter / sklearn imports ──────────────────────────────────
    # anonymeter lazily imports sklearn.neighbors on the first evaluator call.
    # In a frozen binary this cold-load can take several seconds and stalls the
    # progress bar mid-way through attack 1.  Pre-importing here absorbs that
    # cost before per-call progress tracking begins.
    _t_load2_start = _time.perf_counter()
    _state2 = [0.16, "loading anonymeter"]
    _stop2  = _th.Event()
    def _spin2():
        j = 0
        while not _stop2.wait(0.3):
            prog(_state2[0], _state2[1] + " ." * (j % 4 + 1))
            j += 1
    _th.Thread(target=_spin2, daemon=True).start()

    _t = _time.perf_counter()
    try:
        from anonymeter.evaluators import SinglingOutEvaluator as _SO  # noqa: F401
    except Exception:
        pass
    prog(0.17, f"✓ anonymeter  ({(_time.perf_counter()-_t)*1000:.0f} ms)")
    _state2[0] = 0.17; _state2[1] = "loading sklearn"

    _t = _time.perf_counter()
    try:
        from anonymeter.evaluators import LinkabilityEvaluator  as _LK  # noqa: F401
        from anonymeter.evaluators import InferenceEvaluator    as _IE  # noqa: F401
        _patch_anonymeter_nn()
    except Exception:
        pass
    _stop2.set()
    _t_load2_end = _time.perf_counter()
    prog(0.18, f"✓ sklearn  ({(_t_load2_end-_t)*1000:.0f} ms)")

    # ── Privacy ───────────────────────────────────────────────────────────────
    # Pass original DataFrames (with native dtypes, including string categoricals)
    # so Anonymeter uses its native Gower distance for mixed-type columns.
    # Re-seed the global numpy RNG immediately before each Anonymeter call so
    # all three baselines attack the exact same rows for a fair comparison.
    rng  = np.random.RandomState(seed_used)
    shuf = column_shuffle(real, rng)

    # 9 anonymeter calls — progress bar spans 19 %→89 % (2 % reserved for
    # the loading phase above; attacks are evenly spaced within that range).
    _P = [0.19, 0.28, 0.37, 0.46, 0.55, 0.64, 0.73, 0.82, 0.89]

    prog(_P[0], "singling-out  1/9")
    np.random.seed(seed_used)
    so_real  = _run_so(real, real,  n_attacks, n_atk_cap)

    prog(_P[1], "singling-out  2/9")
    np.random.seed(seed_used)
    so_shuf  = _run_so(real, shuf,  n_attacks, n_atk_cap)

    prog(_P[2], "singling-out  3/9")
    np.random.seed(seed_used)
    so_synth = _run_so(real, synth, n_attacks, n_atk_cap)

    prog(_P[3], "linkability  4/9")
    np.random.seed(seed_used)
    lk_real  = _run_lk(real, real,  n_attacks, n_atk_cap, n_neighbors, n_aux_cols, np.random.RandomState(seed_used))

    prog(_P[4], "linkability  5/9")
    np.random.seed(seed_used)
    lk_shuf  = _run_lk(real, shuf,  n_attacks, n_atk_cap, n_neighbors, n_aux_cols, np.random.RandomState(seed_used))

    prog(_P[5], "linkability  6/9")
    np.random.seed(seed_used)
    lk_synth = _run_lk(real, synth, n_attacks, n_atk_cap, n_neighbors, n_aux_cols, np.random.RandomState(seed_used))

    prog(_P[6], "inference  7/9")
    np.random.seed(seed_used)
    inf_real  = _run_inf(real, real,  n_attacks, n_atk_cap, np.random.RandomState(seed_used), n_secrets)

    prog(_P[7], "inference  8/9")
    np.random.seed(seed_used)
    inf_shuf  = _run_inf(real, shuf,  n_attacks, n_atk_cap, np.random.RandomState(seed_used), n_secrets)

    prog(_P[8], "inference  9/9")
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

    _t_end   = _time.perf_counter()
    _load_ms = int((_t_load1_end - _t_start + _t_load2_end - _t_load2_start) * 1000)
    _run_ms  = int((_t_end - _t_start) * 1000) - _load_ms

    return {
        **base_result,
        "privacy": privacy,
        "loadMs":  _load_ms,
        "runMs":   _run_ms,
        "params": {
            "nAttacks":   n_attacks,
            "nSecrets":   n_secrets,
            "nAtkCap":    n_atk_cap,
            "nNeighbors": n_neighbors,
            "nAuxCols":   n_aux_cols,
            "seed":       seed_used,
        },
    }
