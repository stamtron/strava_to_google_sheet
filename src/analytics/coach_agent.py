"""
Conversational AI coach: a tool-using Gemini chat grounded in the athlete's data.

Distinct from `ai_coach.generate_weekly_coaching_insights`, which is one
stateless prompt producing the weekly panel. This module answers arbitrary
questions across turns, and it answers them from the same numbers the dashboard
shows — every quantitative claim comes back through a tool call into
`metrics` / `durability` / `ai_coach` rather than out of the model's memory of
the conversation.

Design notes:

* **Tools are thin wrappers.** Each one delegates to code that already exists
  and is already unit-tested. The docstring and the type annotations *are* the
  schema the SDK sends to Gemini, so they are written for the model to read.
* **No `types.` imports.** Config, history, and tool declarations are passed as
  plain dicts, which the SDK accepts. That keeps this module importable and
  fully testable with a fake client, without depending on the SDK's object
  model.
* **Search is a custom function, not built-in grounding.** Mixing built-in
  `google_search` with custom `function_declarations` in one request is
  expressible in the SDK but has historically been rejected by the API, and it
  cannot be verified from this machine. `search_web` therefore issues its own
  separate, search-only request and returns grounded text plus citations, which
  works regardless.
* **Scope is enforced twice.** The system instruction tells the model to stay
  non-diagnostic on pain and injury, and `chat()` appends a fixed disclaimer
  when the exchange touches medical ground. The second check is the one that
  actually holds, because it does not depend on the model complying.
"""

import json
from datetime import date, timedelta

from src.config import (
    ATHLETE_PB_10K_SEC,
    ATHLETE_PB_5K_SEC,
    ATHLETE_PB_HALF_MARATHON_SEC,
    ATHLETE_PB_OLYMPIC_TRI_SEC,
    ATHLETE_PB_SPRINT_TRI_SEC,
    COACH_AUTO_FACT_LIMIT,
    COACH_CHAT_MODELS,
    COACH_MAX_HISTORY_MESSAGES,
    COACH_MAX_TOOL_CALLS,
    COACH_MEMORY_BACKEND,
    COACH_MEMORY_TOP_K,
    COACH_SESSION_TTL,
    GEMINI_API_KEY,
    HR_MAX,
    HR_REST,
)
from src.formatting import corrected_distance_and_speed, format_duration
from src.storage.activity_store import get_activities as store_get_activities, init_db
from src.storage.chat_store import (
    SqliteMemory,
    append_message,
    get_messages,
    init_chat_tables,
    new_session_id,
    purge_expired_sessions,
)
from src.analytics.metrics import (
    EFFORT_KEYS,
    calculate_acwr,
    calculate_relative_effort,
    process_activities_into_weeks,
    sport_group,
)
from src.analytics.ai_coach import (
    format_pace_min_km,
    format_race_time,
    predict_race_performances,
    predict_triathlon_performances,
)
from src.analytics.durability import (
    assess_run_durability,
    sport_strength_profile,
    suggest_cross_training,
)
from src.integrations.weather import get_weather_outlook

# How many activities the tools read. Generous because the questions worth asking
# ("how was my run volume last month?") span far more than a dashboard page, and
# a local SQLite read of a few hundred rows is cheap.
COACH_ACTIVITY_WINDOW = 600

# Appended verbatim when a turn touches pain, injury, or medication. Fixed text,
# added server-side, so the guardrail holds even if the model ignores its
# instructions.
MEDICAL_DISCLAIMER = (
    "⚕️ This is general training information, not a diagnosis. Persistent, "
    "sharp, or worsening pain needs assessment by a physiotherapist or sports "
    "doctor who can examine you."
)

# Substrings that trigger the disclaimer. Deliberately broad — a false positive
# costs one extra sentence, a false negative costs unlabelled medical advice.
_MEDICAL_TRIGGERS = (
    "pain", "hurt", "hurts", "sore", "injur", "ache", "aching", "strain",
    "sprain", "tendon", "tendin", "fracture", "shin", "knee", "achilles",
    "plantar", "itbs", "swollen", "swelling", "inflam", "physio", "doctor",
    "medic", "anti-inflammatory", "ibuprofen", "limp", "numb", "stress react",
)

