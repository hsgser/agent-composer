# Done

Completed backlog items, archived here from [TODO.md](TODO.md) once shipped. Each keeps its
original section grouping, the design context it was decided under, and the
`-- <short-commit-hash>` of the landing commit, so the roadmap history stays auditable
without cluttering the active backlog.

This backlog is split four ways:
- [**TODO.md**](TODO.md) — immediate or near-future, decided + actionable.
- [**DEFER.md**](DEFER.md) — open questions / trade-offs we're thinking about but haven't decided.
- [**FUTURE.md**](FUTURE.md) — big, directionally-decided plans out of near-term scope (v2-scale).
- **DONE.md** (here) — shipped work, archived from TODO.md.

---

## Engine

- [x] ~~**Kind-agnostic refactor — Phase P0: safety net & kind-dispatch census.** Added
  `tests/engine/test_kind_census.py`, a self-calibrating ast ratchet counting kind-dispatch sites in
  the engine core (`runtime/engine.py` + `runtime/eval_node.py`): a distinct line referencing a
  `NodeKind` member, `_SPAWNER_KINDS`, or `isinstance(_, *Expansion)`. Baseline = 22 (17 engine + 5
  eval_node); imports and diagnostic `.kind.value` reads excluded. Each later phase lowers the ceiling;
  P8 drives it to 0. Confirmed the four at-risk behaviors (skip-flood disposition, spawner fan-in,
  loop prune bound, suspend/resume commit re-pointing) are already covered — no new production code.~~
  -- 7ee49d2 (branch `dev/engine/kind-census`)

- [x] ~~**Kind-agnostic refactor — Phase P1: outcome vocab (`Route` + `NodeRouted` + node contract).**
  Introduced the routing-only outcome `Route(handle)` and the dedicated `NodeRouted(node_id, handle)`
  event so the engine dispatches CASE routing on the OUTCOME/EVENT, never on `node.kind == CASE`:
  CASE `run()` now returns `Route`, `eval_node` emits `NodeRouted`, and `engine._on_route` takes the
  chosen edge + skip-floods siblings. `_on_success` lost both CASE kind-checks (always pool-writes,
  always advances). Dropped the now-dead `Output.handle` and `NodeSucceeded.edge_source_handle` fields.
  Added the node-contract seams `is_spawner: ClassVar` and the `on_failure` recovery hook (default
  re-raise), unused until later phases. Census baseline 22 -> 20.~~
  -- bd20557 (branch `dev/engine/outcome-route`)

- [x] ~~**Kind-agnostic refactor — Phase P2: `commit_as` retires the alias maps.** Replaced the two
  engine-side commit-redirect dicts (`self.alias`, `self.loop_alias`) with a `commit_as` field carried
  on the node and baked by the `_grow_*` expanders onto each subflow terminal (child END / MAP END-list
  / agent resume continuation / loop-body END). `_on_success` now computes `target = event.commit_as or
  node_id` and commits/advances under `target` in ONE unified arm; loop-body ENDs route to `_loop_step`
  via `target in self.loop_desc` (the loop-route discriminator). `commit_as` lives on `Output`
  (node-chosen), base `Node` (engine-baked), and `NodeSucceeded`; `eval_node` folds `result.commit_as or
  node.commit_as` onto the event. The agent multi-pause origin chain is reconstructed off the prior
  segment's baked `commit_as` (no dict). Census unchanged at 20 (removed data structures, not
  kind-dispatch).~~
  -- 84c0bd6 (branch `dev/engine/commit-as`)

