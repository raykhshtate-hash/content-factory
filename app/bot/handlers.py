from aiogram import Router, types, F, Bot
from aiogram.filters import Command

from app.config import settings
from app.services.claude_service import ClaudeService
from app.services.whisper_service import WhisperService
from app.services import supabase_service
from app.bot import messages

import logging

logger = logging.getLogger(__name__)

router = Router()

# Initialize AI Services
claude = ClaudeService(api_key=settings.ANTHROPIC_API_KEY)
whisper = WhisperService(api_key=settings.OPENAI_API_KEY)

# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(messages.WELCOME_MESSAGE)

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(messages.HELP_MESSAGE)

@router.message(Command("status"))
async def cmd_status(message: types.Message):
    items = await supabase_service.list_user_items(message.chat.id)
    if not items:
        await message.answer("У тебя пока нет активных идей.")
        return

    text_lines = ["Твои идеи:\n"]
    for idx, item in enumerate(items, 1):
        fmt = item.get("format", "unknown")
        status = item.get("status", "unknown")
        idea = item.get("idea_text") or "Без описания"
        short_idea = idea[:30] + "..." if len(idea) > 30 else idea
        text_lines.append(f"{idx}. {fmt} - {status}\n   📝 {short_idea}")
    
    await message.answer("\n".join(text_lines))

@router.message(Command("reels", "post", "carousel", "stories"))
async def cmd_format(message: types.Message):
    # Extract the format from the command (e.g., "/reels" -> "reels")
    command_text = message.text.split()[0].lower()
    content_format = command_text[1:] 

    item = await supabase_service.create_content_item(
        user_name=message.from_user.full_name,
        chat_id=message.chat.id,
        format=content_format,
        status="idea",
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="У меня есть готовый сценарий 📝", callback_data=f"script:paste_ready:{item['id']}")]
    ])
    
    await message.answer(messages.FORMAT_SELECTED_MESSAGE.format(format=content_format), reply_markup=keyboard)


from app.services.drive_service import DriveService
from app.services.gcs_service import GCSService
from app.services.gemini_service import GeminiService, VideoAnalysis
from app.services.creatomate_service import CreatomateService, Clip
from app.services.timeline_utils import map_broll_to_render_timeline
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import asyncio
import json
import uuid

async def update_progress(message: types.Message, stage: int, text: str = ""):
    stages = [
        "[⬜⬜⬜⬜⬜] Ищу видео в Drive...",
        "[🟩⬜⬜⬜⬜] Копирую в облако...",
        "[🟩🟩⬜⬜⬜] Анализирую видео...",
        "[🟩🟩🟩⬜⬜] Обрабатываю результат...",
        "[🟩🟩🟩🟩🟩] Готово!"
    ]
    bar = stages[min(stage, len(stages) - 1)]
    try:
        await message.edit_text(f"{bar}\n\n{text}".strip())
    except Exception:
        pass

