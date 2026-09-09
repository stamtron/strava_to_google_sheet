"""
Garmin Workouts Integration Module.

Extracts coach training plans from Google Sheets, filters exclusively
for Running and Cycling workouts, parses them into structured Garmin Workout
steps (warmup, intervals, pace/power targets, cooldown) using Gemini AI
(with heuristic fallback), and uploads/schedules them onto Garmin Connect
so they sync directly to Garmin Watches and the Tacx Training App.
"""

from datetime import date, datetime, timedelta
import json
import logging
import re
import time
from typing import Any

from garminconnect.workout import (
    ConditionType,
    CyclingWorkout,
    ExecutableStep,
    RepeatGroup,
    RunningWorkout,
    SportType,
    StepType,
    TargetType,
    WorkoutSegment,
)

from src.config import (
    GEMINI_API_KEY,
    GEMINI_MODELS,
    GOOGLE_SHEET_ID,
    SHEET_NAME,
)
from src.integrations.garmin import get_garmin_client
from src.integrations.sheets import (
    execute_with_retry,
    get_sheets_service,
    parse_date_range,
)

logger = logging.getLogger(__name__)

# Keywords for sport detection
SWIM_KEYWORDS = [
    "κολύμβηση",
    "κολυμπι",
    "πισίνα",
    "swim",
    "ύπτιο",
    "πρόσθιο",
    "χεράκια",
    "μόνο πόδια",
    "μικτή",
]

STRENGTH_KEYWORDS = [
    "πλειομετρικές",
    "ενδυνάμωση",
    "full body",
    "push ups",
    "ημικαθίσματα",
    "σανίδα",
    "αλτήρες",
    "μονόζυγο",
    "προβολές",
    "skipping",
    "κουτσό",
]

RUN_KEYWORDS = [
    "τρέξιμο",
    "τρεξιμο",
    "δρομικές",
    "ανοίγματα",
    "διαλειμματική",
    "run",
    "running",
    "treadmill",
    "tempo",
    "long run",
    "στίβο",
]

BIKE_KEYWORDS = [
    "ποδηλασία",
    "ποδηλατο",
    "προπονητήριο",
    "εργόμετρο",
    "tacx",
    "trainer",
    "cadence",
    "rpm",
    "watt",
    "watts",
    "bike",
    "cycling",
]


def pace_str_to_speed_ms(pace_str: str) -> float | None:
    """
    Convert min/km pace string (e.g. '5:15', '5,15', '5.15') to speed in meters/sec.
    Formula: 1000m / (minutes * 60 + seconds).
    """
    if not pace_str:
        return None
    m = re.search(r"(\d{1,2})[:,\.](\d{2})", pace_str.strip())
    if m:
        mins = int(m.group(1))
        secs = int(m.group(2))
        total_sec = mins * 60 + secs
        if total_sec > 0:
            return 1000.0 / total_sec
    return None


def speed_ms_to_pace_str(speed_ms: float) -> str:
    """Convert meters/sec speed to min:sec /km format."""
    if speed_ms <= 0:
        return "-:--"
    total_sec = int(round(1000.0 / speed_ms))
    mins = total_sec // 60
    secs = total_sec % 60
    return f"{mins}:{secs:02d}"


