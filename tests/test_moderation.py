"""
tests/test_moderation.py
─────────────────────────────────────────────────────────────────
Unit tests for the moderation pipeline.
"""

import pytest

from app.services.moderation import ModerationResult, moderate, normalise


# ── Normalisation ───────────────────────────────────────────────
class TestNormalise:
    def test_lowercases(self):
        assert normalise("HELLO") == "hello"

    def test_collapses_repeated_chars(self):
        assert normalise("loooooser") == "looser"

    def test_strips_extra_whitespace(self):
        assert normalise("  hello   world  ") == "hello world"


# ── Quality checks ──────────────────────────────────────────────
class TestQuality:
    def test_too_short_rejected(self):
        result = moderate("Hi")
        assert result.status == "rejected"
        assert "too_short" in (result.reason or "")

    def test_too_long_rejected(self):
        result = moderate("a" * 300)
        assert result.status == "rejected"
        assert "too_long" in (result.reason or "")

    def test_valid_length_passes_quality(self):
        result = moderate("You are wonderful and kind!")
        # Should not fail quality — might be approved or pending
        assert "too_short" not in (result.reason or "")
        assert "too_long" not in (result.reason or "")


# ── Keyword blocking ───────────────────────────────────────────
class TestKeywordBlock:
    def test_blocked_term_rejected(self):
        result = moderate("You are a complete loser in life")
        assert result.status == "rejected"

    def test_blocked_phrase_rejected(self):
        result = moderate("Why don't you just go away please")
        assert result.status == "rejected"


# ── Contextual rules ───────────────────────────────────────────
class TestContextualRules:
    def test_threat_pattern_rejected(self):
        result = moderate("You should just go die already okay")
        assert result.status == "rejected"
        assert "threat_pattern" in (result.reason or "")

    def test_harassment_pattern_rejected(self):
        result = moderate("Nobody cares about you at all okay")
        assert result.status == "rejected"


# ── Approved messages ──────────────────────────────────────────
class TestApproval:
    def test_positive_message_approved(self):
        result = moderate("You are amazing and the world is better with you in it!")
        assert result.status == "approved"

    def test_neutral_kind_message_approved(self):
        result = moderate("Keep going, you are doing great today!")
        assert result.status == "approved"

    def test_events_empty_for_approved(self):
        result = moderate("Every day is a new chance to be kind.")
        assert result.status == "approved"
        assert len(result.events) == 0
