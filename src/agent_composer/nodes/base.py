"""The Node base contract — the single most portable idea from graphon.

A node is a **pure function of its bound input record**: it implements
`run(inputs, **caps) -> NodeResult` and returns ONE of the closed sum
`Output | Pause | Enqueue` (the node-result sum type) — **or**, for a streaming
kind, a generator that yields `StreamChunk` and then *returns* a `NodeResult`.
The node never receives the pool: the engine's `runtime.eval_node` seam binds its
inputs (the read boundary) and hands it a record. The one effectful kind that still
needs a narrow capability is a mapped `call` (`bind_item`, a keyword-only arg); every other kind
takes only `inputs`. Failure is **not** a variant — a failing node `raise`s and the
engine boundary turns it into `NodeFailed`. A returned `Pause` becomes one
`PauseRequested`; the engine delivers the answer as the parked leaf's `Output`
(deliver-as-Output — the node never re-runs). A streaming kind is a generator that
yields `StreamChunk` and *returns* its `NodeResult` (drained by `_drain_node_generator`).

Invariant: a node never writes the pool. It *describes* its one output value as
`Output(value)`; the engine performs the write under `node_id`. Keeps nodes pure.
"""

from abc import ABC, abstractmethod
from collections.abc import Generator
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Optional, Union

from agent_composer.expr import ExpressionError
from agent_composer.expr.expressions import evaluate_when_record
from agent_composer.nodes.binding import ParamDecl
from agent_composer.state.segments import Shape


class NodeKind(str, Enum):
    """Closed vocabulary. Dispatch is an explicit `match`, never a registry."""

    AGENT = "agent"
    CODE = "code"
    MODEL = "model"
    TOOL = "tool"
    CASE = "case"
    HUMAN_INPUT = "human_input"  # suspend for a person
    WAIT = "wait"  # internal-only: suspend for an external poke (WATCH uses it)
    LOOP = "loop"  # internal-only: iterate a callable to a fixpoint (`('a -> 'a) -> 'a -> 'a`)
    START = "start"  # internal-only: loader-synthesized input boundary (parameter binding)
    END = "end"      # internal-only: loader-synthesized return boundary (record + list modes)
    CALL = "call"    # internal-only: consult another flow once (REF — `kind: call`)
    MAP = "map"      # internal-only: map a callable over a list (`kind: map` + `over:`)


# --- the node's return type is a closed sum ----------------------------------------
# A pure node returns ONE of these (or a generator that yields StreamChunk and returns one).


@dataclass(frozen=True)
class Output:
    """A produced value. The engine writes `value` into the pool under the node id."""

    value: Any = None


@dataclass(frozen=True)
class Route:
    """A routing-only outcome (a terminal): carries only the chosen case handle — no value,
    no post-asserts. Returned by a router (CASE); the engine takes the handle's out-edge and
    skip-floods the siblings."""

    handle: str


@dataclass(frozen=True)
class Pause:
    """A leaf wait (HUMAN_INPUT / WAIT / an agent mid-loop control-pause). `reason` is a
    `suspension.pause.PauseReason`. The engine emits `PauseRequested` and suspends."""

    reason: Any


@dataclass(frozen=True)
class Enqueue:
    """Grow the live graph — a description the engine splices into the running flow
    (graph expansion). Produced by the REF/MAP drivers and by an agent's mid-loop
    control pause; the engine's `_apply_enqueue` interprets it."""

    target: Any
    inputs: Any


# The closed sum a pure `run(inputs)` returns.
NodeResult = Union[Output, Route, Pause, Enqueue]