def classify_coach_day_text(text: str) -> tuple[bool, str]:
    """
    Classify a coach cell into sport categories.
    Returns (is_target, sport_type) where sport_type is:
    'running', 'cycling', 'brick', 'swim', 'strength', or 'rest'.
    """
    lower = text.lower().strip()
    if not lower or lower == "ρεπό" or lower.startswith("ρεπό."):
        if "τρέξιμο" in lower or "τρεξιμο" in lower:
            return True, "running"
        return False, "rest"

    # Priority check: Swim session without bike or run
    has_swim = any(k in lower for k in SWIM_KEYWORDS)
    has_strength = any(k in lower for k in STRENGTH_KEYWORDS)
    has_bike = any(k in lower for k in BIKE_KEYWORDS)
    has_run = any(k in lower for k in RUN_KEYWORDS)

    # Brick session: explicit bike + run
    is_brick = ("brick" in lower) or (has_bike and has_run and ("αμέσως μετά" in lower or "έπειτα" in lower or "brick" in lower))
    if is_brick:
        return True, "brick"

    if has_swim and not (has_bike or has_run):
        return False, "swim"

    if has_strength and not (has_bike or has_run):
        return False, "strength"

    if has_run and not has_bike:
        return True, "running"
    if has_bike and not has_run:
        return True, "cycling"

    # Fallback to text matching
    if "τρέξιμο" in lower or "τρεξιμο" in lower:
        return True, "running"
    if "ποδηλασία" in lower or "προπονητήριο" in lower or "εργόμετρο" in lower:
        return True, "cycling"

    return False, "other"


def extract_week_coach_workouts(week_offset: int = 0) -> dict[str, Any]:
    """
    Extract the week's coach workouts from Google Sheets.
    Finds the target week by week_offset (0 = current week, 1 = next week, etc.).
    Returns {
        'week_start': date,
        'week_end': date,
        'row_number': int,
        'layout': 'new' | 'old',
        'days': list[dict] # {date, day_name, raw_text, is_target, sport_type}
    }
    """
    if not GOOGLE_SHEET_ID:
        raise ValueError("GOOGLE_SHEET_ID is not configured in .env")

    service = get_sheets_service(interactive=False)
    sheet = service.spreadsheets()

    # 1. Fetch Column A to locate date ranges
    res = execute_with_retry(
        sheet.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{SHEET_NAME}'!A13:A120",
        )
    )
    col_a = res.get("values", [])

    weeks: list[tuple[int, date, date]] = []
    for idx, row in enumerate(col_a):
        row_num = 13 + idx
        val = row[0] if row else ""
        date_range = parse_date_range(val)
        if date_range:
            weeks.append((row_num, date_range[0], date_range[1]))

    if not weeks:
        raise ValueError(f"No week date ranges found in sheet '{SHEET_NAME}'!A13:A120")

    today = date.today()

    # Find the current week index
    current_idx = None
    for idx, (r, w_start, w_end) in enumerate(weeks):
        if w_start <= today <= w_end:
            current_idx = idx
            break

    if current_idx is None:
        # Fallback to closest week (or last week if today is past all weeks)
        future_weeks = [i for i, (_, w_s, _) in enumerate(weeks) if w_s >= today]
        current_idx = future_weeks[0] if future_weeks else (len(weeks) - 1)

    target_idx = max(0, min(len(weeks) - 1, current_idx + week_offset))
    target_row, week_start, week_end = weeks[target_idx]

    # 2. Find the workout row:
    # In New block layout: Column A within target_row .. target_row+4 has 'ΠΡΟΓΡΑΜΜΑ'.
    # In Old single-row layout: target_row itself contains the workouts.
    block_check_res = execute_with_retry(
        sheet.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{SHEET_NAME}'!A{target_row}:A{target_row + 4}",
        )
    )
    block_col_a = block_check_res.get("values", [])

    workout_row = target_row
    is_new_layout = False

    for offset, row_val in enumerate(block_col_a):
        val_str = (row_val[0] if row_val else "").strip().upper()
        if "ΠΡΟΓΡΑΜΜΑ" in val_str or "PROGRAM" in val_str:
            workout_row = target_row + offset
            is_new_layout = True
            break
        elif "ΑΝΑΤΡΟΦΟΔΟΤΗΣΗ" in val_str or "FEEDBACK" in val_str:
            is_new_layout = True

    # 3. Read the workouts across columns B to H (7 days)
    row_data = execute_with_retry(
        sheet.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{SHEET_NAME}'!B{workout_row}:H{workout_row}",
        )
    )
    raw_cells = row_data.get("values", [[]])[0] if row_data.get("values") else []

    day_names_el = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"]
    days = []

    for i in range(7):
        day_date = week_start + timedelta(days=i)
        cell_raw = raw_cells[i] if i < len(raw_cells) else ""

        # Clean coach cell: strip any Strava sync appended section
        coach_text = cell_raw
        if "── Strava Data ──" in coach_text:
            coach_text = coach_text.split("── Strava Data ──")[0].rstrip()

        is_target, sport_type = classify_coach_day_text(coach_text)

        days.append({
            "date": day_date.isoformat(),
            "day_name": day_names_el[i],
            "raw_text": coach_text.strip(),
            "is_target": is_target,
            "sport_type": sport_type,
        })

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "row_number": target_row,
        "workout_row": workout_row,
        "layout": "new" if is_new_layout else "old",
        "days": days,
    }


