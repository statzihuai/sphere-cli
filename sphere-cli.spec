# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the sphere CLI binary.
#
# Build pipeline (run via build.sh — do not call PyInstaller directly):
#   1. Cython compiles sphere_cli/_algo|_core|_generate|_evaluate|_certify.py
#      to native .so files (machine code — not decompilable).
#   2. The .py sources are temporarily hidden so PyInstaller bundles the .so
#      files, not bytecode.
#   3. PyInstaller produces dist/sphere-cli/sphere.
#   4. .py sources are restored for continued development.
#
# Dependencies bundled: numpy, pandas, scipy, pyarrow, anonymeter.
# NOT bundled (SPHERE AI stack not needed for CLI):
#   matplotlib, seaborn, statsmodels, sklearn (except anonymeter's subset).

import os, sys, glob
from PyInstaller.utils.hooks import collect_all

# ── Numba stub: satisfies anonymeter's hard `from numba import jit` without
#    bundling the real 180 MB numba + llvmlite.  The stub provides a no-op @jit
#    decorator; the actual Gower-distance kernel is never called because we
#    pre-encode all data to numeric before passing to anonymeter.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(SPEC)), "numba_stub"))

datas    = [("examples/nhanes_sample.csv", "examples")]
binaries = []

# ── Cython .so files for our proprietary modules ──────────────────────────────
# These are produced by setup_cython.py before PyInstaller runs.
for so in glob.glob("sphere_cli/*.so"):
    binaries.append((so, "sphere_cli"))

# ── Third-party dependencies ──────────────────────────────────────────────────
hiddenimports = ["scipy.optimize", "scipy.stats", "pyarrow"]
# numba is handled by the numba_stub inserted at sys.path[0] above;
# listing it here would make PyInstaller look for real numba (which is not
# installed) and fail.  The stub is auto-collected via the sys.path insertion.

# anonymeter needs a narrow slice of sklearn — only neighbors + linear_model
# (inference attack). Listing them explicitly avoids pulling in the 200+ MB
# full sklearn that SPHERE AI requires.
hiddenimports += [
    "sklearn.base", "sklearn.utils", "sklearn.utils._bunch",
    "sklearn.utils._tags", "sklearn.utils.validation",
    "sklearn.neighbors", "sklearn.neighbors._base",
    "sklearn.neighbors._ball_tree", "sklearn.neighbors._kd_tree",
    "sklearn.neighbors._dist_metrics",
    "sklearn.linear_model", "sklearn.linear_model._base",
    "sklearn.linear_model._logistic",
    "sklearn.preprocessing", "sklearn.preprocessing._encoders",
    "sklearn.pipeline", "sklearn.metrics", "sklearn.metrics._classification",
    "sklearn.svm._base",   # transitive dep of linear_model._logistic
]

for _pkg in ("anonymeter", "polars"):   # pip not needed in the CLI binary
    tmp = collect_all(_pkg)
    datas    += tmp[0]; binaries += tmp[1]; hiddenimports += tmp[2]

# polars runtime extension — explicit binary copy from the correct Python path
import sysconfig as _sc
_sp = _sc.get_path("platlib")
_plr_so = os.path.join(_sp, "_polars_runtime_32", "_polars_runtime.abi3.so")
if os.path.exists(_plr_so):
    binaries.append((_plr_so, "_polars_runtime_32"))

# ── Strip unsignable / test artefacts ─────────────────────────────────────────
_SEP = os.sep
_SKIP_EXTS = {".exe", ".dll", ".pdb", ".orc", ".avro"}

# Packages to scrub from collect_all output even if PyInstaller traces them.
# These leak in via transitive deps of anonymeter/polars/sklearn.
_BANNED_PKGS = {
    "boto3", "botocore", "s3transfer",          # AWS SDK
    "lxml", "docx", "python_docx",              # XML / doc tools
    "sqlalchemy", "alembic", "optuna",          # DB / HPO
    "aiohttp", "wfdb",                          # async HTTP / biosignal
    "PIL", "Pillow",                            # image library
    "Cython",                                   # build tool
    "pip", "setuptools", "wheel",               # packaging tools
    "matplotlib", "seaborn", "statsmodels",     # plotting
    "torch", "torchvision",                     # deep learning
    "IPython", "notebook", "jupyter", "pytest", # dev tools
    "sphinx",                                   # docs
}

def _exclude(src):
    s   = src.replace("/", _SEP)
    ext = os.path.splitext(s)[1].lower()
    if ext in _SKIP_EXTS:
        return True
    # Ban by package prefix
    parts = s.replace("/", _SEP).split(_SEP)
    for part in parts:
        pkg = part.split(".")[0]
        if pkg in _BANNED_PKGS:
            return True
    return any(
        pat in s for pat in
        [_SEP+"tests"+_SEP, _SEP+"test"+_SEP, _SEP+"testing"+_SEP]
    ) or s.endswith((_SEP+"tests", _SEP+"test"))

datas    = [(s, d) for s, d in datas    if not _exclude(s)]
binaries = [(s, d) for s, d in binaries if not _exclude(s)]

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ["_main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=["hooks"],
    runtime_hooks=["hooks/pyi_rth_polars.py"],
    excludes=[
        # SPHERE AI analysis stack — not needed for generate/evaluate/certify
        "matplotlib", "seaborn", "statsmodels",
        "torch", "torchvision",
        # llvmlite (the real JIT compiler) — not needed; stub numba is used instead
        "llvmlite",
        # Dev / notebook tools
        "IPython", "ipykernel", "notebook", "jupyter", "pytest",
        "tkinter", "PIL", "PIL.ImageTk", "Pillow", "sphinx",
        # Cython is a build tool — never needed at runtime
        "Cython",
        # AWS SDK — leaked in via sdv/synthcity transitive deps
        "boto3", "botocore", "s3transfer",
        # XML / doc tools — leaked in via sdv/sacrebleu transitive deps
        "lxml", "docx", "python_docx",
        # Database / hyperparameter-tuning tools — leaked in via synthcity/optuna
        "sqlalchemy", "alembic", "optuna",
        # Async HTTP — leaked in via wfdb
        "aiohttp", "wfdb",
        # pip not needed in the CLI binary
        "pip",
        # Heavy sklearn paths not used by anonymeter
        "sklearn.neural_network", "sklearn.gaussian_process",
        "sklearn.manifold", "sklearn.cluster", "sklearn.datasets",
        "sklearn.feature_extraction", "sklearn.ensemble",
        "sklearn.tree", "sklearn.decomposition",
    ],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="sphere",
    debug=False,
    strip=False,
    upx=True,
    console=True,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=True, upx_exclude=[],
    name="sphere-cli",
)
