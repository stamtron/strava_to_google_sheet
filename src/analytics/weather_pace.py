"""
Weather-adjusted pacing calculator for outdoor endurance running.

Calculates recommended aerobic pace adjustments based on ambient/apparent temperature
and wind speed, preserving heart rate Zone 2 metabolic stimulus and preventing
heat-induced cardiac drift.
"""

from __future__ import annotations

from src.config import WEATHER_HEAT_BANDS, WEATHER_WIND_BANDS
from src.formatting import parse_pace_to_sec

# Impact keys paired by position with WEATHER_HEAT_BANDS / WEATHER_WIND_BANDS.
# Presentation layers map them (and the advice keys below) to display text.
_HEAT_KEYS = ("extreme", "high", "moderate", "mild", "slight")
_WIND_KEYS = ("gale", "moderate", "breezy")


def parse_pace_string_to_sec(pace_str: str) -> float | None:
    """
    Parse pace formats like '5:15', '5,15', '5:15/km', '5,15\'\'' into seconds per km.
    """
    return parse_pace_to_sec(pace_str)


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

    Returns a structured dictionary of paces in seconds per km, impact keys
    (heat: none | slight | mild | moderate | high | extreme; wind: none | breezy
    | moderate | gale) and `advice_keys` (heat_hr_zone2, wind_steady_effort).
    """
    if base_pace_sec_per_km <= 0:
        return {
            "needs_adjustment": False,
            "base_pace_sec": base_pace_sec_per_km,
            "adjusted_pace_sec": base_pace_sec_per_km,
            "adjustment_sec": 0,
            "heat_impact": "none",
            "wind_impact": "none",
            "advice_keys": [],
        }

    effective_temp = apparent_temp_c if apparent_temp_c is not None else temp_c
    temp_pct = 0.0
    heat_impact = "none"

    if effective_temp is not None:
        for (lower_c, pct), key in zip(WEATHER_HEAT_BANDS, _HEAT_KEYS):
            if effective_temp > lower_c:
                temp_pct, heat_impact = pct, key
                break

    wind_pct = 0.0
    wind_impact = "none"
    if wind_kmh is not None:
        last = len(WEATHER_WIND_BANDS) - 1
        for i, ((lower_kmh, pct), key) in enumerate(zip(WEATHER_WIND_BANDS, _WIND_KEYS)):
            # The mildest band includes its lower bound (18 km/h is "breezy").
            if wind_kmh > lower_kmh or (i == last and wind_kmh >= lower_kmh):
                wind_pct, wind_impact = pct, key
                break

    total_pct = temp_pct + wind_pct
    adjustment_sec = base_pace_sec_per_km * total_pct
    adjusted_pace_sec = base_pace_sec_per_km + adjustment_sec

    # Recommended range ±4 sec around adjusted target
    min_adjusted = max(0.0, adjusted_pace_sec - 3.0)
    max_adjusted = adjusted_pace_sec + 5.0

    needs_adj = adjustment_sec >= 4.0

    advice_keys = []
    if heat_impact != "none" and temp_pct >= 0.03:
        advice_keys.append("heat_hr_zone2")
    if wind_impact != "none" and wind_pct >= 0.03:
        advice_keys.append("wind_steady_effort")

    return {
        "needs_adjustment": needs_adj,
        "base_pace_sec": round(base_pace_sec_per_km, 1),
        "adjusted_pace_sec": round(adjusted_pace_sec, 1),
        "adjustment_sec": round(adjustment_sec, 1),
        "adjusted_pace_min_sec": round(min_adjusted, 1),
        "adjusted_pace_max_sec": round(max_adjusted, 1),
        "effective_temp_c": effective_temp,
        "wind_kmh": wind_kmh,
        "heat_impact": heat_impact,
        "wind_impact": wind_impact,
        "advice_keys": advice_keys,
    }
