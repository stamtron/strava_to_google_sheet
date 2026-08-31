"""Chat persistence and the keyword-overlap fact memory. No network, no Gemini."""

import pytest

from src.storage.chat_store import (
    FACT_CATEGORIES,
    SqliteMemory,
    append_message,
    delete_session,
    get_messages,
    init_chat_tables,
    new_session_id,
    purge_expired_sessions,
)


@pytest.fixture
def chat_conn(conn):
    """The shared history DB with the chat tables added, as the API does."""
    return init_chat_tables(conn)


def test_chat_tables_are_created_alongside_the_activity_tables(chat_conn):
    names = {
        row[0]
        for row in chat_conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"activities", "chat_messages", "coach_facts"} <= names


def test_init_chat_tables_is_idempotent(chat_conn):
    session = new_session_id()
    append_message(chat_conn, session, "user", "hello")
    init_chat_tables(chat_conn)
    assert len(get_messages(chat_conn, session)) == 1


def test_session_ids_are_unique():
    assert len({new_session_id() for _ in range(50)}) == 50


def test_messages_are_returned_oldest_first_with_increasing_turns(chat_conn):
    session = new_session_id()
    assert append_message(chat_conn, session, "user", "first") == 0
    assert append_message(chat_conn, session, "model", "second") == 1
    assert append_message(chat_conn, session, "user", "third") == 2

    messages = get_messages(chat_conn, session)
    assert [m["content"] for m in messages] == ["first", "second", "third"]
    assert [m["turn"] for m in messages] == [0, 1, 2]
    assert [m["role"] for m in messages] == ["user", "model", "user"]


def test_turn_numbering_is_independent_per_session(chat_conn):
    a, b = new_session_id(), new_session_id()
    append_message(chat_conn, a, "user", "a0")
    assert append_message(chat_conn, b, "user", "b0") == 0
    assert get_messages(chat_conn, a) != get_messages(chat_conn, b)


def test_the_history_window_keeps_the_most_recent_messages(chat_conn):
    session = new_session_id()
    for i in range(10):
        append_message(chat_conn, session, "user", f"m{i}")

    windowed = get_messages(chat_conn, session, max_messages=3)
    # Still oldest-first, but only the tail: the last turns are what the next
    # reply depends on.
    assert [m["content"] for m in windowed] == ["m7", "m8", "m9"]


def test_an_unknown_session_has_no_messages(chat_conn):
    assert get_messages(chat_conn, "never-used") == []


def test_delete_session_removes_only_that_conversation(chat_conn):
    a, b = new_session_id(), new_session_id()
    append_message(chat_conn, a, "user", "a0")
    append_message(chat_conn, a, "model", "a1")
    append_message(chat_conn, b, "user", "b0")

    assert delete_session(chat_conn, a) == 2
    assert get_messages(chat_conn, a) == []
    assert len(get_messages(chat_conn, b)) == 1


def test_purge_expires_idle_sessions_and_spares_active_ones(chat_conn):
    stale, fresh = new_session_id(), new_session_id()
    append_message(chat_conn, stale, "user", "old")
    append_message(chat_conn, fresh, "user", "new")
    chat_conn.execute(
        "UPDATE chat_messages SET created_at = 1000.0 WHERE session_id = ?", (stale,)
    )
    chat_conn.execute(
        "UPDATE chat_messages SET created_at = 9000.0 WHERE session_id = ?", (fresh,)
    )
    chat_conn.commit()

    assert purge_expired_sessions(chat_conn, ttl_seconds=100, now=9000.0) == 1
    assert get_messages(chat_conn, stale) == []
    assert len(get_messages(chat_conn, fresh)) == 1


