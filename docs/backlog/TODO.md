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

- [ ] **Locate the unknown AGENT mode/control `LoadError`.** `build_leaf_node` surfaces an invalid
  `mode:`/`controls:` as `LoadError(f"node {desc.id!r}: {exc}")` (`compose/build.py:167`) with **no
  `.line`**, so the error can't point the author at the offending YAML line. Thread the node's source
  line onto the raised `LoadError` (the descriptor knows its node id; the parser has the line). Narrower
  and easier than the general "defs-internal error line-mapping" item in DEFER.

- [x] ~~\ngoc{add options to human input so claude can compose question and also options similar to claude. claude we should have an option to let the agent to redesign or write the question/options depending on the inputs/context. Do human input node should have an option to receive context and option to ask LLM to redesign the questions/options. There are should me multiple questions as well.~~ -- shipped across `5a7a574..7eefc16`: static `questions:` list (AskUserQuestion-shaped), `adaptive_questions:` LLM-compose block (desugars to a synth compose-agent + pure gate), and manual `questions: ${ref}` form; answer is a record keyed by header.

- [ ] add isinstance(${var}, Shape) type check builtin function so the assert can check the shape again if needed @ngocbh

- [ ] nested flow definition. or inline definition for MAP, LOOP etc? so instead of defining a flow and then call, we just define it inside MAP node definition directly instead of defining it outside and then call later? it's just a surface, we desugar it to a new flow behind the scene?

- [ ] list append, add, operation etc is now impossible via our flow design. We should be able to support simple function/operations inside ${}. There should be a way to make it general

- [ ] sometimes I see Shape sometimes I see Segment. What are the differences among them? should we unify them?


## Structured AGENT output — follow-ups

The core structured-output work (declare → generate → enforce → retry) shipped; see
[DONE.md](DONE.md). The **tool** typed-output half stays in DEFER ("Contract gaps") — same theme,
separate node kind. These non-blocking follow-ups from the code review remain (the resume-drop fix
landed at 9867b04):

- [ ] **(low) Fallback JSON code-fence tolerance** — the prompt-injection fallback
  (`nodes/agent/structured.py:_generate_fallback`) does a bare `json.loads` on the model's text;
  models often wrap JSON in a ```json … ``` fence, which fails the parse and burns a retry. Strip a
  leading/trailing code fence before `json.loads`.
- [ ] **(low) `tool_calling` final turn double-invokes the model** — the terminal turn already called
  the model to discover there were no tool calls, then `generate_structured` invokes it again to emit
  the shape (`nodes/agent/modes/tool_calling.py`). One redundant call per structured final answer.
  Reuse the terminal message or skip the discovery call when a shape is declared.

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
