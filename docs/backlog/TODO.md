# TODO

Immediate / near-term, **decided** work. **Maintaining this file is the highest-priority
rule** (see CLAUDE.md → "Zeroth rule").

This backlog is split four ways:
- **TODO.md** (here) — immediate or near-future, decided + actionable.
- [**DEFER.md**](DEFER.md) — open questions / trade-offs we're thinking about but haven't decided.
- [**FUTURE.md**](FUTURE.md) — big, directionally-decided plans out of near-term scope (v2-scale).
- [**DONE.md**](DONE.md) — shipped work, archived from here on completion.

**Convention**
- `- [ ] open item` — still to do.
- `- [x] ~~done item~~ -- <short-commit-hash>` — on completion: tick, strike, append `--` with the
  **exact short commit hash** (commit the work first, then record the hash in the next commit).
  Once shipped, archive the entry to [DONE.md](DONE.md) (keeping its section grouping + hash).

Add an item the moment you notice work for later, or whenever the user defers something. When in
doubt about which file: decided+soon → here; undecided → DEFER; big+later → FUTURE.

This directory (`docs/backlog/`) is the project roadmap, tracked in git and published in the doc site
under "Roadmap".

---

## Engine

- [ ] **(low) `pause_reasons = paused[0].reasons` collapses a simultaneous multi-node pause** — only
  the first paused node's reasons surface. Rare (needs two nodes pausing in one step). Fix when a real
  multi-node pause flow exists.

- [ ] add isinstance(${var}, Shape) type check builtin function so the assert can check the shape again if needed @ngocbh

- [ ] nested flow definition. or inline definition for MAP, LOOP etc? so instead of defining a flow and then call, we just define it inside MAP node definition directly instead of defining it outside and then call later? it's just a surface, we desugar it to a new flow behind the scene?

- [x] ~~**Unify `${...}` into one expression grammar** — collapse the three divergent `${}`
  grammars (binding / condition / prompt) into one pure-expression grammar; support arithmetic /
  string / list ops inside `${}`; move flow-invocation out of `${}` into a compile-time `call(...)`
  directive.~~ Design + plan: [`docs/plans/2026-07-02-expr-unification-design-final.md`](../plans/2026-07-02-expr-unification-design-final.md),
  [`docs/plans/2026-07-02-expr-unification-plan-final.md`](../plans/2026-07-02-expr-unification-plan-final.md). -- a97fc80

- [ ] sometimes I see Shape sometimes I see Segment. What are the differences among them? should we unify them?


## Subflow-node rewrite — make graph-expansion kind-agnostic

Brainstorm (2026-07-03). The lens: *leaf* nodes (agent, code, start, end, human_input, wait, model)
are pure single nodes (`run` → `Output`/`Pause`); *subflow* nodes (call, map, loop) are "a node that
IS a flow" — they expand into `__start__ → children → __end__` (`run` → `Enqueue`s). The goal is to
move all graph-growth semantics out of the engine and into the nodes. Below are the problems to
address one by one (no scope/sequence decided yet).

- [x] ~~**Engine owns MAP's fan-in wiring.** The collector `EndNode.list_` + the
  `e{i} <- ${child#i.end.output}` edges are built inside `_grow_map` (`engine.py:705-715`); they
  belong in `MapNode`. (Enabler: namespacing is deterministic and `clone_child` is already pure, so
  the node can compute every id and emit the wiring itself.)~~ `MapNode.run` now emits the whole MAP
  subgraph (child clones + list-END fan-in) via `map_subgraph`; `_grow_map` is deleted. -- 6abcc91
- [x] ~~**`_apply_enqueue` branches per kind** (`engine.py:1058` LOOP, `1107` CALL, `1137` MAP), each
  mirrored by a `_replay_expansions` arm. Collapse to one generic splice once nodes are
  self-describing.~~ One generic `_apply_grow`/`_prune` splice; `_apply_enqueue`/`_grow_map`/
  `_grow_call`/`_grow_residual` all deleted (census 0). -- 5d2f5e2..101e595
- [x] ~~**LOOP policy lives in the engine** (`_loop_step` + the `loop_alias` hook): predicate, merge,
  continue/stop, count. Move it into `LoopNode.run` (pure).~~ `_loop_step`/`loop_iter`/`loop_desc`/
  `_apply_loop_bookkeeping`/`_on_success` loop-back all deleted; `LoopNode.run` owns predicate +
  continue/stop + count + runaway guard and emits a self-respawning `Grow`. -- b7330da
