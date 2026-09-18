"""Failure paths a factory operator actually hits.

These cover the cases where something outside the database goes wrong -
Excel is open in another window, the live database file is damaged, a
Telegram confirmation arrives after the workflow expired - and assert the
guarantee the app makes in SECURITY_NOTES.md and the README: SQLite is
authoritative, it commits first, and nothing downstream of that commit can
roll it back or silently lose it.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

import database
import spreadsheet_sync
from data_safety import backup_database, restore_database, validate_sqlite_database
from database import ValidationError
from telegram_automation import (
    confirm_pending_operation,
    create_pending_operation,
    expire_pending_operations,
    require_authorized_user,
)

ADMIN_ID = 900001
STAFF_ID = 900002


# ---------------------------------------------------------------------------
# Excel sync failures never cost a committed row
# ---------------------------------------------------------------------------

def test_locked_workbook_still_commits_production_and_reports_failed(
    isolated_factory, actor, monkeypatch
):
    """Excel open in another window is the single most common failure here."""
    db_path, _ = isolated_factory
    database.save_machine("M-01", "Running", "Operator", "", date(2026, 1, 1), actor, db_path)

    def locked(*args, **kwargs):
        raise PermissionError("[Errno 13] factory_records.xlsx is open in Excel")

    monkeypatch.setattr(spreadsheet_sync, "sync_factory_workbook", locked)

    result = database.create_production(
        date(2026, 7, 2), "M-01", "Operator", "Cuff", 12, 3, actor, db_path
    )

    # The save succeeded and is durable.
    assert result.entity_id > 0
    row = database.fetch_one(
        "SELECT * FROM production_entries WHERE id = ?", (result.entity_id,), db_path
    )
    assert row is not None
    assert row["quantity"] == 12

    # And the operator is told the workbook is behind, not that the save failed.
    assert result.sync_status == "Failed"
    assert "Excel" in result.sync_message
    status = database.get_excel_sync_status(db_path)
    assert status["status"] == "Failed"


def test_locked_workbook_failure_is_recorded_for_every_mutation_type(
    isolated_factory, actor, monkeypatch
):
    db_path, _ = isolated_factory
    employee = database.create_employee(
        "Worker", "Operator", "", 1000, 0, 80, "Active", date(2026, 1, 1), actor, db_path
    )

    def locked(*args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(spreadsheet_sync, "sync_factory_workbook", locked)

    expense = database.create_expense("Yarn", 500, "", date(2026, 7, 2), actor, db_path)
    attendance = database.create_attendance(
        employee.entity_id, date(2026, 7, 2), "Present", "", actor, db_path
    )

    assert expense.sync_status == "Failed"
    assert attendance.sync_status == "Failed"
    assert database.fetch_one("SELECT COUNT(*) AS n FROM expenses", path=db_path)["n"] == 1
    assert database.fetch_one("SELECT COUNT(*) AS n FROM attendance", path=db_path)["n"] == 1


def test_sync_recovers_once_the_workbook_is_closed(isolated_factory, actor, monkeypatch):
    """After a lock clears, a later write returns the status to Synced."""
    db_path, workbook_path = isolated_factory
    database.save_machine("M-01", "Running", "Operator", "", date(2026, 1, 1), actor, db_path)

    def locked(*args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(spreadsheet_sync, "sync_factory_workbook", locked)
    first = database.create_production(
        date(2026, 7, 2), "M-01", "Operator", "Cuff", 5, 2, actor, db_path
    )
    assert first.sync_status == "Failed"

    monkeypatch.undo()
    monkeypatch.setattr(spreadsheet_sync, "WORKBOOK_PATH", workbook_path)
    second = database.create_production(
        date(2026, 7, 3), "M-01", "Operator", "Cuff", 6, 2, actor, db_path
    )

    assert second.sync_status == "Synced"
    assert database.get_excel_sync_status(db_path)["status"] == "Synced"
    # Both rows are present - the failed sync never cost the first one.
    assert database.fetch_one(
        "SELECT COUNT(*) AS n FROM production_entries", path=db_path
    )["n"] == 2


def test_failed_sync_leaves_no_temp_workbook_behind(isolated_factory, actor, tmp_path, monkeypatch):
    """The atomic write must clean up its temp file even when writing fails."""
    db_path, _ = isolated_factory
    destination = tmp_path / "workbook.xlsx"

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(spreadsheet_sync, "_write_workbook_file", boom)

    with pytest.raises(OSError, match="disk full"):
        spreadsheet_sync.sync_factory_workbook(destination, db_path)

    leftovers = list(tmp_path.glob(".workbook_*.tmp.xlsx"))
    assert leftovers == []
    assert not destination.exists()


# ---------------------------------------------------------------------------
# Backup / restore end to end
# ---------------------------------------------------------------------------

def test_backup_restores_a_corrupted_live_database_end_to_end(
    isolated_factory, actor, tmp_path
):
    """Create a backup, corrupt the live file, restore, verify integrity.

    This is the recovery procedure documented in the README, exercised for
    real rather than assumed.
    """
    db_path, _ = isolated_factory
    backup_dir = tmp_path / "backups"

    database.create_expense("Yarn", 1200, "Before backup", date(2026, 7, 1), actor, db_path)
    database.create_expense("Power", 800, "Before backup", date(2026, 7, 2), actor, db_path)
    backup = backup_database(actor, db_path, backup_dir)
    assert validate_sqlite_database(backup)[0] is True

    # Corrupt the live database by overwriting its header in place.
    with open(db_path, "r+b") as handle:
        handle.seek(0)
        handle.write(b"\x00" * 512)

    corrupted_ok, corrupted_message = validate_sqlite_database(db_path)
    assert corrupted_ok is False
    assert corrupted_message

    restored, pre_restore = restore_database(backup, "RESTORE", actor, db_path, backup_dir)

    # Integrity passes and the pre-restore copy of the damaged file was kept.
    ok, message = validate_sqlite_database(restored)
    assert ok is True, message
    assert pre_restore.exists()

    # The data from before the backup is back, and readable.
    with sqlite3.connect(restored) as conn:
        rows = conn.execute("SELECT description FROM expenses ORDER BY id").fetchall()
    assert [row[0] for row in rows] == ["Before backup", "Before backup"]

    # The restore itself is auditable.
    audit = database.fetch_one(
        "SELECT COUNT(*) AS n FROM audit_logs WHERE action = 'restore'", path=restored
    )
    assert audit["n"] == 1


def test_restore_rejects_a_truncated_sqlite_file(isolated_factory, actor, tmp_path):
    """A half-copied backup has a valid header but fails an integrity check."""
    db_path, _ = isolated_factory
    backup_dir = tmp_path / "backups"
    database.create_expense("Yarn", 10, "", date(2026, 7, 1), actor, db_path)
    good = backup_database(actor, db_path, backup_dir)

    truncated = backup_dir / "truncated.db"
    data = good.read_bytes()
    truncated.write_bytes(data[: len(data) // 3])

    with pytest.raises(ValidationError):
        restore_database(truncated, "RESTORE", actor, db_path, backup_dir)

    # The live database is untouched and still usable.
    assert validate_sqlite_database(db_path)[0] is True
    assert database.fetch_one("SELECT COUNT(*) AS n FROM expenses", path=db_path)["n"] == 1


def test_restore_requires_the_exact_confirmation_word(isolated_factory, actor, tmp_path):
    db_path, _ = isolated_factory
    backup_dir = tmp_path / "backups"
    backup = backup_database(actor, db_path, backup_dir)

    for wrong in ["restore", "RESTORE ", "", "yes"]:
        with pytest.raises(Exception) as excinfo:
            restore_database(backup, wrong, actor, db_path, backup_dir)
        assert not isinstance(excinfo.value, SystemExit)


# ---------------------------------------------------------------------------
# Telegram confirmations racing expiry
# ---------------------------------------------------------------------------

def _authorize(db_path, actor):
    from telegram_automation import authorize_telegram_user

    authorize_telegram_user(ADMIN_ID, "TG Admin", "Admin", actor, db_path)
    authorize_telegram_user(STAFF_ID, "TG Staff", "Staff", actor, db_path)
    return require_authorized_user(STAFF_ID, db_path)


def _authorized_admin(db_path):
    return require_authorized_user(ADMIN_ID, db_path)


def _pending(staff, payload, db_path, ttl):
    return create_pending_operation(
        staff, staff.telegram_user_id, "production", "confirm", payload, db_path, ttl_minutes=ttl
    )


def _production_payload() -> dict:
    return {
        "production_date": "2026-07-22",
        "machine_number": "TG-01",
        "operator_name": "TG Operator",
        "product_type": "Rib Collar",
        "quantity": 10,
        "rate_per_unit": 4,
    }


def test_confirmation_arriving_after_expiry_saves_nothing(isolated_factory, actor):
    """The operator taps Confirm on a card that has already timed out."""
    db_path, _ = isolated_factory
    staff = _authorize(db_path, actor)
    database.save_machine("TG-01", "Idle", "TG Operator", "", date(2026, 1, 1), actor, db_path)

    pending = _pending(staff, _production_payload(), db_path, -1)

    # Expiry sweep runs (as it does on every incoming update), then the
    # delayed confirmation lands.
    assert expire_pending_operations(db_path) == 1

    with pytest.raises(ValidationError):
        confirm_pending_operation(pending.operation_id, STAFF_ID, db_path)

    assert database.fetch_one(
        "SELECT COUNT(*) AS n FROM production_entries", path=db_path
    )["n"] == 0


def test_expired_confirmation_is_rejected_even_without_a_prior_sweep(isolated_factory, actor):
    """confirm_pending_operation must expire lazily, not rely on a sweep."""
    db_path, _ = isolated_factory
    staff = _authorize(db_path, actor)
    database.save_machine("TG-01", "Idle", "TG Operator", "", date(2026, 1, 1), actor, db_path)

    pending = _pending(staff, _production_payload(), db_path, -1)

    # No expire_pending_operations() call here on purpose.
    with pytest.raises(ValidationError):
        confirm_pending_operation(pending.operation_id, STAFF_ID, db_path)

    assert database.fetch_one(
        "SELECT COUNT(*) AS n FROM production_entries", path=db_path
    )["n"] == 0
    row = database.fetch_one(
        "SELECT status FROM telegram_pending_operations WHERE operation_id = ?",
        (pending.operation_id,), db_path,
    )
    assert row["status"] == "Expired"


def test_expiry_does_not_touch_a_still_valid_operation(isolated_factory, actor):
    """Expiry is per-operation, not a blanket sweep of everything pending.

    Each Telegram user may only hold one workflow at a time - starting a new
    one cancels their previous - so the valid and expired operations here
    belong to two different users.
    """
    db_path, _ = isolated_factory
    staff = _authorize(db_path, actor)
    admin = _authorized_admin(db_path)
    database.save_machine("TG-01", "Idle", "TG Operator", "", date(2026, 1, 1), actor, db_path)

    fresh = _pending(admin, _production_payload(), db_path, 20)
    stale = _pending(staff, _production_payload(), db_path, -1)

    assert expire_pending_operations(db_path) == 1

    rows = {
        row["operation_id"]: row["status"]
        for row in database.fetch_df(
            "SELECT operation_id, status FROM telegram_pending_operations", path=db_path
        ).to_dict("records")
    }
    assert rows[stale.operation_id] == "Expired"
    assert rows[fresh.operation_id] == "Pending"

    # The still-valid one confirms normally and writes exactly one row.
    result = confirm_pending_operation(fresh.operation_id, ADMIN_ID, db_path)
    assert result.entity_id > 0
    assert database.fetch_one(
        "SELECT COUNT(*) AS n FROM production_entries", path=db_path
    )["n"] == 1


# ---------------------------------------------------------------------------
# Transaction rollback
# ---------------------------------------------------------------------------

def test_transaction_rolls_back_every_statement_on_failure(isolated_factory, actor):
    """A mid-transaction failure must leave no partial rows behind."""
    db_path, _ = isolated_factory
    before = database.fetch_one("SELECT COUNT(*) AS n FROM expenses", path=db_path)["n"]

    with pytest.raises(RuntimeError, match="interrupted"):
        with database.transaction(db_path) as conn:
            conn.execute(
                "INSERT INTO expenses (expense_type, amount, description, expense_date,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("Yarn", 10, "", "2026-07-01", database.utc_now(), database.utc_now()),
            )
            conn.execute(
                "INSERT INTO expenses (expense_type, amount, description, expense_date,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("Power", 20, "", "2026-07-02", database.utc_now(), database.utc_now()),
            )
            raise RuntimeError("interrupted")

    after = database.fetch_one("SELECT COUNT(*) AS n FROM expenses", path=db_path)["n"]
    assert after == before


def test_failed_validation_writes_no_row_and_no_audit_entry(isolated_factory, actor):
    db_path, _ = isolated_factory
    database.save_machine("M-01", "Running", "Operator", "", date(2026, 1, 1), actor, db_path)
    audit_before = database.fetch_one(
        "SELECT COUNT(*) AS n FROM audit_logs WHERE entity_type = 'production'", path=db_path
    )["n"]

    with pytest.raises(ValidationError):
        database.create_production(
            date(2026, 7, 2), "M-01", "Operator", "Cuff", -5, 3, actor, db_path
        )

    assert database.fetch_one(
        "SELECT COUNT(*) AS n FROM production_entries", path=db_path
    )["n"] == 0
    audit_after = database.fetch_one(
        "SELECT COUNT(*) AS n FROM audit_logs WHERE entity_type = 'production'", path=db_path
    )["n"]
    assert audit_after == audit_before


# ---------------------------------------------------------------------------
# Configuration diagnosability
# ---------------------------------------------------------------------------

def test_malformed_secrets_file_reports_itself(tmp_path, monkeypatch):
    """A TOML syntax error must not masquerade as "no token configured"."""
    import telegram_config

    secrets_dir = tmp_path / ".streamlit"
    secrets_dir.mkdir()
    (secrets_dir / "secrets.toml").write_text(
        '[telegram]\nbot_token = "unterminated\n', encoding="utf-8"
    )
    monkeypatch.setattr(telegram_config, "ROOT", tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    problem = telegram_config.telegram_config_problem()
    assert problem is not None
    assert "secrets.toml" in problem

    # The admin page must still render rather than raising.
    assert telegram_config.telegram_token_is_configured() is False


def test_missing_secrets_file_is_not_an_error(tmp_path, monkeypatch):
    """No secrets.toml is normal - the token may come from the environment."""
    import telegram_config

    monkeypatch.setattr(telegram_config, "ROOT", tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token-value")

    assert telegram_config.telegram_config_problem() is None
    assert telegram_config.telegram_token_is_configured() is True
