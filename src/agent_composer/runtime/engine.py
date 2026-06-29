"""FlowEngine — one engine, one knob (`num_workers`).

A producer/consumer engine with two drive modes behind a single `num_workers`
knob, sharing one set of state-mutation helpers (`_on_success`/`_on_pause`/
`_advance`/`_branch`/`_skip_edge`):

- `num_workers=0` (default) — the single-threaded inline drain: the caller's
  thread pops the ready queue, runs each node's generator inline, applies the
  consequences, and forwards events. This is the deterministic path (exact event
  ordering, no `event_q` hop); golden-locked in F0.
- `num_workers>=1` — a fixed worker pool with a single-writer dispatcher: N
  daemon workers pull ids off `ready_q`, run `eval_node`, and push events onto
  `event_q`; the dispatcher (`run()`'s generator) drains `event_q`, forwards each
  event, and applies the *same* mutation helpers. The dispatcher is the sole
  writer of graph/edge/pool state.

Both modes capture all of graphon's *correctness* (3-state edge join, exact-once
fan-in, outputs-before-successors, branch skip-flood). `resume()` drives under the
same `num_workers` mode the engine carries (serial or pooled), via the shared
`_drive_to_terminal`.

Load-bearing orderings (do not reorder):
- A node's outputs are written to the pool **before** any successor is scheduled.
- A successor is scheduled only when `disposition` (via `is_node_ready`) says it is
  ready — an edge-class-aware join: a diamond fires exactly once, a control edge
  hard-gates (veto) and a required data group co-skips; a `dead` head is
  skip-flooded by `_skip_edge`.
"""

import queue
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

from agent_composer.events import (
    RunAborted,
    RunFailed,
    RunPaused,
    RunResumed,
    RunStarted,
    RunSucceeded,
    SourceSpan,
    NodeExpanded,
    NodeFailed,
    NodeSucceeded,
    PauseRequested,
)
from agent_composer.compile.expand import clone_child, clone_continuation_pair, map_callsite, ns
from agent_composer.compile.model import END_ID, START_ID, CompiledFlow, Edge, NodeState
from agent_composer.nodes.end import EndNode
from agent_composer.nodes.base import NodeKind
from agent_composer.runtime.eval_node import _SPAWNER_KINDS, eval_node
from agent_composer.runtime.state_manager import StateManager
from agent_composer.state import SegmentError
from agent_composer.state.pool import TypedVariablePool

DEFAULT_HANDLE = "default"

# Runtime expansion bounds. Enforced in `_apply_enqueue` (the dispatcher mints
# every node); both raises funnel to RunFailed via the boundary wrap (clean status=="failed").
MAX_TOTAL_NODES = 10_000   # the load-bearing runtime-size bound (nested MAP breadth multiplies)
MAX_REF_DEPTH = 5          # defense-in-depth depth bound (the static call graph is acyclic+finite)

_POLL = 0.02  # queue poll timeout (s); keeps shutdown responsive
_JOIN_TIMEOUT = 2.0


@dataclass(frozen=True)
class _MapElementSlot:
    """Parent-pointer wrapper for a MAP element: nested descriptors go to
    `parent.children_per_element[index]` instead of a flat `children` list.

    Lives in engine memory only — never reaches the checkpoint blob. The
    `Expansion` discriminated union has no `_MapElementSlot` variant on purpose.
    """
    parent: Any  # a MapExpansion; typed Any to avoid a top-level import cycle
    index: int


def _append_child(parent, child) -> None:
    """Append `child` to the right slot on `parent`:
    - `CallExpansion` -> `parent.children`
    - `AgentExpansion` -> `parent.segments[-1].children` (last segment's children list)
    - `_MapElementSlot` -> `parent.parent.children_per_element[parent.index]`
    """
    from agent_composer.suspension.expansions import AgentExpansion  # lazy
    if isinstance(parent, _MapElementSlot):
        parent.parent.children_per_element[parent.index].append(child)
    elif isinstance(parent, AgentExpansion):
        parent.segments[-1].children.append(child)
    else:  # CallExpansion
        parent.children.append(child)


class _Aborted(Exception):
    pass


class NodeExecutionError(RuntimeError):
    """A node emitted NodeFailed and no error strategy recovered it (abort).

    `locator` is an optional `SourceSpan` pinning the failure to a YAML line — set at
    the typed write boundary (a value that fails its node's declared `output:` Shape)
    so the CLI boxes the `output:` field rather than printing a plain message.
    """

    def __init__(
        self,
        node_id: str,
        error: str,
        error_type: str = "",
        locator: Optional[SourceSpan] = None,
    ) -> None:
        super().__init__(f"node {node_id!r} failed: {error}")
        self.node_id = node_id
        self.error = error
        self.error_type = error_type
        self.locator = locator


