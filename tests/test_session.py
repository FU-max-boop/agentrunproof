from __future__ import annotations

import pytest

from agentrunproof.session import RecordingSession


@pytest.mark.asyncio
async def test_recording_session_is_detached_and_honors_limit() -> None:
    original = [{"role": "user", "content": "one"}]
    session = RecordingSession(items=original)
    original[0]["content"] = "mutated"

    await session.add_items([{"role": "assistant", "content": "two"}])
    limited = await session.get_items(limit=1)
    limited[0]["content"] = "changed"

    assert session.snapshot() == [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
    ]
    assert [operation.operation for operation in session.operations] == [
        "add_items",
        "get_items",
    ]


@pytest.mark.asyncio
async def test_recording_session_pop_and_clear() -> None:
    session = RecordingSession(items=[{"role": "user", "content": "one"}])
    assert await session.pop_item() == {"role": "user", "content": "one"}
    assert await session.pop_item() is None
    await session.clear_session()
    assert session.snapshot() == []
