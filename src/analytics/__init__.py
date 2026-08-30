"""
Analytics, Progression, and AI Coaching Package.
"""

from .metrics import (
    calculate_relative_effort,
    process_activities_into_weeks,
    calculate_acwr,
    build_progression_history,
)
from .ai_coach import (
    generate_weekly_coaching_insights,
    predict_race_performances,
    predict_triathlon_performances,
)

__all__ = [
    "calculate_relative_effort",
    "process_activities_into_weeks",
    "calculate_acwr",
    "build_progression_history",
    "generate_weekly_coaching_insights",
    "predict_race_performances",
    "predict_triathlon_performances",
]
