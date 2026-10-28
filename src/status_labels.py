"""
Display text for the machine-readable status keys emitted by `src/analytics/`
and the Garmin readiness helpers.

Analytics only name a state ("fresh", "spike", "heat_hr_zone2"); this module is
the single Python home for turning those keys into words. It is shared by the
Telegram presenter and the LLM coach prompt/tool code so both describe a state
the same way. The web dashboard keeps its own English map in web/app.js.
"""

from __future__ import annotations

from typing import Any

from src.formatting import format_mmss

# --- Banister form (TSB) — metrics.calculate_fitness_fatigue `status` ---
TSB_STATUS_LABELS = {
    "transition": "Transition / Untrained",
    "fresh": "Race Ready / Fresh",
    "neutral": "Neutral / Maintenance",
    "optimal": "Optimal Training / Overload",
    "high_fatigue": "High Fatigue / Overreaching Risk",
}

# --- ACWR — metrics.calculate_acwr `status` ---
ACWR_STATUS_LABELS = {
    "low": "Low Load (Recovery / Undertraining)",
    "optimal": "Optimal Zone",
    "overreaching": "Overreaching",
    "spike": "Overtraining Spike Risk",
    "insufficient_history": "Insufficient history",
    "no_baseline": "No chronic baseline",
}

# --- Weather pacing — weather_pace.calculate_weather_pace_adjustment ---
HEAT_IMPACT_LABELS = {
    "none": "None",
    "slight": "Slight Warmth (16–20°C)",
    "mild": "Mild Warmth (21–25°C)",
    "moderate": "Moderate Heat (26–30°C)",
    "high": "High Heat (31–35°C)",
    "extreme": "Extreme Heat (>35°C)",
}
WIND_IMPACT_LABELS = {
    "none": "None",
    "breezy": "Breezy (18–25 km/h)",
    "moderate": "Moderate Wind (26–35 km/h)",
    "gale": "Strong Gale (>35 km/h)",
}
# Greek, formatted with the adjustment's effective_temp_c / wind_kmh.
WEATHER_ADVICE_TEMPLATES = {
    "heat_hr_zone2": "Υψηλή θερμοκρασία ({temp:.0f}°C): τρέξε βάσει καρδιακών παλμών (Ζώνη 2), όχι απόλυτου ρυθμού.",
    "wind_steady_effort": "Ενισχυμένος άνεμος ({wind:.0f} km/h): διατήρησε σταθερή προσπάθεια στα κόντρα κομμάτια.",
}

# --- Garmin workout readiness — garmin.assess_workout_readiness `advice_key` ---
# Greek; `{flags}` is filled with the recovery flags or the per-key fallback.
READINESS_ADVICE_TEMPLATES = {
    "normal": "Εκτέλεσε την προπόνηση κανονικά σύμφωνα με τις οδηγίες του προπονητή.",
    "rest_day": "Ημέρα ξεκούρασης/αποφόρτισης. Επικεντρώσου στον ύπνο και τη σωστή διατροφή.",
    "poor_high_intensity": (
        "⚠️ *Κόκκινος Δείκτης Αποκατάστασης (Poor Recovery):* Η φυσιολογική κόπωση είναι υψηλή "
        "({flags}). Συνιστάται πλήρης ακύρωση των έντονων "
        "διαλειμμάτων και αντικατάσταση με 40' χαλαρό αερόβιο τρέξιμο (Zone 1-2) ή 50' χαλαρό ποδήλατο Tacx."
    ),
    "poor_easy": (
        "⚠️ *Χαμηλή Αποκατάσταση:* Διατήρησε τον ρυθμό αυστηρά στην καρδιακή Zone 1/2. "
        "Μην πιέσεις για ρυθμό αν οι καρδιακοί παλμοί ανεβαίνουν γρήγορα."
    ),
    "compromised_high_intensity": (
        "🟡 *Μέτρια Αποκατάσταση (Compromised):* Τα βιομετρικά δείχνουν μερική κόπωση "
        "({flags}). Συνιστάται μείωση της έντασης "
        "στα διαστήματα κατά 10–15 δευτ/χλμ ή μείωση των επαναλήψεων (π.χ. 4 αντί για 6) "
        "ώστε να μην υπερφορτωθεί το καρδιαγγειακό σύστημα."
    ),
    "compromised_easy": "Αποδεκτή αποκατάσταση. Εκτέλεσε το αερόβιο πρόγραμμα δίνοντας έμφαση στην ενυδάτωση.",
    "optimal": "🟢 *Βέλτιστη Ετοιμότητα (Optimal Readiness):* Οργανισμός πλήρως αναρρωμένος! Έτοιμος για υψηλή ένταση.",
}
_READINESS_FLAGS_FALLBACK = {
    "poor_high_intensity": "χαμηλή ετοιμότητα",
    "compromised_high_intensity": "μη βέλτιστο HRV/ύπνος",
}
SUBSTITUTE_NOTES = {
    "recovery_ride": "Χωρίς αντίσταση, 85-90 rpm cadence για μυϊκή ανακούφιση.",
    "reduce_intensity": "Reduce intensity by 10-15s/km or cut 1-2 intervals",
}

