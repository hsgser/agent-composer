# Nodes

> This is the `NodeBase` contract the code implements today. Each kind supplies `run` plus a few
> static traits/hooks; the engine core reads only those and the returned `Outcome`, never the node's
> kind. For the other half of the contract (how the engine drives nodes), see [The Engine](engine.md).

## What a node is (for a reader who has never seen this project)

A flow is a graph of steps. **A node is one step.** "Ask an LLM", "run a Python function",
"repeat until good enough" — each is a node.

The single most important property: **a node is a pure function.** The engine reads the node's
inputs out of shared memory and hands them over as a plain dictionary. The node
computes and returns an answer. It **cannot** read or write the shared memory, touch the queue, or
change the graph. It only receives inputs and returns an `Outcome`.

```
        inputs (plain dict)                      Outcome
              │                                     ▲
              ▼                                     │
        ┌──────────────────────────────────────────────┐
        │                    NODE                        │
        │   pure: inputs in → Outcome out                │
        │   no access to memory / queue / graph          │
        └──────────────────────────────────────────────┘
```

Why so strict? Because purity is what makes a run **reproducible** (same inputs → same result) and
**resumable** (stop after any node and continue — all the state is in the engine's memory, none is
hidden inside a node).

## The contract: what every node must provide

Each kind implements exactly one method, `run`, and declares a few static fields. That is the
*whole* node contract. The engine wraps `run` in a generic recipe (`run_node`, which lives in the
engine — see [The Engine](engine.md#the-cycle-read--eval--apply)); the node never writes that
recipe itself.

```python
class NodeBase(ABC):
    id: NodeId                       # where my output is stored
    params: list[ParamDecl]          # my declared input NAMES only — the flow owns their sources
    pre_asserts:  list[Expr]         # conditions the engine checks BEFORE running me
    post_asserts: list[Expr]         # conditions the engine checks AFTER I run
    is_spawner: bool = False         # may I return Grow (expand the graph)?

    # --- read-boundary hooks (how the engine binds my inputs) -------------------
    binds_per_item: bool = False     # bind my inputs PER ELEMENT (map), not once up front
    def bind_reserved(self, wiring, pool) -> dict:  # reserved keys to pre-resolve before run
        return {}                    # e.g. wait -> {"until": ts}; map -> {"over": [...]}
    def reserved_wiring_keys(self) -> set:  # the NAMES of those reserved keys (load-time; no pool)
        return set()                 # e.g. timed wait -> {"until"}; map -> {"over"}

    # --- growth hooks/traits (only read for a spawner) --------------------------
    grow_depth_delta: int | None = None  # REF-depth increment: 1 call/map, 0 agent, None loop
    grow_restamps_self: bool = False     # also stamp my own id on a grow (agent re-pause)
    is_loop: bool = False                # I drive fixpoint iteration (loop bookkeeping gate)
    def iter_boundary_records(self, seed) -> list:  # records to eager boundary-check before splice
        return []                    # call -> one; map -> one per element; agent/loop -> none

    @abstractmethod
    def run(self, inputs, **caps) -> Outcome:  # the ONE thing each kind implements
        ...

    def on_failure(self, exc, inputs, **caps) -> Outcome:  # error seam (default: re-raise)
        raise exc
```

`run` receives two things from the engine:

- **`inputs`** — the node's params, already resolved from memory into a plain dict (*never* the
  memory itself). This is what keeps the node pure. The node declares only the param *names*
  (`params`); the **flow** owns where each one reads from (its wiring), and the engine binds the two
  together into this dict before calling. (Node and flow stay separate: a node never carries its own
  sources.)
- **`caps`** — *capabilities*: the few side-effecting helpers a pure `inputs` dict can't carry — the
  LLM client, a mapped call's per-item binder, etc. It arrives as keyword arguments (`**caps`), so a
  node reaches a helper by name — `caps["llm"]`, `caps["bind_item"]`. The engine builds them per call
  and owns their lifecycle; a node that needs no effects simply ignores `caps`.

**Why only `run` (and not a full `eval`)?** The wrapper that checks pre/post assertions, funnels
errors, and shapes the result is **byte-identical for every kind** — so it is *protocol*, not node
behavior, and lives on the engine as `run_node`. The node contributes only the one part that
actually differs between kinds (`run`) plus an optional recovery hook (`on_failure`). Folding the
wrapper into each node would force every kind to re-implement the same machinery.

## The engine wraps every `run`; `run` is the kind-specific hole

The engine applies a fixed recipe to every node — `run_node`. It checks the pre-conditions, runs
the kind-specific body, checks the post-conditions, and returns. The only variable part is `run`.
`run_node` lives in the engine (it is the same for all kinds); a node supplies just `run` and,
optionally, `on_failure`.

```python
# in the engine — the same wrapper for every node kind
def run_node(node, inputs, caps) -> Outcome:
    check(node.pre_asserts, inputs)                # 1. pre-conditions (pure)
    try:
        out = node.run(inputs, **caps)             # 2. the kind-specific step (THE node's job)
    except Exception as exc:
        out = node.on_failure(exc, inputs, **caps) # 3. error policy (default: re-raise)
    if isinstance(out, Output):
        check(node.post_asserts,                   # 4. post-conditions (pure)
              {**inputs, "output": out.value})
    return out

def check(asserts, scope):                         # pure: raises NodeAssertFailed(id, expr) on fail
    for a in asserts:
        if not evaluate(a, scope):
            raise NodeAssertFailed(scope["__id__"], a)

# on the node — the only recovery seam a kind may override
def on_failure(self, exc, inputs, **caps) -> Outcome:
    raise exc                                       # default; override later for retry/fallback
```

Two things make this clean:

- **Assertions are pure.** They evaluate against the bound `inputs` (a plain dict), never live
  shared memory. Each referenced path resolves **record-first, pool-fallback**: a declared input (or
  the synthetic `${output}` the post-check injects) reads from the record, and any other head — a
  namespaced cross-node ref — falls back to a read-only pool lookup. That fallback is what lets a
  flow's END terminal check refs to other nodes with **no special case** — END is an ordinary node
  to the assert path.
- **Failures are just exceptions.** `run` raises; `run_node` offers `on_failure` a chance to
  recover; otherwise the exception propagates to the engine, which turns it into a `NodeFailed`
  event with a source locator. The node never emits events.

## Two families of node

Every kind is one of two shapes. This split is a *concept*, not a class hierarchy — it is simply
what a node's `run` returns.

```
                         NodeBase
                             │
        ┌────────────────────┴────────────────────┐
        │                                          │
    LEAF node                                 SUBFLOW node
  is_spawner = False                        is_spawner = True
  run → Output | Pause | Route              run → Grow(subgraph)
        │                                          │
  a single unit of work                    "a node that IS a smaller flow":
  (agent, code, model, tool, case,         it expands into  __start__ → children → __end__
   start, end, human_input, wait)          (call, map, loop)
```

- A **leaf** does one thing and returns an `Output` (or `Pause` if it must wait). Most kinds are
  leaves.
- A **router** (`case`) is a leaf that returns `Route(handle)` instead of an `Output`: it produces
  no value, it only selects which out-edge is live (the rest skip-flood). It is still
  `is_spawner = False` — routing is not growth.
- A **subflow** is a node that stands in for a whole sub-graph. Its `run` returns a `Grow`
  describing the boxes and arrows to splice in; by convention that sub-graph has a single
  `__start__` (its entry) and a single `__end__` (whose result becomes this node's result via the
  `commit_as` baked on it), so nothing extra needs naming. The engine splices it — see
  [Grow](engine.md#growing-the-graph-growsubgraph).

## Concrete kinds are just a `run`

```python
class CodeNode(NodeBase):              # LEAF — run a Python function
    def run(self, inputs, **caps):
        return Output(self.fn(inputs))

class AgentNode(NodeBase):             # LEAF — one LLM step (may need to wait → Pause)
    def run(self, inputs, **caps):
        # self.llm_config = WHICH model (pure config, baked on the node, serializable);
        # caps["llm"]     = the live client the engine built from that config.
        answer = caps["llm"](self.prompt.format(inputs))
        return Output(answer)

class CaseNode(NodeBase):              # ROUTER — pick a branch, produce no value
    def run(self, inputs, **caps):
        return Route(self.select(inputs))               # handle names the live out-edge; siblings skip

class MapNode(NodeBase):               # SUBFLOW — run a child once per item
    is_spawner = True
    def run(self, inputs, **caps):
        start    = Start()                              # single entry; fans out to the children
        children = [clone(self.body, i, bake(item))     # one clone per element of `over`
                    for i, item in enumerate(inputs["over"])]
        end      = ListEnd(n=len(children),             # __end__: gathers the N results into a list
                           commit_as=self.id)           # its Output publishes under THIS map node;
                                                         #   the map's own post_asserts are re-checked
                                                         #   at the commit site, not rehomed here
        return Grow(Flow(                               # a well-formed sub-flow: __start__ … __end__
            nodes = [start] + children + [end],
            edges = fan_out(start, children)            # __start__ → each child
                  + fan_in(children, end),              # each child's result → __end__
            start_id = start.id, end_id = end.id,       # single entry; __end__ commits via commit_as
        ))

class LoopNode(NodeBase):              # SUBFLOW — repeat until done (self-respawn)
    is_spawner = True
    def run(self, inputs, **caps):
        if self.should_stop(inputs):                    # HYBRID: stop → leaf-like Output that commits
            return Output(inputs["carried"],            # under the ORIGINAL loop id via commit_as;
                          commit_as=self.origin_id)     # `carried` arrived via ordinary wiring
        body = clone(self.body, inputs["k"], bake(inputs))
        next = self.respawn(k=inputs["k"] + 1,          # continue → a fresh copy of myself,
                            origin_id=self.origin_id)   # carrying the ORIGINAL loop id forward
        return Grow(Flow(                               # entry = body's __start__; no committing
            nodes = [body, next],                       # __end__ — the commit happens later, when a
            edges = [edge(body.end, next)],             # body __end__ → next's `carried` (normal
            start_id = body.start_id, end_id = body.end_id, # wiring); terminating iter commits via commit_as
        ),
        prune = self.own_ids)                           # retire THIS iteration's scratch (the inverse
```

Notice what is **not** here: no node reads the pool, emits an event, or checks its own success/
failure plumbing. Each kind expresses only its own logic. The map node does not know how a run is
scheduled; the loop node does not know how state is stored.

## The static fields, briefly

| Field | What it is for |
|---|---|
| `params` | my declared input *names*; the **flow** owns their sources (wiring) — the engine binds the two into the `inputs` dict, and the wiring's data edges are the node's scheduling dependencies |
| `pre_asserts` / `post_asserts` | conditions checked (purely) before / after `run`; a failure becomes a `NodeFailed` |
| `is_spawner` | declares "I may return `Grow`"; lets the engine reject a leaf that tries to grow the graph |
| `binds_per_item` | declares "bind my inputs PER ELEMENT via a `bind_item` cap" (map); the read seam then starts my record empty instead of binding `params` once |
| `bind_reserved(wiring, pool)` | reserved input keys the read seam pre-resolves before `run` (timed `wait` → `until`, `map` → `over`); default `{}` |
| `reserved_wiring_keys()` | the NAMES of those reserved author-wiring keys — the load-time counterpart of `bind_reserved` (no pool). Compile passes (`check_wiring_parity`, ref-scan) read it instead of dispatching on `node.kind`; timed `wait` → `{"until"}`, `map` → `{"over"}`; default `∅` |
| `iter_boundary_records(seed)` | the input records the growth core eager-checks against my child's boundary asserts *before* splicing; default `[]` (no check) |
| `grow_depth_delta` | my REF-depth increment for the growth core: `1` (call/map, bounded), `0` (agent), `None` (loop / non-REF, no depth work) |
| `grow_restamps_self` | declares "on a grow, also stamp my own id" (agent re-pause nesting); default `False` |
| `is_loop` | declares "I am the fixpoint-iteration driver"; gates the loop-only per-iteration bookkeeping in the growth core; default `False` |
| `needs_llm` | declares "I am LLM-backed" (agent); the read seam then builds the `caps['llm']` model-factory cap and passes it to `run`; default `False` |

## Why this shape

- **One method to add a kind.** Implement `run`; the engine's `run_node` supplies assertion
  checking and error funnelling unchanged.
- **Purity end to end.** Nodes never see shared state, so runs are reproducible and resumable.
- **The engine stays kind-blind.** All that varies between kinds is the `Outcome` a `run` returns —
  and the engine branches only on that ([Outcome](engine.md#outcome--the-only-thing-a-node-returns-the-only-thing-the-engine-branches-on)),
  never on the kind.
