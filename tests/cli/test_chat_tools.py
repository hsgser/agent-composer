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