- [x] ~~**Kind-agnostic refactor — Phase P3: self-describing spawners (`Grow(Subgraph)`).** Every
  spawner (CALL/MAP/AGENT-continuation/LOOP) now `run`s to a self-describing `Grow(subgraph, prune,
  seed)` where `Subgraph(nodes, edges, wiring, roots)` is the fragment the engine splices in via ONE
  generic `_apply_grow` — replacing the deleted `Enqueue(target, inputs)` outcome and the per-kind
  `_apply_enqueue` dispatch. Nodes build their own subgraphs (`call_subgraph`, MAP's N clones + list-END
  fan-in, the agent resume continuation, `loop_iteration_subgraph`); `commit_as` on the subgraph
  terminal publishes the spawner's value. Durability unified to a single `GrowRecord(spawner_id, seed,
  children)` ledger record per grow — kind-blind replay rebuilds the live subgraph from the persisted
  `seed` via each node's `replay_grow` (nested grows ride the parent's `children`; loops keep the single
  live-iteration invariant); `CHECKPOINT_VERSION` -> 7.0. LOOP's turn-0 grow-vs-commit decision moved
  onto `LoopNode.run` (a 0-iteration `while` returns `Output(seed, commit_as=self.id)`). `is_spawner`
  ClassVar replaced the `_SPAWNER_KINDS` membership test; `NodeExpanded.enqueues` -> `.grow`. Absorbs
  the planned P6 (kind-blind durability / replay ledger) — done here as part of P3.5. Census 20 -> 8
  (the 8 remaining sites deferred to P4/P5/P8).~~
  -- 5d2f5e2..74e401c (branch `dev/engine/grow-subgraph`)

- [x] ~~**Kind-agnostic refactor — Phase P4: generic `prune` + loop budget on the node.** The prune/GC
  inverse of `splice` is now generic: `_prune(ids)` does a kind-blind removal of a node-id set from every
  live registry (`flow.nodes`/`edges`, `sm.node_state`/`executing`, `pool`, `depth`, `_spawner_expansion`)
  under `sm.lock`, and `_apply_grow` applies `grow.prune` right after the splice — so growth AND GC are
  both generic operations off the `Grow` outcome. Deleted the kind-specific `_prune_iteration`; added
  `_iteration_ids(spawner, i)` for the loop's iteration namespace. The loop's hard iteration budget moved
  onto the node as `LoopNode.should_stop(iteration)` (`iteration >= max_iters`); the engine's `_loop_step`
  consults it as both the `times` stop-count (continue arm carries the finished iteration's ids in
  `Grow(..., prune=dead)`) and the `while`/`until` runaway guard (raises located `LoopMaxExceeded`). The
  terminating iteration's scratch is reclaimed on the terminate arm. Census stays 8. STILL OPEN in the
  parent backlog item: the `depth` REF-budget rider is still kind-shaped in the residuals — a later phase
  moves it onto the node.~~
  -- d76db9a..07e9e82 (branch `dev/engine/prune-loop-budget`)


- [x] ~~**Kind-agnostic refactor — Phase P5: generic `run_node`, node-owned read boundary, generic
  `_grow_residual`.** Drove the kind-dispatch census (`test_kind_census.py`) from 8 to **0** — the engine
  core (`runtime/engine.py` + `runtime/eval_node.py`) no longer references `NodeKind` at all. The read
  boundary became node-owned: `bind_reserved(node_wiring, pool)` (WAIT `until`, MAP `over`) +
  `binds_per_item` (MAP), replacing the MAP/WAIT `if node.kind ==` reads. Asserts now bind their refs at
  the read boundary (record-first, pool-fallback) and evaluate purely, so END stops being special (the
  `node.kind == NodeKind.END` post-assert branch is gone). The commit-site post-assert lost its
  `NodeKind.CALL` gate (now `target != node_id and target_node.post_asserts`), keeping seed-record
  recovery + the spawner-id locator byte-for-byte. The `_grow_residual` 4-arm switch and its four
  `_grow_*_residual` methods were deleted; residual concerns are now generic rules in `_apply_grow`
  driven by node traits/hooks: `iter_boundary_records(seed)` (eager boundary asserts, still before the
  ledger attach), `grow_depth_delta` (CALL/MAP=1, AGENT=0, LOOP=None; drives MAX_REF_DEPTH),
  `grow_restamps_self` (AGENT), `is_loop` (loop bookkeeping); the AGENT origin `commit_as` override is
  engine-computed and applied generically to the derived terminal. Behavior-preserving (1414 tests
  green); two-round adversarial plan review + a final adversarial code review (APPROVED). Docs updated:
  `docs/engine.md`, `docs/nodes.md`, the `engine` skill.~~
  -- 066cfcf..660322b (branch `dev/engine/run-node-generic`)


- [x] ~~**Kind-agnostic refactor — Phase P7: inject the LLM client via `caps`, not baked on the
  node.** The AGENT node stopped importing `model_from_config`; the engine now owns the LLM-client
  provider (`FlowEngine.llm`, a `model_from_config`-shaped callable defaulting to a lazy
  package-lookup thunk so a monkeypatched factory is still honored) and hands it to LLM-backed
  nodes as `caps["llm"]` — the same capability-provider seam as `bind_item`, gated on a new
  `needs_llm` node trait (True on AgentNode only). `eval_node` gained an `llm=` param threaded from
  both engine call sites; `AgentNode.run(inputs, **caps)` builds its model from the cap
  (`_build_model(llm)`/`_ctx(prompt, llm)`) with a lazy `_default_llm` fallback for direct-driver
  calls. `llm_config` stays on the node (the native-structured gate reads it). Behavior-preserving
  (1421 engine tests green, +7 new in `test_caps_llm.py`; full suite 1462); adversarial plan review
  (APPROVED). Docs updated: the `engine` skill (`SKILL.md`, `reference.md`).~~
  -- be4ee8e..61fef2f (branch `dev/engine/caps-llm`)


- [x] ~~**Kind-agnostic refactor — Phase P8: final sweep & invariant lock-in.** Closing phase of the
  refactor. Locked in the invariant: the kind-dispatch census (`test_kind_census.py`) `BASELINE` is
  **0** and the engine core (`runtime/engine.py` + `runtime/eval_node.py`) contains **zero**
  `NodeKind` references (not even an import) — the engine dispatches only on the abstract node contract
  + the closed `Outcome` sum (`Output | Route | Pause | Grow`). Reconciled the docs/skills that still
  taught the retired `Enqueue` contract: the `engine` skill's `node_kind` template + `WIRING.md` now
  teach the four-arm `Outcome` and the `run(inputs, **caps)` signature; `docs/engine.md` +
  `docs/nodes.md` gained the `needs_llm` trait rows. Ticked the subflow-rewrite backlog items realized
  across the refactor (engine no longer owns MAP fan-in → `6abcc91`; per-kind `_apply_enqueue`
  collapsed to one generic splice → `5d2f5e2..101e595`; four-arm `Outcome` contract → `bd20557..101e595`;
  `on_failure` hook → `bd20557`). Doc/backlog-only phase (no production code); full engine suite green
  (1421), seed smoke green (89). Left OPEN by design: the loop-policy items (`_loop_step` still engine-
  side) and the `depth` REF-budget rider (still kind-shaped in the residuals) — neither blocks the
  census-0 invariant.~~
  -- 59ec997 (branch `dev/engine/p8-final-sweep`)

- [x] ~~**Kind-agnostic refactor — Phase P9: LOOP self-respawn (predicate/continue-stop policy onto
  `LoopNode`).** Moved the whole loop policy off the engine and onto the node per the docs/nodes.md
  Model-A self-respawn: each iteration is a fresh `LoopNode` driver clone (`L` = compiled origin at
  iteration 0; `L~k` = clone for k≥1 with `origin_id = L`; body clones land at `L#k/…`). `LoopNode.run`
  owns predicate + continue/stop + count + the runaway guard: STOP → `Output(carried, commit_as=origin)`
  (generic commit under the compiled loop id); CONTINUE → `Grow({body_k, fresh L~(k+1) driver},
  prune={body_{k-1}} ∪ {self unless origin}, seed=(carried, k))`. Deleted `_loop_step`, `loop_iter`,
  `_iteration_ids`, `_apply_loop_bookkeeping`, `loop_desc`, and the `_on_success` loop-back route;
  replaced loop bookkeeping with an origin-keyed single-record ledger (`_origin_record`). Moved
  `grow.prune` after `finish_executing`+`mark_node(EXPANDED)` so a driver can self-prune its own id.
  Durable replay rebuilds the live window purely from the seed via `LoopNode.replay_grow` /
  `loop_continue_subgraph`. Runaway-guard contract preserved: `max: M` permits exactly M body runs.
  Full engine suite green (1436).~~
  -- b7330da (branch `dev/engine/loop-self-respawn`)


