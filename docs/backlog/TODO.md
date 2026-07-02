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

- [ ] **Unify `${...}` into one expression grammar** — collapse the three divergent `${}`
  grammars (binding / condition / prompt) into one pure-expression grammar; support arithmetic /
  string / list ops inside `${}`; move flow-invocation out of `${}` into a compile-time `call(...)`
  directive. Design final (review-1 + review-2 addressed): [`docs/plans/2026-07-02-expr-unification-design-final.md`](../plans/2026-07-02-expr-unification-design-final.md);
  implementation plan: [`docs/plans/2026-07-02-expr-unification-plan-final.md`](../plans/2026-07-02-expr-unification-plan-final.md).
  Next: execute (branch `dev/engine/expr-unification`).

- [ ] sometimes I see Shape sometimes I see Segment. What are the differences among them? should we unify them?


## Structured AGENT output — follow-ups

The core structured-output work (declare → generate → enforce → retry) shipped; see
[DONE.md](DONE.md). The **tool** typed-output half stays in DEFER ("Contract gaps") — same theme,
separate node kind.

_None currently open — the fallback JSON code-fence tolerance shipped (see [DONE.md](DONE.md)); the
`tool_calling` final-turn double-invoke moved to [DEFER.md](DEFER.md) ("Engine bugs surfaced but
deferred") as largely inherent for the common native path._

## CLI

_None currently open — recently shipped CLI items are archived in [DONE.md](DONE.md)._

## Tooling

- [ ] **Project-wide pyright not clean / not wired to the env** — `npx pyright src/agent_composerr`
  reports errors, but most are artifacts of pyright not resolving the conda env's site-packages
  (`reportMissingImports` on `pydantic`, cascading into override errors on the pydantic models). Needs:
  point pyright at the project interpreter (`pyrightconfig` / `venvPath`+`venv`), then triage what
  genuinely remains. Undecided whether to gate CI on it — see also DEFER.

## Open bugs / known issues

_None currently open — recently fixed items are archived in [DONE.md](DONE.md)._
