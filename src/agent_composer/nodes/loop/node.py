"""LoopNode — the `while:`/`until:`/`times:` driver.

A driver node (like CallNode): `run` returns a `Grow(Subgraph)` splicing the baked body child
seeded with the carried record (turn 0). The turn-0 grow-vs-commit decision — a `while` whose
predicate is already false runs 0 body iterations and commits the seed unchanged — lives on the
node (it is a pure function of the seed). Iterations 1+ grow via the engine's `_loop_step`
predicate re-clone on the body-END route.
"""
from typing import Any, ClassVar, Optional

from agent_composer.compile.expand import loop_iteration_subgraph
from agent_composer.expr.expressions import evaluate_when_record
from agent_composer.nodes.base import Grow, Node, NodeKind, Output


class LoopNode(Node):
    """A higher-order `('a -> 'a) -> 'a -> 'a` driver over a carried record.

    Fields (baked at load by `compose.build.build_loop_node`):
      child          the compiled body subflow (the `'a -> 'a` callable); the Grow subgraph body.
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
        """Turn-0 growth as a self-describing `Grow` (unifying with iterations 1+, which grow via
        `_loop_step`'s predicate re-clone). The turn-0 grow-vs-commit decision is made here on the
        node (a pure function of the seed), never engine-side:

          while  — grow iteration #0 IFF the predicate holds on the seed; else 0 body runs — commit
                   the seed unchanged under the spawner id (`Output(seed, commit_as=self.id)`). The
                   carried record is defined at the seed (`inputs:`), so the predicate can read it.
          until  — DO-WHILE: always grow #0 (1+ runs); the predicate is a POST-check (`_loop_step`).
          times  — always grow #0 (a `times >= 1` count is guaranteed at build).
        """
        if self.child is None:
            raise RuntimeError(f"loop node {self.id!r} was not baked with a body child")
        seed = dict(inputs)
        if self.predicate_kind == "while" and not evaluate_when_record(self.predicate, seed):
            return Output(seed, commit_as=self.id)  # 0 body runs: commit the seed unchanged
        return Grow(loop_iteration_subgraph(self.child, self.id, seed, 0), seed=(seed, 0))

    def replay_grow(self, seed: Any):
        """Durable-replay inverse of one iteration's `Grow`: rebuild the LIVE iteration's body
        subgraph from the persisted `(record, index)` seed via the SAME `loop_iteration_subgraph`
        builder — no body re-run. Only the live iteration is ever recorded (superseded iterations
        are pruned from the ledger), so `seed` re-grows exactly that one iteration at its index. The
        seed round-trips through JSON as a 2-element list, so unpacking covers tuple and list."""
        record, iteration = seed
        return loop_iteration_subgraph(self.child, self.id, dict(record), iteration)