- [x] ~~**Kind-agnostic refactor — Phase P10: Full Flow unification (one `Flow` core +
  `__start__`/`__end__`).** Unified `CompiledFlow`/`Subgraph`/`ClonedSubgraph` onto one shared `Flow`
  base (`nodes`/`edges`/`wiring`/`start_id`/`end_id`); `CompiledFlow(Flow)` adds the runtime concerns
  (outputs, flow_llm_config, adjacency, `add_subgraph`/`remove_subgraph`/`from_parts`). `Grow.subgraph`
  is now a plain `Flow`; the bespoke `Subgraph`/`ClonedSubgraph`/`from_flow` bridge were deleted. Adopted
  the `__start__`/`__end__` convention: a spliced subgraph carries a single `start_id` the engine
  schedules (`_apply_grow` now does `self._schedule(sg.start_id)`), dropping `Subgraph.roots` and
  `ClonedSubgraph.out_node_id`. Reworked MAP to splice one synthetic `map#/__start__` (`StartNode` at
  `ns(spawner, START_ID)`, empty decls) that fans out via non-optional ordering edges to the N element
  entries — replacing the N-root list; N=0 wires `map#/__start__ → map#/__end__` (the list collector,
  `commit_as=spawner`). `CHECKPOINT_VERSION` 7.0 → 8.0 (hard cutover: pre-8.0 blobs replay a topology
  missing the synthetic start). Full engine suite green (1439); census still 0.~~
  -- 946bc77 (branch `dev/engine/flow-unification`)


- [x] ~~**Kind-agnostic refactor — Phase P11: kind-agnostic compile/compose layer.** Extended the
  kind-blind treatment from the runtime into the compile layer: `compose/build.py`,
  `compose/validate.py` and `compile/llm_cascade.py` now dispatch on node TRAITS instead of
  `node.kind == NodeKind.X`. Added a load-time hook `reserved_wiring_keys()` (the pool-free NAMES
  counterpart of the runtime `bind_reserved` — timed `wait` → `{"until"}`, `map` → `{"over"}`);
  `check_wiring_parity` and the validate ref-scan read it. Converted the rest to existing traits:
  `check_ref_map_types` gates on `is_loop`/`child_inputs`, `check_loop_shape_contract` on `is_loop`,
  the MAP-assert rejection + ref-scan on `binds_per_item`, llm_cascade recursion on static-child
  presence. `NodeKind` import removed from all three files; the only kind-string read left is the
  node factory (`build_call_node`, reading raw `desc.kind`), documented as the construction boundary
  where kind identity is born. Also FIXED a latent llm_cascade gap — LOOP body agents now inherit the
  parent flow/CLI llm_config (cascade recurses into any node with a static `.child`, not just
  CALL/MAP) — and restored base `Node.iter_boundary_records` clobbered mid-branch. Full engine suite
  green (1441); census still 0; `grep NodeKind src/ | grep -v /nodes/` clean.~~
  -- 40d898d (branch `dev/engine/kind-agnostic-compile`)


