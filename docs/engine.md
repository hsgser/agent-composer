# The Engine

> **Target design.** This is the contract the engine is being refactored *toward* — a clean
> redesign, not a description of today's code. The refactor items live in
> [Roadmap → TODO](backlog/TODO.md) ("`eval_node` / NodeBase contract cleanup"). For the node
> side of the same contract, see [Nodes](nodes.md).

## What the engine is (for a reader who has never seen this project)

An Agent Composer **flow** is a small program drawn as a graph. Each box in the graph is a
**node** — one step of work: "ask an LLM a question", "run a Python function", "loop until good
enough". Arrows carry data from one node to the next. You write the flow as YAML; the engine is
the thing that *runs* it.

Running a graph means answering three questions, over and over, until there is nothing left to do:

1. **What can run now?** (a node whose inputs are all ready)
2. **What does it produce?** (hand the node its inputs, get back a result)
3. **What changes because of that?** (store the result, unlock the next nodes)

That loop is the entire engine. Everything below is just naming the pieces cleanly so that the
engine can run **any** node without knowing what kind of node it is.

## The one idea: two actors, one narrow contract

There are exactly two kinds of thing, and they meet at a single method.

```
        ┌──────────────────────────── ENGINE ────────────────────────────┐
        │  owns the mutable world:                                        │
        │    • StateManager  — the memory (every node's output)           │
        │    • ReadyQueue    — which nodes can run next                   │
        │    • the graph     — the boxes + arrows (it can GROW mid-run)   │
        │    • the event log + durability (pause / resume)                │
        │                                                                 │
        │  knows about a node ONLY this tiny interface:                   │
        │    node.id, node.params, node.is_spawner, node.run(...)         │
        └───────────────┬─────────────────────────────▲─────────────────┘
                        │  inputs (bound from memory   │  Outcome
                        │  by the engine)               │  (what to do next)
                        ▼                              │
                 ┌─────────────────── NODE ───────────────────┐
                 │  a PURE function of its inputs.             │
                 │  never touches memory / queue / graph.      │
                 │  just returns an Outcome.                   │
                 └────────────────────────────────────────────┘
```

The engine owns *state and scheduling*. The node owns *computation*. Neither reaches into the
other. The node is **pure**: it is handed a plain dictionary of its inputs and returns a value —
it cannot read or write the shared memory. That purity is what makes a run reproducible and
resumable (you can stop after any node and continue later, because all the state lives in one
place the engine controls).