class FlowEngine:
    """
    The flow execution engine: drive a `CompiledFlow` to a terminal, emitting events.

    A producer/consumer engine with two drive modes behind one `num_workers` knob: the
    default single-threaded inline drain (deterministic event order) and a worker pool
    with a single-writer dispatcher. Both capture the same correctness (3-state edge
    join, exact-once fan-in, outputs-before-successors, branch skip-flood). Suspended
    runs can be captured with [`snapshot`][agent_composer.FlowEngine.snapshot] and
    rebuilt in a fresh process with [`restore`][agent_composer.FlowEngine.restore].

    Most callers should use [`run_flow`][agent_composer.run_flow] rather than driving the
    engine directly.

    Args:
        flow (`CompiledFlow`):
            The compiled graph to execute. The engine mutates it in place when a spawner
            node (CALL/MAP) grows the graph at run time.
        pool (`TypedVariablePool`, *optional*, defaults to `None`):
            The variable pool to read/write. A fresh empty pool is created when `None`.
        num_workers (`int`, *optional*, defaults to `0`):
            `0` runs the deterministic inline drain on the caller's thread; `>=1` spawns
            that many daemon workers behind a single-writer dispatcher. Clamped to `>=0`.
        run_inputs (`dict`, *optional*, defaults to `None`):
            The flow's run arguments, seeded into the START node once at init. `None` for
            direct-engine tests that hand-seed the START store entry.
        boundary_asserts (`list`, *optional*, defaults to `None`):
            The flow's boundary asserts, fired pool-scoped right after the START seed and
            before any body node runs.
    """

    def __init__(
        self,
        flow: CompiledFlow,
        pool: Optional[TypedVariablePool] = None,
        *,
        num_workers: int = 0,
        run_inputs: Optional[dict] = None,
        boundary_asserts: Optional[list] = None,
    ) -> None:
        self.flow = flow
        self.pool = pool if pool is not None else TypedVariablePool()
        self.sm = StateManager(flow)
        self.num_workers = max(0, num_workers)
        # The top-level START_ID is seeded at run init by invoking StartNode.run(run_inputs)
        # ONCE — committed to store[START_ID] WITHOUT scheduling START_ID / emitting NodeSucceeded
        # ("no node ran" holds for the boundary-assert path). `boundary_asserts` (the flow's
        # ${inputs}/${system} asserts) fire pool-scoped right after the seed, before any body node.
        # Both default to None for direct-FlowEngine tests that hand-seed store[START_ID].
        self.run_inputs = run_inputs
        self.boundary_asserts = list(boundary_asserts or [])
        self.ready: deque[str] = deque()  # serial path; also the serial _ready_snapshot arm
        if self.num_workers >= 1:
            self.ready_q: "queue.Queue[str]" = queue.Queue()
            self.event_q: "queue.Queue" = queue.Queue()
            self._stop = threading.Event()
        self.paused: list[tuple[str, Any]] = []  # (node_id, PauseReason)
        self.deferred: list[str] = []  # became ready while suspending
        # Runtime graph-expansion bookkeeping: `alias` maps a cloned filler id
        # (REF child END_ID / MAP END_ID-list / agent resume continuation) -> its spawner id, so the
        # filler's _commit substitutes the spawner's value + fires its out-edges; `depth`
        # carries each cloned spawner-eligible id's expansion depth for MAX_REF_DEPTH.
        self.alias: dict[str, str] = {}
        self.depth: dict[str, int] = {}
        self.expansions: list = []  # descriptor ledger (top-level Expansions in
            # dispatcher-fire order; nested expansions ride under their parent
            # descriptor's `children` / `children_per_element`).
        self._spawner_expansion: dict[str, Any] = {}  # cloned spawner_id -> the
            # Expansion (or `_MapElementSlot`) that contains it. The SOLE lookup
            # for "which descriptor does this spawner belong to" — no scan over
            # `self.expansions` ever happens. For multi-pause AGENTs, every
            # cloned resume_id AND the original spawner_id are stamped to point at the
            # SAME AgentExpansion.
        self._cancel = False

    def request_abort(self) -> None:
        """Cooperative cancel: checked between nodes."""
        self._cancel = True

    # --- lifecycle ---------------------------------------------------------- #

    def run(self):
        """Drive the run, yielding run events. One terminal event at the end.

        `num_workers==0` is the inline drain (byte-identical event order, the
        deterministic path); `num_workers>=1` spawns the worker pool + dispatcher.
        """
        yield RunStarted()
        # Init: seed store[START_ID] (StartNode.run ONCE, not scheduled), fire the
        # top-level boundary asserts pool-scoped, then advance START_ID's out-edges. A failure here
        # (e08 shape / boundary assert) yields RunFailed before any body node ("no node ran").
        failure = self._seed_start_and_advance()
        if failure is not None:
            yield failure
            return
        yield from self._drive_to_terminal()

    def _drive_to_terminal(self):
        """Drive an ALREADY-SEEDED ready frontier to a terminal, yielding every event
        incl. the terminal one. Picks serial vs pooled on `self.num_workers` — the SOLE
        drive block, shared by run() (after START seed) and resume() (after the resume
        seed). Both modes capture the same correctness; pooled reorders events but the
        result is worker-count-independent."""
        if self.num_workers == 0:
            try:
                yield from self._drain()
            except _Aborted:
                yield RunAborted(); return
            except NodeExecutionError as exc:
                yield RunFailed(error=exc.error, error_type=exc.error_type, locator=exc.locator); return
            if self.paused:
                yield RunPaused(reasons=[reason for _, reason in self.paused]); return
            yield self._terminal_event(); return

        # Pooled: N daemon workers + single-writer dispatcher. Clear _stop first — a prior
        # run()/resume() pooled pass set it in its finally; a fresh pass must re-enable workers.
        self._stop.clear()
        workers = [
            threading.Thread(target=self._worker, name=f"ac-worker-{i}", daemon=True)
            for i in range(self.num_workers)
        ]
        for w in workers:
            w.start()
        try:
            yield from self._dispatch()
            terminal = (
                RunPaused(reasons=[r for _, r in self.paused])
                if self.paused
                else self._terminal_event()  # co-skipped terminal -> RunFailed (shared helper)
            )
        except _Aborted:
            terminal = RunAborted()
        except NodeExecutionError as exc:
            terminal = RunFailed(error=exc.error, error_type=exc.error_type, locator=exc.locator)
        finally:
            self._stop.set()
            for w in workers:
                w.join(timeout=_JOIN_TIMEOUT)
        yield terminal

    def _seed_start_and_advance(self):
        """The pinned top-level START_ID seeding. Invoke StartNode.run(run_inputs) ONCE
        (coerce + e08 + defaults), commit store[START_ID] directly — WITHOUT enqueuing START_ID and
        WITHOUT a NodeSucceeded — then fire the flow's boundary asserts pool-scoped (reading the
        just-committed store[START_ID]), then mark START_ID done + advance its out-edges. Returns a
        RunFailed on an e08 shape failure or a false boundary assert (fail-fast before any body
        node; "no node ran" holds), else None. Direct-FlowEngine tests that hand-seed
        store[START_ID] pass no `run_inputs` — START_ID is then taken from the pre-seeded store."""
        from agent_composer.expr import first_failing_assert

        start_id = self.flow.start_id
        if start_id in self.flow.nodes:
            if self.run_inputs is not None:
                # seed via StartNode.run, funneling an e08 SegmentError -> RunFailed.
                try:
                    out = self.flow.nodes[start_id].run(dict(self.run_inputs))
                except SegmentError as exc:
                    # e08 forwards the StartNode's `input_decl` locator (the failing input's
                    # declaration line) so the CLI boxes it precisely.
                    return RunFailed(error=str(exc), error_type="SegmentError",
                                     locator=getattr(exc, "locator", None))
                self.pool.set(start_id, out.value)
            # boundary asserts: pool-scoped, reading store[START_ID]; byte-stable "assert failed".
            bad = first_failing_assert(self.boundary_asserts, self.pool)
            if bad is not None:
                return RunFailed(error=f"assert failed: {bad}", error_type="AssertFailed",
                                 locator=SourceSpan(node=None, kind="assert", key=bad))
            # mark START_ID done + fire its out-edges (input-reader data edges + body-root edges).
            # START_ID is NOT enqueued/run; no NodeSucceeded is emitted for it.
            self.sm.mark_node(start_id, NodeState.TAKEN)
            for nid in self._advance(start_id):
                self._schedule(nid)
        return None

    def _terminal_event(self):
        """The terminal event for a completed (non-paused/aborted/failed) run: the
        run result IS the END_ID node's committed value. END_ID is an ordinary tail — when it RAN
        (`END_ID in pool.store`) the run SUCCEEDED with `store[END_ID]`; when it was skip-flooded
        (a required `outputs:` group co-skipped -> END_ID's disposition `dead` -> never committed)
        the run FAILED with the byte-stable `terminal output {name!r} skipped`, the name recovered
        from the SKIPPED required group's `input_group` (the output name keyed onto each
        producer->END_ID edge). Shared by serial run()/resume() + the parallel run() so the
        co-skip path is identical on both engines."""
        if END_ID in self.pool.store:
            return RunSucceeded(output=self.pool.get(END_ID))
        return RunFailed(error=f"terminal output {self._coskipped_output_name()!r} skipped",
                         error_type="TerminalSkipped")

    def _coskipped_output_name(self) -> Optional[str]:
        """The declared-output name of END_ID's dead required group: the `input_group` of a
        required (`optional=False`) producer->END_ID data edge whose every edge in the group SKIPPED.
        Recovers the exact name for the byte-stable terminal message."""
        st = self.sm.edge_state
        groups: dict[Optional[str], list] = {}
        for e in self.flow.incoming(END_ID):
            if e.source_handle is None and not e.ordering:
                groups.setdefault(e.input_group, []).append(e)
        for group, edges_g in groups.items():
            if edges_g[0].optional:
                continue
            if all(st.get(e.id) == NodeState.SKIPPED for e in edges_g):
                return group
        return None

    # --- durable suspend / resume ------------------------------------------ #

    def snapshot(self):
        """Capture the suspended run as a serializable RunCheckpoint.

        Captures pool + ready + node_state + edge_state + paused_nodes +
        deferred_nodes + pause_reasons + num_workers + expansions (the ledger of
        descriptor entries for runtime-grown REF/CALL/MAP/AGENT subgraphs, which
        `restore()` replays top-down to re-grow the cloned subgraphs).

        Call after `run()` yields `RunPaused`. The checkpoint can be persisted
        (dumps/loads) and resumed in a FRESH process via `restore` + `resume`,
        including a run paused mid-expansion (a grown CALL/MAP/AGENT subgraph).
        """
        from agent_composer.suspension.checkpoint import RunCheckpoint

        # Capture by VALUE — a point-in-time snapshot the holder can serialize later. The
        # pool and the Expansion descriptors are mutable pydantic models that the live engine
        # keeps advancing (e.g. a multi-pause AGENT appends segments to its AgentExpansion);
        # a shallow `self.pool` / `list(self.expansions)` would let later live progress
        # retro-mutate an already-taken checkpoint. node_state/edge_state are dict() copies of
        # immutable NodeState enum values, so they need no deep copy.
        return RunCheckpoint(
            pool=self.pool.model_copy(deep=True),
            ready=self._ready_snapshot(),
            node_state=dict(self.sm.node_state),
            edge_state=dict(self.sm.edge_state),
            paused_nodes=[node_id for node_id, _ in self.paused],
            deferred_nodes=list(self.deferred),
            pause_reasons=[reason for _, reason in self.paused],
            num_workers=self.num_workers,
            expansions=[d.model_copy(deep=True) for d in self.expansions],
        )

    @classmethod
    def restore(cls, flow: CompiledFlow, checkpoint, *, num_workers: Optional[int] = None) -> "FlowEngine":
        """Rebuild a resumable engine on `flow` from a (deserialized) checkpoint.

        `num_workers=None` (default) rebuilds the engine at the checkpoint's recorded
        drive mode; pass an int to OVERRIDE it — a run checkpointed serial can resume
        pooled and vice-versa (workers are pure executors; correctness is
        worker-count-independent).

        Order: build the engine on the pool (at the resolved drive mode) → replay the
        expansions descriptor tree
        (re-grows flow + sm, re-derives alias/depth/_spawner_expansion) → OVERWRITE
        node_state/edge_state from the checkpoint (now covers the re-grown nodes too) →
        re-seed paused/deferred/ready. Order matters: replay must register the cloned nodes
        BEFORE the node_state overwrite restores their TAKEN/SKIPPED/EXPANDED states.

        `flow` MUST be CLEAN — a fresh compile with NO namespaced ids. `add_subgraph`
        is non-idempotent (it `extend`s edges + `append`s adjacency), so replaying over an
        already-grown flow would duplicate edges/adjacency and double-run a side-effecting
        node. restore() mutates `flow` in place, so it must not be re-invoked on the same
        object. A hand-built flow passed here must carry the SAME baked `.child` on its
        CALL/MAP spawners as a loader compile (the replay needs it)."""
        # Defense-in-depth version gate: a checkpoint may reach restore() without
        # passing through RunCheckpoint.loads() (which also gates). The current blob
        # version is a breaking migration over older blobs.
        from agent_composer.suspension.checkpoint import CHECKPOINT_VERSION
        if getattr(checkpoint, "version", None) != CHECKPOINT_VERSION:
            raise ValueError(
                f"incompatible checkpoint version {getattr(checkpoint, 'version', None)!r}; "
                f"this build reads {CHECKPOINT_VERSION!r} (adds the num_workers drive-mode field)"
            )
        # Clean-flow guard (BEFORE replay): a cloned id carries `/` or `#`, so a flow that
        # already has any is a re-grown one — replaying onto it duplicates the overlay.
        bad = [n for n in flow.nodes if "/" in n or "#" in n]
        if bad:
            raise ValueError(
                f"restore() requires a clean flow (fresh compile); found namespaced/cloned "
                f"node ids {bad[:5]!r} — pass a freshly recompiled flow, not a re-grown one"
            )
        # Consume the checkpoint BY VALUE (symmetric with snapshot()'s write-side deep-copy):
        # a held checkpoint stays a point-in-time value even on the READ side, so a host that
        # reuses a retained snapshot()/loads() object across resume_flow() retries is not
        # retro-mutated (resume dirties the pool; an AGENT 2nd segment appends in place).
        workers = checkpoint.num_workers if num_workers is None else num_workers
        engine = cls(flow, pool=checkpoint.pool.model_copy(deep=True), num_workers=max(0, workers))
        # Replay re-grows the live topology + sm overlay + alias/depth/_spawner_expansion and
        # rebuilds self.expansions from OUR OWN descriptor copies. schedule=False.
        engine._replay_expansions([d.model_copy(deep=True) for d in checkpoint.expansions])
        # Overwrite node/edge state from the checkpoint — now covers the re-grown nodes too.
        engine.sm.node_state = dict(checkpoint.node_state)
        engine.sm.edge_state = dict(checkpoint.edge_state)
        # Re-seed the suspend frontier: self.paused (zip nodes+reasons), self.deferred, and
        # self.ready as a PLAIN frontier (set directly, NOT via _schedule — _schedule would
        # route through the paused-check into deferred). resume()'s `seed = deferred + ready`
        # then _enqueue(seed) consumes it.
        engine.paused = list(zip(checkpoint.paused_nodes, checkpoint.pause_reasons))
        engine.deferred = list(checkpoint.deferred_nodes)
        engine.ready = deque(checkpoint.ready)
        return engine

    def resume(self, commands=None):
        """Continue a paused run by DELIVERING each command's answer as the parked leaf's
        Output. ORDERING INVARIANT: apply commands WHILE self.paused is still set, so
        a delivered node's newly-ready successors are held in self.deferred (via _schedule);
        only THEN clear paused/deferred and seed = deferred + ready. This makes a
        multi-command resume drop no successor and double-run none — a fan-in fires exactly
        once after all its predecessors are delivered. NO re-enqueue of paused nodes (the
        re-run model is gone). Resume drives under the SAME drive mode the engine carries
        (serial or pooled), via the shared _drive_to_terminal."""
        yield RunResumed()
        # A commandless resume of a STILL-paused run re-emits RunPaused and returns
        # WITHOUT clearing self.paused. An idempotent poll / watcher tick / partial multi-pause
        # delivery must not destroy the pause (a no-op resume stays paused, never falls
        # through to a state-destroying terminal). Guard before the clear below.
        if self.paused and not (commands or []):
            yield RunPaused(reasons=[reason for _, reason in self.paused])
            return
        # Apply commands WHILE self.paused is still set (successors route to deferred). A
        # type-invalid answer raises NodeExecutionError here -> RunFailed (resume never crashes).
        try:
            for command in commands or []:
                self._apply_command(command)
        except NodeExecutionError as exc:
            yield RunFailed(error=exc.error, error_type=exc.error_type, locator=exc.locator)
            return
        seed = list(self.deferred) + list(self.ready)
        self.paused = []
        self.deferred = []
        self.ready = deque()           # seed already captured it; _enqueue re-appends once
        for node_id in seed:
            self._enqueue(node_id)     # serial -> self.ready; pooled -> self.ready_q
        yield from self._drive_to_terminal()

    def _apply_command(self, command) -> None:
        from agent_composer.suspension.commands import (
            AbortCommand,
            DeliverAnswerCommand,
        )

        if isinstance(command, DeliverAnswerCommand):
            # Deliver-as-Output: write the answer as the parked leaf's value and fire
            # its existing out-edges. The node is resolved against the LIVE graph, so a
            # runtime-namespaced id resolves. Wrapped in the SAME SegmentError -> NodeExecutionError
            # guard _on_success uses, so a type-invalid answer FAILS the run (it does not crash
            # resume). A WAIT release delivers value=None (timed WAIT output_shape is None).
            node = self.flow.nodes[command.node_id]
            try:
                self.pool.set(command.node_id, command.value, declared=node.output_shape)
            except SegmentError as exc:
                self.sm.finish_executing(command.node_id)
                raise NodeExecutionError(
                    command.node_id, str(exc), type(exc).__name__,
                    locator=SourceSpan(node=command.node_id, kind="field", key="output"),
                )
            self.sm.finish_executing(command.node_id)  # idempotent (already finished on pause)
            for nid in self._advance(command.node_id):
                self._schedule(nid)
        elif isinstance(command, AbortCommand):
            self._cancel = True

    # --- drain -------------------------------------------------------------- #

    def _drain(self):
        while self.ready:
            if self._cancel:
                raise _Aborted
            node_id = self.ready.popleft()
            yield from self._run_node(node_id)

    def _run_node(self, node_id: str):
        node = self.flow.nodes[node_id]
        succeeded: Optional[NodeSucceeded] = None
        for event in eval_node(node, self.flow, self.pool):
            yield event
            if isinstance(event, NodeSucceeded):
                succeeded = event
            elif isinstance(event, NodeFailed):
                self.sm.finish_executing(node_id)
                raise NodeExecutionError(
                    node_id, event.error, event.error_type, locator=event.locator
                )
            elif isinstance(event, PauseRequested):
                self._on_pause(node_id, event.reason)
                return
            elif isinstance(event, NodeExpanded):
                # _apply_enqueue runs OUTSIDE eval_node's try/except; wrap any raise
                # (boundary-assert / bounds / unhandled-kind / clone_child error) into
                # NodeExecutionError so run() yields RunFailed, never an uncaught escape.
                try:
                    self._apply_enqueue(node_id, event.enqueues)
                except NodeExecutionError:
                    raise
                except Exception as exc:  # noqa: BLE001 — boundary: any apply error -> RunFailed
                    self.sm.finish_executing(node_id)
                    raise NodeExecutionError(node_id, str(exc), type(exc).__name__)
                return
        if succeeded is not None:
            self._on_success(node_id, succeeded)

    # --- pooled path (num_workers>=1): dispatcher + workers ----------------- #

    def _dispatch(self):
        # Single writer: drains event_q, forwards each event, applies the shared
        # mutation helpers. Completion = ready_q empty AND no node executing.
        while not self.sm.is_complete(self.ready_q.empty()):
            if self._cancel:
                raise _Aborted
            try:
                event = self.event_q.get(timeout=_POLL)
            except queue.Empty:
                continue
            yield event  # forward to the caller (streaming)
            if isinstance(event, NodeSucceeded):
                self._on_success(event.node_id, event)
            elif isinstance(event, NodeFailed):
                self.sm.finish_executing(event.node_id)
                raise NodeExecutionError(
                    event.node_id, event.error, event.error_type, locator=event.locator
                )
            elif isinstance(event, PauseRequested):
                self._on_pause(event.node_id, event.reason)
            elif isinstance(event, NodeExpanded):
                # Same wrap as the inline _run_node branch.
                try:
                    self._apply_enqueue(event.node_id, event.enqueues)
                except NodeExecutionError:
                    raise
                except Exception as exc:  # noqa: BLE001 — boundary: any apply error -> RunFailed
                    self.sm.finish_executing(event.node_id)
                    raise NodeExecutionError(event.node_id, str(exc), type(exc).__name__)

    def _worker(self) -> None:
        # Pure executor: pulls a node id, runs eval_node, pushes events. Never
        # mutates graph/edge/pool state.
        while not self._stop.is_set():
            try:
                node_id = self.ready_q.get(timeout=_POLL)
            except queue.Empty:
                continue
            node = self.flow.nodes[node_id]
            try:
                for event in eval_node(node, self.flow, self.pool):
                    self.event_q.put(event)
            except Exception as exc:  # noqa: BLE001 — never let a worker die silently
                self.event_q.put(NodeFailed(node_id, str(exc), type(exc).__name__))

    # --- state mutation (shared by the inline loop and the pooled dispatcher) #

    def _enqueue(self, node_id: str) -> None:
        # Runs on the dispatcher / inline loop only (single writer).
        self.sm.mark_node(node_id, NodeState.TAKEN)
        self.sm.add_executing(node_id)
        if self.num_workers == 0:
            self.ready.append(node_id)
        else:
            self.ready_q.put(node_id)

    def _ready_snapshot(self) -> list[str]:
        """The queued-ready ids for a checkpoint — branches strictly on
        `num_workers==0` (the serial deque) vs the pooled `ready_q`, so snapshot()
        captures the queued ids under either path."""
        return list(self.ready) if self.num_workers == 0 else list(self.ready_q.queue)

    def _schedule(self, node_id: str) -> None:
        # While the run is suspending, hold newly-ready nodes as deferred rather
        # than starting fresh work; they re-enter the queue on resume.
        if self.paused:
            self.deferred.append(node_id)
        else:
            self._enqueue(node_id)

    def _on_pause(self, node_id: str, reason: Any) -> None:
        # Park the node: the engine delivers its answer as an Output on resume (no
        # re-run, so NO UNKNOWN reset). The node stays TAKEN (set by _enqueue); finish_executing
        # only discards it from the `executing` set, it does not touch node_state.
        self.sm.finish_executing(node_id)
        self.paused.append((node_id, reason))

    # --- whole-arm clone+register helpers ---------------------------------- #
    #
    # Each of the three `_grow_*` helpers performs the SHARED topology+bookkeeping
    # half of one `_apply_enqueue` arm: clone (pure) -> add_subgraph -> register ->
    # alias/depth/_spawner_expansion stamps -> mark the spawner EXPANDED. They are
    # called BOTH by the live dispatcher (`_apply_enqueue`, `schedule=True`, with the
    # boundary-assert + budget effects) AND by the restore-side fold
    # (`_replay_expansions`, `schedule=False`, effects suppressed). The caller owns
    # the descriptor (create+attach to the ledger) and passes it in; the helper stamps
    # `_spawner_expansion` to point at THAT object so a later in-place append (an AGENT
    # 2nd segment, a nested CALL/MAP under it) grows a descriptor that IS in the ledger.
    #
    # _spawner_expansion retention DIFFERS from alias: _spawner_expansion keeps
    # ALL ids (no pop) — it is the branch key for "append-to-existing vs new-top-level";
    # alias keeps only the LATEST (pop) — it routes the final Output to the origin.

    def _grow_call(self, spawner_id: str, child, record: dict, desc, *, schedule: bool,
                   assert_boundary=None):
        """Clone+register one CALL child at `spawner_id` (the shared CALL topology). `child`
        is the clone source (== `enq.target` live, == `flow.nodes[spawner_id].child` on
        replay — the two are the same baked child for a real CallNode). Returns the
        `ClonedSubgraph`. The caller created `desc`; this stamps every cloned spawner-eligible
        node's parent-pointer at it (so a nested REF/MAP/AGENT finds THIS CallExpansion) — the
        caller attaches `desc` to the ledger only after this returns. `assert_boundary(cloned)`
        (live only) runs the eager boundary-assert on the freshly cloned child and raises BEFORE
        any mutation; None suppresses it (replay)."""
        d = self.depth.get(spawner_id, 0) + 1   # depth computed INTERNALLY
        cloned = clone_child(child, callsite=spawner_id, record=record)
        if assert_boundary is not None:
            assert_boundary(cloned)             # raises -> RunFailed, before add_subgraph
        with self.sm.lock:                      # append + register atomically
            self.flow.add_subgraph(cloned.nodes, cloned.edges, cloned.wiring)
            self.sm.register(list(cloned.nodes), cloned.edges)
            if schedule and len(self.flow.nodes) > MAX_TOTAL_NODES:
                raise RuntimeError(
                    f"expansion exceeded node budget ({MAX_TOTAL_NODES}) at spawner {spawner_id!r}"
                )
            for nid, node in cloned.nodes.items():
                if node.kind in _SPAWNER_KINDS:
                    self.depth[nid] = d
                    self._spawner_expansion[nid] = desc
        self.depth[cloned.out_node_id] = d      # the filler's alias carries the depth
        self.alias[cloned.out_node_id] = spawner_id
        self.sm.finish_executing(spawner_id)
        self.sm.mark_node(spawner_id, NodeState.EXPANDED)
        if schedule:
            for root in cloned.roots:
                self._schedule(root)            # _schedule respects suspend (deferred)
        return cloned

    def _grow_map(self, spawner_id: str, child, records: list, desc, *, schedule: bool,
                  assert_boundary=None):
        """Clone+register the WHOLE MAP fan-in at `spawner_id`: N per-element child
        clones PLUS the `map_end` LIST EndNode + its fan-in edges/wiring + `alias[map_end]
        =spawner`, including the N=0 case (an EndNode.list_(n=0) still built + stamped). `child`
        is the clone source (== `enq.target` live, == `flow.nodes[spawner_id].child` on
        replay). `assert_boundary(cloned, i, record)` (live only) runs the per-element eager
        boundary-assert and raises on a violation; None suppresses it (replay). Returns the
        `map_end_id`."""
        d = self.depth.get(spawner_id, 0) + 1   # depth computed INTERNALLY
        map_end_id = ns(spawner_id, END_ID)                 # the END_ID-list filler
        end_wiring: dict[str, str] = {}
        end_edges: list[Edge] = []
        seed_roots: list[str] = []                          # per-element roots — ONE clone each
        with self.sm.lock:                                  # append + register atomically
            for i, record in enumerate(records):
                callsite = map_callsite(spawner_id, i)       # f"{spawner}#{i}"
                cloned = clone_child(child, callsite=callsite, record=record)
                if assert_boundary is not None:
                    assert_boundary(cloned, i, record)
                self.flow.add_subgraph(cloned.nodes, cloned.edges, cloned.wiring)
                self.sm.register(list(cloned.nodes), cloned.edges)
                if schedule and len(self.flow.nodes) > MAX_TOTAL_NODES:
                    raise RuntimeError(
                        f"expansion exceeded node budget ({MAX_TOTAL_NODES}) at spawner {spawner_id!r}"
                    )
                # The i-th _MapElementSlot is the parent-pointer: a nested expansion appends
                # to desc.children_per_element[i] (NOT a flat children list).
                elem_slot = _MapElementSlot(desc, i)
                for nid, node in cloned.nodes.items():
                    if node.kind in _SPAWNER_KINDS:
                        self.depth[nid] = d
                        self._spawner_expansion[nid] = elem_slot
                seed_roots.extend(cloned.roots)
                end_wiring[f"e{i}"] = f"${{{cloned.out_node_id}.output}}"   # node-first
                end_edges.append(Edge(
                    id=f"{cloned.out_node_id}->{map_end_id}#{i}",
                    from_=cloned.out_node_id, to=map_end_id, input_group=f"e{i}"))
            # ONE EndNode in LIST mode — the MAP fan-in over child ENDs.
            # N=0: still built + registered even when records==[] (a MAP over [] that fired
            # before a sibling pause) — the END_ID-list is then a 0-incoming root that emits [].
            map_end = EndNode.list_(map_end_id, n=len(records))
            self.flow.add_subgraph({map_end_id: map_end}, end_edges,
                                   {map_end_id: end_wiring})
            self.sm.register([map_end_id], end_edges)
        self.depth[map_end_id] = d                          # the filler's alias carries the depth
        self.alias[map_end_id] = spawner_id
        self.sm.finish_executing(spawner_id)
        self.sm.mark_node(spawner_id, NodeState.EXPANDED)
        if schedule:
            for root in seed_roots:                         # schedule AFTER the whole subgraph
                self._schedule(root)
            if not records:                                 # N=0: the END_ID-list has 0 incoming -> a root
                self._schedule(map_end_id)                  # -> emits [] (must NOT short-circuit)
        return map_end_id

    def _grow_agent_segment(self, spawner_id: str, hi_desc: dict, resume_desc: dict,
                            desc, *, schedule: bool):
        """Clone+register one AGENT-pause continuation segment at `spawner_id` (the shared
        AGENT topology). The Enqueue target is the PAIR `[hi_desc, resume_desc]`. Returns the
        `ClonedSubgraph`. Stamps `_spawner_expansion` on BOTH `spawner_id` AND
        `cloned.out_node_id` (the resume id = the NEXT segment's spawner) at `desc` (keep-ALL
        retention, no pop). alias chains to origin (pop). depth is carried UNCHANGED (a K-pause
        agent is NOT recursion)."""
        pair = [hi_desc, resume_desc]
        # Carry the spawner's declared output Shape + self-correction cap onto the resume node so a
        # resumed agent with a non-text `output:` still emits the declared shape on its final turn
        # (else the alias-filler write boundary rejects the plain-text answer). build.py stamps
        # `output_shape` on the compiled agent; each resume node re-stamps it (expand.py), so it
        # propagates segment to segment across a multi-pause chain.
        spawner = self.flow.nodes[spawner_id]
        cloned = clone_continuation_pair(
            pair,
            callsite=spawner_id,
            output_shape=getattr(spawner, "output_shape", None),
            retries=getattr(spawner, "retries", 2),
        )
        with self.sm.lock:                      # append + register atomically
            self.flow.add_subgraph(cloned.nodes, cloned.edges, cloned.wiring)
            self.sm.register(list(cloned.nodes), cloned.edges)
            if schedule and len(self.flow.nodes) > MAX_TOTAL_NODES:
                raise RuntimeError(
                    f"expansion exceeded node budget ({MAX_TOTAL_NODES}) at spawner {spawner_id!r}"
                )
            # Retention: stamp BOTH the spawner_id (idempotent for segment 2+) AND the
            # cloned resume_id (the NEXT segment's spawner) — keep ALL (no pop). Also any
            # spawner-eligible cloned subnodes (today empty).
            self._spawner_expansion[spawner_id] = desc
            self._spawner_expansion[cloned.out_node_id] = desc
            for nid, node in cloned.nodes.items():
                if node.kind in _SPAWNER_KINDS:
                    self._spawner_expansion[nid] = desc
        # Re-point the alias to the latest resume continuation so the FINAL non-pausing Output
        # commits under the ORIGINAL spawner id (multi-pause chaining): chain to origin.
        origin = self.alias.pop(spawner_id, spawner_id)
        self.alias[cloned.out_node_id] = origin
        self.sm.finish_executing(spawner_id)
        self.sm.mark_node(spawner_id, NodeState.EXPANDED)
        # INVARIANT: the resume filler carries the PARENT depth UNCHANGED (no `+1`).
        self.depth[cloned.out_node_id] = self.depth.get(spawner_id, 0)
        if schedule:
            for root in cloned.roots:          # the human_input leaf
                self._schedule(root)
        return cloned

    def _replay_expansions(self, expansions: list, *, parent_depth: int = 0,
                           is_top_level: bool = True) -> None:
        """Deterministic fold over a persisted descriptor tree: re-grow the live
        topology + bookkeeping a paused run had, with all live effects suppressed
        (`schedule=False` — no boundary assert, no budget raise, no scheduling). The pure
        clone (`ns(callsite, child_id)`) re-keys every cloned node identically, so the
        rebuilt overlay matches the live engine's byte-for-byte.

        REBUILDS `self.expansions` by REUSING each deserialized descriptor object — a
        top-level descriptor (`is_top_level`) is appended to `self.expansions`; a nested one is
        ALREADY attached under its parent's slot by deserialization, so the fold only walks it.
        The append decision is keyed on `is_top_level` (NOT on `parent_depth == 0`): an AGENT
        carries depth UNCHANGED, so a future child of a top-level AGENT segment would still have
        `parent_depth == 0` yet must NOT be promoted to a top-level ledger entry. The `_grow_*`
        helpers stamp `_spawner_expansion` to point at THESE SAME objects, so a later in-place
        append (an AGENT 2nd segment after a 2nd durable hop) grows a descriptor that IS in the
        ledger.

        Closed `Expansion` sum — exhaustive dispatch on `type`, `else: raise` (loud)."""
        from agent_composer.suspension.expansions import (
            AgentExpansion, CallExpansion, MapExpansion,
        )

        for desc in expansions:
            if is_top_level:
                self.expansions.append(desc)    # rebuild the ledger; nested ones ride their parent
            spawner_id = desc.spawner_id
            if isinstance(desc, CallExpansion):
                child = self.flow.nodes[spawner_id].child  # baked at load (CALL/MAP)
                self._grow_call(spawner_id, child, dict(desc.record), desc, schedule=False)
                this_depth = self.depth.get(spawner_id, 0) + 1
                self._replay_expansions(desc.children, parent_depth=this_depth, is_top_level=False)
            elif isinstance(desc, MapExpansion):
                child = self.flow.nodes[spawner_id].child  # baked at load (CALL/MAP)
                records = [dict(r) for r in desc.records]
                self._grow_map(spawner_id, child, records, desc, schedule=False)
                this_depth = self.depth.get(spawner_id, 0) + 1
                for kids in desc.children_per_element:
                    self._replay_expansions(kids, parent_depth=this_depth, is_top_level=False)
            elif isinstance(desc, AgentExpansion):
                # Chain `current_spawner` across segments — seg0 = spawner_id, then the
                # prior segment's resume id (`cloned.out_node_id`). Reuse the ONE AgentExpansion
                # as `desc` for every segment so `_spawner_expansion` of each resume id points
                # at the SAME object; `_grow_agent_segment` chains `alias[final_out]=origin`.
                current = spawner_id
                for segment in desc.segments:
                    cloned = self._grow_agent_segment(
                        current, segment.hi_desc, segment.resume_desc, desc, schedule=False)
                    # AGENT carries depth UNCHANGED; segment children are a future slot (today []).
                    self._replay_expansions(segment.children, parent_depth=parent_depth,
                                            is_top_level=False)
                    current = cloned.out_node_id
            else:
                raise ValueError(f"unknown Expansion descriptor {type(desc).__name__!r}")

    def _apply_enqueue(self, spawner_id: str, enqueues: list) -> None:
        """Grow the live graph from a spawner's Enqueue(s) (the dispatcher's sole-writer
        expansion). A guarded `if/elif/else` ladder so each arm (CALL, MAP, AGENT)
        is mutually exclusive and an unhandled kind is loud (-> RunFailed via the boundary wrap).
        Each arm: create the descriptor, call the shared `_grow_*` helper with `schedule=True`
        (the helper does clone+register+alias/depth/_spawner_expansion), THEN attach the
        descriptor to the ledger — attach-after-grow so a boundary-assert/budget raise inside
        `_grow_*` leaves no orphan descriptor (CALL/MAP)."""
        from agent_composer.expr import first_failing_assert
        from agent_composer.state.seeding import apply_defaults, coerce_inputs

        spawner = self.flow.nodes[spawner_id]
        # The child START_ID's full input transform (start/node.py): coerce the present args to their
        # declared types, THEN fill omitted defaults — the exact view the spliced child START_ID commits.
        # The eager boundary-assert temp pool mirrors it so `${input.X}` asserts read the SAME value
        # the body will (a present "30" -> int 30, an omitted default filled), never the raw record.
        def _child_boundary_record(record):
            decls = spawner.child_inputs
            return apply_defaults(decls, coerce_inputs(decls, dict(record)))
        if spawner.kind == NodeKind.AGENT:
            # Agent-pause continuation: the Enqueue target is a PAIR of primitive node
            # descriptors (a human_input leaf + a resume continuation), NOT a child flow. The
            # spawner is an AGENT in EITHER entry mode — a Fresh agent pausing, or a resumed
            # AgentNode (a Resume entry) pausing AGAIN (multi-pause); both share `kind = AGENT`,
            # so this one arm covers both. It precedes the CALL/MAP arms and SKIPS the
            # MAX_REF_DEPTH check below — an agent pausing K times is NOT recursion; it
            # is bounded by MAX_TOOL_ITERATIONS / MAX_TOTAL_NODES, not the REF depth.
            # Unpack the continuation PAIR up front: a malformed AGENT target (not a
            # [hi_desc, resume_desc] pair) raises "cannot unpack" HERE, funneled to RunFailed
            # by the boundary wrap (matches the pre-refactor failure point).
            hi_desc, resume_desc = enqueues[0].target
            # Create+attach the descriptor. Parent-pointer is the SOLE lookup.
            # Three branches: multi-pause (append a segment to the same AgentExpansion), first
            # AGENT pause nested under a CALL/MAP parent, top-level first AGENT pause.
            from agent_composer.suspension.expansions import (
                AgentExpansion, AgentSegment,
            )
            segment = AgentSegment(hi_desc=hi_desc, resume_desc=resume_desc)
            parent_desc = self._spawner_expansion.get(spawner_id)
            if isinstance(parent_desc, AgentExpansion):
                parent_desc.segments.append(segment)
                desc = parent_desc
            elif parent_desc is not None:
                desc = AgentExpansion(spawner_id=spawner_id, segments=[segment])
                _append_child(parent_desc, desc)
            else:
                desc = AgentExpansion(spawner_id=spawner_id, segments=[segment])
                self.expansions.append(desc)
            self._grow_agent_segment(spawner_id, hi_desc, resume_desc, desc, schedule=True)
            return

        # Depth bound (REF/MAP only): this expansion is at depth d (parent depth +
        # 1). A genuinely deep REF/MAP chain trips MAX_REF_DEPTH before MAX_TOTAL_NODES; raises ->
        # RunFailed. (The agent arm above returns before this — its pauses are NOT recursion.)
        d = self.depth.get(spawner_id, 0) + 1
        if d > MAX_REF_DEPTH:
            raise RuntimeError(
                f"expansion exceeded MAX_REF_DEPTH ({MAX_REF_DEPTH}) at {spawner_id!r}"
            )

        if spawner.kind == NodeKind.CALL:
            enq = enqueues[0]                       # plain call: exactly one child
            record = dict(enq.inputs)
            # Eager boundary-assert eval against a temp pool seeded with the baked
            # record (inputs namespace) + the live system; a violation raises -> RunFailed.
            def _assert_call(cloned):
                if not cloned.boundary_asserts:
                    return
                temp = TypedVariablePool()
                # Seed the temp pool's START_ID with the child's EFFECTIVE inputs —
                # coerced + defaulted, the same view the spliced child START_ID will commit.
                temp.set(START_ID, _child_boundary_record(record))
                temp.system = dict(self.pool.system)
                bad = first_failing_assert(cloned.boundary_asserts, temp)
                if bad is not None:
                    raise RuntimeError(f"REF child {spawner_id!r} boundary assert failed: {bad}")
            # Create the CallExpansion descriptor (parent-pointer is the SOLE lookup).
            from agent_composer.suspension.expansions import CallExpansion
            desc = CallExpansion(spawner_id=spawner_id, record=record, children=[])
            parent_desc = self._spawner_expansion.get(spawner_id)
            self._grow_call(spawner_id, enq.target, record, desc, schedule=True,
                            assert_boundary=_assert_call)
            # Attach to the ledger AFTER _grow_call succeeds — a boundary-assert or node-budget
            # raise inside _grow_call must NOT leave an orphan descriptor in self.expansions /
            # the parent slot (the NIT closed here). `_grow_call` stamps `_spawner_expansion`
            # with `desc` directly, so nested lookups don't need the ledger attach.
            if parent_desc is not None:
                _append_child(parent_desc, desc)
            else:
                self.expansions.append(desc)
        elif spawner.kind == NodeKind.MAP:
            records = [dict(enq.inputs) for enq in enqueues]
            child = enqueues[0].target if enqueues else None  # N=0: no per-element clone
            # Create the MapExpansion descriptor (empty per-element children lists;
            # populated as each element's nested expansions fire).
            from agent_composer.suspension.expansions import MapExpansion
            desc = MapExpansion(
                spawner_id=spawner_id, records=records,
                children_per_element=[[] for _ in records],
            )
            parent_desc = self._spawner_expansion.get(spawner_id)
            # Eager PER-ELEMENT boundary-assert eval; a violation raises ->
            # RunFailed (the boundary wrap). The SINGLE per-element firing point.
            def _assert_map(cloned, i, record):
                if not cloned.boundary_asserts:
                    return
                temp = TypedVariablePool()
                temp.set(START_ID, _child_boundary_record(record))
                temp.system = dict(self.pool.system)
                bad = first_failing_assert(cloned.boundary_asserts, temp)
                if bad is not None:
                    raise RuntimeError(
                        f"MAP child {spawner_id!r} element {i} boundary assert failed: {bad}"
                    )
            self._grow_map(spawner_id, child, records, desc, schedule=True,
                           assert_boundary=_assert_map)
            # Attach AFTER _grow_map succeeds — a per-element boundary-assert or node-budget raise
            # must NOT leave an orphan descriptor in self.expansions / the parent slot (NIT closed).
            if parent_desc is not None:
                _append_child(parent_desc, desc)
            else:
                self.expansions.append(desc)
        else:
            raise RuntimeError(f"unhandled spawner kind {spawner.kind}")

    def _on_success(self, node_id: str, event: NodeSucceeded) -> None:
        # An alias filler (cloned child END_ID / MAP END_ID-list / resume continuation) is a pure sink with no
        # out-edge of its own: substitute the spawner — write the filler's value under
        # the SPAWNER id (same SegmentError -> NodeExecutionError guard the ordinary tail uses)
        # and fire the spawner's existing out-edges. The filler's own pool.set is SKIPPED.
        if node_id in self.alias:
            spawner_id = self.alias[node_id]
            spawner = self.flow.nodes[spawner_id]
            try:
                self.pool.set(spawner_id, event.output, declared=spawner.output_shape)
            except SegmentError as exc:
                self.sm.finish_executing(node_id)
                raise NodeExecutionError(
                    node_id, str(exc), type(exc).__name__,
                    locator=SourceSpan(node=spawner_id, kind="field", key="output"),
                )
            self.sm.finish_executing(node_id)
            for nid in self._advance(spawner_id):
                self._schedule(nid)
            return
        node = self.flow.nodes[node_id]
        # (a) the node's ONE value lands in the pool BEFORE successors are scheduled,
        # type-enforced against the node's declared Shape. IF_ELSE is routing-only
        # (no value) — it writes nothing.
        if node.kind != NodeKind.IF_ELSE:
            try:
                self.pool.set(node_id, event.output, declared=node.output_shape)
            except SegmentError as exc:
                self.sm.finish_executing(node_id)
                raise NodeExecutionError(
                    node_id, str(exc), type(exc).__name__,
                    locator=SourceSpan(node=node_id, kind="field", key="output"),
                )
        self.sm.finish_executing(node_id)

        if node.kind == NodeKind.IF_ELSE:
            newly_ready = self._branch(node_id, event.edge_source_handle or DEFAULT_HANDLE)
        else:
            newly_ready = self._advance(node_id)
        for nid in newly_ready:
            self._schedule(nid)

    def _advance(self, node_id: str) -> list[str]:
        ready: list[str] = []
        for edge in self.flow.outgoing(node_id):
            self.sm.mark_edge(edge.id, NodeState.TAKEN)
            # END_ID is a REAL node now — it must be scheduled, run, and committed (the run
            # result), so no `edge.to != END_ID` guard. END_ID participates in readiness/skip-flood.
            if self.sm.is_node_ready(edge.to):
                ready.append(edge.to)
        return ready

    def _branch(self, node_id: str, handle: str) -> list[str]:
        ready: list[str] = []
        for edge in self.flow.outgoing(node_id):
            if (edge.source_handle or DEFAULT_HANDLE) == handle:
                self.sm.mark_edge(edge.id, NodeState.TAKEN)
                if self.sm.is_node_ready(edge.to):
                    ready.append(edge.to)
            else:
                ready += self._skip_edge(edge)
        return ready

    def _skip_edge(self, edge) -> list[str]:
        """Skip an edge, then resolve the head via the unified disposition: ready -> schedule;
        dead -> skip-flood; wait -> leave for a later edge. The veto/data-co-skip live in disposition,
        so a skipped control edge that leaves all-control-skipped (or a fully-skipped required
        data group) floods the head even if another data edge is TAKEN."""
        self.sm.mark_edge(edge.id, NodeState.SKIPPED)
        head = edge.to
        # END_ID participates in skip-flood + disposition (its required output group can die).
        disp = self.sm.disposition(head)
        if disp == "ready":
            return [head]
        if disp == "dead":
            self.sm.mark_node(head, NodeState.SKIPPED)
            ready: list[str] = []
            for out in self.flow.outgoing(head):
                ready += self._skip_edge(out)
            return ready
        return []  # "wait" — a predecessor edge is still pending; decide later
