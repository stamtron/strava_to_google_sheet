"""
The conversational coach's tool surface and turn loop, with Gemini faked.

Two things are being pinned down here. First, that every tool returns the same
numbers the dashboard does — the tools are the only thing standing between the
model and invented statistics. Second, that the parts which must not depend on
the model complying — the medical disclaimer, the tool-call ceiling, history
persistence — hold regardless of what comes back from the API.

Nothing here touches the network: the client is a stub, activities come from the
shared fixture, and the database lives in `tmp_path`.
"""

import json
from types import SimpleNamespace

import pytest

from src.analytics import coach_agent
from src.analytics.coach_agent import (
    MEDICAL_DISCLAIMER,
    CoachUnavailable,
    build_system_instruction,
    build_tools,
    _fact_key,
    chat,
    extract_session_facts,
    get_session_transcript,
    needs_medical_disclaimer,
)
from src.config import (
    ATHLETE_PB_10K_SEC,
    COACH_CHAT_MODELS,
    COACH_MAX_TOOL_CALLS,
    RUN_SPORTS,
)
from src.storage.chat_store import SqliteMemory, append_message, init_chat_tables


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeSession:
    """Stands in for `client.chats.create(...)`'s returned chat session."""

    def __init__(self, client, model, config, history):
        self.client = client
        self.model = model
        self.config = config
        self.history = history

    def send_message(self, message):
        self.client.sent.append((self.model, message))
        if self.model in self.client.failing_models:
            raise RuntimeError(f"{self.model} is unavailable")
        reply = self.client.replies.pop(0) if self.client.replies else "Looks good."
        return SimpleNamespace(text=reply)


class FakeChats:
    def __init__(self, client):
        self.client = client

    def create(self, model, config, history):
        self.client.created.append({"model": model, "config": config, "history": history})
        return FakeSession(self.client, model, config, history)


class FakeModels:
    def __init__(self, client):
        self.client = client

    def generate_content(self, model, contents, config):
        self.client.searches.append({"model": model, "contents": contents, "config": config})
        if self.client.search_raises:
            raise RuntimeError("search backend down")
        chunks = [
            SimpleNamespace(web=SimpleNamespace(uri=uri, title=title))
            for uri, title in self.client.search_results
        ]
        return SimpleNamespace(
            text="Two options look plausible.",
            candidates=[
                SimpleNamespace(grounding_metadata=SimpleNamespace(grounding_chunks=chunks))
            ],
        )


class FakeClient:
    """A Gemini client with the two surfaces `coach_agent` actually uses."""

    def __init__(self, replies=None, failing_models=(), search_results=(), search_raises=False):
        self.replies = list(replies or [])
        self.failing_models = set(failing_models)
        self.search_results = list(search_results)
        self.search_raises = search_raises
        self.created = []
        self.sent = []
        self.searches = []
        self.chats = FakeChats(self)
        self.models = FakeModels(self)


@pytest.fixture
def provider(activities):
    """
    An activity provider that never reads a database or Strava.

    Newest first, as the store returns them, and with `average_speed` filled in —
    the shared fixture omits it, and pace is read from it rather than derived.
    """
    enriched = [{**a, "average_speed": a["distance"] / a["moving_time"]} for a in activities]
    return lambda limit=None: list(reversed(enriched))


@pytest.fixture
def tools(provider, conn):
    """The tool surface, keyed by name, with a real in-tmp_path memory."""
    functions, used, sources = build_tools(
        activity_provider=provider,
        memory=SqliteMemory(conn),
        client=FakeClient(search_results=[("https://example.com/race", "Autumn Tri")]),
        session_id="test-session",
    )
    return {f.__name__: f for f in functions}, used, sources


# --------------------------------------------------------------------------- #
# Tool surface shape
# --------------------------------------------------------------------------- #


def test_every_planned_tool_is_exposed(tools):
    by_name, _, _ = tools
    assert set(by_name) == {
        "get_week_summary",
        "get_activities",
        "get_training_load",
        "get_run_durability",
        "get_race_projections",
        "get_health_metrics",
        "get_weather_forecast",
        "search_web",
        "find_exercise_videos",
        "remember_fact",
    }


