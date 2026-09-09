"""
Tests for Garmin Workouts integration:
sport classification, pace conversions, structured parsing, and Garmin DTO generation.
"""

from unittest.mock import MagicMock, patch
import pytest

from src.integrations.garmin_workouts import (
    build_garmin_workout_object,
    classify_coach_day_text,
    pace_str_to_speed_ms,
    parse_workout_with_heuristics,
    speed_ms_to_pace_str,
    sync_week_workouts_to_garmin,
)


def test_pace_conversions():
    # 5:00 min/km = 300 sec/km -> 1000/300 = 3.333 m/s
    speed = pace_str_to_speed_ms("5:00")
    assert speed is not None
    assert round(speed, 2) == 3.33

    # 5:15 min/km = 315 sec/km -> 1000/315 = 3.175 m/s
    speed_515 = pace_str_to_speed_ms("5,15")
    assert speed_515 is not None
    assert round(speed_515, 2) == 3.17

    # Round trip conversion
    assert speed_ms_to_pace_str(speed) == "5:00"
    assert speed_ms_to_pace_str(speed_515) == "5:15"

    # Edge cases
    assert pace_str_to_speed_ms("") is None
    assert pace_str_to_speed_ms(None) is None
    assert pace_str_to_speed_ms("invalid") is None
    assert speed_ms_to_pace_str(0.0) == "-:--"


def test_classify_sport_filters_swim_and_strength():
    # Swim with flippers (βατραχοπέδιλα) must be classified as swim, not cycling!
    swim_text = (
        "Κολύμβηση 2500μ ως εξής:\n"
        "400μ ζέσταμα και\n"
        "2χ500μ rpe 6/7, διαλλ. 45''\n"
        "400μ : 200μ βαρελάκι / 200μ βατραχοπέδιλα\n"
        "200μ χαλαρά στο τέλος."
    )
    is_target, sport = classify_coach_day_text(swim_text)
    assert not is_target
    assert sport == "swim"

    # Strength / gym must be classified as strength
    strength_text = (
        "Πλειομετρικές (full body)\n"
        "3χ12-15 με ταχύτητα και εκρηκτικά\n"
        "Push ups, Ημικαθίσματα, Σανίδα"
    )
    is_target, sport = classify_coach_day_text(strength_text)
    assert not is_target
    assert sport == "strength"

    # Plain rest day
    is_target, sport = classify_coach_day_text("Ρεπό.")
    assert not is_target
    assert sport == "rest"


def test_classify_sport_keeps_running_and_cycling():
    # Running session with pace
    run_text = "20' ζέσταμα / δρομικές / 4χ100μ ανοίγματα και 8χλμ @ 5,15'' 10' αποθεραπεία."
    is_target, sport = classify_coach_day_text(run_text)
    assert is_target
    assert sport == "running"

    # Indoor trainer / προπονητήριο (Tacx)
    trainer_text = "Στο προπονητήριο: 95-100' rpe 4-5, cadence 85-95rpm"
    is_target, sport = classify_coach_day_text(trainer_text)
    assert is_target
    assert sport == "cycling"

    # Outdoor bike ride
    bike_text = "Ποδηλασία: 2,30' ώρες / rpe 4-5 ενδιάμεσα"
    is_target, sport = classify_coach_day_text(bike_text)
    assert is_target
    assert sport == "cycling"

    # Brick session (cycling followed by run)
    brick_text = "Ποδηλασία: 2,30' ώρες. Αμέσως μετά: 45' @ 150-155πλ τρέξιμο."
    is_target, sport = classify_coach_day_text(brick_text)
    assert is_target
    assert sport == "brick"

    # Rest day with optional run
    optional_run = "Ρεπό ή 25' τρέξιμο χαμηλής έντασης."
    is_target, sport = classify_coach_day_text(optional_run)
    assert is_target
    assert sport == "running"


def test_heuristic_parser_running_pace_and_strides():
    text = "20' ζέσταμα / 4χ100μ ανοίγματα και 8χλμ @ 5,15'' 10' αποθεραπεία."
    parsed = parse_workout_with_heuristics("running", text, "Τρίτη")

    assert parsed["sport"] == "running"
    assert "8km" in parsed["workout_name"]
    steps = parsed["steps"]

    # Warmup
    assert steps[0]["step_type"] == "warmup"
    assert steps[0]["condition_value"] == 1200.0  # 20 min

    # Main interval 8km with speed target
    interval_8k = [s for s in steps if s.get("condition_value") == 8000.0][0]
    assert interval_8k["target_type"] == "speed"
    assert interval_8k["target_value_low"] is not None
    assert interval_8k["target_value_high"] is not None

    # Cooldown
    cooldown = [s for s in steps if s["step_type"] == "cooldown"][0]
    assert cooldown["condition_value"] == 600.0  # 10 min


