"""eval_node — the engine's node-evaluation seam.

A node is a pure function of its bound input record; the ENGINE owns both boundaries —
the read (bind the inputs from the pool) and the write (the dispatcher stores `Output`).
This generator is that read seam plus the assert + dispatch normalization, lifted out of
the node (it replaces the temporary `Node._emit`). It yields `NodeStarted`, binds the
record, pre-resolves reserved keys (timed WAIT `until` / mapped-call `over`), builds the per-kind
narrow caps (only a mapped call's `bind_item` now — no node uses a private namespace), runs the node,
and turns the returned `NodeResult` into one terminal `NodeSucceeded`/`NodeFailed` — or a
single `PauseRequested` for a returned `Pause`.

Every node-side failure path funnels to `NodeFailed` so the two engines (serial + parallel)
agree byte-for-byte on the same input: a node `raise`, a `Grow` returned by a NON-spawner
kind, and a non-`NodeResult` return all become `NodeFailed` inside the `try` — none
escape the generator uncaught. (A spawner's `Grow` instead becomes `NodeExpanded`.)

The bind is PURE: it reads the node's sources ONLY from `flow.wiring[node.id]` joined to the
node's `params` — the node carries no source. Direct-drive tests supply a stub `flow` with
`wiring` (the test helpers derive it). Layer: runtime imports nodes.* freely.

Accepted bind ordering vs the old per-node `_run` (both on states a loaded flow cannot reach,
both still terminating in `NodeFailed`): a timed WAIT re-resolves `until` on every path including
release/resume (harmless given the monotonic pool), and a `call` that is BOTH unbaked AND has a
bad `over`/binding surfaces the over/bind error before the not-baked guard (loader always bakes).
"""

import copy
import inspect
import traceback as _tb
from dataclasses import replace
from typing import Any

from agent_composer.events import (
    NodeExpanded,
    NodeFailed,
    NodeRouted,
    NodeStarted,
    NodeSucceeded,
    PauseRequested,
    SourceSpan,
)
from agent_composer.expr import resolve_reference
from agent_composer.expr.expressions import _evaluate, _resolve_in_record
from agent_composer.nodes.base import Grow, NodeKind, Output, Pause, Route
from agent_composer.nodes.binding import bind_params
from agent_composer.state.pool import TypedVariablePool


