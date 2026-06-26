import typing

from nodnod.agent import Agent
from nodnod.error import NodeError
from nodnod.interface import (
    ConcurrentEither,
    DataNode,
    Externals,
    NodeConstructor,
    ResultNode,
    SequentialEither,
    case,
    compose_one,
    create_agent_from_node,
    create_node_from_function,
    generic_node,
    inject_externals,
    inject_internals,
    polymorphic,
    scalar_node,
)
from nodnod.node import Injection, Node, Scalar
from nodnod.scope import Scope
from nodnod.value import Value

def backend_name() -> str: ...

class NodinaAgent(Agent):
    def __init__(
        self,
        traversed_nodes: typing.Iterable[type[Node]],
        final_nodes: typing.Iterable[type[Node]] | None = ...,
    ) -> None: ...
    @classmethod
    def build(cls, nodes: set[type[Node]]) -> typing.Self: ...
    def run(self, local_scope: Scope, mapped_scopes: dict[type[Node], Scope]) -> None: ...

class AsyncNodinaAgent(Agent):
    def __init__(
        self,
        traversed_nodes: typing.Iterable[type[Node]],
        final_nodes: typing.Iterable[type[Node]] | None = ...,
    ) -> None: ...
    @classmethod
    def build(cls, nodes: set[type[Node]]) -> typing.Self: ...
    def run(self, local_scope: Scope, mapped_scopes: dict[type[Node], Scope]) -> typing.Awaitable[None]: ...

__all__ = (
    "AsyncNodinaAgent",
    "ConcurrentEither",
    "DataNode",
    "Externals",
    "Injection",
    "Node",
    "NodeConstructor",
    "NodeError",
    "NodinaAgent",
    "ResultNode",
    "Scalar",
    "Scope",
    "SequentialEither",
    "Value",
    "backend_name",
    "case",
    "compose_one",
    "create_agent_from_node",
    "create_node_from_function",
    "generic_node",
    "inject_externals",
    "inject_internals",
    "polymorphic",
    "scalar_node",
)