- [x] ~~**Route all `${...}` reference extraction through the one AST walker.** The prior
  expr-unification landed the grammar, but six call sites still re-derived references with
  copy-pasted flat regexes (asserts, cases, build wiring, validation, expand) — so whole-span and
  computed-expression conditions (`${a + b}`, `${x in xs}`) could load or inline incorrectly. Collapsed
  all six onto a single expr AST walker, so every binding / condition / prompt extracts and rewrites
  refs identically; dropped the mixed `${ref} op` spelling from the condition examples.~~ -- 6b91a48
  (branch `dev/engine/unify-ref-extraction`)

- [x] ~~**Unify `${...}` into one expression grammar.** Collapsed the three divergent `${}` dialects
  (binding coalesce/default, `when:` boolean/arithmetic, prompt builtin-call) into ONE pure-expression
  grammar (parse → AST → evaluate / ref-walk): arithmetic (`+ - * / % **`), comparisons, `in`, list
  literals, and pure builtins now work in every binding, condition, and prompt. Moved flow-invocation
  out of `${}` into a compile-time whole-value `call(...)` directive (recognized at binding + case-target
  sites; desugars to an anonymous `call` node); the old `${flow(args)}` span form is now a `LoadError`.
  The `:-`/`:?` RHS is an expression, so a string default/message must be quoted (`${x:-"today"}`); a
  prompt `$$` renders a single `$`. New `expr/grammar.py` (parse) + reshaped `expr/expressions.py`
  (eval + ref-walk) + `expr/template.py` (string surface); three resolve modes; restricted-AST safety
  boundary. Design + plan under `docs/plans/2026-07-02-expr-unification-*-final.md`.~~ -- a97fc80
  (branch `dev/engine/expr-unification`, 34 commits; 1379 tests green)

- [x] ~~**Locate the unknown AGENT mode/control `LoadError`.** `build_leaf_node` surfaces an invalid
  `mode:`/`controls:` as `LoadError(f"node {desc.id!r}: {exc}")` with no `.line`, so the error can't
  point the author at the offending YAML line. Thread the node's source line onto the raised
  `LoadError`.~~ -- ccaf3cf: already located — the loader's generic `except LoadError` around
  `build_leaf_node` stamps `exc.line = n_lines.get(nid)` (added by the later line-mapping work); this
  commit adds the missing regression test through the full `load_flow` path (bad `mode:` and bad
  `controls:` both assert `.line`).

