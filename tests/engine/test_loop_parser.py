"""Unit test for parsing `kind: loop` into a LoopDescriptor.

The parser layer only: `parse_file` -> `ComposeFile` (raw `nodes` dict), then
`parse_nodes(f.nodes)` reads each keyed-map body into its typed descriptor.
`kind: loop` must produce a `LoopDescriptor` with `call`/`while_`/`max`/`inputs`
populated. Baking the descriptor into a runtime LoopNode is a later step.
"""

from agent_composer.compose.parser import parse_file, parse_nodes

LOOP_YAML = """
id: chat
name: chat
input:
  history: list
output:
  messages: list
nodes:
  chat_loop:
    kind: loop
    call: chat_turn
    inputs:
      messages: ${input.history}
      exited: false
    while: not ${exited}
    max: 1000
"""


def test_parser_reads_loop_node():
    cf = parse_file(LOOP_YAML)
    desc = parse_nodes(cf.nodes)["chat_loop"]
    assert desc.call == "chat_turn"
    assert desc.while_ == "not ${exited}"
    assert desc.max == 1000
    assert desc.inputs["exited"] is False
