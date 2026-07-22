from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    approved_group_id: int | None
    rate_limit_requests: int = 25
    rate_limit_window_seconds: int = 60
    rate_limit_block_seconds: int = 300


def _load_streamlit_secrets() -> dict:
    path = ROOT / ".streamlit" / "secrets.toml"
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _optional_int(value) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("TELEGRAM_APPROVED_GROUP_ID must be a numeric chat ID.") from exc


def load_telegram_config(require_token: bool = True) -> TelegramConfig:
    secrets = _load_streamlit_secrets()
    telegram_section = secrets.get("telegram", {}) if isinstance(secrets, dict) else {}
    token = os.getenv("TELEGRAM_BOT_TOKEN") or telegram_section.get("bot_token", "")
    token = str(token).strip()
    if require_token and not token:
        raise RuntimeError(
            "Telegram bot token is not configured. Set TELEGRAM_BOT_TOKEN or "
            ".streamlit/secrets.toml [telegram].bot_token."
        )
    approved_group = os.getenv("TELEGRAM_APPROVED_GROUP_ID")
    if approved_group in {None, ""}:
        approved_group = telegram_section.get("approved_group_id")
    return TelegramConfig(token, _optional_int(approved_group))


def telegram_token_is_configured() -> bool:
    return bool(load_telegram_config(require_token=False).bot_token)
