# DEFER

Open questions and trade-offs we're **thinking about but haven't decided** — each needs a
decision before it becomes a [TODO](TODO.md). Not a committed v2 plan ([FUTURE](FUTURE.md)).

This directory (`docs/backlog/`) is tracked in git and published in the doc site under "Roadmap".

---

## Engine bugs surfaced but deferred

- [ ] **Cross-version durable resume is not guaranteed (surfaced 2026-07-05, P10 Flow unification).** A
  run suspended on an older binary and resumed on a newer one can re-grow a subgraph window whose node
  topology changed between versions — e.g. post-P10 MAP grows a `map#/__start__` node absent from the
  pre-P10 restored pool. Intra-version replay parity holds (the ledger serializes `GrowRecord` + seed and
  the builder rebuilds the same window on the same binary). Fixing cross-binary migration would need a
  ledger schema version + migration shims; deferred as out of scope for the young engine.

- [ ] **`and`/`or`/`not`/`in` are not reserved words in the unified `${}` grammar** — the LALR lexer
  resolves them contextually: in operator position they lex as the keyword token, in operand position as
  a plain `NAME`/ref. So `${x and and}` parses (the 2nd `and` is a ref named `and`), and bare `${and}`
  resolves a pool variable named `and`. Consequence: a malformed boolean like `x and and` silently
  parses instead of erroring, and a variable may shadow a keyword. Harmless today (no one names a var
  `and`; the condition evaluator is unaffected because it keys off token TYPE), but it weakens
  malformed-expression detection. Decide whether to reserve `and or not in true false null` as keywords
  (rejecting them as identifiers) or keep the ergonomic looseness. (Surfaced 2026-07-03 during
  expr-unification Step 9.)

- [ ] **Prompt strict-floor asymmetry on an explicit `${null}`** — under STRICT_RAISE prompt rendering,
  a whole-single-span `${null}` (a genuine null VALUE, not a missing ref) RAISES (a prompt can't be
  None), but the SAME `${null}` embedded in a multi-span prompt (`x ${null} y`) silently stringifies to
  `""` → `"x  y"`. Missing refs already raise consistently in both single- and multi-span; this only
  affects an *explicit* null literal (or an expression legitimately computing to null). Pre-existing and
  narrow. Decide whether an explicit null in a prompt span should raise, render `""`, or render the
  literal — then make single- and multi-span agree. (Surfaced 2026-07-03 during expr-unification Step 8.)

- [ ] **`tool_calling` structured final turn double-invokes the model** — on the final turn the loop
  calls the model to discover there are no more tool calls (`nodes/agent/modes/tool_calling.py:89`),
  which returns *prose*; `generate_structured` then invokes again to emit the declared shape. Two calls
  per structured final answer. **Largely inherent, not a clean fix:** for native providers
  (Anthropic/OpenAI — the common case) `with_structured_output` *must* do its own schema-bound invoke;
  there is no API to convert already-returned prose into a validated typed value without another call.
  The only real wins are narrow — the degenerate no-tools `tool_calling` config (skip discovery, go
  straight to `generate_structured`) and the fallback path when the prose happens to already be valid
  JSON. Revisit only if a provider gains combined tools+structured binding, or the no-tools case proves
  common enough to special-case.

- [ ] **A control-call id containing `.` breaks producer parsing / re-homing.** An AGENT control-call
  (or any node) whose id contains `.` is mis-split by `.output`-based producer parsing, minting a
  malformed `Edge(from_=None)`. Bites the live single-level agent pause. Decide: sanitize/assert
  `.`-free control-call ids, or `rpartition('.output')` in both the producer-of and internal re-homing
  helpers.

## Engine design forks (undecided)

- [ ] **Durable CROSS-PROCESS resume of a tool-agent-in-loop is unverified.** The `ac chat` composer
  chat runs a `tool_calling` agent inside a `loop` body; in-process run/resume across turns is proven
  (`tests/engine/test_agent_in_loop.py`), but a mid-loop checkpoint resumed in a FRESH process (the
  durable path) for the agent-in-loop shape has not been verified end-to-end. Confirm (or fix) before
  relying on cross-process durability for the agentic REPL. (Surfaced 2026-07-06, `ac chat`.)

- [ ] **Flow-side `/exit` for `ac chat`.** Today the CLI intercepts `/exit`/`/quit`/EOF and breaks the
  resume loop; the carried `{transcript, exited}` record already leaves `exited` open as a clean seam
  for a flow-side end — a real command (or a tool the agent can call) that SETS the carried `exited`
  field, so the loop's `while: not ${exited}` terminates from inside the flow rather than the host
  intercepting the line. Decide whether to move end-of-session into the flow. (Surfaced 2026-07-06.)