def _get_gemini_client():
    """Return configured Gemini client or None."""
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        return genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        logger.warning("Gemini client initialization failed: %s", e)
        return None


def parse_workout_with_heuristics(sport_type: str, coach_text: str, day_name: str) -> dict[str, Any]:
    """
    Robust heuristic fallback parser for workouts when LLM is offline.
    Extracts warmup, steady duration/distance, and pace targets.
    """
    lower = coach_text.lower()
    steps = []
    step_order = 1
    total_est_sec = 0

    # 1. Check warmup
    warmup_match = re.search(r"(\d{1,2})['\’\s]*ζέσταμα", lower)
    warmup_mins = int(warmup_match.group(1)) if warmup_match else 15
    steps.append({
        "step_order": step_order,
        "step_type": "warmup",
        "condition_type": "time",
        "condition_value": float(warmup_mins * 60),
        "target_type": "no_target",
    })
    total_est_sec += warmup_mins * 60
    step_order += 1

    # 2. Check main workout
    if sport_type == "running":
        # Distance pattern: e.g. '8χλμ @ 5,15''' or '8 χλμ'
        dist_match = re.search(r"(\d{1,2}(?:[\.,]\d{1,2})?)\s*χλμ", lower)
        pace_match = re.search(r"@?\s*(\d{1,2}[:,\.]\d{2})", lower)
        time_match = re.search(r"(\d{2,3})['\’\s]*(?:τρέξιμο|rpe)?", lower)
        hr_match = re.search(r"(?:έως|εως)?\s*(\d{3})(?:\s*-\s*(\d{3}))?\s*πλ", lower)
        strides_match = re.search(r"(\d{1,2})[xχ](\d{2,3})μ\s*ανοίγματα", lower)

        # Pre-main strides if mentioned before main run
        if strides_match and ("και" in lower or "έπειτα" in lower) and "στο τέλος" not in lower:
            reps = int(strides_match.group(1))
            stride_dist = float(strides_match.group(2))
            for _ in range(reps):
                steps.append({
                    "step_order": step_order,
                    "step_type": "interval",
                    "condition_type": "distance",
                    "condition_value": stride_dist,
                    "target_type": "no_target",
                })
                step_order += 1
                steps.append({
                    "step_order": step_order,
                    "step_type": "recovery",
                    "condition_type": "distance",
                    "condition_value": stride_dist,
                    "target_type": "no_target",
                })
                step_order += 1
                total_est_sec += 60

        if dist_match:
            dist_km = float(dist_match.group(1).replace(",", "."))
            dist_m = dist_km * 1000.0
            pace_str = pace_match.group(1) if pace_match else None
            speed_ms = pace_str_to_speed_ms(pace_str) if pace_str else None

            interval_step = {
                "step_order": step_order,
                "step_type": "interval",
                "condition_type": "distance",
                "condition_value": dist_m,
                "target_type": "speed" if speed_ms else "no_target",
            }
            if speed_ms:
                interval_step["target_value_low"] = round(speed_ms - 0.10, 2)
                interval_step["target_value_high"] = round(speed_ms + 0.10, 2)
                est_time = int(dist_m / speed_ms)
            else:
                est_time = int(dist_km * 315)
            steps.append(interval_step)
            total_est_sec += est_time
            step_order += 1
        elif time_match:
            run_mins = int(time_match.group(1))
            run_step = {
                "step_order": step_order,
                "step_type": "interval",
                "condition_type": "time",
                "condition_value": float(run_mins * 60),
                "target_type": "no_target",
            }
            if hr_match:
                hr1 = int(hr_match.group(1))
                hr2 = int(hr_match.group(2)) if hr_match.group(2) else hr1
                run_step["target_type"] = "heart_rate"
                run_step["target_value_low"] = float(min(hr1, hr2) - 5)
                run_step["target_value_high"] = float(max(hr1, hr2))
            steps.append(run_step)
            total_est_sec += run_mins * 60
            step_order += 1
        else:
            steps.append({
                "step_order": step_order,
                "step_type": "interval",
                "condition_type": "time",
                "condition_value": 2700.0,
                "target_type": "no_target",
            })
            total_est_sec += 2700
            step_order += 1

        # Post-run strides if mentioned
        if strides_match and "στο τέλος" in lower:
            reps = int(strides_match.group(1))
            stride_dist = float(strides_match.group(2))
            for _ in range(reps):
                steps.append({
                    "step_order": step_order,
                    "step_type": "interval",
                    "condition_type": "distance",
                    "condition_value": stride_dist,
                    "target_type": "no_target",
                })
                step_order += 1
                steps.append({
                    "step_order": step_order,
                    "step_type": "recovery",
                    "condition_type": "distance",
                    "condition_value": stride_dist,
                    "target_type": "no_target",
                })
                step_order += 1
                total_est_sec += 60

    elif sport_type == "cycling":
        # Cycling time: e.g. 95' or 2,30' ώρες or 80' στο εργόμετρο
        hr_match = re.search(r"(\d{1,2})[,:\.](\d{2})['\’\s]*ώρες", lower)
        time_match = re.search(r"(\d{2,3})['\’\s]*(?:στο|rpe|προπονητήριο|εργόμετρο|λεπτά)?", lower)

        if hr_match:
            h = int(hr_match.group(1))
            m = int(hr_match.group(2))
            bike_sec = (h * 60 + m) * 60
        elif time_match:
            bike_sec = int(time_match.group(1)) * 60
        else:
            bike_sec = 3600

        steps.append({
            "step_order": step_order,
            "step_type": "interval",
            "condition_type": "time",
            "condition_value": float(bike_sec),
            "target_type": "no_target",
        })
        total_est_sec += bike_sec
        step_order += 1

    # 3. Check cooldown
    cd_match = re.search(r"(\d{1,2})['\’\s]*αποθεραπεία", lower)
    if cd_match:
        cd_mins = int(cd_match.group(1))
        steps.append({
            "step_order": step_order,
            "step_type": "cooldown",
            "condition_type": "time",
            "condition_value": float(cd_mins * 60),
            "target_type": "no_target",
        })
        total_est_sec += cd_mins * 60

    # Concise title
    if sport_type == "running":
        dist_m = re.search(r"(\d{1,2}(?:[\.,]\d{1,2})?)\s*χλμ", lower)
        time_m = re.search(r"(\d{2,3})['\’\s]*(?:τρέξιμο|rpe)?", lower)
        pace_m = re.search(r"@?\s*(\d{1,2}[:,\.]\d{2})", lower)
        if dist_m and pace_m:
            title = f"Run {dist_m.group(1)}km @ {pace_m.group(1)}"
        elif dist_m:
            title = f"Run {dist_m.group(1)}km"
        elif time_m:
            title = f"Run {time_m.group(1)}m"
        else:
            title = f"Coach Run ({day_name})"
    else:
        is_trainer = "προπονητήριο" in lower or "εργόμετρο" in lower or "tacx" in lower
        prefix = "Tacx Trainer" if is_trainer else "Bike Ride"
        time_m = re.search(r"(\d{2,3})['\’\s]*", lower)
        hr_m = re.search(r"(\d{1,2})[,:\.](\d{2})['\’\s]*ώρες", lower)
        if hr_m:
            title = f"{prefix} {hr_m.group(1)}h{hr_m.group(2)}"
        elif time_m:
            title = f"{prefix} {time_m.group(1)}m"
        else:
            title = f"Coach {prefix} ({day_name})"

    return {
        "sport": sport_type,
        "workout_name": title[:35],
        "description": coach_text[:250],
        "estimated_duration_sec": total_est_sec,
        "steps": steps,
        "source": "heuristic",
    }


