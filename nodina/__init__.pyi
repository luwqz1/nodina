import typing

from nodnod.error import NodeError
from nodnod.agent import Agent
from nodnod.interface import (
    ConcurrentEither,
    DataNode,
    NodeConstructor,
    ResultNode,
    Externals,
    SequentialEither,
    inject_externals,
    inject_internals,
    case,
    compose_one,
    create_agent_from_node,
    create_node_from_function,
    generic_node,
    polymorphic,
    scalar_node,
)
from nodnod.node import Injection, Node, Scalar
from nodnod.scope import Scope
from nodnod.value import Value

def backend_name() -> str: ...

class AsyncNodinaAgent(Agent):
    def run(self, local_scope: Scope, mapped_scopes: dict[type[Node], Scope]) -> typing.Awaitable[None]: ...

class NodinaAgent(Agent):
    ...

__all__ = (
    "Agent",
    "AsyncNodinaAgent",
    "NodinaAgent",
    "ConcurrentEither",
    "NodeError",
    "DataNode",
    "Injection",
    "Scalar",
    "Value",
    "Scope",
    "Node",
    "NodeConstructor",
    "inject_externals",
    "inject_internals",
    "ResultNode",
    "Externals",
    "SequentialEither",
    "case",
    "compose_one",
    "create_agent_from_node",
    "create_node_from_function",
    "generic_node",
    "polymorphic",
    "scalar_node",
    "backend_name",
)