- [x] ~~**Node return contract is too narrow.** `Enqueue(target, inputs)` can't describe reconvergence
  wiring or references to sibling nodes spawned in the same enqueue. It needs to become a
  self-describing subgraph (nodes + edges incl. `__end__` wiring + roots + "commit end under me").~~ -- d217cff
- [x] ~~**LOOP grows via a static-end + bespoke hook** instead of self-respawn. Target model: each
  iteration spawns the body subflow; on body-end it spawns *itself* as a fresh namespaced instance
  (`loop#k+1`, incremented index + updated carried record baked in); the terminating iteration's
  `Output` commits under the ORIGINAL loop id (the one shared "subflow result → subflow node id" rule
  MAP/CALL already use).~~ Each iteration now grows `{body_k, fresh L~(k+1) driver}`; STOP commits
  `Output(carried, commit_as=origin)` under the compiled loop id; origin-keyed single-record ledger. -- b7330da
- [ ] **Reconvergence is the only per-kind concept left** (CALL=identity/alias, MAP=list collector,
  LOOP=predicate chain). Unify under the single "subflow result commits under the subflow node's id"
  rule.
- [ ] **Nested-spawner bookkeeping must stay generic** — replay determinism (node subgraph build must
  be deterministic + re-runnable with effects suppressed for `_replay_expansions`), `MAX_TOTAL_NODES`
  global budget, and parent-pointer stamping for nested spawners, all derived from whatever subgraph
  is spliced (not per-kind).
- [ ] **AGENT is a hybrid** — a leaf that can also enqueue a continuation (mid-loop control-pause);
  make sure it still fits once growth is generic.
- [x] ~~**F1 — AGENT/spawner inside a loop body** can't durably resume (DEFER:75). Deferred behind this
  rewrite; should fall out for free (a body grown via the generic splice gets the same nested-spawner
  stamping as any subflow).~~ Proven: a CALL/MAP spawner nested in a loop body durably resumes both
  in-process and across a checkpoint hop, with NO engine change — it fell out for free. -- 9cf72d9
- [ ] **F2 — loop body partial-update / passthrough** — body must emit the entire carried record
  (`==`); want `output ⊆ carried` with unchanged fields merged through (DEFER:68). Deferred behind
  this rewrite; becomes pure code (`{**prior, **body_output}`) in `LoopNode.run`. The old plan
  ([`docs/plans/2026-07-03-complete-loop-implementation.md`](../plans/2026-07-03-complete-loop-implementation.md))
  is written against the old mechanism.

### `eval_node` / NodeBase contract cleanup — zero `if node.kind` in the engine

Same lens, aimed at the read/dispatch seam (`eval_node`). Today it carries four kind-specific
islands; the target is an engine that knows only the abstract `NodeBase` contract + a closed
`Outcome` sum. Target design writeup: [`docs/engine.md`](../engine.md) (Engine / Queue /
StateManager / `Outcome`) + [`docs/nodes.md`](../nodes.md) (NodeBase template + leaf/subflow).

- [x] ~~**`eval_node` has four `if node.kind ==` islands** — `over_mode` skip-bind + `over` list +
  `bind_item` cap (MAP, `eval_node.py:69-100`), `until` resolve (WAIT, `76-77`), `${output}`
  record-vs-pool resolve (END post-assert, `155-171`), and the `_SPAWNER_KINDS` tuple (`53`, `118`).
  Collapse all four; the engine dispatches on the returned `Outcome`, never on the kind.~~ -- 066cfcf..cf9c6a6
  (MAP/WAIT reads → node-owned `bind_reserved`/`binds_per_item`; END post-assert → generic ref-binding;
  `_SPAWNER_KINDS` was retired earlier under `is_spawner`.)
- [x] ~~**Keep pre/post assert checking in the engine's generic node wrapper (`run_node`), not per
  kind.** `run_node` wraps the node's abstract `run`: check pre-asserts → `run` → `on_failure` →
  check post-asserts. It is byte-identical for every kind, so it is protocol (engine side), not a
  `NodeBase` method. On failure the check raises; the engine keeps the raise→`NodeFailed` event
  funnel + locator stamp. Preserve `SourceSpan(id,"assert",expr)` and
  `error_type="NodeAssertFailed"` for both-engine byte-parity.~~ -- cf9c6a6