@router.message(Command("ready"))
async def cmd_ready(message: types.Message):
    # 1. Idempotency Check
    active_item = await supabase_service.find_active_item(message.chat.id, "awaiting_footage")
    
    if not active_item:
        proc_item = await supabase_service.find_active_item(message.chat.id, "processing_video")
        analyzing_item = await supabase_service.find_active_item(message.chat.id, "analyzing")
        if proc_item or analyzing_item:
            await message.answer("⏳ Твое видео уже находится в процессе обработки! Пожалуйста, подожди.")
            return
            
        await message.answer("У тебя нет сценариев, ожидающих видео. Создай новый сценарий (например через /reels).")
        return

    item_id = active_item["id"]
    await supabase_service.update_item(item_id, status="processing_video")
    
    status_msg = await message.answer("[⬜⬜⬜⬜⬜] Ищу видео в Drive...")
    
    drive_service = DriveService()
    gcs_service = GCSService()
    
    # 2. Drive → GCS sync
    try:
        inbox_files = drive_service.list_inbox_files()
        video_extensions = ('.mov', '.mp4', '.avi', '.mkv', '.webm')
        inbox_files = [f for f in inbox_files if f['name'].lower().endswith(video_extensions)]
    except Exception as e:
        await update_progress(status_msg, 0, f"❌ Ошибка доступа к Google Drive: {e}")
        await supabase_service.update_item(item_id, status="awaiting_footage")
        return
        
    if not inbox_files:
        await update_progress(status_msg, 0, "⚠️ В папке INBOX пусто! Пожалуйста, загрузи туда видео и нажми /ready снова.")
        await supabase_service.update_item(item_id, status="awaiting_footage")
        return
        
    await update_progress(status_msg, 1, f"Найдено файлов: {len(inbox_files)}.")
    
    gcs_bucket = settings.GCS_BUCKET_NAME
    gcs_uris = []
    failed_files = []
    
    for idx, file_meta in enumerate(inbox_files, 1):
        file_id = file_meta['id']
        file_name = file_meta['name']
        gcs_path = f"footage/{item_id}/{uuid.uuid4().hex[:8]}_{file_name}"
        
        try:
            await update_progress(status_msg, 1, f"Файл {idx}/{len(inbox_files)}: {file_name}\n(Видео скачиваются 10-15 сек/файл)...")
            gcs_uri = await asyncio.to_thread(drive_service.copy_to_gcs, file_id, gcs_bucket, gcs_path)
            gcs_uris.append(gcs_uri)
        except Exception as e:
            print(f"File copy error for {file_name}: {e}")
            failed_files.append(file_name)
            continue
        
        # Delete is best-effort — don't let it kill the pipeline
        try:
            await asyncio.to_thread(drive_service.delete_file, file_id)
        except Exception as e:
            print(f"File delete warning for {file_name} (non-fatal): {e}")
            
    if not gcs_uris:
        await update_progress(status_msg, 1, f"❌ Не удалось перенести ни одного файла. Ошибок: {len(failed_files)}")
        await supabase_service.update_item(item_id, status="awaiting_footage")
        return
        
    error_summary = f"\n⚠️ Ошибок доступа (фото/файлы): {len(failed_files)}" if failed_files else ""
    await update_progress(status_msg, 2, f"Успешно скопировано: {len(gcs_uris)}{error_summary}")
        
    # Phase update
    try:
        await supabase_service.update_item(item_id, status="analyzing", gcs_uris=gcs_uris)
    except Exception:
        # Fallback if gcs_uris column doesn't exist in Supabase yet
        await supabase_service.update_item(item_id, status="analyzing")
    
    # 3. Fire background task for Gemini
    # We're already inside a BackgroundTask from the webhook handler,
    # so a plain await keeps the CPU alive in Cloud Run.
    await analyze_and_propose(message.chat.id, item_id, gcs_uris, status_msg)


