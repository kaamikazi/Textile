"""Natural-language intake.

Every test here mocks the Gemini client completely. No API key is read and
no network call is made, so these run identically in CI and offline.

The point of most of them is not that extraction works but that it *refuses*
to work: the module's job is to fail closed to the button flow on anything
short of a complete, validated entry.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

import database
import gemini_intake
from database import Actor
from gemini_intake import build_payload, extract_entry, resolve_machine_number
from telegram_automation import (
    authorize_telegram_user,
    confirm_pending_operation,
    create_pending_operation,
    require_authorized_user,
)

STAFF_ID = 770001


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, payload, error=None) -> None:
        self._payload = payload
        self._error = error
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if isinstance(self._payload, str):
            return _FakeResponse(self._payload)
        return _FakeResponse(json.dumps(self._payload))


class FakeClient:
    """Stands in for google.genai.Client."""

    def __init__(self, payload=None, error=None) -> None:
        self.models = _FakeModels(payload, error)


@pytest.fixture
def factory(isolated_factory, actor):
    """A database with two registered machines."""
    db_path, _ = isolated_factory
    database.save_machine("M-01", "Running", "Rahim", "", date(2026, 1, 1), actor, db_path)
    database.save_machine("M-03", "Running", "Rafiq", "", date(2026, 1, 1), actor, db_path)
    return db_path


def _production_response(**overrides):
    data = {
        "intent": "production",
        "machine_number": "machine 3",
        "operator_name": "Rafiq",
        "product_type": "round-neck t-shirt",
        "quantity": 250,
        "rate_per_unit": 12,
        "entry_date": None,
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------

def test_well_formed_message_produces_the_button_flow_payload(factory):
    client = FakeClient(_production_response())

    result = extract_entry(
        "machine 3, rafiq made 250 round-neck tshirts today at 12 taka each",
        factory, client,
    )

    assert result is not None
    operation_type, payload = result
    assert operation_type == "production"

    # Exactly the keys the button flow writes - no more, no fewer.
    assert set(payload) == {
        "production_date", "machine_number", "operator_name",
        "product_type", "quantity", "rate_per_unit",
    }
    assert payload["machine_number"] == "M-03"   # resolved to the registered label
    assert payload["operator_name"] == "Rafiq"
    assert payload["quantity"] == 250
    assert payload["rate_per_unit"] == 12.00
    assert payload["production_date"] == date.today().isoformat()


def test_expense_message_produces_the_expense_payload(factory):
    client = FakeClient({
        "intent": "expense",
        "expense_type": "Yarn",
        "amount": 4500.5,
        "description": "cotton from supplier",
        "entry_date": "2026-07-02",
    })

    result = extract_entry("bought yarn for 4500.50 taka", factory, client)

    assert result is not None
    operation_type, payload = result
    assert operation_type == "expense"
    assert set(payload) == {"expense_date", "expense_type", "amount", "description"}
    assert payload["amount"] == 4500.50
    assert payload["expense_date"] == "2026-07-02"


def test_stated_date_is_used_and_validated(factory):
    client = FakeClient(_production_response(entry_date="2026-07-02"))
    _, payload = extract_entry("...", factory, client)
    assert payload["production_date"] == "2026-07-02"


# ---------------------------------------------------------------------------
# Refusals - the actual point of the module
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing", ["quantity", "rate_per_unit"])
def test_missing_production_number_returns_none(factory, missing):
    """Never guess. A message without a quantity or a rate is not an entry."""
    client = FakeClient(_production_response(**{missing: None}))
    assert extract_entry("rafiq made some shirts on machine 3", factory, client) is None


def test_missing_expense_amount_returns_none(factory):
    client = FakeClient({"intent": "expense", "expense_type": "Yarn", "amount": None})
    assert extract_entry("bought some yarn today", factory, client) is None


def test_ambiguous_message_returns_none(factory):
    """"unknown" must not be coerced into the more likely option."""
    client = FakeClient({"intent": "unknown"})
    assert extract_entry("machine 3 had a good day", factory, client) is None


def test_unregistered_machine_returns_none(factory):
    """Fails closed rather than warning and continuing."""
    client = FakeClient(_production_response(machine_number="machine 99"))
    assert extract_entry("machine 99 made 250 shirts at 12", factory, client) is None


def test_machine_omitted_entirely_returns_none(factory):
    client = FakeClient(_production_response(machine_number=None))
    assert extract_entry("rafiq made 250 shirts at 12 taka", factory, client) is None


def test_api_exception_returns_none(factory):
    """A timeout, revoked key or quota error degrades to the button flow."""
    client = FakeClient(error=TimeoutError("deadline exceeded"))
    assert extract_entry("machine 3 made 250 shirts at 12", factory, client) is None


def test_malformed_json_returns_none(factory):
    client = FakeClient("this is not json{{")
    assert extract_entry("machine 3 made 250 shirts at 12", factory, client) is None


def test_empty_model_response_returns_none(factory):
    client = FakeClient("")
    assert extract_entry("machine 3 made 250 shirts at 12", factory, client) is None


def test_negative_or_zero_quantity_returns_none(factory):
    for bad in (0, -5):
        client = FakeClient(_production_response(quantity=bad))
        assert extract_entry("...", factory, client) is None


def test_zero_rate_returns_none(factory):
    """round_money(allow_zero=False) rejects it, same as the typed flow."""
    client = FakeClient(_production_response(rate_per_unit=0))
    assert extract_entry("...", factory, client) is None


def test_blank_and_oversized_messages_return_none(factory):
    client = FakeClient(_production_response())
    assert extract_entry("   ", factory, client) is None
    assert extract_entry("x" * 2001, factory, client) is None
    # Neither reached the model.
    assert client.models.calls == []


def test_no_api_key_means_no_call(factory, monkeypatch):
    """Without a key the module must not attempt a request at all."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(gemini_intake, "get_api_key", lambda: "")
    assert gemini_intake.natural_language_enabled() is False
    assert extract_entry("machine 3 made 250 shirts at 12", factory) is None