- [x] ~~**`MAX_TOOL_ITERATIONS` (8) is low for the `ac chat` composer assistant.** The composer chat wields
  five flow-op tools; a discover → read → validate → run → answer sequence can approach the cap, and a
  model that waffles hits it and fails the turn.~~ **RAISED to 100 (2026-07-07).** The shared
  `tool_calling` cap is now 100, giving multi-tool agents room to work. Making it *per-node* configurable
  (a general node-config surface, e.g. an `env:`/`config:` field) is tracked separately as a design
  discussion. -- 8542e23

- [x] ~~**`:-` / `:?` RHS: expression vs. legacy bare-literal text.**~~ **DECIDED (2026-07-03): Option A —
  the RHS of `:-` (default) and `:?` (required message) is a **full expression** under the unified
  grammar, uniform with everything else inside `${}`. A bare word is a **reference**; a literal must be
  **quoted** (`${a:-"today"}`, `${a:?"a topic is required"}`). This is an authoring break vs. the legacy
  binding dialect (which treated the RHS as plain literal text), so the expr-unification codemod now also
  rewrites every bare `:-`/`:?` RHS to quoted form across the seeds + tests it touches. Landed as part of
  expr-unification Step 5.

- [ ] **A general flow-local variable scope — needed?** The `loop` node reads its *carried record*
  by **bare name** (`${exited}`) in `until:`/`while:`, consistent with the existing convention that a
  node references its own declared inputs bare (AGENT/HUMAN_INPUT `prompt:`, `case when:`); dotted
  names stay pool refs (`${input.*}` external, `${nodeid.*}` another node). Open question raised
  during LOOP design: do we ever want a **first-class per-flow local scratchpad** beyond
  node-own-inputs? Hard constraint: it must stay **pure** — a READ / functionally-**threaded** scope
  only, **never an ambient MUTABLE pool** that nodes write to (that would break the "a node never
  writes the pool" invariant and kill referential transparency / checkpointing). For non-loop flows
  it is largely redundant with existing `${nodeid.*}` refs. Decide only when a real flow needs it.
  (Raised 2026-07-01 during LOOP design.)

- [ ] **`loop` body partial-update / passthrough.** The first `loop` slice requires the body flow's
  **output shape to equal the carried record** (`inputs:` shape) — the body returns the *full* next
  state; its input may be a subset. A future ergonomic: let the body emit only the fields it
  **changed** (`output ⊂ carried`), with unchanged carried fields flowing through automatically
  (merge semantics). Nice-to-have, but it layers a merge on top of the clean total-threading model —
  defer until a real flow finds re-emitting unchanged fields painful. (Raised 2026-07-01.)

- [ ] **`_grow_loop` spawner-subnode stamping (`_spawner_expansion`/`depth`).** Slice-1 loop bodies
  are leaf-only, so `_grow_loop` does NOT stamp `_spawner_expansion`/`depth` on cloned spawner-eligible
  subnodes (the other `_grow_*` helpers do). An **AGENT-in-loop** body (the `ac chat` case) needs that
  stamping so its pause segments route through `_replay_expansions` correctly. Required before the
  agentic `ac chat` REPL. (Raised 2026-07-02.)

- [ ] **A loaded flow is single-use — expansion mutates `loaded.compiled` in place.**
  `run_flow(loaded, …)` grows the *shared* `loaded.compiled` (subgraph expansion appends an append-only
  overlay), so re-running the SAME `LoadedFlow` sees the prior expansion. Fine for a one-shot run;
  **wrong for load-once-run-many** (a long-lived process loads a flow once and runs it per request).
  Decide: `run_flow` deep-copies the compiled flow per run, OR resets/discards the overlay between
  runs, OR the engine expands into a per-run copy and never touches `loaded.compiled`. (Lean: per-run
  copy in `run_flow`.)

- [ ] **Streaming as an `Outcome` shape — a second output channel.** A node today can produce
  incremental chunks via `_drain_node_generator` (`nodes/base.py:158`) yielding `StreamChunk`, but that
  path is **dormant** (zero producers) and lives *outside* the `Outcome` sum — it is not one of
  `Output | Route | Pause | Grow`. Open question raised during the kind-agnostic redesign: does streaming
  become a first-class `Outcome` arm (e.g. a `Stream(chunks)` / a node that emits many partials then one
  final `Output`), or stay a side channel the engine drains around the pure `run`? A streaming arm
  complicates the "one value committed per node" model (partials aren't committed, aren't checkpointed,
  don't unlock dependents) and the durability/replay story (what does resume replay — the final only?).
  Deferred: no live producer forces the decision yet; revisit when a real flow needs token streaming
  (the `ac chat` REPL is the likely first caller). (Raised 2026-07-04 during kind-agnostic redesign.)

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

- [ ] **Declaring the EXPECTED output shape at a `call` site (opaque/external child).** A `call`
  node's output type is *inherited* from the child flow's declared `output:` — there's no `output:`
  on a `call` (it's a loud "field not allowed"). When you call an external/untyped subflow whose
  terminal declares no output type, `${call.output.field}` reads go lenient (no compile check), so the
  caller has no static way to say "I expect `{label, confidence}`". Today's workarounds: (a) call-site
  `asserts:` reading `${call.output.field}` — they fire loudly at runtime (a missing field fails the
  run, not a silent pass); (b) route the opaque output through a typed *validation/coercion* `code`
  node that re-declares the expected `output:` so the write boundary enforces it. Decide whether a
  first-class affordance is worth it — e.g. an `expect:`/asserted-`output:` on a `call` that
  type-checks (not authors) the child's actual output — vs. leaving it to the two workarounds.

  **Proposed direction (note):** make `output:` *optional* on a `call` (today it's a loud "field not
  allowed"). When present, it is an **author-declared expectation, not an authoring directive**: the
  engine verifies the declared shape matches the child flow's actual declared `output:` and fails the
  *load/compile* with a clear mismatch error if they diverge (a "I expected `{label, confidence}` but
  the child emits `{rating, score}`" diagnostic). When omitted, behavior is unchanged (output type
  inherited from the child). This differs from the leaf-node `output:` (which *declares/coerces* the
  node's own output) — on a `call` it would *check against* the child's contract, not define it. Open:
  how to handle an opaque/untyped child (child declares no `output:`) — degrade to a runtime
  write-boundary check, or require the child to be typed for the `call`'s `output:` to mean anything.

  The mismatch error must be **located** — pointed at the `output:` key on the `call` node in the
  author's YAML (line/column), the same way other compile errors carry a source span — so the author
  sees exactly where the expectation diverges, not just a bare message. (Top-level nodes already stay
  located; this slots into that path, unlike the deferred defs-internal line-mapping below.)

## Type system tails

- [ ] **`dict[K, V]` full key/value typing** — no `parse_type`/`Shape` branch yet.
- [ ] **`enum` flow inputs** still map to `type: string` + `options` (a pragmatic stopgap until the
  type registry makes `enum` a first-class variant).

## External references (`uses:` / paths)

- [ ] **Path-traversal / sandbox safety** — `..`-escape + absolute `system.paths`/`uses:` entries are
  joined as-is (relative-only is the intent); add a trust/sandbox stance for third-party flows' CODE
  nodes before remote pulls land.
- [ ] **Multi-version selection** — beyond exact `<path>@<version>.yaml` filename match (ranges/latest).

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

## LLM config — per-field inherit opt-out (deferred extension)

The cascade (per-field fill-the-gap, most-specific wins), optional flow-level config, whole-node
`inherit: false` opt-out, and CLI config injection are **decided** and tracked in [TODO](TODO.md).

Deferred here: **per-field** inherit control. `inherit: false` is all-or-nothing — it drops the node
out of the whole cascade. A finer knob ("inherit everything except `temperature`", or "pin only
`model` and let the rest cascade") is possible but adds surface and precedence questions. Revisit only
when a real flow needs partial inheritance.

Also deferred: **persisting the CLI config in the checkpoint.** The CLI cascade layer
(`--provider`/`--model`) is not serialized into a checkpoint, so a cross-process durable resume must
re-supply it via `resume_flow(..., llm_config=...)` (it is re-applied before `restore`). Baking it into
the checkpoint would remove that host obligation but couples the persisted run to a CLI-time choice.

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

- [x] ~~**defs-internal error line-mapping** — a nested def's internal errors are unlocated (top-level
  stays located); compute nested line maps from the parent compose tree later. (Hard, low value.)
  Same class: synth inline-call downstream errors are unlocated.~~ -- DONE: a namespaced node failure
  now renders a Python-traceback-style STACK of boxed `.yaml` frames descending into the `defs:` /
  external `uses:` child down to the ACTUAL failing node (not just the owning call node) — parser
  `def_node_lines`/`def_node_field_lines` + a render-only `SourceFrame` on each call/map node's
  `child_source`, walked by `cli/run.py:_walk_call_frames`. See TODO "Multi-frame call traceback". -- e801d26

- [ ] **Line-precise vs. node-precise compile-error highlight.** The CLI renders a `LoadError` as a
  boxed `.yaml` source frame with the offending line highlighted (`cli/run.py:_render_load_error`,
  via `rich.Syntax` + `Panel`), but `LoadError` carries only `.line` (not a column), and many errors
  locate to the *node's* declaration line rather than the precise binding line — so the highlight can
  land on `  b:` instead of the `  brief: ${frame_typo.output}` line that actually names the bad ref.
  Tightening this would need finer line/column tracking threaded through the ~74 `LoadError` raise
  sites (the parser already has `start_mark.column`). Decide if worth it. Two known-coarse anchors:
  a `bad typedefs:` error lands on the `typedefs:` section line (not the offending typedef name —
  the `state` layer doesn't track source lines), and a non-exhaustive `case` lands on the case
  node line (not the uncovered `when:`/`else:` region).
