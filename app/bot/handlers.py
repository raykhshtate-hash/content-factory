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
# map_broll_to_render_timeline kept in timeline_utils.py for potential future Pexels/GIF use
from app.services.audio_processing import process_voiceover, get_duration as get_media_duration
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import asyncio
import json
import subprocess
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

async def storyboard_render(
    chat_id: int,
    item_id: str,
    video_gcs_uris: list[str],
    voiceover_gcs_uri: str,
    status_msg: types.Message,
    quality: str = "dev",
):
    """Storyboard render: process audio + Gemini scene mapping + Creatomate."""

    gcs_service = GCSService()

    # ── Step 1: Get video durations via ffprobe ──
    await update_progress(status_msg, 2, "Storyboard: определяю длительность видео...")

    video_durations = []
    for uri in video_gcs_uris:
        signed = await asyncio.to_thread(gcs_service.generate_presigned_url, uri)
        dur = await get_media_duration(signed)
        if dur <= 0:
            dur = 5.0
            logger.warning("Could not get duration for %s, fallback 5.0s", uri)
        video_durations.append(dur)

    total_video_duration = sum(video_durations)
    logger.info("Video durations: %s (total=%.1fs)", video_durations, total_video_duration)

    # ── Step 2: Process voiceover (silence removal + speedup) ──
    await update_progress(status_msg, 2,
        f"Обрабатываю озвучку (убираю паузы, подгоняю скорость)...\n"
        f"Видео: {total_video_duration:.0f}с"
    )

    try:
        processed_uri, processed_duration, speedup = await process_voiceover(
            voiceover_gcs_uri=voiceover_gcs_uri,
            total_video_duration=total_video_duration,
            video_durations=video_durations,
            max_speedup=1.5,
            silence_threshold_sec=1.0,
        )
    except Exception as e:
        logger.error("Audio processing failed: %s", e)
        # Fallback: use original audio without processing
        processed_uri = voiceover_gcs_uri
        signed = await asyncio.to_thread(gcs_service.generate_presigned_url, voiceover_gcs_uri)
        processed_duration = await get_media_duration(signed)
        speedup = 1.0
        await update_progress(status_msg, 2, "⚠️ Не удалось обработать аудио, использую оригинал.")
        await asyncio.sleep(2)

    duration_diff = abs(processed_duration - total_video_duration)
    if duration_diff > 3.0:
        await update_progress(status_msg, 2,
            f"⚠️ После обработки: аудио {processed_duration:.0f}с, видео {total_video_duration:.0f}с "
            f"(разница {duration_diff:.0f}с, ускорение {speedup:.1f}x).\n"
            f"Продолжаю — Gemini подгонит таймкоды."
        )
        await asyncio.sleep(2)
    else:
        await update_progress(status_msg, 2,
            f"Аудио обработано: {processed_duration:.0f}с (ускорение {speedup:.1f}x)."
        )

    # ── Step 3: Gemini storyboard analysis ──
    await update_progress(status_msg, 3, "Gemini анализирует видео + озвучку...")

    item = await supabase_service.get_item(item_id)
    scenario_text = item.get("script", "") if item else ""

    gemini = GeminiService()
    analysis = await gemini.analyze_storyboard(
        video_gcs_uris=video_gcs_uris,
        audio_gcs_uri=processed_uri,
        scenario_text=scenario_text,
        video_durations=video_durations,
        audio_duration=processed_duration,
    )

    if not analysis:
        # Fallback: equal split without Gemini
        logger.warning("Gemini storyboard analysis failed, using equal split")
        segment_duration = processed_duration / len(video_gcs_uris) if video_gcs_uris else 5.0

        clips = []
        for uri in video_gcs_uris:
            signed = await asyncio.to_thread(gcs_service.generate_presigned_url, uri)
            clips.append(Clip(
                source=signed,
                trim_start=0.0,
                trim_duration=segment_duration,
            ))

        voiceover_signed = await asyncio.to_thread(
            gcs_service.generate_presigned_url, processed_uri
        )

    else:
        # Safety net: cap scenes that exceed video clip by more than 0.5s
        for scene in analysis.scenes:
            v_idx = scene.video_index - 1
            if 0 <= v_idx < len(video_durations):
                max_dur = video_durations[v_idx]
                clip_dur = scene.audio_end - scene.audio_start
                overflow = clip_dur - max_dur
                if overflow > 0.5:
                    logger.warning(
                        "Scene %d: audio %.1fs exceeds video %.1fs by %.1fs — capping",
                        scene.scene_id, clip_dur, max_dur, overflow,
                    )
                    scene.audio_end = scene.audio_start + max_dur

        # ── Step 4: Build clips from Gemini analysis ──
        clips = []
        for scene in analysis.scenes:
            v_idx = scene.video_index - 1
            if v_idx < 0 or v_idx >= len(video_gcs_uris):
                v_idx = 0

            signed = await asyncio.to_thread(
                gcs_service.generate_presigned_url, video_gcs_uris[v_idx]
            )
            clip_duration = scene.audio_end - scene.audio_start
            clips.append(Clip(
                source=signed,
                trim_start=scene.video_trim_start,
                trim_duration=clip_duration,
            ))

        voiceover_signed = await asyncio.to_thread(
            gcs_service.generate_presigned_url, processed_uri
        )

    # ── Step 4b: Visual Director — Claude picks transitions + stickers ──
    from app.services.visual_director import get_visual_blueprint
    from app.services.creatomate_service import apply_visual_blueprint

    clips_info = [
        {"index": i, "duration": clip.trim_duration}
        for i, clip in enumerate(clips)
    ]

    # Extract visual descriptions from Gemini storyboard analysis
    clip_descriptions = None
    if analysis and hasattr(analysis, "scenes"):
        descs = [getattr(s, "visual_description", "") for s in analysis.scenes]
        logger.info(f"Clip descriptions: {descs}")
        if any(descs):
            clip_descriptions = descs

    blueprint = await get_visual_blueprint(
        scenario_text=scenario_text,
        clips=clips_info,
        render_mode="storyboard",
        clip_descriptions=clip_descriptions,
    )

    logger.info(
        "Visual style: %s, reason: %s",
        blueprint["overall_style"],
        blueprint.get("reasoning", "n/a"),
    )
    logger.debug("Visual blueprint: %s", blueprint)

    # ── Step 5: Build source, apply blueprint, audio compensation ──
    await update_progress(status_msg, 3, "Запускаю рендер...")

    webhook_url = f"{settings.BASE_URL}/webhooks/creatomate/{item_id}"
    video_format = item.get("format", "reels") if item else "reels"

    music_mood = "professional"
    if analysis and analysis.suggested_music_mood:
        music_mood = analysis.suggested_music_mood

    creatomate = CreatomateService()

    try:
        source = creatomate.build_source(
            clips=clips,
            video_format=video_format,
            music_mood=music_mood,
            karaoke=True,
            quality=quality,
            voiceover_url=voiceover_signed,
            voiceover_duration=processed_duration,
        )

        source["elements"], transition_count = apply_visual_blueprint(
            source["elements"], blueprint,
            [c.trim_duration for c in clips],
        )

        # Audio compensation: voiceover may be longer than video timeline
        if transition_count > 0:
            from app.services.audio_processing import adjust_voiceover_for_transitions

            total_clip_duration = sum(c.trim_duration for c in clips)
            transition_overlap = transition_count * 0.5
            video_timeline = total_clip_duration - transition_overlap
            real_gap = processed_duration - video_timeline

            if real_gap > 0.5:
                new_vo_uri, new_vo_duration = await adjust_voiceover_for_transitions(
                    voiceover_gcs_uri=processed_uri,
                    overlap_seconds=real_gap,
                    current_duration=processed_duration,
                )
                logger.info(
                    f"Audio gap: voiceover={processed_duration:.1f}s, "
                    f"video_timeline={video_timeline:.1f}s, "
                    f"gap={real_gap:.1f}s"
                )
                new_vo_url = await asyncio.to_thread(
                    gcs_service.generate_presigned_url, new_vo_uri
                )
                for el in source["elements"]:
                    if el.get("id") == "voiceover":
                        el["source"] = new_vo_url
                    if el.get("transcript_source") == "voiceover":
                        el["duration"] = new_vo_duration

        render_id = await creatomate.submit_render(source, webhook_url)

        await supabase_service.update_item(
            item_id,
            status="rendering",
            creatomate_render_id=render_id,
            selected_clips=[c.__dict__ for c in clips],
        )

        quality_label = "🧪 Dev (720p)" if quality == "dev" else "🚀 Prod (1080p)"
        await update_progress(status_msg, 4,
            f"🎬 Storyboard ({quality_label})! Ускорение {speedup:.1f}x.\n"
            f"Монтирую... Пришлю результат когда будет готово."
        )
    except RuntimeError as e:
        logger.error(f"Storyboard render failed: {e}")
        await supabase_service.update_item(item_id, status="awaiting_footage")
        try:
            await status_msg.edit_text(
                "❌ Ошибка запуска рендера. Попробуй нажать /ready ещё раз."
            )
        except Exception:
            pass