- [x] ~~**Bind assert-refs into the record at the read boundary** (reuse the data-edge ref extractor,
  `condition_refs`/`expr_refs`) so asserts evaluate against a pure, fully-populated record — **END
  needs no pool access and stops being special** (deletes `eval_node.py:155-171`). The subflow END
  asserts (`each#0/n.output.X`, `expand.py:174`) become ordinary bound record entries.~~ -- cf9c6a6
- [x] ~~**`is_spawner` trait on `NodeBase`** (default `False`) replaces `_SPAWNER_KINDS`; only a
  subflow node may return `Grow`.~~ -- d217cff
- [x] ~~**Node return contract → `Outcome = Output | Route | Pause | Grow(subgraph)`** — four arms, the
  ONLY thing the engine matches on (never on kind). Supersedes narrow `Enqueue(target, inputs)` (and
  folds in the earlier "self-describing subgraph" item above).~~ All four arms live in `nodes/base.py`;
  `Enqueue` deleted; the engine dispatches only on the returned outcome (census 0). Sub-detail notes
  below record which redirect pieces landed. -- bd20557..101e595 Decided pieces:
  - **`Output(value, commit_as=None)`** — `commit_as` (default → the node's own id) lets a subflow
    terminal publish under its spawner. The spawner *bakes* it onto the terminal (`__end__` for
    call/map; the terminating iteration for loop, `commit_as=origin_id`), and the terminal echoes it
    into its `Output`. This **supersedes the `alias` + `loop_alias` maps and the resume continuation
    re-pointing** (`engine.py:188, 205, 763`): one baked redirect, no engine-side alias table — drops
    the `state.alias(...)` step from the `Grow` apply. Unifies loop with call/map (loop stops being
    the "baked-identity" special case; it just bakes `commit_as=origin_id`).
  - **`Route(handle)`** — CASE, routing-only: stores no value, takes the handle's out-edge, skip-floods
    the siblings (the existing `_branch`/`_skip_edge`/`disposition` machinery, unchanged). Kills BOTH
    CASE kind-checks (`engine.py:1220` no-value guard, `engine.py:1231` branch guard). `handle`
    migrates off `Output` onto `Route`; `queue.done(node)` (all edges) vs `queue.route(node, handle)`
    (one edge + skip) are selected by the Outcome arm, not by `node.kind`.
  - **A spawner rehomes its own `post_asserts` onto the terminal**, so they fire in the generic
    `run_node` post-check against the committed value — deletes the CALL alias-site post-assert special
    case (`engine.py:1199`). Wrinkle: a post-assert that reads the spawner's *inputs* (not just
    `${output}`) needs those inputs wired into the terminal's params (the ref-extractor already knows
    which refs an assert mentions).
    **PARTIALLY LANDED** `204de76`: the CALL-kind special case is gone — the commit-site post-check is
    now kind-blind (`target != node_id and target_node.post_asserts`), keeping seed-record recovery for
    bare input heads and the spawner-id locator. (Not literally "rehomed onto the terminal": rehoming
    was rejected because bare `${input}` heads live only in the call-arg seed, not under a pool head.)
  - **Dependencies / notes:** needs Model A (loop predicate inside `LoopNode.run`) for the loop half;
    needs the resume path to bake `commit_as` into each continuation clone (role 3 of the old alias).
    The durable-replay ledger must persist the baked `commit_as`/`post_asserts` on spliced terminals.
    The `depth`/node-budget bookkeeping that rode alongside `alias` (`engine.py:656,716`) is a
    SEPARATE concern — tracked under the budget/GC item, not solved by `commit_as`.
- [x] ~~**`Grow` carries a `Flow`, not a bespoke `Subgraph`/`ClonedSubgraph`.** A spliced subgraph is
  just a flow (same `nodes`/`edges`/`wiring` core as `CompiledFlow`); factor a shared `Flow` core so
  `clone_child`'s `ClonedSubgraph` (`expand.py:55`) and the top-level `CompiledFlow`
  (`compile/model.py`) are one type. Drops `roots`/`out_node_id` via the `__start__`/`__end__`
  convention (entry = `__start__`, result = `__end__`).~~ Done: one `Flow` base
  (`nodes`/`edges`/`wiring`/`start_id`/`end_id`), `CompiledFlow(Flow)`, `Grow.subgraph: Flow`;
  `Subgraph`/`ClonedSubgraph` deleted; MAP splices a synthetic `map#/__start__` fan-out. -- 946bc77
