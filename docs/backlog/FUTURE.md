# FUTURE

Big, directionally-decided plans that are out of near-term scope — v2-scale. These are *not*
"thinking about it" (that's [DEFER](DEFER.md)) and *not* immediate ([TODO](TODO.md)); they're large
bodies of work we know we want eventually.

This directory (`docs/backlog/`) is tracked in git and published in the doc site under "Roadmap".

---

## Engine — higher-order drivers + control flow

- **`FOLD` / `REDUCE` + `LOOP`** — the rest of the higher-order subflow-driver family.
  `FOLD`/`REDUCE`: sequential accumulate `List[T]→U` (pairwise/tournament synthesis). `LOOP`:
  repeat-until-N / `while:` predicate with carried state (e.g. a self-critique refine loop).
  Child-engine drivers reusing the `MAP` `over`/`${item}` machinery + a carried-state accumulator.
  The until-condition `LOOP` also needs the in-iteration suspension story (host resume seam + parallel
  resume).
- **`WATCH` predefined composite** — TOOL + IF_ELSE + WAIT + loop, run via `call`, shown as one
  collapsed node. Needs cyclic-graph validation + engine-level re-enqueue (the watch-loop) and an
  unauthorable `EventAwaited` pause reason.
- **Cyclic-graph validation + engine re-enqueue** — prerequisite for the WATCH watch-loop.
- **Structured AGENT output** — the larger build of a typed-output contract on the AGENT node. The
  near-term, focused version — *wiring the declared `output:` shape into generation* (structured-output
  / tool-forcing / parse-retry) — is the "Structured AGENT output" item in [DEFER](DEFER.md), pending a
  design pass.

## Engine — durability & scale (the server story)

- **Parallel/cross-process durable resume** — durable resume *inside* a `parallel:true` `MAP` /
  parallel graph / ref'd subflow. Needs the host resume seam + parallel-engine snapshot/resume +
  nested-suspension-through-reference.
- **Durable channel impls** — Redis or Mongo-collection `ReadyQueue` + `CommandChannel`, injected from
  outside the core; the watcher/scheduler that pokes suspended runs.
- **Error strategies** — retry / fail-branch / default-value as an engine-side seam, **with per-node
  typed-error hierarchies** (a `<Node>Error` base + per-kind subclasses; the strategy dispatches on
  failure *type*, not a boundary string). Add an `exc.py` only to multi-failure-mode nodes
  (agent/code/ref/model).

## Agent self-improvement — feedback + credit assignment

A feedback loop that lets agents improve themselves. The shape: the **system receives feedback** on a
run's output → **analyzes** it → **assigns blame** to which node/input actually caused the problem →
**routes a targeted feedback signal back to the agent that produced that input**, which then improves
(updates its prompt / memory / reflection). Think "backprop / textual-gradient for a graph of agents"
(TextGrad / DSPy-optimizer family), adapted to our model.

**Why this architecture fits it well (the differentiator):** because the call graph is
**author-fixed, typed, and a DAG**, blame can be traced *along the fixed edges* — each node's inputs
are explicit `from:` bindings, so "this bad output traces to that upstream node's output" is
mechanically derivable, unlike an agentic system where the structure is itself runtime-decided. The
typed boundaries + `asserts:` are natural feedback *signals* (a failed assert / a downstream rejection
is a located error pointing at a producer).

**Open / needs design (a whole subsystem):** the feedback ingestion surface (human thumbs / a critic
agent / a failed assert); the credit-assignment algorithm (propagate a textual "gradient" backward
across the DAG); the per-agent update channel (prompt edit? a reflection note into long-term
**memory** — ties to the agent-memory axis in [DEFER](DEFER.md)?; offline vs online); and where state
lives (per-agent learned context = the long-term memory/store seam). Relates to: agent memory (DEFER),
structured AGENT output (DEFER), the assert/contract layer.

## Integration / providers / serving

- **AGENT modes / control tools** — add a `react` mode (`MODES`, `nodes/agent/modes/`); more control
  tools (e.g. `call_subagent`). Today: `plain` + `tool_calling`; `ask_user` done.
- **MODEL serving seam** — re-introduce an injected `model_runtime(ctx)->value` (threaded
  load→run→build, into reference/MAP children) when real ML serving lands; the MODEL kind exists but
  `run` raises today (the dead seam was removed).
- **Token streaming** — an agent-strategy that yields `StreamChunk` per token through the loop (the
  node already drains generator strategies; needs a streaming `complete`). CLI: token-stream the
  tool-calling final answer once tools resolve.
- **`vllm` provider** — finish wiring (a `model_catalog` entry + CLI endpoint-confirm mirroring the
  ollama `confirm_ollama_endpoint` path) so vLLM shows in the picker.
- **Run-history UI** — a `/runs` browser over written run transcripts; durable-run surfacing — pairs
  with the server.
