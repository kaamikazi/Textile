from __future__ import annotations

import asyncio
import logging
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
import spreadsheet_sync
from database import Actor, ConflictError, ValidationError
from telegram_automation import (
    TelegramAccessError,
    TelegramRateLimitError,
    authorize_telegram_user,
    acquire_bot_lease,
    cancel_pending_operation,
    confirm_pending_operation,
    create_pending_operation,
    enforce_rate_limit,
    expire_pending_operations,
    get_active_pending_operation,
    get_telegram_user,
    record_telegram_rejection,
    release_bot_lease,
    require_authorized_user,
    set_telegram_user_status,
    today_summary,
    update_pending_operation,
)
from telegram_bot import RedactingFormatter, _guard, _operation_keyboard, _selection_keyboard
from telegram_config import TelegramConfig


ADMIN_ID = 110001
STAFF_ID = 110002


@pytest.fixture
def telegram_factory(isolated_factory, actor):
    db_path, workbook_path = isolated_factory
    authorize_telegram_user(ADMIN_ID, "Telegram Admin", "Admin", actor, db_path)
    authorize_telegram_user(STAFF_ID, "Telegram Staff", "Staff", actor, db_path)
    employee = database.create_employee(
        "Telegram Operator", "Operator", "", 1000, 0, 80, "Active", "2026-01-01", actor, db_path
    )
    machine = database.save_machine(
        "TG-01", "Idle", "Telegram Operator", "", "2026-01-01", actor, db_path
    )
    return db_path, workbook_path, employee.entity_id, machine.entity_id


def _production_payload():
    return {
        "production_date": "2026-07-22",
        "machine_number": "TG-01",
        "operator_name": "Telegram Operator",
        "product_type": "Rib Collar",
        "quantity": 12,
        "rate_per_unit": 2.5,
    }


def _pending(user_id, operation_type, payload, db_path, ttl=20):
    user = require_authorized_user(user_id, db_path)
    pending = create_pending_operation(
        user, user_id, operation_type, "confirm", payload, db_path, ttl_minutes=ttl
    )
    return pending


def test_unauthorized_and_suspended_users_are_rejected_and_audited(isolated_factory, actor):
    db_path, _ = isolated_factory
    with pytest.raises(TelegramAccessError):
        require_authorized_user(999999, db_path)
    authorize_telegram_user(STAFF_ID, "Suspended", "Staff", actor, db_path)
    set_telegram_user_status(STAFF_ID, "Suspended", actor, db_path)
    with pytest.raises(TelegramAccessError):
        require_authorized_user(STAFF_ID, db_path)
    rejected = database.fetch_df(
        "SELECT * FROM audit_logs WHERE action = 'telegram_rejected'", path=db_path
    )
    assert len(rejected) == 2
    assert all("telegram:" in username for username in rejected["username"])


def test_mocked_telegram_update_rejects_unknown_user(isolated_factory):
    update = MagicMock()
    update.effective_user.id = 777777
    update.effective_chat.id = 777777
    update.effective_chat.type = "private"
    update.callback_query = None
    update.effective_message.reply_text = AsyncMock()
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"config": TelegramConfig("test-token", None)}
        )
    )
    result = asyncio.run(_guard(update, context))
    assert result is None
    update.effective_message.reply_text.assert_awaited_once()


def test_no_save_before_confirmation_and_cancel_changes_nothing(telegram_factory):
    db_path, _, _, _ = telegram_factory
    user = require_authorized_user(STAFF_ID, db_path)
    pending = create_pending_operation(
        user, STAFF_ID, "production", "confirm", _production_payload(), db_path
    )
    assert database.fetch_one("SELECT COUNT(*) AS total FROM production_entries", path=db_path)["total"] == 0
    assert cancel_pending_operation(user, pending.operation_id, db_path) == 1
    assert database.fetch_one("SELECT COUNT(*) AS total FROM production_entries", path=db_path)["total"] == 0


