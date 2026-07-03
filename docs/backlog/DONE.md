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

