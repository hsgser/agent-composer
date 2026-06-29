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

- [ ] \ngoc{add options to human input so claude can compose question and also options similar to claude. claude we should have an option to let the agent to redesign or write the question/options depending on the inputs/context. Do human input node should have an option to receive context and option to ask LLM to redesign the questions/options. There are should me multiple questions as well.

- [ ] add isinstance(${var}, Shape) type check builtin function so the assert can check the shape again if needed

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

- [ ] **Describe inputs when prompting** — the flow `input:` section is `name: TYPE` (or
  `TYPE = default`) with no place for a human description (`InputDecl` in `compose/shapes.py:55` has
  `name`/`type`/`default`/`required`/`shape`, **no `description`**). Two parts: (a) let an author
  attach a per-input description in the YAML and thread it onto `InputDecl`; (b) when the CLI prompts
  for a missing input (`_prompt_missing`, `cli/run.py`), show that description. Required/optional is
  already surfaced (required inputs are starred). **Scope: flow-level inputs only** — node `inputs:` are
  wired from refs, never prompted from a human, so they get no description slot.

- [ ] **`cli/utils.py` helpers** referenced by `llm_clients` comments but not built: `ensure_api_key`
  (interactive key prompt) + `confirm_ollama_endpoint`.

## Tooling

- [ ] **Project-wide pyright not clean / not wired to the env** — `npx pyright src/agent_composerr`
  reports errors, but most are artifacts of pyright not resolving the conda env's site-packages
  (`reportMissingImports` on `pydantic`, cascading into override errors on the pydantic models). Needs:
  point pyright at the project interpreter (`pyrightconfig` / `venvPath`+`venv`), then triage what
  genuinely remains. Undecided whether to gate CI on it — see also DEFER.

## Open bugs / known issues

- [ ] **Node-local post-`asserts:` on a spawner (`call`/`map`) are silently dropped.** A leaf node's
  node-local `asserts:` reading `${output}` fire correctly (eval_node POST block), but a `call`/`map`
  node returns an `Enqueue` and `eval_node` yields `NodeExpanded` + `return`s
  (`runtime/eval_node.py:113`) BEFORE the post-assert block (`:122`). The spawner's value is deferred
  to its alias filler (the child `END`), committed at `pool.set(spawner_id, event.output, ...)`
  (`runtime/engine.py:911`), so the node's own `${output}` post-asserts never run — a false one passes
  silently (verified). This violates "a false assert fails the run loudly." PRE-asserts (reading
  inputs) on a spawner DO fire. **Fix:** evaluate the spawner node's post-asserts against the
  alias-filled value at the `_apply_enqueue`/alias-commit site (where `event.output` lands), not in the
  per-node run path. Until fixed, assert a call's output via a top-level flow `asserts:` reading
  `${<call_id>.output...}` (those DO fire) or a downstream typed validation node.

- [ ] **`ask_user` resume is broken for providers with dashed tool-call ids (e.g. Ollama uuids).**
  When a `tool_calling` agent calls the `ask_user` control, the loop mints a namespaced human-input
  leaf id `__ask#<call_id>` and an answer forward-ref `${__ask#<call_id>.output}`
  (`nodes/agent/modes/tool_calling.py:109,121`). On resume that ref is parsed by `_PATH_RE`
  (`expr/template.py:45` = `^[A-Za-z_][A-Za-z0-9_#/]*...`), which allows `_ # /` but **not `-`**.
  Ollama's `call_id` is a uuid (`adebc542-e4a3-...`), so resume fails with `malformed reference path`.
  Anthropic/OpenAI ids (`toolu_…`/`call_…`, no dashes) happen to pass. **Fix:** sanitize the call_id
  to a path-safe slug when forming `hi_id`/the answer ref (keep the real id only in the pending
  `call_id`/`slot` for the `ToolMessage` match), and add a test using a dashed/uuid call_id. (The
  HUMAN_INPUT node path is unaffected.)
