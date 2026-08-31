"""
SQLite-backed conversation state for the chat coach.

History is persisted rather than held in a process dictionary because the API
runs under uvicorn's reloader: any edit to a source file restarts the worker and
would silently drop every open conversation mid-sentence.

Lives in the same database file as the activity history so there is one file to
back up and one connection to open per request. The tables are created on demand
by `init_chat_tables`, which the activity store's `init_db` knows nothing about.

`SqliteMemory` is the durable-fact store behind the coach's `remember_fact`
tool. It is deliberately a small interface — `remember` / `recall` / `forget` /
`all_facts` — so a semantic (vector) implementation can replace it without the
agent noticing.
"""

import sqlite3
import time
import uuid

_CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
    session_id  TEXT NOT NULL,
    turn        INTEGER NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  REAL NOT NULL,
    PRIMARY KEY (session_id, turn)
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages (session_id, turn);

CREATE TABLE IF NOT EXISTS coach_facts (
    id          TEXT PRIMARY KEY,
    fact        TEXT NOT NULL,
    category    TEXT NOT NULL,
    session_id  TEXT,
    source      TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coach_facts_created
    ON coach_facts (created_at);
"""

# Categories the coach may file a durable fact under. Free-form strings would
# make the memory unqueryable within a few weeks of use.
FACT_CATEGORIES = ("injury", "preference", "goal", "equipment", "constraint", "other")


def init_chat_tables(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Create the chat tables if absent. Idempotent; safe on every request."""
    conn.executescript(_CHAT_SCHEMA)
    conn.commit()
    return conn


def new_session_id() -> str:
    return uuid.uuid4().hex


def append_message(conn: sqlite3.Connection, session_id: str, role: str, content: str) -> int:
    """
    Append one message and return its turn number.

    The turn number is derived from the stored maximum rather than kept in
    memory, so two requests racing on the same session can't collide on the
    primary key without one of them failing loudly.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(turn), -1) + 1 AS next FROM chat_messages WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    turn = row["next"] if isinstance(row, sqlite3.Row) else row[0]
    conn.execute(
        """
        INSERT INTO chat_messages (session_id, turn, role, content, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, turn, role, content, time.time()),
    )
    conn.commit()
    return turn


def get_messages(
    conn: sqlite3.Connection, session_id: str, max_messages: int | None = None
) -> list[dict]:
    """
    Return a session's messages oldest first.

    `max_messages` keeps the most *recent* ones: an old opening exchange matters
    far less to the next reply than the last few turns, and the window is what
    bounds the tokens sent on every request.
    """
    rows = conn.execute(
        "SELECT role, content, turn FROM chat_messages WHERE session_id = ? ORDER BY turn DESC",
        (session_id,),
    ).fetchall()
    if max_messages is not None:
        rows = rows[:max_messages]
    return [{"role": r["role"], "content": r["content"], "turn": r["turn"]} for r in reversed(rows)]


def delete_session(conn: sqlite3.Connection, session_id: str) -> int:
    """Drop one conversation. Returns the number of messages removed."""
    cur = conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
    conn.commit()
    return cur.rowcount


def purge_expired_sessions(conn: sqlite3.Connection, ttl_seconds: float, now: float | None = None) -> int:
    """
    Delete sessions whose last message is older than `ttl_seconds`.

    Expiry is judged on a session's newest message, not each message's own age,
    so a long-running conversation is never truncated from the front while it is
    still in use.
    """
    if ttl_seconds <= 0:
        return 0
    cutoff = (now if now is not None else time.time()) - ttl_seconds
    cur = conn.execute(
        """
        DELETE FROM chat_messages WHERE session_id IN (
            SELECT session_id FROM chat_messages
            GROUP BY session_id HAVING MAX(created_at) < ?
        )
        """,
        (cutoff,),
    )
    conn.commit()
    return cur.rowcount


class SqliteMemory:
    """
    Durable athlete facts in SQLite, recalled by keyword overlap.

    This is the baseline memory: no embeddings, no extra dependency, and no
    network. Recall scores a fact by how many of the query's words it contains,
    which is crude but honest — it never invents a match, and with a few dozen
    facts it retrieves the obvious ones. A vector-backed implementation of the
    same four methods can be dropped in when the fact count outgrows that.
    """

    # Words too common to carry any signal in a query about training.
    _STOPWORDS = frozenset(
        "a an and are as at be but by can do does for from had has have how i if in "
        "is it me my of on or should that the to was what when where which why will "
        "with you your".split()
    )

    def __init__(self, conn: sqlite3.Connection):
        self.conn = init_chat_tables(conn)

    def remember(
        self, fact: str, category: str = "other", session_id: str | None = None, source: str = "explicit"
    ) -> dict:
        """Store one durable fact and return it, including the id needed to delete it."""
        fact = (fact or "").strip()
        if not fact:
            raise ValueError("a fact cannot be empty")
        if category not in FACT_CATEGORIES:
            category = "other"

        entry = {
            "id": uuid.uuid4().hex,
            "fact": fact,
            "category": category,
            "session_id": session_id,
            "source": source,
            "created_at": time.time(),
        }
        self.conn.execute(
            """
            INSERT INTO coach_facts (id, fact, category, session_id, source, created_at)
            VALUES (:id, :fact, :category, :session_id, :source, :created_at)
            """,
            entry,
        )
        self.conn.commit()
        return entry

    def recall(self, query: str, k: int = 5) -> list[dict]:
        """Return up to `k` facts most relevant to `query`, best match first."""
        facts = self.all_facts()
        terms = {w for w in _words(query) if w not in self._STOPWORDS}
        if not terms:
            return facts[:k]

        scored = []
        for fact in facts:
            overlap = len(terms & set(_words(fact["fact"])))
            if overlap:
                scored.append((overlap, fact["created_at"], fact))
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)

        hits = [f for _, _, f in scored[:k]]
        # Injuries and hard constraints are the facts whose absence produces bad
        # advice, so they ride along even when the wording doesn't overlap.
        if len(hits) < k:
            ids = {f["id"] for f in hits}
            for fact in facts:
                if len(hits) >= k:
                    break
                if fact["id"] not in ids and fact["category"] in ("injury", "constraint"):
                    hits.append(fact)
        return hits

    def all_facts(self) -> list[dict]:
        """Every stored fact, newest first."""
        rows = self.conn.execute(
            "SELECT id, fact, category, session_id, source, created_at FROM coach_facts "
            "ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def forget(self, fact_id: str) -> bool:
        """Delete one fact. False if the id was not stored."""
        cur = self.conn.execute("DELETE FROM coach_facts WHERE id = ?", (fact_id,))
        self.conn.commit()
        return cur.rowcount > 0


def _words(text: str) -> list[str]:
    """Lowercase alphanumeric words, so punctuation never blocks a match."""
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in text or "")
    return [w for w in cleaned.split() if len(w) > 2]
