"""
app/settings.py
─────────────────────────────────────────────────────────────────
Central configuration loaded from environment / .env file using
Pydantic Settings.  All tuning knobs are exposed here.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Core ────────────────────────────────────────────────────
    secret_key: str = "change-me-to-a-real-secret"
    database_url: str = "sqlite+aiosqlite:///./uplift.db"
    admin_password: str = "admin"

    # ── Message constraints ─────────────────────────────────────
    max_message_length: int = 200
    min_message_length: int = 10

    # ── Rate limiting ───────────────────────────────────────────
    max_submissions_per_user_per_day: int = 3
    max_submissions_per_ip_per_hour: int = 10

    # ── Moderation ──────────────────────────────────────────────
    sentiment_threshold: float = -0.3

    # ── Delivery ────────────────────────────────────────────────
    delivery_repeat_window_days: int = 30

    # ── Paths (derived, not from .env) ──────────────────────────
    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def blocked_terms_path(self) -> Path:
        return self.data_dir / "blocked_terms.txt"

    @property
    def blocked_phrases_path(self) -> Path:
        return self.data_dir / "blocked_phrases.txt"


settings = Settings()
