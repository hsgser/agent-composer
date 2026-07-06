"""Unit tests for the boundary clock seam (seed_system_clock / now_utc)."""

from datetime import datetime

from agent_composer.state.pool import TypedVariablePool
from agent_composer.state.segments import DateValue, DateTimeValue
from agent_composer.state.seeding import (
    default_run_id,
    now_utc,
    seed_system_clock,
    today_utc,
)


def test_now_utc_is_iso_datetime():
    v = now_utc()
    # parses as an ISO datetime and carries a time component (distinct from a date)
    datetime.fromisoformat(v)
    assert "T" in v


def test_seed_system_clock_seeds_typed_today_and_now():
    pool = TypedVariablePool()
    seed_system_clock(pool)
    assert isinstance(pool.system["today"], DateValue)
    assert isinstance(pool.system["now"], DateTimeValue)
    assert pool.system["today"].value == today_utc()
    assert pool.resolve("system", ["today"]) == today_utc()
    assert "T" in pool.resolve("system", ["now"])


def test_default_run_id_is_a_fresh_nonempty_string():
    a, b = default_run_id(), default_run_id()
    assert isinstance(a, str) and a
    assert a != b  # a fresh id per call
