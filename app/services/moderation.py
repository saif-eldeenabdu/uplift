"""
app/services/moderation.py
─────────────────────────────────────────────────────────────────
Multi-layer content-moderation pipeline.

Pipeline order:
  1. Normalise text
  2. Hard-block keywords  (from data/blocked_terms.txt)
  3. Hard-block phrases   (from data/blocked_phrases.txt)
  4. Contextual regex rules (threats, directed insults)
  5. VADER sentiment check
  6. Length & quality checks

Returns (status, reason | None).
  status ∈ {"approved", "rejected", "pending"}
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Optional, Tuple

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from app.settings import settings

# ── Singleton VADER analyser ────────────────────────────────────
_vader = SentimentIntensityAnalyzer()


# ── Load external block lists (cached at import time) ──────────
def _load_lines(path: Path) -> list[str]:
    """Read non-empty, non-comment lines from a text file."""
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [
            line.strip().lower()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]


_blocked_terms: list[str] = _load_lines(settings.blocked_terms_path)
_blocked_phrases: list[str] = _load_lines(settings.blocked_phrases_path)


# ── 1. Normalisation ───────────────────────────────────────────
def normalise(text: str) -> str:
    """Lowercase, strip, normalise unicode, collapse repeated chars."""
    text = unicodedata.normalize("NFKC", text)
    text = text.lower().strip()
    # Collapse 3+ repeated characters to 2  ("loooser" → "looser")
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text


# ── 2. Hard-block keywords ─────────────────────────────────────
def _check_blocked_terms(text: str) -> Optional[str]:
    for term in _blocked_terms:
        pattern = rf"\b{re.escape(term)}\b"
        if re.search(pattern, text):
            return f"blocked_term: matched"
    return None


# ── 3. Hard-block phrases ──────────────────────────────────────
def _check_blocked_phrases(text: str) -> Optional[str]:
    for phrase in _blocked_phrases:
        if phrase in text:
            return f"blocked_phrase: matched"
    return None


# ── 4. Contextual regex rules ──────────────────────────────────
_CONTEXTUAL_PATTERNS: list[Tuple[str, str]] = [
    # Threats / self-harm directives
    (r"\b(go\s+(kill|die|hurt)\b)", "threat_pattern"),
    (r"\b(kill\s+your\s*self)\b", "threat_pattern"),
    (r"\b(you\s+should\s+(die|disappear))\b", "threat_pattern"),
    (r"\b(nobody\s+(loves|likes|cares\s+about)\s+you)\b", "harassment_pattern"),
    (r"\b(you\s+are\s+(worthless|useless|pathetic|disgusting))\b", "insult_pattern"),
    (r"\b(i\s+hate\s+you)\b", "harassment_pattern"),
]


def _check_contextual_rules(text: str) -> Optional[str]:
    for pattern, label in _CONTEXTUAL_PATTERNS:
        if re.search(pattern, text):
            return f"contextual_rule: {label}"
    return None


# ── 5. VADER sentiment check ──────────────────────────────────
def _check_sentiment(text: str) -> Optional[Tuple[str, float]]:
    """Return a reason string if sentiment is below the threshold."""
    scores = _vader.polarity_scores(text)
    compound = scores["compound"]
    if compound < settings.sentiment_threshold:
        return f"sentiment_negative: compound={compound:.3f}"
    return None


def _sentiment_is_borderline(text: str) -> bool:
    """True when the score is negative but above the hard-reject line."""
    scores = _vader.polarity_scores(text)
    compound = scores["compound"]
    return settings.sentiment_threshold <= compound < 0.0


# ── 6. Length & quality ────────────────────────────────────────
_EMOJI_RE = re.compile(
    "["
    "\U0001f600-\U0001f64f"
    "\U0001f300-\U0001f5ff"
    "\U0001f680-\U0001f6ff"
    "\U0001f1e0-\U0001f1ff"
    "\U00002702-\U000027b0"
    "\U000024c2-\U0001f251"
    "]+",
    flags=re.UNICODE,
)


def _check_quality(raw_text: str) -> Optional[str]:
    length = len(raw_text.strip())
    if length < settings.min_message_length:
        return f"too_short: {length} chars (min {settings.min_message_length})"
    if length > settings.max_message_length:
        return f"too_long: {length} chars (max {settings.max_message_length})"

    # Excessive emoji (>50 % of message)
    emoji_chars = _EMOJI_RE.findall(raw_text)
    total_emoji = sum(len(e) for e in emoji_chars)
    if length > 0 and total_emoji / length > 0.5:
        return "excessive_emoji"

    # Excessive repeated characters in original text (before normalisation)
    if re.search(r"(.)\1{5,}", raw_text.lower()):
        return "excessive_repetition"

    return None


# ── Public API ─────────────────────────────────────────────────
class ModerationResult:
    """Immutable result from the pipeline."""
    __slots__ = ("status", "reason", "events")

    def __init__(self, status: str, reason: Optional[str], events: list[dict]):
        self.status = status
        self.reason = reason
        self.events = events  # list of {"event_type": ..., "details_json": ...}


def moderate(raw_text: str) -> ModerationResult:
    """
    Run the full moderation pipeline on *raw_text*.

    Returns a ModerationResult with:
      - status: "approved" | "rejected" | "pending"
      - reason: human-readable string or None
      - events: list of dicts for moderation_events table
    """
    events: list[dict] = []

    # 6-a. Quality first (length)
    quality_issue = _check_quality(raw_text)
    if quality_issue:
        events.append({"event_type": "quality_block", "details_json": json.dumps({"reason": quality_issue})})
        return ModerationResult("rejected", quality_issue, events)

    # 1. Normalise for the keyword / pattern checks
    norm = normalise(raw_text)

    # 2. Blocked terms
    bt = _check_blocked_terms(norm)
    if bt:
        events.append({"event_type": "keyword_block", "details_json": json.dumps({"reason": bt})})
        return ModerationResult("rejected", bt, events)

    # 3. Blocked phrases
    bp = _check_blocked_phrases(norm)
    if bp:
        events.append({"event_type": "keyword_block", "details_json": json.dumps({"reason": bp})})
        return ModerationResult("rejected", bp, events)

    # 4. Contextual patterns
    ctx = _check_contextual_rules(norm)
    if ctx:
        events.append({"event_type": "contextual_block", "details_json": json.dumps({"reason": ctx})})
        return ModerationResult("rejected", ctx, events)

    # 5. Sentiment
    sent = _check_sentiment(norm)
    if sent:
        events.append({"event_type": "sentiment_block", "details_json": json.dumps({"reason": sent})})
        return ModerationResult("rejected", sent, events)

    # 5-b. Borderline? → pending for admin review
    if _sentiment_is_borderline(norm):
        events.append({"event_type": "sentiment_borderline", "details_json": json.dumps({"note": "near threshold"})})
        return ModerationResult("pending", "borderline sentiment — queued for review", events)

    # All clear
    return ModerationResult("approved", None, events)