def test_purge_judges_a_session_by_its_newest_message(chat_conn):
    """A long conversation must never be truncated from the front while in use."""
    session = new_session_id()
    append_message(chat_conn, session, "user", "opening")
    append_message(chat_conn, session, "model", "reply")
    chat_conn.execute("UPDATE chat_messages SET created_at = 10.0 WHERE turn = 0")
    chat_conn.execute("UPDATE chat_messages SET created_at = 9000.0 WHERE turn = 1")
    chat_conn.commit()

    assert purge_expired_sessions(chat_conn, ttl_seconds=100, now=9000.0) == 0
    assert len(get_messages(chat_conn, session)) == 2


def test_a_zero_ttl_disables_expiry(chat_conn):
    session = new_session_id()
    append_message(chat_conn, session, "user", "keep me")
    assert purge_expired_sessions(chat_conn, ttl_seconds=0, now=1e12) == 0
    assert len(get_messages(chat_conn, session)) == 1


# --------------------------------------------------------------------------- #
# SqliteMemory
# --------------------------------------------------------------------------- #


def test_remember_returns_the_stored_entry_with_an_id(conn):
    memory = SqliteMemory(conn)
    entry = memory.remember("Swims on Tuesday mornings.", category="preference")

    assert entry["id"]
    assert entry["fact"] == "Swims on Tuesday mornings."
    assert entry["category"] == "preference"
    assert memory.all_facts() == [entry]


def test_remember_rejects_an_empty_fact(conn):
    memory = SqliteMemory(conn)
    with pytest.raises(ValueError):
        memory.remember("   ")


def test_an_unknown_category_is_coerced_rather_than_rejected(conn):
    memory = SqliteMemory(conn)
    entry = memory.remember("Likes hill repeats.", category="vibes")
    assert entry["category"] == "other"
    assert entry["category"] in FACT_CATEGORIES


def test_recall_ranks_by_keyword_overlap(conn):
    memory = SqliteMemory(conn)
    memory.remember("Owns a Canyon gravel bike.", category="equipment")
    memory.remember("Swims in a 25 metre pool on Tuesdays.", category="preference")

    hits = memory.recall("what pool does the swimming happen in", k=1)
    assert len(hits) == 1
    assert "pool" in hits[0]["fact"]


def test_recall_falls_back_to_recent_facts_when_the_query_has_no_terms(conn):
    memory = SqliteMemory(conn)
    memory.remember("Older fact about shoes.")
    newest = memory.remember("Newest fact about the bike.")

    hits = memory.recall("is it?", k=1)
    assert hits[0]["id"] == newest["id"]


def test_injuries_ride_along_in_recall_even_without_a_keyword_match(conn):
    """Advice given without knowing about an injury is the failure that matters."""
    memory = SqliteMemory(conn)
    memory.remember("Left knee ITBS flares above 40 km per week.", category="injury")
    memory.remember("Cannot train before 07:00 on weekdays.", category="constraint")
    memory.remember("Prefers flat routes.", category="preference")

    hits = memory.recall("recommend a wetsuit for open water", k=5)
    categories = {h["category"] for h in hits}
    assert "injury" in categories
    assert "constraint" in categories


def test_recall_respects_k(conn):
    memory = SqliteMemory(conn)
    for i in range(6):
        memory.remember(f"Injury note number {i}.", category="injury")
    assert len(memory.recall("injury note", k=2)) == 2


def test_forget_removes_a_fact_and_reports_unknown_ids(conn):
    memory = SqliteMemory(conn)
    entry = memory.remember("Races in Greece.", category="goal")

    assert memory.forget(entry["id"]) is True
    assert memory.all_facts() == []
    assert memory.forget(entry["id"]) is False


def test_all_facts_is_newest_first(conn):
    memory = SqliteMemory(conn)
    first = memory.remember("First.")
    second = memory.remember("Second.")
    conn.execute("UPDATE coach_facts SET created_at = 1.0 WHERE id = ?", (first["id"],))
    conn.execute("UPDATE coach_facts SET created_at = 2.0 WHERE id = ?", (second["id"],))
    conn.commit()

    assert [f["id"] for f in memory.all_facts()] == [second["id"], first["id"]]
