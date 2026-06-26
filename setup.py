from __future__ import annotations

import platform

from setuptools import Extension, setup

try:
    from Cython.Build import cythonize
except ImportError as exc:  # pragma: no cover - setup-time guard
    raise SystemExit("Cython is required to build nodina. Install it with `uv add --dev cython`.") from exc


def compile_args() -> list[str]:
    if platform.system() == "Windows":
        return ["/O2"]
    return ["-O3", "-fno-strict-aliasing", "-Wall", "-Wno-unused-function"]


def libraries() -> list[str]:
    # The work pool uses pthreads; not needed (or available) on Windows.
    return [] if platform.system() == "Windows" else ["pthread"]


extensions = [
    Extension(
        "nodina._agent",
        sources=["nodina/_agent.pyx", "nodina/core/nodina_pool.c"],
        include_dirs=["nodina", "nodina/core"],
        libraries=libraries(),
        extra_compile_args=compile_args(),
    ),
]

setup(
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            "language_level": "3str",
            "boundscheck": False,
            "wraparound": False,
        },
    ),
)
