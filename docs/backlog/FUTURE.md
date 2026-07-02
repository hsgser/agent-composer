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
  resume). **The `LOOP` node SHIPPED** (2026-07-02) — all three drivers (`while:`, `until:`, `times:`),
  overlay pruning (only one iteration resident at a time), and DURABLE cross-process resume of a live
  loop (`_replay_expansions` re-grows the live iteration). The in-iteration-suspension prerequisite for
  the agentic `ac chat` REPL below is met both **in-process** and **across processes** (a `human_input`
  body pauses per turn and a mid-loop checkpoint resumes in a fresh process). Still pending on the
  higher-order family: `FOLD`/`REDUCE`, and an AGENT (not just `human_input`) loop body.
- **`WATCH` predefined composite** — TOOL + CASE + WAIT + loop, run via `call`, shown as one
  collapsed node. Needs cyclic-graph validation + engine-level re-enqueue (the watch-loop) and an
  unauthorable `EventAwaited` pause reason.
- **Cyclic-graph validation + engine re-enqueue** — prerequisite for the WATCH watch-loop.
- **Structured AGENT output** — the larger build of a typed-output contract on the AGENT node. The
  near-term, focused version — *wiring the declared `output:` shape into generation* (structured-output
  / tool-forcing / parse-retry) — is **decided and tracked in [TODO](TODO.md)**.

## Engine — durability & scale (the server story)

- **Parallel/cross-process durable resume** — durable resume *inside* a `parallel:true` `MAP` /
  parallel graph / ref'd subflow. Needs the host resume seam + parallel-engine snapshot/resume +
  nested-suspension-through-reference. \ngoc{do we need this now?}
- **Durable channel impls** — Redis or Mongo-collection `ReadyQueue` + `CommandChannel`, injected from
  outside the core; the watcher/scheduler that pokes suspended runs.
- **Error strategies** — retry / fail-branch / default-value as an engine-side seam, **with per-node
  typed-error hierarchies** (a `<Node>Error` base + per-kind subclasses; the strategy dispatches on
  failure *type*, not a boundary string). Add an `exc.py` only to multi-failure-mode nodes
  (agent/code/ref/model). \ngoc{this is important}

## Integration / providers / serving

- **AGENT modes / control tools** — add a `react` mode (`MODES`, `nodes/agent/modes/`); more control
  tools (e.g. `call_subagent`). Today: `plain` + `tool_calling`; `ask_user` done.
- **MODEL serving seam** — re-introduce an injected `model_runtime(ctx)->value` (threaded
  load→run→build, into reference/MAP children) when real ML serving lands; the MODEL kind exists but
  `run` raises today (the dead seam was removed). \ngoc{maybe we just remove MODEL node? it either be API call if it's deployed or code node if it's run locally?}
- **Token streaming** — an agent-strategy that yields `StreamChunk` per token through the loop (the
  node already drains generator strategies; needs a streaming `complete`). CLI: token-stream the
  tool-calling final answer once tools resolve.
- **`vllm` provider** — finish wiring (a `model_catalog` entry + CLI endpoint-confirm mirroring the
  ollama `confirm_ollama_endpoint` path) so vLLM shows in the picker.
- **Run-history UI** — a `/runs` browser over written run transcripts; durable-run surfacing — pairs
  with the server. \ngoc{what is this?}
- **Agentic `ac chat` REPL** (decided 2026-07-01) — a Claude-Code-style interactive chatbox where a
  top-level **composer agent** can discover, run, validate, and *author* flows on the user's behalf.
  Dogfooded as a real Agent Composer flow: a `tool_calling` AGENT inside a `LOOP` that suspends each
  turn for the human's next message. Dependency chain: (1) the `LOOP` construct above (in-iteration-
  suspension `while:` variant) — **DONE in-process** (2026-07-02); an AGENT-in-loop body still needs
  `_grow_loop` spawner-subnode stamping (see DEFER) to route its pause segments; (2) flow-op tools
  (`list_flows`, `read_flow`, `run_flow`, `validate_flow`, `write_flow`) registered into
  `TOOL_REGISTRY`; (3) the chat flow `.yaml`; (4) the `ac chat` CLI surface (scaffold exists at
  `cli/chat/`). `write_flow` writes YAML to disk — needs a confirm/guard. Scoped chat model + tool set
  chosen with the user; sequencing = LOOP (done), then plan tools + chat.
