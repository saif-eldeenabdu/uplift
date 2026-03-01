"""
app/schemas.py
─────────────────────────────────────────────────────────────────
Pydantic models for request / response validation.
"""

from pydantic import BaseModel, Field

from app.settings import settings


class MessageSubmission(BaseModel):
    """Validates the message-writing form."""
    text: str = Field(
        ...,
        min_length=settings.min_message_length,
        max_length=settings.max_message_length,
        description="The positive message content.",
    )


class AdminReviewAction(BaseModel):
    """Validates an admin approve / reject action."""
    message_id: str
    action: str = Field(..., pattern=r"^(approve|reject)$")
    reason: str = ""
