# TODO

Immediate / near-term, **decided** work. **Maintaining this file is the highest-priority
rule** (see CLAUDE.md → "Zeroth rule").

This backlog is split three ways:
- **TODO.md** (here) — immediate or near-future, decided + actionable.
- [**DEFER.md**](DEFER.md) — open questions / trade-offs we're thinking about but haven't decided.
- [**FUTURE.md**](FUTURE.md) — big, directionally-decided plans out of near-term scope (v2-scale).

**Convention**
- `- [ ] open item` — still to do.
- `- [x] ~~done item~~ -- <short-commit-hash>` — on completion: tick, strike, append `--` with the
  **exact short commit hash** (commit the work first, then record the hash in the next commit).

Add an item the moment you notice work for later, or whenever the user defers something. When in
doubt about which file: decided+soon → here; undecided → DEFER; big+later → FUTURE.

This directory (`docs/backlog/`) is the project roadmap, tracked in git and published in the doc site
under "Roadmap".

---

## Publish to PyPI

- [ ] (optional) Run `twine upload` from `dist/` to publish the built wheel/sdist to PyPI.

## Engine

- [ ] **Pooled durable resume — make `resume()` drive-mode-aware + checkpoint `num_workers`.**
  `resume()` hardcodes the serial drain (`runtime/engine.py:389`); it should pick serial vs pooled
  exactly as `run()` does (spawn workers + dispatch + join), so a checkpointed run is resumable with
  ANY worker count. Sound because workers are pure executors and the single-writer dispatcher owns all
  mutation — correctness is worker-count-independent. **Persist `num_workers` in `RunCheckpoint`**
  (snapshot captures `engine.num_workers`); `restore()` defaults to the checkpointed count, but
  `restore(flow, ckpt, num_workers=N)` **overrides** it.

- [ ] **(low) `pause_reasons = paused[0].reasons` collapses a simultaneous multi-node pause** — only
  the first paused node's reasons surface. Rare (needs two nodes pausing in one step). Fix when a real
  multi-node pause flow exists.

## CLI

- [ ] **Describe inputs when prompting** — the flow `input:` section is `name: TYPE` (or
  `TYPE = default`) with no place for a human description (`InputDecl` in `compose/shapes.py:55` has
  `name`/`type`/`default`/`required`/`shape`, **no `description`**). Two parts: (a) let an author
  attach a per-input description in the YAML and thread it onto `InputDecl`; (b) when the CLI prompts
  for a missing input (`_prompt_missing`, `cli/run.py`), show that description. Required/optional is
  already surfaced (required inputs are starred).

- [ ] **`cli/utils.py` helpers** referenced by `llm_clients` comments but not built: `ensure_api_key`
  (interactive key prompt) + `confirm_ollama_endpoint`.

## Tooling

- [ ] **Project-wide pyright not clean / not wired to the env** — `npx pyright src/agent_composerr`
  reports errors, but most are artifacts of pyright not resolving the conda env's site-packages
  (`reportMissingImports` on `pydantic`, cascading into override errors on the pydantic models). Needs:
  point pyright at the project interpreter (`pyrightconfig` / `venvPath`+`venv`), then triage what
  genuinely remains. Undecided whether to gate CI on it — see also DEFER.

## Open bugs / known issues

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
