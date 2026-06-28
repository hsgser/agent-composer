# The `ac` CLI

`ac` runs a flow file: it loads the YAML, gathers the inputs, drives the flow to
a terminal state, and prints the output.

```console
ac run FLOW.yaml [--input k=v]... [--inputs inputs.json] [--quiet] [--verbose] [--num-workers N] [--engine-trace]
```

## Supplying inputs

A flow declares typed `input:` fields. You can supply them three ways, and they
layer in this order (later overrides earlier):

1. **`--inputs file.json`** — a JSON object of `{ "key": value, ... }`.
2. **`--input k=v`** — one input, repeatable. Values arrive as strings and are
   coerced to each input's declared type at the run boundary.
3. **Interactive prompt** — any *required* input still missing is prompted for.

```console
# all from flags
ac run examples/decision-brief.yaml --input question="Adopt a monorepo?" --input audience="execs"

# from a JSON file, with one override
ac run flow.yaml --inputs base.json --input audience="execs"

# supply nothing — get prompted for each required input
ac run examples/hello.yaml
```

### The "Running flow" banner

At the start of every run (unless `--quiet`), `ac` prints a boxed banner to
**stderr** identifying what it is about to run — the flow's `name:`, its
`version:` (if set), and its `description:` — so you can confirm you picked the
right file:

```console
╭──────────────────────────────────────────────────────────╮
│ Running flow: debate  (version: v2)                        │
│ Description: Frame a question, argue both sides, then decide. │
╰──────────────────────────────────────────────────────────╯
```

A flow with no `name`/`description`/`version` prints no banner.

### The interactive prompt

When a required input is missing, `ac` prompts for it. The widget follows each
input's declared type:

- a `bool` input → a yes/no confirm,
- a `Literal[...]` enum → a select list of the allowed tags,
- anything else → a free-text entry.

Each prompt's label carries the input's declared **type**, a required (`*`) /
`optional` mark, and any **default** — e.g. `topic (str) *`,
`as_of (Optional[date]) [optional]`, `window (int) [default: 30]`. For the
ISO-8601 `date` / `datetime` scalars — whose accepted string form isn't obvious —
a label with no default also shows an **example** value (`e.g. 2026-05-21`,
`e.g. 2026-05-21T14:30`); when a default is present it already shows the format,
so the example is dropped. An optional input left blank is skipped (its default
applies). Cancelling a prompt (Ctrl-C / Esc) cancels the run.

!!! note
    The prompt needs a real terminal (TTY). In a non-interactive context
    (a CI job, a piped command, `srun` without a pty) supply every required
    input via `--input` / `--inputs` instead, or the run will abort.

## Interactive resume (pauses)

A flow can suspend mid-run — at a `human_input` node, a timed `wait`, or when a
`tool_calling` agent calls the `ask_user` control. `ac` resumes such a run
**interactively**: each pause prints its prompt and asks for the awaited value,
then the run continues. This repeats until the flow reaches a terminal state.

```console
ac run examples/human-approval.yaml --input task="Plan a team offsite"
#   → draft is written
#   → CLI pauses: "Approve it as-is, or send it back to be revised? (approve / revise)"
#   → you type: approve
#   → run continues to completion and prints the kickoff message
```

A timed `wait` asks whether to release the wait now. An external-event pause
can't be satisfied from the CLI and leaves the run paused.

## Output and progress

- The terminal output is rendered as Markdown when it is a non-empty string,
  otherwise printed as-is (e.g. a multi-field object).
- **Per-node progress** is printed to **stderr** as the flow advances. On a real
  terminal each running node shows an animated spinner that is rewritten in place
  as a green `✓ node_id` on success, or a red `✗ node_id` (with the error on the
  next line) on failure. Off a terminal (a pipe, CI) only the final `✓`/`✗` line
  per node is printed. Nodes that fan out run concurrently, so several spinners
  can be live at once.
- Pass `--quiet` / `-q` to suppress progress entirely.
- Pass `--verbose` / `-v` to also print **each node's output** under its check as
  it finishes — handy for seeing the intermediate values a multi-node flow
  produces, not just the final result.

## Concurrency (`--num-workers`)

By default the engine runs single-threaded: ready nodes execute one at a time in a
deterministic order. Pass `--num-workers N` / `-w N` to run with a worker pool of
size `N`, so **independent** ready nodes — the parallel arms of a fan-out — execute
concurrently:

