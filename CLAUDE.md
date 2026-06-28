# CLAUDE.md — conventions for Claude Code working on Agent Composer

## What Agent Composer is — in 60 seconds

A library for **building, running, and composing workflows of agents** — a
*flow* is a graph of nodes, where each node is an **LLM agent / code / ML
model / tool**. A flow's output is **whatever its terminal node emits** — a
report, a classification, a number, a structured record. The engine privileges
no output type and no domain.

The core is a **generic runner**: it reads a flow authored as
**Docker-Compose-shaped YAML**, compiles it to a typed IR, and executes it with
its own scheduler — there is no compilation down to a third-party graph runtime.
The primary surface is the **`ac` CLI** (`ac run flow.yaml`).

The engine **is** a small functional language. The canonical design reference is
the package README — [`src/agent_composer/README.md`](src/agent_composer/README.md):
flow-is-a-function, agent-is-a-flow, OCaml as the concept map, a closed
`NodeKind` with explicit `match` dispatch, node purity (a node never writes the
pool; it returns `Output | Pause | Enqueue`), a typed serializable variable
pool, and durable suspend/resume. **Consult it before designing any engine or
flow change.** The flow-authoring surface is documented in
[`docs/syntax.md`](docs/syntax.md); docstring/style conventions are in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## AI behavior rules — read these every session

These exist because Claude tends to over-help. Each rule has cost the project
something at some point; the rule prevents the next time.

### Zeroth rule — keep the backlog current (HIGHEST PRIORITY)

**This rule outranks every other rule.** The backlog lives under `todos/` (git-ignored,
local-only): [`todos/TODO.md`](todos/TODO.md) (immediate/near-term, decided),
[`todos/DEFER.md`](todos/DEFER.md) (undecided / thinking about it),
[`todos/FUTURE.md`](todos/FUTURE.md) (big, later, v2-scale). You must:

- **Add** an item the moment you notice work that should happen later, or the
  moment the user defers something ("let's do that later", "we'll fix X next").
  Capture it as `- [ ] <item>` in the right file — decided+soon → `TODO.md`;
  undecided → `DEFER.md`; big+later → `FUTURE.md`. Never let it live only in chat.
- **Check it off** when you complete it: tick the box and strike the text, then
  append `--` with the **exact short commit hash** where the work landed:
  `- [x] ~~<item>~~ -- <hash>`. Never write "this commit" — the hash isn't known
  until after the commit, so commit the work first, then record its hash in the
  following commit.
- **Consult** these at the start of work so nothing is forgotten across sessions.

### First-class rule — ask when uncertain

**If anything is uncertain, ask the user. Do not make assumptions.**
This rule overrides every other rule below.

Applies to (non-exhaustive):
- API contracts (model fields, response shapes, route paths).
- Design decisions with more than one reasonable answer.
- Whether a behavior should change vs. stay as-is.
- File / directory / naming conventions when the existing pattern
  doesn't clearly cover the case.
- Cross-cutting changes that touch multiple files or layers.
- Anything you'd hedge in your head with "I think..." or "probably..."

A clarifying question costs the user 5 seconds. A wrong assumption costs a code
review, a revert, or a bug. Always choose the question. If you've asked and still
aren't sure after the answer, **ask again** — don't fill gaps from inference.

### Don't add what wasn't asked

- **No abstractions not requested.** If the spec says "implement function X,"
  implement function X. Don't introduce a `Service` class, an abstract base, or
  a factory unless the spec asked for it.
- **No error handling beyond the spec.** Don't add retries, fallbacks, or
  graceful-degradation paths unless the spec calls for them. Error strategies
  (retry / fail-branch / default) are a planned first-class engine seam — until
  then, error handling is best-effort and at the boundary layer only.
- **No features outside the active scope.** If you notice "we'll eventually need
  X," say so in chat (or add it to `todos/`); don't implement it.
