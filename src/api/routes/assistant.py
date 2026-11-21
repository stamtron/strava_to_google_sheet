"""
AI endpoints: the one-shot weekly insights panel, the conversational data
assistant, and inspection of what the assistant remembers.

Paths keep their original `/api/ai/coach` and `/api/coach/...` names so the
frontend and any saved clients keep working.
"""

import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from src.analytics.ai_coach import generate_weekly_coaching_insights
from src.analytics.coach_agent import (
    CoachUnavailable,
    extract_session_facts,
    forget_remembered_fact,
    list_remembered_facts,
)
from src.analytics.coach_agent import chat as coach_chat
from src.api.deps import internal_error
from src.config import ATHLETE_NOTES_MAX_CHARS, CHAT_MESSAGE_MAX_CHARS, CHAT_WEEK_CONTEXT_MAX_CHARS

logger = logging.getLogger(__name__)
router = APIRouter()


class FeedbackRequest(BaseModel):
    week_summary: dict
    garmin_health: dict | None = None
    athlete_notes: str = Field("", max_length=ATHLETE_NOTES_MAX_CHARS)


class ChatRequest(BaseModel):
    """One conversational turn. `session_id` is minted server-side on the first."""

    message: str = Field(..., max_length=CHAT_MESSAGE_MAX_CHARS)
    session_id: str | None = None
    # The week the athlete has open on the dashboard, so "how was this week?"
    # resolves without them naming a date. Context only — the agent still calls
    # a tool for authoritative numbers.
    week_context: dict | None = None

    @field_validator("week_context")
    @classmethod
    def _cap_week_context(cls, value: dict | None) -> dict | None:
        # It is pasted into the prompt verbatim, so its size is a token bill.
        if value is not None and len(json.dumps(value, default=str)) > CHAT_WEEK_CONTEXT_MAX_CHARS:
            raise ValueError(f"week_context exceeds {CHAT_WEEK_CONTEXT_MAX_CHARS} characters")
        return value


class MemoryExtractRequest(BaseModel):
    """Which conversation to mine for durable facts."""

    session_id: str


@router.post("/api/ai/coach")
def get_ai_coaching_feedback(req: FeedbackRequest):
    """Generate the weekly AI analysis of the athlete's data."""
    try:
        return generate_weekly_coaching_insights(
            week_summary=req.week_summary,
            garmin_health=req.garmin_health,
            athlete_notes=req.athlete_notes,
        )
    except Exception as e:
        logger.exception("AI weekly feedback failed")
        raise HTTPException(status_code=500, detail="AI feedback is unavailable right now.") from e


@router.post("/api/ai/chat")
def post_ai_chat(req: ChatRequest):
    """
    Answer one turn of conversation with the tool-using assistant.

    Separate from `/api/ai/coach`, which stays a stateless one-shot generator for
    the weekly panel and keeps its heuristic fallback. This endpoint has no
    fallback on purpose: a conversational answer assembled from heuristics would
    be worse than an honest 503.
    """
    try:
        return coach_chat(
            message=req.message,
            session_id=req.session_id,
            week_context=req.week_context,
        )
    except CoachUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise internal_error("Assistant chat") from e


@router.get("/api/coach/memory")
def get_coach_memory():
    """
    List every durable fact the assistant has stored about the athlete.

    The memory outlives any conversation, so it has to be inspectable: this is
    how you find out that the assistant is still working around an injury you
    recovered from months ago.
    """
    try:
        facts = list_remembered_facts()
        return {"facts": facts, "count": len(facts)}
    except Exception as e:
        raise internal_error("Reading assistant memory") from e


@router.delete("/api/coach/memory/{fact_id}")
def delete_coach_memory(fact_id: str):
    """Prune one stored fact. 404 if that id was never stored."""
    try:
        removed = forget_remembered_fact(fact_id)
    except Exception as e:
        raise internal_error("Deleting assistant memory") from e
    if not removed:
        raise HTTPException(status_code=404, detail=f"No stored fact with id {fact_id}")
    return {"deleted": fact_id}


@router.post("/api/coach/memory/extract")
def post_coach_memory_extract(req: MemoryExtractRequest):
    """
    Mine a finished conversation for durable facts and file them.

    Called when the athlete starts a new chat: the closing conversation is the
    last chance to keep what they mentioned in passing before the transcript
    ages out. Facts land with `source="auto"` so they can be told apart from the
    ones they asked the assistant to remember.
    """
    try:
        return extract_session_facts(req.session_id)
    except CoachUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise internal_error("Memory extraction") from e
