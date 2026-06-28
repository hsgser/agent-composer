# DEFER

Open questions and trade-offs we're **thinking about but haven't decided** — each needs a
decision before it becomes a [TODO](TODO.md). Not a committed v2 plan ([FUTURE](FUTURE.md)).

This directory (`docs/backlog/`) is tracked in git and published in the doc site under "Roadmap".

---

## Engine bugs surfaced but deferred

- [ ] **A control-call id containing `.` breaks producer parsing / re-homing.** An AGENT control-call
  (or any node) whose id contains `.` is mis-split by `.output`-based producer parsing, minting a
  malformed `Edge(from_=None)`. Bites the live single-level agent pause. Decide: sanitize/assert
  `.`-free control-call ids, or `rpartition('.output')` in both the producer-of and internal re-homing
  helpers.

## Engine design forks (undecided)

- [ ] **A loaded flow is single-use — expansion mutates `loaded.compiled` in place.**
  `run_flow(loaded, …)` grows the *shared* `loaded.compiled` (subgraph expansion appends an append-only
  overlay), so re-running the SAME `LoadedFlow` sees the prior expansion. Fine for a one-shot run;
  **wrong for load-once-run-many** (a long-lived process loads a flow once and runs it per request).
  Decide: `run_flow` deep-copies the compiled flow per run, OR resets/discards the overlay between
  runs, OR the engine expands into a per-run copy and never touches `loaded.compiled`. (Lean: per-run
  copy in `run_flow`.)

- [ ] **Seam-injection timing.** Injected seams bind at **compile/load** time, so a
  `CompiledFlow`/`LoadedFlow` is bound to one set of seams. Open: inject at **run** time so one
  compiled artifact runs under different clients (real vs dummy, per-tenant) without recompiling?
  Trade-off: node self-containment vs artifact reusability.

- [ ] **Inline CODE source (sandbox + trust model).** CODE is `module:function` only; inline `exec`
  is RCE the moment a flow isn't run by its author. Decide the trust model ((A) single-tenant self-run
  → unsandboxed-behind-opt-in; (B) shared/deploy → sandbox first), then add a `CodeExecutor` seam.

- [ ] **Tighter required contract (low priority).** A required child input BOUND to an explicit null
  (a present edge resolving `None`) reaches the body as `None` silently: the synthesized START's
  presence-gated required-check only fires for an OMITTED input. Consistent with `f(x=None)`; a
  stricter contract would need a bound-null-required guard.

- [ ] **Binding present-`None` vs missing.** Binding treats a resolved `None` as unbound (a required
  input from a node that genuinely emitted `None` raises; a `default` overrides a real `None`). Root
  cause is the pool's "missing → `None`" resolve. Needs a pool API that distinguishes absent from
  present-`None` (a sentinel). Edge case.

- [ ] **MAP `over` output-key naming.** A `MAP` aggregates via one list-mode `END`; the value rides the
  map node's bare `${<map>.output}` (a `list[U]` in `over` order). Index-keyed outputs were rejected
  (N is run-time). Cosmetic; revisit.

## Type system tails

- [ ] **`dict[K, V]` full key/value typing** — no `parse_type`/`Shape` branch yet.
- [ ] **`enum` flow inputs** still map to `type: string` + `options` (a pragmatic stopgap until the
  type registry makes `enum` a first-class variant).

## External references (`uses:` / paths)

- [ ] **Path-traversal / sandbox safety** — `..`-escape + absolute `system.paths`/`uses:` entries are
  joined as-is (relative-only is the intent); add a trust/sandbox stance for third-party flows' CODE
  nodes before remote pulls land.
- [ ] **Multi-version selection** — beyond exact `<path>@<version>.yaml` filename match (ranges/latest).

## Structured AGENT output — wire the declared shape into generation

There are three parts to "an AGENT produces its declared output": **(a) declare** the type
(`output:` ✓), **(b) enforce** it (the write boundary fails a mismatch ✓), and **(c) generate** it —
*teach/constrain the model to emit that shape*. **(c) is missing.** Today every AGENT mode returns a
plain string, `invoke`d with no structured-output constraint (`modes/plain.py`,
`modes/tool_calling.py`); the declared `output:` Shape is never passed to the model. So an AGENT
declaring a record/typed output just **fails at the boundary**.

**Design space (decide):** derive a JSON schema from the node's `output:` Shape, then —
- **native structured output** — langchain `with_structured_output(schema)` / provider
  `response_format` (best fidelity where supported);
- **tool-forcing** — bind a single "emit" tool whose args are the schema + force `tool_choice`;
- **prompt-injection** — render the schema + "respond with JSON matching this" + parse (universal
  fallback, least reliable);
