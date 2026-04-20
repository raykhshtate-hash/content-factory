import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import (
    CarouselStates,
    cmd_carousel,
    on_carousel_brief,
)


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()


def _make_message(text: str = "", chat_id: int = 111, user_id: int = 222) -> MagicMock:
    m = MagicMock()
    m.text = text
    m.chat.id = chat_id
    m.from_user.id = user_id
    m.from_user.full_name = "Test User"
    m.message_id = 1
    m.answer = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_cmd_carousel_sets_awaiting_brief(storage):
    from aiogram.fsm.storage.base import StorageKey
    key = StorageKey(bot_id=0, chat_id=111, user_id=222)
    state = FSMContext(storage=storage, key=key)

    msg = _make_message()
    with patch("app.bot.handlers.supabase_service.create_content_item", new=AsyncMock(return_value={"id": "item-1"})), \
         patch("app.bot.handlers.supabase_service.set_stage_detail", new=AsyncMock()):
        await cmd_carousel(msg, state)

    current_state = await state.get_state()
    assert current_state == CarouselStates.awaiting_brief.state
    data = await state.get_data()
    assert data["item_id"] == "item-1"
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_brief_transitions_to_preview_on_success(storage):
    from aiogram.fsm.storage.base import StorageKey
    key = StorageKey(bot_id=0, chat_id=111, user_id=222)
    state = FSMContext(storage=storage, key=key)
    await state.set_state(CarouselStates.awaiting_brief)
    await state.update_data(item_id="item-1", approved_indices=[])

    msg = _make_message(text="Расскажи про уход за кожей зимой")
    fake_payload = {
        "slides": [
            {"order": 1, "text_title": "t1", "text_body": "b1"},
            {"order": 2, "text_title": "t2", "text_body": "b2"},
            {"order": 3, "text_title": "t3", "text_body": "b3"},
        ],
        "caption_telegram": "c1",
        "caption_instagram": "c2",
    }

    with patch("app.bot.handlers.claude_service.generate_carousel_slides",
               new=AsyncMock(return_value=fake_payload)), \
         patch("app.bot.handlers.supabase_service.set_carousel_slides", new=AsyncMock()), \
         patch("app.bot.handlers.supabase_service.set_stage_detail", new=AsyncMock()):
        await on_carousel_brief(msg, state)

    assert (await state.get_state()) == CarouselStates.preview.state
    data = await state.get_data()
    assert data["brief"] == "Расскажи про уход за кожей зимой"


@pytest.mark.asyncio
async def test_brief_stays_in_awaiting_on_valueerror(storage):
    from aiogram.fsm.storage.base import StorageKey
    key = StorageKey(bot_id=0, chat_id=111, user_id=222)
    state = FSMContext(storage=storage, key=key)
    await state.set_state(CarouselStates.awaiting_brief)
    await state.update_data(item_id="item-1", approved_indices=[])

    msg = _make_message(text="бриф")
    with patch("app.bot.handlers.claude_service.generate_carousel_slides",
               new=AsyncMock(side_effect=ValueError("bad"))), \
         patch("app.bot.handlers.supabase_service.set_stage_detail", new=AsyncMock()):
        await on_carousel_brief(msg, state)

    assert (await state.get_state()) == CarouselStates.awaiting_brief.state
