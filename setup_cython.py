"""
Compile the proprietary sphere_cli modules to native C extensions.

Usage:
    python3 setup_cython.py build_ext --inplace

The .so files produced replace the .py source at PyInstaller bundle time
so the final binary contains compiled machine code, not Python source.
"""
from setuptools import setup, Extension
from Cython.Build import cythonize
import numpy as np

MODULES = [
    "sphere_cli._algo",
    "sphere_cli._core",
    "sphere_cli._generate",
    "sphere_cli._evaluate",
    "sphere_cli._certify",
    "sphere_cli.cli",       # protect CLI structure (command flags, function calls)
]

extensions = cythonize(
    [Extension(m, [m.replace(".", "/") + ".py"]) for m in MODULES],
    compiler_directives={
        "language_level": "3",
        "boundscheck":    False,
        "wraparound":     False,
    },
    quiet=True,
)

setup(
    name="sphere_cli_native",
    ext_modules=extensions,
    include_dirs=[np.get_include()],
)