- **parse + retry** — parse text→shape, retry-with-error on failure.

Open: which modes get it (plain vs tool_calling), per-provider capability detection + fallback chain,
and the relationship to the boundary check. The **tool** half (below) and the FUTURE
"structured AGENT output" item are the same theme. Needs a design pass (engine skill).

## Agent memory mechanisms

An AGENT today is effectively a **bare, stateless LLM** per run (the `tool_calling` mode keeps only a
*within-run* conversation memo in a private pool namespace, for re-run-on-resume replay — not a memory
feature). We want pluggable memory: **bare LLM** (no memory), **reflection** (the agent
critiques/condenses its own context), **long-term memory** (a persisted store the agent reads/writes),
**accumulated across runs/time**.

**The fork — where does it live?**
- **A new `memories/` package** (an orthogonal axis to `modes/`) + a node `memory:` knob — memory is
  arguably *orthogonal to the loop*, so a reflection/long-term memory should compose with *any* mode
  (a `MEMORIES` registry like `MODES`, selected per AGENT).
- **A mode in `modes/`** — simpler, but conflates two axes (loop × memory) and combinatorially
  explodes.

**Open:** the abstraction (a `Memory` protocol: `load(ctx)->context` + `write(ctx, result)`?);
short-term vs long-term — unify or keep separate?; cross-run persistence needs a **store seam** (ties
to the server/durable story — [FUTURE](FUTURE.md)); purity. Lean: memory is a separate axis. Needs a
design pass.

## Contract gaps (decide the shape)

- [ ] **No typed-output contract on tools** — tools return arbitrary `str` (`StructuredTool` infers
  only the *input* schema; the return is stringified). The tool half of the structured-output theme.
- [ ] **Typed tool args** — `ToolCall.args` is an untyped `name→source` dict (binder uses `type=None`).
  A typed `inputs: list[IOField]` on `ToolCall` would type-check tool args.

## LLM config — flow-level requirement + cascade + lock

Today `llm_config` is **per-AGENT only** (a raw dict defaulting to `{}`). There is **no flow-level
config and no propagation** — `LLMConfig`'s "unset fields inherit from global defaults" is
aspirational. So an AGENT with no `llm_config` relies entirely on whatever `model_from_config`
defaults at runtime; a flow can silently run on an unintended model.

**Proposal (decide the shape):**
- **Require `llm_config` at least at the flow level** — a top-level section; loading an AGENT-bearing
  flow with no resolvable config is loud.
- **Cascade** — resolve a node's effective config most-specific-first: node → (sub)flow → parent flow
  (a `call`/`uses:` child inherits the parent's flow-level config when it sets none).
- **Lock vs overridable (the open fork)** — let the author choose per config whether it is **fixed**
  (a caller cannot override it) or **default + overridable**. Open: the surface (a `lock: true` flag?),
  precedence, whether per-field locking is needed.

Needs a design pass (engine skill → cascade semantics from the functional model) before it's a TODO.

## Integration knobs (undecided)

- [ ] **`LLMConfig.provider` Literal vs factory drift** — the config Literal and the set of providers
  `create_llm_client` actually supports can drift. Sync the Literal to the factory, or keep curated +
  document.
- [ ] **`DEFAULT_SYSTEM` contradicts `ask_user`** — the hardcoded system prompt ends with "Do not ask
  the user questions" while granting the `ask_user` control tool. Make the system prompt controls-aware.
- [ ] **`ask_user` follow-ups** — surface the injected-answer pool location on the pause reason; >1
  control-tool call per model turn unsupported.
- [ ] **Ollama reasoning capture for OpenAI-compat reasoning models** — Ollama uses its native client
  with `reasoning=False`; a generic reasoning-capture for the `/v1` path is separate work.

## Tooling

- [ ] **Gate CI on pyright?** — once pyright is wired to the project env (see TODO) and the genuine
  errors are triaged, decide whether to make it a CI gate.

## Flagged-not-adopted (revisit)

- `case`-as-value-expression (SQL `CASE…END` returning a value — would remove the join-coalesce).
- A small builtin set (`len`, …) in `when:`/`asserts:`.
- No-colon interpolation variants (`${X-d}`). (`${X:+alt}` was dropped; kept `:-`/`:?`/`|`/`$$`.)

## Doc deferrals

- [ ] **defs-internal error line-mapping** — a nested def's internal errors are unlocated (top-level
  stays located); compute nested line maps from the parent compose tree later. (Hard, low value.)
  Same class: synth inline-call downstream errors are unlocated.
