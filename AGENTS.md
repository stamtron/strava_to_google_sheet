# 🤖 AGENTS.md — Strava & Garmin to Google Sheet

This document serves as the primary technical guide for AI agents and developers working on the `strava_to_google_sheet` repository.

---

## 📌 Project Overview

`strava_to_google_sheet` is an automated fitness tracking integration that fetches workout activities from **Strava** and 24/7 health biometrics (Sleep, Resting Heart Rate, HRV) from **Garmin Connect**, formatting and synchronizing them into a structured Greek coaching spreadsheet in **Google Sheets**, while providing a modern **FastAPI + Chart.js** analytics web app with **AI Coaching (Gemini)** and **Acute:Chronic Workload Ratio (ACWR)** tracking.

Beyond the sheet sync it also carries a **local SQLite history store** (paginated Strava
backfill), a **run-durability & cross-training engine**, and a **conversational Gemini
coach** with function-calling tools, grounded web search, and persistent memory.

---

## 🏗️ Architecture & Module Organization

```
strava_to_google_sheet/
├── src/                          # Core backend package
│   ├── config.py                 # Centralized configuration & environment variables
│   ├── formatting.py             # Duration/pace formatting & sport data corrections
│   ├── integrations/             # External service APIs
│   │   ├── strava.py             # Strava OAuth2 & activity fetcher
│   │   ├── strava_backfill.py    # Paginated full-history import + incremental sync
│   │   ├── garmin.py             # Garmin Connect authentication & biometrics
│   │   └── sheets.py             # Google Sheets API & dual-layout sync engine
│   ├── analytics/                # Data processing & AI
│   │   ├── metrics.py            # Relative Effort (Suffer Score), ACWR, weekly/monthly volume
│   │   ├── durability.py         # Run ramp rate, spacing, monotony/strain, cross-training
│   │   ├── ai_coach.py           # Gemini LLM coach & Peter Riegel race predictor
│   │   ├── coach_agent.py        # Conversational coach: tools, sessions, fact extraction
│   │   └── coach_memory.py       # ChromaDB long-term memory (Gemini embeddings)
│   ├── storage/                  # Local persistence (SQLite, stdlib only)
│   │   ├── activity_store.py     # Full Strava history + sync watermarks
│   │   └── chat_store.py         # Chat sessions, transcripts, SqliteMemory
│   └── api/                      # Web API Server
│       └── server.py             # FastAPI REST endpoints & static routes
├── web/                          # Frontend Single Page App
│   ├── index.html                # Dashboard + floating AI Coach chat drawer
│   ├── styles.css                # Custom glassmorphic design system
│   └── app.js                    # Chart.js charts, chat drawer & interaction logic
├── tests/                        # pytest suite (offline)
│   ├── conftest.py               # Shared synthetic-activity fixtures & tmp_path DB
│   ├── test_formatting.py        # Unit conversions & sport corrections
│   ├── test_metrics.py           # Relative effort, weekly rollups, ACWR
│   ├── test_sheets.py            # Date-range parsing, weekly totals, cell formatting
│   ├── test_caching.py           # Activity-cache and Garmin week-cache correctness
│   ├── test_strava_client.py     # Retry/429 handling with monkeypatched requests
│   ├── test_activity_store.py    # SQLite CRUD, upsert idempotency, filtering
│   ├── test_backfill.py          # Pagination termination, resume cursor, rate limits
│   ├── test_durability.py        # Ramp rate, spacing, long-run share, monotony/strain
│   ├── test_predictions.py       # Riegel exponents, PB vs training projection modes
│   ├── test_chat_store.py        # Session persistence, TTL purge, keyword recall
│   ├── test_coach_agent.py       # Tool functions & chat loop against a fake client
│   └── test_coach_memory.py      # Memory interface with a fake embedding function
├── main.py                       # Root CLI entry point (incl. --backfill)
├── server.py                     # Root Web server entry point
├── pyproject.toml                # Project configuration (deps, pytest)
├── README.md                     # Documentation
├── .env.example                  # Documented configuration template
├── .training_history.db          # SQLite history + chat store (gitignored)
├── .coach_memory/                # ChromaDB memory store (gitignored)
└── .env
```

---

