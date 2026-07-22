from __future__ import annotations

import sqlite3
from datetime import date

import pytest

import database
from database import ConflictError, ValidationError
from pages.dashboard import calculate_monthly_profit


def test_initialization_is_idempotent_and_creates_no_demo_rows(tmp_path):
    path = tmp_path / "empty.db"
    database.initialize_database(path)
    database.initialize_database(path)

    for table in ["production_entries", "expenses", "employees", "machines", "activities"]:
        row = database.fetch_one(f"SELECT COUNT(*) AS total FROM {table}", path=path)
        assert row["total"] == 0
    migrations = database.fetch_one(
        "SELECT COUNT(*) AS total FROM schema_migrations WHERE version = '1.1.0'", path=path
    )
    assert migrations["total"] == 1


def test_production_total_rounding_and_negative_validation(isolated_factory, actor):
    db_path, _ = isolated_factory
    result = database.create_production(
        date(2026, 7, 2), "M-01", "Operator", "Collar", 3, "1.335", actor, db_path
    )
    row = database.fetch_one("SELECT * FROM production_entries WHERE id = ?", (result.entity_id,), db_path)
    assert row["rate_per_unit"] == 1.34
    assert row["total_amount"] == 4.02

    with pytest.raises(ValidationError):
        database.create_production(
            date(2026, 7, 2), "M-01", "Operator", "Collar", -1, 2, actor, db_path
        )
    with pytest.raises(ValidationError):
        database.create_expense("Fuel", -0.01, "", date(2026, 7, 2), actor, db_path)
    with pytest.raises(ValidationError):
        database.create_employee(
            "Worker", "Operator", "", -1, 0, 50, "Active", date(2026, 1, 1), actor, db_path
        )


def test_monthly_profit_only_uses_selected_month():
    import pandas as pd

    production = pd.DataFrame(
        {"production_date": ["2026-06-30", "2026-07-01"], "total_amount": [900, 500]}
    )
    expenses = pd.DataFrame(
        {"expense_date": ["2026-06-30", "2026-07-03"], "amount": [100, 175]}
    )
    earnings, spending, profit = calculate_monthly_profit(production, expenses, "2026-07")
    assert (earnings, spending, profit) == (500.0, 175.0, 325.0)


def test_duplicate_attendance_and_foreign_keys(isolated_factory, actor):
    db_path, _ = isolated_factory
    employee = database.create_employee(
        "Worker", "Operator", "", 1000, 0, 75, "Active", date(2026, 1, 1), actor, db_path
    )
    database.create_attendance(employee.entity_id, "2026-07-01", "Present", "", actor, db_path)
    with pytest.raises(ConflictError):
        database.create_attendance(employee.entity_id, "2026-07-01", "Late", "", actor, db_path)

    with database.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO attendance(employee_id, attendance_date, status) VALUES (9999, '2026-07-02', 'Present')"
            )


def test_machine_update_preserves_id(isolated_factory, actor):
    db_path, _ = isolated_factory
    created = database.save_machine("M-09", "Idle", "A", "", "2026-01-01", actor, db_path)
    updated = database.save_machine("m-09", "Running", "B", "Checked", "2026-01-01", actor, db_path)
    assert updated.entity_id == created.entity_id
    row = database.fetch_one("SELECT id, status, assigned_operator FROM machines", path=db_path)
    assert dict(row) == {"id": created.entity_id, "status": "Running", "assigned_operator": "B"}


def test_mutations_log_audit_without_secrets(isolated_factory, actor):
    db_path, _ = isolated_factory
    result = database.create_expense("Power", 10, "meter", "2026-07-01", actor, db_path)
    database.update_expense(result.entity_id, "Power", 12, "meter 2", "2026-07-01", actor, db_path)
    database.delete_expense(result.entity_id, actor, db_path)
    logs = database.fetch_df("SELECT * FROM audit_logs ORDER BY id", path=db_path)
    assert logs["action"].tolist() == ["create", "update", "delete"]
    assert "password" not in logs.to_json().lower()
    assert "secret" not in logs.to_json().lower()
