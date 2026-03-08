"""
Smoke-test for supabase_service.

Run from the project root:
    python3 -m scripts.test_supabase

Table schema (already created in Supabase):
    content_items (
        id                  uuid PK default gen_random_uuid(),
        user_name           text NOT NULL,
        telegram_chat_id    bigint NOT NULL,
        telegram_message_id bigint,
        status              text NOT NULL DEFAULT 'idea',
        format              text,
        idea_text           text,
        script              text,
        drive_file_ids      text[],
        gcs_uris            text[],
        gcs_render_uri      text,
        analysis_mode       text DEFAULT 'pending',
        analysis_json       jsonb,
        selected_clips      jsonb,
        creatomate_render_id    text,
        creatomate_render_url   text,
        caption             text,
        hashtags            text,
        compliance_json     jsonb,
        compliance_ok       boolean,
        created_at          timestamptz DEFAULT now(),
        updated_at          timestamptz DEFAULT now()
    )
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from app.services import supabase_service as svc


async def main() -> None:
    TEST_CHAT_ID = 999999999  # won't collide with real users

    print("=== create_content_item ===")
    item = await svc.create_content_item(
        user_name="smoke_test",
        chat_id=TEST_CHAT_ID,
        format="reel",
        status="idea",
        idea_text="Test idea",
    )
    print(item)
    item_id = item["id"]
    assert item["status"] == "idea"
    assert item["telegram_chat_id"] == TEST_CHAT_ID

    print("\n=== get_item ===")
    fetched = await svc.get_item(item_id)
    print(fetched)
    assert fetched["id"] == item_id

    print("\n=== find_active_item ===")
    found = await svc.find_active_item(chat_id=TEST_CHAT_ID, status="idea")
    print(found)
    assert found is not None and found["id"] == item_id

    print("\n=== update_item ===")
    updated = await svc.update_item(item_id, status="in_progress", idea_text="Updated text")
    print(updated)
    assert updated["status"] == "in_progress"
    assert updated["idea_text"] == "Updated text"
    assert updated["updated_at"] > item["updated_at"]

    print("\n=== list_items_by_status ===")
    items = await svc.list_items_by_status("in_progress")
    print(f"Found {len(items)} item(s) with status=in_progress")
    assert any(i["id"] == item_id for i in items)

    # Cleanup
    client = svc._get_client()
    client.table(svc.TABLE).delete().eq("id", item_id).execute()
    print(f"\nCleaned up test row {item_id}")

    print("\n✓ All assertions passed.")


if __name__ == "__main__":
    asyncio.run(main())
