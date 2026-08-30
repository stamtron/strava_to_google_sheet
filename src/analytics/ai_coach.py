"""
AI Coach and Race Prediction Engine.

Generates automated workout feedback, readiness advice,
and race predictions using LLMs (Gemini) with robust heuristic fallbacks.
"""

import math
from src.config import GEMINI_API_KEY


def _get_gemini_client():
    """Return configured Gemini client or None if no API key."""
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        return genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"⚠️  Gemini client initialization failed: {e}")
        return None


def calculate_riegel_prediction(dist_km: float, time_sec: float, target_dist_km: float, fatigue_factor: float = 1.06) -> float:
    """Peter Riegel race prediction formula: T2 = T1 * (D2/D1)^1.06"""
    if dist_km <= 0 or time_sec <= 0 or target_dist_km <= 0:
        return 0.0
    return time_sec * ((target_dist_km / dist_km) ** fatigue_factor)


def format_race_time(seconds: float) -> str:
    """Format total seconds into HH:MM:SS or MM:SS."""
    if seconds <= 0:
        return "N/A"
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs > 0:
        return f"{hrs}h {mins:02d}m {secs:02d}s"
    return f"{mins}m {secs:02d}s"


def format_pace_min_km(seconds_per_km: float) -> str:
    """Format seconds/km to min:sec /km."""
    if seconds_per_km <= 0 or math.isinf(seconds_per_km):
        return "-:--"
    mins = int(seconds_per_km // 60)
    secs = int(seconds_per_km % 60)
    return f"{mins}:{secs:02d} /χλμ"


def predict_race_performances(activities: list[dict], custom_5k_pace_sec: float = None) -> dict:
    """
    Calculate race predictions for 5K, 10K, Half Marathon, and Marathon.
    Extracts fastest sustained runs from recent activities or uses provided baseline.
    """
    best_5k_pace = custom_5k_pace_sec

    if not best_5k_pace:
        candidates = []
        for a in activities:
            sport = a.get("sport_type") or a.get("type", "")
            dist_km = (a.get("distance", 0) or 0) / 1000.0
            moving_time = a.get("moving_time", 0) or 0
            if sport in ["Run", "TrailRun"] and dist_km >= 3.0 and moving_time > 0:
                pace = moving_time / dist_km
                candidates.append((dist_km, moving_time, pace))

        if candidates:
            candidates.sort(key=lambda x: x[2])
            best_run = candidates[0]
            best_dist, best_time, best_pace = best_run
            best_5k_pace = best_pace
        else:
            best_5k_pace = 300.0  # Default 5:00/km

    base_5k_time = best_5k_pace * 5.0

    races = [
        {"name": "5K", "dist_km": 5.0, "time_sec": base_5k_time},
        {"name": "10K", "dist_km": 10.0, "time_sec": calculate_riegel_prediction(5.0, base_5k_time, 10.0, 1.06)},
        {"name": "Ημιμαραθώνιος (21.1K)", "dist_km": 21.0975, "time_sec": calculate_riegel_prediction(5.0, base_5k_time, 21.0975, 1.07)},
        {"name": "Μαραθώνιος (42.2K)", "dist_km": 42.195, "time_sec": calculate_riegel_prediction(5.0, base_5k_time, 42.195, 1.08)},
    ]

    results = []
    for r in races:
        time_s = r["time_sec"]
        pace_s = time_s / r["dist_km"]
        results.append({
            "name": r["name"],
            "distance_km": r["dist_km"],
            "predicted_time": format_race_time(time_s),
            "predicted_pace": format_pace_min_km(pace_s),
            "predicted_time_seconds": round(time_s),
        })

    return {
        "base_pace_used": format_pace_min_km(best_5k_pace),
        "predictions": results,
    }


def predict_triathlon_performances(activities: list[dict]) -> dict:
    """
    Calculate comprehensive multi-sport finish time projections for:
    - Sprint (750m swim, 20k bike, 5k run)
    - Olympic (1500m swim, 40k bike, 10k run)
    - 70.3 Half Ironman (1900m swim, 90k bike, 21.1k run)
    - 140.6 Full Ironman (3800m swim, 180k bike, 42.2k run)
    """
    # 1. Base Swim Pace (/100m) - filter realistic pace 65s - 220s /100m
    swim_paces = []
    for a in activities:
        if (a.get("sport_type") or a.get("type")) == "Swim":
            raw_dist = (a.get("distance", 0) or 0) / 2.0  # Halved
            moving_time = a.get("moving_time", 0) or 0
            if raw_dist >= 300 and moving_time > 180:
                pace = moving_time / (raw_dist / 100.0)
                if 65.0 <= pace <= 220.0:
                    swim_paces.append(pace)
    base_swim_100m_sec = min(swim_paces) if swim_paces else 105.0  # Default 1:45/100m

    # 2. Base Bike Speed (km/h) - filter realistic sustained speed 18 - 48 km/h
    bike_speeds = []
    for a in activities:
        sport = a.get("sport_type") or a.get("type", "")
        if "Ride" in sport:
            dist_km = (a.get("distance", 0) or 0) / 1000.0
            moving_time = a.get("moving_time", 0) or 0
            if a.get("trainer", False) and dist_km < 0.1 and moving_time > 0:
                dist_km = (moving_time / 3600.0) * 21.0
            if dist_km >= 15.0 and moving_time > 1200:
                speed = dist_km / (moving_time / 3600.0)
                if 18.0 <= speed <= 48.0:
                    bike_speeds.append(speed)
    base_bike_speed_kmh = max(bike_speeds) if bike_speeds else 28.0  # Default 28 km/h

    # 3. Base Run Pace (sec/km) - filter realistic running pace 180s - 420s /km
    run_paces = []
    for a in activities:
        sport = a.get("sport_type") or a.get("type", "")
        if sport in ["Run", "TrailRun"]:
            dist_km = (a.get("distance", 0) or 0) / 1000.0
            moving_time = a.get("moving_time", 0) or 0
            if dist_km >= 3.0 and moving_time > 600:
                pace = moving_time / dist_km
                if 180.0 <= pace <= 420.0:
                    run_paces.append(pace)
    base_run_km_sec = min(run_paces) if run_paces else 315.0  # Default 5:15/km

    # Calculations for 4 standard triathlon distances
    distances = [
        {
            "category": "Sprint Triathlon",
            "name": "Sprint (750m / 20k / 5k)",
            "swim_m": 750,
            "swim_factor": 1.00,
            "t1_sec": 105,
            "bike_km": 20.0,
            "bike_factor": 1.02,
            "t2_sec": 75,
            "run_km": 5.0,
            "run_factor": 1.03,
        },
        {
            "category": "Olympic Triathlon",
            "name": "Olympic (1.5k / 40k / 10k)",
            "swim_m": 1500,
            "swim_factor": 1.03,
            "t1_sec": 135,
            "bike_km": 40.0,
            "bike_factor": 0.98,
            "t2_sec": 90,
            "run_km": 10.0,
            "run_factor": 1.05,
        },
        {
            "category": "Half Ironman (70.3)",
            "name": "Ironman 70.3 (1.9k / 90k / 21.1k)",
            "swim_m": 1900,
            "swim_factor": 1.06,
            "t1_sec": 180,
            "bike_km": 90.0,
            "bike_factor": 0.94,
            "t2_sec": 120,
            "run_km": 21.0975,
            "run_factor": 1.08,
        },
        {
            "category": "Full Ironman (140.6)",
            "name": "Ironman 140.6 (3.8k / 180k / 42.2k)",
            "swim_m": 3800,
            "swim_factor": 1.10,
            "t1_sec": 270,
            "bike_km": 180.0,
            "bike_factor": 0.88,
            "t2_sec": 180,
            "run_km": 42.195,
            "run_factor": 1.14,
        },
    ]

    triathlon_predictions = []
    for d in distances:
        # Swim split
        swim_pace = base_swim_100m_sec * d["swim_factor"]
        swim_time = (d["swim_m"] / 100.0) * swim_pace

        # Bike split
        bike_speed = base_bike_speed_kmh * d["bike_factor"]
        bike_time = (d["bike_km"] / bike_speed) * 3600.0

        # Run split
        run_pace = base_run_km_sec * d["run_factor"]
        run_time = d["run_km"] * run_pace

        total_time = swim_time + d["t1_sec"] + bike_time + d["t2_sec"] + run_time

        triathlon_predictions.append({
            "name": d["name"],
            "category": d["category"],
            "total_time": format_race_time(total_time),
            "total_time_seconds": round(total_time),
            "splits": {
                "swim": {
                    "distance": f"{d['swim_m']}m",
                    "time": format_race_time(swim_time),
                    "pace": f"{int(swim_pace // 60)}:{int(swim_pace % 60):02d} /100m",
                },
                "t1": format_race_time(d["t1_sec"]),
                "bike": {
                    "distance": f"{d['bike_km']:.0f}km",
                    "time": format_race_time(bike_time),
                    "speed": f"{bike_speed:.1f} km/h",
                },
                "t2": format_race_time(d["t2_sec"]),
                "run": {
                    "distance": f"{d['run_km']:.1f}km",
                    "time": format_race_time(run_time),
                    "pace": format_pace_min_km(run_pace),
                },
            },
        })

    return {
        "baselines": {
            "swim_100m": f"{int(base_swim_100m_sec // 60)}:{int(base_swim_100m_sec % 60):02d} /100m",
            "bike_speed": f"{base_bike_speed_kmh:.1f} km/h",
            "run_pace": format_pace_min_km(base_run_km_sec),
        },
        "predictions": triathlon_predictions,
    }



def generate_weekly_coaching_insights(
    week_summary: dict,
    garmin_health: dict | None = None,
    athlete_notes: str = "",
    target_language: str = "el",
) -> dict:
    """
    Generate AI coaching evaluation, Relative Effort assessment, and recommendations.
    Uses Gemini API if available, otherwise smart sports-science heuristic coach.
    """
    client = _get_gemini_client()

    run_dist = week_summary.get("run_dist", 0.0)
    bike_dist = week_summary.get("bike_dist", 0.0)
    swim_dist = week_summary.get("swim_dist", 0.0)
    total_time_h = week_summary.get("total_time_seconds", 0) / 3600.0
    activities_count = week_summary.get("activities_count", 0)
    relative_effort = week_summary.get("relative_effort", 0.0)
    elevation_m = week_summary.get("elevation_m", 0.0)

    sleep_h = garmin_health.get("total_sleep_h") if garmin_health else None
    rhr = garmin_health.get("avg_rhr") if garmin_health else None
    hrv = garmin_health.get("avg_hrv") if garmin_health else None

    # Determine physiological readiness score
    readiness_score = 80
    if sleep_h and sleep_h < 45:
        readiness_score -= 15
    elif sleep_h and sleep_h > 52:
        readiness_score += 10

    if hrv and hrv > 70:
        readiness_score += 10
    elif hrv and hrv < 50:
        readiness_score -= 15

    readiness_score = max(30, min(98, readiness_score))

    if client:
        prompt = f"""
Είσαι ένας κορυφαίος προπονητής τριάθλου και αντοχής (Triathlon / Endurance Coach).
Ανάλυσε τα παρακάτω εβδομαδιαία δεδομένα του αθλητή:

- Τρέξιμο: {run_dist:.1f} km
- Ποδηλασία: {bike_dist:.1f} km
- Κολύμβηση: {swim_dist:.0f} m
- Συνολικές Ώρες Προπόνησης: {total_time_h:.1f} ώρες ({activities_count} προπονήσεις)
- Σχετική Προσπάθεια Strava (Relative Effort / Suffer Score): {relative_effort:.0f}
- Συνολικά Υψομετρικά: {elevation_m:.0f} m
- Ύπνος (Garmin Connect): {sleep_h or 'N/A'} ώρες
- Resting HR (HRrest): {rhr or 'N/A'} bpm
- Overnight HRV: {hrv or 'N/A'} ms
- Σημειώσεις Αθλητή: {athlete_notes or 'Καμία επιπλέον σημείωση'}

Παρακαλώ δώσε σε μορφή JSON:
1. "feedback": Ένα εμπεριστατωμένο προπονητικό σχόλιο/ανατροφοδότηση 2-3 παραγράφων στα Ελληνικά για τον προπονητή και τον αθλητή.
2. "readiness_evaluation": Εκτίμηση κόπωσης, σχετικής προσπάθειας και αποκατάστασης (Recovery & Relative Effort load).
3. "recommendations": Λίστα με 3 συγκεκριμένες συμβουλές για την επόμενη εβδομάδα.
4. "readiness_score": Αριθμός 1-100.
"""
        models_to_try = ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-2.0-flash"]
        for model_name in models_to_try:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={"response_mime_type": "application/json"}
                )
                import json
                data = json.loads(response.text)
                return {
                    "feedback": data.get("feedback", ""),
                    "readiness_evaluation": data.get("readiness_evaluation", ""),
                    "recommendations": data.get("recommendations", []),
                    "readiness_score": data.get("readiness_score", readiness_score),
                    "source": model_name,
                }
            except Exception as e:
                print(f"⚠️  Gemini call with {model_name} failed: {e}")

    # Heuristic Coach Fallback
    feedback_text = (
        f"Εξαιρετική εβδομάδα με συνολικό όγκο {total_time_h:.1f} ωρών σε {activities_count} συνεδρίες και Σχετική Προσπάθεια {relative_effort:.0f}. "
        f"Η κατανομή σε τρέξιμο ({run_dist:.1f} χλμ), ποδηλασία ({bike_dist:.1f} χλμ) και κολύμπι ({swim_dist:.0f} μ) "
        f"έδειξε σταθερή προπονητική συνέπεια."
    )
    if elevation_m > 0:
        feedback_text += f" Καταγράφηκαν {elevation_m:.0f}μ συνολικών υψομετρικών."
    if sleep_h:
        feedback_text += f" Ο συνολικός ύπνος ({sleep_h:.1f}h) και το HRV ({hrv or 'N/A'}) δείχνουν " + (
            "πολύ καλή αποκατάσταση του νευρικού συστήματος." if (hrv or 60) >= 60 else "αυξημένη κόπωση που απαιτεί έμφαση στην αποκατάσταση."
        )

    return {
        "feedback": feedback_text,
        "readiness_evaluation": f"Δείκτης ετοιμότητας στο {readiness_score}%. Το σώμα ανταποκρίνεται καλά στον τρέχοντα προπονητικό όγκο και το φορτίο σχετικής προσπάθειας ({relative_effort:.0f}).",
        "recommendations": [
            "Διατήρησε την ένταση στις ζώνες Z1-Z2 στις ενδιάμεσες προπονήσεις για βέλτιστη αποκατάσταση.",
            "Εστίασε στη σταθερή ενυδάτωση και πρόσληψη ηλεκτρολυτών στα μεγάλα ποδηλατικά sessions.",
            "Συνέχισε τη στοχευμένη ενδυνάμωση για σταθεροποίηση κορμού και αποφυγή τραυματισμών.",
        ],
        "readiness_score": readiness_score,
        "source": "smart-heuristics",
    }