async def analyze_and_propose(chat_id: int, item_id: str, gcs_uris: list[str], status_msg: types.Message):
    """Background asyncio task for Gemini Video Analysis."""
    await update_progress(status_msg, 2, "Запустил Gemini 3.0 Flash для поиска лучших моментов...")
    
    item = await supabase_service.get_item(item_id)
    scenario_text = item.get("script", "") if item else ""
    
    prompt = (
        f"Тебе передано {len(gcs_uris)} видео. "
        "Проанализируй их и найди самые виральные моменты для Reels/Shorts. "
        "Оцени силу хука от 1 до 10, визуальные риски и уверенность.\n\n"
    )
    
    if scenario_text:
        prompt += (
            "СЦЕНАРИЙ ДЛЯ ПОИСКА:\n"
            f"{scenario_text}\n\n"
            "ТВОЯ ГЛАВНАЯ ЗАДАЧА: Найди фрагменты на видео, где спикер произносит фразы, максимально близкие по смыслу или тексту к этому сценарию. "
            "Твои выбранные моменты должны собираться в этот сценарий.\n\n"
        )
        
    prompt += (
        "ВАЖНОЕ ПРАВИЛО ПО ЗВУКУ: Выбирай моменты, ориентируясь на РЕЧЬ и ЗВУК. "
        "Таймкоды `start_time` и `end_time` должны строго соответствовать началу и концу логической фразы человека. "
        "КАТЕГОРИЧЕСКИ ЗАПРЕЩАЕТСЯ обрывать слова на середине или лексически не завершать фразу. "
        "Каждый клип должен звучать как цельное, законченное высказывание.\n\n"
        "КРИТИЧНО: Для каждого момента ОБЯЗАТЕЛЬНО укажи `video_index` (от 1 до N, в порядке загрузки), "
        "а `start_time` и `end_time` строй ОТНОСИТЕЛЬНО НАЧАЛА ЭТОГО КОНКРЕТНОГО ВИДЕО."
    )
    
    video_uris = [uri for uri in gcs_uris if uri.lower().endswith(('.mov', '.mp4', '.avi', '.mkv', '.webm'))]
    if not video_uris:
        # Если почему-то расширения из Drive потерялись, берем первый файл на удачу
        if gcs_uris:
            video_uris = [gcs_uris[0]]
        else:
            await update_progress(status_msg, 4, "⚠️ Видео не найдено среди загруженных файлов.")
            await supabase_service.update_item(item_id, status="analysis_failed")
            return

    # ПРОБЛЕМА 2 ИСПРАВЛЕНА: Передаем весь список video_uris
    gemini = GeminiService()
    analysis = await gemini.analyze_video(video_uris, prompt)
    
    if not analysis:
        # Fallback Mode
        await update_progress(status_msg, 4, "⚠️ Ошибка анализа Gemini. Продолжаем без ИИ-разметки.")
        await supabase_service.update_item(item_id, status="analysis_failed")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Без анализа ⚡", callback_data=f"render:skip:{item_id}")]
        ])
        
        try:
            await status_msg.edit_text(
                "[🟩🟩🟩🟩🟩] Готово!\n\n⚠️ Не удалось проанализировать видео.\nЧто делаем дальше?",
                reply_markup=keyboard
            )
        except Exception:
            pass
        return
        
    # Success Mode
    await update_progress(status_msg, 3, "Формирую результаты...")
    
    try:
        await supabase_service.update_item(
            item_id, 
            status="ready_for_render",
            analysis_result=analysis.model_dump() # Сохраняем как словарь (Supabase автоматически конвертирует в JSON)
        )
    except Exception as e:
        print(f"⚠️ Ошибка при сохранении в БД: {e}")
        
    # Format message
    risks_warning = f"⚠️ Риски: {analysis.visual_risk}" if str(analysis.visual_risk).lower() != "none" else "✅ Рисков нет"
    music_recommendation = f"🎵 Рекомендуемая музыка (для Reels/TikTok): {analysis.suggested_music_mood}" if analysis.suggested_music_mood else ""

    text = (
        f"[🟩🟩🟩🟩🟩] Готово!\n\n"
        f"📊 Результаты ИИ-анализа:\n"
        f"🪝 Сила хука: {analysis.hook_score}/10\n"
        f"🤖 Уверенность: {int(analysis.confidence * 100)}%\n"
        f"🎬 Кандидатов: {len(analysis.clip_candidates)}\n"
        f"{risks_warning}\n"
        f"{music_recommendation}\n\n"
        f"Выбери дальнейший шаг:"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Рекомендация 🎯", callback_data=f"render:recommendation:{item_id}")],
        [InlineKeyboardButton(text="Выбрать свои 🎬", callback_data=f"render:custom:{item_id}")],
        [InlineKeyboardButton(text="Без анализа ⚡", callback_data=f"render:skip:{item_id}")]
    ])
    
    try:
        await status_msg.edit_text(text, reply_markup=keyboard)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Render Callback Handlers
# ---------------------------------------------------------------------------

class ClipSelection(StatesGroup):
    waiting_for_clips = State()

class ScriptEdit(StatesGroup):
    chatting = State()
    pasting_ready = State()


def _parse_mmss(mmss: str) -> float:
    """Convert 'MM:SS' or 'MM:SS.s' to seconds."""
    parts = mmss.split(":")
    return int(parts[0]) * 60 + float(parts[1])


