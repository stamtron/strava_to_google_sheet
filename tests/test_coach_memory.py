"""
The vector-backed coach memory, with embeddings faked.

The point of these tests is interchangeability: `ChromaMemory` must be
substitutable for `SqliteMemory` in `coach_agent.chat` without the agent
noticing, so the assertions mirror `test_chat_store.py`'s memory section — same
entry shape, same newest-first ordering, same injury/constraint padding.

No network and no model weights. Embeddings come from a deterministic stub and
the Chroma client is ephemeral, which is what keeps this honest: a real embedding
call would be a network request, and Chroma's own default would download
all-MiniLM on first use.
"""

import pytest

chromadb = pytest.importorskip("chromadb")

from src.analytics.coach_memory import (  # noqa: E402
    ChromaMemory,
    GeminiEmbeddingFunction,
    MemoryUnavailable,
    _pad_with_critical_facts,
)
from src.storage.chat_store import FACT_CATEGORIES  # noqa: E402


class StubEmbeddings(chromadb.api.types.EmbeddingFunction):
    """
    A word-bag embedding: deterministic, offline, and just directional enough.

    Each text becomes a fixed-length vector counting how often its words hash to
    each slot. Two texts sharing vocabulary end up closer than two that don't,
    which is all `recall` ordering needs. `embed_query` is left inherited so the
    query and document spaces match.
    """

    DIMS = 32

    def __init__(self):
        self.calls = []

    @staticmethod
    def name() -> str:
        return "stub_word_bag"

    def get_config(self) -> dict:
        return {}

    @staticmethod
    def build_from_config(config: dict) -> "StubEmbeddings":
        return StubEmbeddings()

    def __call__(self, input):
        texts = [input] if isinstance(input, str) else list(input)
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            vector = [0.0] * self.DIMS
            for word in "".join(
                c.lower() if c.isalnum() else " " for c in text
            ).split():
                vector[sum(ord(c) for c in word) % self.DIMS] += 1.0
            # A non-zero vector everywhere: an all-zero embedding has no defined
            # cosine distance, and an empty document would produce one.
            vector[0] += 0.01
            vectors.append(vector)
        return vectors


@pytest.fixture
def embeddings():
    return StubEmbeddings()


@pytest.fixture
def memory(embeddings, request):
    """A ChromaMemory over an ephemeral client, isolated per test."""
    client = chromadb.EphemeralClient(
        settings=chromadb.config.Settings(anonymized_telemetry=False, allow_reset=True)
    )
    return ChromaMemory(
        client=client,
        embedding_function=embeddings,
        collection_name=f"test_{abs(hash(request.node.name)) % 10**8}",
    )


# --------------------------------------------------------------------------- #
# Interface parity with SqliteMemory
# --------------------------------------------------------------------------- #


def test_it_exposes_the_same_four_methods_the_agent_calls():
    """`coach_agent.chat` accepts whichever memory it is handed; the shape is the contract."""
    for method in ("remember", "recall", "forget", "all_facts"):
        assert callable(getattr(ChromaMemory, method, None))


def test_remember_returns_the_stored_entry_with_an_id(memory):
    entry = memory.remember("Swims on Tuesday mornings.", category="preference")

    assert entry["id"]
    assert entry["fact"] == "Swims on Tuesday mornings."
    assert entry["category"] == "preference"
    assert entry["source"] == "explicit"
    assert entry["created_at"] > 0


def test_remember_rejects_an_empty_fact(memory):
    with pytest.raises(ValueError):
        memory.remember("   ")


def test_an_unknown_category_is_coerced_rather_than_rejected(memory):
    entry = memory.remember("Likes hill repeats.", category="vibes")
    assert entry["category"] == "other"
    assert entry["category"] in FACT_CATEGORIES


def test_a_stored_fact_round_trips_through_chroma_unchanged(memory):
    stored = memory.remember(
        "Cannot train before 07:00 on weekdays.",
        category="constraint",
        session_id="s1",
        source="auto",
    )
    (loaded,) = memory.all_facts()

    assert loaded["id"] == stored["id"]
    assert loaded["fact"] == stored["fact"]
    assert loaded["category"] == "constraint"
    assert loaded["session_id"] == "s1"
    assert loaded["source"] == "auto"
    assert loaded["created_at"] == pytest.approx(stored["created_at"])


