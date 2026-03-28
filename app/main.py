import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
# App-level loggers at DEBUG
logging.getLogger("app").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)

from aiogram import Bot, Dispatcher

from app.config import settings
from app.bot.handlers import router as bot_router
from app.webhooks.creatomate_webhook import router as webhook_router

# Initialize bot and dispatcher
# Default parse_mode can be set if needed
bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher()
dp.include_router(bot_router)

polling_task = None

import os
from fastapi import Request

@asynccontextmanager
async def lifespan(app: FastAPI):
    # on_startup
    logger.info("Starting Content Factory Application...")

    from app.services.supabase_service import _get_client
    try:
        _get_client()
        logger.info("Supabase client initialized")
    except Exception as e:
        logger.warning("Failed to initialize Supabase: %s", e)

    global polling_task
    # K_SERVICE is set automatically by Google Cloud Run.
    # If not set, we assume local development mode -> use polling.
    if not os.getenv("K_SERVICE"):
        logger.info("Local dev mode: Starting Telegram Bot polling...")
        # Since we're polling locally, make sure to delete any leftover webhooks
        await bot.delete_webhook()
        polling_task = asyncio.create_task(dp.start_polling(bot))
    else:
        logger.info("Cloud Run mode: Awaiting Telegram Webhooks...")

    yield

    # on_shutdown
    logger.info("Shutting down Application...")

    if polling_task:
        logger.info("Stopping Bot polling...")
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass

    await bot.session.close()
    logger.info("Shutdown complete")

# Create FastAPI App
app = FastAPI(title="Content Factory API", lifespan=lifespan)

# Include webhooks
app.include_router(webhook_router)

from fastapi import BackgroundTasks

# Telegram Webhook endpoint (only used on Cloud Run)
@app.post("/webhook")
async def bot_webhook(request: Request, background_tasks: BackgroundTasks):
    from aiogram.types import Update
    data = await request.json()
    logger.debug("[Webhook] Received raw update: %s", data)

    try:
        telegram_update = Update(**data)
        # CRITICAL GCP CLOUD RUN FIX:
        # Offload the update processing to FastAPI BackgroundTasks.
        # This allows the webhook to immediately return 200 OK to Telegram,
        # while FastAPI keeps the request context alive long enough for Gemini (50s).
        # Cloud Run will keep CPU allocated until all background tasks finish
        # as long as we use standard FastAPI BackgroundTasks!
        background_tasks.add_task(dp.feed_update, bot, telegram_update)

    except Exception as e:
        logger.error("[Webhook] Failed to process update: %s", e)
        
    return {"ok": True}

if __name__ == "__main__":
    # Local development mode entrypoint
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
