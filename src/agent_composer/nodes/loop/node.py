"""LoopNode — the `while:`/`until:`/`times:` driver (slice 1: `while:`).

A driver node (like CallNode): `run` returns an `Enqueue` of the baked body child seeded with the
carried record; the engine's `_apply_enqueue`/`_loop_step` own the predicate + re-clone loop-back.
The node itself is pure and stateless — it never decides whether to iterate.
"""
from typing import Any, Optional

from agent_composer.nodes.base import Enqueue, Node, NodeKind


class LoopNode(Node):
    """A higher-order `('a -> 'a) -> 'a -> 'a` driver over a carried record.

    Fields (baked at load by `compose.build.build_loop_node`):
      child          the compiled body subflow (the `'a -> 'a` callable); the Enqueue target.
      child_inputs   the body's declared input decls (subset of the carried record).
      predicate_kind "while" (slice 1). "until"/"times" reserved for later slices.
      predicate      the `while:` boolean source, e.g. "not ${exited}" (bare = carried-record scope).
      max_iters      the runaway guard (required for while/until).
    """

    kind = NodeKind.LOOP

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
        self.max_iters = max_iters

    def run(self, inputs: dict[str, Any], **caps: Any):
        """Return an Enqueue of the body child seeded with the carried record (turn 0)."""
        if self.child is None:
            raise RuntimeError(f"loop node {self.id!r} was not baked with a body child")
        return Enqueue(self.child, dict(inputs))
