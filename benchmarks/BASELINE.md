# nodina baseline (Phase 0)

This is the bar every later phase is measured against. Numbers are the **median
of N runs** of `agent.run()` (the agent is built once; each run uses a fresh
`Scope`, so no scope-cache hits are measured). Reproduce the raw table with:

```bash
uv run python -m benchmarks.bench           # prints the table
uv run python -m benchmarks.bench --out /tmp/raw.md
```

(This file is curated; `--out` writes a bare table elsewhere so it does not
clobber the analysis below.)

- Platform: macOS-26.6-arm64-arm-64bit-Mach-O (Apple Silicon)
- Python: 3.14.2
- nodina backend: `libuv`
- nodina commit: pre-optimization `main`

## Results: native nodina vs. ~150-line pure-Python reference

`reference.py` implements the same scheduling contract in plain Python:
`ReferenceSyncAgent` uses a shared `ThreadPoolExecutor`; `ReferenceAsyncAgent`
uses one `asyncio` task per node. Both reuse only nodnod's *node-model*
primitives (`Value`, `__initialize__`, generator helpers), not its schedulers.

| Case | Mode | nodina (ms) | pure-Python ref (ms) | nodina / ref |
| --- | --- | ---: | ---: | ---: |
| CPU-bound compose (200 nodes x 20k spins) | sync | 334.498 | 331.816 | 1.01x |
| CPU-bound compose (200 nodes x 20k spins) | async | 337.808 | 341.871 | 0.99x |
| I/O-bound compose (40 nodes x 10ms sleep) | sync | 121.951 | 37.115 | **3.29x** |
| I/O-bound compose (40 nodes x 10ms asyncio.sleep) | async | **BROKEN** | 11.812 | n/a |
| Wide DAG (500 independent trivial nodes) | sync | 6.265 | 3.307 | 1.89x |
| Deep DAG (chain of 400) | sync | 18.698 | 30.926 | 0.60x |
| Scope churn (tiny 2-node graph) | sync | 0.039 | 0.030 | 1.30x |
| SequentialEither (5 failing fallbacks) | sync | 0.146 | 0.101 | 1.45x |
| SequentialEither (5 failing fallbacks) | async | 0.408 | 0.392 | 1.04x |
| ResultNode path | sync | 0.036 | 0.017 | 2.15x |
| ResultNode path | async | 0.139 | 0.126 | 1.11x |

`nodina / ref` > 1.0 means nodina is **slower** than the pure-Python reference.

## Key findings

1. **CPU-bound: dead heat (1.01x / 0.99x).** The sync libuv work callback is
   `with gil`, so CPU nodes never run in parallel — libuv buys nothing over pure
   Python, exactly as predicted.

2. **I/O-bound sync: nodina is 3.3x SLOWER (122ms vs 37ms).** This is the *only*
   workload the libuv threadpool was supposed to win, and it loses badly. Causes:
   a fresh `uv_loop_init` on every `run()`, plus the scheduler **busy-polls**
   `work->done` while calling `uv_run(UV_RUN_ONCE)` instead of driving completion
   off the after-work callback. A shared `ThreadPoolExecutor` crushes it.

3. **I/O-bound async: BROKEN.** `AsyncNodinaAgent` cannot drive a *real* asyncio
   future. A node that does `await asyncio.sleep(0.01)` (or awaits any real
   future) raises `RuntimeError: await wasn't used with future` from
   `_drive_awaitable`, which double-steps the future and breaks asyncio's
   `_asyncio_future_blocking` handshake. The test suite only ever uses
   `asyncio.sleep(0)` (a no-op that yields `None`), which hides the bug.

4. **Everything else is slower too** (wide DAG 1.89x, churn 1.30x, either 1.45x,
   result 2.15x), except the **deep chain (0.60x)**, where nodina wins. Both
   schedulers are O(depth²) on a chain; that niche shape is the only place the
   native path beats Python.

## Implications for later phases

- **Phase 2 is effectively decided by findings 1–3:** the libuv offload is slower
  than `ThreadPoolExecutor` on blocking work, ties on CPU work, and the async
  future driver is broken. The native vendoring does not earn its place.
- The async future bug (finding 3) is a real correctness gap; the fix lands with
  the Phase 3 rewrite and gets a regression test using a real (`> 0`)
  `asyncio.sleep`.
