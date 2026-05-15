# PyInstaller entry point — must be a top-level file so the package's
# relative imports resolve correctly inside the frozen binary.
import multiprocessing
multiprocessing.freeze_support()  # must be first for PyInstaller + spawn

import os
import sys

# ── Numba JIT cache fix ───────────────────────────────────────────────────────
# anonymeter.neighbors.mixed_types_kneighbors decorates its Gower-distance
# kernels with @jit(nopython=True, nogil=True) — without cache=True.  This
# forces numba to recompile from LLVM IR on EVERY process start (~3 s overhead
# on first call per run).
#
# Fix: intercept numba.jit before anonymeter is imported and inject cache=True
# into every decorator call.  Combined with a user-writable NUMBA_CACHE_DIR,
# the kernels are compiled once and reloaded in ~0.04 s on subsequent runs.
#
# This block must run before any anonymeter import (anonymeter is lazily
# imported inside _run_lk / _run_so / _run_inf, so the patch is in effect
# by the time those functions are first called).
if getattr(sys, "frozen", False):
    try:
        _cache_dir = os.path.join(
            os.path.expanduser("~"), ".cache", "sphere-cli", "numba"
        )
        os.makedirs(_cache_dir, exist_ok=True)
        os.environ["NUMBA_CACHE_DIR"] = _cache_dir

        import numba as _nb
        _orig_jit = _nb.jit

        def _cached_jit(*args, **kwargs):
            kwargs.setdefault("cache", True)
            return _orig_jit(*args, **kwargs)

        _nb.jit = _cached_jit
    except Exception:
        pass  # numba unavailable or already patched — no-op

from sphere_cli.cli import main
main()
