"""
Pure evaluation functions shared between the CLI and the PyInstaller sidecar.

No argparse, no IPC, no stdout/stderr assumptions — just numpy/pandas/scipy.
"""
from __future__ import annotations

import re
import sys
import warnings

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

warnings.filterwarnings("ignore")

# ── ID-column detection ───────────────────────────────────────────────────────

_ID_KEYWORDS = {
    'id', 'iid', 'fid', 'eid',
    'identifier', 'subject', 'patient', 'participant',
    'cohort', 'record', 'sample', 'case', 'uuid', 'barcode',
    'index', 'no', 'num', 'number', 'code', 'key', 'study',
}
_SEP_RE = re.compile(r'[_\-\s]+')


def _name_is_id_like(col_name: str) -> bool:
    """Return True only when the column name contains an ID keyword as a
    whole token (delimited by _ - or whitespace), not as a substring of a
    longer biological name like idua, ido1, bid, atraid, etc."""
    name = col_name.lower().strip()
    if name in _ID_KEYWORDS:
        return True
    tokens = _SEP_RE.split(name)
    return any(t in _ID_KEYWORDS for t in tokens)


def _detect_id_columns(df: pd.DataFrame) -> set:
    """Return set of column *indices* that look like row-ID columns."""
    id_cols: set = set()
    n = len(df)
    if n < 2:
        return id_cols
    for i, col in enumerate(df.columns):
        series = df.iloc[:, i]
        if series.isna().any():
            continue
        unique_count = series.nunique()
        all_unique = unique_count == n
        try:
            nums = pd.to_numeric(series, errors='raise')
            all_numeric = True
            all_integer = bool(np.all(nums.values == np.floor(nums.values)))
        except (ValueError, TypeError):
            all_numeric = False
            all_integer = False

        if all_integer and all_unique:
            sorted_vals = np.sort(nums.values)
            start = float(sorted_vals[0])
            if start in (0.0, 1.0) and np.all(
                sorted_vals == np.arange(start, start + n)
            ):
                id_cols.add(i)
                continue

        if all_unique and not all_numeric:
            id_cols.add(i)
            continue

        if all_unique and _name_is_id_like(col):
            id_cols.add(i)
            continue

        if _name_is_id_like(col):
            id_cols.add(i)
            continue

    return id_cols


# ── Encoding ──────────────────────────────────────────────────────────────────

