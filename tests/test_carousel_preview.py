from app.bot.handlers import _build_slide_keyboard, _build_bulk_keyboard


def test_slide_keyboard_has_seven_buttons():
    """4 action buttons + 3 plashka position buttons."""
    kb = _build_slide_keyboard("item-1", 0)
    # InlineKeyboardMarkup.inline_keyboard is list[list[InlineKeyboardButton]]
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert len(buttons) == 7
    labels = " ".join(b.text for b in buttons)
    assert "✏️" in labels
    assert "✅" in labels
    assert "🔁" in labels
    assert "❌" in labels
    assert "⬆️" in labels
    assert "↔️" in labels
    assert "⬇️" in labels


def test_slide_keyboard_callback_data_format():
    kb = _build_slide_keyboard("abc-123", 4)
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "car:edit:abc-123:4" in cbs
    assert "car:approve:abc-123:4" in cbs
    assert "car:regen:abc-123:4" in cbs
    assert "car:reject:abc-123:4" in cbs
    assert "car:pos:abc-123:4:top" in cbs
    assert "car:pos:abc-123:4:center" in cbs
    assert "car:pos:abc-123:4:bottom" in cbs


def test_bulk_keyboard_has_two_buttons():
    kb = _build_bulk_keyboard("item-1")
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert len(buttons) == 2
    cbs = [b.callback_data for b in buttons]
    assert "car:approve_all:item-1" in cbs
    assert "car:regen_all:item-1" in cbs
