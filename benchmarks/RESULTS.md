# nodina before / after

Median ms per `run()`, same harness as [`BASELINE.md`](BASELINE.md). Lower is
better. "baseline" is the original libuv/Cython `main`; "optimized" is the
pure-Python result; "pure-Python ref" is the ~150-line reference scheduler (the
bar). Reproduce the optimized + reference columns with:

```bash
uv run python -m benchmarks.bench
```

| Case | Mode | nodina baseline (libuv) | nodina optimized | pure-Python ref | optimized / ref |
| --- | --- | ---: | ---: | ---: | ---: |
| CPU-bound compose (200 nodes x 20k spins) | sync | 334.5 | 340.2 | 337.8 | 1.01x |
| CPU-bound compose (200 nodes x 20k spins) | async | 337.8 | 351.7 | 345.4 | 1.02x |
| I/O-bound compose (40 nodes x 10ms sleep) | sync | 122.0 | 36.8 | 37.3 | 0.99x |
| I/O-bound compose (40 nodes x 10ms asyncio.sleep) | async | **BROKEN** | 11.9 | 11.6 | 1.03x |
| Wide DAG (500 independent trivial nodes) | sync | 6.3 | 5.0 | 3.8 | 1.32x |
| Deep DAG (chain of 400) | sync | 18.7 | 24.2 | 30.7 | 0.79x |
| Scope churn (tiny 2-node graph) | sync | 0.039 | 0.008 | 0.031 | 0.25x |
| SequentialEither (5 failing fallbacks) | sync | 0.146 | 0.027 | 0.103 | 0.26x |
| SequentialEither (5 failing fallbacks) | async | 0.408 | 0.393 | 0.390 | 1.01x |
| ResultNode path | sync | 0.036 | 0.005 | 0.017 | 0.32x |
| ResultNode path | async | 0.139 | 0.130 | 0.127 | 1.02x |

## Headlines

- **I/O sync: 122ms → 36.8ms** (3.3x faster) — libuv's threadpool offload lost to
  a plain `ThreadPoolExecutor`; now at parity with the reference.
- **I/O async: BROKEN → 11.9ms** — real asyncio futures now work (was
  `RuntimeError: await wasn't used with future`).
- **Scope churn / sync either / sync result: now 3-4x faster than the reference**
  thanks to the inline fast-path (a lone ready node skips the thread hand-off).
- **CPU-bound: unchanged tie** — GIL-serialized either way, as predicted.
- **Deep chain: 18.7 → 24.2ms** — the one spot the old libuv path was faster
  (sequential dispatch), but the optimized result still beats the pure-Python
  reference (24.2 < 30.7). That single niche win did not justify ~1.6k lines of
  C/Cython, a libuv submodule, a 3.3x I/O regression, and a broken async path.
- **Wide DAG: 5.0 vs 3.8ms** — the only case still behind the reference; 500
  independent nodes are all dispatched to the pool (per-submit overhead). Both
  are ~single-digit ms.
