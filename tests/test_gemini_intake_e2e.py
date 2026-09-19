"""End-to-end: a free-text message through the real Telegram handlers.

This is the closest equivalent to the browser pass used for the Streamlit
pages. A Telegram bot has no browser to drive, and a literal end-to-end run
would need a live bot token, a real Telegram account and a real Gemini key -
none of which belong in CI. So these drive the actual handler functions
(text_input_handler -> _handle_natural_language -> callback_handler) with
fake Update/Context objects against a temporary database, which exercises
the same wiring an operator would.

Only the Gemini client is faked. Everything else - the guard, rate limiting,
authorization, create_pending_operation, the confirmation text, the keyboard
callback data and confirm_pending_operation - is the real code.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

import database
import gemini_intake
import telegram_automation
import telegram_bot
from telegram_automation import authorize_telegram_user
from telegram_config import TelegramConfig

STAFF_ID = 880001
CHAT_ID = 880001


# ---------------------------------------------------------------------------
# Fake Telegram objects
# ---------------------------------------------------------------------------

class Sent:
    """Records what the bot replied."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []

    @property
    def last_text(self) -> str:
        return self.messages[-1][0] if self.messages else ""

    @property
    def last_markup(self):
        return self.messages[-1][1] if self.messages else None


class FakeMessage:
    def __init__(self, text: str, sent: Sent) -> None:
        self.text = text
        self._sent = sent

    async def reply_text(self, text, reply_markup=None):
        self._sent.messages.append((text, reply_markup))


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id
        self.type = "private"


class FakeCallbackQuery:
    def __init__(self, data: str, sent: Sent) -> None:
        self.data = data
        self.message = FakeMessage("", sent)

    async def answer(self):
        return None


class FakeUpdate:
    def __init__(self, sent: Sent, text: str = "", callback_data: str | None = None) -> None:
        self.effective_user = FakeUser(STAFF_ID)
        self.effective_chat = FakeChat(CHAT_ID)
        self.effective_message = FakeMessage(text, sent) if callback_data is None else None
        self.callback_query = FakeCallbackQuery(callback_data, sent) if callback_data else None


class FakeApplication:
    def __init__(self) -> None:
        self.bot_data = {
            "config": TelegramConfig(bot_token="test-token", approved_group_id=None)
        }


class FakeContext:
    def __init__(self) -> None:
        self.application = FakeApplication()


class _FakeModels:
    def __init__(self, payload) -> None:
        self._payload = payload

    def generate_content(self, **kwargs):
        class R:
            text = json.dumps(payload) if isinstance(payload := self._payload, dict) else payload
        return R()


class FakeGeminiClient:
    def __init__(self, payload) -> None:
        self.models = _FakeModels(payload)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def bot_factory(isolated_factory, actor, monkeypatch):
    """A temp database with a machine and an authorized Telegram user.

    telegram_bot and telegram_automation call the database helpers without a
    path, so the module-level DB_PATH is redirected at the source.
    """
    db_path, _ = isolated_factory
    database.save_machine("M-03", "Running", "Rafiq", "", date(2026, 1, 1), actor, db_path)
    authorize_telegram_user(STAFF_ID, "TG Staff", "Staff", actor, db_path)

    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(telegram_automation, "DB_PATH", db_path, raising=False)
    return db_path


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# The whole journey
# ---------------------------------------------------------------------------