class Node(ABC):
    """
    The base contract every node kind implements: a pure function of its input record.

    A node implements [`run`][agent_composer.nodes.base.Node.run] and returns one of the
    closed sum `Output | Pause | Enqueue` (or, for a streaming kind, a generator that yields
    `StreamChunk` and returns a `NodeResult`). The node never receives the pool — the engine's
    `eval_node` seam binds its inputs and hands it a record — and never writes the pool; it
    *describes* its one output as `Output(value)` and the engine performs the write under the
    node id. Failure is a `raise`, not a variant.

    Attributes:
        kind (`NodeKind`):
            The closed-vocabulary tag the engine dispatches on. Set per subclass.
        id (`str`):
            The node's unique id within its (possibly namespaced) flow.
        title (`str`, *optional*):
            A human-friendly display title, or `None`.
        output_shape (`Shape`, *optional*):
            The declared output Shape (one value); `None` leaves the write unenforced.
        params (`list[ParamDecl]`, *optional*):
            The node-side declared params (no source — the flow owns the wiring); `None`
            for a node that declares no inputs.
        pre_asserts (`list[str]`):
            Node-local `asserts:` checked against the bound record before `run`.
        post_asserts (`list[str]`):
            Node-local `asserts:` (reading `${output}`) checked after `run`.
    """

    kind: ClassVar[NodeKind]
    # Declares "I may grow the graph" (a spawner returns Enqueue/Grow). Overridden True
    # by the spawner kinds; unused this phase (wired in a later phase).
    is_spawner: ClassVar[bool] = False

    def __init__(
        self,
        node_id: str,
        *,
        title: Optional[str] = None,
        output_shape: Optional[Shape] = None,
    ) -> None:
        self.id = node_id
        self.title = title
        # The node's declared output Shape (one value). Threaded by the compiler;
        # None for fakes / nodes that declare none (then the write is unenforced).
        self.output_shape: Optional[Shape] = output_shape
        # The node-side signature (the node/flow split): declared params with NO source — the flow owns
        # the wiring in `CompiledFlow.wiring[node_id][param]`. Stamped by the compiler
        # (`build_*`/case desugar); the engine's `eval_node` binds via `params` + `flow.wiring`.
        # `None` for a fake / directly-constructed node that declares no inputs (== no params).
        self.params: Optional[list[ParamDecl]] = None
        # Node-local `asserts:` (a per-node contract), classified + stamped by the loader and
        # enforced by the engine's `eval_node` seam: PRE checked against the bound input record
        # before `run`; POST (reads `${output}`) after `run` against `{**inputs, output}`.
        # Empty for most nodes.
        self.pre_asserts: list[str] = []
        self.post_asserts: list[str] = []

    @abstractmethod
    def run(self, inputs: dict[str, Any], **caps: Any) -> "NodeResult":
        """Execute the node as a pure function of its bound input record.

        Returns a `NodeResult` (`Output | Pause | Enqueue`), or — for a streaming kind —
        a generator that yields `StreamChunk` and *returns* a `NodeResult`. The one effectful
        cap left is a mapped `call`'s `bind_item` (keyword-only); every other kind takes only `inputs`. A
        failure is a `raise`, not a variant — the engine boundary turns it into `NodeFailed`."""

    def on_failure(self, exc: Exception, inputs: dict[str, Any], **caps: Any) -> "NodeResult":
        """Recovery seam: called by the engine wrapper when run() raises. Default: re-raise."""
        raise exc

    @staticmethod
    def _assert_holds(expr: str, record: dict) -> bool:
        """Evaluate a node assert against `record`; a raising assert (ordered/arith over a
        non-scalar / None `${output}`) is treated as NOT holding (-> a clean NodeFailed)."""
        try:
            return bool(evaluate_when_record(expr, record))
        except ExpressionError:
            return False

    def _drain_node_generator(self, gen: Generator) -> Generator[Any, Any, "NodeResult"]:
        """Forward a streaming node's yielded `StreamChunk`s and capture its RETURNED
        `NodeResult`. A pause is now a *returned* `Pause` (not a yielded event), so a
        generator only ever yields `StreamChunk`; the dispatch happens in `eval_node`."""
        try:
            event = next(gen)
            while True:
                yield event  # StreamChunk
                event = next(gen)
        except StopIteration as stop:
            result = stop.value
            if not isinstance(result, (Output, Route, Pause, Enqueue)):
                raise RuntimeError(f"node {self.id!r} generator did not return a NodeResult")
            return result