def parse_workout_with_gemini(sport_type: str, coach_text: str, day_name: str, target_date: str) -> dict[str, Any]:
    """
    Parse Greek coach notes into structured Garmin Workout JSON steps using Gemini.
    Falls back to heuristic parser on failure.
    """
    client = _get_gemini_client()
    if not client:
        return parse_workout_with_heuristics(sport_type, coach_text, day_name)

    sport_keyword = "running" if sport_type == "running" else "cycling"

    prompt = f"""
You are an expert endurance sports coach converting Greek coaching instructions into a Garmin Structured Workout.
Athlete Day: {day_name}, {target_date}
Sport: {sport_keyword}
Coach Prescription (Greek):
\"\"\"{coach_text}\"\"\"

Please parse this workout and return a clean JSON object following this exact schema:
{{
  "sport": "{sport_keyword}",
  "workout_name": "Short descriptive title under 30 chars (e.g. Coach Run: 8km @ 5:15 or Coach Tacx: 95m Trainer)",
  "description": "Concise summary of steps in English or Greek",
  "estimated_duration_sec": 3600,
  "steps": [
    {{
      "step_order": 1,
      "step_type": "warmup" | "interval" | "recovery" | "cooldown",
      "condition_type": "time" | "distance",
      "condition_value": 900.0,
      "target_type": "speed" | "power" | "heart_rate" | "no_target",
      "target_value_low": 3.08,
      "target_value_high": 3.28
    }}
  ]
}}

Rules:
1. Always preserve warmup and cooldown if mentioned.
2. If intervals with repeats are prescribed (e.g. 4x100m or 6x1000m), you can unroll them or provide interval + recovery steps.
3. For indoor trainer sessions ("προπονητήριο", "εργόμετρο", "tacx"), sport MUST be "cycling".
4. Pace in min/km MUST be converted to speed in m/s (meters per second) for Garmin compatibility.
5. Provide a valid JSON response ONLY.
"""

    # Try the first model only; if unavailable, immediately use heuristic parser
    candidate_models = GEMINI_MODELS[:1]
    for model_name in candidate_models:
        try:
            res = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            data = json.loads(res.text)
            if "steps" in data and len(data["steps"]) > 0:
                data["source"] = model_name
                if len(data.get("workout_name", "")) > 35:
                    data["workout_name"] = data["workout_name"][:35]
                return data
        except Exception as e:
            logger.warning("Gemini parsing with %s failed: %s, falling back to heuristics", model_name, e)
            break

    return parse_workout_with_heuristics(sport_type, coach_text, day_name)


