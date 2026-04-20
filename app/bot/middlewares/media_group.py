"""Phase 07 (CAR-09): MediaGroupAggregatorMiddleware.

Buffers messages sharing media_group_id, forwards the FIRST message's handler
invocation with data['album'] populated with the sorted list once the debounce
elapses. Non-first messages of the same album are suppressed (return None).

Guardrails (per 07-RESEARCH.md § G3):
- 1.5s debounce (Telegram typically delivers full album in <500ms)
- message_id sort (Telegram delivery is unordered)
- Hard cap 10 messages (excess silently truncated — handler enforces UX reject)
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 1.5
HARD_CAP = 10


class MediaGroupAggregatorMiddleware(BaseMiddleware):
    """Outer middleware — runs before filters so duplicates short-circuit."""

    def __init__(self, debounce: float = DEBOUNCE_SECONDS):
        super().__init__()
        self.debounce = debounce
        self._groups: dict[str, list[Message]] = {}

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        mgid = event.media_group_id
        if not mgid:
            return await handler(event, data)

        is_first = mgid not in self._groups
        self._groups.setdefault(mgid, []).append(event)

        if len(self._groups[mgid]) > HARD_CAP:
            self._groups[mgid] = self._groups[mgid][:HARD_CAP]
            logger.warning("media_group %s truncated at %d messages", mgid, HARD_CAP)

        if is_first:
            await asyncio.sleep(self.debounce)
            messages = self._groups.pop(mgid, [])
            messages.sort(key=lambda m: m.message_id)
            data["album"] = messages
            logger.info("media_group %s aggregated %d messages", mgid, len(messages))
            return await handler(messages[0], data)

        # Non-first messages of the album — suppress; first message owns the handler
        return None
