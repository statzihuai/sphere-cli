"""Evaluate fidelity and privacy of a synthetic dataset."""
from __future__ import annotations

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


def evaluate(
    real_path: Path | str,
    synth_path: Path | str,
    *,
    n_attacks: int = 500,
    n_secrets: int = 5,
    n_atk_cap: int = 2000,
    n_neighbors: int = 1,
    n_aux_cols: int = 20,
    seed: int | None = None,
    skip_privacy: bool = False,
    on_progress: Progress | None = None,
) -> dict:
    """Evaluate a real/synthetic CSV pair.

    Returns a result dict matching the structure produced by the
    SPHERE.app sidecar (nReal, nSynth, pOrig, pEnc, fidelity, privacy, …).

    Raises ValueError for user-visible problems (header mismatch, etc.).
    """
    def prog(frac: float, msg: str) -> None:
        if on_progress:
            on_progress(frac, msg)

    real_path  = Path(real_path)
    synth_path = Path(synth_path)

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
        sub      = np.random.RandomState(seed_used).choice(len(real), _MAX_FIDELITY_N, replace=False)
        real_fid  = real.iloc[sub].reset_index(drop=True)
        synth_fid = synth.iloc[sub].reset_index(drop=True)
        prog(0.04, f"subsampled {_MAX_FIDELITY_N:,} / {len(real):,} rows for fidelity")
    else:
        real_fid, synth_fid = real, synth

    prog(0.05, "fidelity")
    fid = fidelity_metrics(real_fid, synth_fid)
    real_fid = synth_fid = None  # free memory
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

    # ── Privacy (Anonymeter) ──────────────────────────────────────────────────
    # Pre-encode to purely numeric DataFrames so anonymeter never invokes its
    # Numba-dependent mixed-types Gower kNN kernel.
    real_enc  = pd.DataFrame(re_, columns=enc_cols)
    synth_enc = pd.DataFrame(se_, columns=enc_cols)

    rng  = np.random.RandomState(seed_used)
    shuf = column_shuffle(real_enc, rng)

    prog(0.20, "singling out …")
    so_real  = _run_so(real_enc, real_enc,  n_attacks, n_atk_cap)
    so_shuf  = _run_so(real_enc, shuf,      n_attacks, n_atk_cap)
    so_synth = _run_so(real_enc, synth_enc, n_attacks, n_atk_cap)

    prog(0.45, "linkability …")
    lk_real  = _run_lk(real_enc, real_enc,  n_attacks, n_atk_cap, n_neighbors, n_aux_cols, np.random.RandomState(seed_used))
    lk_shuf  = _run_lk(real_enc, shuf,      n_attacks, n_atk_cap, n_neighbors, n_aux_cols, np.random.RandomState(seed_used))
    lk_synth = _run_lk(real_enc, synth_enc, n_attacks, n_atk_cap, n_neighbors, n_aux_cols, np.random.RandomState(seed_used))

    prog(0.65, "inference …")
    inf_real  = _run_inf(real_enc, real_enc,  n_attacks, n_atk_cap, np.random.RandomState(seed_used), n_secrets)
    inf_shuf  = _run_inf(real_enc, shuf,      n_attacks, n_atk_cap, np.random.RandomState(seed_used), n_secrets)
    inf_synth = _run_inf(real_enc, synth_enc, n_attacks, n_atk_cap, np.random.RandomState(seed_used), n_secrets)

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