## 🔑 Key Backend Components

1. **[`src/config.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/config.py)**
   - Centralizes project constants, sheet names, token paths, and `.env` loading.
   - All tunables are env-overridable through `_env_int` / `_env_float` / `_env_list`
     helpers that fall back to the default on malformed input. Anything a reviewer
     would call a magic number (HR max/rest, the swim divisor, the indoor-bike
     speed, ACWR window sizes, cache TTLs, Gemini model names) lives here, not
     inline at the call site. See [`.env.example`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/.env.example)
     for the documented list.
   - Owns the local-state paths too: `HISTORY_DB_FILE` (`.training_history.db`, the
     activity **and** chat store) and `COACH_MEMORY_DIR` (`.coach_memory/`). Both are
     gitignored; nothing outside `config.py` should construct these paths.
   - `SERVER_HOST` defaults to `127.0.0.1` and `ALLOWED_ORIGINS` to localhost only:
     the API is unauthenticated, so it must not be exposed to the LAN by default.
   - `STRAVA_REDIRECT_PORT` (8123) is deliberately distinct from `SERVER_PORT`
     (8000) — the OAuth callback listener blocks its thread, so sharing the
     dashboard's port deadlocks the flow.

2. **[`src/formatting.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/formatting.py)**
   - Single home for duration and pace formatting, in both English (`format_duration`,
     `format_pace`) and Greek (`format_duration_el`, `format_duration_short_el`,
     `format_pace(..., greek=True)`). Do not reintroduce local copies in `main.py`
     or `sheets.py`.
   - `corrected_distance_and_speed(act)` is the **only** place sport corrections
     are applied: the swim divisor and the indoor-trainer distance estimate. Every
     consumer (CLI, sheets, metrics, AI coach) routes through it so the numbers
     agree everywhere.
   - `is_indoor_ride(act)` treats a ride as indoor only when it is trainer-flagged
     *and* reports ~zero distance; a trainer that does report distance keeps it.

3. **[`src/integrations/strava.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/integrations/strava.py)**
   - Manages Strava OAuth2 browser authorization, token caching in `token.json`, and automatic token refreshes.
   - Fetches activity summaries (`fetch_activities`) and detailed metrics (`fetch_activity_detail`).
   - Raises `StravaAuthRequired` and `StravaRateLimitError` rather than returning
     `{}` — a swallowed 429 previously wrote blank cells over real sheet data.
   - `fetch_details_for_activities` throttles by `STRAVA_DETAIL_DELAY_SEC` and
     retries 429s up to `STRAVA_MAX_RETRIES`, honouring `Retry-After`.

4. **[`src/integrations/strava_backfill.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/integrations/strava_backfill.py)**
   - `backfill_all` pages `fetch_activities` until a short/empty page, writing each
     page into the SQLite store before requesting the next, and persisting the page
     cursor to `sync_state` so an interrupted import resumes instead of restarting.
   - Reuses `strava.py`'s `Retry-After` handling rather than reimplementing
     throttling; `STRAVA_BACKFILL_PAGE_DELAY_SEC` adds a pause on top of it and
     `STRAVA_BACKFILL_MAX_PAGES` is a runaway guard, not a real limit.
   - `sync_recent` is the cheap incremental path used on normal dashboard loads.

5. **[`src/integrations/garmin.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/integrations/garmin.py)**
   - Connects to Garmin Connect using credentials from `.env` (`GARMIN_EMAIL`, `GARMIN_PASSWORD`).
   - Caches session tokens in `.garmin_tokens/` to prevent repeated logins.
   - Queries daily sleep seconds, resting heart rate (RHR), and overnight HRV for any week range.
   - `get_weekly_health_summaries` batches many weeks behind **one** login and
     persists results to `.garmin_cache.json`. Finished weeks are cached
     indefinitely; the in-progress week honours `GARMIN_CACHE_TTL`. Prefer it over
     per-week calls — the dashboard used to issue ~210 sequential requests.

6. **[`src/integrations/sheets.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/integrations/sheets.py)**
   - Authenticates via desktop OAuth2 (`credentials.json` -> `gsheets_token.json`).
   - Dynamic layout detection: batch inspects $A(R+4)$ for `ΑΝΑΤΡΟΦΟΔΟΤΗΣΗ` to distinguish **New Block Layout** from **Old Single-Row Layout**.
   - Formats swimming pace in `/100μ` (distance corrected via `src.formatting`), running pace in `/χλμ`, cycling in `χλμ/ω`.
   - Populates weekly sport totals and Garmin health tracker in Column A (Old) or Column B of `ΕΒΔΟΜΑΔΑ` (New).
   - Detail lookups accept both int and string activity ids, since cached details
     round-trip through JSON.

