# PyInstaller entry point — must be a top-level file so the package's
# relative imports resolve correctly inside the frozen binary.
import multiprocessing
multiprocessing.freeze_support()  # must be first for PyInstaller + spawn

import sys

# Print something immediately so the terminal isn't silent during .so loading.
# The \r lets the next real output line overwrite this message.
if getattr(sys, "frozen", False):
    sys.stdout.write("sphere  loading … (first run is slow — next runs are instant)\r")
    sys.stdout.flush()

import numpy as _np


def _nearest_neighbors_numpy(queries, candidates, cat_cols_index, n_neighbors):
    """Vectorized numpy drop-in for anonymeter's numba _nearest_neighbors.

    Identical Gower formula and NaN handling; 0.09 s per 200-attack call
    vs numba's 3 s first-call JIT.  No LLVM, no cold-start overhead.
    """
    q = queries.astype(_np.float64)
    c = candidates.astype(_np.float64)
    n_q, n_c = q.shape[0], c.shape[0]

    if cat_cols_index > 0:
        num = _np.abs(q[:, None, :cat_cols_index] - c[None, :, :cat_cols_index])
    else:
        num = _np.empty((n_q, n_c, 0), dtype=_np.float64)

    if cat_cols_index < q.shape[1]:
        qc = q[:, None, cat_cols_index:]
        cc = c[None, :, cat_cols_index:]
        cat = _np.where(_np.isnan(qc) & _np.isnan(cc), 1.0,
                        (qc != cc).astype(_np.float64))
    else:
        cat = _np.empty((n_q, n_c, 0), dtype=_np.float64)

    dists = _np.concatenate([num, cat], axis=2).mean(axis=2)
    idx   = _np.argsort(dists, axis=1)[:, :n_neighbors]
    return idx, dists[_np.arange(n_q)[:, None], idx]


if getattr(sys, "frozen", False):
    # ── Replace anonymeter's numba Gower kernels with vectorized numpy ────────
    # anonymeter does `from numba import jit` at module level; make jit a no-op
    # so the import succeeds even if numba is removed from the bundle.
    # _core.py applies the fast numpy _nearest_neighbors after import.
    try:
        import numba as _nb
        _nb.jit = lambda *a, **kw: (lambda fn: fn)
    except ImportError:
        # numba not bundled — install a minimal fake so anonymeter can import
        import types as _types
        _fake_nb = _types.ModuleType("numba")
        _fake_nb.jit = lambda *a, **kw: (lambda fn: fn)
        sys.modules["numba"] = _fake_nb

    # Store replacement for _core.py to pick up on first evaluator call
    sys._sphere_nn_patch = _nearest_neighbors_numpy


from sphere_cli.cli import main
main()