def test_confirm_saves_once_and_repeated_confirm_is_idempotent(telegram_factory):
    db_path, _, _, _ = telegram_factory
    pending = _pending(STAFF_ID, "production", _production_payload(), db_path)
    first = confirm_pending_operation(pending.operation_id, STAFF_ID, db_path)
    second = confirm_pending_operation(pending.operation_id, STAFF_ID, db_path)
    assert first.entity_id == second.entity_id
    assert second.already_confirmed is True
    assert database.fetch_one("SELECT COUNT(*) AS total FROM production_entries", path=db_path)["total"] == 1
    audit = database.fetch_df(
        "SELECT * FROM audit_logs WHERE action = 'telegram_confirmed'", path=db_path
    )
    assert len(audit) == 1
    assert audit.iloc[0]["username"] == f"telegram:{STAFF_ID}"


@pytest.mark.parametrize(
    "payload",
    [
        {**_production_payload(), "quantity": -1},
        {**_production_payload(), "rate_per_unit": -0.01},
    ],
)
def test_invalid_negative_production_is_rejected(telegram_factory, payload):
    db_path, _, _, _ = telegram_factory
    pending = _pending(STAFF_ID, "production", payload, db_path)
    with pytest.raises(ValidationError):
        confirm_pending_operation(pending.operation_id, STAFF_ID, db_path)
    record_telegram_rejection(STAFF_ID, "Invalid numeric field.", "Staff", pending.operation_id, db_path)
    assert database.fetch_one("SELECT COUNT(*) AS total FROM production_entries", path=db_path)["total"] == 0


def test_duplicate_attendance_is_rejected(telegram_factory):
    db_path, _, employee_id, _ = telegram_factory
    payload = {
        "employee_id": employee_id,
        "employee_name": "Telegram Operator",
        "attendance_date": "2026-07-22",
        "status": "Present",
        "notes": "",
    }
    first = _pending(STAFF_ID, "attendance", payload, db_path)
    confirm_pending_operation(first.operation_id, STAFF_ID, db_path)
    second = _pending(STAFF_ID, "attendance", payload, db_path)
    with pytest.raises(ConflictError):
        confirm_pending_operation(second.operation_id, STAFF_ID, db_path)
    assert database.fetch_one("SELECT COUNT(*) AS total FROM attendance", path=db_path)["total"] == 1


def test_machine_change_requires_admin_and_audits_before_after(telegram_factory):
    db_path, _, _, machine_id = telegram_factory
    staff = require_authorized_user(STAFF_ID, db_path)
    with pytest.raises(TelegramAccessError):
        create_pending_operation(staff, STAFF_ID, "machine", "confirm", {}, db_path)

    payload = {
        "machine_id": machine_id,
        "machine_number": "TG-01",
        "previous_status": "Idle",
        "status": "Maintenance",
    }
    pending = _pending(ADMIN_ID, "machine", payload, db_path)
    result = confirm_pending_operation(pending.operation_id, ADMIN_ID, db_path)
    assert result.entity_id == machine_id
    row = database.fetch_one("SELECT status FROM machines WHERE id = ?", (machine_id,), db_path)
    assert row["status"] == "Maintenance"
    log = database.fetch_one(
        "SELECT * FROM audit_logs WHERE action = 'telegram_confirmed' AND entity_type = 'machine'",
        path=db_path,
    )
    assert '"status": "Idle"' in log["before_state_json"]
    assert '"status": "Maintenance"' in log["after_state_json"]


