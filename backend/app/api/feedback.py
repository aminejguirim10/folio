from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.feedback_service import record, summary

log = logging.getLogger(__name__)
router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackIn(BaseModel):
    rating: int = Field(description="1 for helpful, -1 for not")
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=8000)
    interaction_id: str | None = None
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackOut(BaseModel):
    stored: bool
    rating: int


@router.post("", response_model=FeedbackOut)
async def submit_feedback(payload: FeedbackIn) -> FeedbackOut:
    if payload.rating not in (1, -1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="rating must be 1 or -1",
        )
    record(
        interaction_id=payload.interaction_id,
        question=payload.question,
        answer=payload.answer,
        rating=payload.rating,
        comment=payload.comment,
    )
    return FeedbackOut(stored=True, rating=payload.rating)


@router.get("/summary")
async def feedback_summary() -> dict:
    return summary()