def test_tools_are_plain_functions_with_docstrings_the_sdk_can_read(tools):
    """The docstring and annotations *are* the schema sent to Gemini."""
    by_name, _, _ = tools
    for name, func in by_name.items():
        assert func.__doc__, f"{name} has no docstring, so it has no description"
        assert func.__name__ == name
        for param, annotation in func.__annotations__.items():
            assert annotation in (str, int, float, dict, bool), (
                f"{name}.{param} is annotated {annotation!r}, which the SDK "
                "cannot turn into a JSON schema"
            )


def test_calling_a_tool_records_it_once(tools):
    by_name, used, _ = tools
    by_name["get_week_summary"]()
    by_name["get_week_summary"]("2026-08-03")
    assert used == ["get_week_summary"]


# --------------------------------------------------------------------------- #
# get_week_summary
# --------------------------------------------------------------------------- #


def test_week_summary_defaults_to_the_latest_week_with_activities(tools):
    by_name, _, _ = tools
    summary = by_name["get_week_summary"]()
    assert summary["week_monday"] == "2026-08-10"
    assert summary["week_sunday"] == "2026-08-16"
    assert summary["activities_count"] == 3


def test_week_summary_snaps_a_mid_week_date_to_its_monday(tools):
    """The model is given dates, not necessarily Mondays; failing on that is unhelpful."""
    by_name, _, _ = tools
    assert by_name["get_week_summary"]("2026-08-06")["week_monday"] == "2026-08-03"


def test_week_summary_reports_per_sport_effort_and_sessions(tools):
    by_name, _, _ = tools
    summary = by_name["get_week_summary"]("2026-08-03")
    assert summary["effort_by_sport"]["run"] > 0
    assert summary["effort_by_sport"]["swim"] > 0
    assert {s["id"] for s in summary["sessions"]} == {1, 2, 3}


def test_week_summary_rejects_a_non_date_and_lists_what_exists(tools):
    by_name, _, _ = tools
    result = by_name["get_week_summary"]("last week")
    assert "error" in result
    assert result["available_weeks"] == ["2026-08-03", "2026-08-10"]


def test_week_summary_reports_a_week_with_no_activities_as_such(tools):
    by_name, _, _ = tools
    result = by_name["get_week_summary"]("2026-01-05")
    assert "error" in result
    assert "2026-01-05" in result["error"]


def test_tools_report_an_empty_store_instead_of_zeroes(conn):
    """Zeroed totals would read as real rest weeks; an error reads as missing data."""
    by_name = {
        f.__name__: f
        for f in build_tools(activity_provider=lambda limit=None: [], memory=SqliteMemory(conn))[0]
    }
    for name in ("get_week_summary", "get_training_load", "get_run_durability", "get_race_projections"):
        assert "error" in by_name[name]()


# --------------------------------------------------------------------------- #
# get_activities
# --------------------------------------------------------------------------- #


def test_activities_filter_by_sport_group_not_strava_sport_type(tools):
    """TrailRun must count as running, as it does everywhere else in the app."""
    by_name, _, _ = tools
    result = by_name["get_activities"](sport="run")
    sports = {a["sport"] for a in result["activities"]}
    assert sports == {"Run", "TrailRun"}
    assert sports <= set(RUN_SPORTS)
    assert result["matched_total"] == 3


def test_activities_filter_by_date(tools):
    by_name, _, _ = tools
    result = by_name["get_activities"](since="2026-08-10")
    assert {a["id"] for a in result["activities"]} == {4, 5, 6}


def test_activities_reject_a_malformed_since_date(tools):
    by_name, _, _ = tools
    assert "error" in by_name["get_activities"](since="August")


def test_activities_are_newest_first_and_capped(tools):
    by_name, _, _ = tools
    result = by_name["get_activities"](limit=2)
    assert [a["id"] for a in result["activities"]] == [6, 5]
    assert result["count"] == 2
    assert result["matched_total"] == 6


def test_an_absurd_limit_is_clamped_not_honoured(tools):
    by_name, _, _ = tools
    assert by_name["get_activities"](limit=10_000)["count"] == 6
    assert by_name["get_activities"](limit=-5)["count"] == 1


def test_swim_distance_goes_through_the_dashboard_correction(tools):
    """Strava double-counts this athlete's pool swims; the chat must not."""
    by_name, _, _ = tools
    swim = by_name["get_activities"](sport="swim")["activities"][0]
    assert swim["distance_m"] == 5000  # 10 000 raw / SWIM_DISTANCE_DIVISOR
    assert swim["pace_per_100m"].endswith("/100m")
    assert "distance_km" not in swim


