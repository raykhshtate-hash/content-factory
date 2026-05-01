from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
import httpx
import json
import logging
import os
import asyncio
from aiogram import Bot
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from app.config import settings
from app.services import supabase_service
from app.services.gcs_service import GCSService
from app.services.claude_service import ClaudeService
from app.services.creatomate_service import CreatomateService

router = APIRouter(prefix="/webhooks/creatomate")
logger = logging.getLogger(__name__)

async def process_creatomate_render(item_id: str, payload: dict):
    """Background task to process the rendered video from Creatomate."""
    try:
        status = payload.get("status")
        if status != "succeeded":
            logger.error(f"Creatomate render failed for item {item_id}: {payload}")

            item = await supabase_service.get_item(item_id)

            # Stale-render guard: ignore failure webhooks for an old render_id
            payload_render_id = payload.get("id")
            current_render_id = item.get("creatomate_render_id") if item else None
            if current_render_id and payload_render_id and payload_render_id != current_render_id:
                logger.info(
                    "Stale failure webhook ignored: payload render_id=%s != current=%s",
                    payload_render_id, current_render_id,
                )
                return

            # Auto-recovery: OpenAI moderation_blocked on AI sticker → strip and resubmit once.
            # Idempotent by construction: after stripping, ai_count == 0, so a second hit falls through.
            error_message = payload.get("error_message") or ""
            if "moderation_blocked" in error_message:
                render_source = item.get("render_source") if item else None
                if isinstance(render_source, str):
                    try:
                        render_source = json.loads(render_source)
                    except Exception:
                        render_source = None
                if isinstance(render_source, dict):
                    elements = render_source.get("elements") or []
                    ai_elements = [
                        e for e in elements
                        if e.get("type") == "image"
                        and isinstance(e.get("source"), str)
                        and (e.get("provider") or "").startswith("openai")
                    ]
                    if ai_elements:
                        stripped_elements = [e for e in elements if e not in ai_elements]
                        new_source = {**render_source, "elements": stripped_elements}
                        try:
                            creatomate = CreatomateService()
                            webhook_url = f"{settings.BASE_URL}/webhooks/creatomate/{item_id}"
                            new_render_id = await creatomate.submit_render(new_source, webhook_url)
                            await supabase_service.update_item(
                                item_id,
                                status="rendering",
                                creatomate_render_id=new_render_id,
                                render_source=new_source,
                            )
                            logger.info(
                                "Moderation auto-recovery: stripped %d AI sticker(s), resubmitted item=%s new_render=%s",
                                len(ai_elements), item_id, new_render_id,
                            )
                            if item.get("telegram_chat_id"):
                                bot = Bot(token=settings.BOT_TOKEN)
                                try:
                                    await bot.send_message(
                                        chat_id=item["telegram_chat_id"],
                                        text=(
                                            f"⚠️ OpenAI заблокировал {len(ai_elements)} стикер(ов) "
                                            f"по модерации. Пересобираю рендер без них…"
                                        ),
                                    )
                                finally:
                                    await bot.session.close()
                            return
                        except Exception as e:
                            logger.exception(
                                "Moderation auto-recovery failed for item %s: %s", item_id, e,
                            )
                            # Fall through to render_failed flow below

            await supabase_service.update_item(item_id, status="render_failed")

            # Notify user with retry button (STAB-03)
            if item and item.get("telegram_chat_id"):
                bot = Bot(token=settings.BOT_TOKEN)
                try:
                    retry_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(
                            text="🔄 Повторить рендер",
                            callback_data=f"retry_render:{item_id}"
                        )]
                    ])
                    await bot.send_message(
                        chat_id=item["telegram_chat_id"],
                        text=f"❌ Ошибка рендера видео.\nCreatomate статус: {status}",
                        reply_markup=retry_keyboard,
                    )
                finally:
                    await bot.session.close()
            return

        render_url = payload.get("url")
        if not render_url:
            logger.error(f"Render succeeded but no URL provided for item {item_id}")
            return

        # 1. Download to /tmp (since Telegram limits URL uploads to 20MB, but local to 50MB)
        logger.info(f"Downloading render for {item_id} from {render_url}")
        tmp_path = f"/tmp/render_{item_id}.mp4"
        
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", render_url) as response:
                response.raise_for_status()
                with open(tmp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)

        # 2. Upload to GCS for permanent storage
        gcs_service = GCSService()
        gcs_bucket = settings.GCS_BUCKET_NAME
        gcs_path = f"renders/{item_id}/final.mp4"
        gcs_uri = f"gs://{gcs_bucket}/{gcs_path}"
        
        # We can upload the local file to GCS
        # To avoid adding another method to GCSService, let's just use the GCS Client directly
        await asyncio.to_thread(
            gcs_service._client.bucket(gcs_bucket).blob(gcs_path).upload_from_filename,
            tmp_path
        )
        logger.info(f"Uploading render to GCS: {gcs_uri}")

        # 3. Compliance Gates
        item = await supabase_service.get_item(item_id)
        chat_id = item.get("telegram_chat_id")
        script_text = item.get("script", "")
        
        raw_analysis = item.get("analysis_result")
        visual_risk = "none"
        if raw_analysis:
            analysis = json.loads(raw_analysis) if isinstance(raw_analysis, str) else raw_analysis
            visual_risk = analysis.get("visual_risk", "none")
            
        # TODO: re-enable compliance for medical content only
        # Currently disabled — false positives on lifestyle content
        # claude = ClaudeService(api_key=settings.ANTHROPIC_API_KEY)
        # visual_risk_list = [visual_risk] if str(visual_risk).lower() != "none" else []
        # compliance_result = await claude.check_compliance(script_text, visual_risk_list)
        # is_compliant = compliance_result.get("ok", False)
        # issues = compliance_result.get("issues", [])

        # 4. Delivery via Telegram
        bot = Bot(token=settings.BOT_TOKEN)
        try:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{item_id}")],
                [InlineKeyboardButton(text="🔄 Перемонтаж", callback_data=f"remix:{item_id}")],
                [InlineKeyboardButton(text="🎬 Добавить кадр", callback_data=f"addclip:{item_id}")],
                [InlineKeyboardButton(text="✏️ Переделать", callback_data=f"redo:{item_id}")]
            ])

            caption = "🎬 Твое видео готово!\n"
                
            # Check if already delivered (race condition guard)
            render_id = payload.get("id", "unknown")
            item_check = await supabase_service.get_item(item_id)
            current_status = item_check.get("status") if item_check else None
            logger.info(f"Webhook delivery: item={item_id}, render={render_id}, status={current_status}")
            if current_status in ("pending_approval", "approved", "published"):
                logger.warning(f"Already delivered {item_id}, skipping")
                return

            # Lock: update status BEFORE sending to block duplicate webhooks
            await supabase_service.update_item(item_id, status="pending_approval")

            video_file = FSInputFile(tmp_path)
            await bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=caption,
                reply_markup=keyboard,
                width=1080,
                height=1920,
                supports_streaming=True,
            )

            # Cost breakdown (BILL-03)
            item_with_costs = await supabase_service.get_item(item_id)
            if item_with_costs:
                cost_total = item_with_costs.get("cost_total_usd", 0) or 0
                cost_w = item_with_costs.get("cost_whisper", 0) or 0
                cost_g = item_with_costs.get("cost_gemini", 0) or 0
                cost_c = item_with_costs.get("cost_claude", 0) or 0
                cost_cr = item_with_costs.get("cost_creatomate", 0) or 0

                if cost_total > 0:
                    cost_text = (
                        f"💰 Стоимость рендера: ${cost_total:.2f}\n"
                        f"  Whisper: ${cost_w:.4f}\n"
                        f"  Gemini: ${cost_g:.4f}\n"
                        f"  Claude: ${cost_c:.4f}\n"
                        f"  Creatomate: ${cost_cr:.2f}"
                    )
                    try:
                        await bot.send_message(chat_id=chat_id, text=cost_text)
                    except Exception:
                        logger.warning("Failed to send cost breakdown for item %s", item_id)

        except Exception as e:
            logger.error(f"Failed to send video to Telegram for item {item_id}: {e}")
            await supabase_service.update_item(item_id, status="render_failed")
            try:
                 await bot.send_message(chat_id, f"❌ Видео отрендерилось, но не удалось отправить его в Telegram (возможно слишком большое).")
            except Exception:
                 pass
        finally:
            await bot.session.close()
            # Cleanup tmp file
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    except Exception as e:
        logger.exception(f"Exception in process_creatomate_render for item {item_id}: {e}")


@router.post("/{item_id}")
async def creatomate_webhook(item_id: str, request: Request, background_tasks: BackgroundTasks):
    """Webhook entrypoint for Creatomate."""
    # 1. Extract payload
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    render_id = payload.get("id", "unknown")
    status = payload.get("status", "unknown")
    logger.info(f"Webhook received: render_id={render_id}, status={status}, item_id={item_id}")

    # 2. Idempotency Check
    item = await supabase_service.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
        
    current_status = item.get("status")
    if current_status in ["pending_approval", "approved", "render_failed", "delivering"]:
        logger.info(f"Webhook idempotency stop: item {item_id} already in status {current_status}")
        return {"ok": True, "message": "Already processed"}

    # Immediately mark as delivering to block duplicate webhooks
    await supabase_service.update_item(item_id, status="delivering")

    # 3. Offload to background task so Creatomate gets an immediate 200 OK
    background_tasks.add_task(process_creatomate_render, item_id, payload)
    
    return {"ok": True, "message": "Processing started"}
