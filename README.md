<div align="center">
  <a href="https://github.com/luwqz1/nodina"><img src="https://raw.githubusercontent.com/luwqz1/nodina/refs/heads/main/assets/logo.svg" alt="Nodina Logo" width="250" height="200"></a>

  <i>asyncio / threadpool DAG scheduler agents for <a href="https://github.com/timoniq/nodnod">nodnod</i></a> \
  <i>We make a <a href="https://github.com/timoniq/nodnod">nodnod</a> family</i> 🧑‍🧑‍🧒‍🧒
</div>


## Getting started
```python
import asyncio

from nodina import AsyncNodinaAgent, Scope, scalar_node


@scalar_node
class Hello:
    @classmethod
    async def __compose__(cls) -> str:
        return "Hello,"


@scalar_node
class World:
    @classmethod
    async def __compose__(cls) -> str:
        return "World!"


async def main() -> None:
    agent = AsyncNodinaAgent.build({Hello, World})
    mapped_scopes = {}

    async with Scope(detail="local") as local_scope:
        await agent.run(local_scope, mapped_scopes)
        print(local_scope[Hello], local_scope[World])


asyncio.run(main())
```

## How it works

nodina is a small DAG scheduler for [nodnod](https://github.com/timoniq/nodnod)
graphs, built as a Cython extension over a tiny native (libuv-free) pthread work
pool. It ships two agents:

- **`AsyncNodinaAgent`** resolves the dependency graph on the running asyncio
  event loop — one task per node, so independent `async` nodes (and any node that
  awaits real I/O) run concurrently.
- **`NodinaAgent`** resolves it synchronously, dispatching independent nodes onto
  a shared pthread pool (`nodina/core/nodina_pool.c`). The pool's queueing and
  waiting run **without the GIL** — the GIL is held only inside each `__compose__`
  call. A lone ready node with nothing else in flight composes inline to skip a
  thread hand-off.

Both pick the first **successful** `SequentialEither` / `ConcurrentEither`
candidate in declared order; `ConcurrentEither` composes all of its candidates
concurrently, `SequentialEither` composes them lazily. Neither cancels losing
candidates.

### Performance & the GIL, honestly

`backend_name()` returns `"cython"`. The thread pool overlaps nodes that
**release the GIL** (blocking I/O, `time.sleep`, C extensions). On a normal
CPython build, **CPU-bound** `__compose__` work still serializes on the GIL —
that is a property of the interpreter, not of nodina.

On a **free-threaded build** (`python3.14t`, PEP 703) the GIL is gone and the
pool runs CPU-bound nodes **truly in parallel**. The extension declares
`freethreading_compatible`, so importing it does not re-enable the GIL:

```text
# 8 independent CPU nodes, 8-node graph, 11-core machine
python3.14   (GIL):  8 nodes = 206 ms   speedup vs serial 1.0x
python3.14t  (no GIL): 8 nodes =  40 ms   speedup vs serial 5.1x
```

```bash
uv run python -m benchmarks.bench               # vs a pure-Python reference
python3.14t -m benchmarks.freethreading_demo    # CPU parallelism with the GIL off
```

The benchmark suite in [`benchmarks/`](benchmarks/) measures nodina against a
~150-line pure-Python reference scheduler. nodina ties or beats it on every case
(deep chains and small graphs 3-4x faster); see
[`benchmarks/RESULTS.md`](benchmarks/RESULTS.md) and
[`benchmarks/BASELINE.md`](benchmarks/BASELINE.md).
