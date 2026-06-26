# nodina before / after

Median ms per `run()`, same harness as [`BASELINE.md`](BASELINE.md). Lower is
better. Columns: **baseline** = original libuv/Cython `main`; **Cython pool** =
the current Cython extension over a native pthread pool (no libuv); **ref** = the
~150-line pure-Python reference scheduler (the bar). Reproduce with:

```bash
uv run python -m benchmarks.bench
```

| Case | Mode | baseline (libuv) | Cython pool | pure-Python ref | Cython / ref |
| --- | --- | ---: | ---: | ---: | ---: |
| CPU-bound compose (200 nodes x 20k spins) | sync | 334.5 | 340 | 336 | 1.01x |
| CPU-bound compose (200 nodes x 20k spins) | async | 337.8 | 340 | 351 | 0.97x |
| I/O-bound compose (40 nodes x 10ms sleep) | sync | 122.0 | 37.4 | 37.9 | 0.99x |
| I/O-bound compose (40 nodes x 10ms asyncio.sleep) | async | **BROKEN** | 11.9 | 12.1 | 0.98x |
| Wide DAG (500 independent trivial nodes) | sync | 6.3 | 4.8 | 3.3 | 1.45x |
| Deep DAG (chain of 400) | sync | 18.7 | 9.1 | 30.8 | 0.30x |
| Scope churn (tiny 2-node graph) | sync | 0.039 | 0.007 | 0.029 | 0.24x |
| SequentialEither (5 failing fallbacks) | sync | 0.146 | 0.028 | 0.103 | 0.27x |
| SequentialEither (5 failing fallbacks) | async | 0.408 | 0.392 | 0.395 | 0.99x |
| ResultNode path | sync | 0.036 | 0.005 | 0.017 | 0.29x |
| ResultNode path | async | 0.139 | 0.133 | 0.131 | 1.02x |

## Headlines

- **Deep chain: 9.1ms** — 3.4x faster than the reference (31ms) and 2x faster
  than the old libuv path (18.7ms). The Cython inline fast-path composes a chain
  with zero thread hand-offs.
- **Scope churn / sync either / sync result: 3-4x faster than the reference.**
- **I/O sync + async: parity** with the reference (and the old libuv I/O was 3.3x
  slower / async was broken).
- **CPU-bound: tie on a GIL build** — serialized by the interpreter (see below).
- **Wide DAG: 4.8 vs 3.3ms** — the one case behind the reference. 500 *zero-work*
  nodes are all dispatched to the pool; that is pure dispatch overhead with
  nothing to parallelize. Realistic wide *blocking* work is the I/O-sync row
  (parity).

## Removing the GIL (free-threaded build)

The native pool holds the GIL only inside each `__compose__`; its queueing and
waiting are GIL-free. On a free-threaded interpreter (`python3.14t`, PEP 703)
that means CPU-bound nodes run **truly in parallel**. Same demo, two builds
(`benchmarks/freethreading_demo.py`, 8 CPU nodes, 11-core machine):

| build | GIL | 1 node | 8 nodes | speedup vs serial |
| --- | --- | ---: | ---: | ---: |
| `python3.14`  | enabled  | 25.5 ms | 206 ms | **1.0x** (serialized) |
| `python3.14t` | disabled | 25.0 ms |  40 ms | **5.1x** (parallel) |

The extension declares `freethreading_compatible`, so importing it does not
re-enable the GIL. The full test suite passes under both builds.