def test_heuristic_parser_cycling_trainer():
    text = "Στο προπονητήριο: 95' rpe 4-5, cadence 85-95rpm"
    parsed = parse_workout_with_heuristics("cycling", text, "Τετάρτη")

    assert parsed["sport"] == "cycling"
    assert "Tacx" in parsed["workout_name"] or "Trainer" in parsed["workout_name"]
    steps = parsed["steps"]
    # Should have warmup and main trainer interval
    interval = [s for s in steps if s["step_type"] == "interval"][0]
    assert interval["condition_value"] == 5700.0  # 95 min


def test_build_garmin_workout_object_serialization():
    parsed = {
        "sport": "running",
        "workout_name": "Test Run 8k",
        "description": "8km test run",
        "estimated_duration_sec": 3600,
        "steps": [
            {
                "step_order": 1,
                "step_type": "warmup",
                "condition_type": "time",
                "condition_value": 600.0,
                "target_type": "no_target",
            },
            {
                "step_order": 2,
                "step_type": "interval",
                "condition_type": "distance",
                "condition_value": 8000.0,
                "target_type": "speed",
                "target_value_low": 3.08,
                "target_value_high": 3.28,
            },
        ],
    }

    workout = build_garmin_workout_object(parsed)
    d = workout.to_dict()

    assert d["workoutName"] == "Test Run 8k"
    assert d["sportType"]["sportTypeKey"] == "running"
    segment = d["workoutSegments"][0]
    assert len(segment["workoutSteps"]) == 2

    step1 = segment["workoutSteps"][0]
    assert step1["stepType"]["stepTypeKey"] == "warmup"
    assert step1["endConditionValue"] == 600.0

    step2 = segment["workoutSteps"][1]
    assert step2["stepType"]["stepTypeKey"] == "interval"
    assert step2["endConditionValue"] == 8000.0
    assert step2["targetValueOne"] == 3.08
    assert step2["targetValueTwo"] == 3.28


def test_sync_workouts_dry_run_mode():
    with patch("src.integrations.garmin_workouts.preview_week_workouts") as mock_preview:
        mock_preview.return_value = {
            "week_start": "2026-09-07",
            "week_end": "2026-09-13",
            "workouts_found": 1,
            "workouts": [
                {
                    "date": "2026-09-08",
                    "sport": "running",
                    "workout_name": "Run 8km",
                    "steps": [{"step_type": "interval"}],
                }
            ],
        }

        res = sync_week_workouts_to_garmin(week_offset=0, dry_run=True)
        assert res["dry_run"] is True
        assert res["total_workouts"] == 1
        assert res["results"][0]["status"] == "dry_run_success"


def test_sync_workouts_live_mocked():
    with patch("src.integrations.garmin_workouts.preview_week_workouts") as mock_preview, \
         patch("src.integrations.garmin_workouts.get_garmin_client") as mock_get_client:

        mock_preview.return_value = {
            "week_start": "2026-09-07",
            "week_end": "2026-09-13",
            "workouts_found": 1,
            "workouts": [
                {
                    "date": "2026-09-08",
                    "sport": "running",
                    "workout_name": "Run 8km",
                    "estimated_duration_sec": 3600,
                    "steps": [
                        {
                            "step_order": 1,
                            "step_type": "interval",
                            "condition_type": "time",
                            "condition_value": 3600.0,
                            "target_type": "no_target",
                        }
                    ],
                }
            ],
        }

        fake_client = MagicMock()
        fake_client.upload_workout.return_value = {"workoutId": 12345}
        fake_client.schedule_workout.return_value = {"workoutScheduleId": 67890}
        mock_get_client.return_value = fake_client

        res = sync_week_workouts_to_garmin(week_offset=0, dry_run=False)
        assert res["dry_run"] is False
        assert res["total_workouts"] == 1
        assert res["results"][0]["status"] == "scheduled"
        assert res["results"][0]["workout_id"] == 12345
        assert res["results"][0]["schedule_id"] == 67890

        fake_client.upload_workout.assert_called_once()
        fake_client.schedule_workout.assert_called_once_with(12345, "2026-09-08")
