"""PyInstaller runtime hook for polars.

Two problems fixed here:

1. polars/_plr.py uses a self-replacement trick:
     sys.modules[__name__] = <rust_extension>
   In the PyInstaller frozen-import context the frozen importer may overwrite
   sys.modules['polars._plr'] back to the frozen Python stub after the
   replacement.  We pre-seed sys.modules['polars._plr'] with the real Rust
   extension, which bypasses the frozen importer's interference.

2. Several Series methods in polars are documented stubs (body is only a
   docstring; compiles to LOAD_CONST None / RETURN_VALUE).  In a normal Python
   install __register_startup_deps() (called from polars/__init__.py) replaces
   these stubs with real implementations.  In the frozen binary that patching
   sometimes does not fire correctly.  After polars is fully imported we test
   Series.unique(); if it still returns None we replace the stubs manually
   with correct Python-side implementations that delegate to self._s (the
   underlying Rust PySeries object).
"""
import sys
import os

_dbg = []

# ── Step 1: pre-seed polars._plr with the real Rust extension ────────────────
_plr_rust = None
try:
    import _polars_runtime_32._polars_runtime as _plr_rust
    sys.modules['polars._plr'] = _plr_rust
    _dbg.append(f"OK: pre-seeded polars._plr = {_plr_rust}")
    _dbg.append(f"    has SeriesInternal : {hasattr(_plr_rust, 'SeriesInternal')}")
    _dbg.append(f"    has __register_startup_deps: "
                f"{hasattr(_plr_rust, '__register_startup_deps')}")
except Exception as e:
    _dbg.append(f"ERROR in rt_32: {e}")
    try:
        import _polars_runtime_64._polars_runtime as _plr_rust
        sys.modules['polars._plr'] = _plr_rust
        _dbg.append(f"OK: rt_64 pre-seeded")
    except Exception as e2:
        _dbg.append(f"ERROR in rt_64: {e2}")

# ── Step 2: import polars now so __register_startup_deps() fires while we can
#           still intervene, then check / fix any remaining stubs ─────────────
try:
    import polars as pl
    _dbg.append(f"OK: polars imported (version {pl.__version__})")

    # Force-import all sub-modules that contain stubs so they are in
    # sys.modules before we test them.
    import polars.series.series   # noqa: F401
    import polars.dataframe.frame # noqa: F401

    # ── Test: is Series.unique still the docstring-only stub? ────────────────
    _test = pl.Series([1, 2, 2, 3])
    _unique_result = pl.Series.unique(_test)

    if _unique_result is None:
        _dbg.append("Series.unique is a stub — trying re-register …")

        # Try calling __register_startup_deps() again now that every polars
        # sub-module is loaded.  It claims to be one-shot but in practice is
        # idempotent (it will skip methods that are already patched on the
        # C side).
        if _plr_rust is not None and hasattr(_plr_rust, '__register_startup_deps'):
            try:
                _plr_rust.__register_startup_deps()
                _unique_result = pl.Series.unique(_test)
                _dbg.append(f"After re-register: unique = {_unique_result}")
            except Exception as _re_err:
                _dbg.append(f"re-register raised: {_re_err}")

    if pl.Series.unique(_test) is None:
        _dbg.append("Stubs remain after re-register — applying expression-API patches")

        # In polars 1.40+, PySeries no longer exposes .unique() / .drop_nulls()
        # at the Rust binding level.  The real implementation dispatches through
        # the lazy expression / DataFrame API.  We replicate that here by going
        # through self.to_frame() → select(expr) → to_series().
        #
        # Captured references so the closures don't need to re-import polars.
        _col  = pl.col
        _lit  = pl.lit  # noqa: F841 (held in case we need it later)

        # ── Series.unique ─────────────────────────────────────────────────
        # anonymeter.singling_out_evaluator:
        #   unique_values = {col: df[col].unique().to_list() for col in df.columns}
        def _series_unique(self, *, maintain_order: bool = False):
            """Return Series of unique values (expression-API fallback)."""
            return (
                self.to_frame()
                    .select(_col(self.name).unique(maintain_order=maintain_order))
                    .to_series()
            )

        pl.Series.unique = _series_unique

        # ── Series.drop_nulls ─────────────────────────────────────────────
        # anonymeter.singling_out_evaluator:
        #   non_null_count = df[col].drop_nulls().len()
        def _series_drop_nulls(self):
            """Drop null values (expression-API fallback)."""
            return (
                self.to_frame()
                    .select(_col(self.name).drop_nulls())
                    .to_series()
            )

        pl.Series.drop_nulls = _series_drop_nulls

        # ── Series.drop_nans ──────────────────────────────────────────────
        def _series_drop_nans(self):
            """Drop NaN values (expression-API fallback)."""
            return (
                self.to_frame()
                    .select(_col(self.name).drop_nans())
                    .to_series()
            )

        pl.Series.drop_nans = _series_drop_nans

        # ── Verify ────────────────────────────────────────────────────────
        _unique_result = pl.Series.unique(_test)
        _dbg.append(f"After manual patch: unique = {_unique_result}")
        _dbg.append(f"  drop_nulls test: {pl.Series([1.0, None, 3.0]).drop_nulls()}")
    else:
        _dbg.append(f"Series.unique is properly patched: {_unique_result}")

except Exception as e:
    _dbg.append(f"ERROR in polars stub-patch: {e}")
    import traceback
    _dbg.append(traceback.format_exc())

# ── Save diagnostics ──────────────────────────────────────────────────────────
try:
    with open('/tmp/sphere-polars-debug.txt', 'w') as f:
        f.write('\n'.join(_dbg) + '\n')
        f.write(f"sys.path[:5]: {sys.path[:5]}\n")
except Exception:
    pass
