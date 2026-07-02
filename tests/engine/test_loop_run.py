"""End-to-end `while:` loop runs (Ollama-free, pure-CODE body).

The loop carries `{n, exited}`, runs the body `bump` (`{n, exited} -> {n: n+1, exited: n+1>=3}`)
while `not ${exited}`, and terminates when the predicate goes false. Covers the multi-iteration
happy path, the turn-0 (0-iteration) case (seed already satisfies exit), and the max-exceeded
runaway guard.
"""

from agent_composer.compose.loader import load_flow
from agent_composer.compose.run import resume_command, resume_flow, run_flow


COUNTER = """
id: counter
name: counter
defs:
  bump:
    input:
      n: int
      exited: bool
    nodes:
      step:
        kind: code
        code: tests.engine._compose_codefns:loop_bump
        input:
          n: ${input.n}
          exited: ${input.exited}
        output:
          n: int
          exited: bool
    output: ${step.output}
nodes:
  loop:
    kind: loop
    call: bump
    input:
      n: 0
      exited: false
    while: not ${exited}
    max: 10
output: ${loop.output}
"""

# Seed already satisfies exited: true -> 0 body runs, seed committed unchanged.
TURN0 = COUNTER.replace("exited: false", "exited: true")
# max: 2 (< the 3 iterations the body needs) -> the runaway guard fires.
MAXED = COUNTER.replace("max: 10", "max: 2")


def test_while_loop_runs_until_predicate_false():
    result = run_flow(load_flow(COUNTER), {})
    assert result.status == "succeeded"
    assert result.output == {"n": 3, "exited": True}


def test_while_loop_zero_iterations_commits_seed():
    result = run_flow(load_flow(TURN0), {})
    assert result.status == "succeeded"
    assert result.output == {"n": 0, "exited": True}   # seed committed unchanged; 0 body runs


def test_while_loop_max_exceeded_fails_run():
    result = run_flow(load_flow(MAXED), {})
    assert result.status != "succeeded"
    # Assert the runaway guard fired specifically — not just any error mentioning "max"
    # (a `MAX_TOTAL_NODES` budget blowup also contains "max"). The guard message is
    # `loop 'loop' exceeded max (2)`; the budget error says "exceeded node budget".
    assert "exceeded max" in (result.error or "")
    # And confirm the located error type reaches the RunFailed event.
    error_types = {getattr(e, "error_type", None) for e in result.events}
    assert "LoopMaxExceeded" in error_types


# The chat-shaped slice: a body that PAUSES each turn on a `human_input` leaf, folds the
# delivered message into the carried {messages, exited} record, and loops until the human
# types "bye". Drives run() -> paused -> resume -> paused -> ... -> succeeded in-process.
LOOP_CHAT = """
id: chat
name: chat
defs:
  chat_turn:
    input:
      messages: list[str]
    nodes:
      ask:
        kind: human_input
        prompt: "your message"
        output: str
      fold:
        kind: code
        code: tests.engine._compose_codefns:chat_fold
        input:
          messages: ${input.messages}
          msg: ${ask.output}
        output:
          messages: list[str]
          exited: bool
    output: ${fold.output}
nodes:
  loop:
    kind: loop
    call: chat_turn
    input:
      messages: []
      exited: false
    while: not ${exited}
    max: 100
output: ${loop.output}
"""


def test_loop_body_pauses_and_resumes_each_turn():
    loaded = load_flow(LOOP_CHAT)
    # Turn 1: the body's human_input leaf parks the run.
    r1 = run_flow(loaded, {})
    assert r1.status == "paused"
    assert len(r1.pause_reasons) == 1
    # Deliver "hi" -> body END fires -> _loop_step clones the next iteration -> pauses again.
    r2 = resume_flow(loaded, engine=r1.engine,
                     commands=[resume_command(loaded, r1.pause_reasons[0], "hi")])
    assert r2.status == "paused"
    # Deliver "bye" -> fold sets exited=true -> predicate false -> the run succeeds.
    r3 = resume_flow(loaded, engine=r2.engine,
                     commands=[resume_command(loaded, r2.pause_reasons[0], "bye")])
    assert r3.status == "succeeded", r3.error
    assert r3.output == {"messages": ["hi", "bye"], "exited": True}


# A loop that is NOT the terminal node: its committed record feeds a downstream code node.
# Guards that `_loop_step` commits under the spawner id AND fires the spawner's out-edges
# (the terminate -> commit -> advance tail), not just that a terminal loop returns a value.
DOWNSTREAM = COUNTER.replace(
    "output: ${loop.output}",
    """  after:
    kind: code
    code: tests.engine._compose_codefns:double
    input:
      n: ${loop.output.n}
    output: int
output: ${after.output}""",
)


def test_loop_feeding_downstream_node_commits_and_advances():
    result = run_flow(load_flow(DOWNSTREAM), {})
    assert result.status == "succeeded", result.error
    assert result.output == 6          # loop ends at n=3; double(3) = 6