**The key rule: the engine never asks "what kind of node is this?"** It reads inputs, calls
`run_node` (which calls the node's `run`), and looks only at the *shape of the answer*. Adding a
brand-new node kind requires **zero** changes to the engine.

## The cycle: read → eval → apply

Every node goes through the same three steps. The engine does step 1 and step 3, and it *wraps*
step 2; the node's own `run` is the kind-specific core inside that wrapper.

```
   ┌─ pop a node whose inputs are ready ─┐
   │                                     │
   ▼                                     │
  READ        inputs   = state.bind(node.params)   ← engine reads memory into a plain dict
   │                                     │
   ▼                                     │
  EVAL        outcome  = run_node(node, inputs)     ← engine wraps node.run (pure); → an Outcome
   │                                     │
   ▼                                     │
  APPLY       apply(outcome, node, ...)             ← engine writes memory / grows the graph
   │                                     │
   └────────────── repeat ───────────────┘
```

`run_node` is a fixed, kind-agnostic recipe the engine applies to *every* node: check the
pre-conditions, call the node's `run`, offer `on_failure` a chance to recover, check the
post-conditions. The only kind-specific part is `run` itself — see [Nodes](nodes.md).

## `Outcome` — the only thing a node returns, the only thing the engine branches on

A node hands back exactly one of four answers. This closed set is the whole vocabulary between
the two actors:

```
Outcome =
  | Output(value, commit_as=None)  # "here is my result"     → commit under commit_as or node.id, unlock dependents
  | Route(handle)                  # "route only, no value"  → take handle's edge, skip-flood the siblings
  | Pause(reason)                  # "I need to wait/ask"     → checkpoint and stop
  | Grow(subgraph, prune=∅)        # "I am really a subflow"  → splice in more nodes (and retire prune)
```

`Output` covers ordinary steps; `commit_as` (default `None` → the node's own id) lets a subflow's
terminal publish under its spawner (see [Grow](#growing-the-graph-growsubgraph)). `Route` covers a
pure router (CASE): it stores nothing and only selects which out-edge is live — the unselected
branches skip-flood. `Pause` covers a node that must suspend (waiting for a human answer, or a
timer). `Grow` covers a node that *expands into more nodes*; its optional `prune` names a set of
already-committed nodes to **retire** in the same step (a self-respawn loop retires the iteration it
just finished — the inverse of splice) — see below.

## Growing the graph: `Grow(subgraph)`

Some nodes are not a single step — they *are a smaller flow*. "Call another flow", "run this
child once per item in a list" (map), "repeat until done" (loop). Instead of hard-coding each of
these into the engine, such a node returns a **self-describing subgraph** and the engine splices
it in:

```
Subgraph(
  nodes,       # the new boxes to add
  edges,       # the new arrows between them
  wiring,      # where each new box reads its inputs from
)
```

A `Subgraph` is not a new type — it is just a **`Flow`**: the same `nodes`/`edges`/`wiring` core the
top-level flow is built from. Splicing therefore reuses the flow's own construction, and a subflow
node builds its expansion with the same primitives that authored the flow (clone a child flow, or
synthesize one from `__start__` + children + `__end__`).

That is the whole description — no `roots` — because every subgraph obeys one
**convention: it is a well-formed sub-flow with a single `__start__` and a single `__end__`.** From
that, the engine derives the entry, and the terminal carries the reconvergence:

- **entry** is always the subgraph's `__start__` (so `roots` is redundant);
- **reconvergence**: the spawner *bakes* `commit_as=<its own id>` onto the subgraph's terminal node
  (`__end__`). When that terminal runs, its `Output(value, commit_as=<spawner>)` commits under the
  spawner's id via the normal output path — so downstream nodes see one clean output no matter how
  many inner nodes ran. `commit_as` is a field on `Output` (data the engine reads), not a field on
  `Subgraph`, and it replaces the older alias map (one baked redirect, no engine-side alias table).

This one convention unifies **call** (clone a child flow — it already has `__start__`/`__end__`) and
**map** (synthesize a `__start__` that fans out to N children and an `__end__` that collects them
back into a list). The spawner also rehomes its own `post_asserts` onto that terminal, so they fire
in the ordinary `run_node` post-check against the committed value — no alias-site special case.

**Loop folds into the same mechanism.** A loop can't name its `__end__` when it spawns — it doesn't
know which iteration is last until it tests the predicate. So each iteration spawns its body plus a
fresh copy of the loop node, and the loop **bakes `commit_as=<the original loop id>`** into every
copy; the terminating iteration returns `Output(carried, commit_as=<origin>)`, committing under the
original id through the same output path as call/map. The only thing that stays loop-specific is that
its end-of-iteration node is a *hybrid* (continue → `Grow`, stop → `Output`), which is what a loop
fundamentally is — but the *commit* is no longer a special case.

**Growth has one generic inverse: `prune`.** A self-respawn loop would grow without bound if every
iteration's scratch nodes lingered, so the continue arm carries the retirement in its outcome:
`Grow(subgraph, prune=<the finished iteration's ids>)`. The engine applies both halves in one step —
`graph.splice(subgraph)` adds the next iteration, `graph.prune(prune)` retires the one that just
committed its `carried` forward. This stays kind-blind: the engine executes whatever id-set the
outcome names; only the loop knows *which* namespace is spent. `call`/`map` return an empty `prune`
(they add nodes but retire none). Bounding growth top-down is a separate, pure engine backstop
(`MAX_TOTAL_NODES` counts nodes, not kinds); `prune` is the fine-grained per-iteration reclaim.

## The run loop (pseudocode)

```python
def run(flow, inputs):
    state = StateManager(flow.types)      # the typed memory (a.k.a. the pool)
    state.seed(inputs)                    # write the flow's inputs
    queue = ReadyQueue(flow.graph)        # tracks which nodes' inputs are satisfied
    queue.add(flow.roots)                 # the entry nodes

    while (node := queue.pop_ready(state)) is not None: 
        emit(NodeStarted(node.id))
        inputs  = state.bind(node.params, flow.wiring[node.id])  # READ : memory → plain dict
        outcome = run_node(node, inputs, caps)     # EVAL  : asserts + run + on_failure (generic)
        apply(outcome, node, state, queue, flow.graph)   # WRITE

    return state.result()                 # whatever the END node produced


def run_node(node, inputs, caps):         # the generic wrapper — IDENTICAL for every kind
    check(node.pre_asserts, inputs)                    # pure: refs already bound into the inputs dict
    try:
        out = node.run(inputs, **caps)                 # the ONLY kind-specific step
    except Exception as exc:
        out = node.on_failure(exc, inputs, **caps)     # error-strategy hook (default: re-raise)
    if isinstance(out, Output):
        check(node.post_asserts, {**inputs, "output": out.value})
    return out


def apply(outcome, node, state, queue, graph):
    match outcome:                        # the ONLY match in the engine — on Outcome, never on kind
        case Output(value, commit_as):
            target = commit_as or node.id             # a subflow terminal redirects to its spawner
            state.set(target, value)                  # write-once, typed
            queue.done(target)                        # unlock target's dependents (all data out-edges)
            emit(NodeSucceeded(target, value))

        case Route(handle):
            queue.route(node, handle)                 # take the handle's edge; skip-flood the siblings
            emit(NodeRouted(node.id, handle))         # stores no value (CASE is routing-only)

        case Pause(reason):
            checkpoint(state, queue)              # persist everything so we can resume later
            emit(PauseRequested(node.id, reason))
            raise Suspended(node, reason)

        case Grow(subgraph, prune):
            graph.splice(subgraph)                     # add the new boxes + arrows
            graph.prune(prune)                         # retire the finished namespace (∅ for call/map)
            queue.add(subgraph.start)                  # entry is always the subgraph __start__
            emit(NodeExpanded(node.id, subgraph))      # no alias — the terminal carries commit_as=<node>,
                                                       #  so its Output commits back here on the normal path
```

Failures never crash the loop. `run_node` may raise (a failed assertion, a bad LLM response); the
engine wraps the call in one `try/except` that turns any exception into a `NodeFailed` event with a
source locator (so the CLI can point at the offending YAML line). The node gets first refusal via
`on_failure` (default: re-raise) — that is the future home of retry / fallback policy.

The loop above is the **serial** reference engine. A **parallel** engine drains several ready
nodes at once through the *same* read → eval → apply contract; this is safe precisely because nodes
are pure and every write happens write-once at `apply` (which stays serialized). Both engines share
one contract — only how many nodes are in flight at a time differs.

## The three things the engine owns

### StateManager — the memory (the "pool")

Every node's output lives here, keyed by node id, typed and losslessly serializable. It is
**write-once** (a node id is set exactly once) and monotonic, which is what makes checkpoint/resume
trivial.

```
seed(inputs)                 # write the flow's inputs at the start
bind(node.params, wiring) -> inputs  # READ boundary: resolve each input source → a plain dict
set(id, value, shape)        # WRITE boundary: commit a typed output (id = commit_as when a subflow
                             #   terminal redirects to its spawner — no separate alias table)
snapshot() / restore(s)      # durability: the whole memory as a serializable blob
result()                     # the END node's value
```

It knows nothing about node kinds — it stores and hands back typed values.

### ReadyQueue — what can run next

A node is *ready* when every arrow feeding it has delivered. The queue tracks the outstanding
arrows and produces ready nodes in a deterministic order (deterministic so that a resumed run
replays identically).

```
add(nodes)                  # register new nodes and their incoming arrows
pop_ready(state) -> node    # a node whose inputs are all satisfied, or None
done(node)                  # mark produced; take all data out-edges; unlock dependents
route(node, handle)         # CASE path: take the handle's edge, skip-flood the siblings
```

Because `Grow` can add nodes mid-run, the queue must accept new work at any time — but the
*mechanism* is identical for the first node and the ten-thousandth spliced one.

### The graph — boxes and arrows that can grow

Static at authoring time, but a `Grow` outcome splices in more — and, when the outcome names a
`prune` set, retires spent nodes in the same step. Both are generic operations
(`graph.splice(subgraph)` / `graph.prune(ids)`); there is no per-kind growth or GC code.

## What the engine knows about a node — the entire interface

| The engine sees | Meaning |
|---|---|
| `node.id` | where to store its output |
| `node.params` | my declared input *names*; the flow owns their sources (wiring), which the engine binds into the `inputs` dict — the wiring's data edges are also the scheduling dependencies |
| `node.pre_asserts` / `node.post_asserts` | conditions the generic `run_node` checks (purely) before / after `run` |
| `node.is_spawner` | may it return `Grow`? (used only to reject a leaf that grows the graph) |
| `node.run(inputs, **caps) -> Outcome` | the one kind-specific step; the engine calls it via `run_node` |
| `node.on_failure(exc, inputs, **caps)` | error-strategy hook (default: re-raise); the recovery seam |

That is the complete list. No node kind, no map/loop/agent-specific field, is visible to the
engine. The generic wrapper `run_node` and everything kind-specific inside `run` live on the node
side — see [Nodes](nodes.md).

## Design note: a closed match, just on the right axis

An earlier version of the engine branched on node *kind* (`if node.kind == MAP: ...`) in several
places. This design keeps the value of an explicit, closed `match` — but moves it to the right
axis: the engine matches on the **`Outcome`** (four arms), not on the kind (a dozen and growing).
Kind-specific behavior becomes ordinary polymorphism inside each node's `run`. New kinds extend
the node side; the engine never changes. Success-path routing that once forked on kind — CASE's
branch-and-skip, a subflow's commit-under-spawner — is now data on the `Outcome` (`Route`'s handle,
`Output`'s `commit_as`), so it rides the same four-arm match.
