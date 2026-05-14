"""
Minimal numba stub for the SPHERE CLI binary.

anonymeter's mixed_types_kneighbors imports `from numba import jit` at module
load time and decorates its Gower-distance kernel with @jit(nopython=True).
The CLI pre-encodes all data to numeric before calling anonymeter, so the
Gower-distance kernel is never actually invoked; we only need the decorator
to resolve at import time without error.
"""

__version__ = "0.0.0-stub"


def jit(*args, **kwargs):
    """No-op JIT decorator stub."""
    if args and callable(args[0]):
        return args[0]
    return lambda f: f


# Common aliases that anonymeter / other packages may reference
njit      = jit
vectorize = jit
guvectorize = jit
cfunc   = jit
stencil = jit


def prange(*args):
    return range(*args)


# Minimal type stubs so 'from numba import int64, float64' doesn't error
int32  = int
int64  = int
float32 = float
float64 = float
boolean = bool