def test_run_briefs_carry_pace_and_bike_briefs_carry_speed(tools):
    by_name, _, _ = tools
    run = by_name["get_activities"](sport="run", limit=1)["activities"][0]
    bike = by_name["get_activities"](sport="bike", limit=1)["activities"][0]
    assert run["pace_per_km"].endswith("/km")
    assert bike["distance_km"] == 10.0
    assert "pace_per_km" not in bike


# --------------------------------------------------------------------------- #
# Load, durability, projections, health
# --------------------------------------------------------------------------- #


def test_training_load_reports_per_sport_acwr_alongside_the_total(tools):
    """Total load can look flat while run load doubles — that's the blind spot."""
    by_name, _, _ = tools
    load = by_name["get_training_load"](weeks=8)
    latest = load["history"][-1]
    assert set(latest["acwr_by_sport"]) == {"run", "bike", "swim"}
    assert "zone" in latest["acwr"]
    assert load["weeks_available"] == 2


def test_training_load_window_is_sliced_after_acwr_is_computed(tools):
    """Trimming first would strip the chronic baseline the reported week needs."""
    by_name, _, _ = tools
    load = by_name["get_training_load"](weeks=1)
    assert load["weeks_reported"] == 1
    assert load["weeks_available"] == 2
    assert load["history"][0]["week_monday"] == "2026-08-10"


def test_training_load_weeks_is_clamped(tools):
    by_name, _, _ = tools
    assert by_name["get_training_load"](weeks=999)["weeks_reported"] == 2


def test_run_durability_returns_the_assessment_profile_and_swap_plan(tools):
    by_name, _, _ = tools
    result = by_name["get_run_durability"]()
    assert result["durability"]["risk_level"] in ("low", "moderate", "high", "unknown")
    assert "substitutions" in result["cross_training"]
    assert result["weeks_analyzed"] == 2


def test_durability_output_stays_machine_readable(tools):
    """Colours, emoji, and prose belong in app.js, not in an analytics payload."""
    by_name, _, _ = tools
    blob = repr(by_name["get_run_durability"]())
    assert not any(ord(c) > 0x2100 for c in blob)


def test_race_projections_expose_both_modes_and_the_configured_pbs(tools):
    by_name, _, _ = tools
    training = by_name["get_race_projections"]("training")
    race = by_name["get_race_projections"]("race_pb")
    assert training["mode"] == "training"
    assert race["mode"] == "race_pb"
    assert "10k" in race["verified_personal_bests"]
    assert "running" in race and "triathlon" in race


def test_an_unrecognised_projection_mode_falls_back_to_training(tools):
    by_name, _, _ = tools
    assert by_name["get_race_projections"]("banana")["mode"] == "training"


def test_health_metrics_degrade_to_an_error_when_garmin_is_unreachable(tools, monkeypatch):
    """Garmin is optional: a login failure must not take down the whole answer."""
    import src.integrations.garmin as garmin

    def boom(week_ranges):
        raise RuntimeError("garmin login failed")

    monkeypatch.setattr(garmin, "get_weekly_health_summaries", boom)
    by_name, _, _ = tools
    result = by_name["get_health_metrics"](weeks=2)
    assert "garmin login failed" in result["error"]


def test_health_metrics_passes_monday_sunday_ranges_per_week(tools, monkeypatch):
    import src.integrations.garmin as garmin

    seen = {}

    def capture(week_ranges):
        seen.update(week_ranges)
        return {k: {"sleep_hours": 7.5} for k in week_ranges}

    monkeypatch.setattr(garmin, "get_weekly_health_summaries", capture)
    by_name, _, _ = tools
    result = by_name["get_health_metrics"](weeks=1)
    assert list(seen) == ["2026-08-10"]
    assert seen["2026-08-10"][0].isoformat() == "2026-08-10"
    assert seen["2026-08-10"][1].isoformat() == "2026-08-16"
def test_weather_forecast_tool_returns_forecast(tools, monkeypatch):
    from src.analytics import coach_agent as coach_agent_mod

    def fake_outlook(past_days=1, forecast_days=7):
        return [
            {
                "date": "2026-09-01",
                "city": "Athens, Greece",
                "condition": "Clear sky",
                "icon": "☀️",
                "temp_max_c": 32.0,
                "temp_min_c": 22.0,
                "precipitation_mm": 0.0,
                "wind_speed_max_kmh": 15.0,
            }
        ]

    monkeypatch.setattr(coach_agent_mod, "get_weather_outlook", fake_outlook)
    by_name, _, _ = tools
    result = by_name["get_weather_forecast"](days=3)
    assert result["city"] == "Athens, Greece"
    assert len(result["forecast"]) == 1
    assert result["forecast"][0]["temp_max_c"] == 32.0


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