def test_an_unattributed_fact_reads_back_with_a_null_session(memory):
    """Chroma rejects None metadata, so the empty string has to decode back to None."""
    memory.remember("Races in Greece.", category="goal")
    assert memory.all_facts()[0]["session_id"] is None


def test_all_facts_is_newest_first(memory):
    first = memory.remember("First.")
    second = memory.remember("Second.")
    third = memory.remember("Third.")

    ids = [f["id"] for f in memory.all_facts()]
    assert ids == [third["id"], second["id"], first["id"]]


def test_all_facts_is_empty_on_a_fresh_memory(memory):
    assert memory.all_facts() == []


def test_forget_removes_a_fact_and_reports_unknown_ids(memory):
    entry = memory.remember("Owns a Canyon gravel bike.", category="equipment")

    assert memory.forget(entry["id"]) is True
    assert memory.all_facts() == []
    assert memory.forget(entry["id"]) is False


def test_forget_leaves_the_other_facts_alone(memory):
    keep = memory.remember("Keep this one.")
    drop = memory.remember("Drop this one.")

    assert memory.forget(drop["id"]) is True
    assert [f["id"] for f in memory.all_facts()] == [keep["id"]]


# --------------------------------------------------------------------------- #
# Recall
# --------------------------------------------------------------------------- #


def test_recall_returns_the_semantically_closest_fact_first(memory):
    memory.remember("Owns a Canyon gravel bike.", category="equipment")
    memory.remember("Swims in a 25 metre pool on Tuesdays.", category="preference")

    hits = memory.recall("which pool does the swimming happen in", k=1)
    assert len(hits) == 1
    assert "pool" in hits[0]["fact"]


def test_recall_reports_the_distance_it_ranked_by(memory):
    """The endpoint surfaces it; a hit with no score can't be judged by the athlete."""
    memory.remember("Prefers flat routes.", category="preference")
    (hit,) = memory.recall("flat routes", k=1)
    assert isinstance(hit["distance"], float)


def test_recall_respects_k(memory):
    for i in range(6):
        memory.remember(f"Injury note number {i}.", category="injury")
    assert len(memory.recall("injury note", k=2)) == 2


def test_recall_on_an_empty_memory_returns_nothing(memory):
    assert memory.recall("anything at all", k=5) == []


def test_recall_asking_for_nothing_returns_nothing(memory):
    memory.remember("Prefers flat routes.")
    assert memory.recall("flat", k=0) == []


def test_a_blank_query_falls_back_to_recent_facts(memory):
    memory.remember("Older fact about shoes.")
    newest = memory.remember("Newest fact about the bike.")

    hits = memory.recall("   ", k=1)
    assert hits[0]["id"] == newest["id"]


def test_k_above_the_collection_size_is_clamped_not_an_error(memory):
    memory.remember("Only fact.")
    assert len(memory.recall("fact", k=50)) == 1


def test_injuries_ride_along_in_recall_even_without_a_semantic_match(memory):
    """Advice given without knowing about an injury is the failure that matters."""
    memory.remember("Left knee ITBS flares above 40 km per week.", category="injury")
    memory.remember("Cannot train before 07:00 on weekdays.", category="constraint")
    memory.remember("Prefers flat routes.", category="preference")

    hits = memory.recall("recommend a wetsuit for open water", k=5)
    categories = {h["category"] for h in hits}
    assert "injury" in categories
    assert "constraint" in categories


def test_a_failing_query_degrades_to_recent_facts_rather_than_raising(memory, monkeypatch):
    """A degraded recall costs context; a raised exception costs the whole answer."""
    memory.remember("Left knee ITBS above 40 km per week.", category="injury")
    monkeypatch.setattr(
        memory.collection,
        "query",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("index corrupt")),
    )

    hits = memory.recall("knee", k=3)
    assert [h["fact"] for h in hits] == ["Left knee ITBS above 40 km per week."]


def test_padding_never_duplicates_a_fact_already_recalled():
    injury = {"id": "a", "category": "injury", "fact": "knee"}
    other = {"id": "b", "category": "preference", "fact": "flat"}
    padded = _pad_with_critical_facts([injury], [injury, other], k=5)
    assert [f["id"] for f in padded] == ["a"]


def test_padding_does_not_grow_a_full_result_set():
    hits = [{"id": str(i), "category": "preference"} for i in range(3)]
    extra = [{"id": "x", "category": "injury"}]
    assert _pad_with_critical_facts(hits, hits + extra, k=3) == hits