async def _candidates_to_clips(candidates, gcs_uris: list[str], gcs_service: GCSService) -> list[Clip]:
    """Convert ClipCandidate list to Creatomate Clip list with signed URLs."""
    clips = []
    _signed_cache: dict[str, str] = {}

    for c in candidates:
        if isinstance(c, dict):
            start = _parse_mmss(c["start_time"])
            end = _parse_mmss(c["end_time"])
            v_idx = c.get("video_index", 1) - 1
        else:
            start = _parse_mmss(c.start_time)
            end = _parse_mmss(c.end_time)
            v_idx = getattr(c, "video_index", 1) - 1

        if v_idx < 0 or v_idx >= len(gcs_uris):
            v_idx = 0

        gcs_uri = gcs_uris[v_idx]
        if gcs_uri not in _signed_cache:
            _signed_cache[gcs_uri] = await asyncio.to_thread(
                gcs_service.generate_presigned_url, gcs_uri
            )
        signed_url = _signed_cache[gcs_uri]
        clips.append(Clip(source=signed_url, trim_start=start, trim_duration=end - start))
    return clips


async def _start_render(callback: types.CallbackQuery, item: dict, clips: list[Clip], quality: str = "prod"):
    """Common render logic for all modes. Karaoke subtitles are handled
    natively by Creatomate's transcript_effect — no Whisper needed."""
    item_id = item["id"]
    webhook_url = f"{settings.BASE_URL}/webhooks/creatomate/{item_id}"

    video_format = item.get("format", "reels")

    if not clips:
        await callback.message.edit_text("❌ Нет клипов для монтажа. Попробуй загрузить видео заново и нажми /ready.")
        return

    music_mood = None
    raw_analysis = item.get("analysis_result") or item.get("analysis_json")
    if raw_analysis:
        analysis_data = json.loads(raw_analysis) if isinstance(raw_analysis, str) else raw_analysis
        music_mood = analysis_data.get("suggested_music_mood")

    # B-roll overlays: Claude generates AI image prompts
    script_text = item.get("script", "")
    broll_overlays = []
    if script_text and raw_analysis:
        analysis_data = json.loads(raw_analysis) if isinstance(raw_analysis, str) else raw_analysis
        candidates = analysis_data.get("clip_candidates", [])
        if candidates:
            # Convert MM:SS.s timecodes to seconds for Claude
            clip_secs = []
            for c in candidates:
                clip_secs.append({
                    "video_index": c.get("video_index", 1),
                    "start_sec": _parse_mmss(c["start_time"]),
                    "end_sec": _parse_mmss(c["end_time"]),
                    "reason": c.get("reason", ""),
                })
            try:
                broll_prompts = await claude.generate_broll_prompts(script_text, clip_secs)

                # Calculate total render duration
                total_render_duration = sum(c.trim_duration for c in clips)

                # Remap source timecodes → render timeline
                clips_as_dicts = [c.__dict__ for c in clips]
                broll_overlays = map_broll_to_render_timeline(
                    broll_prompts, clips_as_dicts, total_render_duration
                )
                logger.info("[Render] B-roll: %d AI prompts generated", len(broll_overlays))
            except Exception as e:
                logger.warning("B-roll generation failed (non-fatal): %s", e)

    creatomate = CreatomateService()
    quality_label = "🧪 Dev (720p)" if quality == "dev" else "🚀 Prod (1080p)"
    logger.debug("[Render] video_format=%r, music_mood=%r, clips=%d, quality=%s, broll=%d", video_format, music_mood, len(clips), quality, len(broll_overlays))
    try:
        render_id = await creatomate.create_render(
            clips=clips,
            video_format=video_format,
            music_mood=music_mood,
            karaoke=True,
            quality=quality,
            broll_overlays=broll_overlays or None,
            webhook_url=webhook_url,
        )

        await supabase_service.update_item(
            item_id,
            status="rendering",
            creatomate_render_id=render_id,
            selected_clips=[c.__dict__ for c in clips],
        )
        await callback.message.edit_text(f"🎬 Монтирую ({quality_label})... Пришлю результат когда будет готово.")

    except RuntimeError as e:
        logger.error(f"Render initialization completely failed: {e}")
        await supabase_service.update_item(item_id, status="awaiting_footage")
        try:
            await callback.message.answer("❌ Произошла ошибка при запуске сборки видео (сервер не отвечает). Попробуй нажать /ready ещё раз.")
        except Exception:
            pass


