# Reference — engine internals

Lookup companion to [`SKILL.md`](SKILL.md). The skill is the *workflow* (design
from the functional model → fit invariants → plan → implement); this is the
quick-reference and the [`templates/`](templates/) index. The canonical design
text is [`src/agent_composer/README.md`](../../../src/agent_composer/README.md);
the authoring surface is [`docs/syntax.md`](../../../docs/syntax.md).

## Templates

| Template | Use |
|----------|-----|
| [`templates/node_kind/node.py.template`](templates/node_kind/node.py.template) | the `Node` subclass skeleton |
| [`templates/node_kind/__init__.py.template`](templates/node_kind/__init__.py.template) | the package charter / re-export |
| [`templates/node_kind/test_node.py.template`](templates/node_kind/test_node.py.template) | unit + load/run test skeleton |
| [`templates/node_kind/WIRING.md`](templates/node_kind/WIRING.md) | the cross-file edits to make `kind: xxx` authorable |

## The node contract (the most portable idea)

A node is a **pure function of its bound input record**. It implements
`run(inputs, **caps) -> NodeResult` and returns ONE of the closed sum:

| Return | Meaning |
|--------|---------|
| `Output(value, handle=None)` | the one produced value; the engine writes it under the node id. `handle` is set only by routing (the chosen case). |
| `Pause(reason)` | a leaf wait (HUMAN_INPUT / WAIT / agent control-pause). The engine emits `PauseRequested` and suspends; the answer is delivered as this node's `Output` (the node never re-runs). |
| `Enqueue(target, inputs)` | grow the live graph — a description the engine splices in (the REF/MAP drivers, agent control-pause). |

A streaming kind is a generator that yields `StreamChunk` and *returns* a
`NodeResult`. **Failure is not a variant** — a node `raise`s and the engine
boundary turns it into `NodeFailed`.

**Invariants:** a node never receives the pool (the `eval_node` seam binds its
inputs); a node never writes the pool (it *describes* `Output(value)`, the engine
performs the write). Keeps nodes pure and the state immutable (`let`-bindings).

## `NodeKind` (closed vocabulary)

Dispatch is an explicit `match`, never a registry/metaclass.

| Authorable leaves | Internal-only (loader-synthesized / runtime-expanded) |
|-------------------|-------------------------------------------------------|
| `AGENT`, `CODE`, `MODEL`*, `TOOL`, `IF_ELSE` (`case` desugars to it), `HUMAN_INPUT` | `WAIT`, `START`, `END`, `CALL`, `MAP`, `LOOP` (reserved) |

\* `MODEL` parses but `run` raises — the ML-serving seam was removed as dead
plumbing; re-add when real serving lands.

## OCaml analogue map (design from this)

| Our construct | OCaml concept | Why it holds |
|---------------|---------------|--------------|
| a flow | a function `'a -> 'b` | typed in, typed out; composes |
| an agent | a flow whose leaf is an LLM loop | no special contract |
| `call:` / `uses:` | function application / a module ref | nests to any depth |
| the variable pool bind | `let (node_id, key) = ...` | immutable, no mutation |
| `NodeKind` + `match` | a variant + exhaustive `match` | closed set, no registry |
| `Output \| Pause \| Enqueue` | a sum type (the result) | failure is a `raise`, not a case |
| pause / resume | algebraic effect + handler | node *performs* a pause; the scheduler *handles* it |
| a package charter (`__init__`) | a module signature (`.mli`) | a narrow declared interface |

When borrowing runtime mechanics from a prior worker engine: **borrow** the
correctness-critical parts (single-writer dispatcher, 3-state edge join,
outputs-before-successors, recursive skip-flood, layered checkpoint, discriminated
pause reasons); **drop** scale/framework baggage (external DBs, dynamic worker
scaling, plugin registries, heavy layering, multi-tenancy).

## Non-negotiable invariants (the keep/simplify/drop bar)

A change is a design smell if it breaks any of these — most are the functional
model made enforceable.

- **Deterministic structure** — the author fixes the call graph; the LLM fills
  leaf boxes. A flow never rewrites itself. No agentic routing.
- **A flow is a function** — explicit typed input/output signature; an agent gets
  no special contract.
- **Composable / recursive** — a node can *be* a flow (`call:`/`uses:`), nestable
  to any depth. Never assume a node is a leaf; preserve recursion through
  compile + run + checkpoint.
- **Privileges no output type or domain** — prefer the most general primitive that
  composes over a use-case-specific feature.
- **Node never writes the pool** (purity); **typed, losslessly-serializable state**
  (`Segment` / `TypedVariablePool` — the basis for checkpoints + `${...}` refs).
- **Durable suspend/resume** — a node performs a pause; the run serializes to a
  `RunCheckpoint`; an external scheduler resumes (re-run-on-resume).
- **Dependency-light core** — no DB / heavy frameworks; external capabilities enter
  through injected seams (plain callables). *Exception:* the AGENT node imports
  langchain + `llm_clients` and builds its model via `model_from_config`.
- **`llm_config` cascade resolved once at run start** — `resolve_llm_cascade`
  (`compile/`) walks the static call tree top-down, per-field fill-the-gap
  (most-specific wins), deep-copying each CALL/MAP child for per-callsite isolation,
  and bakes the effective dict onto every `AgentNode`. The CLI config is the outermost
  layer; env defaults stay in `model_from_config` (applied last). On a **durable resume
  it must run BEFORE `FlowEngine.restore`** — restore's replay re-clones children from
  the static graph, so the effective configs must be baked on first.
- **Closed `NodeKind` + explicit `match`**; **single-writer** (workers are pure
  executors, the dispatcher is the only mutator); **single-process CLI target**.

## Layer ladder (where code goes)

```
events  <-  state  <-  nodes  <-  compile  <-  compose  <-  runtime  ->  suspension
                        ^   ^
            expr  ──────┘   └──────  llm_clients     (both leaves, imported by nodes upward)
```

Arrows never reverse: a package imports only lower-level or peer packages. See the
`structure` skill. An upward import means the code is in the wrong package (extract
the shared contract to `common.py` / a leaf, or invert via a seam).

## Design-note template (step 3 of the workflow)

Before coding any engine change, write this short note and confirm any non-obvious
choice (CLAUDE.md "ask when uncertain"):

```
Construct:    <name> — what it consumes -> what it produces (its type signature)
OCaml analogue: <the concept you're borrowing> — how it maps to nodes/pool/runtime
Keep / drop:  <what you borrow from prior engines; what scale baggage you drop>
Lands in:     <layer/package on the ladder>  (new package? write its charter)
Seam:         <injected callable, if it touches an external dependency> | none
Invariants:   <which of the non-negotiables it touches; how it stays within them>
Tests:        <the tests/engine cases that will prove it>
```