# --------------------------------------------------------------------------- #
# Embeddings
# --------------------------------------------------------------------------- #


def test_writes_and_reads_go_through_the_embedding_function(memory, embeddings):
    memory.remember("Owns a Canyon gravel bike.")
    memory.recall("bike", k=1)
    assert any("Owns a Canyon gravel bike." in call for call in embeddings.calls)


def test_all_facts_needs_no_embedding_call(memory, embeddings):
    """Listing is a metadata read; charging an embedding request for it would be waste."""
    memory.remember("Prefers flat routes.")
    before = len(embeddings.calls)
    memory.all_facts()
    assert len(embeddings.calls) == before


def test_the_gemini_embedding_function_is_not_legacy_to_chroma():
    """Chroma warns on every call for a function missing these three."""
    fn = GeminiEmbeddingFunction(client=object())
    assert fn.name() == "gemini_coach_memory"
    assert fn.get_config() == {"model": fn._model}
    assert GeminiEmbeddingFunction.build_from_config({"model": "m"})._model == "m"


def test_documents_and_queries_use_different_task_types():
    """Gemini embeds asymmetrically: a fact and a question about it must match."""
    seen = []

    class RecordingClient:
        class models:
            @staticmethod
            def embed_content(model, contents, config):
                seen.append(config["task_type"])

                class _R:
                    embeddings = [type("E", (), {"values": [0.1, 0.2]})() for _ in contents]

                return _R()

    fn = GeminiEmbeddingFunction(client=RecordingClient())
    # Chroma wraps `__call__` to normalize the result into numpy, hence the cast
    # back before comparing.
    assert [list(v) for v in fn(["a fact"])] == [pytest.approx([0.1, 0.2])]
    fn.embed_query(["a question"])
    assert seen == ["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]


def test_embedding_nothing_never_calls_the_api():
    """Chroma rejects an empty result before `__call__` returns, so `_embed` is the seam."""

    class ExplodingClient:
        class models:
            @staticmethod
            def embed_content(**kwargs):
                raise AssertionError("should not be called")

    fn = GeminiEmbeddingFunction(client=ExplodingClient())
    assert fn._embed([], "RETRIEVAL_DOCUMENT") == []
    assert fn.embed_query([]) == []


def test_a_missing_api_key_is_reported_as_unavailable(monkeypatch):
    monkeypatch.setattr("src.analytics.coach_memory.GEMINI_API_KEY", "")
    with pytest.raises(MemoryUnavailable):
        GeminiEmbeddingFunction()(["anything"])


def test_a_broken_client_surfaces_as_memory_unavailable(embeddings):
    class BrokenClient:
        def get_or_create_collection(self, **kwargs):
            raise RuntimeError("cannot open the database")

    with pytest.raises(MemoryUnavailable):
        ChromaMemory(client=BrokenClient(), embedding_function=embeddings)


# --------------------------------------------------------------------------- #
# Backend selection
# --------------------------------------------------------------------------- #


def test_the_default_backend_is_the_keyword_memory(conn, monkeypatch):
    from src.analytics import coach_agent
    from src.storage.chat_store import SqliteMemory

    monkeypatch.setattr(coach_agent, "COACH_MEMORY_BACKEND", "sqlite")
    assert isinstance(coach_agent._default_memory(conn), SqliteMemory)


def test_a_failing_vector_backend_falls_back_instead_of_failing_the_turn(conn, monkeypatch):
    from src.analytics import coach_agent
    from src.storage.chat_store import SqliteMemory

    monkeypatch.setattr(coach_agent, "COACH_MEMORY_BACKEND", "chroma")
    monkeypatch.setattr(
        "src.analytics.coach_memory.ChromaMemory.__init__",
        lambda self, **kwargs: (_ for _ in ()).throw(MemoryUnavailable("no key")),
    )
    assert isinstance(coach_agent._default_memory(conn), SqliteMemory)


def test_the_memory_endpoints_read_and_prune_the_configured_store(memory):
    from src.analytics.coach_agent import forget_remembered_fact, list_remembered_facts

    entry = memory.remember("Left knee ITBS above 40 km/week.", category="injury")

    listed = list_remembered_facts(memory=memory)
    assert [f["id"] for f in listed] == [entry["id"]]
    assert forget_remembered_fact(entry["id"], memory=memory) is True
    assert forget_remembered_fact(entry["id"], memory=memory) is False
    assert list_remembered_facts(memory=memory) == []