@router.callback_query(F.data.startswith("render:"))
async def on_render_callback(callback: types.CallbackQuery, state: FSMContext):
    try:
        print(f"🎯 [Callback] Received render action: {callback.data}")
        parts = callback.data.split(":")
        mode = parts[1]
        item_id = parts[2]
        # quality is optional 4th part, default "prod"
        quality = parts[3] if len(parts) > 3 else None

        item = await supabase_service.get_item(item_id)
        if not item:
            await callback.answer("Запись не найдена", show_alert=True)
            return

        gcs_uris = item.get("gcs_uris") or []
        if not gcs_uris:
            await callback.answer("Нет видео для монтажа.", show_alert=True)
            return

        gcs_service = GCSService()

        # ── Step 1: If quality not yet chosen, show dev/prod buttons ──
        if quality is None and mode in ("recommendation", "raw", "skip"):
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧪 Dev (720p, быстро)", callback_data=f"render:{mode}:{item_id}:dev")],
                [InlineKeyboardButton(text="🚀 Prod (1080p, финал)", callback_data=f"render:{mode}:{item_id}:prod")],
            ])
            try:
                await callback.message.edit_text("Выбери качество рендера:", reply_markup=keyboard)
            except Exception:
                pass  # message already updated (duplicate click)
            await callback.answer()
            return

        # Default quality for custom flow (user already waited for clip selection)
        if quality is None:
            quality = "prod"

        # ── Step 2: Build clips and render ────────────────────────
        if mode == "recommendation":
            raw = item.get("analysis_result") or item.get("analysis_json")
            if not raw:
                await callback.answer("Нет данных анализа.", show_alert=True)
                return

            analysis = json.loads(raw) if isinstance(raw, str) else raw
            candidates = analysis.get("clip_candidates", [])
            clips = await _candidates_to_clips(candidates, gcs_uris, gcs_service)

            if not clips:
                # No recommended clips — fall back to using full raw videos
                logger.warning("[Render] No clip candidates, falling back to raw video clips")
                clips = []
                for uri in gcs_uris:
                    signed_url = await asyncio.to_thread(
                        gcs_service.generate_presigned_url, uri
                    )
                    clips.append(Clip(source=signed_url, trim_start=0.0, trim_duration=15.0))

            await _start_render(callback, item, clips, quality=quality)

        elif mode == "custom":
            raw = item.get("analysis_result") or item.get("analysis_json")
            if not raw:
                await callback.answer("Нет данных анализа.", show_alert=True)
                return

            analysis = json.loads(raw) if isinstance(raw, str) else raw
            candidates = analysis.get("clip_candidates", [])

            lines = []
            for i, c in enumerate(candidates, 1):
                start = c["start_time"]
                end = c["end_time"]
                reason = c.get("reason", "")
                lines.append(f"{i}. {start}–{end}  {reason}")

            await callback.message.edit_text(
                "Доступные клипы:\n\n" + "\n".join(lines)
                + "\n\nНапиши номера через запятую (например: 1,3,4):"
            )
            await state.set_state(ClipSelection.waiting_for_clips)
            await state.update_data(item_id=item_id, candidates=candidates, gcs_uris=gcs_uris)

        elif mode in ("raw", "skip"):
            signed_url = await asyncio.to_thread(
                gcs_service.generate_presigned_url, gcs_uris[0]
            )
            clips = [
                Clip(source=signed_url, trim_start=i * 3.0, trim_duration=3.0)
                for i in range(4)
            ]
            await _start_render(callback, item, clips, quality=quality)

        await callback.answer()

    except Exception as e:
        import traceback
        print(f"❌ [Callback Error] {e}")
        traceback.print_exc()
        await callback.answer("Произошла ошибка при обработке кнопки.", show_alert=True)


