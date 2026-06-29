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
