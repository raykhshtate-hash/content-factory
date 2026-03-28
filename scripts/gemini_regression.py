#!/usr/bin/env python3
"""
Gemini Regression Harness — fixture-based testing for clip selection quality.

Usage:
    python scripts/gemini_regression.py              # Run all fixtures, validate assertions
    python scripts/gemini_regression.py --record      # Call Gemini, save baselines
    python scripts/gemini_regression.py --fixture talking_head_01  # Run single fixture
"""

import argparse
import asyncio
import glob
import json
import os
import re
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import GEMINI_PROMPT_V, settings
from app.services.gemini_service import GeminiService, VideoAnalysis


# ── Time Parsing ──────────────────────────────────────────────

def parse_mmss(time_str: str) -> float:
    """Parse MM:SS.s format to seconds. E.g. '01:23.5' -> 83.5"""
    match = re.match(r"(\d+):(\d+)(?:\.(\d+))?", time_str)
    if not match:
        return 0.0
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    frac = int(match.group(3)) if match.group(3) else 0
    # Handle fractional part: '.5' -> 0.5, '.50' -> 0.50
    frac_len = len(match.group(3)) if match.group(3) else 0
    frac_val = frac / (10 ** frac_len) if frac_len > 0 else 0.0
    return minutes * 60 + seconds + frac_val


# ── Prompt Builder ────────────────────────────────────────────

