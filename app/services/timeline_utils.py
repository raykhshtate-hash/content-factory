"""
Timeline utilities for mapping source video timecodes to render timeline.

Problem: B-roll items have timecodes relative to the ORIGINAL source video
(as returned by Gemini). But the render is a splice of trimmed clips —
render_timeline != source_timeline.

This module bridges the two.
"""

import logging

logger = logging.getLogger(__name__)

MIN_GAP_SECONDS = 2.0  # minimum gap between two b-roll overlays on render timeline
DONUT_DURATION = 4.0  # duration of each donut overlay in seconds


def map_broll_to_render_timeline(
    broll_items: list[dict],
    selected_clips: list[dict],
    total_render_duration: float | None = None,
) -> list[dict]:
    """
    Map b-roll overlay timecodes from source video space → render timeline.

    :param broll_items: List of b-roll dicts, each must have:
        - start_sec (float): start time in the source video
        - end_sec   (float): end time in the source video
        Other keys (url, type, overlay_type, …) are preserved as-is.

    :param selected_clips: List of clip dicts, each must have:
        - trim_start    (float): start of this clip in the source video
        - trim_duration (float): duration of this clip

    :param total_render_duration: Total duration of the final render (optional).
        If provided, donuts that would extend beyond this are either shifted or dropped.

    :return: Filtered, remapped list with `render_time` added and
        `start_sec`/`end_sec` replaced with render-relative values.
        Sorted by render_time. Items that don't fall inside any clip are dropped.
        Items closer than MIN_GAP_SECONDS to a previous item are dropped.
    """
    if not broll_items or not selected_clips:
        return []

    # Build clip windows with their render start positions
    clip_windows: list[dict] = []
    render_cursor = 0.0
    for clip in selected_clips:
        trim_start = float(clip.get("trim_start", 0))
        trim_duration = float(clip.get("trim_duration", 0))
        clip_windows.append({
            "source_start": trim_start,
            "source_end": trim_start + trim_duration,
            "render_start": render_cursor,
            "trim_duration": trim_duration,
        })
        render_cursor += trim_duration

    # If total_render_duration not provided, calculate from clips
    if total_render_duration is None:
        total_render_duration = render_cursor

    mapped: list[dict] = []

    for item, window in zip(broll_items, clip_windows):
        # Place overlay at 40% into the clip's duration on the render timeline
        render_time = window["render_start"] + (window["trim_duration"] * 0.4)

        # Drop if too early (before 1.5s)
        if render_time < 1.5:
            logger.debug(
                "B-roll dropped — render_time %.2fs < 1.5s (keyword=%r)",
                render_time, item.get("broll_keyword"),
            )
            continue

        # Check if donut would extend beyond video end
        donut_end = render_time + DONUT_DURATION
        if donut_end > total_render_duration:
            # Try to shift donut earlier to fit within video bounds
            render_time = total_render_duration - DONUT_DURATION

            # If still too early (before 1.5s), drop this donut entirely
            if render_time < 1.5:
                logger.debug(
                    "B-roll dropped — would extend beyond video end (%.2fs + %.2fs > %.2fs, keyword=%r)",
                    render_time, DONUT_DURATION, total_render_duration, item.get("broll_keyword"),
                )
                continue

            logger.debug(
                "B-roll shifted earlier — was %.2fs, now %.2fs to fit within %.2fs (keyword=%r)",
                render_time + (window["trim_duration"] * 0.4), render_time,
                total_render_duration, item.get("broll_keyword"),
            )

        new_item = {
            **item,
            "start_sec": render_time,
            "end_sec": render_time + DONUT_DURATION,
            "render_time": render_time,
        }
        mapped.append(new_item)

    # Sort by render timeline position
    mapped.sort(key=lambda x: x["render_time"])

    # Deduplicate: drop items closer than MIN_GAP_SECONDS to the previous one
    deduplicated: list[dict] = []
    last_render_time = -MIN_GAP_SECONDS

    for item in mapped:
        if item["render_time"] - last_render_time < MIN_GAP_SECONDS:
            logger.debug(
                "B-roll at render %.2fs dropped — too close to previous (gap=%.2fs, keyword=%r)",
                item["render_time"],
                item["render_time"] - last_render_time,
                item.get("broll_keyword"),
            )
            continue
        deduplicated.append(item)
        last_render_time = item["render_time"]

    logger.info(
        "map_broll_to_render_timeline: %d in → %d mapped → %d after dedup",
        len(broll_items), len(mapped), len(deduplicated),
    )
    return deduplicated
