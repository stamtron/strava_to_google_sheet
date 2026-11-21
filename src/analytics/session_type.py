"""
Classify a planned (Final Surge) session from its text.

Shared by readiness checks, weekly compliance and the Sunday digest so "hard"
and "key" mean the same thing everywhere. Returns machine-readable keys only.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from src.config import LONG_RUN_MIN_KM, LONG_RUN_MIN_MINUTES


def fold_accents(text: str) -> str:
    """Lowercase and strip combining accents so 'Ξεκούραση' matches 'ξεκουραση'."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


# Whole-word rest markers: substring matching made "90'' rest" between intervals,
# "interest" or "offroad" read as a rest day.
REST_DAY_RE = re.compile(r"\b(?:rest|off|ξεκουραση|ρεπο)\b")
# Rep notation such as "6x400m" / "4 × 8'".
REPS_RE = re.compile(r"\d\s*[x×]\s*\d")
HIGH_INTENSITY_KEYWORDS = (
    "διαλειμματικη",
    "διαλειμματα",
    "tempo",
    "κατωφλι",
    "threshold",
    "vo2",
    "strides",
    "ανοιγματα",
    "repeat",
    "1000m",
    "400m",
    "800m",
    "2000m",
    "z4",
    "z5",
    "interval",
)
_PACE_TARGET_MARKERS = ("@ 3:", "@ 4:", "@ 3,", "@ 4,")
_LONG_RUN_RE = re.compile(r"\b(?:long run|μακρυ\w*|μεγαλη διαρκεια)")


def detect_intensity(workout_text: str) -> str:
    """'high', 'moderate' or 'rest' for a day's plan text."""
    clean = fold_accents(workout_text or "")
    # Detect intensity first: a structured session that mentions rest between
    # reps (e.g. "6x400m, 90'' rest") is not a rest day.
    is_high = (
        any(k in clean for k in HIGH_INTENSITY_KEYWORDS)
        or bool(REPS_RE.search(clean))
        or any(p in clean for p in _PACE_TARGET_MARKERS)
    )
    if is_high:
        return "high"
    is_structured = "@" in clean
    if not clean.strip() or (not is_structured and REST_DAY_RE.search(clean)):
        return "rest"
    return "moderate"


def is_long_run(workout_text: str) -> bool:
    """A run at or beyond LONG_RUN_MIN_KM / LONG_RUN_MIN_MINUTES, or labelled as one."""
    from src.analytics.compliance import parse_planned_workout

    if _LONG_RUN_RE.search(fold_accents(workout_text or "")):
        return True
    for item in parse_planned_workout(workout_text or ""):
        if item["sport"] != "Run":
            continue
        if (item["target_dist_km"] or 0) >= LONG_RUN_MIN_KM:
            return True
        if (item["target_duration_min"] or 0) >= LONG_RUN_MIN_MINUTES:
            return True
    return False


def classify_planned_session(workout_text: str) -> dict[str, Any]:
    """
    {intensity, is_long_run, is_key}. Key sessions are the ones a week is built
    around: intervals/tempo/threshold work or the long run.
    """
    intensity = detect_intensity(workout_text)
    long_run = intensity != "rest" and is_long_run(workout_text)
    return {"intensity": intensity, "is_long_run": long_run, "is_key": intensity == "high" or long_run}
