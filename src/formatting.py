"""
Shared Duration, Pace, and Distance Formatting.

Single source of truth for unit conversions used by the CLI, Telegram,
the coach and the API. Distances and speeds are taken as Strava reports them.
"""

import re

from src.config import RUN_SPORTS, SWIM_SPORTS

# Sports whose pace reads as min/km rather than km/h.
_PACE_SPORTS = (*RUN_SPORTS, "Walk", "Hike")


def format_duration(seconds: int) -> str:
    """Format seconds as '1h 24m 30s' / '24m 30s'."""
    seconds = int(seconds or 0)
    if seconds <= 0:
        return "0s"
    hrs, rem = divmod(seconds, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs}h {mins}m {secs}s"
    return f"{mins}m {secs}s"


def format_duration_el(seconds: int) -> str:
    """Format seconds as Greek '1ω 24λ 30δ', omitting zero components."""
    seconds = int(seconds or 0)
    if seconds <= 0:
        return "0δ"
    hrs, rem = divmod(seconds, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if hrs > 0:
        parts.append(f"{hrs}ω")
    if mins > 0:
        parts.append(f"{mins}λ")
    if secs > 0 or not parts:
        parts.append(f"{secs}δ")
    return " ".join(parts)


def format_duration_short_el(seconds: int) -> str:
    """Format seconds as Greek '1ω 24λ', dropping seconds."""
    seconds = int(seconds or 0)
    if seconds <= 0:
        return "0λ"
    hrs, rem = divmod(seconds, 3600)
    mins = rem // 60
    if hrs > 0:
        return f"{hrs}ω {mins}λ"
    return f"{mins}λ"


# Shared by the weather-pace calculator and the plan parser.
_PACE_RE = re.compile(r"(\d{1,2})[:,\.](\d{2})")


def format_mmss(seconds: float) -> str:
    """Render a seconds-per-unit value as 'M:SS' (negatives clamp to 0:00)."""
    mins, secs = divmod(max(0, round(seconds)), 60)
    return f"{mins}:{secs:02d}"


def parse_pace_to_sec(text: str) -> float | None:
    """
    Parse the first 'M:SS' / 'M,SS' / 'M.SS' pace in `text` into seconds per unit.

    Units and trailing marks ('/km', "''") are ignored, so "5,15''" and
    "5:15/km" both give 315.0. Returns None when no pace is present.
    """
    if not text:
        return None
    match = _PACE_RE.search(str(text))
    if not match:
        return None
    return float(int(match.group(1)) * 60 + int(match.group(2)))


def format_pace(speed_mps: float, sport_type: str = "", greek: bool = False) -> str:
    """
    Format an average speed as a sport-appropriate pace.

    Swim -> min/100m, run/walk/hike -> min/km, everything else -> km/h.
    """
    if not speed_mps or speed_mps <= 0:
        return "N/A" if greek else "—"

    if sport_type in SWIM_SPORTS:
        unit = "/100μ" if greek else "/100m"
        return f"{format_mmss(100.0 / speed_mps)} {unit}"

    if sport_type in _PACE_SPORTS:
        unit = "/χλμ" if greek else "/km"
        return f"{format_mmss(1000.0 / speed_mps)} {unit}"

    unit = "χλμ/ω" if greek else "km/h"
    return f"{speed_mps * 3.6:.1f} {unit}"


def distance_and_speed(act: dict) -> tuple[float, float]:
    """Return (distance_m, speed_mps) exactly as Strava reports them, None-safe."""
    return float(act.get("distance") or 0), float(act.get("average_speed") or 0)
