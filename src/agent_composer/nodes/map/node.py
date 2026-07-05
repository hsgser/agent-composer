"""MAP — the `List.map` driver (`kind: map` + `over:`), internal-only build target.

`MapNode` is the mapped-call node, distinct from REF's `CallNode`. The two are different typed
drivers: a `CallNode` applies a callable ONCE (`'a flow -> 'b`); a `MapNode` maps it over a
list (`list['a] -> list['b]`). The discriminator is the KIND (`NodeKind.MAP`) — `MapNode` carries
NO `over` attribute and no `${...}` source on the node. The `over` SOURCE binding rides
`flow.wiring[id]["over"]` (mirroring WaitNode's timed `until`), pre-resolved into `inputs["over"]`
by the engine's `eval_node` before `run`.

`run` returns a `list[Enqueue]` *description* — one `Enqueue(child, dict(bind_item(el)))` per element
(the engine's `_apply_enqueue` MAP arm clones the baked child per element + fans the child ENDs into
one `EndNode.list_`); an empty `over` -> `[]`. The per-element call-args go RAW: the spliced child
START_ID owns omitted-input defaulting (its params carry default/required), so the driver no longer
pre-defaults. `child`/`child_inputs`/`child_asserts` are baked at load by
`compose.build` (`build_call_node`); `child_inputs` is read by compile validation
(`check_ref_map_types`) AND at runtime by the engine's per-element boundary-assert temp pool (to
mirror START_ID's coerce+default view). `parallel` is inert (concurrency is the engine's
`num_workers`); it is carried for the over case.
"""

from typing import Any, Callable, ClassVar, Optional

from agent_composer.nodes.base import Enqueue, Node, NodeKind


class MapNode(Node):
    """
    The `List.map` driver (`kind: map` + `over:`) — map a callable flow over a list (`list['a] -> list['b]`).

    The MAP half of the REF/MAP pair (the REF half is
    [`CallNode`][agent_composer.nodes.call.node.CallNode]). The `over` source is pre-resolved into
    `inputs["over"]` by the engine bind seam; `run` returns one `Enqueue` per element. The engine
    clones the baked child per element and fans the child ENDs into one list END. The spliced child
    START owns omitted-input defaulting, so per-element call-args go raw.

    Args:
        node_id (`str`):
            The node's unique id.
        flow_id (`str`):
            The id of the child flow to map.
        parallel (`bool`, *optional*, defaults to `False`):
            Inert — concurrency is the engine's `num_workers`; carried for the `over` case.
        flow_version (`int`, *optional*, defaults to `None`):
            A pinned child flow version, if any.
        child (`Any`, *optional*, defaults to `None`):
            The baked child flow (stamped at load); a `None` child raises at run time.
        child_inputs (`list`, *optional*, defaults to `None`):
            The child's input decls, read by compile validation and the per-element temp pool.
        child_asserts (`Any`, *optional*, defaults to `None`):
            The child's baked boundary asserts.
        child_source (`Any`, *optional*, defaults to `None`):
            The child flow's render-only `SourceFrame` (label + node-line maps). Carried
            for the CLI's nested-error traceback only; `run` ignores it entirely.
        title (`str`, *optional*, defaults to `None`):
            Display title.
    """

    kind = NodeKind.MAP
    is_spawner: ClassVar[bool] = True  # grows the graph: run() returns a Grow(Subgraph)

    def __init__(self, node_id: str, *, flow_id: str, parallel: bool = False,
                 flow_version: Optional[int] = None, child: Any = None,
                 child_inputs: Optional[list] = None, child_asserts: Any = None,
                 child_source: Any = None, title: Optional[str] = None) -> None:
        super().__init__(node_id, title=title)
        self.flow_id = flow_id
        self.flow_version = flow_version
        self.parallel = parallel    # inert (the engine's num_workers)
        self.child = child
        self.child_inputs = child_inputs or []
        self.child_asserts = child_asserts
        # Render-only: the child's SourceFrame for the CLI error traceback. `run` never reads it.
        self.child_source = child_source

    def run(self, inputs: dict, *, bind_item: Optional[Callable[[Any], dict]] = None):
        if self.child is None:
            raise RuntimeError(f"MAP node {self.id!r}: child flow {self.flow_id!r} not baked")
        # Per-element call-args RAW (no driver pre-default): the spliced child START_ID fills omitted
        # inputs from its params' declared defaults.
        return [
            Enqueue(self.child, dict(bind_item(el)))
            for el in inputs["over"]
        ]