def test_search_web_returns_grounded_text_and_accumulates_citations(provider, conn):
    client = FakeClient(search_results=[("https://tri.gr/race", "Autumn Tri"), ("https://x.gr", "X")])
    functions, used, sources = build_tools(
        activity_provider=provider, memory=SqliteMemory(conn), client=client
    )
    by_name = {f.__name__: f for f in functions}

    result = by_name["search_web"]("triathlon in Greece this autumn")
    assert result["summary"]
    assert [s["uri"] for s in result["sources"]] == ["https://tri.gr/race", "https://x.gr"]
    # Citations surface on the endpoint response, not only inside the tool result.
    assert sources == result["sources"]
    assert used == ["search_web"]
    # A separate, search-only request — never mixed with the function declarations.
    assert client.searches[0]["config"] == {"tools": [{"google_search": {}}]}
    assert client.created == []


def test_repeated_searches_do_not_duplicate_a_citation(provider, conn):
    client = FakeClient(search_results=[("https://tri.gr/race", "Autumn Tri")])
    functions, _, sources = build_tools(
        activity_provider=provider, memory=SqliteMemory(conn), client=client
    )
    by_name = {f.__name__: f for f in functions}
    by_name["search_web"]("races")
    by_name["search_web"]("races again")
    assert len(sources) == 1


def test_exercise_videos_prefer_youtube_links_when_present(provider, conn):
    client = FakeClient(
        search_results=[
            ("https://blog.example.com/calf-raises", "A blog post"),
            ("https://www.youtube.com/watch?v=abc", "Single leg calf raise"),
        ]
    )
    by_name = {
        f.__name__: f
        for f in build_tools(activity_provider=provider, memory=SqliteMemory(conn), client=client)[0]
    }
    result = by_name["find_exercise_videos"]("single leg calf raise for runners")
    assert [s["uri"] for s in result["sources"]] == ["https://www.youtube.com/watch?v=abc"]
    assert "youtube.com" in client.searches[0]["contents"]


def test_exercise_videos_fall_back_to_whatever_was_found(provider, conn):
    """Filtering to nothing would be worse than returning a non-YouTube source."""
    client = FakeClient(search_results=[("https://blog.example.com/x", "A blog post")])
    by_name = {
        f.__name__: f
        for f in build_tools(activity_provider=provider, memory=SqliteMemory(conn), client=client)[0]
    }
    assert len(by_name["find_exercise_videos"]("split squat")["sources"]) == 1


def test_a_failing_search_is_reported_to_the_model_not_raised(provider, conn):
    client = FakeClient(search_raises=True)
    by_name = {
        f.__name__: f
        for f in build_tools(activity_provider=provider, memory=SqliteMemory(conn), client=client)[0]
    }
    assert "error" in by_name["search_web"]("anything")


def test_search_without_a_client_is_reported_not_crashed():
    by_name = {f.__name__: f for f in build_tools(activity_provider=lambda limit=None: [])[0]}
    assert "error" in by_name["search_web"]("anything")


# --------------------------------------------------------------------------- #
# remember_fact
# --------------------------------------------------------------------------- #


def test_remember_fact_writes_through_to_the_memory(provider, conn):
    memory = SqliteMemory(conn)
    by_name = {
        f.__name__: f
        for f in build_tools(activity_provider=provider, memory=memory, session_id="s1")[0]
    }
    result = by_name["remember_fact"]("Swims on Tuesday mornings.", "preference")

    assert result["stored"] is True
    stored = memory.all_facts()
    assert stored[0]["fact"] == "Swims on Tuesday mornings."
    assert stored[0]["session_id"] == "s1"


def test_remember_fact_reports_a_bad_write_instead_of_raising(provider, conn):
    by_name = {
        f.__name__: f
        for f in build_tools(activity_provider=provider, memory=SqliteMemory(conn))[0]
    }
    assert by_name["remember_fact"]("   ")["stored"] is False


def test_remember_fact_without_a_memory_says_so(provider):
    by_name = {f.__name__: f for f in build_tools(activity_provider=provider, memory=None)[0]}
    assert by_name["remember_fact"]("something")["stored"] is False