def encode_pair(
    real: pd.DataFrame, synth: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Numeric → as-is (mean-imputed).  String → K ±1 indicator columns.

    Returns (real_enc, synth_enc, col_names).
    """
    real_blocks: list[np.ndarray] = []
    synth_blocks: list[np.ndarray] = []
    col_names: list[str] = []
    for c in real.columns:
        rcol = real[c]
        scol = synth[c] if c in synth.columns else pd.Series([np.nan] * len(synth))
        if pd.api.types.is_numeric_dtype(rcol):
            r_vals = pd.to_numeric(rcol, errors="coerce")
            s_vals = pd.to_numeric(scol, errors="coerce")
            mean_r = float(r_vals.mean()) if r_vals.notna().any() else 0.0
            mean_s = float(s_vals.mean()) if s_vals.notna().any() else mean_r
            real_blocks.append(r_vals.fillna(mean_r).to_numpy().reshape(-1, 1))
            synth_blocks.append(s_vals.fillna(mean_s).to_numpy().reshape(-1, 1))
            col_names.append(str(c))
        else:
            cats = sorted(rcol.dropna().astype(str).unique())
            for cat in cats:
                real_blocks.append(
                    np.where(rcol.astype(str) == cat, 1.0, -1.0).reshape(-1, 1)
                )
                synth_blocks.append(
                    np.where(scol.astype(str) == cat, 1.0, -1.0).reshape(-1, 1)
                )
                col_names.append(f"{c}__{cat}")
    return (
        np.hstack(real_blocks).astype(float),
        np.hstack(synth_blocks).astype(float),
        col_names,
    )


# ── Fidelity ──────────────────────────────────────────────────────────────────

def fidelity_metrics(real: pd.DataFrame, synth: pd.DataFrame) -> dict:
    """Paper-exact Δmean / Δvar / Δcor / KS on the ±1-encoded matrix."""
    re_, sy_, _ = encode_pair(real, synth)
    p = re_.shape[1]
    mean_r, mean_s = re_.mean(axis=0), sy_.mean(axis=0)
    var_r,  var_s  = re_.var(axis=0, ddof=1), sy_.var(axis=0, ddof=1)
    mean_ref = float(np.mean(np.abs(mean_r))) + 1e-8
    var_ref  = float(np.mean(var_r))           + 1e-8

    d_mean = float(np.max(np.abs(mean_r - mean_s))) / mean_ref * 100
    d_var  = float(np.max(np.abs(var_r  - var_s)))  / var_ref  * 100

    cor_r = np.corrcoef(re_.T)
    cor_s = np.corrcoef(sy_.T)
    cor_r = np.nan_to_num(cor_r, nan=0.0)
    cor_s = np.nan_to_num(cor_s, nan=0.0)
    np.fill_diagonal(cor_r, 1)
    np.fill_diagonal(cor_s, 1)
    cor_ref = float(np.linalg.norm(cor_r, "fro")) / p + 1e-8
    d_cor   = float(np.linalg.norm(cor_s - cor_r, "fro")) / p / cor_ref * 100

    ks = float(
        np.mean([ks_2samp(re_[:, j], sy_[:, j]).statistic for j in range(p)])
    )

    def clip(v, lo=0.0, hi=100.0):
        return max(lo, min(hi, v))

    mean_score = clip(100 - d_mean)
    var_score  = clip(100 - d_var)
    cor_score  = clip(100 - d_cor)
    ks_score   = (1 - ks) * 100
    return {
        "pctDeltaMean": d_mean,
        "pctDeltaVar":  d_var,
        "pctDeltaCor":  d_cor,
        "ksStatistic":  ks,
        "meanScore":    mean_score,
        "varScore":     var_score,
        "corScore":     cor_score,
        "ksScore":      ks_score,
        "composite":    (mean_score + var_score + cor_score + ks_score) / 4,
    }


# ── Privacy (Anonymeter) ──────────────────────────────────────────────────────

_anon_patched = False

def _patch_anonymeter_nn() -> None:
    """Replace anonymeter's numba _nearest_neighbors with a vectorized numpy
    version stored in sys._sphere_nn_patch by _main.py.  Applied once, on the
    first evaluator call, after anonymeter is fully imported."""
    global _anon_patched
    if _anon_patched:
        return
    _anon_patched = True
    try:
        import sys
        patch = getattr(sys, "_sphere_nn_patch", None)
        if patch is None:
            return
        import anonymeter.neighbors.mixed_types_kneighbors as _m
        _m._nearest_neighbors = patch
    except Exception:
        pass


def column_shuffle(df: pd.DataFrame, rng: np.random.RandomState) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        out[c] = rng.permutation(out[c].values)
    return out


def normalize(raw: float, r_real: float, r_shuf: float) -> float:
    """Two-anchor normalization — paper formula:
    score = clip( (r_real − r_synth) / (r_real − r_shuf), 0, 1 ) × 100."""
    denom = r_real - r_shuf
    if abs(denom) < 1e-9:
        return 100.0
    score = (r_real - raw) / denom * 100
    return max(0.0, min(100.0, float(score)))


def _run_so(ori, syn, n_attacks: int, n_atk_cap: int) -> float:
    from anonymeter.evaluators import SinglingOutEvaluator
    _patch_anonymeter_nn()
    n_atk = min(n_atk_cap, len(ori))
    ev = SinglingOutEvaluator(
        ori.head(n_atk).copy(), syn.head(n_atk).copy(),
        n_attacks=min(n_attacks, n_atk), n_cols=3,
    )
    ev.evaluate()
    return float(ev.risk().value)


def _run_lk(ori, syn, n_attacks, n_atk_cap, n_neighbors, n_aux_cols, rng) -> float:
    from anonymeter.evaluators import LinkabilityEvaluator
    _patch_anonymeter_nn()
    n_atk = min(n_atk_cap, len(ori))
    cols  = list(ori.columns)
    pool  = max(2, min(n_aux_cols, len(cols)))
    feats = list(rng.choice(cols, size=pool, replace=False))
    half  = max(1, len(feats) // 2)
    ev = LinkabilityEvaluator(
        ori.head(n_atk).copy(), syn.head(n_atk).copy(),
        n_attacks=min(n_attacks, n_atk),  # cap to dataset size
        n_neighbors=n_neighbors,
        aux_cols=(feats[:half], feats[half:]),
    )
    ev.evaluate()
    return float(ev.risk().value)


def _run_inf(ori, syn, n_attacks, n_atk_cap, rng, n_secrets=5) -> float:
    from anonymeter.evaluators import InferenceEvaluator
    _patch_anonymeter_nn()
    n_atk  = min(n_atk_cap, len(ori))
    cols   = list(ori.columns)
    if not cols:
        return 0.0
    chosen = rng.choice(cols, size=min(n_secrets, len(cols)), replace=False)
    risks  = []
    for secret in chosen:
        aux = [c for c in cols if c != secret]
        try:
            ev = InferenceEvaluator(
                ori.head(n_atk).copy(), syn.head(n_atk).copy(),
                aux_cols=aux, secret=str(secret),
                n_attacks=min(n_attacks, n_atk),  # cap to dataset size
            )
            ev.evaluate()
            risks.append(float(ev.risk().value))
        except Exception:
            pass
    return float(np.mean(risks)) if risks else 0.0