def build_garmin_workout_object(parsed: dict[str, Any]) -> RunningWorkout | CyclingWorkout:
    """
    Build a typed Garmin workout model (RunningWorkout or CyclingWorkout)
    from the parsed JSON dictionary.
    """
    sport = parsed.get("sport", "running")
    workout_name = parsed.get("workout_name", "Coach Workout")[:35]
    desc = parsed.get("description", "")
    est_sec = int(parsed.get("estimated_duration_sec", 3600))
    raw_steps = parsed.get("steps", [])

    step_type_map = {
        "warmup": (StepType.WARMUP, "warmup"),
        "interval": (StepType.INTERVAL, "interval"),
        "recovery": (StepType.RECOVERY, "recovery"),
        "cooldown": (StepType.COOLDOWN, "cooldown"),
        "rest": (StepType.REST, "rest"),
    }

    condition_type_map = {
        "time": (ConditionType.TIME, "time"),
        "distance": (ConditionType.DISTANCE, "distance"),
        "heart_rate": (ConditionType.HEART_RATE, "heart.rate"),
        "calories": (ConditionType.CALORIES, "calories"),
        "power": (ConditionType.POWER, "power"),
    }

    target_type_map = {
        "speed": (TargetType.SPEED, "speed.zone"),
        "power": (TargetType.POWER, "power.zone"),
        "heart_rate": (TargetType.HEART_RATE, "heart.rate.zone"),
        "cadence": (TargetType.CADENCE, "cadence"),
        "no_target": (TargetType.NO_TARGET, "no.target"),
    }

    executable_steps: list[ExecutableStep] = []

    for idx, s in enumerate(raw_steps):
        s_order = s.get("step_order", idx + 1)
        s_type_key = s.get("step_type", "interval")
        c_type_key = s.get("condition_type", "time")
        c_val = float(s.get("condition_value", 300.0))
        t_type_key = s.get("target_type", "no_target")

        st_id, st_key = step_type_map.get(s_type_key, (StepType.INTERVAL, "interval"))
        ct_id, ct_key = condition_type_map.get(c_type_key, (ConditionType.TIME, "time"))
        tt_id, tt_key = target_type_map.get(t_type_key, (TargetType.NO_TARGET, "no.target"))

        step_kwargs: dict[str, Any] = {
            "stepOrder": s_order,
            "stepType": {
                "stepTypeId": st_id,
                "stepTypeKey": st_key,
                "displayOrder": st_id,
            },
            "endCondition": {
                "conditionTypeId": ct_id,
                "conditionTypeKey": ct_key,
                "displayOrder": ct_id,
                "displayable": True,
            },
            "endConditionValue": c_val,
            "targetType": {
                "workoutTargetTypeId": tt_id,
                "workoutTargetTypeKey": tt_key,
                "displayOrder": tt_id,
            },
        }

        # Target range values (pace in m/s, or power in watts, or HR in bpm)
        if "target_value_low" in s and s["target_value_low"] is not None:
            step_kwargs["targetValueOne"] = float(s["target_value_low"])
        if "target_value_high" in s and s["target_value_high"] is not None:
            step_kwargs["targetValueTwo"] = float(s["target_value_high"])

        executable_steps.append(ExecutableStep(**step_kwargs))

    if not executable_steps:
        # Default safety step if none parsed
        executable_steps.append(
            ExecutableStep(
                stepOrder=1,
                stepType={"stepTypeId": StepType.INTERVAL, "stepTypeKey": "interval", "displayOrder": 3},
                endCondition={"conditionTypeId": ConditionType.TIME, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True},
                endConditionValue=float(est_sec),
                targetType={"workoutTargetTypeId": TargetType.NO_TARGET, "workoutTargetTypeKey": "no.target", "displayOrder": 1},
            )
        )

    if sport == "cycling":
        return CyclingWorkout(
            workoutName=workout_name,
            description=desc,
            estimatedDurationInSecs=est_sec,
            workoutSegments=[
                WorkoutSegment(
                    segmentOrder=1,
                    sportType={
                        "sportTypeId": SportType.CYCLING,
                        "sportTypeKey": "cycling",
                        "displayOrder": 2,
                    },
                    workoutSteps=executable_steps,
                )
            ],
        )
    else:
        return RunningWorkout(
            workoutName=workout_name,
            description=desc,
            estimatedDurationInSecs=est_sec,
            workoutSegments=[
                WorkoutSegment(
                    segmentOrder=1,
                    sportType={
                        "sportTypeId": SportType.RUNNING,
                        "sportTypeKey": "running",
                        "displayOrder": 1,
                    },
                    workoutSteps=executable_steps,
                )
            ],
        )