# --------------------------------------------------------------------------- #
# System instruction
# --------------------------------------------------------------------------- #


def test_the_system_instruction_carries_the_scope_limit_and_the_pbs():
    instruction = build_system_instruction()
    assert "diagnos" in instruction
    assert "physiotherapist" in instruction
    # The configured 10k PB, not a number the model remembers.
    assert "50m 15s" in instruction
    assert str(ATHLETE_PB_10K_SEC) not in instruction  # formatted, not raw seconds


def test_recalled_memories_are_injected_with_their_category():
    instruction = build_system_instruction(
        [{"category": "injury", "fact": "Left knee ITBS above 40 km/week."}]
    )
    assert "[injury] Left knee ITBS above 40 km/week." in instruction


def test_the_open_dashboard_week_is_labelled_as_context_only(activities):
    from src.analytics.metrics import process_activities_into_weeks

    week = process_activities_into_weeks(activities)["2026-08-10"]
    instruction = build_system_instruction(week_context=week)
    assert "get_week_summary" in instruction
    assert "2026-08-10" in instruction


# --------------------------------------------------------------------------- #
# The medical guardrail
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "my left knee hurts after long runs",
        "I think I have shin splints",
        "should I take an anti-inflammatory?",
        "my achilles is sore",
        "is this tendinopathy?",
    ],
)
def test_medical_topics_are_detected(text):
    assert needs_medical_disclaimer(text) is True


@pytest.mark.parametrize(
    "text",
    ["what pace should I run my 10k at", "find me a triathlon in October", ""],
)
def test_ordinary_training_questions_are_not_flagged(text):
    assert needs_medical_disclaimer(text) is False


def test_the_disclaimer_is_detected_in_the_reply_too():
    """The model can raise an injury the athlete never named."""
    assert needs_medical_disclaimer("how is my form?", "that ramp risks a stress fracture")


# --------------------------------------------------------------------------- #
# chat()
# --------------------------------------------------------------------------- #


def _chat(message, client, conn, provider, **kwargs):
    return chat(
        message,
        client=client,
        conn=conn,
        activity_provider=provider,
        memory=SqliteMemory(conn),
        **kwargs,
    )


def test_a_turn_returns_the_reply_and_a_fresh_session_id(provider, conn):
    client = FakeClient(replies=["Your run load is climbing fast."])
    result = _chat("How is my running load?", client, conn, provider)

    assert result["reply"] == "Your run load is climbing fast."
    assert result["session_id"]
    assert result["source_model"] == COACH_CHAT_MODELS[0]
    assert result["disclaimer_applied"] is False


def test_both_sides_of_the_turn_are_persisted(provider, conn):
    client = FakeClient(replies=["Reply one."])
    result = _chat("Question one?", client, conn, provider)

    stored = get_session_transcript(result["session_id"], conn=conn)
    assert [(m["role"], m["content"]) for m in stored] == [
        ("user", "Question one?"),
        ("model", "Reply one."),
    ]


def test_history_is_replayed_on_the_next_turn(provider, conn):
    client = FakeClient(replies=["Reply one.", "Reply two."])
    first = _chat("Question one?", client, conn, provider)
    _chat("And then?", client, conn, provider, session_id=first["session_id"])

    replayed = client.created[-1]["history"]
    assert [part["parts"][0]["text"] for part in replayed] == ["Question one?", "Reply one."]
    assert [part["role"] for part in replayed] == ["user", "model"]


def test_history_is_bounded_by_the_configured_window(provider, conn, monkeypatch):
    monkeypatch.setattr(coach_agent, "COACH_MAX_HISTORY_MESSAGES", 2)
    session = "bounded"
    init_chat_tables(conn)
    for i in range(6):
        append_message(conn, session, "user" if i % 2 == 0 else "model", f"m{i}")

    client = FakeClient(replies=["ok"])
    _chat("next?", client, conn, provider, session_id=session)
    assert [p["parts"][0]["text"] for p in client.created[-1]["history"]] == ["m4", "m5"]


def test_a_failed_turn_is_not_written_to_the_history(provider, conn):
    """A half-finished turn would poison every later request in the session."""
    client = FakeClient(failing_models=list(COACH_CHAT_MODELS))
    with pytest.raises(CoachUnavailable):
        _chat("Question?", client, conn, provider, session_id="doomed")
    assert get_session_transcript("doomed", conn=conn) == []