```console
# the debate diamond argues both sides at once instead of one after the other
ac run examples/debate.yaml --input question="Adopt a monorepo?" --num-workers 4
```

- `0` (the default) is the single-threaded, deterministic drain.
- `>=1` spawns that many workers. Nodes are pure executors and a single-writer
  dispatcher owns all state, so the **terminal output is the same** whatever the
  worker count — only the intra-run scheduling changes (and wall-clock time, when a
  flow has parallel branches that each do real work, e.g. separate LLM calls).
- A run that **pauses** and is resumed interactively resumes single-threaded
  regardless of `--num-workers`.

## Exit codes

| Status | Exit code |
|--------|-----------|
| Flow succeeded | `0` |
| Flow failed | `1` (error printed to stderr) |
| Flow failed to compile (bad YAML) | `1` (located error printed to stderr) |
| Run paused and resume was cancelled | `1` |
| Run cancelled at the input prompt | `1` |

## Compile errors

When a flow fails to **load/compile** (an unknown reference, a cycle, a bad field,
a malformed `uses:`), `ac` does not dump an engine Python traceback — it shows
**where in your `.yaml` it broke** as a boxed source frame: the `.yaml` around the
fault with line numbers, the offending line highlighted (`❱`), the panel titled
`file:line`, and the message below. (Same shape as a Python traceback's code box.)

```console
$ ac run broken.yaml
╭─ broken.yaml:7 ──────────────────────────────────────────────╮
│    4   a:                                                     │
│    5     kind: agent                                          │
│    6     prompt: hi                                           │
│ ❱  7   b:                                                     │
│    8     kind: agent                                          │
│    9     input:                                               │
│   10       brief: ${frame_typo.output}                        │
╰──────────────────────────────────────────────────────────────╯
flow has unresolved references:
  node 'b' input 'brief' from: reference ${frame_typo.output} uses unknown namespace 'frame_typo'
```

Pass `--engine-trace` to **also** print the engine's Python traceback under the
located error — for debugging the engine itself, not your flow.

An error that spans **several** places (e.g. a **cycle**, which implicates every node
in the loop) highlights *all* of them, widens the frame to cover both ends, and prints a
"why" legend naming each dependency edge that closes the loop — so you can see not just
*which* nodes form the cycle but *which references* create it:

```console
$ ac run cycle.yaml
╭─ cycle.yaml:4,9 ─────────────────────────────────────────────╮
│    3 nodes:                                                   │
│ ❱  4   a:                                                     │
│    5     kind: agent                                          │
│    6     input:                                               │
│    7       x: ${b.output}                                     │
│    8     prompt: use ${x}                                     │
│ ❱  9   b:                                                     │
│   10     kind: agent                                          │
│   11     input:                                               │
│   12       y: ${a.output}                                     │
╰──────────────────────────────────────────────────────────────╯
flow has a cycle involving ['a', 'b']; flows must be acyclic
  ↳ a depends on b (a.input.x)
  ↳ b depends on a (b.input.y)
```

## Runtime errors

A failure that happens **while the flow runs** — a node raises, a `code` node
returns the wrong type, an `:?` required reference is missing at its use site —
is shown the same way: a boxed `.yaml` frame pointed at the **node that failed**
(via its source line), with the message below. So a runtime error reads like a
compile error — you see *where* in the flow it broke, not an engine traceback:

```console
$ ac run e07-required-missing.yaml --input topic=climate   # as_of omitted
╭─ e07-required-missing.yaml:19 ───────────────────────────────╮
│   17                                                         │
│   18 nodes:                                                  │
│ ❱ 19   report:                                               │
│   20     kind: agent                                         │
│   21     input:                                              │
│   22       topic: ${input.topic}                             │
│   23       as_of:  ${input.as_of:?as_of is required ...}     │
╰──────────────────────────────────────────────────────────────╯
as_of is required for the report
```

A failure with **no node behind it** — a false boundary/post `assert:`, or an
input that can't be coerced to its declared type at the run boundary — has no
node line to point at, so it prints the plain `run failed: <message>` line.

## Next

- [Flow syntax](syntax.md) — write your own flows.
- [Examples](examples.md) — walk the shipped flows.