SYSTEM_INSTRUCTION = """\
You are the athlete's endurance coach inside their own training dashboard. You \
coach one specific person: a triathlete who is strong in swimming and cycling \
and wants to run more, but is impact-sensitive and injury-prone when running \
volume climbs.

HOW TO ANSWER
- Never state a number about this athlete's training from memory. Call a tool. \
The tools read the same database the dashboard renders, so their numbers are \
the only correct ones.
- Prefer few, well-chosen tool calls over many. Answer directly once you have \
the data.
- Be concise and concrete: short paragraphs or tight bullets, actual numbers, \
actual paces, actual sessions. No filler, no restating the question.
- When you use search results, name the source in the text.
- Units are metric and English throughout: km, /km, /100m, km/h.

WHAT YOU DO
- Training load, periodisation, pacing, session design, race strategy, gear, \
race finding, and strength/mobility work.
- When running load looks risky, say so plainly and propose the cross-training \
swap that keeps the aerobic dose while dropping the impact — get_run_durability \
returns exactly that plan.
- Use remember_fact when the athlete tells you something durable about \
themselves: an injury, a constraint, a goal, a schedule, a piece of equipment. \
Do not use it for passing chat.

SCOPE LIMIT — PAIN AND INJURY
You are not a clinician and you do not diagnose. When asked about pain, an \
injury, or anything medical: give general, non-diagnostic information, be \
explicit about what you cannot know without an examination, cite sources when \
you searched, and recommend a physiotherapist or sports doctor. Never name a \
specific condition as the athlete's diagnosis, and never suggest medication or \
dosages.
"""


class CoachUnavailable(RuntimeError):
    """Raised when no Gemini client can be built — no API key, or SDK missing."""


def _get_gemini_client():
    """Return a configured Gemini client, or raise `CoachUnavailable`."""
    if not GEMINI_API_KEY:
        raise CoachUnavailable(
            "GEMINI_API_KEY is not set, so the chat coach cannot run. "
            "The weekly panel still works from heuristics."
        )
    try:
        from google import genai

        return genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:  # noqa: BLE001 - surfaced to the caller as 503
        raise CoachUnavailable(f"Gemini client initialization failed: {e}") from e


# --------------------------------------------------------------------------- #
# Data access
# --------------------------------------------------------------------------- #


def default_activity_provider(limit: int = COACH_ACTIVITY_WINDOW) -> list[dict]:
    """
    Read activities from the local history store, newest first.

    The store rather than Strava: every dashboard load already writes into it, a
    chat turn may fire several tool calls, and none of them should spend a
    rate-limited API request or block on the network.
    """
    try:
        conn = init_db()
    except Exception as e:  # noqa: BLE001 - a tool must degrade, not explode
        print(f"⚠️  Coach history store unavailable: {e}")
        return []
    try:
        return store_get_activities(conn, limit=limit)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  Coach history read failed: {e}")
        return []
    finally:
        conn.close()


def _activity_brief(act: dict) -> dict:
    """
    Condense one activity to what a coach reasons about.

    Full Strava payloads are ~80 fields each; sending them would burn the
    context window on segment efforts and gear ids. Distance and speed come from
    `corrected_distance_and_speed` so the chat agrees with the dashboard on
    indoor rides and swim lengths.
    """
    dist_m, speed_mps = corrected_distance_and_speed(act)
    moving = act.get("moving_time") or 0
    group = sport_group(act.get("sport_type") or act.get("type") or "")

    brief = {
        "id": act.get("id"),
        "date": (act.get("start_date_local") or "")[:10],
        "name": act.get("name"),
        "sport": act.get("sport_type") or act.get("type"),
        "sport_group": group,
        "duration": format_duration(moving),
        "moving_time_sec": moving,
        "relative_effort": round(calculate_relative_effort(act), 1),
    }
    if group == "swim":
        brief["distance_m"] = round(dist_m)
        if speed_mps > 0:
            # `format_pace_min_km` hardcodes the "/km" suffix, so swim pace is
            # formatted here rather than mislabelled.
            per_100m = 100.0 / speed_mps
            brief["pace_per_100m"] = f"{int(per_100m // 60)}:{int(per_100m % 60):02d} /100m"
    else:
        brief["distance_km"] = round(dist_m / 1000.0, 2)
        if group == "run" and speed_mps > 0:
            brief["pace_per_km"] = format_pace_min_km(1000.0 / speed_mps)
        elif speed_mps > 0:
            brief["speed_kmh"] = round(speed_mps * 3.6, 1)
    if act.get("average_heartrate"):
        brief["avg_hr"] = round(act["average_heartrate"])
    if act.get("total_elevation_gain"):
        brief["elevation_m"] = round(act["total_elevation_gain"])
    return brief


