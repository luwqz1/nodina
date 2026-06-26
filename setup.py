from __future__ import annotations

import os
import pathlib
import platform
import shlex

from setuptools import Extension, setup

try:
    from Cython.Build import cythonize
except ImportError as exc:  # pragma: no cover - setup-time guard
    raise SystemExit("Cython is required to build nodina. Install it with `uv add --dev cython`.") from exc

ROOT = pathlib.Path(__file__).parent.resolve()


def ensure_libuv() -> pathlib.Path:
    env_source_dir = os.environ.get("NODINA_LIBUV_SOURCE_DIR")

    if env_source_dir:
        source_dir = pathlib.Path(env_source_dir)

        if not (source_dir / "include" / "uv.h").exists():
            raise RuntimeError(f"NODINA_LIBUV_SOURCE_DIR does not look like libuv source: {source_dir}")
        return source_dir

    libuv = pathlib.Path("vendor") / "libuv"

    if (libuv / "include" / "uv.h").exists():
        return libuv

    raise LookupError("libuv vendor is not found")


LIBUV = ensure_libuv()


def split_env(name: str) -> list[str]:
    value = os.environ.get(name, "")
    return shlex.split(value) if value else []


def uv_sources(*names: str) -> list[str]:
    return [str(LIBUV / name) for name in names]


COMMON_UV_SOURCES = uv_sources(
    "src/fs-poll.c",
    "src/idna.c",
    "src/inet.c",
    "src/random.c",
    "src/strscpy.c",
    "src/strtok.c",
    "src/thread-common.c",
    "src/threadpool.c",
    "src/timer.c",
    "src/uv-common.c",
    "src/uv-data-getter-setters.c",
    "src/version.c",
)

UNIX_UV_SOURCES = uv_sources(
    "src/unix/async.c",
    "src/unix/core.c",
    "src/unix/dl.c",
    "src/unix/fs.c",
    "src/unix/getaddrinfo.c",
    "src/unix/getnameinfo.c",
    "src/unix/loop-watcher.c",
    "src/unix/loop.c",
    "src/unix/pipe.c",
    "src/unix/poll.c",
    "src/unix/process.c",
    "src/unix/random-devurandom.c",
    "src/unix/signal.c",
    "src/unix/stream.c",
    "src/unix/tcp.c",
    "src/unix/thread.c",
    "src/unix/tty.c",
    "src/unix/udp.c",
)

WIN_UV_SOURCES = uv_sources(
    "src/win/async.c",
    "src/win/core.c",
    "src/win/detect-wakeup.c",
    "src/win/dl.c",
    "src/win/error.c",
    "src/win/fs.c",
    "src/win/fs-event.c",
    "src/win/getaddrinfo.c",
    "src/win/getnameinfo.c",
    "src/win/handle.c",
    "src/win/loop-watcher.c",
    "src/win/pipe.c",
    "src/win/poll.c",
    "src/win/process.c",
    "src/win/process-stdio.c",
    "src/win/signal.c",
    "src/win/snprintf.c",
    "src/win/stream.c",
    "src/win/tcp.c",
    "src/win/thread.c",
    "src/win/tty.c",
    "src/win/udp.c",
    "src/win/util.c",
    "src/win/winapi.c",
    "src/win/winsock.c",
)


def compiler_args() -> list[str]:
    if platform.system() == "Windows":
        return ["/O2", *split_env("NODINA_CFLAGS")]

    return [
        "-O3",
        "-fno-strict-aliasing",
        "-Wno-unused-parameter",
        "-Wno-unreachable-code",
        *split_env("NODINA_CFLAGS"),
    ]


def platform_uv_config() -> tuple[list[str], list[tuple[str, str | None]], list[str], list[str]]:
    system = platform.system()
    sources = list(COMMON_UV_SOURCES)
    macros: list[tuple[str, str | None]] = [
        ("NODINA_HAVE_LIBUV", "1"),
        ("_FILE_OFFSET_BITS", "64"),
        ("_LARGEFILE_SOURCE", "1"),
    ]
    libraries: list[str] = []
    extra_link_args: list[str] = []

    if system == "Darwin":
        sources.extend(UNIX_UV_SOURCES)
        libraries.append("pthread")
        macros.extend(
            [
                ("_DARWIN_UNLIMITED_SELECT", "1"),
                ("_DARWIN_USE_64_BIT_INODE", "1"),
            ]
        )
        sources.extend(
            uv_sources(
                "src/unix/proctitle.c",
                "src/unix/bsd-ifaddrs.c",
                "src/unix/kqueue.c",
                "src/unix/random-getentropy.c",
                "src/unix/darwin-proctitle.c",
                "src/unix/darwin.c",
                "src/unix/fsevents.c",
            )
        )
        extra_link_args.extend(["-framework", "CoreServices", "-framework", "CoreFoundation"])
    elif system == "Linux":
        sources.extend(UNIX_UV_SOURCES)
        libraries.append("pthread")
        macros.extend(
            [
                ("_GNU_SOURCE", "1"),
                ("_POSIX_C_SOURCE", "200112"),
            ]
        )
        libraries.extend(["dl", "rt"])
        sources.extend(
            uv_sources(
                "src/unix/proctitle.c",
                "src/unix/linux.c",
                "src/unix/procfs-exepath.c",
                "src/unix/random-getrandom.c",
                "src/unix/random-sysctl-linux.c",
            )
        )
    elif system == "Windows":
        sources.extend(WIN_UV_SOURCES)
        macros = [
            ("NODINA_HAVE_LIBUV", "1"),
            ("WIN32_LEAN_AND_MEAN", "1"),
            ("_WIN32_WINNT", "0x0602"),
            ("_CRT_SECURE_NO_WARNINGS", "1"),
        ]
        libraries.extend(
            [
                "advapi32",
                "iphlpapi",
                "psapi",
                "shell32",
                "user32",
                "userenv",
                "ws2_32",
            ]
        )
    elif system.startswith("CYGWIN") or system == "CYGWIN_NT":
        sources.extend(UNIX_UV_SOURCES)
        libraries.append("pthread")
        macros.extend(
            [
                ("_GNU_SOURCE", "1"),
                ("_POSIX_C_SOURCE", "200112"),
            ]
        )
        sources.extend(
            uv_sources(
                "src/unix/no-proctitle.c",
                "src/unix/cygwin.c",
                "src/unix/random-getentropy.c",
            )
        )
    else:
        raise RuntimeError(f"nodina vendored libuv build does not support {system!r} yet")

    return sources, macros, libraries, extra_link_args


libuv_sources, macros, libraries, uv_link_args = platform_uv_config()

extensions = [
    Extension(
        "nodina._agent",
        sources=["nodina/_agent.pyx", "nodina/core/nodina_core.c", *libuv_sources],
        include_dirs=[
            ".",
            "nodina/core",
            str(LIBUV / "include"),
            str(LIBUV / "src"),
        ],
        libraries=libraries,
        define_macros=macros,
        extra_compile_args=compiler_args(),
        extra_link_args=[*uv_link_args, *split_env("NODINA_LDFLAGS")],
    )
]


setup(
    name="nodina",
    version="0.1.0",
    packages=["nodina"],
    package_dir={"nodina": "."},
    package_data={"nodina": ["py.typed"]},
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            "language_level": "3str",
            "boundscheck": False,
            "wraparound": False,
        },
    ),
)