7. **[`src/analytics/metrics.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/analytics/metrics.py)**
   - Extracts Strava Relative Effort (`suffer_score`) or computes HR-based TRIMP stress from `HR_MAX`/`HR_REST`.
   - Computes **ACWR** as the current week's load over the mean of the
     `ACWR_CHRONIC_WEEKS` weeks *preceding* it. The current week is excluded from
     its own baseline; with fewer than `ACWR_MIN_CHRONIC_WEEKS` of history, or a
     zero baseline, `acwr_ratio` is `None` and `zone` is `"unknown"`.
   - Emits machine-readable `zone` strings (`low`/`optimal`/`overreaching`/`spike`/
     `unknown`) — no colours or UI text. Presentation belongs in `web/app.js`.
   - `build_progression_history(weeks, acwr_map=...)` accepts a precomputed ACWR
     map so callers don't recompute it per request.
   - `calculate_relative_effort_by_sport` splits effort per sport group, and
     `process_activities_into_weeks` stores `run_/bike_/swim_/strength_relative_effort`
     next to the per-sport volumes. Per-sport ACWR is the *same* `calculate_acwr`
     with `effort_key="run_relative_effort"` — a parameter, not a second function,
     so the exclude-the-current-week invariant can only be implemented once.

8. **[`src/analytics/durability.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/analytics/durability.py)**
   - The run-injury engine, and entirely deterministic: `run_ramp_rate` (week-over-week
     run km vs `RUN_RAMP_SAFE_PCT`), `run_spacing_profile` (consecutive run days,
     longest gap), `long_run_share`, `training_monotony_and_strain` (Foster monotony
     = mean daily load / SD, strain = weekly load × monotony), and
     `assess_run_durability` which folds them into
     `{risk_level, signals: [{key, value, threshold, severity}], limiters}`.
   - `sport_strength_profile` ranks the disciplines from the `ATHLETE_PB_*` constants;
     `suggest_cross_training` converts a target run stimulus into bike / aqua-jog
     minutes via `BIKE_RUN_LOAD_FACTOR` / `AQUA_JOG_LOAD_FACTOR` plus a single-leg
     strength prescription.
   - The LLM coach *explains* these numbers; it never computes them. Keep the maths
     here so it is unit-testable offline and identical between the panel and the chat.
   - Same presentation invariant as `metrics.py`: machine-readable `severity`,
     `risk_level`, and `key` strings only — no colours, prose, or emoji.

