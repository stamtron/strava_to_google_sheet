"""
Vector-backed durable memory for the chat coach.

`ChromaMemory` is a drop-in alternative to `chat_store.SqliteMemory`: the same
four methods — `remember` / `recall` / `forget` / `all_facts` — so
`coach_agent.chat` accepts either without knowing which it got. The difference is
recall. SQLite matches words; this matches meaning, so "what should I wear on the
bike" can surface "owns a Canyon gravel bike" with no shared vocabulary.

What lives here is durable facts about the athlete, not activities. Activities
are numbers, and numbers belong in typed tool calls — embedding them would let
the model retrieve an approximately-right week, which is worse than no answer.

Embeddings come from Gemini rather than Chroma's default. Chroma's default
all-MiniLM downloads model weights on first use, which is a network fetch this
project's tests must never make, on top of an onnxruntime dependency it does not
otherwise need. `GeminiEmbeddingFunction` is a thin wrapper so tests can pass a
deterministic stand-in and never reach the network either.
"""

import time
import uuid

from chromadb.api.types import EmbeddingFunction

from src.config import (
    COACH_EMBEDDING_MODEL,
    COACH_MEMORY_COLLECTION,
    COACH_MEMORY_DIR,
    GEMINI_API_KEY,
)
from src.storage.chat_store import FACT_CATEGORIES

# Distinct task types on write and on read. Gemini's embedding models are
# trained asymmetrically: a stored fact and a question about it land closer
# together when each is embedded for its own role.
_DOCUMENT_TASK = "RETRIEVAL_DOCUMENT"
_QUERY_TASK = "RETRIEVAL_QUERY"

# Chroma metadata values must be str/int/float/bool — `None` is rejected outright,
# so an unattributed fact stores this instead of a null session id.
_NO_SESSION = ""


class MemoryUnavailable(RuntimeError):
    """Chroma or the embedding backend could not be initialized."""


class GeminiEmbeddingFunction(EmbeddingFunction):
    """
    Embeds text with Gemini, batching a whole call's worth of documents at once.

    Implements `name` / `get_config` / `build_from_config` because Chroma treats
    an embedding function lacking them as legacy and warns on every use. The
    config it round-trips is the model name only: the API key is read from the
    environment, never persisted into `.coach_memory/`.
    """

    def __init__(self, client=None, model: str = COACH_EMBEDDING_MODEL):
        self._client = client
        self._model = model

    @staticmethod
    def name() -> str:
        return "gemini_coach_memory"

    def get_config(self) -> dict:
        return {"model": self._model}

    @staticmethod
    def build_from_config(config: dict) -> "GeminiEmbeddingFunction":
        return GeminiEmbeddingFunction(model=config.get("model", COACH_EMBEDDING_MODEL))

    def _get_client(self):
        if self._client is None:
            if not GEMINI_API_KEY:
                raise MemoryUnavailable(
                    "GEMINI_API_KEY is not set, so the vector memory cannot embed facts."
                )
            from google import genai

            self._client = genai.Client(api_key=GEMINI_API_KEY)
        return self._client

    def _embed(self, input, task_type: str) -> list[list[float]]:
        texts = [input] if isinstance(input, str) else list(input)
        if not texts:
            return []
        response = self._get_client().models.embed_content(
            model=self._model,
            contents=texts,
            config={"task_type": task_type},
        )
        return [list(e.values) for e in (response.embeddings or [])]

    def __call__(self, input) -> list[list[float]]:
        return self._embed(input, _DOCUMENT_TASK)

    def embed_query(self, input) -> list[list[float]]:
        return self._embed(input, _QUERY_TASK)


