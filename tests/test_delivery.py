"""
tests/test_delivery.py
─────────────────────────────────────────────────────────────────
Unit tests for the daily-message delivery logic.

Uses an in-memory async SQLite database for isolation.
"""

import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base, Delivery, Message, MessageStatus, User
from app.services.delivery import DEFAULT_FALLBACK_TEXT, get_or_assign_daily_message


@pytest_asyncio.fixture
async def session():
    """Create a fresh in-memory database for each test."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess

    await engine.dispose()


async def _create_user(session: AsyncSession, user_id: str | None = None) -> str:
    uid = user_id or str(uuid.uuid4())
    session.add(User(id=uid))
    await session.commit()
    return uid


async def _create_message(
    session: AsyncSession,
    author_id: str,
    text: str = "You are wonderful!",
    status: MessageStatus = MessageStatus.approved,
) -> Message:
    msg = Message(
        author_user_id=author_id,
        text=text,
        status=status,
        approved_at=datetime.now(timezone.utc) if status == MessageStatus.approved else None,
    )
    session.add(msg)
    await session.commit()
    return msg


# ── Tests ───────────────────────────────────────────────────────
class TestDelivery:
    @pytest.mark.asyncio
    async def test_empty_pool_returns_none(self, session):
        """When no approved messages exist, return None (fallback)."""
        user_id = await _create_user(session)
        result = await get_or_assign_daily_message(session, user_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_assigns_available_message(self, session):
        """When approved messages exist, one is assigned."""
        author = await _create_user(session)
        recipient = await _create_user(session)
        msg = await _create_message(session, author)

        result = await get_or_assign_daily_message(session, recipient)
        assert result is not None
        assert result.text == msg.text

    @pytest.mark.asyncio
    async def test_same_day_idempotent(self, session):
        """Calling twice on the same day returns the same message."""
        author = await _create_user(session)
        recipient = await _create_user(session)
        await _create_message(session, author)

        first = await get_or_assign_daily_message(session, recipient)
        second = await get_or_assign_daily_message(session, recipient)
        assert first is not None
        assert second is not None
        assert first.id == second.id

    @pytest.mark.asyncio
    async def test_no_self_delivery(self, session):
        """User should not receive their own message."""
        user = await _create_user(session)
        await _create_message(session, user, text="My own message")

        result = await get_or_assign_daily_message(session, user)
        # Pool has only self-authored → should be None
        assert result is None

    @pytest.mark.asyncio
    async def test_delivery_row_created(self, session):
        """A Delivery row is created after assignment."""
        author = await _create_user(session)
        recipient = await _create_user(session)
        await _create_message(session, author)

        await get_or_assign_daily_message(session, recipient)

        stmt = select(Delivery).where(
            Delivery.recipient_user_id == recipient,
            Delivery.delivery_date == date.today(),
        )
        result = await session.execute(stmt)
        delivery = result.scalar_one_or_none()
        assert delivery is not None

    @pytest.mark.asyncio
    async def test_pending_message_not_delivered(self, session):
        """Pending (un-approved) messages are never delivered."""
        author = await _create_user(session)
        recipient = await _create_user(session)
        await _create_message(session, author, status=MessageStatus.pending)

        result = await get_or_assign_daily_message(session, recipient)
        assert result is None