@router.message(ClipSelection.waiting_for_clips, F.text)
async def on_custom_clip_numbers(message: types.Message, state: FSMContext):
    data = await state.get_data()
    item_id = data["item_id"]
    candidates = data["candidates"]
    gcs_uris = data["gcs_uris"]

    try:
        indices = [int(x.strip()) - 1 for x in message.text.split(",")]
        selected = [candidates[i] for i in indices if 0 <= i < len(candidates)]
    except (ValueError, IndexError):
        await message.answer("Не могу разобрать номера. Напиши через запятую, например: 1,3,4")
        return

    if not selected:
        await message.answer("Ни один клип не выбран. Попробуй ещё раз.")
        return

    await state.clear()

    item = await supabase_service.get_item(item_id)
    gcs_service = GCSService()
    clips = await _candidates_to_clips(selected, gcs_uris, gcs_service)

    class _Ctx:
        pass
    ctx = _Ctx()
    ctx.message = message
    await _start_render(ctx, item, clips, quality="prod")


# ---------------------------------------------------------------------------
# Message Handlers
# ---------------------------------------------------------------------------

async def _process_idea(message: types.Message, idea_text: str):
    """Core logic for text and voice messages."""
    # Find active item with status="idea"
    active_item = await supabase_service.find_active_item(message.chat.id, "idea")
    
    if active_item:
        # Flow 1: Complete the idea -> script pipeline
        await message.answer(messages.IDEA_SAVED_MESSAGE)
        
        item_id = active_item["id"]
        content_format = active_item["format"]
        
        # 1. Update idea text in DB
        await supabase_service.update_item(item_id, idea_text=idea_text)
        
        # 2. Generate script via Claude
        script = await claude.generate_script(content_format, idea_text)
        
        # 3. Save script — wait for user approval
        await supabase_service.update_item(
            item_id,
            status="awaiting_script_approval",
            script=script
        )

        # 4. Send script with approve/edit buttons
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Одобрить ✅", callback_data=f"script:approve:{item_id}")],
            [InlineKeyboardButton(text="Редактировать ✏️", callback_data=f"script:edit:{item_id}")]
        ])
        await message.answer(messages.SCRIPT_READY_MESSAGE.format(script=script), reply_markup=keyboard)
    else:
        # No active format selected -> Ideation mode / suggest formats
        await message.answer("Анализирую твою идею...")
        formats = await claude.suggest_formats(idea_text)
        fmt_string = ", ".join(f"/{f}" for f in formats)
        
        # We create a placeholder item in "ideation" status
        await supabase_service.create_content_item(
            user_name=message.from_user.full_name,
            chat_id=message.chat.id,
            format="pending",
            status="ideation",
            idea_text=idea_text
        )
        
        await message.answer(
            f"Отличная идея! Рекомендую эти форматы: {fmt_string}.\n\n"
            f"Выбери один из них (нажми на команду), чтобы создать сценарий!"
        )

@router.message(F.voice)
async def handle_voice(message: types.Message, bot: Bot):
    await message.answer("Распознаю аудио...")

    # Download file in-memory
    file_info = await bot.get_file(message.voice.file_id)
    audio_bytes_io = await bot.download_file(file_info.file_path)
    audio_bytes = audio_bytes_io.read()

    # Transcribe
    try:
        text = await whisper.transcribe(audio_bytes, filename="voice.ogg")
        await message.answer(f"Распознанный текст:\n{text}")
        await _process_idea(message, text)
    except Exception as e:
        await message.answer(f"Произошла ошибка при распознавании аудио.")