- [x] ~~\ngoc{add options to human input so claude can compose question and also options similar to claude. claude we should have an option to let the agent to redesign or write the question/options depending on the inputs/context. Do human input node should have an option to receive context and option to ask LLM to redesign the questions/options. There are should me multiple questions as well.~~ -- shipped across `5a7a574..7eefc16`: static `questions:` list (AskUserQuestion-shaped), `adaptive_questions:` LLM-compose block (desugars to a synth compose-agent + pure gate), and manual `questions: ${ref}` form; answer is a record keyed by header.

- [x] ~~rename ifelsenode to CaseNode for consistency~~ -- b5004a2
  Internal-only rename: `IfElseNode` -> `CaseNode`, `NodeKind.IF_ELSE` -> `NodeKind.CASE` (value
  `"if_else"` -> `"case"`), module `nodes/if_else/` -> `nodes/case/`. The YAML authoring surface was
  already `kind: case`, so nothing author-facing changed.

- [x] ~~**Compact mode — a single-node flow authored inline (flow *is* the node).** Let an author
  collapse the common "one flow, one node" case so they don't have to write a `nodes:` map + a
  redundant `output: ${greet.output}` wiring step. The parser detects the compact shape (a node
  `kind:` at flow top level, no `nodes:` map) and desugars it into the canonical one-node flow before
  compile, so the IR and engine are unchanged.~~ -- b12957d
  Shipped: the flow `id:` names the single node; the flow `input:` is the node signature (auto-wired
  by name, `p = ${input.p}`); the flow `output:` is the node's output type, re-exported as the flow
  output; restricted to the value-producing leaf kinds (agent/code/model/tool/human_input).
  Documented in `docs/syntax.md` + the `composing-agents` skill (`templates/compact.yaml`).

- [x] ~~**Precise runtime-error source line (phase 1: node-level).** `ac run` boxed the failing
  *node header*; now it boxes the EXACT originating line — an input binding (`as_of: ${...:?...}`),
  a node pre/post assert expr — via a structured `SourceSpan` locator produced at the failure site,
  carried on `NodeFailed`, and resolved by parser sub-line maps, with a kind fallback (a code node's
  `code:` line) then the node header then a plain message.~~ -- f7f4b60
- [x] ~~**Precise runtime-error source line (phase 2: flow-level).** Flow-level failures with no node
  behind them now box their precise line too: a false post-terminal / boundary assert boxes the
  `asserts:` expr, and a boundary input-coercion error boxes the input's declaration — via
  `RunFailed.locator` / `RunResult.locator` (run + resume) and the `StartNode` e08 `input_decl`
  locator.~~ -- ab29d17
- [x] ~~**Precise runtime-error source line (phase 3: code wrong-type output).** A value that fails
  its node's declared `output:` Shape is rejected at the typed write boundary; the resulting
  node-less `RunFailed` now carries a `field` `SourceSpan` (set on `NodeExecutionError`) so the box
  points at the node's `output:` declaration instead of printing a plain message.~~ -- 1b63723
- [x] ~~**Pooled durable resume — make `resume()` drive-mode-aware + checkpoint `num_workers`.**
  `resume()` hardcodes the serial drain (`runtime/engine.py:389`); it should pick serial vs pooled
  exactly as `run()` does (spawn workers + dispatch + join), so a checkpointed run is resumable with
  ANY worker count. Sound because workers are pure executors and the single-writer dispatcher owns all
  mutation — correctness is worker-count-independent. **Persist `num_workers` in `RunCheckpoint`**
  (snapshot captures `engine.num_workers`); `restore()` defaults to the checkpointed count, but
  `restore(flow, ckpt, num_workers=N)` **overrides** it.~~ -- 6a2fe36
- [x] ~~**F1 — AGENT/spawner inside a loop body durably resumes.** Proven (not fixed): a CALL/MAP
  spawner nested inside a dynamically-grown loop iteration, paused mid-body, resumes both in-process
  and across a durable checkpoint hop. The generic growth splice stamps nested spawners uniformly, so
  the behavior fell out for free after the kind-agnostic refactor — NO engine change was needed;
  `tests/engine/test_loop_body_spawner_resume.py` is the proof.~~
  -- 9cf72d9 (branch `dev/engine/loop-body-spawner-resume-test`)
- [x] ~~**Unify the typed-value vocabulary (Type/Value family, `typesys/` package).** Pure mechanical
  rename, zero behavior change, across 30 source + 55 test files. Collapsed the overlapping
  `Segment`/`SegmentType`/`Shape`/parse-`Type` names into one coherent family: value wrapper
  `Segment`→`TypedValue`, tag enum `SegmentType`→`ValueKind`, resolved type `Shape`→`Type`, parse AST
  `Type`→`TypeExpr` (demoted internal to `types.py`), `SegmentError`→`TypeCheckError`,
  `TypedVariablePool`→`VariablePool`. All 15 `*Segment`→`*Value`, one field spelling `.kind` everywhere
  (was `value_type`/`seg_type`/`element_type`), `build_segment*`→`build_value*`,
  `resolve_shape`/`shape_for`→`resolve_type`/`type_for`, and the whole lowercase `shape` family
  →`type`. Package `state/`→`typesys/`, `segments.py`→`values.py`, `compose/shapes.py`→`compose/types.py`.
  Checkpoint wire format changed (`value_type`→`kind`) — old checkpoints won't load (acceptable
  no-compat). Carve-outs: English "shape" prose, `test_surface_vocab_golden` names, the
  `_validate_body_shape` comment.~~
  -- f59f215 (branch `dev/typesys/unify-vocabulary`)

## LLM config — cascade + per-node opt-out + CLI override

**Decided shape** (promoted from DEFER): `llm_config` propagates parent→child as a per-field
**fill-the-gap** cascade (most-specific wins); flow-level config is **optional**; a node can opt out of
the whole cascade with `inherit: false`; the CLI can inject a config as the outermost layer.

Resolve each agent node's **effective** config at compile/expand time so nodes stay pure (the effective
dict is baked onto the node — no runtime pool reads). Precedence, most→least specific:
**node → enclosing (sub)flow → parent flow(s) → top flow → CLI-passed config → global runtime defaults.**

- [x] ~~**Flow-level `llm_config` section**~~ — allow a top-level `llm_config:` on a flow (and on a
  subflow), parsed onto the flow shape (`compose/parser.py`, `compose/shapes.py`). Optional — absent is
  fine, no loud load error. -- 4ed6f24
- [x] ~~**Cascade resolution (fill-the-gap, per field, most-specific wins).**~~ Build each agent's effective
  config by merging the layers above; threads through `call`/`uses:` subflow expansion
  (`compile/expand.py`) so a child inherits the enclosing/parent flow config for fields it leaves unset. -- ddfc066
- [x] ~~**`inherit: false` on an agent's `llm_config`**~~ — opt the node out of the **entire** cascade: use
  only its own dict over global runtime defaults. Whole-node only (per-field locking deferred → see
  DEFER). Parser field → `AgentNode`; short-circuits cascade resolution. -- 5da4878
- [x] ~~**CLI flags supply the flow-level config**~~ — `ac run --provider <p> --model <m>` (mirrors the
  `AGENT_COMPOSER_DEFAULT_*` env vars). The flags don't override `_settings.py` directly; they **supply
  an outermost `llm_config` layer** that **propagates via the cascade** to every agent that sets none.
  Precedence is just the cascade (fill-the-gap, most-specific-wins): a node's own `llm_config` wins,
  `inherit:false` nodes ignore it, and an unset flag falls back to the env-var default. **Open edge:** if
  a flow *authors its own* top-level `llm_config:` AND the user passes `--model`, the lean is CLI
  **fills gaps only** (authored flow-level config wins) — not a force-override. Depends on the cascade above. -- d38675f