9. **[`src/analytics/ai_coach.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/analytics/ai_coach.py)**
   - Generates qualitative coach feedback and readiness scoring, trying each entry of `GEMINI_MODELS` in order, with sports-science heuristic fallbacks.
   - Computes Peter Riegel race finish time predictions for 5K, 10K, 21.1K, and 42.2K,
     in two modes: `pb` calibrates from the `ATHLETE_PB_*` race results, `training`
     from recent training paces. The Riegel exponent is a distance-dependent ladder
     (1.07 / 1.10 / 1.13 / 1.145), not the textbook single 1.06 — a flat exponent
     under-predicted the longer distances against this athlete's verified results.
   - Model IDs are a config-driven fallback chain because Google retires them on a
     rolling basis. A 404 like `models/... is no longer available` is fixed by setting
     `GEMINI_MODELS` in `.env`, not by editing code.

10. **[`src/analytics/coach_agent.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/analytics/coach_agent.py)**
    - The conversational coach behind `POST /api/ai/chat`, built on plain
      `google-genai` `client.chats` with `automatic_function_calling` — deliberately
      **not** Google ADK, whose session service and dev UI duplicate what the
      dashboard already provides.
    - `build_tools` exposes nine callables whose schemas the SDK derives from their
      signatures and docstrings: `get_week_summary`, `get_activities`,
      `get_training_load`, `get_run_durability`, `get_race_projections`,
      `get_health_metrics`, `search_web`, `find_exercise_videos`, `remember_fact`.
      Every numeric tool routes through `formatting.corrected_distance_and_speed`,
      so the chat can never quote a number the dashboard and sheet disagree with.
    - `_grounded_search` issues a **separate**, search-only `generate_content` call
      rather than mixing `google_search` with `function_declarations` in one request.
      That mix is expressible in the SDK but rejected by the API, so this is the
      intended design, not an oversimplification to be "cleaned up".
    - `build_system_instruction` assembles the athlete profile, the live durability
      assessment, and recalled memories. `needs_medical_disclaimer` gates a fixed
      disclaimer that the *endpoint* appends: injury scope is enforced in the prompt
      **and** server-side, so it does not depend on the model complying.
    - `chat` walks `COACH_CHAT_MODELS` in order and, on total failure, raises
      `CoachUnavailable` listing *every* model's error — when Google retires an ID the
      whole chain 404s, and only the full list makes that diagnosable from the drawer.
    - `extract_session_facts` is the end-of-conversation pass; `list_remembered_facts`
      and `forget_remembered_fact` back the drawer's 🧠 inspector.

11. **[`src/analytics/coach_memory.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/analytics/coach_memory.py)**
    - `get_memory()` returns whichever backend `COACH_MEMORY_BACKEND` selects:
      `ChromaMemory` here (semantic recall, one embedding request per write and per
      turn) or `SqliteMemory` from `storage/chat_store.py` (keyword overlap, free and
      the default). Both implement the same four methods, so swapping needs no
      caller change and tests can substitute a stub.
    - `GeminiEmbeddingFunction` is used instead of Chroma's default, which would pull
      onnxruntime and download model weights on first use. Its `get_config()`
      round-trips the **model name only** — the API key must never be persisted into
      `.coach_memory/`.
    - Stores durable facts and conversation summaries, never activities: numeric
      lookups belong in typed tool calls, not vector search.
    - `_pad_with_critical_facts` tops up a short recall with injuries and constraints
      the query didn't happen to match, so a safety-relevant fact isn't crowded out.

12. **[`src/storage/activity_store.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/storage/activity_store.py)**
    - SQLite at `HISTORY_DB_FILE`, stdlib only. The full Strava summary is stored as
      a JSON `payload` column rather than a column per field: that stays
      forward-compatible with Strava's schema changes and avoids a migration each
      time the app reads a new key. Only `start_date_local` and `sport_type` are
      indexed, because they are the only columns ever filtered on.
    - `init_db` is idempotent (`CREATE TABLE IF NOT EXISTS`); `upsert_activities` /
      `upsert_details` use `ON CONFLICT(id) DO UPDATE`, so re-running a backfill is
      safe. `get_sync_state` / `set_sync_state` hold the watermarks and backfill cursor.

