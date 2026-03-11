import json
import re
from pathlib import Path
from anthropic import AsyncAnthropic

# Load system prompt once at module level
_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "claude_system.txt"
SYSTEM_PROMPT = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()

MODEL = "claude-sonnet-4-6"

FORMAT_DESCRIPTIONS = {
    "reels":    "вертикальное видео 60 сек (Reels / TikTok / Shorts)",
    "post":     "текстовый пост для Instagram / ВКонтакте",
    "carousel": "карусель 7–10 слайдов с заголовками и подписями",
    "stories":  "серия вертикальных Stories 5–7 экранов",
}


class ClaudeService:
    def __init__(self, api_key: str):
        self.client = AsyncAnthropic(api_key=api_key)

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    async def _chat(self, user_prompt: str, max_tokens: int = 2048) -> str:
        """Send a single-turn message and return the assistant's text."""
        message = await self.client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text.strip()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_script(self, format: str, idea_text: str) -> str:
        """
        Generate a content script for the given format.

        :param format: One of 'reels', 'post', 'carousel', 'stories'.
        :param idea_text: Raw idea or voice transcription from the doctor.
        :return: Ready-to-use script in Russian.
        """
        fmt_desc = FORMAT_DESCRIPTIONS.get(format, format)
        prompt = (
            f"Формат контента: {fmt_desc}\n\n"
            f"Идея врача:\n{idea_text}\n\n"
            "Напиши готовый сценарий на русском языке для этого формата. "
            "Соблюдай структуру, характерную для формата. "
            "Используй простой, но профессиональный язык. "
            "Добавь хук в начале и призыв к действию в конце."
        )
        return await self._chat(prompt, max_tokens=2048)

    async def refine_script(self, history: list[dict], user_prompt: str) -> str:
        """
        Refine an existing script based on conversational history.
        
        :param history: List of dicts [{"role": "user"|"assistant", "content": str}]
        :param user_prompt: The newest requested edit.
        :return: Updated script text.
        """
        messages = history.copy()
        messages.append({"role": "user", "content": user_prompt})
        
        response = await self.client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        return response.content[0].text.strip()

    async def check_compliance(self, caption: str, visual_risks: list[str]) -> dict:
        """
        Check a caption (and visual risks) for medical compliance.

        :param caption: The text of the post / caption to verify.
        :param visual_risks: List of potential visual compliance risks (may be empty).
        :return: {
            "ok": bool,
            "issues": list[str],   # empty if ok
            "fixed_caption": str   # corrected version, or original if ok
          }
        """
        risks_block = ""
        if visual_risks:
            risks_block = (
                "\n\nВизуальные риски, выявленные на видео:\n"
                + "\n".join(f"- {r}" for r in visual_risks)
            )

        prompt = (
            "Проверь следующий текст на соответствие медицинскому комплаенсу "
            "(российское законодательство о рекламе медицинских услуг, ФЗ-38):\n\n"
            f"«{caption}»"
            f"{risks_block}\n\n"
            "Ответь строго в формате JSON (без markdown-обёртки):\n"
            '{\n'
            '  "ok": true | false,\n'
            '  "issues": ["<issue1>", ...],\n'
            '  "fixed_caption": "<исправленный текст или оригинал если ok>"\n'
            '}'
        )
        raw = await self._chat(prompt, max_tokens=1024)

        # Strip accidental markdown code fences if the model adds them
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE).strip()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: return raw text as a non-ok result so caller never crashes
            result = {
                "ok": False,
                "issues": ["Не удалось разобрать ответ модели: " + raw[:200]],
                "fixed_caption": caption,
            }
        return result

    async def generate_overlays(self, script: str, total_duration: float) -> list[dict]:
        """
        Claude as director: reads the script and generates pop-up overlay
        instructions (emoji + optional short text) with precise timing.
        Each overlay gets a mood that determines its pill background color.
        """
        prompt = (
            f"Ты — креативный режиссёр монтажа Instagram Reels.\n\n"
            f"Вот сценарий видео (длительность {total_duration:.1f} сек):\n\n"
            f"{script}\n\n"
            f"ЗАДАЧА: Придумай 3-5 pop-up элементов (эмодзи или эмодзи + короткий текст до 3 слов), "
            f"которые появляются поверх видео в ключевые моменты.\n\n"
            f"ПРАВИЛА:\n"
            f"- Каждый pop-up длится 2.5-3.5 секунды\n"
            f"- НЕ перекрывай первые 1.5 секунды (хук) и последнюю секунду\n"
            f"- Минимум 3 секунды между pop-ups (они не должны перекрываться!)\n"
            f"- Используй эмодзи как основу: 🤔 💡 ✨ 🧴 💆‍♀️ 🪞 ❤️ 👀 🔥 и т.д.\n"
            f"- Можно добавить максимум 2 коротких слова к эмодзи. Текст должен быть ОЧЕНЬ коротким: '✨ Секрет', '🧴 3 средства', '💡 Факт'\n"
            f"- position: 'top-left', 'top-right', 'center', 'bottom-left', 'bottom-right'\n"
            f"- НЕ ставь в bottom-center — там субтитры\n"
            f"- Чередуй позиции (не ставь все в одно место)\n"
            f"- size: 'small' (акцент), 'medium' (основной), 'large' (wow-момент)\n"
            f"- mood: 'question' (вопрос/сомнение), 'insight' (инсайт/совет), 'positive' (позитив/результат), 'warning' (важно/осторожно), 'default'\n"
            f"- Привязывай mood к контексту: вопрос → question, совет → insight, результат → positive\n\n"
            f"Ответь ТОЛЬКО валидным JSON массивом, без markdown:\n"
            f'[{{"time": 3.0, "duration": 3.0, "content": "🧴 Совет", "position": "top-right", "size": "medium", "mood": "insight"}}]'
        )

        raw = await self._chat(prompt, max_tokens=1024)
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE).strip()

        VALID_MOODS = {"default", "question", "insight", "positive", "warning"}
        VALID_POSITIONS = {"top-left", "top-right", "center", "bottom-left", "bottom-right"}
        VALID_SIZES = {"small", "medium", "large"}

        try:
            overlays = json.loads(raw)
            valid = []
            last_end = 0.0
            for o in overlays:
                t = float(o.get("time", 0))
                d = float(o.get("duration", 3.0))
                d = max(min(d, 3.5), 2.5)  # clamp 2.5–3.5s

                # Skip if out of bounds or overlapping with previous
                if t < 1.5 or t + d > total_duration - 1.0:
                    continue
                if t < last_end + 1.0:  # at least 1s gap between pop-ups
                    continue

                pos = o.get("position", "top-right")
                size = o.get("size", "medium")
                mood = o.get("mood", "default")

                valid.append({
                    "time": t,
                    "duration": d,
                    "content": str(o.get("content", "✨"))[:15],
                    "position": pos if pos in VALID_POSITIONS else "top-right",
                    "size": size if size in VALID_SIZES else "medium",
                    "mood": mood if mood in VALID_MOODS else "default",
                })
                last_end = t + d
            return valid[:5]
        except (json.JSONDecodeError, KeyError, TypeError):
            return []

    async def generate_broll_keywords(
        self,
        script: str,
        clip_candidates: list[dict],
    ) -> list[dict]:
        """
        For each clip candidate, generate a Pexels search keyword and overlay type.

        :param script: The Russian content script.
        :param clip_candidates: List of dicts from Gemini with keys:
            video_index, start_sec, end_sec, reason (and optionally scene_topic).
        :return: List of dicts:
            {video_index, start_sec, end_sec, broll_keyword, overlay_type}
            overlay_type: "corner" | "fullscreen"
        """
        clips_block = "\n".join(
            f"- Клип {c['video_index']} [{c['start_sec']:.1f}s–{c['end_sec']:.1f}s]: {c.get('reason', '')}"
            for c in clip_candidates
        )
        prompt = (
            "Ты — режиссёр монтажа Instagram Reels для дерматолога.\n\n"
            "Сценарий видео (на русском):\n"
            f"{script}\n\n"
            "Выбранные клипы (таймкоды и описание сцены от AI-анализа):\n"
            f"{clips_block}\n\n"
            "ЗАДАЧА: Для каждого клипа придумай B-roll overlay.\n\n"
            "ПРАВИЛА для broll_keyword:\n"
            "- ОБЯЗАТЕЛЬНО 2-3 слова. Одно слово ЗАПРЕЩЕНО.\n"
            "- Описывай КОНКРЕТНЫЙ предмет или действие, НЕ абстрактную сцену.\n"
            "  ХОРОШО: 'hyaluronic acid syringe', 'face cream jar closeup', 'woman touching face skin'\n"
            "  ПЛОХО: 'beauty', 'skincare', 'cream', 'woman shower' (слишком абстрактно или широко)\n"
            "- К предметам ВСЕГДА добавляй 'closeup' или 'product':\n"
            "  'cream' → 'face cream jar closeup'\n"
            "  'injection' → 'cosmetic injection procedure closeup'\n"
            "- Для дерматологии/косметологии используй медицинские термины:\n"
            "  'dermal filler injection', 'chemical peel procedure', 'LED light therapy face'\n"
            "- Если не уверен в визуале — используй 'woman dermatologist office', это безопасный фоллбэк.\n\n"
            "ПРАВИЛА для overlay_type:\n"
            "  'corner' — маленький overlay в углу, спикер остаётся виден. Используй когда спикер упоминает конкретный предмет или продукт.\n"
            "  'fullscreen' — B-roll на весь экран (2 сек перебивка). Используй при смене темы или для визуальной передышки.\n\n"
            "Ответь ТОЛЬКО валидным JSON массивом, без markdown:\n"
            '[{"video_index": 1, "start_sec": 0.0, "end_sec": 5.0, '
            '"broll_keyword": "hyaluronic acid syringe closeup", "overlay_type": "corner"}]'
        )

        raw = await self._chat(prompt, max_tokens=1024)
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE).strip()

        try:
            items = json.loads(raw)
            result = []
            for item, clip in zip(items, clip_candidates):
                overlay_type = item.get("overlay_type", "corner")
                result.append({
                    "video_index": clip["video_index"],
                    "start_sec": clip["start_sec"],
                    "end_sec": clip["end_sec"],
                    "broll_keyword": str(item.get("broll_keyword", "skincare routine"))[:60],
                    "overlay_type": overlay_type if overlay_type in ("corner", "fullscreen") else "corner",
                })
            return result
        except (json.JSONDecodeError, KeyError, TypeError):
            # Fallback: return clips with generic keyword
            return [
                {
                    "video_index": c["video_index"],
                    "start_sec": c["start_sec"],
                    "end_sec": c["end_sec"],
                    "broll_keyword": "skincare routine",
                    "overlay_type": "corner",
                }
                for c in clip_candidates
            ]

    async def suggest_formats(self, idea_text: str) -> list[str]:
        """
        Suggest the three best content formats for a given idea.

        :param idea_text: Raw idea or voice transcription from the doctor.
        :return: List of exactly 3 format strings from
                 ['reels', 'post', 'carousel', 'stories'].
        """
        available = ", ".join(FORMAT_DESCRIPTIONS.keys())
        prompt = (
            f"Доступные форматы контента: {available}\n\n"
            f"Идея врача:\n{idea_text}\n\n"
            "Выбери три наиболее подходящих формата для этой идеи и объясни кратко "
            "почему каждый подходит.\n\n"
            "Ответь строго в формате JSON (без markdown-обёртки):\n"
            '[\n'
            '  {"format": "<название>", "reason": "<одно предложение>"},\n'
            '  {"format": "<название>", "reason": "<одно предложение>"},\n'
            '  {"format": "<название>", "reason": "<одно предложение>"}\n'
            ']'
        )
        raw = await self._chat(prompt, max_tokens=512)

        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE).strip()

        try:
            items = json.loads(raw)
            # Return just the format names; caller can inspect reasons if needed
            return [item["format"] for item in items[:3]]
        except (json.JSONDecodeError, KeyError):
            # Graceful fallback to all formats
            return list(FORMAT_DESCRIPTIONS.keys())[:3]
