"""
Shared Duration, Pace, and Distance Formatting.

Single source of truth for unit conversions used by the CLI, the Google Sheets
writer, and the API. Sport-specific data corrections (swim distance divisor,
indoor-trainer distance estimate) also live here so every consumer applies them
identically.
"""

from src.config import (
    BIKE_SPORTS,
    INDOOR_BIKE_SPEED_KMH,
    RUN_SPORTS,
    SWIM_DISTANCE_DIVISOR,
    SWIM_SPORTS,
)

# Sports whose pace reads as min/km rather than km/h.
_PACE_SPORTS = RUN_SPORTS + ("Walk", "Hike")


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


def _pace_mmss(seconds: float) -> str:
    """Render a seconds-per-unit value as 'M:SS'."""
    mins, secs = divmod(int(round(seconds)), 60)
    return f"{mins}:{secs:02d}"


def format_pace(speed_mps: float, sport_type: str = "", greek: bool = False) -> str:
    """
    Format an average speed as a sport-appropriate pace.

    Swim -> min/100m, run/walk/hike -> min/km, everything else -> km/h.
    `speed_mps` must already have any sport correction applied (see
    `corrected_distance_and_speed`).
    """
    if not speed_mps or speed_mps <= 0:
        return "N/A" if greek else "—"

    if sport_type in SWIM_SPORTS:
        unit = "/100μ" if greek else "/100m"
        return f"{_pace_mmss(100.0 / speed_mps)} {unit}"

    if sport_type in _PACE_SPORTS:
        unit = "/χλμ" if greek else "/km"
        return f"{_pace_mmss(1000.0 / speed_mps)} {unit}"

    unit = "χλμ/ω" if greek else "km/h"
    return f"{speed_mps * 3.6:.1f} {unit}"


def is_indoor_ride(act: dict) -> bool:
    """True if this is a trainer ride whose reported distance is unusable."""
    sport = act.get("sport_type") or act.get("type", "")
    if sport not in BIKE_SPORTS:
        return False
    distance_m = float(act.get("distance", 0) or 0)
    moving_time = int(act.get("moving_time", 0) or 0)
    return bool(act.get("trainer", False)) and distance_m < 100 and moving_time > 0


def corrected_distance_and_speed(act: dict) -> tuple[float, float]:
    """
    Return (distance_m, speed_mps) with sport corrections applied.

    Swims are divided by SWIM_DISTANCE_DIVISOR; indoor trainer rides get their
    distance estimated from duration at INDOOR_BIKE_SPEED_KMH.
    """
    sport = act.get("sport_type") or act.get("type", "")
    distance_m = float(act.get("distance", 0) or 0)
    speed_mps = float(act.get("average_speed", 0) or 0)
    moving_time = int(act.get("moving_time", 0) or 0)

    if sport in SWIM_SPORTS and SWIM_DISTANCE_DIVISOR:
        distance_m /= SWIM_DISTANCE_DIVISOR
        speed_mps /= SWIM_DISTANCE_DIVISOR
    elif is_indoor_ride(act):
        distance_m = (moving_time / 3600.0) * INDOOR_BIKE_SPEED_KMH * 1000.0
        speed_mps = INDOOR_BIKE_SPEED_KMH / 3.6

    return distance_m, speed_mps
