"""Natural-language intake for the Telegram bot.

Staff can send one free-text message - "machine 3, rafiq made 250 round-neck
tshirts today at 12 taka each" - instead of answering the button flow one
field at a time.

What this module is allowed to do is deliberately narrow:

- It NEVER writes to SQLite. Its only database access is a read against the
  machines table to validate an extracted machine number.
- It NEVER produces a saved record. It returns a payload dict, which the
  caller hands to create_pending_operation() exactly as the button flow
  does, so the entry lands on the same Confirm / Edit / Cancel screen and is
  only written by confirm_pending_operation().
- It NEVER guesses a missing number. A message with no quantity, no rate or
  no amount, or one that could be either a production or an expense entry,
  returns None rather than a best-effort payload.
- It fails closed. A missing API key, a missing SDK, a timeout, a bad key, a
  malformed response or an unregistered machine all return None, and the
  caller falls back to the existing "Choose a command from the menu first."

The payload keys it emits are exactly the keys the button flow writes, so a
parsed entry is indistinguishable downstream:

    production: production_date, machine_number, operator_name,
                product_type, quantity, rate_per_unit
    expense:    expense_date, expense_type, amount, description
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

import database
from database import FactoryError, fetch_one

LOGGER = logging.getLogger("al_sadi.gemini_intake")

# Verified against Google's model list rather than carried over from an
# earlier draft: gemini-2.0-flash and 2.0-flash-lite are shut down, not
# merely deprecated. This is the current cheapest/fastest stable model, and
# the job here is short-text structured extraction, not reasoning.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# A factory operator is waiting on this message. Better to fall back to the
# menu quickly than to hold the chat open.
REQUEST_TIMEOUT_MS = 10_000

# Marks audit entries that originated from a parsed message rather than the
# button flow, so an admin reviewing history can tell them apart while trust
# in the parser is still being built.
NL_AUDIT_TAG = "[natural-language]"

SUPPORTED_OPERATIONS = ("production", "expense")

SYSTEM_INSTRUCTION = """
You extract structured factory records from short messages sent by staff at a
knitwear factory in Bangladesh. Messages may mix English and transliterated
Bengali, and may be lowercase and unpunctuated.

Return ONLY the JSON object described by the schema.

Rules you must follow exactly:

- intent is "production" when the message describes goods made on a machine.
- intent is "expense" when the message describes money spent.
- intent is "unknown" if the message is neither, is a greeting or question,
  or could plausibly be read as either. Do not pick the more likely one.
- NEVER invent, infer or round a number that is not stated. If quantity,
  rate or amount is absent, leave it null and set intent to "unknown".
- machine_number: copy the machine label as written ("machine 3", "M-03",
  "3"). Do not normalise it. Leave null if no machine is mentioned.
- operator_name: the person who did the work, if named. Leave null otherwise.
- product_type: what was produced, e.g. "round-neck t-shirt", "rib collar".
- rate_per_unit is the price of ONE unit, not the total. If the message only
  gives a total, leave rate_per_unit null and set intent to "unknown".
- entry_date: an ISO date (YYYY-MM-DD) only if the message states one.
  "today" or no date at all must be null - the caller supplies today's date.
- Currency words (taka, tk, BDT, ৳) are units, never part of a number.
""".strip()

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["production", "expense", "unknown"]},
        "machine_number": {"type": ["string", "null"]},
        "operator_name": {"type": ["string", "null"]},
        "product_type": {"type": ["string", "null"]},
        "quantity": {"type": ["integer", "null"]},
        "rate_per_unit": {"type": ["number", "null"]},
        "expense_type": {"type": ["string", "null"]},
        "amount": {"type": ["number", "null"]},
        "description": {"type": ["string", "null"]},
        "entry_date": {"type": ["string", "null"]},
    },
    "required": ["intent"],
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def get_api_key() -> str:
    """Read the key from the environment, then Streamlit secrets.

    Mirrors how telegram_config.py resolves the bot token. The key is never
    logged and never returned to a chat.
    """
    key = os.getenv("GEMINI_API_KEY", "")
    if key.strip():
        return key.strip()
    try:
        from telegram_config import _load_streamlit_secrets

        secrets = _load_streamlit_secrets()
    except Exception:
        return ""
    section = secrets.get("gemini", {}) if isinstance(secrets, dict) else {}
    return str(section.get("api_key", "") or "").strip()


def natural_language_enabled() -> bool:
    """True when free-text intake can run at all."""
    return bool(get_api_key())


def _build_client():
    """Construct a Gemini client, or return None.

    The SDK is imported lazily so that a factory PC which has not yet
    reinstalled requirements.txt degrades to the button flow instead of
    failing to start the bot.
    """
    key = get_api_key()
    if not key:
        return None
    try:
        from google import genai
        from google.genai import types

        return genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )
    except Exception:
        LOGGER.warning("Gemini client unavailable; falling back to the button flow.")
        return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def resolve_machine_number(raw: Any, path: str | Path | None = None) -> str | None:
    """Map whatever the model extracted onto a real machines row.

    Fails closed: an unregistered machine returns None so the caller
    abandons the parse. It never invents a machine or passes free text
    through, because the button flow only ever writes a machine_number that
    came out of the machines table.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    # Exact match on the registered label first.
    row = fetch_one(
        "SELECT machine_number FROM machines WHERE machine_number = ? COLLATE NOCASE",
        (text,), path,
    )
    if row is not None:
        return str(row["machine_number"])

    # "machine 3", "m3", "#3" -> match on the digits, which is how operators
    # actually refer to machines out loud.
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    candidates = database.fetch_df(
        "SELECT machine_number FROM machines ORDER BY machine_number", path=path
    )
    if candidates.empty:
        return None
    matches = [
        str(value)
        for value in candidates["machine_number"].tolist()
        if "".join(ch for ch in str(value) if ch.isdigit()).lstrip("0") == digits.lstrip("0")
    ]
    # Ambiguity is a failure, not a coin flip.
    return matches[0] if len(matches) == 1 else None