def test_free_text_message_reaches_confirmation_and_then_saves(bot_factory, monkeypatch):
    """Send a message, get the confirm screen, tap Confirm, check the row."""
    sent = Sent()

    monkeypatch.setattr(
        gemini_intake, "natural_language_enabled", lambda: True
    )
    monkeypatch.setattr(telegram_bot, "natural_language_enabled", lambda: True)
    monkeypatch.setattr(
        gemini_intake, "_build_client",
        lambda: FakeGeminiClient({
            "intent": "production",
            "machine_number": "machine 3",
            "operator_name": "Rafiq",
            "product_type": "round-neck t-shirt",
            "quantity": 250,
            "rate_per_unit": 12,
            "entry_date": None,
        }),
    )

    # --- the operator sends one free-text message -------------------------
    update = FakeUpdate(sent, text="machine 3, rafiq made 250 round-neck tshirts at 12 taka each")
    _run(telegram_bot.text_input_handler(update, FakeContext()))

    # It produced the confirmation screen, not the menu fallback.
    assert "Confirm production entry" in sent.last_text
    assert "Machine: M-03" in sent.last_text
    assert "Quantity: 250" in sent.last_text
    assert "Calculated total: BDT 3,000.00" in sent.last_text

    # With the real Confirm / Edit / Cancel keyboard.
    buttons = [b.text for row in sent.last_markup.inline_keyboard for b in row]
    assert buttons == ["Confirm and Save", "Edit", "Cancel"]

    # Nothing is written yet.
    assert database.fetch_one(
        "SELECT COUNT(*) AS n FROM production_entries", path=bot_factory
    )["n"] == 0

    # --- the operator taps Confirm ----------------------------------------
    confirm_button = sent.last_markup.inline_keyboard[0][0]
    assert confirm_button.callback_data.startswith("c|")

    confirm_update = FakeUpdate(sent, callback_data=confirm_button.callback_data)
    _run(telegram_bot.callback_handler(confirm_update, FakeContext()))

    assert "Saved production record #" in sent.last_text

    # --- and the row is really there --------------------------------------
    rows = database.fetch_df("SELECT * FROM production_entries", path=bot_factory)
    assert len(rows) == 1
    row = rows.iloc[0]
    assert row["machine_number"] == "M-03"
    assert row["operator_name"] == "Rafiq"
    assert row["product_type"] == "round-neck t-shirt"
    assert int(row["quantity"]) == 250
    assert float(row["rate_per_unit"]) == 12.0
    assert float(row["total_amount"]) == 3000.0
    assert row["production_date"] == date.today().isoformat()

    # --- tagged so an admin can tell it was parsed ------------------------
    descriptions = database.fetch_df(
        "SELECT description FROM audit_logs", path=bot_factory
    )["description"].tolist()
    assert any(gemini_intake.NL_AUDIT_TAG in d for d in descriptions)


def test_unparseable_message_falls_back_to_the_menu(bot_factory, monkeypatch):
    sent = Sent()
    monkeypatch.setattr(telegram_bot, "natural_language_enabled", lambda: True)
    monkeypatch.setattr(
        gemini_intake, "_build_client",
        lambda: FakeGeminiClient({"intent": "unknown"}),
    )

    update = FakeUpdate(sent, text="good morning")
    _run(telegram_bot.text_input_handler(update, FakeContext()))

    assert sent.last_text == "Choose a command from the menu first."
    assert database.fetch_one(
        "SELECT COUNT(*) AS n FROM telegram_pending_operations", path=bot_factory
    )["n"] == 0


def test_api_failure_falls_back_to_the_menu(bot_factory, monkeypatch):
    """A Gemini outage must not surface a traceback in the chat."""
    sent = Sent()
    monkeypatch.setattr(telegram_bot, "natural_language_enabled", lambda: True)

    def exploding():
        class Boom:
            class models:
                @staticmethod
                def generate_content(**kwargs):
                    raise TimeoutError("deadline exceeded")
        return Boom()

    monkeypatch.setattr(gemini_intake, "_build_client", exploding)

    update = FakeUpdate(sent, text="machine 3 made 250 shirts at 12 taka")
    _run(telegram_bot.text_input_handler(update, FakeContext()))

    assert sent.last_text == "Choose a command from the menu first."


def test_free_text_never_interrupts_a_workflow_in_progress(bot_factory, monkeypatch):
    """Mid-/production, a reply answers the prompt and is not parsed."""
    sent = Sent()
    monkeypatch.setattr(telegram_bot, "natural_language_enabled", lambda: True)

    called = {"parsed": False}

    def should_not_run():
        called["parsed"] = True
        return FakeGeminiClient({"intent": "production"})

    monkeypatch.setattr(gemini_intake, "_build_client", should_not_run)

    # Start the button flow, which creates a pending operation.
    _run(telegram_bot.production_command(FakeUpdate(sent), FakeContext()))
    assert "Enter production date" in sent.last_text

    # A plain reply now answers that prompt instead of being parsed.
    _run(telegram_bot.text_input_handler(FakeUpdate(sent, text="Today"), FakeContext()))

    assert called["parsed"] is False
    assert "Select a machine." in sent.last_text


def test_unauthorized_user_never_reaches_the_parser(bot_factory, monkeypatch):
    """Authorization runs before any API call, so free text is not free."""
    sent = Sent()
    monkeypatch.setattr(telegram_bot, "natural_language_enabled", lambda: True)

    called = {"parsed": False}

    def should_not_run():
        called["parsed"] = True
        return FakeGeminiClient({"intent": "production"})

    monkeypatch.setattr(gemini_intake, "_build_client", should_not_run)

    class StrangerUpdate(FakeUpdate):
        def __init__(self, sent):
            super().__init__(sent, text="machine 3 made 250 shirts at 12")
            self.effective_user = FakeUser(999999)

    _run(telegram_bot.text_input_handler(StrangerUpdate(sent), FakeContext()))

    assert called["parsed"] is False
    assert "not authorized" in sent.last_text.lower()
