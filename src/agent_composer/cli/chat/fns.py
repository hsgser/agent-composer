"""Deterministic transcript-fold for the bundled composer chat flow (cli/chat/chat.yaml)."""
from __future__ import annotations
from typing import Any


def fold_turn(inputs: dict) -> dict[str, Any]:
    "Grow the carried transcript with this turn's user message + assistant reply (in Python)."
    grown = (
        f"{inputs['transcript']}\n\nUser: {inputs['message']}"
        f"\n\nAssistant: {inputs['reply']}"
    )
    return {"transcript": grown, "exited": bool(inputs["exited"])}
