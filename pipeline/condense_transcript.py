from __future__ import annotations

from typing import Any

EVENT_KEYWORDS = [
    "goal", "score", "penalty", "save", "card", "foul",
    "free kick", "corner", "shot", "header", "offside",
    "sub", "injur", "var", "crossbar", "post", "miss",
    "yellow", "red", "handball",
]

REACTION_KEYWORDS = [
    "crowd", "fan", "celebra", "referee", "coach",
    "manager", "bench", "ronaldo", "messi", "mbappe",
]

ALL_KEYWORDS = EVENT_KEYWORDS + REACTION_KEYWORDS


def _text_matches_keywords(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in ALL_KEYWORDS)


def _merge_windows(windows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not windows:
        return []
    sorted_w = sorted(windows)
    merged = [sorted_w[0]]
    for start, end in sorted_w[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def condense_timestamps(
    segments: list[dict[str, Any]],
    *,
    research_events: list[dict[str, Any]] | None = None,
    duration_seconds: int = 0,
    coverage_interval: int = 360,
    anchor_padding: int = 25,
    window_seconds: int = 30,
    always_include_first: int = 60,
    always_include_last: int = 60,
    enable_keywords: bool = False,
) -> list[dict[str, Any]]:
    if not segments:
        return []

    if not duration_seconds:
        duration_seconds = int(segments[-1]["end"])

    dur = float(duration_seconds)
    windows: list[tuple[float, float]] = []

    # 1. Bookends — always include first and last N seconds
    windows.append((0.0, float(always_include_first)))
    windows.append((max(0.0, dur - float(always_include_last)), dur))

    # 2. Research anchor windows — strongest signal
    if research_events:
        for event in research_events:
            ts = event.get("video_time_seconds")
            if ts is not None:
                t = float(ts)
                windows.append((
                    max(0.0, t - float(anchor_padding)),
                    min(dur, t + float(anchor_padding)),
                ))

    # 3. Time-based sampling — fallback coverage for non-English / noisy transcripts
    half_window = float(window_seconds) / 2.0
    t = float(coverage_interval)
    while t < dur - float(always_include_last):
        windows.append((
            max(0.0, t - half_window),
            min(dur, t + half_window),
        ))
        t += float(coverage_interval)

    # 4. Keyword-matched windows — optional, only when explicitly enabled
    if enable_keywords:
        for seg in segments:
            text = seg.get("text", "")
            if _text_matches_keywords(text):
                windows.append((
                    max(0.0, float(seg["start"]) - 10.0),
                    min(dur, float(seg["end"]) + 10.0),
                ))

    # 5. Merge overlapping windows
    keep_windows = _merge_windows(windows)

    # 6. Filter segments whose midpoint falls inside a keep-window
    kept = []
    for seg in segments:
        mid = (float(seg["start"]) + float(seg["end"])) / 2.0
        if any(s <= mid <= e for s, e in keep_windows):
            kept.append(seg)

    return kept