# ---------------------------------------------------------------------------
# Script Edit Handlers
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("script:"))
async def on_script_action(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    action = parts[1]
    item_id = parts[2]

    if action == "approve":
        await supabase_service.update_item(item_id, status="awaiting_footage")
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.message.answer(messages.AWAITING_FOOTAGE_MESSAGE)
        await callback.answer("Сценарий одобрен! Переходим к загрузке видео.")
        await state.clear()
        
    elif action == "edit":
        # Get the current script from DB to start the history
        item = await supabase_service.get_item(item_id)
        current_script = item.get("script", "") if item else ""
        
        # Initialize history array
        history = [
            {"role": "assistant", "content": f"Вот текущий сценарий:\n\n{current_script}"}
        ]
        
        await state.set_state(ScriptEdit.chatting)
        await state.update_data(item_id=item_id, history=history)
        await callback.message.answer(messages.EDIT_SCRIPT_PROMPT)
        await callback.answer()
        
    elif action == "paste_ready":
        await state.set_state(ScriptEdit.pasting_ready)
        await state.update_data(item_id=item_id)
        await callback.message.answer("Отлично! Отправь мне свой готовый сценарий одним сообщением. Я сохраню его, и мы перейдём к загрузке видео 🎬")
        await callback.answer()


@router.message(ScriptEdit.chatting, F.text)
async def process_script_edit(message: types.Message, state: FSMContext):
    data = await state.get_data()
    item_id = data.get("item_id")
    history = data.get("history", [])
    
    if not item_id:
        await state.clear()
        return

    user_prompt = message.text
    progress_msg = await message.answer("Переписываю сценарий... ⏳")
    
    try:
        # Call Claude with history
        new_script = await claude.refine_script(history, user_prompt)
        
        # Update DB
        await supabase_service.update_item(
            item_id,
            script=new_script,
        )
        
        # Update FSM history with the new turn
        history.append({"role": "user", "content": user_prompt})
        history.append({"role": "assistant", "content": new_script})
        await state.update_data(history=history)
        
        # Send new version with the same approve/edit buttons to continue or finish
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Одобрить ✅", callback_data=f"script:approve:{item_id}")],
            [InlineKeyboardButton(text="Ещё правки ✏️", callback_data=f"script:edit:{item_id}")]
        ])
        
        await progress_msg.delete()
        await message.answer(f"Новая версия сценария:\n\n{new_script}", reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Script edit error: {e}")
        await progress_msg.edit_text("❌ Произошла ошибка при редактировании. Попробуй еще раз.")


@router.message(ScriptEdit.pasting_ready, F.text)
async def process_ready_script(message: types.Message, state: FSMContext):
    data = await state.get_data()
    item_id = data.get("item_id")
    if not item_id:
        await state.clear()
        return

    ready_script = message.text
    
    await supabase_service.update_item(
        item_id,
        status="awaiting_footage",
        script=ready_script,
    )
    await state.clear()

    await message.answer("✅ Сценарий сохранён!")
    await message.answer(messages.AWAITING_FOOTAGE_MESSAGE)


# Fallback text handler MUST be registered LAST
@router.message(F.text, ~Command("start", "help", "status", "reels", "post", "carousel", "stories", "ready"))
async def handle_text(message: types.Message):
    await _process_idea(message, message.text)
@router.callback_query(F.data.startswith("approve:"))
async def on_approve_video(callback: types.CallbackQuery):
    try:
        print(f"🎯 [Callback] Received approve action: {callback.data}")
        _, item_id = callback.data.split(":", 1)
        
        await supabase_service.update_item(item_id, status="approved")
        
        # We edit the caption to remove the keyboard and show it's approved
        new_caption = callback.message.caption or "🎬 Видео одобрено"
        if "✅ Одобрить и опубликовать" not in new_caption:
            new_caption = f"✅ Видео одобрено и готово к публикации!\n\n---\n{new_caption}"
            
        try:
            await callback.message.edit_caption(
                caption=new_caption,
                reply_markup=None
            )
        except Exception:
            pass
            
        await callback.answer("Видео одобрено!")
    except Exception as e:
        print(f"❌ [Callback Error] {e}")


@router.callback_query(F.data.startswith("reject:"))
async def on_reject_video(callback: types.CallbackQuery):
    try:
        print(f"🎯 [Callback] Received reject action: {callback.data}")
        _, item_id = callback.data.split(":", 1)
        
        await supabase_service.update_item(item_id, status="rejected")
        
        new_caption = callback.message.caption or "❌ Видео отклонено"
        if "❌ Видео отклонено" not in new_caption:
            new_caption = f"❌ Видео отправлено на переделку.\n\n---\n{new_caption}"
            
        try:
            await callback.message.edit_caption(
                caption=new_caption,
                reply_markup=None
            )
        except Exception:
            pass
            
        await callback.answer("Видео отклонено (статус: rejected)")
    except Exception as e:
        print(f"❌ [Callback Error] {e}")
