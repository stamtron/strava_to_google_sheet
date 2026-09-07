"""
Weather-adjusted pacing calculator for outdoor endurance running.

Calculates recommended aerobic pace adjustments based on ambient/apparent temperature
and wind speed, preserving heart rate Zone 2 metabolic stimulus and preventing
heat-induced cardiac drift.
"""

from __future__ import annotations

import re


def parse_pace_string_to_sec(pace_str: str) -> float | None:
    """
    Parse pace formats like '5:15', '5,15', '5:15/km', '5,15''' into seconds per km.
    """
    if not pace_str:
        return None

    cleaned = str(pace_str).strip()
    match = re.search(r"(\d{1,2})[:,\.](\d{2})", cleaned)
    if match:
        mins = int(match.group(1))
        secs = int(match.group(2))
        return float(mins * 60 + secs)
    return None


def format_sec_to_pace_string(sec_per_km: float) -> str:
    """Format seconds per km into mm:ss/km string."""
    total_sec = max(0, int(round(sec_per_km)))
    mins = total_sec // 60
    secs = total_sec % 60
    return f"{mins}:{secs:02d}/χλμ"


def calculate_weather_pace_adjustment(
    base_pace_sec_per_km: float,
    temp_c: float | None = None,
    apparent_temp_c: float | None = None,
    wind_kmh: float | None = None,
) -> dict:
    """
    Calculate thermal and aerodynamic pace adjustment.

    Optimal distance running ambient temperature is around 12°C - 15°C.
    Above 15°C, thermoregulation requires redirecting blood flow to the skin,
    causing cardiac drift. To maintain Zone 2 stimulus without spiking HR,
    pace slows down progressively:
      - <= 15°C: 0%
      - 16°C - 20°C: +1.0%
      - 21°C - 25°C: +3.0%
      - 26°C - 30°C: +5.5%
      - 31°C - 35°C: +8.5%
      - > 35°C: +12.0%

    Wind penalty (open loop / headwind exposure above 18 km/h):
      - 18 - 25 km/h: +1.5%
      - 26 - 35 km/h: +3.0%
      - > 35 km/h: +5.0%

    Returns structured dictionary with adjusted pace range and coaching notes.
    """
    if base_pace_sec_per_km <= 0:
        return {
            "needs_adjustment": False,
            "base_pace_sec": base_pace_sec_per_km,
            "adjusted_pace_sec": base_pace_sec_per_km,
            "adjusted_pace_str": "",
            "adjustment_sec": 0,
            "heat_impact": "None",
            "wind_impact": "None",
            "advice": "",
        }

    effective_temp = apparent_temp_c if apparent_temp_c is not None else temp_c
    temp_pct = 0.0
    heat_impact = "None"

    if effective_temp is not None:
        if effective_temp > 35.0:
            temp_pct = 0.12
            heat_impact = "Extreme Heat (>35°C)"
        elif effective_temp > 30.0:
            temp_pct = 0.085
            heat_impact = "High Heat (31–35°C)"
        elif effective_temp > 25.0:
            temp_pct = 0.055
            heat_impact = "Moderate Heat (26–30°C)"
        elif effective_temp > 20.0:
            temp_pct = 0.03
            heat_impact = "Mild Warmth (21–25°C)"
        elif effective_temp > 15.0:
            temp_pct = 0.01
            heat_impact = "Slight Warmth (16–20°C)"

    wind_pct = 0.0
    wind_impact = "None"
    if wind_kmh is not None:
        if wind_kmh > 35.0:
            wind_pct = 0.05
            wind_impact = "Strong Gale (>35 km/h)"
        elif wind_kmh > 25.0:
            wind_pct = 0.03
            wind_impact = "Moderate Wind (26–35 km/h)"
        elif wind_kmh >= 18.0:
            wind_pct = 0.015
            wind_impact = "Breezy (18–25 km/h)"

    total_pct = temp_pct + wind_pct
    adjustment_sec = base_pace_sec_per_km * total_pct
    adjusted_pace_sec = base_pace_sec_per_km + adjustment_sec

    # Recommended range ±4 sec around adjusted target
    min_adjusted = max(0.0, adjusted_pace_sec - 3.0)
    max_adjusted = adjusted_pace_sec + 5.0
    range_str = f"{format_sec_to_pace_string(min_adjusted).replace('/χλμ', '')} – {format_sec_to_pace_string(max_adjusted)}"

    needs_adj = adjustment_sec >= 4.0

    advice_parts = []
    if heat_impact != "None" and temp_pct >= 0.03:
        advice_parts.append(f"Υψηλή θερμοκρασία ({effective_temp:.0f}°C): τρέξε βάσει καρδιακών παλμών (Ζώνη 2), όχι απόλυτου ρυθμού.")
    if wind_impact != "None" and wind_pct >= 0.03:
        advice_parts.append(f"Ενισχυμένος άνεμος ({wind_kmh:.0f} km/h): διατήρησε σταθερή προσπάθεια στα κόντρα κομμάτια.")

    return {
        "needs_adjustment": needs_adj,
        "base_pace_sec": round(base_pace_sec_per_km, 1),
        "adjusted_pace_sec": round(adjusted_pace_sec, 1),
        "adjustment_sec": round(adjustment_sec, 1),
        "adjusted_pace_str": format_sec_to_pace_string(adjusted_pace_sec),
        "adjusted_pace_range": range_str,
        "heat_impact": heat_impact,
        "wind_impact": wind_impact,
        "advice": " ".join(advice_parts),
    }
