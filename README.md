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

nodina is a small, pure-Python DAG scheduler for [nodnod](https://github.com/timoniq/nodnod)
graphs. It ships two agents:

- **`AsyncNodinaAgent`** resolves the dependency graph on the running asyncio
  event loop — one task per node, so independent `async` nodes (and any node that
  awaits real I/O) run concurrently.
- **`NodinaAgent`** resolves it synchronously, offloading independent nodes onto a
  shared `ThreadPoolExecutor` so *blocking* (GIL-releasing) nodes overlap. A lone
  ready node with nothing else in flight composes inline to skip a thread hand-off.

Both pick the first **successful** `SequentialEither` / `ConcurrentEither`
candidate in declared order; `ConcurrentEither` composes all of its candidates
concurrently, `SequentialEither` composes them lazily. Neither cancels losing
candidates.

### Performance, honestly

There is no native/C backend — `backend_name()` returns `"threadpool"`. The
thread offload only helps nodes that **release the GIL** (blocking I/O, `time.sleep`,
C extensions). **CPU-bound** `__compose__` work is GIL-serialized and will not run
in parallel — use `AsyncNodinaAgent` with truly async I/O for real concurrency.

The benchmark suite in [`benchmarks/`](benchmarks/) measures nodina against a
~150-line pure-Python reference scheduler across CPU-bound, blocking-I/O, wide,
deep, and either/result workloads. nodina is on par with or faster than that
reference on every case; see [`benchmarks/BASELINE.md`](benchmarks/BASELINE.md)
for the methodology and the pre-optimization numbers.

```bash
uv run python -m benchmarks.bench
```