def test_the_next_model_is_tried_when_the_first_fails(provider, conn):
    client = FakeClient(replies=["Second model answered."], failing_models=[COACH_CHAT_MODELS[0]])
    result = _chat("Question?", client, conn, provider)
    assert result["source_model"] == COACH_CHAT_MODELS[1]
    assert [model for model, _ in client.sent] == list(COACH_CHAT_MODELS)


def test_an_empty_reply_counts_as_a_failure(provider, conn):
    client = FakeClient(replies=["", "A real answer."])
    result = _chat("Question?", client, conn, provider)
    assert result["reply"] == "A real answer."
    assert result["source_model"] == COACH_CHAT_MODELS[1]


def test_the_disclaimer_is_appended_server_side(provider, conn):
    """The model was told to stay non-diagnostic and ignored it; this still holds."""
    client = FakeClient(replies=["You have patellar tendinopathy. Rest two weeks."])
    result = _chat("My knee hurts when I run downhill", client, conn, provider)

    assert result["reply"].endswith(MEDICAL_DISCLAIMER)
    assert result["disclaimer_applied"] is True
    # And it is persisted with the disclaimer, so a later turn sees it too.
    assert MEDICAL_DISCLAIMER in get_session_transcript(result["session_id"], conn=conn)[1]["content"]


def test_the_disclaimer_is_not_duplicated_if_the_model_already_included_it(provider, conn):
    client = FakeClient(replies=[f"See a professional about that pain.\n\n{MEDICAL_DISCLAIMER}"])
    result = _chat("my knee pain again", client, conn, provider)
    assert result["reply"].count(MEDICAL_DISCLAIMER) == 1


def test_the_tool_loop_is_capped(provider, conn):
    client = FakeClient(replies=["ok"])
    _chat("Question?", client, conn, provider)
    config = client.created[-1]["config"]
    assert config["automatic_function_calling"] == {"maximum_remote_calls": COACH_MAX_TOOL_CALLS}


def test_the_full_tool_surface_is_handed_to_the_model(provider, conn):
    client = FakeClient(replies=["ok"])
    _chat("Question?", client, conn, provider)
    names = {f.__name__ for f in client.created[-1]["config"]["tools"]}
    assert "get_run_durability" in names and "search_web" in names and "get_weather_forecast" in names
    assert len(names) == 10


def test_remembered_facts_reach_the_next_conversation(provider, conn):
    """The point of the memory: a new session still knows about the injury."""
    memory = SqliteMemory(conn)
    memory.remember("Left knee ITBS flares above 40 km per week.", category="injury")

    client = FakeClient(replies=["Keep it under 40."])
    chat(
        "Can I run 50 km next week?",
        client=client,
        conn=conn,
        activity_provider=provider,
        memory=memory,
    )
    instruction = client.created[-1]["config"]["system_instruction"]
    assert "Left knee ITBS" in instruction


def test_a_broken_memory_costs_recall_not_the_answer(provider, conn):
    class ExplodingMemory:
        def recall(self, query, k=5):
            raise RuntimeError("memory backend down")

        def remember(self, *a, **kw):
            raise RuntimeError("memory backend down")

    client = FakeClient(replies=["Still answering."])
    result = chat(
        "How is my week?",
        client=client,
        conn=conn,
        activity_provider=provider,
        memory=ExplodingMemory(),
    )
    assert result["reply"] == "Still answering."


def test_an_empty_message_is_rejected_before_any_api_call(provider, conn):
    client = FakeClient(replies=["never used"])
    with pytest.raises(ValueError):
        _chat("   ", client, conn, provider)
    assert client.sent == []


def test_no_api_key_and_no_injected_client_is_unavailable_not_a_crash(monkeypatch, conn, provider):
    monkeypatch.setattr(coach_agent, "GEMINI_API_KEY", "")
    with pytest.raises(CoachUnavailable):
        chat("Question?", conn=conn, activity_provider=provider)


def test_expired_sessions_are_purged_on_the_next_turn(provider, conn):
    init_chat_tables(conn)
    append_message(conn, "ancient", "user", "hello from long ago")
    conn.execute("UPDATE chat_messages SET created_at = 0.0 WHERE session_id = 'ancient'")
    conn.commit()

    client = FakeClient(replies=["ok"])
    _chat("Question?", client, conn, provider)
    assert get_session_transcript("ancient", conn=conn) == []


