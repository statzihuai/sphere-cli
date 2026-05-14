"""Generate synthetic data from a CSV using SPHERE."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from ._algo import sphere
from ._core import _detect_id_columns

Progress = Callable[[float, str], None]
_MAX_INT_CAT_UNIQUE = 20


def generate(
    input_path: Path | str,
    output_path: Path | str,
    *,
    k: int = 2,
    theta: float | None = None,
    delta: float | None = None,
    mix_prob: float = 0.75,
    seed: int | None = None,
    on_progress: Progress | None = None,
) -> dict:
    """Run SPHERE synthesis and write the result to *output_path*.

    Returns a dict with keys: rows, cols, transform, idColDetected,
    idColName, elapsedMs, seed.

    Raises ValueError for user-visible problems (missing values, no data
    columns, etc.).
    """
    if theta is None:
        theta = float(np.pi / 6)
    if delta is None:
        delta = float(5.0 * np.pi / 180.0)

    def prog(frac: float, msg: str) -> None:
        if on_progress:
            on_progress(frac, msg)

    t0 = time.perf_counter()
    input_path  = Path(input_path)
    output_path = Path(output_path)

    # ── Load ──────────────────────────────────────────────────────────────────
    prog(0.0, "loading")
    try:
        import pyarrow.csv as _pa_csv
        df = _pa_csv.read_csv(str(input_path)).to_pandas()
    except Exception:
        df = pd.read_csv(input_path, low_memory=False)

    n_rows, n_cols_total = len(df), len(df.columns)
    prog(0.04, f"loaded {n_rows:,} rows × {n_cols_total} cols")

    # ── ID columns ────────────────────────────────────────────────────────────
    id_col_set   = _detect_id_columns(df)
    id_col_names = [df.columns[i] for i in sorted(id_col_set)]
    id_col_name_set = set(id_col_names)
    data_cols    = [c for c in df.columns if c not in id_col_name_set]
    if not data_cols:
        raise ValueError("No data columns remain after removing ID columns.")
    orig_cols = list(df.columns)
    if id_col_names:
        prog(0.06, f"ID columns excluded: {id_col_names}")

    # ── Coerce object columns that are purely numeric ─────────────────────────
    data_df = df[data_cols].copy()
    df = None  # free memory
    for c in data_df.columns:
        if data_df[c].dtype == object:
            attempt = pd.to_numeric(data_df[c], errors='coerce')
            if attempt.notna().all():
                data_df[c] = attempt

    # ── Missing-value guard ───────────────────────────────────────────────────
    missing_counts = data_df.isnull().sum()
    missing_cols   = missing_counts[missing_counts > 0]
    if not missing_cols.empty:
        names      = list(missing_cols.index)
        n_miss_col = len(names)
        preview    = ", ".join(names[:4]) + (f" … (+{n_miss_col - 4} more)" if n_miss_col > 4 else "")
        n_miss_row = int(data_df.isnull().any(axis=1).sum())
        raise ValueError(
            f"Missing values in {n_miss_col} column(s): {preview}. "
            f"{n_miss_row:,} row(s) affected. "
            "Please impute or drop incomplete rows/columns before generating."
        )

    # ── Integer-coded categorical detection ───────────────────────────────────
    int_cat_col_indices: list[int] = []
    for idx, c in enumerate(data_df.columns):
        col = data_df[c]
        if not pd.api.types.is_numeric_dtype(col):
            continue
        vals     = col.values.astype(float)
        n_unique = len(np.unique(vals))
        if n_unique <= _MAX_INT_CAT_UNIQUE and np.allclose(vals, np.round(vals), atol=1e-9):
            int_cat_col_indices.append(idx)

    # ── Seed ──────────────────────────────────────────────────────────────────
    actual_seed = seed if seed is not None else int(
        np.random.SeedSequence().entropy & 0xFFFFFFFF
    )
    np.random.seed(actual_seed)

    # ── Build input array ─────────────────────────────────────────────────────
    string_cols_present = not all(
        pd.api.types.is_numeric_dtype(data_df[c]) for c in data_df.columns
    )
    if not string_cols_present and not int_cat_col_indices:
        Z = data_df.to_numpy(dtype=np.float64)
        sphere_cat_cols = []     # type: ignore  # pure continuous: skip encode-decode
    else:
        Z = data_df.to_numpy()                     # object array for mixed types
        sphere_cat_cols = int_cat_col_indices or None
    data_df = None  # free memory

    # ── k SPHERE rotations ────────────────────────────────────────────────────
    for i in range(k):
        prog(0.10 + 0.72 * (i / k), f"rotation {i + 1}/{k}")
        Z = sphere(Z, categorical_cols=sphere_cat_cols,
                   theta=theta, delta=delta, mix_prob=mix_prob)

    prog(0.85, "writing output")

    # ── Reconstruct output DataFrame with original column order ───────────────
    width    = max(3, len(str(n_rows)))
    synth_ids = [f"Synthetic{str(i + 1).zfill(width)}" for i in range(n_rows)]

    out_data: dict = {}
    data_idx = 0
    for c in orig_cols:
        if c in id_col_name_set:
            out_data[c] = synth_ids
        else:
            out_data[c] = Z[:, data_idx]
            data_idx += 1

    out_df = pd.DataFrame(out_data)[orig_cols]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(output_path, index=False)

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # ── Write metadata sidecar ────────────────────────────────────────────────
    # Saved as <output>.sphere.json so that `sphere certify` can auto-load the
    # generation parameters without requiring the user to re-supply them.
    import json as _json
    from datetime import datetime, timezone
    _meta = {
        "tool":         "sphere generate",
        "version":      "0.1.0",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "real":         input_path.name,
        "seed":         actual_seed,
        "k":            k,
        "theta":        theta,
        "delta":        delta,
        "mix_prob":     mix_prob,
        "rows":         len(out_df),
        "cols":         len(out_df.columns),
    }
    _meta_path = output_path.with_suffix(".sphere.json")
    _meta_path.write_text(_json.dumps(_meta, indent=2), encoding="utf-8")

    prog(1.0, "done")

    return {
        "rows":          len(out_df),
        "cols":          len(out_df.columns),
        "transform":     "SPHERE",
        "idColDetected": len(id_col_names) > 0,
        "idColName":     ", ".join(id_col_names) if id_col_names else None,
        "elapsedMs":     elapsed_ms,
        "seed":          actual_seed,
        "metaFile":      str(_meta_path),
    }
