import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services import claude_service

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_response_raw() -> str:
    return (FIXTURES / "sample_carousel_response.json").read_text(encoding="utf-8")


@pytest.fixture
def sample_brief() -> str:
    return (FIXTURES / "sample_brief.txt").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_generate_carousel_slides_happy_path(sample_response_raw, sample_brief):
    with patch.object(claude_service, "_chat", new=AsyncMock(return_value=sample_response_raw)):
        result = await claude_service.generate_carousel_slides(sample_brief)
    assert "slides" in result
    assert 3 <= len(result["slides"]) <= 10
    assert all("order" in s and "text_title" in s and "text_body" in s for s in result["slides"])
    assert len(result["caption_telegram"]) <= 1024
    assert len(result["caption_instagram"]) <= 2200


@pytest.mark.asyncio
async def test_generate_strips_markdown_fence(sample_response_raw, sample_brief):
    fenced = f"```json\n{sample_response_raw}\n```"
    with patch.object(claude_service, "_chat", new=AsyncMock(return_value=fenced)):
        result = await claude_service.generate_carousel_slides(sample_brief)
    assert len(result["slides"]) >= 3


@pytest.mark.asyncio
async def test_generate_raises_on_malformed_json(sample_brief):
    with patch.object(claude_service, "_chat", new=AsyncMock(return_value="not json at all")):
        with pytest.raises(ValueError, match="невалидный JSON"):
            await claude_service.generate_carousel_slides(sample_brief)


@pytest.mark.asyncio
async def test_generate_raises_on_too_few_slides(sample_brief):
    bad = json.dumps({
        "slides": [{"order": 1, "text_title": "t", "text_body": "b"}, {"order": 2, "text_title": "t", "text_body": "b"}],
        "caption_telegram": "a",
        "caption_instagram": "b",
    })
    with patch.object(claude_service, "_chat", new=AsyncMock(return_value=bad)):
        with pytest.raises(ValueError, match="3-10 слайдов"):
            await claude_service.generate_carousel_slides(sample_brief)


@pytest.mark.asyncio
async def test_generate_raises_on_long_title(sample_brief):
    bad = json.dumps({
        "slides": [{"order": i, "text_title": "x" * 61, "text_body": "b"} for i in range(1, 6)],
        "caption_telegram": "a",
        "caption_instagram": "b",
    })
    with patch.object(claude_service, "_chat", new=AsyncMock(return_value=bad)):
        with pytest.raises(ValueError, match="text_title"):
            await claude_service.generate_carousel_slides(sample_brief)


@pytest.mark.asyncio
async def test_regenerate_single_slide_preserves_order(sample_response_raw, sample_brief):
    slides = json.loads(sample_response_raw)["slides"]
    target_order = slides[2]["order"]
    replacement = {"order": target_order, "text_title": "Новый заголовок", "text_body": "Новое тело"}
    with patch.object(claude_service, "_chat", new=AsyncMock(return_value=json.dumps(replacement))):
        result = await claude_service.regenerate_single_slide(sample_brief, slides, 2)
    assert result["order"] == target_order
    assert result["text_title"] == "Новый заголовок"


@pytest.mark.asyncio
async def test_regenerate_rejects_wrong_order(sample_response_raw, sample_brief):
    slides = json.loads(sample_response_raw)["slides"]
    bad = json.dumps({"order": 99, "text_title": "x", "text_body": "y"})
    with patch.object(claude_service, "_chat", new=AsyncMock(return_value=bad)):
        with pytest.raises(ValueError, match="order=99"):
            await claude_service.regenerate_single_slide(sample_brief, slides, 0)