def preview_week_workouts(week_offset: int = 0) -> dict[str, Any]:
    """
    Inspect the week's workouts, filter for running & cycling,
    and parse them into preview structures without touching Garmin API.
    """
    raw_week = extract_week_coach_workouts(week_offset=week_offset)
    parsed_items = []

    for day in raw_week["days"]:
        if not day["is_target"] or not day["raw_text"]:
            continue

        sport_type = day["sport_type"]

        if sport_type == "brick":
            # For brick sessions, we create both a cycling workout and a running workout
            # 1. Cycling portion
            parsed_bike = parse_workout_with_gemini("cycling", day["raw_text"], day["day_name"], day["date"])
            parsed_bike["date"] = day["date"]
            parsed_bike["day_name"] = day["day_name"]
            parsed_bike["raw_coach_text"] = day["raw_text"]
            parsed_items.append(parsed_bike)

            # 2. Running portion
            parsed_run = parse_workout_with_gemini("running", day["raw_text"], day["day_name"], day["date"])
            parsed_run["date"] = day["date"]
            parsed_run["day_name"] = day["day_name"]
            parsed_run["raw_coach_text"] = day["raw_text"]
            parsed_items.append(parsed_run)
        else:
            parsed = parse_workout_with_gemini(sport_type, day["raw_text"], day["day_name"], day["date"])
            parsed["date"] = day["date"]
            parsed["day_name"] = day["day_name"]
            parsed["raw_coach_text"] = day["raw_text"]
            parsed_items.append(parsed)

    return {
        "week_start": raw_week["week_start"],
        "week_end": raw_week["week_end"],
        "row_number": raw_week["row_number"],
        "layout": raw_week["layout"],
        "workouts_found": len(parsed_items),
        "workouts": parsed_items,
    }


