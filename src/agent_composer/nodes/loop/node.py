"""LoopNode — the `while:`/`until:`/`times:` driver.

A driver node (like CallNode): `run` returns an `Enqueue` of the baked body child seeded with the
carried record; the engine's `_apply_enqueue`/`_loop_step` own the predicate + re-clone loop-back.
The node itself is pure and stateless — it never decides whether to iterate.
"""
from typing import Any, ClassVar, Optional

from agent_composer.compile.expand import loop_iteration_subgraph
from agent_composer.nodes.base import Enqueue, Node, NodeKind


class LoopNode(Node):
    """A higher-order `('a -> 'a) -> 'a -> 'a` driver over a carried record.

    Fields (baked at load by `compose.build.build_loop_node`):
      child          the compiled body subflow (the `'a -> 'a` callable); the Enqueue target.
      child_inputs   the body's declared input decls (subset of the carried record).
      predicate_kind one of "while" | "until" | "times".
      predicate      the boolean source for `while`/`until`, e.g. "not ${exited}" (bare =
                     carried-record scope); None for `times` (no predicate).
      times          the fixed run count when `predicate_kind == "times"`; None otherwise.
      max_iters      the runaway guard for `while`/`until`; for `times`, equals the count N.
    """

    kind = NodeKind.LOOP
    is_spawner: ClassVar[bool] = True  # grows the graph: each iteration splices a Grow(Subgraph)

    def __init__(
        self,
        node_id: str,
        *,
        flow_id: str,
        flow_version: Optional[str] = None,
        child: Any = None,
        child_inputs: Any = None,
        child_asserts: Any = None,
        child_source: Any = None,
        predicate_kind: str = "while",
        predicate: Optional[str] = None,
        times: Optional[int] = None,
        max_iters: Optional[int] = None,
        title: Optional[str] = None,
    ) -> None:
        super().__init__(node_id, title=title)
        self.flow_id = flow_id
        self.flow_version = flow_version
        self.child = child
        self.child_inputs = child_inputs
        self.child_asserts = child_asserts
        self.child_source = child_source
        self.predicate_kind = predicate_kind
        self.predicate = predicate
        self.times = times
        self.max_iters = max_iters

    def run(self, inputs: dict[str, Any], **caps: Any):
        """Return an Enqueue of the body child seeded with the carried record (turn 0)."""
        if self.child is None:
            raise RuntimeError(f"loop node {self.id!r} was not baked with a body child")
        return Enqueue(self.child, dict(inputs))

    def replay_grow(self, seed: Any):
        """Durable-replay inverse of one iteration's `Grow`: rebuild the LIVE iteration's body
        subgraph from the persisted `(record, index)` seed via the SAME `loop_iteration_subgraph`
        builder — no body re-run. Only the live iteration is ever recorded (superseded iterations
        are pruned from the ledger), so `seed` re-grows exactly that one iteration at its index. The
        seed round-trips through JSON as a 2-element list, so unpacking covers tuple and list."""
        record, iteration = seed
        return loop_iteration_subgraph(self.child, self.id, dict(record), iteration)
