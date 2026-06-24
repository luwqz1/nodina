from __future__ import annotations

import os
import platform
import pathlib
import shlex
import tarfile
import urllib.request

from setuptools import Extension, setup

try:
    from Cython.Build import cythonize  # type: ignore
except ImportError as exc:  # pragma: no cover - setup-time guard
    raise SystemExit("Cython is required to build nodina. Install it with `uv add --dev cython`.") from exc


ROOT = pathlib.Path(__file__).parent.resolve()
LIBUV_COMMIT = "1cfa32ff59c076ffb6ed735bbc8c18361558661f"
LIBUV_URL = f"https://github.com/libuv/libuv/archive/{LIBUV_COMMIT}.tar.gz"
LIBUV_CACHE = pathlib.Path(".cache") / f"libuv-{LIBUV_COMMIT}"


def ensure_libuv() -> pathlib.Path:
    env_source_dir = os.environ.get("NODINA_LIBUV_SOURCE_DIR")
    if env_source_dir:
        source_dir = pathlib.Path(env_source_dir)
        if not (source_dir / "include" / "uv.h").exists():
            raise RuntimeError(f"NODINA_LIBUV_SOURCE_DIR does not look like libuv source: {source_dir}")
        return source_dir

    if (LIBUV_CACHE / "include" / "uv.h").exists():
        return LIBUV_CACHE

    LIBUV_CACHE.parent.mkdir(parents=True, exist_ok=True)
    archive_path = LIBUV_CACHE.parent / f"libuv-{LIBUV_COMMIT}.tar.gz"
    if not archive_path.exists():
        urllib.request.urlretrieve(LIBUV_URL, archive_path)

    extract_dir = LIBUV_CACHE.parent / f"libuv-{LIBUV_COMMIT}.tmp"
    if extract_dir.exists():
        import shutil

        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)

    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            target = (extract_dir / member.name).resolve()
            if not target.is_relative_to(extract_dir.resolve()):
                raise RuntimeError(f"unsafe path in libuv archive: {member.name}")
        archive.extractall(extract_dir)

    extracted_roots = [path for path in extract_dir.iterdir() if path.is_dir()]
    if len(extracted_roots) != 1:
        raise RuntimeError(f"unexpected libuv archive layout in {archive_path}")

    extracted_roots[0].rename(LIBUV_CACHE)
    extract_dir.rmdir()
    return LIBUV_CACHE


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


def platform_uv_config() -> tuple[list[str], list[tuple[str, str | None]], list[str], list[str]]:
    system = platform.system()
    sources = list(COMMON_UV_SOURCES)
    macros: list[tuple[str, str | None]] = [
        ("NODINA_HAVE_LIBUV", "1"),
        ("_FILE_OFFSET_BITS", "64"),
        ("_LARGEFILE_SOURCE", "1"),
    ]
    libraries: list[str] = ["pthread"]
    extra_link_args: list[str] = []

    if system == "Darwin":
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
        extra_compile_args=[
            "-O3",
            "-fno-strict-aliasing",
            "-Wno-unused-parameter",
            *split_env("NODINA_CFLAGS"),
        ],
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