13. **[`src/storage/chat_store.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/storage/chat_store.py)**
    - Chat sessions and transcripts in the same SQLite file. History is on disk, not
      in process memory, because `server.py` runs uvicorn with `reload=True` — any
      code edit would otherwise wipe a live conversation mid-thread.
    - `purge_expired_sessions` enforces `COACH_SESSION_TTL` measured from the last
      message, not the first. `get_messages` returns the newest
      `COACH_MAX_HISTORY_MESSAGES` for replay; older turns stay stored, they just
      leave the prompt.
    - `SqliteMemory` is the default fact store, recalling by keyword overlap via
      `_words`.

14. **[`src/api/server.py`](file:///Users/anastasios.stamoulak/Documents/strava_to_google_sheet/src/api/server.py)**
    - FastAPI backend. Thirteen routes: `GET /api/health`, `GET /api/activities`,
      `GET /api/dashboard`, `GET /api/durability`, `POST /api/ai/coach`,
      `POST /api/ai/chat`, `GET /api/coach/memory`,
      `DELETE /api/coach/memory/{fact_id}`, `POST /api/coach/memory/extract`,
      `GET /api/history/status`, `POST /api/history/backfill`,
      `POST /api/sheet/sync`, and `GET /` for the SPA.
    - Reads summaries from the SQLite store first and falls back to Strava for the
      freshness window. The `.activities_cache.json` hot cache stays in front of it —
      it protects both the DB and Strava from every dashboard poll — gated by
      `_cache_satisfies`, which checks the TTL **and** that the cache holds enough
      activities for the requested `count` (a 20-activity cache must not answer a 50
      request).
    - Mounted by `server.py` at the repo root, which only calls `uvicorn.run` with
      `SERVER_HOST`/`SERVER_PORT`.

---

## 🔒 Invariants Worth Not Breaking

- **The API is unauthenticated by design.** `SERVER_HOST` stays `127.0.0.1` and
  `ALLOWED_ORIGINS` stays localhost-only with credentials off — a wildcard origin
  would let any page the browser visits read this athlete's training and biometric
  data. Do not expose it to the LAN.
- **Secrets stay unread and ungitted:** `.env`, `token.json`, `credentials.json`,
  `gsheets_token.json`, `.garmin_tokens/`. Never echo their contents into logs,
  transcripts, or the memory store.
- **Injury scope is enforced twice.** Pain and injury questions get general,
  cited, non-diagnostic guidance plus a physio/sports-doctor referral. The prompt
  asks for it; the endpoint appends the disclaimer regardless. Removing either half
  makes the guarantee depend on the model complying.
- **One source of truth for numbers.** All distance/speed corrections go through
  `formatting.corrected_distance_and_speed`; all tunables live in `config.py`. A
  literal at a call site is a bug, not a shortcut.
- **Analytics return data, not presentation.** `zone`, `severity`, `risk_level` —
  no colours, prose, or emoji outside `web/`.

---

## 🛠️ Development & Execution Commands

```bash
# Install dependencies (including the dev group)
uv sync

# Run CLI tool
uv run python main.py --count 30

# Sync to Google Sheets via CLI
uv run python main.py --sheet --count 30

# Import the FULL Strava history into the local SQLite store, then exit.
# Resumable and idempotent; --no-resume restarts from page 1.
uv run python main.py --backfill
uv run python main.py --backfill --no-resume

# Run Web Dashboard Server
uv run python server.py

# Run the test suite
uv run pytest
uv run pytest tests/test_metrics.py -k acwr -v

# Type / compile check
uv run python -m py_compile main.py server.py src/config.py src/formatting.py src/integrations/*.py src/analytics/*.py src/storage/*.py src/api/*.py

# Frontend syntax gate (no bundler, no test runner for the SPA)
node --check web/app.js
```

Note: bare `python` is not on `PATH` in this environment — use `uv run python` or `python3`.

The dev server binds a port, which some sandboxes disallow; to exercise an endpoint
without serving HTTP, call it in-process with `PYTHONPATH=. uv run python -c "..."`.

Any change to `web/styles.css` or `web/app.js` must bump the `?v=` query strings on
both `<link>` and `<script>` in `web/index.html`. The repo has a commit that exists
solely because stale assets were served.

---

## 🧪 Testing Conventions

- Tests live in `tests/` and import the project as `src.*`; `pyproject.toml` sets
  `pythonpath = ["."]` so the uninstalled package resolves from the repo root.
  Shared synthetic-activity fixtures and the `tmp_path` database live in
  `tests/conftest.py`.
- Everything under test is pure: formatting, corrections, weekly rollups, ACWR,
  durability signals, projections, date parsing, the cache-freshness predicates, and
  the SQLite stores against a temporary file. No test performs network I/O
  or touches Strava, Garmin, Google, or Gemini.
- Anything that would call out is faked at the boundary: `monkeypatch` over
  `requests` for the Strava client and backfill, a fake `client.chats` for the coach
  agent, and a fake embedding function for memory. Add to the fake rather than
  reaching for a real client.
- When changing a tunable's default in `config.py`, prefer asserting against the
  imported constant (as the existing tests do) rather than hardcoding the number,
  so the suite tracks configuration instead of duplicating it. This applies to model
  IDs too — `COACH_CHAT_MODELS[0]`, not `"gemini-3.6-flash"`, since Google retires
  them on a rolling basis.