async def _get_duration_ffprobe(url: str) -> float:
    """Get media duration via ffprobe from a URL. Reads only headers — fast."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("ffprobe failed (rc=%d): %s", result.returncode, result.stderr[:200])
            return 0.0
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except Exception as e:
        logger.warning("ffprobe exception: %s", e)
        return 0.0


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

    # 2. Check both folders
    try:
        th_files = drive_service.list_talking_head_files()
        sb_files = drive_service.list_storyboard_files()
    except Exception as e:
        await update_progress(status_msg, 0, f"❌ Ошибка доступа к Google Drive: {e}")
        await supabase_service.update_item(item_id, status="awaiting_footage")
        return

    VIDEO_EXT = ('.mov', '.mp4', '.avi', '.mkv', '.webm')
    AUDIO_EXT = ('.mp3', '.m4a', '.wav', '.aac', '.ogg')

    th_videos = [f for f in th_files if f['name'].lower().endswith(VIDEO_EXT)]
    sb_videos = [f for f in sb_files if f['name'].lower().endswith(VIDEO_EXT)]
    sb_audio = [f for f in sb_files if f['name'].lower().endswith(AUDIO_EXT)]

    has_th = len(th_videos) > 0
    has_sb = len(sb_videos) > 0

    if has_th and has_sb:
        await update_progress(status_msg, 0,
            "⚠️ Файлы найдены в обеих папках (talking_head и storyboard).\n"
            "Оставь файлы только в одной и нажми /ready снова."
        )
        await supabase_service.update_item(item_id, status="awaiting_footage")
        return

    if not has_th and not has_sb:
        await update_progress(status_msg, 0,
            "⚠️ Обе папки пусты.\n"
            "Загрузи видео в INBOX/talking_head или INBOX/storyboard и нажми /ready."
        )
        await supabase_service.update_item(item_id, status="awaiting_footage")
        return

    is_storyboard = has_sb

    if is_storyboard:
        if not sb_audio:
            await update_progress(status_msg, 0,
                "⚠️ В папке storyboard нет аудиофайла с озвучкой.\n"
                "Загрузи озвучку (.mp3 / .m4a / .wav) вместе с видео и нажми /ready."
            )
            await supabase_service.update_item(item_id, status="awaiting_footage")
            return

        if len(sb_audio) > 1:
            sb_audio.sort(key=lambda f: f['name'])
            logger.warning("Multiple audio files in storyboard, using first: %s", sb_audio[0]['name'])

        sb_videos.sort(key=lambda f: f['name'])
        all_files = sb_videos + [sb_audio[0]]  # audio LAST — important for URI split later
        source_label = "storyboard"
    else:
        all_files = th_videos
        source_label = "talking_head"

    await update_progress(status_msg, 1,
        f"Найдено файлов: {len(all_files)} ({source_label})."
    )
    
    gcs_bucket = settings.GCS_BUCKET_NAME
    gcs_uris = []
    failed_files = []
    
    for idx, file_meta in enumerate(all_files, 1):
        file_id = file_meta['id']
        file_name = file_meta['name']
        gcs_path = f"footage/{item_id}/{uuid.uuid4().hex[:8]}_{file_name}"

        try:
            await update_progress(status_msg, 1, f"Файл {idx}/{len(all_files)}: {file_name}\n(Видео скачиваются 10-15 сек/файл)...")
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
        
    # Split URIs based on mode
    if is_storyboard:
        # Audio was added last in all_files, so last GCS URI is voiceover
        video_gcs_uris = gcs_uris[:-1]
        voiceover_gcs_uri = gcs_uris[-1]
    else:
        video_gcs_uris = gcs_uris
        voiceover_gcs_uri = None

    # Phase update
    if is_storyboard:
        try:
            await supabase_service.update_item(
                item_id,
                status="ready_for_storyboard",
                content_mode="storyboard",
                gcs_uris=video_gcs_uris,
                voiceover_gcs_uri=voiceover_gcs_uri,
            )
        except Exception:
            await supabase_service.update_item(
                item_id,
                status="ready_for_storyboard",
                content_mode="storyboard",
            )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🧪 Dev (720p, быстро)",
                callback_data=f"storyboard:dev:{item_id}"
            )],
            [InlineKeyboardButton(
                text="🚀 Prod (1080p, финал)",
                callback_data=f"storyboard:prod:{item_id}"
            )],
        ])
        await status_msg.edit_text(
            "🎬 Storyboard mode! Выбери качество:",
            reply_markup=keyboard,
        )
    else:
        try:
            await supabase_service.update_item(
                item_id,
                status="analyzing",
                content_mode="talking_head",
                gcs_uris=video_gcs_uris,
            )
        except Exception:
            await supabase_service.update_item(item_id, status="analyzing")

        await analyze_and_propose(message.chat.id, item_id, video_gcs_uris, status_msg)


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
            # DISABLED: scene_label prompt instructions — changed Gemini clip selection behavior
            # "СТРУКТУРА СЦЕНАРИЯ: Типичный сценарий Reels состоит из сцен:\n"
            # "- HOOK (хук, первые 1-3 секунды) — захватывающее начало\n"
            # "- STORY (основная часть) — развитие истории, факты, советы\n"
            # "- PIVOT (опциональный поворот/контраст)\n"
            # "- CLOSING (финал) — вывод, призыв к действию\n\n"
            # "Для каждого клипа укажи scene_label (HOOK/STORY/PIVOT/CLOSING) — к какой сцене сценария относится этот момент.\n\n"
        )
        
    prompt += (
        "ВАЖНОЕ ПРАВИЛО ПО ЗВУКУ: Выбирай моменты, ориентируясь на РЕЧЬ и ЗВУК. "
        "Таймкоды `start_time` и `end_time` должны строго соответствовать началу и концу логической фразы человека. "
        "КАТЕГОРИЧЕСКИ ЗАПРЕЩАЕТСЯ обрывать слова на середине или лексически не завершать фразу. "
        "Каждый клип должен звучать как цельное, законченное высказывание.\n\n"
        "КРИТИЧНО: Для каждого момента ОБЯЗАТЕЛЬНО укажи `video_index` (от 1 до N, в порядке загрузки), "
        "а `start_time` и `end_time` строй ОТНОСИТЕЛЬНО НАЧАЛА ЭТОГО КОНКРЕТНОГО ВИДЕО."
    )

    # ── Diversity & dedup instructions (GEMINI_PROMPT_V 1.1) ──
    prompt += (
        "\n\nПРАВИЛА РАЗНООБРАЗИЯ B-ROLL (ОБЯЗАТЕЛЬНЫ):\n"
        "- ДЕДУПЛИКАЦИЯ: Не используй один и тот же исходный фрагмент более 2 раз. "
        "Если видео одно — выбирай РАЗНЫЕ временные отрезки с минимум 5 секунд расстояния между началами клипов.\n"
        "- РАЗНООБРАЗИЕ: Чередуй визуальные типы кадров (крупный план, средний план, детали, движение). "
        "Не ставь подряд два клипа с одинаковым visual_description.\n"
        "- АНТИ-ЛИНЕЙНОСТЬ: НЕ выбирай клипы строго по порядку таймлайна. "
        "Лучший монтаж — когда порядок клипов отличается от хронологии видео. "
        "Перемешивай broll клипы по визуальной логике, а не по времени."
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

    # TODO: old broll pipeline, replaced by visual_director
    # Keep code for potential Pexels/GIF future use
    # script_text = item.get("script", "")
    # broll_overlays = []
    # if script_text and raw_analysis:
    #     ...

    # Visual Director: Claude picks transitions + sticker overlays
    from app.services.visual_director import get_visual_blueprint
    from app.services.creatomate_service import apply_visual_blueprint

    script_text = item.get("script", "")
    clips_info = [
        {"index": i, "duration": clip.trim_duration}
        for i, clip in enumerate(clips)
    ]

    blueprint = await get_visual_blueprint(
        scenario_text=script_text,
        clips=clips_info,
        render_mode="talking_head",
    )

    logger.info(
        "Visual style: %s, reason: %s",
        blueprint["overall_style"],
        blueprint.get("reasoning", "n/a"),
    )
    logger.debug("Visual blueprint: %s", blueprint)

    creatomate = CreatomateService()
    quality_label = "🧪 Dev (720p)" if quality == "dev" else "🚀 Prod (1080p)"
    logger.debug("[Render] video_format=%r, music_mood=%r, clips=%d, quality=%s", video_format, music_mood, len(clips), quality)
    try:
        source = creatomate.build_source(
            clips=clips,
            video_format=video_format,
            music_mood=music_mood,
            karaoke=True,
            quality=quality,
        )

        source["elements"], _ = apply_visual_blueprint(
            source["elements"], blueprint,
            [c.trim_duration for c in clips],
        )

        render_id = await creatomate.submit_render(source, webhook_url)

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


@router.callback_query(F.data.startswith("storyboard:"))
async def on_storyboard_quality(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    quality = parts[1]
    item_id = parts[2]

    item = await supabase_service.get_item(item_id)
    if not item:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    # Idempotency: block duplicate clicks
    current_status = item.get("status", "")
    if current_status in ("rendering", "pending_approval", "approved"):
        await callback.answer("⏳ Уже обрабатывается!", show_alert=True)
        return

    # Immediately mark as rendering + answer callback to stop Telegram retries
    await supabase_service.update_item(item_id, status="rendering")
    await callback.answer()

    # Remove keyboard to prevent re-clicks
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    video_gcs_uris = item.get("gcs_uris") or []
    voiceover_gcs_uri = item.get("voiceover_gcs_uri")

    if not video_gcs_uris or not voiceover_gcs_uri:
        await supabase_service.update_item(item_id, status="awaiting_footage")
        try:
            await callback.message.edit_text("❌ Нет файлов для рендера.")
        except Exception:
            pass
        return

    await storyboard_render(
        callback.message.chat.id,
        item_id,
        video_gcs_uris,
        voiceover_gcs_uri,
        callback.message,
        quality=quality,
    )


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

            # DISABLED: scene_label sorting — caused clip trimming issues
            # # Sort by scene_label if present (HOOK → STORY → PIVOT → CLOSING)
            # scene_order = {"HOOK": 0, "STORY": 1, "PIVOT": 2, "CLOSING": 3}
            # if candidates and any(c.get("scene_label") for c in candidates):
            #     candidates = sorted(
            #         candidates,
            #         key=lambda c: scene_order.get(c.get("scene_label", "").upper(), 999)
            #     )
            #     logger.info("[Render] Sorted %d clips by scene_label", len(candidates))

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
        
        # 2. Passthrough: сценарий уже готов, не генерируем
        # script = await claude.generate_script(content_format, idea_text)
        logger.info("Scenario passthrough: using idea_text as script")

        # 3. Save script directly, skip approval
        await supabase_service.update_item(
            item_id,
            status="awaiting_footage",
            script=idea_text,
        )

        # 4. Prompt user to upload footage
        await message.answer("Сценарий принят. Загрузи видео в Drive INBOX и нажми /ready")
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
