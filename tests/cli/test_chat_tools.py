"""Flow-op tools for `ac chat` — workspace-confined discover/read/validate/run/write."""
import pytest
from agent_composer.cli.chat import tools as T


def test_list_and_read_flow(tmp_path):
    (tmp_path / "a.yaml").write_text("id: a\nname: a\nnodes: {}\noutput: {}\n")
    T.set_workspace(tmp_path)
    listed = T.list_flows()
    assert "a.yaml" in listed
    assert "id: a" in T.read_flow("a.yaml")


def test_read_flow_rejects_escape(tmp_path):
    T.set_workspace(tmp_path)
    with pytest.raises(ValueError):
        T.read_flow("../secret.txt")


GOOD = "id: g\nname: g\ninput:\n  x: str\nnodes:\n  e:\n    kind: code\n    input:\n      x: ${input.x}\n    output: str\n    code: tests.seeds.fns:echo_value\noutput:\n  x: ${e.output}\n"

def test_validate_good_and_bad(tmp_path):
    T.set_workspace(tmp_path)
    (tmp_path / "g.yaml").write_text(GOOD)
    (tmp_path / "b.yaml").write_text("id: b\nname: b\nnodes:\n  x: {kind: nope}\n")
    assert "OK" in T.validate_flow("g.yaml")
    assert "OK" not in T.validate_flow("b.yaml")

def test_run_flow_tool(tmp_path):
    T.set_workspace(tmp_path)
    (tmp_path / "g.yaml").write_text(GOOD)
    out = T.run_flow("g.yaml", '{"x": "hi"}')
    assert "hi" in out
