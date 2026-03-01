"""
app/models.py
─────────────────────────────────────────────────────────────────
SQLAlchemy 2.0 ORM models.
Tables: users, messages, deliveries, moderation_events.
"""

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ── Base ────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Enums ───────────────────────────────────────────────────────
class MessageStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


# ── Users ───────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
    daily_streak_count = Column(Integer, default=0, nullable=False)

    messages = relationship("Message", back_populates="author", lazy="selectin")
    deliveries = relationship("Delivery", back_populates="recipient", lazy="selectin")


# ── Messages ────────────────────────────────────────────────────
class Message(Base):
    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    author_user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    text = Column(String(500), nullable=False)
    status = Column(Enum(MessageStatus), default=MessageStatus.pending, nullable=False)
    rejection_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=True)

    author = relationship("User", back_populates="messages")
    moderation_events = relationship("ModerationEvent", back_populates="message", lazy="selectin")


# ── Deliveries ──────────────────────────────────────────────────
class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("recipient_user_id", "delivery_date", name="uq_one_per_user_per_day"),
    )

    id = Column(String(36), primary_key=True, default=_new_uuid)
    recipient_user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    message_id = Column(String(36), ForeignKey("messages.id"), nullable=False)
    delivery_date = Column(Date, default=lambda: date.today(), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    recipient = relationship("User", back_populates="deliveries")
    message = relationship("Message")


# ── Moderation Events ──────────────────────────────────────────
class ModerationEvent(Base):
    __tablename__ = "moderation_events"

    id = Column(String(36), primary_key=True, default=_new_uuid)
    message_id = Column(String(36), ForeignKey("messages.id"), nullable=False)
    event_type = Column(String(50), nullable=False)  # keyword_block, sentiment_block, etc.
    details_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    message = relationship("Message", back_populates="moderation_events")