def eval_node(node, flow, pool: TypedVariablePool):
    """Evaluate one node through the engine read/dispatch seam; yield its event stream."""
    yield NodeStarted(node.id)
    try:
        # The flow-owned wiring for this node (the node/flow split): every kind's sources live here
        # (leaf/WAIT, CALL, CASE). The node holds NO source. A direct-driver must supply a
        # stub `flow.wiring` with the reserved keys; `flow is None` gives empty wiring, so a timed
        # WAIT / mapped call driven that way would KeyError on `until`/`over` (caught as NodeFailed).
        node_wiring = {} if flow is None else flow.wiring.get(node.id, {})
        # Read boundary (pure): bind the node from its `params` + the flow-owned
        # wiring — never the node's own `inputs`. A per-item node (MAP) binds per-element via
        # bind_item, so its record starts empty. (`params or []` covers a no-input node / a
        # direct-construction test fake; loader-built nodes always carry params.)
        per_item = node.binds_per_item          # MAP = per-element bind (trait, not a kind read)
        if per_item:
            record = {}
        else:
            record = bind_params(node.params or [], node_wiring, pool)
        # Reserved-key pre-resolve (node-owned): timed WAIT `until` -> ISO ts; mapped-call `over`
        # -> list (validated in the hook -> NodeFailed). The hook reads its sources from
        # `node_wiring` (the node/flow split) + `pool`; default `{}` for an ordinary node.
        record.update(node.bind_reserved(node_wiring, pool))
        for a in node.pre_asserts:
            if not node._assert_holds(a, record):
                yield NodeFailed(node.id, error=f"node {node.id!r} pre-assert failed: {a}",
                                 error_type="NodeAssertFailed",
                                 locator=SourceSpan(node.id, "assert", a))
                return
        # Per-kind narrow caps, built by the engine — never the pool itself.
        # HUMAN_INPUT/WAIT are deliver-as-Output: they always Pause and the engine
        # delivers the answer. AGENT lowers a control pause to a continuation `Grow`,
        # carrying its memo as graph data — so a mapped call's `bind_item` is the only cap now.
        caps: dict[str, Any] = {}
        if per_item:
            # Per-element bind from params + flow.wiring (pure). No system cap — the
            # cloned children share the one live pool, so `${system.X}` resolves directly.
            caps["bind_item"] = lambda el: bind_params(node.params or [], node_wiring, pool, item=el)
        # Pristine snapshot for the POST asserts: a leaf may mutate the dict it receives
        # (e.g. a CODE function transforming in place), which must not corrupt the contract
        # check — restores the isolation an earlier double-bind gave. Only paid when declared.
        post_input = copy.deepcopy(record) if node.post_asserts else None
        outcome = node.run(record, **caps)
        if isinstance(outcome, (Output, Route, Pause, Grow)):
            result = outcome
        elif inspect.isgenerator(outcome):  # a streaming kind: yields StreamChunk, returns a NodeResult
            result = yield from node._drain_node_generator(outcome)
        else:
            raise RuntimeError(
                f"node {node.id!r} run() returned {type(outcome).__name__}, not a NodeResult"
            )
        if isinstance(result, Grow):
            # A spawner grows the live graph: hand the self-describing `Grow` to the dispatcher's
            # `_apply_grow` via NodeExpanded. Any non-spawner kind returning a Grow is a clear error
            # (the graph only grows from spawners).
            if not node.is_spawner:
                raise RuntimeError(
                    f"node {node.id!r} (kind {node.kind.value}) returned a Grow but is "
                    f"not a spawner (CALL/MAP/AGENT/LOOP); only spawner kinds may grow the graph"
                )
            yield NodeExpanded(node.id, result)
            return
    except Exception as exc:  # noqa: BLE001 — boundary: any node error -> NodeFailed (both engines)
        # A failure may carry a `locator` (BindingError stamps a node-less input SourceSpan;
        # the binding layer has no node identity). Fill the node id here — this funnel is the
        # single point that knows it.
        loc = getattr(exc, "locator", None)
        if loc is not None and loc.node is None:
            loc = replace(loc, node=node.id)
        # Capture the full formatted traceback here, while the exception is live, so the CLI
        # can surface the raising call's Python stack under `--engine-trace`. The terse
        # default (message + boxed YAML frame) never shows it.
        yield NodeFailed(node.id, error=str(exc), error_type=type(exc).__name__, locator=loc,
                         traceback=_tb.format_exc())
        return

    if isinstance(result, Pause):
        yield PauseRequested(node.id, result.reason)  # suspended; no terminal
        return
    if isinstance(result, Route):
        yield NodeRouted(node.id, result.handle)
        return
    if node.post_asserts:
        # An END_ID node's `${output}` reads its terminal value via the
        # injected record, NOT the pool (which has no `output` head and would silently
        # resolve to None -> assert false-holds). Inject `{"output": end_value}`
        # EXACTLY (NOT `{**end_value, ...}`). A flow whose
        # `output:` declares a field literally named `output` is overwritten by the
        # synthetic `${output}` selector (the precedence rule documented in
        # agent-compose-principles.md).
        #
        # END_ID additionally needs POOL-fallback for namespaced cross-node refs that ride
        # END_ID from expand.py:_rens_internal (e.g. a child flow's `${each#0/n.output.X}`
        # asserts). Resolve `${output[...]}` from the record; everything else from the pool.
        if node.kind == NodeKind.END:
            end_record = {"output": result.value}
            def _resolve_end(path: str):
                head = path.split(".", 1)[0].strip()
                if head == "output":
                    return _resolve_in_record(path, end_record)
                return resolve_reference(path, pool)
            for a in node.post_asserts:
                try:
                    holds = bool(_evaluate(a, _resolve_end))
                except Exception:
                    holds = False
                if not holds:
                    yield NodeFailed(node.id, error=f"node {node.id!r} post-assert failed: {a}",
                                     error_type="NodeAssertFailed",
                                     locator=SourceSpan(node.id, "assert", a))
                    return
        else:
            post_record = {**post_input, "output": result.value}
            for a in node.post_asserts:
                if not node._assert_holds(a, post_record):
                    yield NodeFailed(node.id, error=f"node {node.id!r} post-assert failed: {a}",
                                     error_type="NodeAssertFailed",
                                     locator=SourceSpan(node.id, "assert", a))
                    return
    # Fold the commit redirect onto the terminal: a node-chosen `Output.commit_as` (roadmap)
    # wins over the engine-baked `node.commit_as` (the subflow-terminal redirect); both default
    # None so an ordinary node commits under its own id. This is the sole channel `_on_success`
    # reads to decide the commit target.
    yield NodeSucceeded(node.id, output=result.value,
                        commit_as=(result.commit_as or node.commit_as))
