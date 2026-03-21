"""Tests for analyze_silence (Silence Map Generator)."""

from app.services.whisper_service import analyze_silence


def _w(word: str, start: float, end: float) -> dict:
    """Shorthand for a word timestamp dict."""
    return {"word": word, "start": start, "end": end}


# ── 1. Normal speech, 5 words, no pauses ────────────────────────────

def test_continuous_speech_single_block():
    words = [
        _w("привет", 0.5, 0.9),
        _w("как", 0.95, 1.1),
        _w("дела", 1.15, 1.5),
        _w("у", 1.55, 1.6),
        _w("тебя", 1.65, 2.0),
    ]
    result = analyze_silence(words)

    assert len(result) == 2  # leading silence + one speech block
    assert result[0]["type"] == "silence"
    assert result[0]["start"] == 0
    assert result[0]["end"] == 0.5

    speech = result[1]
    assert speech["type"] == "speech"
    assert speech["start"] == 0.5
    # end = 2.0 + 0.2 safety margin (no next word to cap)
    assert speech["end"] == 2.2
    assert len(speech["words"]) == 5


# ── 2. Speech with 2s pause in the middle ───────────────────────────

def test_pause_creates_two_blocks():
    words = [
        _w("один", 0.0, 0.5),
        _w("два", 0.6, 1.0),
        # 2s gap
        _w("три", 3.0, 3.5),
        _w("четыре", 3.6, 4.2),
    ]
    result = analyze_silence(words)

    speech_blocks = [s for s in result if s["type"] == "speech"]
    silence_blocks = [s for s in result if s["type"] == "silence"]

    assert len(speech_blocks) == 2
    assert len(silence_blocks) == 1  # gap between the two speech blocks

    # First speech: 0.0 - ~1.2
    assert speech_blocks[0]["start"] == 0.0
    assert len(speech_blocks[0]["words"]) == 2

    # Silence between
    assert silence_blocks[0]["start"] == speech_blocks[0]["end"]
    assert silence_blocks[0]["end"] == 3.0

    # Second speech: 3.0 - ~4.4
    assert speech_blocks[1]["start"] == 3.0
    assert len(speech_blocks[1]["words"]) == 2


# ── 3. Isolated filler "э" removed ─────────────────────────────────

def test_isolated_filler_removed():
    words = [
        _w("привет", 0.0, 0.5),
        _w("как", 0.6, 1.0),
        # gap > 1s
        _w("э", 3.0, 3.9),  # 0.9s duration >= 0.8 min, single word, filler
        # gap > 1s
        _w("пока", 5.5, 6.0),
        _w("всем", 6.1, 6.5),
    ]
    result = analyze_silence(words)

    speech_blocks = [s for s in result if s["type"] == "speech"]
    assert len(speech_blocks) == 2  # "э" block removed

    # No speech block should contain "э"
    all_words = [w["word"] for b in speech_blocks for w in b["words"]]
    assert "э" not in all_words


# ── 4. "Ну" + "вот" together — not filler ──────────────────────────

def test_nu_in_phrase_not_filler():
    words = [
        _w("ну", 0.0, 0.3),
        _w("вот", 0.4, 0.7),  # gap 0.1s — same block
        _w("смотрите", 0.8, 1.5),
    ]
    result = analyze_silence(words)

    speech_blocks = [s for s in result if s["type"] == "speech"]
    assert len(speech_blocks) == 1
    all_words = [w["word"] for w in speech_blocks[0]["words"]]
    assert "ну" in all_words
    assert "вот" in all_words


# ── 5. Empty input ──────────────────────────────────────────────────

def test_empty_input():
    assert analyze_silence([]) == []


# ── 6. Three short single words → all removed by min duration ──────

def test_short_words_all_removed():
    words = [
        _w("а", 0.0, 0.3),   # 0.3s < 0.8 min
        # gap > 1s
        _w("б", 2.0, 2.3),   # 0.3s < 0.8 min
        # gap > 1s
        _w("в", 4.0, 4.3),   # 0.3s < 0.8 min
    ]
    result = analyze_silence(words)
    assert result == []


# ── 7. Uppercase filler "Э" at start, then normal speech ───────────

def test_uppercase_filler_at_start_removed():
    words = [
        _w("Э", 0.5, 1.5),   # 1.0s >= 0.8, uppercase filler, at start (no prev)
        # gap > 1s
        _w("добрый", 3.0, 3.5),
        _w("день", 3.6, 4.0),
    ]
    result = analyze_silence(words)

    speech_blocks = [s for s in result if s["type"] == "speech"]
    assert len(speech_blocks) == 1
    assert speech_blocks[0]["words"][0]["word"] == "добрый"

    # Should have leading silence covering the removed filler
    assert result[0]["type"] == "silence"
    assert result[0]["start"] == 0
    assert result[0]["end"] == 3.0


# ── 8. "э" inside continuous speech — NOT removed ──────────────────

def test_filler_in_stream_kept():
    words = [
        _w("я", 1.0, 1.2),
        _w("э", 1.25, 1.4),   # gap 0.05s — same block
        _w("пошла", 1.45, 2.0),
    ]
    result = analyze_silence(words)

    speech_blocks = [s for s in result if s["type"] == "speech"]
    assert len(speech_blocks) == 1
    all_words = [w["word"] for w in speech_blocks[0]["words"]]
    assert "э" in all_words
    assert len(all_words) == 3


# ── 9. Safety margin capped by next word ────────────────────────────

def test_safety_margin_capped():
    words = [
        _w("раз", 0.0, 5.0),
        # next word starts at 5.1 — margin wants 5.2, should be capped to 5.1
        _w("два", 5.1, 5.5),
    ]
    # gap = 0.1s < 1.0 threshold → single block
    result = analyze_silence(words)

    speech_blocks = [s for s in result if s["type"] == "speech"]
    assert len(speech_blocks) == 1
    # The last word "два" end=5.5 + 0.2 = 5.7 (no next word → uncapped)
    assert speech_blocks[0]["end"] == 5.7

    # Now test with two separate blocks where margin matters
    words2 = [
        _w("один", 0.0, 5.0),
        # gap > 1s → separate blocks
        _w("два", 6.5, 7.0),
        _w("три", 7.1, 7.5),  # makes second block >= 0.8s
    ]
    result2 = analyze_silence(words2)
    speech_blocks2 = [s for s in result2 if s["type"] == "speech"]
    assert len(speech_blocks2) == 2
    # First block: end=5.0, next word starts 6.5 → margin 5.2 < 6.5 → 5.2
    assert speech_blocks2[0]["end"] == 5.2
    # Second block: end=7.5 + 0.2 = 7.7 (no next word)
    assert speech_blocks2[1]["end"] == 7.7