def build_talking_head_prompt(gcs_uris: list[str], scenario_text: str = "") -> str:
    """
    Build the production prompt for analyze_video.
    Mirrors app/bot/handlers.py analyze_and_propose() lines 545-573.
    """
    prompt = (
        f"Тебе передано {len(gcs_uris)} видео. "
        "Проанализируй их и найди самые виральные моменты для Reels/Shorts. "
        "Оцени силу хука от 1 до 10, визуальные риски и уверенность.\n\n"
    )

    if scenario_text:
        prompt += (
            "СЦЕНАРИЙ ДЛЯ ПОИСКА:\n"
            f"{scenario_text}\n\n"
            "ТВОЯ ГЛАВНАЯ ЗАДАЧА: Найди фрагменты на видео, где спикер произносит фразы, "
            "максимально близкие по смыслу или тексту к этому сценарию. "
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
    return prompt


# ── Assertion Engine ──────────────────────────────────────────

class AssertionResult:
    def __init__(self, name: str, passed: bool, hard: bool, value, expected, note: str = ""):
        self.name = name
        self.passed = passed
        self.hard = hard  # True = exit code 1 on fail; False = warning only
        self.value = value
        self.expected = expected
        self.note = note

    @property
    def status(self) -> str:
        if self.passed:
            return "PASS"
        return "FAIL" if self.hard else "WARN"


def run_assertions(
    clips: list[dict],
    expected: dict,
    thresholds: dict,
) -> list[AssertionResult]:
    """
    Run 5 quality assertions on Gemini clip output.
    CRITICAL: Quality assertions (dedup, linearity, consecutive) apply only to broll clips.
    """
    results = []

    # All clips for count/duration
    all_clips = clips
    total_clips = len(all_clips)

    # Filter broll clips for quality assertions (D-13)
    broll_clips = [c for c in clips if c.get("clip_type") == "broll"]

    # ── 1. Clip count (HARD) ──
    clip_range = expected.get("clip_count", [thresholds["min_clip_count"], thresholds["max_clip_count"]])
    min_c, max_c = clip_range
    count_ok = min_c <= total_clips <= max_c
    results.append(AssertionResult(
        name="clip_count",
        passed=count_ok,
        hard=True,
        value=total_clips,
        expected=f"[{min_c}, {max_c}]",
        note=f"{total_clips} clips" if count_ok else f"Expected {min_c}-{max_c}, got {total_clips}",
    ))

    # ── 2. Total duration (HARD) ──
    total_dur = 0.0
    for c in all_clips:
        start = parse_mmss(c.get("start_time", "00:00.0"))
        end = parse_mmss(c.get("end_time", "00:00.0"))
        total_dur += max(0, end - start)

    dur_range = expected.get("total_duration", [thresholds["min_duration_s"], thresholds["max_duration_s"]])
    min_d, max_d = dur_range
    dur_ok = min_d <= total_dur <= max_d
    results.append(AssertionResult(
        name="total_duration",
        passed=dur_ok,
        hard=True,
        value=f"{total_dur:.1f}s",
        expected=f"[{min_d}, {max_d}]s",
        note=f"{total_dur:.1f}s" if dur_ok else f"Expected {min_d}-{max_d}s, got {total_dur:.1f}s",
    ))

    # ── 3. Dedup ratio — broll only (WARNING) ──
    if broll_clips:
        sources = [c.get("video_index", 0) for c in broll_clips]
        unique_sources = len(set(sources))
        dedup_ratio = unique_sources / len(broll_clips)
    else:
        dedup_ratio = 1.0
    dedup_threshold = thresholds.get("min_dedup_ratio", 0.4)
    dedup_ok = dedup_ratio >= dedup_threshold
    results.append(AssertionResult(
        name="dedup_ratio",
        passed=dedup_ok,
        hard=False,
        value=f"{dedup_ratio:.2f}",
        expected=f">= {dedup_threshold}",
        note=f"{dedup_ratio:.2f}" if dedup_ok else f"Low diversity: {dedup_ratio:.2f} < {dedup_threshold}",
    ))

    # ── 4. Max consecutive same-source — broll only (WARNING) ──
    max_consec = 0
    if broll_clips:
        current_consec = 1
        for i in range(1, len(broll_clips)):
            if broll_clips[i].get("video_index") == broll_clips[i - 1].get("video_index"):
                current_consec += 1
            else:
                max_consec = max(max_consec, current_consec)
                current_consec = 1
        max_consec = max(max_consec, current_consec)
    consec_threshold = thresholds.get("max_consecutive_same_source", 2)
    consec_ok = max_consec <= consec_threshold
    results.append(AssertionResult(
        name="max_consecutive",
        passed=consec_ok,
        hard=False,
        value=max_consec,
        expected=f"<= {consec_threshold}",
        note=f"{max_consec}" if consec_ok else f"Too many consecutive: {max_consec} > {consec_threshold}",
    ))

    # ── 5. Anti-linearity — broll only (WARNING) ──
    # Fail only if ALL broll clips are in strict ascending (video_index, start_time) order
    if len(broll_clips) <= 1:
        linear_ok = True
    else:
        is_strictly_linear = True
        for i in range(1, len(broll_clips)):
            prev_idx = broll_clips[i - 1].get("video_index", 0)
            curr_idx = broll_clips[i].get("video_index", 0)
            prev_start = parse_mmss(broll_clips[i - 1].get("start_time", "00:00.0"))
            curr_start = parse_mmss(broll_clips[i].get("start_time", "00:00.0"))
            if (curr_idx, curr_start) <= (prev_idx, prev_start):
                is_strictly_linear = False
                break
        linear_ok = not is_strictly_linear

    results.append(AssertionResult(
        name="anti_linearity",
        passed=linear_ok,
        hard=False,
        value="non-linear" if linear_ok else "LINEAR",
        expected="non-linear",
        note="OK" if linear_ok else "All broll clips in strict ascending order",
    ))

    return results


# ── Fixture Loading ───────────────────────────────────────────

def load_fixtures(fixture_dir: str, fixture_name: str | None = None) -> list[dict]:
    """Load fixture JSON files from directory."""
    fixtures = []
    pattern = os.path.join(fixture_dir, "*.json")
    for path in sorted(glob.glob(pattern)):
        basename = os.path.basename(path)
        if basename == "defaults.json":
            continue
        if fixture_name and not basename.startswith(fixture_name):
            continue
        with open(path) as f:
            data = json.load(f)
        data["_path"] = path
        fixtures.append(data)
    return fixtures


def load_defaults(fixture_dir: str) -> dict:
    """Load default thresholds."""
    defaults_path = os.path.join(fixture_dir, "defaults.json")
    if os.path.exists(defaults_path):
        with open(defaults_path) as f:
            return json.load(f)
    return {"thresholds": {}}


# ── Gemini Invocation ─────────────────────────────────────────

async def run_gemini_talking_head(fixture: dict, gemini: GeminiService) -> VideoAnalysis | None:
    """Run Gemini analyze_video for a talking_head fixture."""
    gcs_uris = fixture["gcs_uris"]
    scenario_text = fixture.get("scenario_text", "")
    prompt = build_talking_head_prompt(gcs_uris, scenario_text)
    return await gemini.analyze_video(gcs_uris, prompt)


async def run_gemini_storyboard_smart(fixture: dict, gemini: GeminiService) -> VideoAnalysis | None:
    """
    Run Gemini for storyboard_smart fixture.
    Smart mode uses analyze_video (same as talking_head) — NOT analyze_storyboard.
    Smart mode routes through the same Gemini pipeline as talking_head.
    """
    gcs_uris = fixture["gcs_uris"]
    scenario_text = fixture.get("scenario_text", "")
    prompt = build_talking_head_prompt(gcs_uris, scenario_text)

    # Smart mode may have voiceover_data and audio_map
    audio_map = fixture.get("audio_map")
    voiceover_data = fixture.get("voiceover_data")

    return await gemini.analyze_video(
        gcs_uris,
        prompt,
    )


# ── Record Mode ───────────────────────────────────────────────

async def record_baselines(fixtures: list[dict], gemini: GeminiService) -> int:
    """Run Gemini on each fixture and save output as baseline."""
    recorded = 0
    for fixture in fixtures:
        name = fixture.get("name", "unknown")
        mode = fixture.get("mode", "talking_head")
        print(f"  Recording: {name} ({mode})...", end=" ", flush=True)

        try:
            if mode == "storyboard_smart":
                analysis = await run_gemini_storyboard_smart(fixture, gemini)
            else:
                analysis = await run_gemini_talking_head(fixture, gemini)

            if analysis:
                fixture["baseline"] = analysis.model_dump()
                # Write back to fixture file
                path = fixture.pop("_path", None)
                if path:
                    with open(path, "w") as f:
                        json.dump(fixture, f, indent=2, ensure_ascii=False)
                    fixture["_path"] = path
                recorded += 1
                clips = analysis.clip_candidates
                print(f"OK ({len(clips)} clips)")
            else:
                print("FAILED (no response)")
        except Exception as e:
            print(f"ERROR: {e}")

    return recorded


# ── Output Formatting ─────────────────────────────────────────

def print_table(rows: list[dict]):
    """Print results as a formatted terminal table."""
    headers = ["Fixture", "Status", "Clips", "Duration", "Dedup", "Linear", "Consec", "Notes"]
    widths = [max(len(h), max((len(str(r.get(h.lower(), ""))) for r in rows), default=0)) for h in headers]
    # Minimum widths
    widths = [max(w, len(h)) for w, h in zip(widths, headers)]

    # Header
    header_line = " | ".join(h.ljust(w) for h, w in zip(headers, widths))
    sep_line = "-+-".join("-" * w for w in widths)
    print(header_line)
    print(sep_line)

    # Rows
    for row in rows:
        vals = [
            str(row.get("fixture", "")),
            str(row.get("status", "")),
            str(row.get("clips", "")),
            str(row.get("duration", "")),
            str(row.get("dedup", "")),
            str(row.get("linear", "")),
            str(row.get("consec", "")),
            str(row.get("notes", "")),
        ]
        print(" | ".join(v.ljust(w) for v, w in zip(vals, widths)))


# ── Main ──────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Gemini regression harness")
    parser.add_argument("--record", action="store_true", help="Call Gemini and save output as baselines")
    parser.add_argument("--fixture", type=str, default=None, help="Run single fixture by name prefix")
    args = parser.parse_args()

    fixture_dir = os.path.join(PROJECT_ROOT, "scripts", "fixtures")
    defaults = load_defaults(fixture_dir)
    thresholds = defaults.get("thresholds", {})
    fixtures = load_fixtures(fixture_dir, args.fixture)

    if not fixtures:
        print("No fixtures found in scripts/fixtures/")
        sys.exit(1)

    print(f"GEMINI_PROMPT_V = {GEMINI_PROMPT_V}")
    print(f"Fixtures: {len(fixtures)}")
    print()

    # Init Gemini
    project_id = settings.GCP_PROJECT_ID if hasattr(settings, "GCP_PROJECT_ID") else "romina-content-factory-489121"
    gemini = GeminiService(project_id=project_id)

    # ── Record mode ──
    if args.record:
        print("RECORD MODE: calling Gemini and saving baselines...")
        print()
        recorded = await record_baselines(fixtures, gemini)
        print()
        print(f"Baselines recorded: {recorded}/{len(fixtures)}")
        print("Review fixture files manually before committing.")
        sys.exit(0)

    # ── Run mode ──
    print("RUN MODE: validating fixtures against Gemini output...")
    print()

    table_rows = []
    total_pass = 0
    total_warn = 0
    total_fail = 0
    has_hard_fail = False

    for fixture in fixtures:
        name = fixture.get("name", "unknown")
        mode = fixture.get("mode", "talking_head")
        expected = fixture.get("expected", {})
        overrides = fixture.get("threshold_overrides", {})
        merged_thresholds = {**thresholds, **overrides}

        print(f"  Running: {name} ({mode})...", end=" ", flush=True)

        try:
            if mode == "storyboard_smart":
                analysis = await run_gemini_storyboard_smart(fixture, gemini)
            else:
                analysis = await run_gemini_talking_head(fixture, gemini)

            if not analysis:
                print("FAILED (no Gemini response)")
                table_rows.append({
                    "fixture": name, "status": "ERROR", "clips": "-",
                    "duration": "-", "dedup": "-", "linear": "-",
                    "consec": "-", "notes": "No Gemini response",
                })
                has_hard_fail = True
                total_fail += 1
                continue

            clips_data = [c.model_dump() for c in analysis.clip_candidates]
            results = run_assertions(clips_data, expected, merged_thresholds)

            # Determine row status
            row_hard_fail = any(not r.passed and r.hard for r in results)
            row_warn = any(not r.passed and not r.hard for r in results)

            if row_hard_fail:
                status = "FAIL"
                has_hard_fail = True
                total_fail += 1
            elif row_warn:
                status = "WARN"
                total_warn += 1
            else:
                status = "PASS"
                total_pass += 1

            # Build row
            result_map = {r.name: r for r in results}
            notes_parts = [r.note for r in results if not r.passed]

            table_rows.append({
                "fixture": name,
                "status": status,
                "clips": result_map["clip_count"].value,
                "duration": result_map["total_duration"].value,
                "dedup": result_map["dedup_ratio"].value,
                "linear": result_map["anti_linearity"].value,
                "consec": result_map["max_consecutive"].value,
                "notes": "; ".join(notes_parts) if notes_parts else "OK",
            })
            print(status)

        except Exception as e:
            print(f"ERROR: {e}")
            table_rows.append({
                "fixture": name, "status": "ERROR", "clips": "-",
                "duration": "-", "dedup": "-", "linear": "-",
                "consec": "-", "notes": str(e)[:60],
            })
            has_hard_fail = True
            total_fail += 1

    # ── Summary ──
    print()
    print_table(table_rows)
    print()
    print(f"Summary: {total_pass}/{len(fixtures)} passed, {total_warn} warnings, {total_fail} failures")
    print(f"GEMINI_PROMPT_V = {GEMINI_PROMPT_V}")

    if has_hard_fail:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