- [x] ~~**Docs + skills (same change)**~~ — `docs/syntax.md` (flow-level config, `inherit:false`, CLI flag),
  `composing-agents` skill (`reference.md` + a template for flow-level config / opt-out), `engine` skill
  if cascade semantics touch internals. Re-validate touched templates load. -- 4e69909
- [x] ~~**Tests**~~ — gap-fill merge; node field wins over parent; `inherit:false` isolation; CLI injection
  as outermost layer; no-config-anywhere falls back to global runtime defaults. -- 6506c35

## Structured AGENT output — wire the declared shape into generation

**Decided shape** (promoted from DEFER). Parts (a) **declare** `output:` ✓ and (b) **enforce** at the
write boundary ✓ already exist; this builds **(c) generate** — constrain the model to emit the declared
shape. Layered strategy, with the boundary check kept as the final guarantee (defense-in-depth):
generation *tries*, the boundary *enforces*, retry catches the residual.

- [x] ~~**Shape → schema derivation** — convert a node's `output:` `Shape` into a JSON schema / pydantic
  model that `with_structured_output` accepts. Skip a bare scalar `str` (today's text passthrough); apply
  for every other declared shape — records, lists, AND scalar `int`/`float` (structured extraction beats
  text parsing).~~ -- 44d6048
- [x] ~~**`plain` mode: native structured output** — invoke via `model.with_structured_output(schema)`
  instead of the raw string return (`modes/plain.py:22`). The primary path.~~ -- 8cf9d17
- [x] ~~**Boundary parse-retry** — on a write-boundary mismatch, re-invoke with the error appended
  (self-correction), capped at N retries, then fail. The existing (b) check stays the enforcer.~~ -- 0fd5a28
- [x] ~~**Authorable `retries:` field** — let an author set the self-correction cap per agent node
  (`retries: 3`, default 2); threads parser → build → `AgentNode` → `AgentRunContext` →
  `generate_structured(max_retries=...)`.~~ -- 0fd5a28
- [x] ~~**Prompt-injection fallback + capability detection** — for providers/models without native
  structured output, render the schema + "respond with JSON matching this" + parse. Detect support via a
  **capability flag in the model catalog** (explicit, testable), not try/except.~~ -- 6752e6f, dc61c84
- [x] ~~**`tool_calling` mode: structured final answer** — the loop still calls tools mid-run, but the
  FINAL answer turn must emit the declared shape (a forced final "emit" step / `with_structured_output`
  on the synthesis turn). Lands after `plain`.~~ -- bfc31ac
- [x] ~~**Docs + skills (same change)** — `docs/syntax.md` (the `output:` → structured-generation
  contract; remove the "no JSON/structured parse" caveat at `syntax.md:100`), `composing-agents` skill
  (`reference.md` + a typed-output template), `engine` skill if the agent contract notes change.~~ -- 8f876ad
- [x] ~~**Tests** — schema derivation per shape; `plain` native path; boundary-retry on a bad emit;
  prompt-injection fallback for a no-native-support provider; `tool_calling` structured final answer;
  bare-`str` still passes through untouched.~~ -- e4504ce
- [x] ~~**(low) Fallback JSON code-fence tolerance** — the prompt-injection fallback
  (`nodes/agent/structured.py:_generate_fallback`) did a bare `json.loads` on the model's text; models
  often wrap JSON in a ```json … ``` (or bare ``` … ```) fence despite the "no fences" instruction,
  which failed the parse and burned a retry. `_strip_code_fence` strips a single wrapping fence before
  `json.loads`.~~ -- 80f90c6

## Gallery

- [x] ~~**Showcase the unified `${...}` grammar + the `loop` node in the seed gallery.** The
  expr-unification and loop features shipped but no seed demonstrated their new expressive power
  (arithmetic / string / list ops / builtins inside `${}`, and `kind: loop`), leaving the "gallery
  doubles as the spec" goal unmet. Added `27-expr-ops.yaml` (every unified-`${...}` form across
  bindings, a prompt, and `asserts:`) and `28-refine-loop.yaml` (the `loop` node with `until:` +
  `max:` over an in-file `defs:` body, `'a -> 'a` carried record). Both registered in
  `test_load.py`'s loadable set (load green resolver-free); README rows added and `loop` joined the
  "every node kind" line.~~ -- f54d8aa (branch `dev/seeds/expr-loop-showcase`)

## CLI

- [x] ~~**`cli/utils.py` helpers** referenced by `llm_clients` comments but not built: `ensure_api_key`
  (interactive key prompt) + `confirm_ollama_endpoint`.~~ -- fab705c: both helpers built (TTY-aware;
  keyless/unknown providers skipped), and `ensure_api_key` wired into `ac run` via a pre-flight
  `_ensure_provider_keys` walk that resolves each agent's effective provider through the llm_config
  cascade. `confirm_ollama_endpoint` stays built-but-uncalled for the future provider-selection picker.

