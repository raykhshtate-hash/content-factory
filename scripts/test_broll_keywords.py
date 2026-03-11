"""
Test Claude broll keyword generation:
    python -m scripts.test_broll_keywords
"""

import asyncio
import json

from app.config import settings
from app.services.claude_service import ClaudeService

SAMPLE_SCRIPT = """
Знаете, почему ваш крем не работает? Дело не в составе.

Я дерматолог и вижу одну и ту же ошибку каждый день: люди наносят уходовые средства на грязную кожу.

Сначала очищение — потом всё остальное. Без этого даже самый дорогой крем просто не проникает в кожу.

Ещё один момент: порядок нанесения. Лёгкие текстуры сначала, потом плотные. Сыворотка → крем → масло. Никак не наоборот.

Запишите себе: очищение + правильный порядок = ваш уход начнёт работать.
"""

SAMPLE_CLIPS = [
    {"video_index": 1, "start_sec": 0.0,  "end_sec": 5.0,  "reason": "Hook — врач задаёт провокационный вопрос про крем"},
    {"video_index": 1, "start_sec": 8.0,  "end_sec": 16.0, "reason": "Объяснение ошибки — нанесение на грязную кожу"},
    {"video_index": 1, "start_sec": 18.0, "end_sec": 26.0, "reason": "Показ правильного порядка нанесения средств"},
    {"video_index": 1, "start_sec": 28.0, "end_sec": 34.0, "reason": "Итог и призыв к действию"},
]


async def main():
    svc = ClaudeService(api_key=settings.ANTHROPIC_API_KEY)
    print("Generating broll keywords...\n")
    result = await svc.generate_broll_keywords(SAMPLE_SCRIPT, SAMPLE_CLIPS)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
