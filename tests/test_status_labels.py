"""
Unit tests for the status-key -> display-text presenters in src/status_labels.py.
"""

from src.analytics.compliance import evaluate_daily_compliance
from src.analytics.weather_pace import calculate_weather_pace_adjustment
from src.status_labels import (
    BIOMETRIC_UNKNOWN_TEXT,
    HEAT_IMPACT_LABELS,
    WIND_IMPACT_LABELS,
    acwr_label,
    biometric_description,
    compliance_summary,
    format_pace_km,
    gear_display_name,
    readiness_advice,
    tsb_label,
    weather_advice,
    weather_pace_range,
)


def test_tsb_and_acwr_labels():
    assert tsb_label("high_fatigue") == "High Fatigue / Overreaching Risk"
    assert tsb_label(None) == "Neutral"
    assert acwr_label("low") == "Low Load (Recovery / Undertraining)"
    assert acwr_label("low", short=True) == "Low Load"
    assert acwr_label("insufficient_history") == "Insufficient history"
    assert acwr_label("bogus") == "Unknown"


def test_format_pace_km():
    assert format_pace_km(315.0) == "5:15/χλμ"
    assert format_pace_km(270.0) == "4:30/χλμ"
    assert format_pace_km(365.4) == "6:05/χλμ"


def test_weather_presenters_cover_every_key():
    adj = calculate_weather_pace_adjustment(300.0, temp_c=32.0, wind_kmh=28.0)
    assert HEAT_IMPACT_LABELS[adj["heat_impact"]] == "High Heat (31–35°C)"
    assert WIND_IMPACT_LABELS[adj["wind_impact"]] == "Moderate Wind (26–35 km/h)"
    advice = weather_advice(adj)
    assert "Ζώνη 2" in advice and "28 km/h" in advice
    assert weather_pace_range(adj).endswith("/χλμ") and " – " in weather_pace_range(adj)
    assert weather_advice(calculate_weather_pace_adjustment(300.0, temp_c=10.0, wind_kmh=5.0)) == ""


def test_readiness_advice_fills_flags_or_fallback():
    assert "(depressed_hrv, low_sleep)" in readiness_advice(
        {"advice_key": "poor_high_intensity", "flags": ["depressed_hrv", "low_sleep"]}
    )
    assert "(χαμηλή ετοιμότητα)" in readiness_advice({"advice_key": "poor_high_intensity", "flags": []})
    assert "Βέλτιστη Ετοιμότητα" in readiness_advice({"advice_key": "optimal"})
    assert readiness_advice({}).startswith("Εκτέλεσε την προπόνηση κανονικά")


def test_biometric_description():
    ev = {"status": "low", "value": 45, "normal_range": [55.0, 65.0]}
    assert biometric_description(ev, "hrv") == "HRV 45ms below baseline (55-65ms): fatigue or stress detected"
    ev = {"status": "elevated", "value": 60, "normal_range": [47.0, 53.0]}
    assert "elevated cardiovascular load" in biometric_description(ev, "rhr")
    assert biometric_description({"status": "unknown", "value": None, "normal_range": None}, "hrv") == BIOMETRIC_UNKNOWN_TEXT


def test_gear_display_name_fallback():
    assert gear_display_name({"name": "Pegasus 40", "gear_type": "Shoes", "gear_id": "g1"}) == "Pegasus 40"
    assert gear_display_name({"name": None, "gear_type": "Shoes", "gear_id": "g1"}) == "Shoes (g1)"


def test_compliance_summary_matches_former_analytics_prose():
    run_90 = {"id": 1, "name": "Run", "sport_type": "Run", "distance": 15000.0, "moving_time": 5400}
    res = evaluate_daily_compliance("Τρέξιμο 60' rpe 3-4", [run_90])
    assert compliance_summary(res) == "Run: Volume overshot (+50%). Watch recovery."

    assert compliance_summary({"status": "rest_day_honored"}) == "Rest day honored. Zero training load recorded."
    assert (
        compliance_summary({"status": "rest_day_broken", "session_count": 2, "total_time_min": 75.4})
        == "Rest day scheduled, but 2 session(s) totaling 75m logged."
    )
    assert (
        compliance_summary({"status": "unplanned_training", "session_count": 1})
        == "1 activity(ies) logged without a scheduled plan."
    )
    assert compliance_summary({"status": "rest_or_empty"}) == "No workout planned and no activities recorded."
    assert compliance_summary({"status": "evaluated", "matches": []}) == "Workout evaluated."
    missed = {"status": "evaluated", "matches": [{"sport": "Swim", "status": "missed"}]}
    assert compliance_summary(missed) == "Swim: Session was not completed."
