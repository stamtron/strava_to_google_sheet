"""
AI Coach and Race Prediction Engine.

Generates automated workout feedback, readiness advice,
and race predictions using LLMs (Gemini / OpenAI) with robust heuristic fallbacks.
"""

import math
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


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
        # Search activities for fastest run >= 3km
        candidates = []
        for a in activities:
            sport = a.get("sport_type") or a.get("type", "")
            dist_km = (a.get("distance", 0) or 0) / 1000.0
            moving_time = a.get("moving_time", 0) or 0
            if sport in ["Run", "TrailRun"] and dist_km >= 3.0 and moving_time > 0:
                pace = moving_time / dist_km  # sec/km
                candidates.append((dist_km, moving_time, pace))

        if candidates:
            # Sort by fastest pace
            candidates.sort(key=lambda x: x[2])
            # Take top 10% average or fastest
            best_run = candidates[0]
            best_dist, best_time, best_pace = best_run
            best_5k_pace = best_pace
        else:
            best_5k_pace = 300.0  # Default 5:00/km

    # 5K base
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


def generate_weekly_coaching_insights(
    week_summary: dict,
    garmin_health: dict | None = None,
    athlete_notes: str = "",
    target_language: str = "el",
) -> dict:
    """
    Generate AI coaching evaluation and feedback recommendations.
    Uses Gemini API if available, otherwise high-quality heuristic expert coaching feedback.
    """
    client = _get_gemini_client()

    run_dist = week_summary.get("run_dist", 0.0)
    bike_dist = week_summary.get("bike_dist", 0.0)
    swim_dist = week_summary.get("swim_dist", 0.0)
    total_time_h = week_summary.get("total_time_seconds", 0) / 3600.0
    activities_count = week_summary.get("activities_count", 0)

    sleep_h = garmin_health.get("total_sleep_h") if garmin_health else None
    rhr = garmin_health.get("avg_rhr") if garmin_health else None
    hrv = garmin_health.get("avg_hrv") if garmin_health else None

    # Determine readiness score
    readiness_score = 80
    readiness_status = "Καλή Ετοιμότητα (Good)"
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
- Ύπνος (Garmin Connect): {sleep_h or 'N/A'} ώρες
- Resting HR (HRrest): {rhr or 'N/A'} bpm
- Overnight HRV: {hrv or 'N/A'} ms
- Σχόλιο/Αίσθηση Αθλητή: {athlete_notes or 'Καμία επιπλέον σημείωση'}

Παρακαλώ δώσε σε μορφή JSON:
1. "feedback": Ένα προπονητικό σχόλιο/ανατροφοδότηση 2-3 παραγράφων στα Ελληνικά για τον προπονητή και τον αθλητή.
2. "readiness_evaluation": Εκτίμηση κόπωσης και αποκατάστασης (Recovery & Fatigue analysis).
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
        f"Εξαιρετική εβδομάδα με συνολικό όγκο {total_time_h:.1f} ωρών σε {activities_count} συνεδρίες. "
        f"Η κατανομή σε τρέξιμο ({run_dist:.1f} χλμ), ποδηλασία ({bike_dist:.1f} χλμ) και κολύμπι ({swim_dist:.0f} μ) "
        f"έδειξε σταθερή προπονητική συνέπεια."
    )
    if sleep_h:
        feedback_text += f" Ο συνολικός ύπνος ({sleep_h:.1f}h) και το HRV ({hrv or 'N/A'}) δείχνουν " + (
            "πολύ καλή αποκατάσταση του νευρικού συστήματος." if (hrv or 60) >= 60 else "αυξημένη κόπωση που απαιτεί έμφαση στην αποκατάσταση."
        )

    return {
        "feedback": feedback_text,
        "readiness_evaluation": f"Δείκτης ετοιμότητας στο {readiness_score}%. Το σώμα ανταποκρίνεται καλά στον τρέχοντα προπονητικό όγκο.",
        "recommendations": [
            "Διατήρησε την ένταση στις ζώνες Z1-Z2 στις ενδιάμεσες προπονήσεις για βέλτιστη αποκατάσταση.",
            "Εστίασε στη σταθερή ενυδάτωση και πρόσληψη ηλεκτρολυτών στα μεγάλα ποδηλατικά sessions.",
            "Συνέχισε τη στοχευμένη ενδυνάμωση για σταθεροποίηση κορμού και αποφυγή τραυματισμών.",
        ],
        "readiness_score": readiness_score,
        "source": "smart-heuristics",
    }