- [x] ~~**`on_failure(exc)` no-op hook on `NodeBase`** (default re-raise) — the error-strategy seam
  (retry / fallback / fail-branch). Define the signature now; **defer the behavior** to the
  error-strategy work.~~ `on_failure(self, exc, inputs, **caps) -> NodeResult` on `NodeBase`
  (default re-raise), called from the generic `run_node` wrapper; behavior still deferred. -- bd20557
- [ ] **Budget / GC has no home in a grow-only `splice` (DECIDED — implement).**
  The target engine models growth as one generic `graph.splice(subgraph)`; it now gains one generic
  inverse. Three live mechanisms are all decided:
    - **`MAX_TOTAL_NODES` (`engine.py:62,648`) — KEEP as a pure engine backstop.** DECIDED
      (2026-07-04): it is already kind-agnostic (counts nodes, not kinds) and is the *only* backstop
      against unbounded/exponential expansion that isn't a loop cap (nested map fan-out, runaway
      spawners). Dropping it trades a clean failure for an OOM crash and saves essentially nothing.
    - **`max_iters` (`engine.py:898`) — MOVE into `LoopNode.should_stop`.** DECIDED (2026-07-04): it
      is loop-only state, so it belongs on the node, not the engine. Folds into the Model-A loop work.
      Two meanings ride the same field: a **counted** loop (`times: N`) stops *successfully* — its
      `should_stop` returns the terminating `Output(carried, commit_as=origin_id)` at the cap; a
      **while/until** loop treats the cap as a **safety limit** — hitting it is a failure, so
      `should_stop` raises (`LoopNotConverged`) → `on_failure` → `NodeFailed`, never a silent commit.
      **LANDED** `d76db9a..07e9e82`: `LoopNode.should_stop(iteration)` (`iteration >= max_iters`) now
      owns the budget; the driver decides the consequence (terminate for `times`, `LoopMaxExceeded`
      for `while`/`until`). The `on_failure`-routed variant stays pending the error-strategy seam.
    - **The prune/GC inverse of `splice` — a generic `graph.prune(ids)`.** DECIDED (2026-07-04):
      replaces today's kind-specific `_prune_iteration` (`engine.py:820`, which drops a committed loop
      iteration's whole namespace). The prune set rides **on the `Grow` outcome**: a pure node returns
      exactly one outcome, so the continue arm carries both halves —
      `Grow(subgraph, prune=<the finished iteration's ids>)`. The engine applies both in one step
      (`graph.splice(subgraph); graph.prune(prune)`) and stays kind-blind — only the loop knows which
      namespace is spent; `call`/`map` return an empty `prune`. This keeps the engine's growth *and*
      its GC as generic operations off the `Outcome`, retiring the last consumer of the
      `alias`/`loop_alias` cleanup path. The `depth` REF-budget rider (`engine.py:656,716`) belongs
      here too. Sequence: land alongside the `commit_as`/alias-removal work (pruning currently rides
      that cleanup path). Open sub-detail: the terminating iteration's own scratch is a bounded
      one-time residue (prune only fires on continue) — decide whether a final GC sweep reclaims it or
      it is left as negligible.
      **LANDED** `d76db9a..07e9e82`: generic `_prune(ids)` (kind-blind removal of nodes/edges/state/
      pool/depth/`_spawner_expansion`) + `_iteration_ids(spawner, i)`; `_apply_grow` applies
      `grow.prune` after the splice; `_prune_iteration` deleted. The terminating iteration's scratch is
      reclaimed on the terminate arm too. STILL OPEN in this checkbox: the `depth` REF-budget rider is
      still stamped kind-shaped in the residuals (`engine.py` ~847/906) — a later phase moves it here.

- [ ] **Loop `max:` reached should be an author-choosable outcome, not a hard fail.** Today a
  `while`/`until` loop that hits `max:` raises `LoopMaxExceeded` from the driver clone (`LoopNode.run`)
  → `NodeFailed` → the whole run fails. That is one reasonable policy, but the author should be able to
  choose: **fail loudly** (today's default) OR **end gracefully**, committing the last carried record
  as the loop's output (`Output(carried, commit_as=origin_id)`, exactly the `times`/predicate-satisfied
  STOP arm). Design the surface (a loop field? part of the error-strategy seam below?) — do NOT just
  silently swap the default. Ties into the budget/GC item above (the `on_failure`-routed variant of the
  runaway guard) and the `on_failure` seam below.