class ChromaMemory:
    """
    Durable athlete facts in a Chroma collection, recalled by semantic similarity.

    Interchangeable with `SqliteMemory`. Two behaviours are copied from it
    deliberately, because the agent depends on them: `all_facts` is newest first,
    and `recall` pads its results with injuries and hard constraints even when
    they don't match the question — advice given without knowing about an injury
    is the failure that matters most.
    """

    def __init__(self, client=None, embedding_function=None, collection_name: str | None = None):
        self.embedding_function = embedding_function or GeminiEmbeddingFunction()
        name = collection_name or COACH_MEMORY_COLLECTION
        try:
            self.client = client if client is not None else _persistent_client()
            self.collection = self.client.get_or_create_collection(
                name=name,
                embedding_function=self.embedding_function,
            )
        except MemoryUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 - the caller degrades to SqliteMemory
            raise MemoryUnavailable(f"Chroma memory unavailable: {e}") from e

    # ----------------------------------------------------------------- writes

    def remember(
        self,
        fact: str,
        category: str = "other",
        session_id: str | None = None,
        source: str = "explicit",
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
        self.collection.add(
            ids=[entry["id"]],
            documents=[fact],
            metadatas=[
                {
                    "category": category,
                    "session_id": session_id or _NO_SESSION,
                    "source": source,
                    "created_at": entry["created_at"],
                }
            ],
        )
        return entry

    def forget(self, fact_id: str) -> bool:
        """Delete one fact. False if the id was not stored."""
        existing = self.collection.get(ids=[fact_id], include=[])
        if not (existing.get("ids") or []):
            return False
        self.collection.delete(ids=[fact_id])
        return True

    # ----------------------------------------------------------------- reads

    def all_facts(self) -> list[dict]:
        """Every stored fact, newest first."""
        result = self.collection.get(include=["documents", "metadatas"])
        facts = [
            _to_entry(fact_id, document, metadata)
            for fact_id, document, metadata in zip(
                result.get("ids") or [],
                result.get("documents") or [],
                result.get("metadatas") or [],
            )
        ]
        facts.sort(key=lambda f: f["created_at"], reverse=True)
        return facts

    def recall(self, query: str, k: int = 5) -> list[dict]:
        """Return up to `k` facts most relevant to `query`, best match first."""
        if k <= 0:
            return []
        query = (query or "").strip()
        if not query:
            return self.all_facts()[:k]

        try:
            # `n_results` above the collection size is an error in some Chroma
            # versions, so it is clamped rather than trusted.
            available = self.collection.count()
            if not available:
                return []
            result = self.collection.query(
                query_texts=[query],
                n_results=min(k, available),
                include=["documents", "metadatas", "distances"],
            )
        except Exception:  # noqa: BLE001 - a degraded recall beats a failed turn
            return self.all_facts()[:k]

        hits = [
            _to_entry(fact_id, document, metadata, distance)
            for fact_id, document, metadata, distance in zip(
                (result.get("ids") or [[]])[0],
                (result.get("documents") or [[]])[0],
                (result.get("metadatas") or [[]])[0],
                (result.get("distances") or [[None]])[0],
            )
        ]
        return _pad_with_critical_facts(hits, self.all_facts(), k)


def _persistent_client():
    """A Chroma client writing to `.coach_memory/`, telemetry off."""
    import chromadb
    from chromadb.config import Settings

    return chromadb.PersistentClient(
        path=COACH_MEMORY_DIR,
        settings=Settings(anonymized_telemetry=False, allow_reset=True),
    )


def _to_entry(fact_id: str, document: str, metadata, distance=None) -> dict:
    """Rebuild a `SqliteMemory`-shaped entry from a Chroma row."""
    metadata = metadata or {}
    session_id = metadata.get("session_id") or None
    entry = {
        "id": fact_id,
        "fact": document or "",
        "category": metadata.get("category", "other"),
        "session_id": session_id,
        "source": metadata.get("source", "explicit"),
        "created_at": float(metadata.get("created_at", 0.0) or 0.0),
    }
    if distance is not None:
        entry["distance"] = round(float(distance), 4)
    return entry


def _pad_with_critical_facts(hits: list[dict], facts: list[dict], k: int) -> list[dict]:
    """Top up a recall with injuries and constraints the query didn't happen to match."""
    if len(hits) >= k:
        return hits[:k]
    seen = {h["id"] for h in hits}
    padded = list(hits)
    for fact in facts:
        if len(padded) >= k:
            break
        if fact["id"] not in seen and fact["category"] in ("injury", "constraint"):
            padded.append(fact)
    return padded


def get_memory(conn=None):
    """
    Return the configured memory, falling back to SQLite if Chroma can't start.

    The fallback is silent-but-logged on purpose: a missing API key or an
    unwritable `.coach_memory/` should cost semantic recall, not the ability to
    hold a conversation.
    """
    from src.config import COACH_MEMORY_BACKEND
    from src.storage.chat_store import SqliteMemory

    if COACH_MEMORY_BACKEND == "chroma":
        try:
            return ChromaMemory()
        except MemoryUnavailable as e:
            print(f"⚠️  Falling back to keyword memory: {e}")
    if conn is None:
        return None
    return SqliteMemory(conn)