# ---------------------------------------------------------------------------
# Machine resolution
# ---------------------------------------------------------------------------

def test_machine_resolution_accepts_real_labels_and_spoken_forms(factory):
    assert resolve_machine_number("M-01", factory) == "M-01"
    assert resolve_machine_number("m-01", factory) == "M-01"     # case-insensitive
    assert resolve_machine_number("machine 3", factory) == "M-03"
    assert resolve_machine_number("3", factory) == "M-03"
    assert resolve_machine_number("#3", factory) == "M-03"


def test_machine_resolution_fails_closed(factory):
    assert resolve_machine_number("machine 99", factory) is None
    assert resolve_machine_number("the big one", factory) is None
    assert resolve_machine_number("", factory) is None
    assert resolve_machine_number(None, factory) is None


def test_ambiguous_machine_digits_return_none(isolated_factory, actor):
    """Two machines whose digits match must not be guessed between."""
    db_path, _ = isolated_factory
    database.save_machine("M-03", "Running", "A", "", date(2026, 1, 1), actor, db_path)
    database.save_machine("L-3", "Running", "B", "", date(2026, 1, 1), actor, db_path)
    assert resolve_machine_number("3", db_path) is None


# ---------------------------------------------------------------------------
# The parsed payload must travel the ordinary confirmation path
# ---------------------------------------------------------------------------

def test_parsed_payload_writes_nothing_until_confirmed(factory, actor):
    """The whole safety argument, end to end.

    Extraction alone must leave the database untouched; only
    confirm_pending_operation() writes, exactly as for a button entry.
    """
    authorize_telegram_user(STAFF_ID, "TG Staff", "Staff", actor, factory)
    user = require_authorized_user(STAFF_ID, factory)

    client = FakeClient(_production_response())
    operation_type, payload = extract_entry("machine 3 ...", factory, client)

    # Nothing written by extraction.
    assert database.fetch_one(
        "SELECT COUNT(*) AS n FROM production_entries", path=factory
    )["n"] == 0

    operation = create_pending_operation(
        user, STAFF_ID, operation_type, "confirm", payload, factory
    )
    # Still nothing written - the entry is only pending.
    assert database.fetch_one(
        "SELECT COUNT(*) AS n FROM production_entries", path=factory
    )["n"] == 0
    assert operation.step == "confirm"

    result = confirm_pending_operation(operation.operation_id, STAFF_ID, factory)

    row = database.fetch_one(
        "SELECT * FROM production_entries WHERE id = ?", (result.entity_id,), factory
    )
    assert row is not None
    assert row["machine_number"] == "M-03"
    assert row["operator_name"] == "Rafiq"
    assert row["quantity"] == 250
    assert row["rate_per_unit"] == 12.0
    assert row["total_amount"] == 3000.0


def test_intake_audit_entry_is_tagged_distinctly(factory, actor):
    """An admin must be able to tell parsed entries from button entries."""
    authorize_telegram_user(STAFF_ID, "TG Staff", "Staff", actor, factory)
    user = require_authorized_user(STAFF_ID, factory)

    operation = create_pending_operation(
        user, STAFF_ID, "production", "confirm",
        {
            "production_date": "2026-07-02", "machine_number": "M-03",
            "operator_name": "Rafiq", "product_type": "Tee",
            "quantity": 10, "rate_per_unit": 2,
        },
        factory,
    )
    gemini_intake.record_intake_audit(
        user, operation.operation_id, "production",
        "machine 3 rafiq made 10 tees at 2 taka", factory,
    )

    rows = database.fetch_df(
        "SELECT description FROM audit_logs WHERE entity_id = ?",
        (operation.operation_id,), path=factory,
    )["description"].tolist()

    tagged = [r for r in rows if gemini_intake.NL_AUDIT_TAG in r]
    assert len(tagged) == 1
    assert "machine 3 rafiq made 10 tees" in tagged[0]
    # The button-flow entry is still there and is NOT tagged.
    assert any(gemini_intake.NL_AUDIT_TAG not in r for r in rows)


def test_audit_helper_never_raises(factory, actor):
    """Failing to annotate must not cost the operator a pending entry."""
    authorize_telegram_user(STAFF_ID, "TG Staff", "Staff", actor, factory)

    class Broken:
        actor = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    gemini_intake.record_intake_audit(Broken(), "op-id", "production", "text", factory)


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------

def test_request_pins_the_model_and_asks_for_json(factory):
    client = FakeClient(_production_response())
    extract_entry("machine 3 made 250 shirts at 12", factory, client)

    call = client.models.calls[0]
    assert call["model"] == gemini_intake.GEMINI_MODEL
    # A shut-down model id would silently break this in production.
    assert not gemini_intake.GEMINI_MODEL.startswith(("gemini-1.", "gemini-2."))
    config = call["config"]
    assert config.response_mime_type == "application/json"
    assert config.temperature == 0


def test_build_payload_rejects_non_dict_input(factory):
    assert build_payload(None, factory) is None
    assert build_payload([], factory) is None
    assert build_payload({}, factory) is None


def test_actor_for_parsed_entry_is_the_telegram_user(factory, actor):
    """Audit attribution must not become "system" just because AI parsed it."""
    authorize_telegram_user(STAFF_ID, "TG Staff", "Staff", actor, factory)
    user = require_authorized_user(STAFF_ID, factory)
    assert isinstance(user.actor, Actor)
    assert user.actor.username == f"telegram:{STAFF_ID}"
