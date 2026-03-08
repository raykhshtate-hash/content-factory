import asyncio
from datetime import datetime, timezone
from typing import Optional

from supabase import create_client, Client

from app.config import settings

TABLE = "content_items"

_client: Optional[Client] = None


def _get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    return _client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def create_content_item(
    user_name: str,
    chat_id: int,
    format: str,
    status: str = "idea",
    idea_text: Optional[str] = None,
) -> dict:
    """Insert a new row and return the created record."""
    now = _now()
    payload = {
        "user_name": user_name,
        "telegram_chat_id": chat_id,
        "format": format,
        "status": status,
        "idea_text": idea_text,
        "created_at": now,
        "updated_at": now,
    }
    client = _get_client()
    result = await asyncio.to_thread(
        lambda: client.table(TABLE).insert(payload).execute()
    )
    return result.data[0]


async def get_item(item_id: str) -> dict:
    """Fetch a single row by primary key."""
    client = _get_client()
    result = await asyncio.to_thread(
        lambda: client.table(TABLE).select("*").eq("id", item_id).single().execute()
    )
    return result.data


async def find_active_item(chat_id: int, status: str) -> Optional[dict]:
    """Return the most recent row for chat_id+status, or None."""
    client = _get_client()
    result = await asyncio.to_thread(
        lambda: client.table(TABLE)
        .select("*")
        .eq("telegram_chat_id", chat_id)
        .eq("status", status)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data
    return rows[0] if rows else None


async def update_item(item_id: str, **fields) -> dict:
    """Patch arbitrary columns; updated_at is set automatically."""
    fields["updated_at"] = _now()
    client = _get_client()
    result = await asyncio.to_thread(
        lambda: client.table(TABLE).update(fields).eq("id", item_id).execute()
    )
    return result.data[0]


async def list_items_by_status(status: str) -> list[dict]:
    """Return all rows with the given status, newest first."""
    client = _get_client()
    result = await asyncio.to_thread(
        lambda: client.table(TABLE)
        .select("*")
        .eq("status", status)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data


async def list_user_items(chat_id: int) -> list[dict]:
    """Return all active rows for a specific user to display status."""
    client = _get_client()
    result = await asyncio.to_thread(
        lambda: client.table(TABLE)
        .select("*")
        .eq("telegram_chat_id", chat_id)
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )
    return result.data