def _week_brief(week: dict, acwr: dict | None = None) -> dict:
    """Condense one processed week, keeping machine-readable keys only."""
    brief = {
        "week_monday": week.get("week_monday"),
        "week_sunday": week.get("week_sunday"),
        "activities_count": len(week.get("activities", [])),
        "total_time": format_duration(week.get("total_time_sec", 0)),
        "total_relative_effort": round(week.get("total_relative_effort", 0.0), 1),
        "run_km": round(week.get("run_dist_km", 0.0), 1),
        "run_time": format_duration(week.get("run_time_sec", 0)),
        "bike_km": round(week.get("bike_dist_km", 0.0), 1),
        "bike_time": format_duration(week.get("bike_time_sec", 0)),
        "swim_m": round(week.get("swim_dist_m", 0.0)),
        "swim_time": format_duration(week.get("swim_time_sec", 0)),
        "strength_time": format_duration(week.get("strength_time_sec", 0)),
        "elevation_m": round(week.get("total_elevation_m", 0.0)),
        "effort_by_sport": {
            group: round(week.get(key, 0.0), 1) for group, key in EFFORT_KEYS.items()
        },
    }
    if acwr:
        brief["acwr"] = {
            "ratio": acwr.get("acwr_ratio"),
            "zone": acwr.get("zone"),
            "status": acwr.get("status"),
        }
    return brief


def _athlete_profile() -> dict:
    """The configured PBs and physiology, for the system instruction."""
    return {
        "hr_max": HR_MAX,
        "hr_rest": HR_REST,
        "personal_bests": {
            "5k": format_race_time(ATHLETE_PB_5K_SEC),
            "10k": format_race_time(ATHLETE_PB_10K_SEC),
            "half_marathon": format_race_time(ATHLETE_PB_HALF_MARATHON_SEC),
            "sprint_triathlon": format_race_time(ATHLETE_PB_SPRINT_TRI_SEC),
            "olympic_triathlon": format_race_time(ATHLETE_PB_OLYMPIC_TRI_SEC),
        },
    }


# --------------------------------------------------------------------------- #
# Tool surface
# --------------------------------------------------------------------------- #


