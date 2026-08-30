"""Google Sheets layer tests: date-range parsing, weekly totals, cell formatting."""

from datetime import date

from src.integrations.sheets import (
    calculate_weekly_totals,
    format_activity_for_cell,
    format_activities_for_cell,
    parse_date_range,
)


# parse_date_range


def test_parse_day_day_month_year_two_digit():
    assert parse_date_range("17-23/8/'26") == (date(2026, 8, 17), date(2026, 8, 23))


def test_parse_day_day_month_year_four_digit():
    assert parse_date_range("1-7/9/2025") == (date(2025, 9, 1), date(2025, 9, 7))


def test_parse_cross_month_range_with_en_dash():
    assert parse_date_range("24/8–30/8/2026") == (date(2026, 8, 24), date(2026, 8, 30))


def test_parse_cross_month_range_two_digit_year():
    assert parse_date_range("31/8-6/9/'26") == (date(2026, 8, 31), date(2026, 9, 6))


def test_parse_tolerates_whitespace_and_curly_quotes():
    assert parse_date_range(" 17 - 23 / 8 / ’26 ") == (date(2026, 8, 17), date(2026, 8, 23))


def test_parse_rejects_non_dates():
    assert parse_date_range("") is None
    assert parse_date_range(None) is None
    assert parse_date_range("Εβδομάδα") is None
    assert parse_date_range("32-35/8/'26") is None  # impossible day


# calculate_weekly_totals


def test_weekly_totals_split_by_sport():
    activities = [
        {"sport_type": "Run", "distance": 10000.0, "moving_time": 3000, "total_elevation_gain": 100.0},
        {"sport_type": "TrailRun", "distance": 5000.0, "moving_time": 2000, "total_elevation_gain": 250.0},
        {"sport_type": "Ride", "distance": 40000.0, "moving_time": 5400, "total_elevation_gain": 300.0},
        {"sport_type": "Swim", "distance": 3000.0, "moving_time": 2700},
        {"sport_type": "WeightTraining", "moving_time": 1800},
        {"sport_type": "Walk", "distance": 2000.0, "moving_time": 1500},  # counted in nothing
    ]
    run_d, run_t, bike_d, bike_t, bike_e, swim_m, swim_t, str_t = calculate_weekly_totals(activities)

    assert round(run_d, 2) == 15.0
    assert run_t == 5000
    assert round(bike_d, 2) == 40.0
    assert bike_t == 5400
    assert bike_e == 300.0
    assert swim_m == 1500.0  # swim divisor applied
    assert swim_t == 2700
    assert str_t == 1800


def test_weekly_totals_of_nothing_are_zero():
    assert calculate_weekly_totals([]) == (0.0, 0, 0.0, 0, 0.0, 0.0, 0, 0)


def test_weekly_totals_estimate_indoor_bike_distance():
    activities = [{"sport_type": "VirtualRide", "distance": 0.0, "moving_time": 3600, "trainer": True}]
    _, _, bike_d, bike_t, _, _, _, _ = calculate_weekly_totals(activities)
    assert bike_d > 0  # estimated from duration rather than left at zero
    assert bike_t == 3600


# Cell formatting


def test_activity_cell_includes_core_stats():
    act = {
        "sport_type": "Run",
        "name": "Πρωινό τρέξιμο",
        "distance": 10000.0,
        "moving_time": 3000,
        "average_speed": 10000 / 3000,
        "total_elevation_gain": 120.0,
        "average_heartrate": 148.0,
        "max_heartrate": 172.0,
    }
    cell = format_activity_for_cell(act, detail={"calories": 700, "average_temp": 24, "suffer_score": 65})

    assert "Τρέξιμο: Πρωινό τρέξιμο" in cell
    assert "10.00 χλμ" in cell
    assert "Μέσος ρυθμός" in cell
    assert "120 μ" in cell
    assert "148" in cell and "172" in cell
    assert "700" in cell and "24°C" in cell and "65" in cell


def test_bike_activity_uses_speed_label():
    act = {"sport_type": "Ride", "name": "Ποδήλατο", "distance": 40000.0, "moving_time": 5400, "average_speed": 7.4}
    assert "Μέση ταχύτητα" in format_activity_for_cell(act)


def test_activity_cell_omits_missing_optional_fields():
    cell = format_activity_for_cell({"sport_type": "WeightTraining", "name": "Γυμναστήριο", "moving_time": 1800})
    assert "Απόσταση" not in cell
    assert "Υψομετρικά" not in cell
    assert "καρδιακοί" not in cell
    assert "Συνολικός χρόνος: 30λ" in cell


def test_details_lookup_survives_json_string_keys():
    """Cached details come back from JSON with string keys, not int ids."""
    act = {"id": 12345, "sport_type": "Run", "name": "Τρέξιμο", "moving_time": 1800}
    from_int = format_activities_for_cell([act], {12345: {"calories": 400}})
    from_str = format_activities_for_cell([act], {"12345": {"calories": 400}})
    assert "400" in from_int
    assert from_str == from_int


def test_multiple_activities_are_separated():
    acts = [
        {"id": 1, "sport_type": "Run", "name": "A", "moving_time": 1800},
        {"id": 2, "sport_type": "Swim", "name": "B", "moving_time": 1800},
    ]
    cell = format_activities_for_cell(acts, {})
    assert "---" in cell
    assert "A" in cell and "B" in cell
