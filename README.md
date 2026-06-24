<div align="center">
  <a href="https://github.com/luwqz1/nodina">
    <img src="https://raw.githubusercontent.com/luwqz1/nodina/refs/heads/main/assets/logo.svg" alt="Nodina Logo" width="160" height="160">
  </a>

  ⚡️ <i>Ultra fast agents for <a href="https://github.com/timoniq/nodnod">nodnod</i></a> \
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
        return "Hello"


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
        print(local_scope[Hello], ",", local_scope[World])


asyncio.run(main())
```