def build_tools(
    activity_provider=None,
    memory=None,
    client=None,
    session_id: str | None = None,
    search_model: str | None = None,
) -> tuple[list, list[str], list[dict]]:
    """
    Build the coach's callable tools.

    Returns `(tools, tools_used, sources)`. The two lists are live: the closures
    append to them as the model calls them, which is how the endpoint reports
    what the agent actually did without inspecting SDK internals.

    Everything the tools need is injected rather than imported at call time, so
    tests can drive the full surface with fixture activities, a fake client, and
    a temporary database.
    """
    provider = activity_provider or default_activity_provider
    tools_used: list[str] = []
    sources: list[dict] = []

    # Activities are fetched once per turn and shared by every tool: a single
    # question can trigger four tool calls, and re-reading the store for each
    # would be both slower and — if a sync landed between calls — inconsistent.
    cache: dict = {}

    def _activities() -> list[dict]:
        if "acts" not in cache:
            cache["acts"] = provider(COACH_ACTIVITY_WINDOW) or []
        return cache["acts"]

    def _weeks() -> tuple[dict, list[str]]:
        if "weeks" not in cache:
            weeks = process_activities_into_weeks(_activities())
            cache["weeks"] = (weeks, sorted(weeks.keys()))
        return cache["weeks"]

    def _used(name: str) -> None:
        if name not in tools_used:
            tools_used.append(name)

    def get_week_summary(week_monday: str = "") -> dict:
        """Training totals for one week: volume and time per sport, relative effort, and ACWR.

        Args:
            week_monday: The Monday of the week, as YYYY-MM-DD. Leave empty for
                the most recent week that has activities.
        """
        _used("get_week_summary")
        weeks, keys = _weeks()
        if not keys:
            return {"error": "No activities in the local history store."}

        key = (week_monday or "").strip() or keys[-1]
        if key not in weeks:
            # Snap to the containing week: the model is given a date, not
            # necessarily a Monday, and failing on that is unhelpful.
            try:
                d = date.fromisoformat(key)
                key = (d - timedelta(days=d.weekday())).isoformat()
            except ValueError:
                return {"error": f"'{week_monday}' is not a YYYY-MM-DD date.", "available_weeks": keys}
        if key not in weeks:
            return {"error": f"No activities in the week of {key}.", "available_weeks": keys}

        acwr = calculate_acwr(keys, weeks).get(key)
        summary = _week_brief(weeks[key], acwr)
        summary["sessions"] = [_activity_brief(a) for a in weeks[key].get("activities", [])]
        return summary

    def get_activities(sport: str = "", since: str = "", limit: int = 20) -> dict:
        """List the athlete's individual workouts, newest first.

        Args:
            sport: Filter to one sport group: run, bike, swim, or strength.
                Leave empty for all sports.
            since: Only workouts on or after this date, as YYYY-MM-DD. Leave
                empty for no lower bound.
            limit: Maximum workouts to return, 1-50.
        """
        _used("get_activities")
        acts = _activities()
        group = (sport or "").strip().lower()
        if group:
            acts = [a for a in acts if sport_group(a.get("sport_type") or a.get("type") or "") == group]
        if since.strip():
            try:
                date.fromisoformat(since.strip())
            except ValueError:
                return {"error": f"'{since}' is not a YYYY-MM-DD date."}
            acts = [a for a in acts if (a.get("start_date_local") or "")[:10] >= since.strip()]

        limit = max(1, min(int(limit or 20), 50))
        selected = acts[:limit]
        return {
            "count": len(selected),
            "matched_total": len(acts),
            "activities": [_activity_brief(a) for a in selected],
        }

    def get_training_load(weeks: int = 8) -> dict:
        """Weekly training load history with total and per-sport ACWR.

        ACWR is acute (this week) over chronic (the preceding weeks) load: under
        0.8 is detraining, 0.8-1.3 optimal, 1.3-1.5 overreaching, above 1.5 a
        spike. Per-sport ACWR matters here because total load can look flat
        while run load doubles.

        Args:
            weeks: How many recent weeks to report, 1-26.
        """
        _used("get_training_load")
        all_weeks, keys = _weeks()
        if not keys:
            return {"error": "No activities in the local history store."}

        # ACWR is computed over the full history, then sliced: trimming first
        # would strip the chronic baseline the earliest reported week needs.
        acwr_total = calculate_acwr(keys, all_weeks)
        acwr_by_sport = {
            group: calculate_acwr(keys, all_weeks, effort_key=key)
            for group, key in EFFORT_KEYS.items()
            if group in ("run", "bike", "swim")
        }

        window = keys[-max(1, min(int(weeks or 8), 26)):]
        return {
            "weeks_reported": len(window),
            "weeks_available": len(keys),
            "history": [
                {
                    **_week_brief(all_weeks[k], acwr_total.get(k)),
                    "acwr_by_sport": {
                        group: {
                            "ratio": acwr_by_sport[group].get(k, {}).get("acwr_ratio"),
                            "zone": acwr_by_sport[group].get(k, {}).get("zone"),
                        }
                        for group in acwr_by_sport
                    },
                }
                for k in window
            ],
        }

    def get_run_durability() -> dict:
        """Assess whether the athlete's legs can absorb their current run load.

        Returns the run-specific injury-risk signals — week-over-week volume
        ramp, rest-day spacing, long-run share, training monotony and strain,
        run-only ACWR — plus the cross-training plan that preserves the aerobic
        stimulus with less impact, and the athlete's relative strength by sport.
        Use this for any question about running more, running less, or running
        hurting.
        """
        _used("get_run_durability")
        all_weeks, keys = _weeks()
        if not keys:
            return {"error": "No activities in the local history store."}

        acwr_run = calculate_acwr(keys, all_weeks, effort_key="run_relative_effort")
        durability = assess_run_durability(all_weeks, acwr_run)
        profile = sport_strength_profile(_activities())
        recent = [all_weeks[k].get("run_relative_effort", 0.0) for k in keys[-4:]]
        target_run_load = round(max(recent), 1) if recent else 0.0

        return {
            "durability": durability,
            "profile": profile,
            "cross_training": suggest_cross_training(target_run_load, durability, profile),
            "weeks_analyzed": len(keys),
        }

    def get_race_projections(mode: str = "training") -> dict:
        """Projected race finish times for running distances and triathlons.

        Args:
            mode: "training" projects from recent training paces. "race_pb"
                projects from the athlete's verified race personal bests, which
                are faster and are the right basis for race-day planning.
        """
        _used("get_race_projections")
        acts = _activities()
        if not acts:
            return {"error": "No activities in the local history store."}
        use_pb = (mode or "").strip().lower() in ("race_pb", "pb", "race")
        return {
            "mode": "race_pb" if use_pb else "training",
            "running": predict_race_performances(acts),
            "triathlon": predict_triathlon_performances(activities=acts, use_race_pb=use_pb),
            "verified_personal_bests": _athlete_profile()["personal_bests"],
        }

    def get_health_metrics(weeks: int = 4) -> dict:
        """Garmin recovery biometrics per week: sleep duration, resting HR, and HRV.

        Args:
            weeks: How many recent weeks to report, 1-12.
        """
        _used("get_health_metrics")
        all_weeks, keys = _weeks()
        if not keys:
            return {"error": "No activities in the local history store."}

        window = keys[-max(1, min(int(weeks or 4), 12)):]
        ranges = {
            k: (
                date.fromisoformat(all_weeks[k]["week_monday"]),
                date.fromisoformat(all_weeks[k]["week_sunday"]),
            )
            for k in window
        }
        try:
            from src.integrations.garmin import get_weekly_health_summaries

            health = get_weekly_health_summaries(ranges)
        except Exception as e:  # noqa: BLE001 - Garmin is optional, never fatal
            return {"error": f"Garmin data is unavailable right now ({e})."}
        if not health:
            return {"error": "No Garmin health data for these weeks."}
        return {"weeks": {k: health[k] for k in window if k in health}}

    def search_web(query: str) -> dict:
        """Search the web for current information and return it with source links.

        Use for anything outside the athlete's own data: upcoming races and their
        entry details, gear and clothing recommendations, weather, and general
        sports-science or injury information. Do not use it for the athlete's own
        training numbers — those come from the other tools.

        Args:
            query: What to search for, in natural language.
        """
        _used("search_web")
        return _grounded_search(query)

    def find_exercise_videos(query: str) -> dict:
        """Find video demonstrations of an exercise or drill, with links.

        Use when the athlete asks how to perform something — single-leg calf
        raises, split squats, running drills, swim technique — so they get a
        demonstration rather than a text description.

        Args:
            query: The exercise or drill, e.g. "single leg calf raise for runners".
        """
        _used("find_exercise_videos")
        return _grounded_search(
            f"youtube.com video demonstration: {query}. "
            "Return the video titles, the channels, and the youtube.com links.",
            prefer_domain="youtube.com",
        )

    def remember_fact(fact: str, category: str = "other") -> dict:
        """Store a durable fact about the athlete so it is available in future conversations.

        Use for things that stay true: injuries and their history, schedule
        constraints, season goals, equipment, dietary or training preferences.
        Do not use it for this week's numbers, which are always available from
        the data tools.

        Args:
            fact: The fact, written as a complete sentence in the third person,
                e.g. "Swims on Tuesday mornings before work."
            category: One of injury, preference, goal, equipment, constraint, other.
        """
        _used("remember_fact")
        if memory is None:
            return {"stored": False, "error": "Long-term memory is not available."}
        try:
            entry = memory.remember(fact, category=category, session_id=session_id)
        except ValueError as e:
            return {"stored": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"stored": False, "error": f"Memory write failed: {e}"}
        return {"stored": True, "id": entry["id"], "category": entry["category"]}

    def _grounded_search(query: str, prefer_domain: str | None = None) -> dict:
        """
        Run a search-only Gemini request and return its text plus citations.

        A separate request rather than a `google_search` tool alongside the
        function declarations: that combination is expressible in the SDK but has
        been rejected by the API on some model versions, and it cannot be checked
        from here. One extra request buys a search path that works either way.
        """
        if client is None:
            return {"error": "Web search is unavailable: no Gemini client."}

        model = search_model or COACH_CHAT_MODELS[0]
        try:
            response = client.models.generate_content(
                model=model,
                contents=query,
                config={"tools": [{"google_search": {}}]},
            )
        except Exception as e:  # noqa: BLE001 - the model should hear about it
            return {"error": f"Web search failed: {e}"}

        found = _extract_sources(response)
        if prefer_domain:
            preferred = [s for s in found if prefer_domain in (s.get("uri") or "")]
            found = preferred or found
        for source in found:
            if source not in sources:
                sources.append(source)
        return {
            "summary": getattr(response, "text", None) or "",
            "sources": found,
        }

    def get_weather_forecast(days: int = 7) -> dict:
        """Get the daily weather forecast and recent conditions for the athlete's home city (Athens, Greece).

        Returns daily temperature min/max, apparent temperature, precipitation sum & probability,
        wind speeds, and weather conditions.
        Use when answering questions about outdoor workout planning, cycling kit / clothing,
        hydration in hot weather, wind conditions, or moving workouts indoors.

        Args:
            days: How many forecast days to return, 1-14 (default 7).
        """
        _used("get_weather_forecast")
        try:
            days_count = max(1, min(int(days or 7), 14))
            outlook = get_weather_outlook(past_days=1, forecast_days=days_count)
            return {
                "city": "Athens, Greece",
                "days_count": len(outlook),
                "forecast": outlook,
            }
        except Exception as e:  # noqa: BLE001
            return {"error": f"Weather fetch failed: {e}"}

    tools = [
        get_week_summary,
        get_activities,
        get_training_load,
        get_run_durability,
        get_race_projections,
        get_health_metrics,
        get_weather_forecast,
        search_web,
        find_exercise_videos,
        remember_fact,
    ]
    return tools, tools_used, sources


