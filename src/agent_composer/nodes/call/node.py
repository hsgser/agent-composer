"""CALL — the REF driver (`kind: call`), internal-only build target.

`CallNode` applies a callable ONCE (`'a flow -> 'b`). It is the REF half of the REF/MAP pair;
the MAP half is `nodes.map.MapNode` (`kind: map` + `over:`, `list['a] -> list['b]`) — the two are
distinct typed drivers. `CallNode` carries NO `over`/`parallel`.

`run` returns one `Enqueue` *description* — the engine's `_apply_enqueue` REF arm clones the baked
child + grows the live graph: `Enqueue(child, dict(inputs))` (the call-args RAW). The spliced child
START_ID owns omitted-input defaulting (its params carry default/required), so the driver no longer
pre-defaults. `child`/`child_inputs`/`child_asserts` are baked at load by
`compose.build` (`build_call_node`); `child_inputs` is read by compile validation
(`check_ref_map_types`) AND at runtime by the engine's boundary-assert temp pool (to mirror START_ID's
coerce+default view for the eager `${input.X}` check).
"""

from typing import Any, ClassVar, Optional

from agent_composer.nodes.base import Enqueue, Node, NodeKind


class CallNode(Node):
    """
    The REF driver (`kind: call`) — apply a callable flow once (`'a flow -> 'b`).

    The REF half of the REF/MAP pair (the MAP half is
    [`MapNode`][agent_composer.nodes.map.node.MapNode]). `run` returns one `Enqueue` description;
    the engine's `_apply_enqueue` clones the baked child and grows the live graph. The spliced
    child START owns omitted-input defaulting, so the driver passes call-args raw.

    Args:
        node_id (`str`):
            The node's unique id.
        flow_id (`str`):
            The id of the child flow to call.
        flow_version (`int`, *optional*, defaults to `None`):
            A pinned child flow version, if any.
        child (`Any`, *optional*, defaults to `None`):
            The baked child flow (stamped at load); a `None` child raises at run time.
        child_inputs (`list`, *optional*, defaults to `None`):
            The child's input decls, read by compile validation and the boundary-assert temp pool.
        child_asserts (`Any`, *optional*, defaults to `None`):
            The child's baked boundary asserts.
        child_source (`Any`, *optional*, defaults to `None`):
            The child flow's render-only `SourceFrame` (label + node-line maps). Carried
            for the CLI's nested-error traceback only; `run` ignores it entirely.
        title (`str`, *optional*, defaults to `None`):
            Display title.
    """

    kind = NodeKind.CALL
    is_spawner: ClassVar[bool] = True  # grows the graph: run() returns a Grow(Subgraph)

    def __init__(self, node_id: str, *, flow_id: str, flow_version: Optional[int] = None,
                 child: Any = None, child_inputs: Optional[list] = None, child_asserts: Any = None,
                 child_source: Any = None, title: Optional[str] = None) -> None:
        super().__init__(node_id, title=title)
        self.flow_id = flow_id
        self.flow_version = flow_version
        self.child = child
        self.child_inputs = child_inputs or []
        self.child_asserts = child_asserts
        # Render-only: the child's SourceFrame for the CLI error traceback. `run` never reads it.
        self.child_source = child_source

    def run(self, inputs: dict, **caps: Any):
        if self.child is None:
            raise RuntimeError(f"CALL node {self.id!r}: child flow {self.flow_id!r} not baked")
        # Pass the call-args RAW: the spliced child START_ID owns omitted-input defaulting now (its
        # params carry default/required), so the driver no longer pre-defaults.
        return Enqueue(self.child, dict(inputs))