def sync_week_workouts_to_garmin(week_offset: int = 0, dry_run: bool = False) -> dict[str, Any]:
    """
    Extract, parse, and upload/schedule Running and Cycling workouts
    to Garmin Connect (which syncs to Watch & Tacx).
    """
    preview = preview_week_workouts(week_offset=week_offset)
    workouts = preview["workouts"]

    if dry_run:
        return {
            "dry_run": True,
            "week_start": preview["week_start"],
            "week_end": preview["week_end"],
            "total_workouts": len(workouts),
            "results": [
                {
                    "date": w["date"],
                    "sport": w["sport"],
                    "workout_name": w["workout_name"],
                    "steps_count": len(w.get("steps", [])),
                    "status": "dry_run_success",
                }
                for w in workouts
            ],
        }

    client = get_garmin_client()
    if not client:
        raise ConnectionError("Failed to authenticate with Garmin Connect. Check GARMIN_EMAIL and GARMIN_PASSWORD.")

    results = []

    for w in workouts:
        target_date = w["date"]
        workout_obj = build_garmin_workout_object(w)
        payload = workout_obj.to_dict()

        try:
            # 1. Upload to Garmin Workout library
            upload_res = client.upload_workout(payload)
            workout_id = upload_res.get("workoutId")

            if not workout_id:
                results.append({
                    "date": target_date,
                    "sport": w["sport"],
                    "workout_name": w["workout_name"],
                    "status": "upload_failed",
                    "error": "No workoutId in upload response",
                })
                continue

            # 2. Schedule on athlete's Garmin calendar for that exact date
            sch_res = client.schedule_workout(workout_id, target_date)
            schedule_id = sch_res.get("workoutScheduleId")

            results.append({
                "date": target_date,
                "sport": w["sport"],
                "workout_name": w["workout_name"],
                "workout_id": workout_id,
                "schedule_id": schedule_id,
                "status": "scheduled",
            })
            logger.info("Successfully scheduled workout %s on %s (ID: %s)", w["workout_name"], target_date, workout_id)
        except Exception as e:
            logger.error("Failed to sync workout %s on %s: %s", w["workout_name"], target_date, e)
            results.append({
                "date": target_date,
                "sport": w["sport"],
                "workout_name": w["workout_name"],
                "status": "error",
                "error": str(e),
            })

    return {
        "dry_run": False,
        "week_start": preview["week_start"],
        "week_end": preview["week_end"],
        "total_workouts": len(workouts),
        "results": results,
    }