def _extract_sources(response) -> list[dict]:
    """
    Pull citation URIs and titles out of a grounded response.

    Every access is guarded: grounding metadata is optional, its shape has
    changed across SDK versions, and a missing field must cost a citation, not
    the whole answer.
    """
    found: list[dict] = []
    seen: set[str] = set()
    for candidate in getattr(response, "candidates", None) or []:
        meta = getattr(candidate, "grounding_metadata", None)
        for chunk in getattr(meta, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None)
            if not uri or uri in seen:
                continue
            seen.add(uri)
            found.append({"title": getattr(web, "title", None) or uri, "uri": uri})
    return found


# --------------------------------------------------------------------------- #
# Conversation
# --------------------------------------------------------------------------- #


def build_system_instruction(
    recalled: list[dict] | None = None,
    week_context: dict | None = None,
    today: date | None = None,
) -> str:
    """
    Assemble the system instruction: static coaching brief, then live context.

    Recalled memories and the week the athlete is looking at go here rather than
    into the user message so they cannot be mistaken for something the athlete
    just said, and so they survive every turn of the conversation.
    """
    parts = [SYSTEM_INSTRUCTION]
    parts.append(f"\nTODAY: {(today or date.today()).isoformat()}")
    parts.append("\nATHLETE PROFILE (verified, from configuration):")
    parts.append(json.dumps(_athlete_profile(), ensure_ascii=False))

    if recalled:
        parts.append(
            "\nREMEMBERED ABOUT THIS ATHLETE (from earlier conversations — treat as "
            "true unless they correct you, and do not re-store these):"
        )
        for fact in recalled:
            parts.append(f"- [{fact.get('category', 'other')}] {fact.get('fact')}")

    if week_context:
        parts.append(
            "\nTHE WEEK CURRENTLY OPEN ON THEIR DASHBOARD (context only — call "
            "get_week_summary for authoritative totals):"
        )
        parts.append(json.dumps(_week_brief(week_context), ensure_ascii=False, default=str))

    return "\n".join(parts)


