"""Deterministic helper for the `chat` example flow.

Importable so the example's `code:` ref resolves when `examples/` is a package on
`sys.path` (run from the repo root: `PYTHONPATH=$PWD ac run examples/chat.yaml`).
"""
from __future__ import annotations
from typing import Any


def fold_turn(inputs: dict) -> dict[str, Any]:
    """Fold one turn's user message + assistant reply into the carried chat record.

    The transcript grows deterministically here (in Python), NOT in the model — the
    turn boundary AND the transcript shape stay under the flow author's control.
    """
    grown = (
        f"{inputs['transcript']}\n\nUser: {inputs['message']}"
        f"\n\nAssistant: {inputs['reply']}"
    )
    return {"transcript": grown, "exited": bool(inputs["exited"])}