def test_tools_used_is_reported_when_the_model_calls_them(provider, conn):
    """The SDK runs the tool loop, so `tools_used` is populated by the closures."""

    class ToolCallingSession(FakeSession):
        def send_message(self, message):
            by_name = {f.__name__: f for f in self.config["tools"]}
            by_name["get_run_durability"]()
            return SimpleNamespace(text="Your ramp rate is the limiter.")

    client = FakeClient()
    client.chats.create = lambda model, config, history: ToolCallingSession(
        client, model, config, history
    )
    result = chat(
        "Is my running load risky?",
        client=client,
        conn=conn,
        activity_provider=provider,
        memory=SqliteMemory(conn),
    )
    assert result["tools_used"] == ["get_run_durability"]


# --------------------------------------------------------------------------- #
# End-of-session fact extraction
# --------------------------------------------------------------------------- #


class ExtractingClient:
    """
    A client whose one `generate_content` returns a canned extraction payload.

    Separate from `FakeClient` because the extraction pass asks for JSON, while
    `FakeModels` answers prose for the search tools.
    """

    def __init__(self, payload, failing_models=()):
        self.payload = payload
        self.failing_models = set(failing_models)
        self.calls = []
        outer = self

        class _Models:
            @staticmethod
            def generate_content(model, contents, config):
                outer.calls.append({"model": model, "contents": contents, "config": config})
                if model in outer.failing_models:
                    raise RuntimeError(f"{model} is unavailable")
                if isinstance(outer.payload, Exception):
                    raise outer.payload
                return SimpleNamespace(text=outer.payload)

        self.models = _Models()


def _transcript(conn, session_id="s1"):
    init_chat_tables(conn)
    append_message(conn, session_id, "user", "My left knee flares up above 40 km a week.")
    append_message(conn, session_id, "assistant", "Then we cap the ramp there.")
    return session_id


def test_durable_facts_are_extracted_and_stored_as_auto(conn, provider):
    session = _transcript(conn)
    memory = SqliteMemory(conn)
    client = ExtractingClient(
        '{"facts": [{"fact": "Left knee flares above 40 km per week.", "category": "injury"}]}'
    )

    result = extract_session_facts(session, client=client, conn=conn, memory=memory)

    assert [f["fact"] for f in result["stored"]] == ["Left knee flares above 40 km per week."]
    assert result["stored"][0]["category"] == "injury"
    assert result["stored"][0]["source"] == "auto"
    assert result["stored"][0]["session_id"] == session
    assert result["source_model"] == COACH_CHAT_MODELS[0]
    assert memory.all_facts()[0]["fact"] == "Left knee flares above 40 km per week."


def test_the_whole_conversation_is_shown_to_the_extractor(conn):
    session = _transcript(conn)
    client = ExtractingClient('{"facts": []}')
    extract_session_facts(session, client=client, conn=conn, memory=SqliteMemory(conn))

    prompt = client.calls[0]["contents"]
    assert "My left knee flares up above 40 km a week." in prompt
    assert "Then we cap the ramp there." in prompt
    assert client.calls[0]["config"]["response_mime_type"] == "application/json"


def test_extraction_is_capped_so_a_transcript_is_not_filed_wholesale(conn):
    """A model asked for durable facts will happily return the whole conversation."""
    session = _transcript(conn)
    words = "shoes bike pool wetsuit watts cadence hills track sauna gels".split()
    facts = [{"fact": f"Owns {w}.", "category": "equipment"} for w in words]
    client = ExtractingClient(json.dumps({"facts": facts}))

    result = extract_session_facts(
        session, client=client, conn=conn, memory=SqliteMemory(conn), max_facts=3
    )
    assert len(result["stored"]) == 3


def test_a_reworded_restatement_of_a_known_fact_is_skipped(conn):
    """Dedup is on significant words, so word order and punctuation don't defeat it."""
    session = _transcript(conn)
    memory = SqliteMemory(conn)
    memory.remember("Left knee flares above 40 km per week.", category="injury")
    client = ExtractingClient(
        '{"facts": [{"fact": "above 40 km per week, left knee flares!", "category": "injury"},'
        ' {"fact": "Owns a Canyon gravel bike.", "category": "equipment"}]}'
    )

    result = extract_session_facts(session, client=client, conn=conn, memory=memory)

    assert [f["fact"] for f in result["stored"]] == ["Owns a Canyon gravel bike."]
    assert len(result["skipped"]) == 1
    assert len(memory.all_facts()) == 2


