"""
Analytics, Progression, and AI Coaching Package.
"""

from .ai_coach import (
    generate_weekly_coaching_insights,
    predict_race_performances,
    predict_triathlon_performances,
)
from .metrics import (
    build_progression_history,
    calculate_acwr,
    calculate_fitness_fatigue,
    calculate_relative_effort,
    process_activities_into_weeks,
)

__all__ = [
    "build_progression_history",
    "calculate_acwr",
    "calculate_fitness_fatigue",
    "calculate_relative_effort",
    "generate_weekly_coaching_insights",
    "predict_race_performances",
    "predict_triathlon_performances",
    "process_activities_into_weeks",
]