- **Keep refactors scoped, but don't fear them.** The engine is young — cleaning
  up code or tests to make them simpler and clearer is encouraged. What to avoid
  is *unrelated* churn that bloats a review: if a cleanup is adjacent to your
  change and makes it clearer, do it; if it's a large unrelated rewrite, give it
  its own commit or PR. The goal is always clean, simple, easy-to-understand
  code — never a hack that dodges a real change.

### Iterate in small, tested steps

- **Break any task that needs more than ~2 hours of work into smaller pieces.**
  One logical change per commit, one focused group of changes per PR.
- **For each piece: write the code, write at least one test, run the test, only
  then move on.** Not "I'll write tests at the end" — that ships broken code at
  the end.
- **Don't accumulate untested code.** If you find yourself with three unfinished
  files open and no green test, stop and finish one of them.
- **Use the task list** (TaskCreate / TaskUpdate) when work has ≥ 3 sub-pieces.
  Mark them `in_progress` when you start, `completed` only when the test passes.
- **Commit at green.** Each "code + test + green" cycle gets its own commit.

### Keep docs and skills current as the engine changes

The docs and the Claude skills **are part of the engine's contract** — a stale doc
is a bug, not a chore. When a change touches a surface someone reads, update its
documentation **in the same change** (same commit/PR), never "later":

- **Authoring surface** (a node kind, `${...}` syntax, `typedefs`, a new field) →
  update [`docs/syntax.md`](docs/syntax.md) and the **`composing-agents`** skill
  (`.claude/skills/composing-agents/` — `SKILL.md`, `reference.md`, and a
  `templates/` flow when the shape is new). Re-validate any touched template still
  loads (`load_flow`).
- **Engine internals / invariants / layers** (the node contract, `NodeKind`,
  suspend-resume, the layer ladder, a seam) → update
  [`src/agent_composer/README.md`](src/agent_composer/README.md) and the
  **`engine`** skill (incl. its `reference.md` and `templates/node_kind/WIRING.md`).
- **Package structure** (a new package, a moved responsibility, an import rule) →
  the affected `__init__.py` charter and the **`structure`** skill.
- **CLI / public API** → [`README.md`](README.md) and the docstrings the `docs/`
  API reference renders.

If you change behavior and can't say which doc/skill it affects, that's a signal to
check all four. A reviewer who reads a skill should never be taught something the
code no longer does.

### Verify before claiming

- **Run tests before claiming they pass.** Engine tests live in `tests/engine/`;
  run them via `PYTHONPATH=src pytest tests/engine` (or `pip install -e '.[all]'`
  first). If you say "tests passing," the reviewer will check.
- **Don't claim acceptance criteria are met** unless you've verified each one
  against the actual code + behavior.

### Communicate with the human

- **When unsure, ask.** Don't guess at intent on cross-cutting changes (API
  contract, schema, YAML surface). One question beats one bad PR.
- **Tell the user when a decision is non-obvious.** If you choose between two
  reasonable approaches, name the choice and the trade-off before implementing.
- **Be concise.** Don't write paragraphs when a sentence works.

### Tone in code

- **Comments explain *why*, docstrings explain *what*.** Don't narrate what a
  line does when the code already says it. But every function, class, and
  non-trivial variable should carry a docstring or comment that makes its
  **purpose** unmistakable: what it is for, the **possible values** it can take,
  and the **shape** of the data (e.g. `dict[node_id -> list[Edge]]`,
  `None when ...`). Write for a reader who has never seen the file. Follow the
  docstring template in [`CONTRIBUTING.md`](CONTRIBUTING.md).
- **No internal reference numbers in code.** Plan/phase/task tracking tokens, and
  the axiom/law tags, carry no meaning to a reader and rot as the plan moves on.
  They belong **only in `todos/`** (or `docs/`). In code, say the thing in plain
  words instead. If you're tempted to write a tracking number, that's a signal
  the comment should explain the concept directly.
- **No "Generated by Claude" footers**, no AI-attribution comments in source.
  Attribution belongs in `NOTICE` (for ported code).
