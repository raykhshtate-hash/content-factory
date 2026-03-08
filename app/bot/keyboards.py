from aiogram.types import InlineKeyboardMarkup

def get_main_keyboard() -> InlineKeyboardMarkup:
    # TODO: Create and return the main menu keyboard if needed for non-command flow
    return InlineKeyboardMarkup(inline_keyboard=[])

def get_format_selection_keyboard() -> InlineKeyboardMarkup:
    """Useful if the user didn't specify a format via command, but rather through ideation."""
    # TODO: Implement interactive format selection
    return InlineKeyboardMarkup(inline_keyboard=[])