- [x] ~~**Box runtime node failures + traceback under `--engine-trace`** — a runtime `NodeFailed` with a
  RUNTIME-NAMESPACED id (a node inside a called child, e.g. `run/boom`) printed a bare `run failed:`
  line because the parser only indexes top-level nodes; it now falls back to the owning top-level call
  node and boxes a real source frame. The engine also captures the raising call's Python traceback at
  the node-failure boundary (`eval_node.py` -> `NodeFailed.traceback` -> `RunResult.traceback`),
  surfaced behind the existing `--engine-trace` flag (now covers runtime failures, not just compile
  errors). Seed `e24-nested-code-raise.yaml` + tests in `test_cli_prompt.py`.~~ -- 70bcc7b, f7f4b60, e801d26

- [x] ~~**Multi-frame call traceback into defs / external flows** — extends the above: a runtime failure
  inside a called child now renders a Python-traceback-style STACK of boxed `.yaml` frames (the
  top-level `call` node, then each `defs:<name>` / external `uses:` file it descends into, down to the
  failing leaf), not just the owning call node. Mechanism: a render-only `SourceFrame` baked onto each
  `call`/`map` node's `child_source` at load (`compose/loader`; parser gains `def_node_lines` /
  `def_node_field_lines` to index defs-internal node lines), walked by `cli/run.py:_walk_call_frames`.
  Closes the DEFER "defs-internal error line-mapping" item. Seeds `e25-external-raise.yaml` (+
  `lib_boom.yaml`), `e26-three-level-raise.yaml` + tests in `test_cli_prompt.py`,
  `test_call_source_frame.py`, `test_parser_lines.py`.~~ -- e801d26

- [x] ~~**`ac chat` — an interactive REPL dogfooded as a flow.** Turn-taking is a LOOP-per-turn flow
  (the turn boundary is structural, not model-chosen); the CLI is a thin host driving the per-turn
  `human_input` suspend/resume, mirroring how `ac run` hosts pauses. Ships `examples/chat.yaml` (the
  minimal plain REPL, documenting the pattern), a bundled composer chat (`cli/chat/chat.yaml`) whose
  `tool_calling` reply agent wields workspace-confined flow-op tools (`list_flows`, `read_flow`,
  `validate_flow`, `run_flow`, `write_flow` — validate-before-write, `..`/absolute escapes rejected),
  and the `ac chat [flow] [--workspace] [--provider] [--model]` subcommand. The load-bearing assumption
  (a `tool_calling` agent inside a LOOP body returns `Output`, never a mid-body `Pause`) is pinned by
  `tests/engine/test_agent_in_loop.py`. Loop-body `output:` must declare a typed RECORD equal to the
  carried record, so the transcript is grown by a CODE fold node (`{transcript, exited}`,
  `while: not ${exited}`) re-exported via `${fold.output}`. Pure authoring + CLI host — zero engine
  changes (kind-census ratchet stays at 0).~~ -- 8c418e9 (branch `dev/cli/chat-repl`)

## Docs

- [x] ~~**Docstring/comment sweep for CONTRIBUTING compliance + Types/Expressions internals pages.**
  Brought docstrings/comments across `compose`/`typesys`/`runtime`/`nodes` in line with the
  CONTRIBUTING template (imperative one-line summaries; full Args/Returns/Raises; backtick types)
  and stripped internal tracking tokens banned by CLAUDE.md (`e00`–`e08`, `phase3`, `Step 8`,
  `seed NN`, `T6c`, `D2`, `D-DEFAULTS`) — including the `phase3`-prefixed parser helper renames
  (`_back_map_to_plural`, `_normalize_sections`) and `reconcile_case_edges`'s `step8_edges` ->
  `inferred_edges`. Added `docs/typing.md` (typesys) + `docs/expressions.md` (expr `${...}`),
  wired into the Internals nav. Behavior unchanged; `tests/engine` 1448 passed.~~
  -- 6950e61 (branch `dev/docs/docstring-sweep`)

## Open bugs / known issues

- [x] ~~**Node-local post-`asserts:` on a spawner (`call`/`map`) are silently dropped.** A leaf node's
  node-local `asserts:` reading `${output}` fire correctly (eval_node POST block), but a `call`/`map`
  node returns an `Enqueue` and `eval_node` yields `NodeExpanded` + `return`s
  (`runtime/eval_node.py:113`) BEFORE the post-assert block (`:122`). The spawner's value is deferred
  to its alias filler (the child `END`), committed at `pool.set(spawner_id, event.output, ...)`
  (`runtime/engine.py:911`), so the node's own `${output}` post-asserts never run — a false one passes
  silently (verified). This violates "a false assert fails the run loudly." PRE-asserts (reading
  inputs) on a spawner DO fire. **Fix:** evaluate the spawner node's post-asserts against the
  alias-filled value at the `_apply_enqueue`/alias-commit site (where `event.output` lands), not in the
  per-node run path. Until fixed, assert a call's output via a top-level flow `asserts:` reading
  `${<call_id>.output...}` (those DO fire) or a downstream typed validation node.~~ -- `map` post-asserts
  are LOAD-rejected, so this only affected `call`; fired at the `_on_success` alias-commit site, recovering
  the call's input record from the persisted `CallExpansion.record`. -- 21dc4cc