- [ ] **Design `on_failure:` as an authorable node field (error strategy).** The no-op `on_failure`
  hook already exists on `NodeBase` (landed `bd20557`, default re-raise, behavior deferred). Promote it
  to an **authorable** field so an author can choose, per node, what a failure does: **raise** (fail the
  run, today's behavior) vs **catch gracefully** (a default value / a fail-branch route / a retry). This
  is the general seam the loop-`max:` item above is a special case of. Needs real design — the type of
  the recovery value, how a fail-branch names its route, interaction with `commit_as` and the typed
  boundary — before any code.

- [x] ~~**Inject the LLM client via `caps`, not baked on the node**~~ — the node keeps only
  `llm_config` (WHICH model — pure config); the engine owns the `model_from_config`-shaped provider
  (`FlowEngine.llm`, default = a lazy package-lookup thunk) and hands it to LLM-backed nodes as
  `caps["llm"]`, gated on the new `needs_llm` trait (mirrors `bind_item`/`binds_per_item`).
  `AgentNode.run` builds its model from the cap, no longer importing the factory. -- be4ee8e..61fef2f


## Structured AGENT output — follow-ups

The core structured-output work (declare → generate → enforce → retry) shipped; see
[DONE.md](DONE.md). The **tool** typed-output half stays in DEFER ("Contract gaps") — same theme,
separate node kind.

_None currently open — the fallback JSON code-fence tolerance shipped (see [DONE.md](DONE.md)); the
`tool_calling` final-turn double-invoke moved to [DEFER.md](DEFER.md) ("Engine bugs surfaced but
deferred") as largely inherent for the common native path._

## CLI

_None currently open — recently shipped CLI items are archived in [DONE.md](DONE.md)._
- [ ] compress __start__ and __end__ nodes when printing? should we?
? seed (str) * hello
✓ polish#0/__start__
✓ polish#0/critic
✓ polish#0/__end__
✓ polish#1/__start__
✓ polish#1/critic
✓ polish#1/__end__
✓ polish#2/__start__
✓ polish#2/critic
✓ polish#2/__end__
✓ polish#3/__start__
✓ polish#3/critic
✓ polish#3/__end__
✓ polish#4/__start__
✓ polish#4/critic
✓ polish#4/__end__


## Tooling

- [ ] **Project-wide pyright not clean / not wired to the env** — `npx pyright src/agent_composerr`
  reports errors, but most are artifacts of pyright not resolving the conda env's site-packages
  (`reportMissingImports` on `pydantic`, cascading into override errors on the pydantic models). Needs:
  point pyright at the project interpreter (`pyrightconfig` / `venvPath`+`venv`), then triage what
  genuinely remains. Undecided whether to gate CI on it — see also DEFER.

- [ ] **(low) sweep leftover `STEP N` / `C1` plan-tracking tokens from test + source docstrings** —
  CLAUDE.md forbids plan/phase/step tracking tokens in code (they rot and mean nothing to a fresh
  reader). Several remain from the expr-unification build: `test_case_value.py` (STEP 1/2 section
  headers), `test_run_locator.py`, `test_binding_raw_api.py`, `compose/run.py` ("Step 8"),
  `test_expr_grammar.py` + `grammar.py` ("C1" fix). Replace each with plain-language description.
  (`test_inline_calls.py` already cleaned during Step 15.)

- [ ] **(low) `${x:?msg}` required-message test passes for the wrong reason** — with the unified
  grammar the `:?` RHS is an expression: a bare multi-word message (`${x:?a topic required}`) is a
  parse error, and a bare single word reads a *variable* (message lost as `None`); only a *quoted*
  message (`${x:?"..."}`) reaches `RequiredError` intact. `test_required_operator_fails_loud_when_unbound`
  asserts the message text appears, but for a bare message it only appears because the raw source is
  echoed inside the wrapped parse-error string — a false green. Tighten the test to use a quoted
  message and assert the `RequiredError` payload, not the source echo.

## Open bugs / known issues

_None currently open — recently fixed items are archived in [DONE.md](DONE.md)._
