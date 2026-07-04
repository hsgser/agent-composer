# Nodes

> **Target design.** This is the `NodeBase` contract the code is being refactored *toward* — a
> clean redesign, not today's code. Refactor items: [Roadmap → TODO](backlog/TODO.md). For the
> other half of the contract (how the engine drives nodes), see [The Engine](engine.md).

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

- **Assertions are pure.** They evaluate against the `inputs` (a plain dict), never the shared
  memory. This works because the engine bound *everything an assertion mentions* into the inputs
  before calling — so even an assertion that references another node reads it as an ordinary
  dictionary entry. No node needs special access to memory, not even the END node.
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
                           commit_as=self.id,           # its Output publishes under THIS map node
                           post_asserts=self.post_asserts)  # my post-conditions ride the terminal
        return Grow(Subgraph(                           # a well-formed sub-flow: __start__ … __end__
            nodes = [start] + children + [end],
            edges = fan_out(start, children)            # __start__ → each child
                  + fan_in(children, end),              # each child's result → __end__
        ))                                              # entry = __start__; __end__ commits via commit_as

class LoopNode(NodeBase):              # SUBFLOW — repeat until done (self-respawn)
    is_spawner = True
    def run(self, inputs, **caps):
        if self.should_stop(inputs):                    # HYBRID: stop → leaf-like Output that commits
            return Output(inputs["carried"],            # under the ORIGINAL loop id via commit_as;
                          commit_as=self.origin_id)     # `carried` arrived via ordinary wiring
        body = clone(self.body, inputs["k"], bake(inputs))
        next = self.respawn(k=inputs["k"] + 1,          # continue → a fresh copy of myself,
                            origin_id=self.origin_id)   # carrying the ORIGINAL loop id forward
        return Grow(Subgraph(                           # entry = body's __start__; no committing
            nodes = [body, next],                       # __end__ — the commit happens later, when a
            edges = [edge(body.end, next)],             # body __end__ → next's `carried` (normal
        ),                                              # wiring); terminating iter commits via commit_as
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

## Why this shape

- **One method to add a kind.** Implement `run`; the engine's `run_node` supplies assertion
  checking and error funnelling unchanged.
- **Purity end to end.** Nodes never see shared state, so runs are reproducible and resumable.
- **The engine stays kind-blind.** All that varies between kinds is the `Outcome` a `run` returns —
  and the engine branches only on that ([Outcome](engine.md#outcome--the-only-thing-a-node-returns-the-only-thing-the-engine-branches-on)),
  never on the kind.
