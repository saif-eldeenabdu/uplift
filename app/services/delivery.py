"""
app/services/delivery.py
─────────────────────────────────────────────────────────────────
Daily-message assignment logic.

get_or_assign_daily_message(session, user_id) → Message | None
  1. Check deliveries for (user, today).
  2. If found, return that message.
  3. If not, pick a random approved message that:
       - was NOT authored by the recipient
       - was NOT delivered to them in the last N days
  4. Create a Delivery row for today.
  5. If the pool is empty, return None (caller shows fallback).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Delivery, Message, MessageStatus
from app.settings import settings

logger = logging.getLogger(__name__)

# ── Fallback message shown when the pool is empty ──────────────
DEFAULT_FALLBACK_TEXT = (
    "You are worthy of kindness, today and every day. "
    "— The Uplift Team"
)


async def get_or_assign_daily_message(
    session: AsyncSession,
    user_id: str,
) -> Optional[Message]:
    """
    Return the message delivered to *user_id* today.
    Assigns a new one if none exists yet.  Returns None only when
    the approved-message pool is empty (caller should show fallback).
    """
    today = date.today()

    # ── 1. Already delivered today? ─────────────────────────────
    stmt = (
        select(Delivery)
        .where(Delivery.recipient_user_id == user_id)
        .where(Delivery.delivery_date == today)
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        # Eagerly load the related message
        msg_stmt = select(Message).where(Message.id == existing.message_id)
        msg_result = await session.execute(msg_stmt)
        return msg_result.scalar_one_or_none()

    # ── 2. Gather IDs delivered in the recent window ────────────
    window_start = today - timedelta(days=settings.delivery_repeat_window_days)
    recent_stmt = (
        select(Delivery.message_id)
        .where(Delivery.recipient_user_id == user_id)
        .where(Delivery.delivery_date >= window_start)
    )
    recent_result = await session.execute(recent_stmt)
    recent_ids = {row[0] for row in recent_result.all()}

    # ── 3. Pick a random approved message ───────────────────────
    pool_stmt = (
        select(Message)
        .where(Message.status == MessageStatus.approved)
        .where(Message.author_user_id != user_id)        # not self-authored
    )
    if recent_ids:
        pool_stmt = pool_stmt.where(~Message.id.in_(recent_ids))

    # Random ordering (works for SQLite & PostgreSQL)
    pool_stmt = pool_stmt.order_by(func.random()).limit(1)

    pool_result = await session.execute(pool_stmt)
    chosen = pool_result.scalar_one_or_none()

    if chosen is None:
        # Relax the repeat constraint and try once more
        fallback_stmt = (
            select(Message)
            .where(Message.status == MessageStatus.approved)
            .where(Message.author_user_id != user_id)
            .order_by(func.random())
            .limit(1)
        )
        fb_result = await session.execute(fallback_stmt)
        chosen = fb_result.scalar_one_or_none()

    if chosen is None:
        logger.warning("Approved message pool is empty for user %s", user_id)
        return None

    # ── 4. Record the delivery ──────────────────────────────────
    delivery = Delivery(
        recipient_user_id=user_id,
        message_id=chosen.id,
        delivery_date=today,
    )
    session.add(delivery)
    await session.commit()

    return chosen