def needs_medical_disclaimer(*texts: str) -> bool:
    """True if any text touches pain, injury, or medication."""
    blob = " ".join(t or "" for t in texts).lower()
    return any(trigger in blob for trigger in _MEDICAL_TRIGGERS)


def _to_history(messages: list[dict]) -> list[dict]:
    """
    Convert stored messages to the SDK's `Content` dict shape.

    Dicts rather than `types.Content` so this module never imports the SDK's
    object model, keeping it testable with a fake client.
    """
    return [
        {
            "role": "model" if m["role"] == "model" else "user",
            "parts": [{"text": m["content"]}],
        }
        for m in messages
    ]


def _default_memory(conn):
    """
    Resolve the configured fact memory: the vector store if asked for, else SQLite.

    The Chroma import is deferred rather than done at module scope because it
    pulls a large dependency tree, and the default keyword backend should not pay
    for a module it never uses. Any failure to start it degrades to SQLite: losing
    semantic recall is acceptable, losing the conversation is not.
    """
    if COACH_MEMORY_BACKEND != "chroma":
        return SqliteMemory(conn)
    try:
        from src.analytics.coach_memory import ChromaMemory

        return ChromaMemory()
    except Exception as e:  # noqa: BLE001 - keyword recall still answers the turn
        print(f"⚠️  Vector memory unavailable ({e}); using keyword memory.")
        return SqliteMemory(conn)


