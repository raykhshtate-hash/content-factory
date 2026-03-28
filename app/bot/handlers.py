from aiogram import Router, types, F, Bot
from aiogram.filters import Command

from app.config import settings
from app.services.claude_service import ClaudeService
from app.services.whisper_service import WhisperService, analyze_silence
from app.services import supabase_service
from app.bot import messages

import logging
import re

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

    # ── Step 2b: Whisper transcribe voiceover for candidate spans ──
    voiceover_whisper = None
    try:
        vo_signed = await asyncio.to_thread(gcs_service.generate_presigned_url, processed_uri)
        voiceover_whisper = await whisper.transcribe_url_with_timestamps(vo_signed)
        if voiceover_whisper:
            logger.info("Voiceover Whisper: %d words transcribed", len(voiceover_whisper))
    except Exception as e:
        logger.warning("Whisper voiceover transcription failed: %s — stickers will use legacy anchors", e)

    # ── Step 3: Gemini storyboard analysis ──
    await update_progress(status_msg, 3, "Gemini анализирует видео + озвучку...")

    item = await supabase_service.get_item(item_id)
    raw_scenario = item.get("script", "") if item else ""
    scenario_text, style_params = _parse_style_params(raw_scenario)

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

    # Build candidate spans from Whisper voiceover words
    sticker_count = style_params.get("sticker_count", 2) if style_params else 2
    total_dur = sum(c.trim_duration for c in clips)

    # Set clip metadata for _build_candidate_spans compatibility
    for idx, clip in enumerate(clips):
        clip.clip_type = "speech"
        clip.video_index = idx + 1

    whisper_words_for_spans = {}
    if voiceover_whisper and analysis and hasattr(analysis, "scenes"):
        for idx, clip in enumerate(clips):
            scene = analysis.scenes[idx] if idx < len(analysis.scenes) else None
            if scene:
                clip_words = [
                    w for w in voiceover_whisper
                    if scene.audio_start <= w["start"] < scene.audio_end
                ]
                if clip_words:
                    whisper_words_for_spans[str(idx + 1)] = clip_words
        logger.info("Whisper words mapped to %d/%d clips", len(whisper_words_for_spans), len(clips))

    candidate_spans = _build_candidate_spans(whisper_words_for_spans, clips) if whisper_words_for_spans else []

    model_name = "claude-opus-4-6" if quality == "prod" else "claude-sonnet-4-6"
    logger.info("Visual Director model: %s (quality=%s)", model_name, quality)
    director_cost = 0.0

    if candidate_spans:
        # Semantic sticker selection via candidate spans
        blueprint, director_cost = await get_visual_blueprint(
            scenario_text=scenario_text,
            clips=clips_info,
            render_mode="storyboard",
            clip_descriptions=clip_descriptions,
            style_params=style_params,
            candidate_spans=candidate_spans,
            model_name=model_name,
        )

        # Build anchors from Director's selected span IDs
        anchors = []
        if blueprint.get("overlays"):
            span_map = {s["id"]: s for s in candidate_spans}
            for ov in blueprint["overlays"]:
                sid = ov.get("anchor_id")
                if isinstance(sid, str) and sid.isdigit():
                    sid = int(sid)
                if sid and sid in span_map:
                    s = span_map[sid]
                    anchors.append({
                        "anchor_id": str(sid),
                        "phrase": s["text"],
                        "audio_time": s["start"],
                        "clip_index": s["clip_id"],
                        "span_end": s["end"],
                    })
            logger.info("Candidate selection: Director picked %d/%d spans", len(anchors), len(candidate_spans))
    else:
        # Legacy fallback: no Whisper data → description-based anchors
        anchors = _legacy_select_anchors(
            clips=clips,
            whisper_words=None,
            clip_descriptions=clip_descriptions,
            sticker_count=sticker_count,
            total_duration=total_dur,
        )
        blueprint, director_cost = await get_visual_blueprint(
            scenario_text=scenario_text,
            clips=clips_info,
            render_mode="storyboard",
            clip_descriptions=clip_descriptions,
            style_params=style_params,
            anchors=anchors if anchors else None,
            model_name=model_name,
        )

    logger.info(
        "Visual style: %s, reason: %s",
        blueprint["overall_style"],
        blueprint.get("reasoning", "n/a"),
    )
    logger.debug("Visual blueprint: %s", blueprint)

    anchored_overlays = _merge_anchors_into_overlays(blueprint.get("overlays", []), anchors or [])

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
            font_family=blueprint.get("font_family", "Montserrat"),
            subtitle_color=blueprint.get("subtitle_color"),
        )

        source["elements"], transition_count = apply_visual_blueprint(
            source["elements"], blueprint,
            [c.trim_duration for c in clips],
            anchored_overlays=anchored_overlays if anchored_overlays else None,
            clips=clips if anchored_overlays else None,
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

        # ── Cost aggregation + prompt versioning ──
        from app.config import GEMINI_PROMPT_V, DIRECTOR_PROMPT_V
        cost_whisper_val = whisper._last_cost_usd
        cost_gemini_val = gemini._last_cost_usd
        cost_creatomate_val = creatomate._last_cost_usd
        cost_total = cost_whisper_val + cost_gemini_val + director_cost + cost_creatomate_val
        logger.info(
            "Storyboard render cost: total=$%.4f (whisper=$%.4f gemini=$%.4f claude=$%.4f creatomate=$%.4f) | versions: gemini=%s director=%s",
            cost_total, cost_whisper_val, cost_gemini_val, director_cost, cost_creatomate_val,
            GEMINI_PROMPT_V, DIRECTOR_PROMPT_V,
        )

        await supabase_service.update_item(
            item_id,
            status="rendering",
            creatomate_render_id=render_id,
            selected_clips=[c.__dict__ for c in clips],
            render_source=source,  # Save for retry (STAB-03)
            cost_whisper=round(cost_whisper_val, 4),
            cost_gemini=round(cost_gemini_val, 4),
            cost_claude=round(director_cost, 4),
            cost_creatomate=round(cost_creatomate_val, 4),
            cost_total_usd=round(cost_total, 4),
            gemini_prompt_version=GEMINI_PROMPT_V,
            director_prompt_version=DIRECTOR_PROMPT_V,
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
        numbered = all(re.match(r'^\d', f['name']) for f in sb_videos)
        storyboard_submode = "ordered" if numbered else "smart"
        all_files = sb_videos + [sb_audio[0]]  # audio LAST — important for URI split later
        source_label = f"storyboard/{storyboard_submode}"
    else:
        # talking_head: check for optional voiceover audio
        th_audio = [f for f in th_files if f['name'].lower().endswith(AUDIO_EXT)]
        if th_audio:
            th_audio.sort(key=lambda f: f['name'])
            if len(th_audio) > 1:
                logger.warning("Multiple audio files in talking_head, using first: %s", th_audio[0]['name'])
            all_files = th_videos + [th_audio[0]]  # audio LAST — same pattern as storyboard
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
            logger.error("File copy error for %s: %s", file_name, e)
            failed_files.append(file_name)
            continue

        # Delete is best-effort — don't let it kill the pipeline
        try:
            await asyncio.to_thread(drive_service.delete_file, file_id)
        except Exception as e:
            logger.warning("File delete warning for %s (non-fatal): %s", file_name, e)
            
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
        if th_audio and len(gcs_uris) > len(th_videos):
            # Audio was added last in all_files
            video_gcs_uris = gcs_uris[:len(th_videos)]
            voiceover_gcs_uri = gcs_uris[len(th_videos)]
        else:
            video_gcs_uris = gcs_uris
            voiceover_gcs_uri = None

    # Phase update
    if is_storyboard and storyboard_submode == "smart":
        # Smart storyboard: route through talking_head pipeline (Whisper + Gemini analysis)
        try:
            await supabase_service.update_item(
                item_id,
                status="analyzing",
                content_mode="storyboard",
                analysis_mode="smart",
                gcs_uris=video_gcs_uris,
                voiceover_gcs_uri=voiceover_gcs_uri,
            )
        except Exception:
            await supabase_service.update_item(
                item_id, status="analyzing", content_mode="storyboard",
            )

        await analyze_and_propose(message.chat.id, item_id, video_gcs_uris, status_msg)

    elif is_storyboard:
        # Ordered storyboard: existing flow (quality buttons → storyboard_render)
        try:
            await supabase_service.update_item(
                item_id,
                status="ready_for_storyboard",
                content_mode="storyboard",
                analysis_mode="ordered",
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
            "🎬 Storyboard (ordered)! Выбери качество:",
            reply_markup=keyboard,
        )
    else:
        try:
            update_kwargs = dict(
                status="analyzing",
                content_mode="talking_head",
                gcs_uris=video_gcs_uris,
            )
            if voiceover_gcs_uri:
                update_kwargs["voiceover_gcs_uri"] = voiceover_gcs_uri
            await supabase_service.update_item(item_id, **update_kwargs)
        except Exception:
            await supabase_service.update_item(item_id, status="analyzing")

        await analyze_and_propose(message.chat.id, item_id, video_gcs_uris, status_msg)


async def analyze_and_propose(chat_id: int, item_id: str, gcs_uris: list[str], status_msg: types.Message):
    """Background asyncio task for Gemini Video Analysis."""
    await update_progress(status_msg, 2, "Запустил Gemini 3.0 Flash для поиска лучших моментов...")

    item = await supabase_service.get_item(item_id)
    scenario_text = item.get("script", "") if item else ""
    voiceover_gcs_uri = item.get("voiceover_gcs_uri") if item else None
    
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
    
    video_uris = [uri for uri in gcs_uris if uri.lower().endswith(('.mov', '.mp4', '.avi', '.mkv', '.webm'))]
    if not video_uris:
        # Если почему-то расширения из Drive потерялись, берем первый файл на удачу
        if gcs_uris:
            video_uris = [gcs_uris[0]]
        else:
            await update_progress(status_msg, 4, "⚠️ Видео не найдено среди загруженных файлов.")
            await supabase_service.update_item(item_id, status="analysis_failed")
            return

    # ── Whisper audio analysis for clip_type ──
    audio_map = None
    voiceover_data = None
    try:
        gcs_service = GCSService()

        async def _sign(uri):
            return await asyncio.to_thread(gcs_service.generate_presigned_url, uri)

        sign_tasks = [_sign(u) for u in video_uris]
        if voiceover_gcs_uri:
            sign_tasks.append(_sign(voiceover_gcs_uri))
        sign_results = await asyncio.gather(*sign_tasks)
        signed_urls = sign_results[:len(video_uris)]
        vo_signed_url = sign_results[len(video_uris)] if voiceover_gcs_uri else None

        async def _transcribe_one(url):
            words = await whisper.transcribe_url_with_timestamps(url)
            segments = analyze_silence(words) if words else []
            return {"words": words, "segments": segments}

        whisper_tasks = [_transcribe_one(u) for u in signed_urls]
        if vo_signed_url:
            whisper_tasks.append(whisper.transcribe_url_with_segments(vo_signed_url))
        all_results = await asyncio.gather(*whisper_tasks, return_exceptions=True)
        results = all_results[:len(signed_urls)]
        vo_result = all_results[len(signed_urls)] if vo_signed_url else None

        whisper_words = {}  # {video_index_str: [{word, start, end}]}
        map_entries = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.warning("Whisper failed for video %d: %s", i + 1, res)
                continue
            segments = res.get("segments", []) if isinstance(res, dict) else res
            words = res.get("words", []) if isinstance(res, dict) else []
            if segments:
                map_entries.append({"video_index": i + 1, "segments": segments})
            if words:
                whisper_words[str(i + 1)] = words

        if map_entries:
            audio_map = map_entries
            logger.info("Audio map ready: %d/%d videos transcribed", len(map_entries), len(video_uris))
        else:
            logger.warning("All Whisper transcriptions empty/failed — proceeding without audio_map")

        # ── Voiceover transcript for Gemini awareness ──
        if vo_result is not None:
            if isinstance(vo_result, Exception):
                logger.warning("Voiceover transcription failed: %s — proceeding without voiceover awareness", vo_result)
            else:
                voiceover_data = vo_result
                logger.info("Voiceover data ready: %.1fs, %d segments",
                            vo_result["voiceover_duration"], len(vo_result["segments"]))

    except Exception as e:
        logger.error("Whisper pipeline error: %s — proceeding without audio_map", e)
        audio_map = None
        voiceover_data = None
        whisper_words = {}

    gemini = GeminiService()
    analysis = await gemini.analyze_video(video_uris, prompt, audio_map=audio_map, voiceover_data=voiceover_data)
    
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
        result_data = analysis.model_dump()
        if whisper_words:
            result_data["whisper_words"] = whisper_words
        if voiceover_data and voiceover_data.get("words"):
            result_data["voiceover_words"] = voiceover_data["words"]
        if voiceover_data and voiceover_data.get("segments"):
            result_data["voiceover_segments"] = voiceover_data["segments"]
            result_data["voiceover_duration"] = voiceover_data["voiceover_duration"]
        await supabase_service.update_item(
            item_id,
            status="ready_for_render",
            analysis_result=result_data,
            cost_whisper=round(whisper._last_cost_usd, 4),
            cost_gemini=round(gemini._last_cost_usd, 4),
        )
    except Exception as e:
        logger.error("DB save error: %s", e)
        
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
        f"Выбери качество рендера:"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Тест (720p)", callback_data=f"render:recommendation:{item_id}:dev")],
        [InlineKeyboardButton(text="🚀 Продакшн (1080p)", callback_data=f"render:recommendation:{item_id}:prod")],
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

class RedoFeedback(StatesGroup):
    waiting_for_text = State()


def _parse_style_params(text: str) -> tuple[str, dict]:
    """Extract sticker style/quantity params from end of scenario_text.

    Returns (clean_text, params_dict). Params are stripped from text
    so Gemini doesn't see them as part of the script.
    """
    import re
    params = {}
    clean = text

    m = re.search(r'sticker\s+style:?\s*(\w+)', clean, re.IGNORECASE)
    if m:
        params["sticker_style"] = m.group(1).lower()
        clean = clean[:m.start()] + clean[m.end():]

    m = re.search(r'sticker\s+quantity:?\s*(\d+)', clean, re.IGNORECASE)
    if m:
        count = int(m.group(1))
        params["sticker_count"] = min(count, 5)
        clean = clean[:m.start()] + clean[m.end():]

    # Clean up trailing separators/whitespace
    clean = re.sub(r'[,.\s]+$', '', clean.strip())

    return clean, params


def _build_candidate_spans(
    whisper_words: dict[str, list[dict]],
    clips: list,
) -> list[dict]:
    """Build candidate spans from Whisper word timestamps for semantic sticker selection.

    Splits words into spans by pauses (>0.4s) and punctuation, merges short adjacent
    spans within the same clip, drops spans < 1.0s or < 3 words.

    Returns: [{"id": 1, "start": 0.5, "end": 4.2, "text": "...", "clip_id": 0, "word_count": 5}]
    """
    if not whisper_words:
        return []

    # Collect all words with their clip index, filtered to clip trim range
    raw_spans: list[list[tuple[dict, int]]] = []  # list of spans, each span = list of (word, clip_idx)
    current_span: list[tuple[dict, int]] = []

    for clip_idx, clip in enumerate(clips):
        if clip.clip_type != "speech":
            continue
        words = whisper_words.get(str(clip.video_index), [])
        trim_end = clip.trim_start + clip.trim_duration
        clip_words = [
            w for w in words
            if w["start"] >= clip.trim_start and w["start"] < trim_end
        ]

        for i, w in enumerate(clip_words):
            if not current_span:
                current_span.append((w, clip_idx))
            else:
                prev_word, prev_clip = current_span[-1]
                # Split on clip boundary
                if prev_clip != clip_idx:
                    raw_spans.append(current_span)
                    current_span = [(w, clip_idx)]
                # Split on pause > 0.4s
                elif w["start"] - prev_word["end"] > 0.4:
                    raw_spans.append(current_span)
                    current_span = [(w, clip_idx)]
                # Split on punctuation at end of previous word
                elif prev_word["word"].rstrip().endswith((".", "?", "!", ";", ":")):
                    raw_spans.append(current_span)
                    current_span = [(w, clip_idx)]
                else:
                    current_span.append((w, clip_idx))

    if current_span:
        raw_spans.append(current_span)

    # Merge adjacent short spans within same clip
    merged_spans: list[list[tuple[dict, int]]] = []
    for span in raw_spans:
        if not merged_spans:
            merged_spans.append(span)
            continue
        prev = merged_spans[-1]
        prev_clip = prev[0][1]
        curr_clip = span[0][1]
        prev_dur = prev[-1][0]["end"] - prev[0][0]["start"]
        curr_dur = span[-1][0]["end"] - span[0][0]["start"]
        if prev_clip == curr_clip and (prev_dur + curr_dur) < 1.5:
            merged_spans[-1] = prev + span
        else:
            merged_spans.append(span)

    # Post-merge: split spans > 5s by longest internal pause
    split_spans: list[list[tuple[dict, int]]] = []
    for span in merged_spans:
        if not span:
            continue
        duration = span[-1][0]["end"] - span[0][0]["start"]
        if duration <= 5.0:
            split_spans.append(span)
            continue

        # Find longest pause within this span
        best_gap_idx = -1
        best_gap = 0.0
        for j in range(len(span) - 1):
            gap = span[j + 1][0]["start"] - span[j][0]["end"]
            if gap > best_gap:
                best_gap = gap
                best_gap_idx = j

        if best_gap >= 0.4 and best_gap_idx >= 0:
            split_spans.append(span[:best_gap_idx + 1])
            split_spans.append(span[best_gap_idx + 1:])
        else:
            # No suitable pause — keep as-is (trigger_phrase handles precision)
            split_spans.append(span)

    # Build final list, dropping short/thin spans
    candidates: list[dict] = []
    span_id = 1
    for span in split_spans:
        if not span:
            continue
        start = span[0][0]["start"]
        end = span[-1][0]["end"]
        duration = end - start
        word_count = len(span)
        if duration < 1.0 or word_count < 3:
            continue
        text = " ".join(w["word"].strip() for w, _ in span)
        clip_id = span[0][1]
        candidates.append({
            "id": span_id,
            "start": start,
            "end": end,
            "text": text,
            "clip_id": clip_id,
            "word_count": word_count,
        })
        span_id += 1

    logger.info("_build_candidate_spans: %d spans from %d clips", len(candidates), len(clips))
    for c in candidates:
        logger.debug("  span id=%d clip=%d [%.1f-%.1f] %dw: %s", c["id"], c["clip_id"], c["start"], c["end"], c["word_count"], c["text"][:50])

    return candidates


def _normalize_word(w: str) -> str:
    """Lowercase, strip punctuation for fuzzy matching."""
    return re.sub(r'[^\w]', '', w.lower())


def _resolve_trigger_phrases(
    overlays: list[dict],
    anchors: list[dict],
    whisper_words: dict[str, list[dict]],
    clips: list,
) -> list[dict]:
    """Resolve trigger_phrase to precise Whisper timestamps.

    For each anchor with a trigger_phrase, find the word sequence in Whisper data
    and update audio_time to the first matched word's start time.
    """
    if not whisper_words or not anchors:
        return anchors

    anchor_map = {a["anchor_id"]: a for a in anchors}

    # Build anchor_id → trigger_phrase from overlays
    trigger_map: dict[str, str] = {}
    for ov in overlays:
        aid = str(ov.get("anchor_id", ""))
        tp = ov.get("trigger_phrase", "")
        if aid and tp:
            trigger_map[aid] = tp

    for anchor_id, trigger_phrase in trigger_map.items():
        if anchor_id not in anchor_map:
            continue
        anchor = anchor_map[anchor_id]
        span_start = anchor["audio_time"]
        span_end = anchor.get("span_end", span_start + 10)
        clip_idx = anchor["clip_index"]

        clip = clips[clip_idx] if clip_idx < len(clips) else None
        if not clip:
            continue
        words = whisper_words.get(str(clip.video_index), [])

        # Filter to span range
        span_words = [w for w in words if span_start <= w["start"] <= span_end]
        if not span_words:
            continue

        trigger_words = [_normalize_word(tw) for tw in trigger_phrase.split() if _normalize_word(tw)]
        if not trigger_words:
            continue

        # Sequential search for trigger_words in span_words
        found_start = None
        for i in range(len(span_words) - len(trigger_words) + 1):
            match = True
            for j, tw in enumerate(trigger_words):
                if _normalize_word(span_words[i + j]["word"]) != tw:
                    match = False
                    break
            if match:
                found_start = span_words[i]["start"]
                break

        if found_start is not None:
            old_time = anchor["audio_time"]
            anchor["audio_time"] = found_start
            logger.debug(
                "Trigger '%s' → audio_time %.2f (was %.2f)",
                trigger_phrase, found_start, old_time,
            )
        else:
            logger.debug(
                "Trigger '%s' not found in span [%.1f-%.1f], keeping audio_time=%.2f",
                trigger_phrase, span_start, span_end, anchor["audio_time"],
            )

    return anchors


def _legacy_select_anchors(
    clips: list,
    whisper_words: dict[str, list[dict]] | None,
    clip_descriptions: list[str] | None = None,
    sticker_count: int = 2,
    total_duration: float = 0.0,
) -> list[dict]:
    """Select N anchor points from speech/content for sticker placement.

    Returns: [{"anchor_id": "anchor_0", "phrase": str, "audio_time": float, "clip_index": int}]
    """
    if sticker_count <= 0:
        return []

    # Cap sticker count for short reels
    if total_duration > 0 and total_duration < 10:
        sticker_count = min(sticker_count, 1)

    anchors: list[dict] = []

    if whisper_words:
        # ── With Whisper words (talking_head): pick content-rich words ──
        all_words: list[tuple[dict, int]] = []  # (word_dict, clip_index)
        for i, clip in enumerate(clips):
            if clip.clip_type != "speech":
                continue
            words = whisper_words.get(str(clip.video_index), [])
            trim_end = clip.trim_start + clip.trim_duration
            clip_words = [
                w for w in words
                if w["start"] >= clip.trim_start and w["start"] < trim_end
            ]
            for w in clip_words:
                all_words.append((w, i))

        if all_words:
            # Score: longer words score higher (content words tend to be longer)
            scored = []
            for w, ci in all_words:
                text = w["word"].strip()
                score = len(text) * (1.2 if clips[ci].clip_type == "speech" else 1.0)
                scored.append((score, w, ci))
            scored.sort(key=lambda x: -x[0])

            # Select with spacing and diversity constraints
            selected: list[tuple[dict, int]] = []
            clip_counts: dict[int, int] = {}
            min_spacing = max(5.0, total_duration / (sticker_count + 1) * 0.5) if total_duration > 0 else 5.0

            for _score, w, ci in scored:
                if len(selected) >= sticker_count:
                    break
                # Max 2 anchors per clip
                if clip_counts.get(ci, 0) >= 2:
                    continue
                # Spacing check
                too_close = False
                for sel_w, _sel_ci in selected:
                    if abs(w["start"] - sel_w["start"]) < min_spacing:
                        too_close = True
                        break
                if too_close:
                    continue
                selected.append((w, ci))
                clip_counts[ci] = clip_counts.get(ci, 0) + 1

            # Build anchors with phrase context
            for idx, (w, ci) in enumerate(selected):
                # Find neighboring words for phrase context
                words_in_clip = whisper_words.get(str(clips[ci].video_index), [])
                w_idx = next((j for j, ww in enumerate(words_in_clip) if ww["start"] == w["start"]), -1)
                phrase_words = []
                if w_idx >= 0:
                    start_j = max(0, w_idx - 1)
                    end_j = min(len(words_in_clip), w_idx + 2)
                    phrase_words = [words_in_clip[j]["word"].strip() for j in range(start_j, end_j)]
                phrase = " ".join(phrase_words) if phrase_words else w["word"].strip()

                anchors.append({
                    "anchor_id": f"anchor_{idx}",
                    "phrase": phrase,
                    "audio_time": w["start"],
                    "clip_index": ci,
                })
        else:
            # No speech words found — fall through to description-based
            whisper_words = None

    if not whisper_words and clip_descriptions:
        # ── Without Whisper (storyboard fallback): use clip descriptions ──
        n = min(sticker_count, len(clips))
        stride = max(1, len(clips) // n) if n > 0 else 1
        for idx in range(n):
            ci = min(idx * stride, len(clips) - 1)
            clip = clips[ci]
            phrase = clip_descriptions[ci] if ci < len(clip_descriptions) else f"clip {ci}"
            anchors.append({
                "anchor_id": f"anchor_{idx}",
                "phrase": phrase,
                "audio_time": clip.trim_start + clip.trim_duration / 2,
                "clip_index": ci,
            })

    if not anchors and total_duration > 0:
        # ── No data at all: distribute evenly ──
        for idx in range(sticker_count):
            t = (idx + 1) * total_duration / (sticker_count + 1)
            # Map to nearest clip
            cumul = 0.0
            best_ci = 0
            for ci, clip in enumerate(clips):
                if cumul + clip.trim_duration > t:
                    best_ci = ci
                    break
                cumul += clip.trim_duration
            else:
                best_ci = len(clips) - 1
            anchors.append({
                "anchor_id": f"anchor_{idx}",
                "phrase": "",
                "audio_time": t,
                "clip_index": best_ci,
            })

    logger.info("_legacy_select_anchors: %d anchors selected", len(anchors))
    for a in anchors:
        logger.debug("  anchor=%s clip=%d time=%.2f phrase=%r", a["anchor_id"], a["clip_index"], a["audio_time"], a["phrase"][:40])

    return anchors


def _merge_anchors_into_overlays(
    overlays: list[dict],
    anchors: list[dict],
) -> list[dict]:
    """Sync anchor timing data (audio_time, clip_index) into Visual Director overlays.

    _validate_blueprint bakes initial audio_time into overlays, but _resolve_trigger_phrases
    may shift audio_time later. This propagates the updated values.
    """
    if not anchors:
        return overlays
    anchor_map = {str(a["anchor_id"]): a for a in anchors}
    for ov in overlays:
        aid = str(ov.get("anchor_id", ""))
        if aid in anchor_map:
            ov["audio_time"] = anchor_map[aid]["audio_time"]
            ov["clip_index"] = anchor_map[aid]["clip_index"]
    return overlays


def _parse_mmss(mmss: str) -> float:
    """Convert 'MM:SS' or 'MM:SS.s' to seconds.

    Gemini sometimes omits the leading 00: and writes e.g. '10:26.0'
    meaning 10.26 seconds — NOT 10 min 26 sec.  For Reels-length content
    (source videos ≤ ~90 s) a parsed value > 120 s is physically impossible,
    so we re-interpret the colon as a decimal point.
    """
    parts = mmss.split(":")
    result = int(parts[0]) * 60 + float(parts[1])
    if result > 120:
        # Likely SS:fractional, not MM:SS — e.g. "10:26.0" means 10.26s
        # Treat parts[0] as whole seconds, parts[1] as fractional
        reinterpreted = float(parts[0]) + float(parts[1]) / 100
        logger.warning(
            "Timestamp %r parsed as %.1fs (> 120s) — re-interpreted as %.1fs",
            mmss, result, reinterpreted,
        )
        result = reinterpreted
    return result


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
        clip_type = c.get("clip_type", "speech") if isinstance(c, dict) else getattr(c, "clip_type", "speech")
        matched_seg = c.get("matched_voiceover_segment") if isinstance(c, dict) else getattr(c, "matched_voiceover_segment", None)
        clips.append(Clip(
            source=signed_url,
            trim_start=start,
            trim_duration=end - start,
            clip_type=clip_type,
            video_index=v_idx + 1,
            matched_voiceover_segment=matched_seg,
        ))
    return clips


async def _start_render(callback: types.CallbackQuery, item: dict, clips: list[Clip], quality: str = "prod", cost_whisper: float = 0.0, cost_gemini: float = 0.0):
    """Common render logic for all modes. Karaoke subtitles are handled
    natively by Creatomate's transcript_effect — no Whisper needed."""
    item_id = item["id"]
    webhook_url = f"{settings.BASE_URL}/webhooks/creatomate/{item_id}"

    video_format = item.get("format", "reels")

    if not clips:
        await callback.message.edit_text("❌ Нет клипов для монтажа. Попробуй загрузить видео заново и нажми /ready.")
        return

    music_mood = None
    clip_descriptions = None
    whisper_words = None
    voiceover_words = None
    raw_analysis = item.get("analysis_result") or item.get("analysis_json")
    if raw_analysis:
        analysis_data = json.loads(raw_analysis) if isinstance(raw_analysis, str) else raw_analysis
        music_mood = analysis_data.get("suggested_music_mood")
        whisper_words = analysis_data.get("whisper_words")
        voiceover_words = analysis_data.get("voiceover_words")
        candidates = analysis_data.get("clip_candidates", [])
        if candidates:
            descs = [c.get("visual_description", "") for c in candidates]
            if any(descs):
                clip_descriptions = descs

    # Visual Director: Claude picks transitions + sticker overlays
    from app.services.visual_director import get_visual_blueprint
    from app.services.creatomate_service import apply_visual_blueprint

    raw_script = item.get("script", "")
    script_text, style_params = _parse_style_params(raw_script)
    clips_info = [
        {"index": i, "duration": clip.trim_duration}
        for i, clip in enumerate(clips)
    ]

    # Build per-clip context: speech clips get Whisper text, broll keeps Gemini description
    clip_contexts = None
    if whisper_words:
        clip_contexts = []
        for i, clip in enumerate(clips):
            ctx = {
                "clip_type": clip.clip_type,
                "clip_description": clip_descriptions[i] if clip_descriptions and i < len(clip_descriptions) else None,
                "speech_text": None,
            }
            if clip.clip_type == "speech":
                vi_key = str(clip.video_index)
                words = whisper_words.get(vi_key, [])
                clip_words = [
                    w for w in words
                    if w["start"] >= clip.trim_start
                    and w["start"] < clip.trim_start + clip.trim_duration
                ]
                if clip_words:
                    ctx["speech_text"] = " ".join(w["word"] for w in clip_words)
            clip_contexts.append(ctx)

    logger.debug("clip_descriptions for Visual Director: %s", clip_descriptions)

    creatomate = CreatomateService()
    quality_label = "🧪 Dev (720p)" if quality == "dev" else "🚀 Prod (1080p)"
    logger.debug("[Render] video_format=%r, music_mood=%r, clips=%d, quality=%s", video_format, music_mood, len(clips), quality)

    # ── Smart storyboard: force all clips to broll (mute video, voiceover replaces) ──
    content_mode = item.get("content_mode", "talking_head")
    if content_mode == "storyboard":
        for clip in clips:
            clip.clip_type = "broll"
        logger.info("Smart storyboard: forced %d clips to broll", len(clips))

    # ── Hybrid mode: per-clip voiceover for talking_head + smart storyboard ──
    voiceover_segments = None
    signed_voiceover_url = None
    th_voiceover_gcs_uri = item.get("voiceover_gcs_uri")
    if th_voiceover_gcs_uri and content_mode in ("talking_head", "storyboard"):
        # Extract voiceover segments from analysis data (saved by analyze_and_propose)
        voiceover_segments = analysis_data.get("voiceover_segments") if analysis_data else None

        if voiceover_segments:
            # Sign raw voiceover URL for Creatomate per-clip trim
            gcs_service = GCSService()
            try:
                signed_voiceover_url = await asyncio.to_thread(
                    gcs_service.generate_presigned_url, th_voiceover_gcs_uri
                )
            except Exception as e:
                logger.error("Failed to sign voiceover URL: %s", e)
                signed_voiceover_url = None

            # Deduplicate: one segment → one broll (first wins)
            used_segments: set[int] = set()
            for ci, clip in enumerate(clips):
                if clip.matched_voiceover_segment is not None:
                    if clip.matched_voiceover_segment in used_segments:
                        logger.info("[Dedup] clip %d lost segment %d (already used)", ci, clip.matched_voiceover_segment)
                        clip.matched_voiceover_segment = None
                    else:
                        used_segments.add(clip.matched_voiceover_segment)

            # Diagnostic logging
            matched = sum(1 for c in clips if c.matched_voiceover_segment is not None)
            broll_count = sum(1 for c in clips if c.clip_type == "broll")
            logger.info(
                "Per-clip voiceover: %d/%d broll clips matched, %d segments available",
                matched, broll_count, len(voiceover_segments),
            )
            for ci, clip in enumerate(clips):
                if clip.clip_type == "broll":
                    logger.info("[Mapping] clip %d (broll) → Seg %s", ci, clip.matched_voiceover_segment)
        else:
            logger.info("Hybrid mode: no voiceover_segments in analysis — rendering without per-clip audio")

    # ── Post-processing: trim speech clips to first spoken word ──
    if whisper_words:
        for clip in clips:
            if clip.clip_type != "speech":
                continue
            words = whisper_words.get(str(clip.video_index), [])
            trim_end = clip.trim_start + clip.trim_duration
            clip_words = [w for w in words if clip.trim_start <= w["start"] < trim_end]
            if not clip_words:
                logger.warning("Speech trim check: video %d [%.1f-%.1f] — no Whisper words", clip.video_index, clip.trim_start, trim_end)
                continue
            first_word = clip_words[0]
            last_word = clip_words[-1]

            # Trim START: skip silence before first word
            gap = first_word["start"] - clip.trim_start
            if gap > 1.0:
                old_start = clip.trim_start
                new_start = max(first_word["start"] - 0.3, clip.trim_start)
                shift = new_start - clip.trim_start
                clip.trim_start = new_start
                clip.trim_duration -= shift
                logger.info("Speech trim start: video %d, %.1f → %.1f (first word at %.1fs)", clip.video_index, old_start, clip.trim_start, first_word["start"])

            # Trim END: cap at last spoken word + 1s buffer
            last_word_end = last_word["end"] if "end" in last_word else last_word["start"] + 0.5
            ideal_end = last_word_end + 1.0
            current_end = clip.trim_start + clip.trim_duration
            if current_end > ideal_end + 2.0:
                old_dur = clip.trim_duration
                clip.trim_duration = max(1.0, ideal_end - clip.trim_start)
                logger.info("Speech trim end: video %d, duration %.1f → %.1f (last word at %.1fs)", clip.video_index, old_dur, clip.trim_duration, last_word_end)

    # ── Visual Director + anchor-based stickers (AFTER speech trim) ──
    model_name = "claude-opus-4-6" if quality == "prod" else "claude-sonnet-4-6"
    logger.info("Visual Director model: %s (quality=%s)", model_name, quality)

    # Rebuild clips_info with post-trim durations
    clips_info = [
        {"index": i, "duration": clip.trim_duration}
        for i, clip in enumerate(clips)
    ]
    sticker_count = style_params.get("sticker_count", 2) if style_params else 2
    total_dur = sum(c.trim_duration for c in clips)

    # Build candidate spans from Whisper words for semantic selection
    candidate_spans = _build_candidate_spans(whisper_words, clips) if whisper_words else []

    vd_render_mode = "storyboard" if content_mode == "storyboard" else "talking_head"

    if candidate_spans:
        # New path: Director selects from candidates by semantics
        anchors = None
        blueprint, director_cost = await get_visual_blueprint(
            scenario_text=script_text,
            clips=clips_info,
            render_mode=vd_render_mode,
            clip_descriptions=clip_descriptions,
            clip_contexts=clip_contexts,
            style_params=style_params,
            candidate_spans=candidate_spans,
            model_name=model_name,
        )

        # Build anchors from Director's selected span IDs
        if blueprint.get("overlays"):
            span_map = {s["id"]: s for s in candidate_spans}
            anchors = []
            for ov in blueprint["overlays"]:
                sid = ov.get("anchor_id")
                # Parse as int — Director returns int IDs
                if isinstance(sid, str) and sid.isdigit():
                    sid = int(sid)
                if sid and sid in span_map:
                    s = span_map[sid]
                    anchors.append({
                        "anchor_id": str(sid),
                        "phrase": s["text"],
                        "audio_time": s["start"],
                        "clip_index": s["clip_id"],
                        "span_end": s["end"],
                    })
            logger.info("Candidate selection: Director picked %d/%d spans", len(anchors), len(candidate_spans))

            # Resolve trigger phrases to precise Whisper timestamps
            if anchors and whisper_words:
                anchors = _resolve_trigger_phrases(
                    overlays=blueprint.get("overlays", []),
                    anchors=anchors,
                    whisper_words=whisper_words,
                    clips=clips,
                )
        else:
            anchors = []
    else:
        # Fallback: legacy anchor selection
        anchors = _legacy_select_anchors(
            clips=clips,
            whisper_words=whisper_words,
            clip_descriptions=clip_descriptions,
            sticker_count=sticker_count,
            total_duration=total_dur,
        )
        blueprint, director_cost = await get_visual_blueprint(
            scenario_text=script_text,
            clips=clips_info,
            render_mode=vd_render_mode,
            clip_descriptions=clip_descriptions,
            clip_contexts=clip_contexts,
            style_params=style_params,
            anchors=anchors if anchors else None,
            model_name=model_name,
        )

    logger.info(
        "Visual style: %s, reason: %s",
        blueprint["overall_style"],
        blueprint.get("reasoning", "n/a"),
    )
    logger.debug("Visual blueprint: %s", blueprint)

    anchored_overlays = _merge_anchors_into_overlays(blueprint.get("overlays", []), anchors or [])

    # Extract transition offsets from blueprint for subtitle sync
    transition_durations = []
    if blueprint and "clips" in blueprint:
        for j, clip_bp in enumerate(blueprint.get("clips", [])):
            t = clip_bp.get("transition")
            clip_dur = clips[j].trim_duration if j < len(clips) else 0
            transition_durations.append(0.5 if t and clip_dur >= 2.5 else 0.0)

    try:
        source = creatomate.build_source(
            clips=clips,
            video_format=video_format,
            music_mood=music_mood,
            karaoke=True,
            quality=quality,
            whisper_words=whisper_words,
            voiceover_words=voiceover_words,
            transition_durations=transition_durations,
            voiceover_segments=voiceover_segments,
            per_clip_voiceover_url=signed_voiceover_url,
            font_family=blueprint.get("font_family", "Montserrat"),
            subtitle_color=blueprint.get("subtitle_color"),
        )

        source["elements"], _ = apply_visual_blueprint(
            source["elements"], blueprint,
            [c.trim_duration for c in clips],
            anchored_overlays=anchored_overlays if anchored_overlays else None,
            clips=clips if anchored_overlays else None,
        )

        render_id = await creatomate.submit_render(source, webhook_url)

        await supabase_service.update_item(
            item_id,
            status="rendering",
            creatomate_render_id=render_id,
            selected_clips=[c.__dict__ for c in clips],
            render_source=source,  # Save for retry (STAB-03)
        )

        # ── Cost aggregation + prompt versioning ──
        from app.config import GEMINI_PROMPT_V, DIRECTOR_PROMPT_V
        cost_claude = director_cost
        cost_creatomate = creatomate._last_cost_usd
        cost_total = cost_whisper + cost_gemini + cost_claude + cost_creatomate
        logger.info(
            "Render cost: total=$%.4f (whisper=$%.4f gemini=$%.4f claude=$%.4f creatomate=$%.4f) | versions: gemini=%s director=%s",
            cost_total, cost_whisper, cost_gemini, cost_claude, cost_creatomate,
            GEMINI_PROMPT_V, DIRECTOR_PROMPT_V,
        )
        await supabase_service.update_item(
            item_id,
            cost_whisper=round(cost_whisper, 4),
            cost_gemini=round(cost_gemini, 4),
            cost_claude=round(cost_claude, 4),
            cost_creatomate=round(cost_creatomate, 4),
            cost_total_usd=round(cost_total, 4),
            gemini_prompt_version=GEMINI_PROMPT_V,
            director_prompt_version=DIRECTOR_PROMPT_V,
        )

        await callback.message.edit_text(f"🎬 Монтирую ({quality_label})... Пришлю результат когда будет готово.")

    except RuntimeError as e:
        # STAB-04: analysis_result preserved — only status changes on render failure
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
        logger.info("[Callback] Received render action: %s", callback.data)
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

        # Read pre-saved analysis costs (from analyze_and_propose)
        analysis_cost_whisper = item.get("cost_whisper") or 0.0
        analysis_cost_gemini = item.get("cost_gemini") or 0.0

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

            await _start_render(callback, item, clips, quality=quality, cost_whisper=analysis_cost_whisper, cost_gemini=analysis_cost_gemini)

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
            await _start_render(callback, item, clips, quality=quality, cost_whisper=analysis_cost_whisper, cost_gemini=analysis_cost_gemini)

        await callback.answer()

    except Exception as e:
        logger.error("[Callback Error] %s", e, exc_info=True)
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
    await _start_render(ctx, item, clips, quality="prod", cost_whisper=item.get("cost_whisper") or 0.0, cost_gemini=item.get("cost_gemini") or 0.0)


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


# ── Redo feedback FSM handler (MUST be before fallback text handler) ──

@router.message(RedoFeedback.waiting_for_text, F.text)
async def process_redo_feedback(message: types.Message, state: FSMContext):
    """Classify feedback via Haiku and store (per D-21, D-22, D-24, D-25)."""
    data = await state.get_data()
    item_id = data.get("item_id")

    if not item_id:
        await state.clear()
        return

    feedback_text = message.text
    progress_msg = await message.answer("Анализирую замечание...")

    try:
        from app.services.claude_service import classify_feedback
        from app.config import settings as _settings
        from datetime import datetime, timezone

        # Classify via Haiku (per D-21)
        classification = await classify_feedback(feedback_text, _settings.ANTHROPIC_API_KEY)

        # Build emoji response (per D-22, D-26)
        labels = []
        if classification.get("gemini_instruction"):
            labels.append("🎬 выбор кадров")
        if classification.get("director_instruction"):
            labels.append("🎨 визуальный стиль")

        if labels:
            label_str = " + ".join(labels)
            response_text = f"Поняла! Замечание касается: {label_str}. Учту при следующем рендере."
        else:
            response_text = "Поняла! Учту при следующем рендере."

        # Store feedback (per D-24)
        feedback_entry = {
            "text": feedback_text,
            "classification": classification,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Append to feedback_history array
        item = await supabase_service.get_item(item_id)
        existing_history = item.get("feedback_history", []) if item else []
        if not isinstance(existing_history, list):
            existing_history = []
        existing_history.append(feedback_entry)

        # Update item: feedback + status (per D-25)
        await supabase_service.update_item(
            item_id,
            feedback_history=existing_history,
            status="redo_requested",
        )

        try:
            await progress_msg.delete()
        except Exception:
            pass
        await message.answer(response_text)

    except Exception as e:
        logger.error("[Redo Feedback Error] %s", e)
        try:
            await progress_msg.delete()
        except Exception:
            pass
        await message.answer("Замечание сохранено. Учту при следующем рендере.")
        # Still save feedback even on classification failure
        try:
            from datetime import datetime, timezone
            feedback_entry = {
                "text": feedback_text,
                "classification": {"gemini_instruction": feedback_text, "director_instruction": None},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            item = await supabase_service.get_item(item_id)
            existing_history = item.get("feedback_history", []) if item else []
            if not isinstance(existing_history, list):
                existing_history = []
            existing_history.append(feedback_entry)
            await supabase_service.update_item(
                item_id,
                feedback_history=existing_history,
                status="redo_requested",
            )
        except Exception:
            pass

    await state.clear()


# Fallback text handler MUST be registered LAST
@router.message(F.text, ~Command("start", "help", "status", "reels", "post", "carousel", "stories", "ready"))
async def handle_text(message: types.Message):
    await _process_idea(message, message.text)


# ── Approve / Redo callback handlers ─────────────────────────────────────

@router.callback_query(F.data.startswith("approve:"))
async def on_approve_video(callback: types.CallbackQuery):
    try:
        logger.info("[Callback] Received approve action: %s", callback.data)
        _, item_id = callback.data.split(":", 1)

        await supabase_service.update_item(item_id, status="approved")

        # We edit the caption to remove the keyboard and show it's approved
        new_caption = callback.message.caption or "🎬 Видео одобрено"
        if "✅ Одобрить" not in new_caption:
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
        logger.error("[Callback Error] approve: %s", e)


@router.callback_query(F.data.startswith("redo:"))
async def on_redo_video(callback: types.CallbackQuery, state: FSMContext):
    """User wants to redo -- ask for feedback text (per D-20)."""
    try:
        _, item_id = callback.data.split(":", 1)
        logger.info("[Callback] Received redo action: %s", callback.data)

        # Remove keyboard to prevent double-tap
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await state.set_state(RedoFeedback.waiting_for_text)
        await state.update_data(item_id=item_id)
        await callback.message.answer("Что изменить? Опиши замечание свободным текстом")
        await callback.answer()
    except Exception as e:
        logger.error("[Callback Error] redo: %s", e)


# ── Retry Render (STAB-03) ──

@router.callback_query(F.data.startswith("retry_render:"))
async def on_retry_render(callback: types.CallbackQuery):
    """Re-submit a failed render to Creatomate using saved source JSON (STAB-03)."""
    item_id = callback.data.split(":")[1]

    # Idempotency: disable the button
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await callback.answer("Повторяю рендер...")

    item = await supabase_service.get_item(item_id)
    if not item:
        await callback.message.answer("Запись не найдена")
        return

    # Get saved render source
    render_source = item.get("render_source")
    if not render_source:
        await callback.message.answer(
            "❌ Не удалось найти данные рендера. Попробуй /ready заново."
        )
        return

    # Parse if stored as string
    if isinstance(render_source, str):
        render_source = json.loads(render_source)

    # Re-submit to Creatomate
    webhook_url = f"{settings.BASE_URL}/webhooks/creatomate/{item_id}"

    try:
        creatomate = CreatomateService()
        render_id = await creatomate.submit_render(render_source, webhook_url)

        await supabase_service.update_item(
            item_id,
            status="rendering",
            creatomate_render_id=render_id,
        )

        await callback.message.answer(
            "🔄 Рендер отправлен повторно. Пришлю результат когда будет готово."
        )
        logger.info("Retry render submitted for item %s, new render_id=%s", item_id, render_id)

    except Exception as e:
        logger.error("Retry render failed for item %s: %s", item_id, e)
        await supabase_service.update_item(item_id, status="render_failed")
        await callback.message.answer(
            "❌ Повторный рендер не удался. Попробуй /ready заново."
        )
