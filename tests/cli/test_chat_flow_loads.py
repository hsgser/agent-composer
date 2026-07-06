"""The bundled composer chat flow loads (tools must be registered first)."""
from pathlib import Path
from agent_composer.cli.chat import tools  # noqa: F401 - registers the flow-op tools
from agent_composer.compose.loader import load_flow


def test_chat_flow_loads():
    p = Path("src/agent_composer/cli/chat/chat.yaml")
    loaded = load_flow(p.read_text())
    assert loaded.name