# --- Garmin biometric baselines — garmin.evaluate_biometric_status ---
_BIOMETRIC_TEMPLATES = {
    ("hrv", "low"): "HRV {val:.0f}ms below baseline ({low:.0f}-{high:.0f}ms): fatigue or stress detected",
    ("hrv", "elevated"): "HRV {val:.0f}ms above baseline ({low:.0f}-{high:.0f}ms): high parasympathetic recovery",
    ("hrv", "optimal"): "HRV {val:.0f}ms within normal baseline ({low:.0f}-{high:.0f}ms)",
    ("rhr", "elevated"): "RHR {val:.0f} bpm above baseline ({low:.0f}-{high:.0f} bpm): elevated cardiovascular load",
    ("rhr", "low"): "RHR {val:.0f} bpm below baseline ({low:.0f}-{high:.0f} bpm): optimal resting state",
    ("rhr", "optimal"): "RHR {val:.0f} bpm within normal baseline ({low:.0f}-{high:.0f} bpm)",
}
BIOMETRIC_UNKNOWN_TEXT = "Baseline calculating (insufficient history)"


def tsb_label(status: str | None) -> str:
    return TSB_STATUS_LABELS.get(status or "", "Neutral")


def acwr_label(status: str | None, short: bool = False) -> str:
    """ACWR status text; `short` drops the parenthetical, e.g. "Low Load"."""
    label = ACWR_STATUS_LABELS.get(status or "", "Unknown")
    return label.split("(")[0].strip() if short else label


def format_pace_km(sec_per_km: float) -> str:
    """Seconds per km as Greek-unit "m:ss/χλμ"."""
    return f"{format_mmss(sec_per_km)}/χλμ"


def weather_pace_range(adj: dict[str, Any]) -> str:
    """Recommended pace window, e.g. "5:39 – 5:47/χλμ"."""
    return f"{format_mmss(adj['adjusted_pace_min_sec'])} – {format_pace_km(adj['adjusted_pace_max_sec'])}"


def weather_advice(adj: dict[str, Any]) -> str:
    return " ".join(
        WEATHER_ADVICE_TEMPLATES[key].format(temp=adj.get("effective_temp_c") or 0, wind=adj.get("wind_kmh") or 0)
        for key in adj.get("advice_keys", [])
        if key in WEATHER_ADVICE_TEMPLATES
    )


def readiness_advice(readiness: dict[str, Any]) -> str:
    key = readiness.get("advice_key") or "normal"
    template = READINESS_ADVICE_TEMPLATES.get(key, READINESS_ADVICE_TEMPLATES["normal"])
    flags = readiness.get("flags") or []
    return template.format(flags=", ".join(flags) if flags else _READINESS_FLAGS_FALLBACK.get(key, ""))


def biometric_description(evaluation: dict[str, Any], metric_type: str) -> str:
    """Text for a garmin `*_baseline_eval` entry ({status, value, normal_range})."""
    template = _BIOMETRIC_TEMPLATES.get((metric_type, evaluation.get("status")))
    rng = evaluation.get("normal_range")
    if not template or evaluation.get("value") is None or not rng:
        return BIOMETRIC_UNKNOWN_TEXT
    return template.format(val=evaluation["value"], low=rng[0], high=rng[1])


def gear_display_name(gear: dict[str, Any]) -> str:
    """Strava gear name, or "Shoes (g123)" style fallback when Strava has none."""
    return gear.get("name") or f"{gear.get('gear_type', 'Gear')} ({gear.get('gear_id', '?')})"


def compliance_summary(result: dict[str, Any]) -> str:
    """English one-paragraph summary of a compliance.evaluate_daily_compliance result."""
    status = result.get("status")
    if status == "rest_day_honored":
        return "Rest day honored. Zero training load recorded."
    if status == "rest_day_broken":
        return (
            f"Rest day scheduled, but {result.get('session_count', 0)} session(s) totaling "
            f"{result.get('total_time_min', 0):.0f}m logged."
        )
    if status == "unplanned_training":
        return f"{result.get('session_count', 0)} activity(ies) logged without a scheduled plan."
    if status == "rest_or_empty":
        return "No workout planned and no activities recorded."

    parts = []
    for m in result.get("matches", []):
        sport = m.get("sport")
        comp_pct = m.get("dist_compliance_pct") if m.get("dist_compliance_pct") is not None else m.get("dur_compliance_pct")
        has_km = m.get("planned_km") and m.get("actual_km") is not None
        has_min = m.get("planned_min") and m.get("actual_min") is not None
        if m.get("status") == "spot_on":
            if has_km:
                parts.append(f"{sport}: Executed accurately ({m['actual_km']}km vs planned {m['planned_km']}km).")
            elif has_min:
                parts.append(f"{sport}: Executed accurately ({m['actual_min']:.0f}m vs planned {m['planned_min']:.0f}m).")
            else:
                parts.append(f"{sport}: Executed accurately.")
        elif m.get("status") == "overreach":
            pct_str = f" (+{comp_pct - 100:.0f}%)" if comp_pct is not None else ""
            parts.append(f"{sport}: Volume overshot{pct_str}. Watch recovery.")
        elif m.get("status") == "underreach":
            if has_km:
                parts.append(f"{sport}: Volume under prescribed target ({m['actual_km']}km of {m['planned_km']}km).")
            elif has_min:
                parts.append(f"{sport}: Volume under prescribed target ({m['actual_min']:.0f}m of {m['planned_min']:.0f}m).")
            elif comp_pct is not None:
                parts.append(f"{sport}: Volume under prescribed target ({comp_pct:.0f}%).")
            else:
                parts.append(f"{sport}: Volume under prescribed target.")
        elif m.get("status") == "missed":
            parts.append(f"{sport}: Session was not completed.")
    return " ".join(parts) or "Workout evaluated."