def test_a_repeated_fact_within_one_batch_is_only_stored_once(conn):
    session = _transcript(conn)
    memory = SqliteMemory(conn)
    client = ExtractingClient(
        '{"facts": [{"fact": "Swims on Tuesdays.", "category": "preference"},'
        ' {"fact": "Swims on Tuesdays.", "category": "preference"}]}'
    )

    result = extract_session_facts(session, client=client, conn=conn, memory=memory)
    assert len(result["stored"]) == 1
    assert len(result["skipped"]) == 1


def test_an_unknown_category_is_coerced_by_the_memory(conn):
    session = _transcript(conn)
    memory = SqliteMemory(conn)
    client = ExtractingClient('{"facts": [{"fact": "Hates treadmills.", "category": "vibes"}]}')

    result = extract_session_facts(session, client=client, conn=conn, memory=memory)
    assert result["stored"][0]["category"] == "other"


def test_an_empty_transcript_costs_no_model_call(conn):
    """Nothing was said, so there is nothing to mine — and no request worth paying for."""
    init_chat_tables(conn)
    client = ExtractingClient('{"facts": [{"fact": "Should not happen.", "category": "other"}]}')

    result = extract_session_facts("never-existed", client=client, conn=conn, memory=SqliteMemory(conn))

    assert result == {
        "session_id": "never-existed",
        "stored": [],
        "skipped": [],
        "source_model": None,
    }
    assert client.calls == []


def test_the_next_model_is_tried_when_extraction_fails(conn):
    session = _transcript(conn)
    client = ExtractingClient(
        '{"facts": [{"fact": "Races in Greece.", "category": "goal"}]}',
        failing_models=[COACH_CHAT_MODELS[0]],
    )

    result = extract_session_facts(session, client=client, conn=conn, memory=SqliteMemory(conn))
    assert result["source_model"] == COACH_CHAT_MODELS[1]
    assert len(result["stored"]) == 1


def test_unparseable_json_stores_nothing_rather_than_raising(conn):
    """This runs when a conversation closes; there is nobody left to show an error to."""
    session = _transcript(conn)
    memory = SqliteMemory(conn)
    client = ExtractingClient("not json at all")

    result = extract_session_facts(session, client=client, conn=conn, memory=memory)

    assert result["stored"] == []
    assert result["source_model"] is None
    assert memory.all_facts() == []


def test_a_blank_fact_is_skipped_not_stored(conn):
    session = _transcript(conn)
    memory = SqliteMemory(conn)
    client = ExtractingClient('{"facts": [{"fact": "   ", "category": "other"}]}')

    result = extract_session_facts(session, client=client, conn=conn, memory=memory)
    assert result["stored"] == []
    assert memory.all_facts() == []


def test_a_bare_string_fact_is_accepted(conn):
    """The schema is a request, not a guarantee; a list of strings still parses."""
    session = _transcript(conn)
    memory = SqliteMemory(conn)
    client = ExtractingClient('{"facts": ["Races in Greece."]}')

    result = extract_session_facts(session, client=client, conn=conn, memory=memory)
    assert [f["fact"] for f in result["stored"]] == ["Races in Greece."]
    assert result["stored"][0]["category"] == "other"


def test_a_broken_memory_read_does_not_lose_the_extraction(conn, monkeypatch):
    session = _transcript(conn)
    memory = SqliteMemory(conn)
    monkeypatch.setattr(
        memory, "all_facts", lambda: (_ for _ in ()).throw(RuntimeError("index gone"))
    )
    client = ExtractingClient('{"facts": [{"fact": "Races in Greece.", "category": "goal"}]}')

    result = extract_session_facts(session, client=client, conn=conn, memory=memory)
    assert len(result["stored"]) == 1


def test_an_unstorable_fact_is_reported_as_skipped(conn):
    session = _transcript(conn)

    class BrokenMemory(SqliteMemory):
        def remember(self, *args, **kwargs):
            raise RuntimeError("disk full")

    client = ExtractingClient('{"facts": [{"fact": "Races in Greece.", "category": "goal"}]}')
    result = extract_session_facts(
        session, client=client, conn=conn, memory=BrokenMemory(conn)
    )

    assert result["stored"] == []
    assert result["skipped"] == ["Races in Greece."]


def test_fact_keys_ignore_order_case_and_short_words():
    assert _fact_key("Left knee flares above 40 km!") == _fact_key("above 40 km, LEFT knee flares")
    assert _fact_key("   ") == ""