def chat(
    message: str,
    session_id: str | None = None,
    week_context: dict | None = None,
    client=None,
    conn=None,
    activity_provider=None,
    memory=None,
    today: date | None = None,
) -> dict:
    """
    Answer one conversational turn and persist it.

    Returns `{reply, session_id, tools_used, sources, source_model,
    disclaimer_applied, history_length}`.

    The connection is opened here and owned by the caller only when injected —
    tests pass a `tmp_path` database, the endpoint lets this open the real one.
    Raises `CoachUnavailable` when Gemini cannot be reached at all, which the
    endpoint turns into a 503 rather than a broken-looking empty reply.
    """
    message = (message or "").strip()
    if not message:
        raise ValueError("message cannot be empty")

    client = client or _get_gemini_client()

    owns_conn = conn is None
    conn = conn or init_db()
    try:
        init_chat_tables(conn)
        purge_expired_sessions(conn, COACH_SESSION_TTL)

        session_id = session_id or new_session_id()
        memory = memory if memory is not None else _default_memory(conn)
        history = _to_history(get_messages(conn, session_id, COACH_MAX_HISTORY_MESSAGES))

        try:
            recalled = memory.recall(message, k=COACH_MEMORY_TOP_K)
        except Exception as e:  # noqa: BLE001 - memory is an enhancement
            print(f"⚠️  Coach memory recall failed: {e}")
            recalled = []

        tools, tools_used, sources = build_tools(
            activity_provider=activity_provider,
            memory=memory,
            client=client,
            session_id=session_id,
        )
        config = {
            "system_instruction": build_system_instruction(recalled, week_context, today),
            "tools": tools,
            "automatic_function_calling": {"maximum_remote_calls": COACH_MAX_TOOL_CALLS},
        }

        reply, model_used, failures = "", None, []
        for model_name in COACH_CHAT_MODELS:
            try:
                session = client.chats.create(model=model_name, config=config, history=history)
                response = session.send_message(message)
                text = (getattr(response, "text", None) or "").strip()
                if not text:
                    raise RuntimeError("empty reply")
                reply, model_used = text, model_name
                break
            except Exception as e:  # noqa: BLE001 - try the next model, then give up
                failures.append(f"{model_name}: {e}")
                print(f"⚠️  Coach chat with {model_name} failed: {e}")

        if not model_used:
            # Every model's error, not just the last: when Google retires an ID the
            # whole chain 404s, and only the full list makes that diagnosable from
            # the drawer without reading the server log.
            raise CoachUnavailable("No Gemini model could answer — " + "; ".join(failures))

        disclaimer_applied = needs_medical_disclaimer(message, reply)
        if disclaimer_applied and MEDICAL_DISCLAIMER not in reply:
            reply = f"{reply}\n\n{MEDICAL_DISCLAIMER}"

        # Persisted after a successful reply, and only then: a half-finished turn
        # in the history would poison every subsequent request in the session.
        append_message(conn, session_id, "user", message)
        append_message(conn, session_id, "model", reply)

        return {
            "reply": reply,
            "session_id": session_id,
            "tools_used": tools_used,
            "sources": sources,
            "source_model": model_used,
            "disclaimer_applied": disclaimer_applied,
            "history_length": len(history) + 2,
        }
    finally:
        if owns_conn:
            conn.close()


def get_session_transcript(session_id: str, conn=None) -> list[dict]:
    """Return a full stored conversation, oldest first."""
    owns_conn = conn is None
    conn = conn or init_db()
    try:
        init_chat_tables(conn)
        return get_messages(conn, session_id)
    finally:
        if owns_conn:
            conn.close()