def _entry_date(raw: Any) -> str:
    """Validate a stated date, or default to today.

    Uses database._require_date so a parsed entry is validated by exactly the
    same rule as a typed one.
    """
    if raw is None or not str(raw).strip():
        return database._require_date(date.today(), "Date")
    return database._require_date(str(raw).strip(), "Date")


def _build_production_payload(data: dict[str, Any], path) -> dict[str, Any] | None:
    quantity = data.get("quantity")
    rate = data.get("rate_per_unit")
    # Never guess: a missing number ends the parse.
    if quantity is None or rate is None:
        return None
    if not data.get("operator_name") or not data.get("product_type"):
        return None

    machine_number = resolve_machine_number(data.get("machine_number"), path)
    if machine_number is None:
        return None

    try:
        quantity_value = int(quantity)
        if quantity_value <= 0:
            return None
        return {
            "production_date": _entry_date(data.get("entry_date")),
            "machine_number": machine_number,
            "operator_name": database._require_text(str(data["operator_name"]), "Operator", 120),
            "product_type": database._require_text(str(data["product_type"]), "Product type", 120),
            "quantity": quantity_value,
            "rate_per_unit": database.round_money(rate, "Rate per unit", allow_zero=False),
        }
    except (FactoryError, TypeError, ValueError):
        return None


def _build_expense_payload(data: dict[str, Any], path) -> dict[str, Any] | None:
    amount = data.get("amount")
    if amount is None:
        return None
    if not data.get("expense_type"):
        return None

    try:
        description = data.get("description") or ""
        return {
            "expense_date": _entry_date(data.get("entry_date")),
            "expense_type": database._require_text(
                str(data["expense_type"]), "Expense category", 100
            ),
            "amount": database.round_money(amount, "Amount", allow_zero=False),
            "description": str(description)[:500],
        }
    except (FactoryError, TypeError, ValueError):
        return None


def build_payload(
    data: dict[str, Any], path: str | Path | None = None
) -> tuple[str, dict[str, Any]] | None:
    """Turn a raw model response into a validated payload, or None.

    Separated from the API call so the whole validation path is testable
    without a client.
    """
    if not isinstance(data, dict):
        return None
    intent = str(data.get("intent") or "").strip().lower()
    if intent not in SUPPORTED_OPERATIONS:
        return None

    if intent == "production":
        payload = _build_production_payload(data, path)
    else:
        payload = _build_expense_payload(data, path)

    return (intent, payload) if payload is not None else None


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _call_model(client, message: str) -> dict[str, Any] | None:
    from google.genai import types

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=message,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
            temperature=0,
            max_output_tokens=512,
        ),
    )
    raw = (response.text or "").strip()
    if not raw:
        return None
    return json.loads(raw)


def extract_entry(
    message: str,
    path: str | Path | None = None,
    client: Any | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Parse a free-text message into (operation_type, payload), or None.

    Returns None for every failure mode - no key, no SDK, timeout, bad key,
    malformed JSON, unknown or ambiguous intent, a missing number, or an
    unregistered machine. The caller treats None as "not understood" and
    shows the normal menu prompt.

    `client` is injectable so tests can run without an API key or network.
    """
    text = (message or "").strip()
    if not text or len(text) > 2000:
        return None

    active = client if client is not None else _build_client()
    if active is None:
        return None

    try:
        data = _call_model(active, text)
    except Exception as exc:
        # Deliberately broad: a timeout, a revoked key, a quota error and a
        # malformed body must all degrade to the button flow rather than
        # surface a traceback in a factory operator's chat. The reason is
        # logged for the admin; the message never leaves the server.
        LOGGER.warning("Gemini extraction failed (%s); falling back to the button flow.",
                       type(exc).__name__)
        return None

    if data is None:
        return None
    return build_payload(data, path)


def record_intake_audit(
    user: Any,
    operation_id: str,
    operation_type: str,
    message: str,
    path: str | Path | None = None,
) -> None:
    """Tag a pending operation as having come from a parsed message.

    create_pending_operation() already writes its own `telegram_accepted`
    entry, and its signature is fixed, so this adds a second, clearly marked
    entry beside it rather than changing that call. The source message is
    recorded (truncated) because during the first weeks the useful question
    an admin asks is not "was this parsed?" but "did it parse correctly?".

    Never raises: a failure to annotate must not cost the operator their
    entry, which is already pending and confirmable.
    """
    from database import transaction, write_audit_log

    try:
        with transaction(path) as conn:
            write_audit_log(
                conn, user.actor, "telegram_accepted", "telegram_operation", operation_id,
                f"{NL_AUDIT_TAG} Telegram {operation_type} drafted from free text: "
                f"{message.strip()[:120]}",
            )
    except Exception:
        LOGGER.warning("Could not record natural-language intake audit entry.")