def test_sqlite_save_survives_excel_sync_failure(telegram_factory, monkeypatch):
    db_path, _, _, _ = telegram_factory
    pending = _pending(
        STAFF_ID,
        "expense",
        {"expense_date": "2026-07-22", "expense_type": "Power", "amount": 25, "description": ""},
        db_path,
    )

    def fail_sync(*args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(spreadsheet_sync, "sync_factory_workbook", fail_sync)
    result = confirm_pending_operation(pending.operation_id, STAFF_ID, db_path)
    assert result.sync_status == "Failed"
    assert database.fetch_one("SELECT COUNT(*) AS total FROM expenses", path=db_path)["total"] == 1
    receipt = database.fetch_one(
        "SELECT * FROM telegram_mutation_receipts WHERE operation_id = ?",
        (pending.operation_id,), db_path,
    )
    assert receipt is not None


def test_today_summary_uses_selected_day_only(telegram_factory, actor):
    db_path, _, employee_id, _ = telegram_factory
    database.create_production("2026-07-22", "TG-01", "Telegram Operator", "A", 10, 5, actor, db_path)
    database.create_production("2026-07-21", "TG-01", "Telegram Operator", "B", 99, 9, actor, db_path)
    database.create_expense("Power", 15, "", "2026-07-22", actor, db_path)
    database.create_expense("Other", 999, "", "2026-07-21", actor, db_path)
    database.create_attendance(employee_id, "2026-07-22", "Present", "", actor, db_path)
    summary = today_summary("2026-07-22", db_path)
    assert summary["production_quantity"] == 10
    assert summary["earnings"] == 50
    assert summary["expenses"] == 15
    assert summary["profit"] == 35
    assert summary["attendance"]["Present"] == 1
    assert summary["machines"]["Idle"] == 1


def test_incomplete_workflow_expires_without_mutation(telegram_factory):
    db_path, _, _, _ = telegram_factory
    pending = _pending(STAFF_ID, "production", _production_payload(), db_path, ttl=-1)
    assert expire_pending_operations(db_path) == 1
    assert get_active_pending_operation(STAFF_ID, pending.operation_id, db_path) is None
    row = database.fetch_one(
        "SELECT status FROM telegram_pending_operations WHERE operation_id = ?",
        (pending.operation_id,), db_path,
    )
    assert row["status"] == "Expired"
    assert database.fetch_one("SELECT COUNT(*) AS total FROM production_entries", path=db_path)["total"] == 0


def test_rate_limit_blocks_and_persists_audit(isolated_factory):
    db_path, _ = isolated_factory
    enforce_rate_limit(555, db_path, max_requests=1)
    with pytest.raises(TelegramRateLimitError):
        enforce_rate_limit(555, db_path, max_requests=1)
    row = database.fetch_one("SELECT blocked_until FROM telegram_rate_limits WHERE telegram_user_id = 555", path=db_path)
    assert row["blocked_until"] is not None
    audit = database.fetch_one(
        "SELECT COUNT(*) AS total FROM audit_logs WHERE entity_type = 'telegram_rate_limit'",
        path=db_path,
    )
    assert audit["total"] == 1


def test_token_is_redacted_from_structured_logs():
    token = "123456:super-secret-token"
    formatter = RedactingFormatter(token)
    record = logging.LogRecord(
        "telegram-test", logging.ERROR, __file__, 1, f"request failed {token}", (), None
    )
    rendered = formatter.format(record)
    assert token not in rendered
    assert "[REDACTED]" in rendered


def test_all_callback_payloads_fit_telegram_limit():
    operation_id = "12345678-1234-1234-1234-123456789012"
    markups = [
        _operation_keyboard(operation_id),
        _selection_keyboard(operation_id, "e", [("Employee", "999999")]),
        _selection_keyboard(operation_id, "as", [("Present", "Present")]),
        _selection_keyboard(operation_id, "ms", [("Out of Service", "o")]),
    ]
    for markup in markups:
        for row in markup.inline_keyboard:
            for button in row:
                assert len(button.callback_data.encode("utf-8")) <= 64


def test_bot_lease_rejects_duplicate_instances(isolated_factory):
    db_path, _ = isolated_factory
    acquire_bot_lease("instance-one", db_path)
    with pytest.raises(ConflictError):
        acquire_bot_lease("instance-two", db_path)
    release_bot_lease("instance-one", path=db_path)
    acquire_bot_lease("instance-two", db_path)
    release_bot_lease("instance-two", path=db_path)