- [x] ~~**`ask_user` resume is broken for providers with dashed tool-call ids (e.g. Ollama uuids).**
  When a `tool_calling` agent calls the `ask_user` control, the loop mints a namespaced human-input
  leaf id `__ask#<call_id>` and an answer forward-ref `${__ask#<call_id>.output}`
  (`nodes/agent/modes/tool_calling.py:109,121`). On resume that ref is parsed by `_PATH_RE`
  (`expr/template.py:45` = `^[A-Za-z_][A-Za-z0-9_#/]*...`), which allows `_ # /` but **not `-`**.
  Ollama's `call_id` is a uuid (`adebc542-e4a3-...`), so resume fails with `malformed reference path`.
  Anthropic/OpenAI ids (`toolu_…`/`call_…`, no dashes) happen to pass. **Fix:** sanitize the call_id
  to a path-safe slug when forming `hi_id`/the answer ref (keep the real id only in the pending
  `call_id`/`slot` for the `ToolMessage` match), and add a test using a dashed/uuid call_id. (The
  HUMAN_INPUT node path is unaffected.)~~ -- `_slug_call_id` maps every non-`[A-Za-z0-9_]` char to `_`
  for the `hi_id`/answer ref; the real id stays verbatim in `pending["call_id"]`/`slot` for the
  ToolMessage + resume-id match. -- f07c78a

- [x] ~~**`loop` error-boundary + loader-guard hardening (adversarial review of the `while:` slice).**
  Two latent crashes and two silent-mishandling gaps: (1) a `while:` predicate that raises at runtime
  (e.g. a division-by-zero on the carried record) escaped `run()` uncaught on iterations >= 1 — the
  predicate eval and per-iteration `_grow_loop` run in `_loop_step`, outside `eval_node`'s try/except,
  unlike the wrapped `_apply_enqueue` seed pre-check; (2) the node-budget `RuntimeError` from
  `_grow_loop` escaped the same way; (3) a `while:` ref to an undeclared name resolved falsy and spun
  the loop silently to `max`; (4) a non-int `max:` (string/float/bool) blew up the compare or read as
  0/1 (the descriptor is a bare dataclass, so its `Optional[int]` isn't enforced). Fixes: wrap the
  `_loop_step` predicate/grow raises into `NodeExecutionError` -> `RunFailed`; reject undeclared
  `while:` refs and non-int `max:` at build.~~ -- 951cd30 (crashes), d42a731 (loader guards)

- [x] ~~**`loop` `until:`/`times:` predicate forms.** The first `loop` slice shipped `while:` only
  (pre-check, 0+ runs). Complete the driver family: `until:` — a POST-check / do-while (1+ runs,
  stop once the predicate becomes TRUE); `times: N` — a fixed count with no predicate. Exactly one
  of `while:`/`until:`/`times:` is required (a load-time legality check); `max:` stays a required
  runaway guard for `while:`/`until:` but is REDUNDANT+REJECTED with `times:`.~~ -- 42c1739, 8a3a77d
  (parser), 1795b33 (build legality/bake), f531083 (seed pre-check), 099cc4e (loop-back), d89f585
  (authoring docs). The seed pre-check (`_apply_enqueue`) and loop-back (`_loop_step`) both branch
  on `predicate_kind`.

- [x] ~~**`loop` node-budget interplay.** Each iteration re-clones the body into the append-only
  subgraph overlay (`_grow_loop` → `add_subgraph`), so a long loop accumulated nodes until it tripped
  `MAX_TOTAL_NODES` and the run failed. Fine for short loops / a bounded chat REPL; a long-running
  loop needed clone reuse or overlay PRUNING of finished iterations.~~ -- 38d7895
  (`CompiledFlow.remove_subgraph`), c6ed61c (`StateManager.drop`), 79f8ba6 (`_prune_iteration`),
  2208506 (wire pruning into `_loop_step` — bounded budget). Chose overlay pruning over clone reuse
  to preserve the deterministic `#i` namespacing durable replay depends on: once an iteration threads
  its carried record forward, its `#i` namespace is dropped, so only one iteration is resident.

- [x] ~~**Durable cross-process replay of a live loop.** `_replay_expansions` raised
  `NotImplementedError` for the `LoopExpansion` arm — a run paused mid-loop could only resume
  IN-PROCESS, not from a checkpoint in a fresh process.~~ -- b489454 (`_grow_loop schedule=`),
  e0dcb18 (`LoopExpansion` replay arm), 0f95d6f (e2e cross-process resume), c925b1f (multi-hop
  re-snapshot ledger parity). Because pruning leaves only the LIVE iteration resident at any pause,
  the replay re-grows exactly ONE iteration (the last recorded seed) at its recorded index —
  simpler than the design's "re-grow #0..#i", and correct given pruning.

