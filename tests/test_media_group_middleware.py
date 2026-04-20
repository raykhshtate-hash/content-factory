import asyncio
import time
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.bot.middlewares.media_group import (
    MediaGroupAggregatorMiddleware,
    DEBOUNCE_SECONDS,
    HARD_CAP,
)


def _msg(message_id: int, media_group_id: str | None = "grp1") -> MagicMock:
    m = MagicMock()
    m.message_id = message_id
    m.media_group_id = media_group_id
    return m


@pytest.mark.asyncio
async def test_single_non_album_passthrough():
    mw = MediaGroupAggregatorMiddleware(debounce=0.05)
    handler = AsyncMock(return_value="ok")
    event = _msg(1, media_group_id=None)
    result = await mw(handler, event, {})
    assert result == "ok"
    handler.assert_awaited_once()
    args, kwargs = handler.await_args
    assert "album" not in args[1]


@pytest.mark.asyncio
async def test_album_aggregates_and_sorts():
    mw = MediaGroupAggregatorMiddleware(debounce=0.1)
    handler = AsyncMock(return_value="ok")

    # Send 5 messages out of order sharing same media_group_id
    msgs = [_msg(i) for i in [103, 101, 105, 102, 104]]

    async def send(m):
        return await mw(handler, m, {})

    results = await asyncio.gather(*(send(m) for m in msgs))

    # Exactly one handler invocation
    assert handler.await_count == 1
    # Album sorted by message_id
    call_data = handler.await_args[0][1]
    album = call_data["album"]
    assert len(album) == 5
    assert [m.message_id for m in album] == [101, 102, 103, 104, 105]
    # First-message result returns handler output, others return None
    non_none = [r for r in results if r is not None]
    assert len(non_none) == 1
    assert non_none[0] == "ok"


@pytest.mark.asyncio
async def test_album_hard_cap_10():
    mw = MediaGroupAggregatorMiddleware(debounce=0.1)
    handler = AsyncMock(return_value="ok")

    msgs = [_msg(i) for i in range(1, 13)]  # 12 messages

    async def send(m):
        return await mw(handler, m, {})

    await asyncio.gather(*(send(m) for m in msgs))
    album = handler.await_args[0][1]["album"]
    assert len(album) == HARD_CAP


@pytest.mark.asyncio
async def test_debounce_timing():
    mw = MediaGroupAggregatorMiddleware(debounce=1.5)
    handler = AsyncMock(return_value="ok")
    msg = _msg(1)
    start = time.monotonic()
    await mw(handler, msg, {})
    elapsed = time.monotonic() - start
    assert 1.4 <= elapsed <= 2.0  # 1.5s debounce with tolerance


@pytest.mark.asyncio
async def test_different_groups_isolated():
    mw = MediaGroupAggregatorMiddleware(debounce=0.1)
    handler = AsyncMock(return_value="ok")
    msgs = [_msg(1, "grpA"), _msg(2, "grpB"), _msg(3, "grpA"), _msg(4, "grpB")]

    async def send(m):
        return await mw(handler, m, {})

    await asyncio.gather(*(send(m) for m in msgs))
    assert handler.await_count == 2  # one per group