_FACT_EXTRACTION_PROMPT = """\
Read this coaching conversation and extract only the DURABLE facts about the \
athlete — things that will still be true next month and that a coach would want \
to know before giving advice again.

Store: injuries and their triggers, recurring constraints (schedule, equipment, \
access), stated goals and target races, equipment they own, firm preferences.

Do NOT store: this week's numbers, anything already derivable from their \
activity data, the coach's own advice, one-off questions, or speculation.

Return JSON: {{"facts": [{{"fact": "...", "category": "one of \
injury|preference|goal|equipment|constraint|other"}}]}}
Each fact must be one self-contained sentence written in the third person. \
Return at most {max_facts}. If nothing durable was said, return an empty list.

CONVERSATION
{transcript}
"""


def extract_session_facts(
    session_id: str,
    client=None,
    conn=None,
    memory=None,
    max_facts: int | None = None,
    model: str | None = None,
) -> dict:
    """
    Mine one finished conversation for durable facts and store them.

    The complement to the `remember_fact` tool: that captures what the athlete
    asked to be remembered, this captures what they mentioned in passing. Facts
    land with `source="auto"` so they are distinguishable — and prunable — from
    the ones they asked for explicitly.

    Never raises on a model or memory failure. This runs when a conversation is
    closed, where there is nobody left to show an error to; a lost fact is a
    smaller loss than a failed request the athlete cannot act on.
    """
    max_facts = max_facts or COACH_AUTO_FACT_LIMIT
    owns_conn = conn is None
    conn = conn or init_db()
    try:
        init_chat_tables(conn)
        transcript = get_messages(conn, session_id)
        if not transcript:
            return {"session_id": session_id, "stored": [], "skipped": [], "source_model": None}

        memory = memory if memory is not None else _default_memory(conn)
        rendered = "\n".join(
            f"{'Athlete' if m['role'] == 'user' else 'Coach'}: {m['content']}" for m in transcript
        )
        prompt = _FACT_EXTRACTION_PROMPT.format(max_facts=max_facts, transcript=rendered)

        client = client or _get_gemini_client()
        candidates, model_used = [], None
        for model_name in [model] if model else COACH_CHAT_MODELS:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={"response_mime_type": "application/json"},
                )
                candidates = json.loads(response.text or "{}").get("facts") or []
                model_used = model_name
                break
            except Exception as e:  # noqa: BLE001 - try the next model, then give up
                print(f"⚠️  Fact extraction with {model_name} failed: {e}")

        # Compared on normalized words so a reworded restatement of a fact already
        # on file doesn't accumulate as a near-duplicate every conversation.
        try:
            known = {_fact_key(f["fact"]) for f in memory.all_facts()}
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  Coach memory read failed during extraction: {e}")
            known = set()

        stored, skipped = [], []
        for candidate in candidates[:max_facts]:
            text = (candidate.get("fact") if isinstance(candidate, dict) else str(candidate)) or ""
            text = text.strip()
            key = _fact_key(text)
            if not key or key in known:
                skipped.append(text)
                continue
            try:
                stored.append(
                    memory.remember(
                        text,
                        category=(candidate.get("category") if isinstance(candidate, dict) else "other")
                        or "other",
                        session_id=session_id,
                        source="auto",
                    )
                )
                known.add(key)
            except Exception as e:  # noqa: BLE001
                print(f"⚠️  Could not store extracted fact: {e}")
                skipped.append(text)

        return {
            "session_id": session_id,
            "stored": stored,
            "skipped": skipped,
            "source_model": model_used,
        }
    finally:
        if owns_conn:
            conn.close()


def _fact_key(fact: str) -> str:
    """A fact's identity for dedup: its significant words, order-independent."""
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in fact or "")
    return " ".join(sorted(w for w in cleaned.split() if len(w) > 2))


def list_remembered_facts(conn=None, memory=None) -> list[dict]:
    """
    Every durable fact the coach holds, newest first.

    A memory that persists indefinitely and cannot be inspected is a liability,
    so this and `forget_remembered_fact` exist for the athlete, not the model.
    """
    owns_conn = conn is None and memory is None
    if memory is None:
        conn = conn or init_db()
    try:
        return (memory or _default_memory(conn)).all_facts()
    finally:
        if owns_conn:
            conn.close()


def forget_remembered_fact(fact_id: str, conn=None, memory=None) -> bool:
    """Delete one durable fact. False if that id was never stored."""
    owns_conn = conn is None and memory is None
    if memory is None:
        conn = conn or init_db()
    try:
        return (memory or _default_memory(conn)).forget(fact_id)
    finally:
        if owns_conn:
            conn.close()
