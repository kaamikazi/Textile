from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Iterator

import pandas as pd


ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("AL_SADI_DB_PATH", ROOT / "factory.db"))
MONEY_PLACES = Decimal("0.01")
ATTENDANCE_STATUSES = {"Present", "Absent", "Leave", "Late"}
EMPLOYEE_STATUSES = {"Active", "On Leave", "Inactive", "Archived"}
MACHINE_STATUSES = {"Running", "Idle", "Maintenance", "Offline"}


class FactoryError(Exception):
    """Base exception for friendly application errors."""


class ValidationError(FactoryError):
    pass


class ConflictError(FactoryError):
    pass


class NotFoundError(FactoryError):
    pass


@dataclass(frozen=True)
class Actor:
    username: str
    role: str


@dataclass(frozen=True)
class MutationResult:
    entity_id: int
    sync_status: str
    sync_message: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else DB_PATH


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(path), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


@contextmanager
def get_connection(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_df(query: str, params: tuple[Any, ...] = (), path: str | Path | None = None) -> pd.DataFrame:
    with get_connection(path) as conn:
        return pd.read_sql_query(query, conn, params=params)


def fetch_one(
    query: str,
    params: tuple[Any, ...] = (),
    path: str | Path | None = None,
) -> sqlite3.Row | None:
    with get_connection(path) as conn:
        return conn.execute(query, params).fetchone()


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    column = definition.split()[0]
    if column not in _column_names(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def initialize_database(path: str | Path | None = None) -> None:
    """Create/migrate schema only. Never inserts demo or activity records."""
    with transaction(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS production_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                production_date TEXT NOT NULL,
                machine_number TEXT NOT NULL,
                operator_name TEXT NOT NULL,
                product_type TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                rate_per_unit REAL NOT NULL CHECK(rate_per_unit >= 0),
                total_amount REAL NOT NULL CHECK(total_amount >= 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expense_type TEXT NOT NULL,
                amount REAL NOT NULL CHECK(amount >= 0),
                description TEXT,
                expense_date TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                phone TEXT,
                salary REAL NOT NULL DEFAULT 0 CHECK(salary >= 0),
                advance REAL NOT NULL DEFAULT 0 CHECK(advance >= 0),
                attendance_days INTEGER NOT NULL DEFAULT 0,
                performance_score REAL NOT NULL DEFAULT 0 CHECK(performance_score BETWEEN 0 AND 100),
                status TEXT NOT NULL DEFAULT 'Active',
                joined_on TEXT NOT NULL,
                archived_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                attendance_date TEXT NOT NULL,
                status TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE RESTRICT,
                UNIQUE(employee_id, attendance_date)
            );

            CREATE TABLE IF NOT EXISTS machines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_number TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                assigned_operator TEXT,
                maintenance_notes TEXT,
                installed_on TEXT NOT NULL,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_type TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT,
                description TEXT NOT NULL,
                before_state_json TEXT,
                after_state_json TEXT
            );

            CREATE TABLE IF NOT EXISTS auth_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('Admin', 'Staff')),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS excel_sync_status (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                status TEXT NOT NULL CHECK(status IN ('Synced', 'Out of date', 'Failed')),
                last_attempt_at TEXT,
                last_success_at TEXT,
                message TEXT
            );

            CREATE TABLE IF NOT EXISTS attendance_duplicate_archive (
                original_id INTEGER NOT NULL,
                employee_id INTEGER NOT NULL,
                attendance_date TEXT NOT NULL,
                status TEXT NOT NULL,
                notes TEXT,
                archived_at TEXT NOT NULL,
                reason TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )

        for table, definitions in {
            "production_entries": ["updated_at TEXT"],
            "expenses": ["updated_at TEXT"],
            "employees": ["archived_at TEXT", "updated_at TEXT"],
            "attendance": ["created_at TEXT", "updated_at TEXT"],
            "machines": ["updated_at TEXT"],
        }.items():
            for definition in definitions:
                _ensure_column(conn, table, definition)

        duplicate_rows = conn.execute(
            """
            SELECT a.* FROM attendance a
            JOIN (
                SELECT employee_id, attendance_date, MAX(id) AS keep_id, COUNT(*) AS row_count
                FROM attendance
                GROUP BY employee_id, attendance_date
                HAVING COUNT(*) > 1
            ) d ON d.employee_id = a.employee_id
               AND d.attendance_date = a.attendance_date
               AND a.id <> d.keep_id
            """
        ).fetchall()
        for row in duplicate_rows:
            conn.execute(
                """
                INSERT INTO attendance_duplicate_archive
                (original_id, employee_id, attendance_date, status, notes, archived_at, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"], row["employee_id"], row["attendance_date"], row["status"],
                    row["notes"], utc_now(), "Duplicate employee/date archived during v1.1 migration",
                ),
            )
            conn.execute("DELETE FROM attendance WHERE id = ?", (row["id"],))

        conn.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_attendance_employee_date
                ON attendance(employee_id, attendance_date);
            CREATE INDEX IF NOT EXISTS ix_production_date ON production_entries(production_date);
            CREATE INDEX IF NOT EXISTS ix_production_machine ON production_entries(machine_number);
            CREATE INDEX IF NOT EXISTS ix_expenses_date ON expenses(expense_date);
            CREATE INDEX IF NOT EXISTS ix_attendance_date ON attendance(attendance_date);
            CREATE INDEX IF NOT EXISTS ix_attendance_employee ON attendance(employee_id);
            CREATE INDEX IF NOT EXISTS ix_machines_number ON machines(machine_number);
            CREATE INDEX IF NOT EXISTS ix_activities_recent ON activities(created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS ix_audit_recent ON audit_logs(timestamp DESC, id DESC);
            CREATE INDEX IF NOT EXISTS ix_audit_user ON audit_logs(username);
            CREATE INDEX IF NOT EXISTS ix_audit_entity ON audit_logs(entity_type, entity_id);
            INSERT OR IGNORE INTO excel_sync_status(id, status, message)
                VALUES (1, 'Out of date', 'Workbook has not been synchronized by v1.1 yet.');
            INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES ('1.1.0', CURRENT_TIMESTAMP);
            """
        )


def _require_text(value: str, label: str, max_length: int = 200) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValidationError(f"{label} is required.")
    if len(cleaned) > max_length:
        raise ValidationError(f"{label} must be {max_length} characters or fewer.")
    return cleaned


def _require_date(value: date | str, label: str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be a valid date.") from exc


def round_money(value: Any, label: str = "Amount", allow_zero: bool = True) -> float:
    try:
        amount = Decimal(str(value)).quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be a valid number.") from exc
    if amount < 0 or (not allow_zero and amount == 0):
        condition = "zero or greater" if allow_zero else "greater than zero"
        raise ValidationError(f"{label} must be {condition}.")
    return float(amount)


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _json_state(state: dict[str, Any] | None) -> str | None:
    return json.dumps(state, sort_keys=True, default=str) if state is not None else None


def _log_activity(conn: sqlite3.Connection, activity_type: str, title: str, detail: str) -> None:
    conn.execute(
        "INSERT INTO activities(activity_type, title, detail, created_at) VALUES (?, ?, ?, ?)",
        (activity_type, title, detail, utc_now()),
    )


def write_audit_log(
    conn: sqlite3.Connection,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: int | str | None,
    description: str,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_logs
        (timestamp, username, role, action, entity_type, entity_id, description,
         before_state_json, after_state_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now(), actor.username, actor.role, action, entity_type,
            str(entity_id) if entity_id is not None else None, description,
            _json_state(before_state), _json_state(after_state),
        ),
    )


def _mark_excel_out_of_date(conn: sqlite3.Connection, message: str) -> None:
    conn.execute(
        """
        UPDATE excel_sync_status
        SET status = 'Out of date', last_attempt_at = ?, message = ?
        WHERE id = 1
        """,
        (utc_now(), message),
    )


def set_excel_sync_status(status: str, message: str, path: str | Path | None = None) -> None:
    if status not in {"Synced", "Out of date", "Failed"}:
        raise ValueError("Invalid Excel sync status")
    now = utc_now()
    with transaction(path) as conn:
        conn.execute(
            """
            UPDATE excel_sync_status
            SET status = ?, last_attempt_at = ?,
                last_success_at = CASE WHEN ? = 'Synced' THEN ? ELSE last_success_at END,
                message = ?
            WHERE id = 1
            """,
            (status, now, status, now, message),
        )


def get_excel_sync_status(path: str | Path | None = None) -> dict[str, Any]:
    row = fetch_one("SELECT * FROM excel_sync_status WHERE id = 1", path=path)
    return dict(row) if row else {
        "status": "Out of date", "last_attempt_at": None,
        "last_success_at": None, "message": "No synchronization attempt recorded.",
    }


def _sync_after_commit(path: str | Path | None = None) -> tuple[str, str]:
    db_path = _db_path(path)
    try:
        from spreadsheet_sync import sync_factory_workbook

        sync_factory_workbook(db_path=db_path)
        message = "Excel workbook synchronized successfully."
        set_excel_sync_status("Synced", message, db_path)
        return "Synced", message
    except PermissionError:
        message = "Excel sync failed because factory_records.xlsx is open. Close Excel and retry."
        set_excel_sync_status("Failed", message, db_path)
        return "Failed", message
    except Exception as exc:
        message = f"Database saved, but Excel sync failed: {exc}"
        set_excel_sync_status("Failed", message, db_path)
        return "Failed", message


def _mutation_result(entity_id: int, path: str | Path | None = None) -> MutationResult:
    status, message = _sync_after_commit(path)
    return MutationResult(entity_id, status, message)


def create_production(
    production_date: date | str,
    machine_number: str,
    operator_name: str,
    product_type: str,
    quantity: int,
    rate_per_unit: Any,
    actor: Actor,
    path: str | Path | None = None,
) -> MutationResult:
    day = _require_date(production_date, "Production date")
    machine = _require_text(machine_number, "Machine number", 50)
    operator = _require_text(operator_name, "Operator name", 120)
    product = _require_text(product_type, "Product type", 120)
    if not isinstance(quantity, int) or quantity <= 0:
        raise ValidationError("Quantity must be a positive whole number.")
    rate = round_money(rate_per_unit, "Rate per unit", allow_zero=False)
    total = round_money(Decimal(quantity) * Decimal(str(rate)), "Total amount")
    now = utc_now()
    with transaction(path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO production_entries
            (production_date, machine_number, operator_name, product_type, quantity,
             rate_per_unit, total_amount, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (day, machine, operator, product, quantity, rate, total, now),
        )
        entity_id = int(cursor.lastrowid)
        after = _row_dict(conn.execute("SELECT * FROM production_entries WHERE id = ?", (entity_id,)).fetchone())
        detail = f"#{entity_id} {machine}: {quantity} {product} units by {operator}."
        _log_activity(conn, "Production", "Production entry created", detail)
        write_audit_log(conn, actor, "create", "production", entity_id, detail, after_state=after)
        _mark_excel_out_of_date(conn, "Production entry changed after the last sync.")
    return _mutation_result(entity_id, path)


def update_production(
    entity_id: int,
    production_date: date | str,
    machine_number: str,
    operator_name: str,
    product_type: str,
    quantity: int,
    rate_per_unit: Any,
    actor: Actor,
    path: str | Path | None = None,
) -> MutationResult:
    day = _require_date(production_date, "Production date")
    machine = _require_text(machine_number, "Machine number", 50)
    operator = _require_text(operator_name, "Operator name", 120)
    product = _require_text(product_type, "Product type", 120)
    if not isinstance(quantity, int) or quantity <= 0:
        raise ValidationError("Quantity must be a positive whole number.")
    rate = round_money(rate_per_unit, "Rate per unit", allow_zero=False)
    total = round_money(Decimal(quantity) * Decimal(str(rate)), "Total amount")
    with transaction(path) as conn:
        before_row = conn.execute("SELECT * FROM production_entries WHERE id = ?", (entity_id,)).fetchone()
        if before_row is None:
            raise NotFoundError("Production entry was not found.")
        before = _row_dict(before_row)
        conn.execute(
            """
            UPDATE production_entries
            SET production_date = ?, machine_number = ?, operator_name = ?, product_type = ?,
                quantity = ?, rate_per_unit = ?, total_amount = ?, updated_at = ?
            WHERE id = ?
            """,
            (day, machine, operator, product, quantity, rate, total, utc_now(), entity_id),
        )
        after = _row_dict(conn.execute("SELECT * FROM production_entries WHERE id = ?", (entity_id,)).fetchone())
        detail = f"Production #{entity_id} updated: {machine}, {quantity} {product} units."
        _log_activity(conn, "Production", "Production entry updated", detail)
        write_audit_log(conn, actor, "update", "production", entity_id, detail, before, after)
        _mark_excel_out_of_date(conn, "Production entry changed after the last sync.")
    return _mutation_result(entity_id, path)


def delete_production(entity_id: int, actor: Actor, path: str | Path | None = None) -> MutationResult:
    with transaction(path) as conn:
        before_row = conn.execute("SELECT * FROM production_entries WHERE id = ?", (entity_id,)).fetchone()
        if before_row is None:
            raise NotFoundError("Production entry was not found.")
        before = _row_dict(before_row)
        conn.execute("DELETE FROM production_entries WHERE id = ?", (entity_id,))
        detail = f"Production #{entity_id} deleted ({before['machine_number']}, {before['quantity']} units)."
        _log_activity(conn, "Production", "Production entry deleted", detail)
        write_audit_log(conn, actor, "delete", "production", entity_id, detail, before_state=before)
        _mark_excel_out_of_date(conn, "Production entry changed after the last sync.")
    return _mutation_result(entity_id, path)


def create_expense(
    expense_type: str,
    amount: Any,
    description: str,
    expense_date: date | str,
    actor: Actor,
    path: str | Path | None = None,
) -> MutationResult:
    category = _require_text(expense_type, "Expense type", 100)
    value = round_money(amount, "Amount", allow_zero=False)
    day = _require_date(expense_date, "Expense date")
    note = (description or "").strip()[:500]
    with transaction(path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO expenses(expense_type, amount, description, expense_date, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (category, value, note, day, utc_now()),
        )
        entity_id = int(cursor.lastrowid)
        after = _row_dict(conn.execute("SELECT * FROM expenses WHERE id = ?", (entity_id,)).fetchone())
        detail = f"Expense #{entity_id} {category}: BDT {value:,.2f}."
        _log_activity(conn, "Expense", "Expense created", detail)
        write_audit_log(conn, actor, "create", "expense", entity_id, detail, after_state=after)
        _mark_excel_out_of_date(conn, "Expense changed after the last sync.")
    return _mutation_result(entity_id, path)


def update_expense(
    entity_id: int,
    expense_type: str,
    amount: Any,
    description: str,
    expense_date: date | str,
    actor: Actor,
    path: str | Path | None = None,
) -> MutationResult:
    category = _require_text(expense_type, "Expense type", 100)
    value = round_money(amount, "Amount", allow_zero=False)
    day = _require_date(expense_date, "Expense date")
    note = (description or "").strip()[:500]
    with transaction(path) as conn:
        before_row = conn.execute("SELECT * FROM expenses WHERE id = ?", (entity_id,)).fetchone()
        if before_row is None:
            raise NotFoundError("Expense was not found.")
        before = _row_dict(before_row)
        conn.execute(
            """
            UPDATE expenses
            SET expense_type = ?, amount = ?, description = ?, expense_date = ?, updated_at = ?
            WHERE id = ?
            """,
            (category, value, note, day, utc_now(), entity_id),
        )
        after = _row_dict(conn.execute("SELECT * FROM expenses WHERE id = ?", (entity_id,)).fetchone())
        detail = f"Expense #{entity_id} updated: {category}, BDT {value:,.2f}."
        _log_activity(conn, "Expense", "Expense updated", detail)
        write_audit_log(conn, actor, "update", "expense", entity_id, detail, before, after)
        _mark_excel_out_of_date(conn, "Expense changed after the last sync.")
    return _mutation_result(entity_id, path)


def delete_expense(entity_id: int, actor: Actor, path: str | Path | None = None) -> MutationResult:
    with transaction(path) as conn:
        before_row = conn.execute("SELECT * FROM expenses WHERE id = ?", (entity_id,)).fetchone()
        if before_row is None:
            raise NotFoundError("Expense was not found.")
        before = _row_dict(before_row)
        conn.execute("DELETE FROM expenses WHERE id = ?", (entity_id,))
        detail = f"Expense #{entity_id} deleted ({before['expense_type']}, BDT {before['amount']:,.2f})."
        _log_activity(conn, "Expense", "Expense deleted", detail)
        write_audit_log(conn, actor, "delete", "expense", entity_id, detail, before_state=before)
        _mark_excel_out_of_date(conn, "Expense changed after the last sync.")
    return _mutation_result(entity_id, path)


def create_employee(
    name: str,
    role: str,
    phone: str,
    salary: Any,
    advance: Any,
    performance_score: Any,
    status: str,
    joined_on: date | str,
    actor: Actor,
    path: str | Path | None = None,
) -> MutationResult:
    employee_name = _require_text(name, "Employee name", 120)
    employee_role = _require_text(role, "Role", 120)
    salary_value = round_money(salary, "Salary")
    advance_value = round_money(advance, "Advance")
    try:
        performance = float(performance_score)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Performance score must be a number.") from exc
    if not 0 <= performance <= 100:
        raise ValidationError("Performance score must be between 0 and 100.")
    if status not in EMPLOYEE_STATUSES - {"Archived"}:
        raise ValidationError("Invalid employee status.")
    day = _require_date(joined_on, "Joined date")
    with transaction(path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO employees
            (name, role, phone, salary, advance, performance_score, status, joined_on)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (employee_name, employee_role, (phone or "").strip()[:50], salary_value,
             advance_value, performance, status, day),
        )
        entity_id = int(cursor.lastrowid)
        after = _row_dict(conn.execute("SELECT * FROM employees WHERE id = ?", (entity_id,)).fetchone())
        detail = f"Employee #{entity_id} {employee_name} created as {employee_role}."
        _log_activity(conn, "Employee", "Employee created", detail)
        write_audit_log(conn, actor, "create", "employee", entity_id, detail, after_state=after)
        _mark_excel_out_of_date(conn, "Employee changed after the last sync.")
    return _mutation_result(entity_id, path)


def update_employee(
    entity_id: int,
    name: str,
    role: str,
    phone: str,
    salary: Any,
    advance: Any,
    performance_score: Any,
    status: str,
    joined_on: date | str,
    actor: Actor,
    path: str | Path | None = None,
) -> MutationResult:
    employee_name = _require_text(name, "Employee name", 120)
    employee_role = _require_text(role, "Role", 120)
    salary_value = round_money(salary, "Salary")
    advance_value = round_money(advance, "Advance")
    try:
        performance = float(performance_score)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Performance score must be a number.") from exc
    if not 0 <= performance <= 100:
        raise ValidationError("Performance score must be between 0 and 100.")
    if status not in EMPLOYEE_STATUSES - {"Archived"}:
        raise ValidationError("Invalid employee status.")
    day = _require_date(joined_on, "Joined date")
    with transaction(path) as conn:
        before_row = conn.execute("SELECT * FROM employees WHERE id = ?", (entity_id,)).fetchone()
        if before_row is None:
            raise NotFoundError("Employee was not found.")
        before = _row_dict(before_row)
        conn.execute(
            """
            UPDATE employees
            SET name = ?, role = ?, phone = ?, salary = ?, advance = ?, performance_score = ?,
                status = ?, joined_on = ?, updated_at = ?
            WHERE id = ?
            """,
            (employee_name, employee_role, (phone or "").strip()[:50], salary_value,
             advance_value, performance, status, day, utc_now(), entity_id),
        )
        after = _row_dict(conn.execute("SELECT * FROM employees WHERE id = ?", (entity_id,)).fetchone())
        detail = f"Employee #{entity_id} {employee_name} updated."
        _log_activity(conn, "Employee", "Employee updated", detail)
        write_audit_log(conn, actor, "update", "employee", entity_id, detail, before, after)
        _mark_excel_out_of_date(conn, "Employee changed after the last sync.")
    return _mutation_result(entity_id, path)


def archive_employee(entity_id: int, actor: Actor, path: str | Path | None = None) -> MutationResult:
    with transaction(path) as conn:
        before_row = conn.execute("SELECT * FROM employees WHERE id = ?", (entity_id,)).fetchone()
        if before_row is None:
            raise NotFoundError("Employee was not found.")
        before = _row_dict(before_row)
        now = utc_now()
        conn.execute(
            "UPDATE employees SET status = 'Archived', archived_at = ?, updated_at = ? WHERE id = ?",
            (now, now, entity_id),
        )
        after = _row_dict(conn.execute("SELECT * FROM employees WHERE id = ?", (entity_id,)).fetchone())
        detail = f"Employee #{entity_id} {before['name']} archived; attendance history retained."
        _log_activity(conn, "Employee", "Employee archived", detail)
        write_audit_log(conn, actor, "archive", "employee", entity_id, detail, before, after)
        _mark_excel_out_of_date(conn, "Employee changed after the last sync.")
    return _mutation_result(entity_id, path)


def save_machine(
    machine_number: str,
    status: str,
    assigned_operator: str,
    maintenance_notes: str,
    installed_on: date | str,
    actor: Actor,
    path: str | Path | None = None,
) -> MutationResult:
    number = _require_text(machine_number, "Machine number", 50).upper()
    if status not in MACHINE_STATUSES:
        raise ValidationError("Invalid machine status.")
    day = _require_date(installed_on, "Installed date")
    operator = (assigned_operator or "").strip()[:120]
    notes = (maintenance_notes or "").strip()[:1000]
    with transaction(path) as conn:
        before_row = conn.execute("SELECT * FROM machines WHERE machine_number = ?", (number,)).fetchone()
        if before_row:
            entity_id = int(before_row["id"])
            before = _row_dict(before_row)
            conn.execute(
                """
                UPDATE machines
                SET status = ?, assigned_operator = ?, maintenance_notes = ?, installed_on = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, operator, notes, day, utc_now(), entity_id),
            )
            action, title = "update", "Machine updated"
        else:
            cursor = conn.execute(
                """
                INSERT INTO machines
                (machine_number, status, assigned_operator, maintenance_notes, installed_on)
                VALUES (?, ?, ?, ?, ?)
                """,
                (number, status, operator, notes, day),
            )
            entity_id = int(cursor.lastrowid)
            before = None
            action, title = "create", "Machine created"
        after = _row_dict(conn.execute("SELECT * FROM machines WHERE id = ?", (entity_id,)).fetchone())
        detail = f"Machine #{entity_id} {number} saved with status {status}."
        _log_activity(conn, "Machine", title, detail)
        write_audit_log(conn, actor, action, "machine", entity_id, detail, before, after)
        _mark_excel_out_of_date(conn, "Machine changed after the last sync.")
    return _mutation_result(entity_id, path)


def delete_machine(entity_id: int, actor: Actor, path: str | Path | None = None) -> MutationResult:
    with transaction(path) as conn:
        before_row = conn.execute("SELECT * FROM machines WHERE id = ?", (entity_id,)).fetchone()
        if before_row is None:
            raise NotFoundError("Machine was not found.")
        before = _row_dict(before_row)
        conn.execute("DELETE FROM machines WHERE id = ?", (entity_id,))
        detail = f"Machine #{entity_id} {before['machine_number']} deleted. Historical production remains intact."
        _log_activity(conn, "Machine", "Machine deleted", detail)
        write_audit_log(conn, actor, "delete", "machine", entity_id, detail, before_state=before)
        _mark_excel_out_of_date(conn, "Machine changed after the last sync.")
    return _mutation_result(entity_id, path)


def create_attendance(
    employee_id: int,
    attendance_date: date | str,
    status: str,
    notes: str,
    actor: Actor,
    path: str | Path | None = None,
) -> MutationResult:
    if status not in ATTENDANCE_STATUSES:
        raise ValidationError("Invalid attendance status.")
    day = _require_date(attendance_date, "Attendance date")
    note = (notes or "").strip()[:500]
    try:
        with transaction(path) as conn:
            employee = conn.execute(
                "SELECT id, name, status FROM employees WHERE id = ?", (employee_id,)
            ).fetchone()
            if employee is None or employee["status"] == "Archived":
                raise NotFoundError("Active employee was not found.")
            cursor = conn.execute(
                """
                INSERT INTO attendance(employee_id, attendance_date, status, notes, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (employee_id, day, status, note, utc_now()),
            )
            entity_id = int(cursor.lastrowid)
            after = _row_dict(conn.execute("SELECT * FROM attendance WHERE id = ?", (entity_id,)).fetchone())
            detail = f"Attendance #{entity_id}: {employee['name']} marked {status} on {day}."
            _log_activity(conn, "Attendance", "Attendance created", detail)
            write_audit_log(conn, actor, "create", "attendance", entity_id, detail, after_state=after)
            _mark_excel_out_of_date(conn, "Attendance changed after the last sync.")
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc).upper():
            raise ConflictError("Attendance already exists for this employee and date.") from exc
        raise ValidationError("Attendance could not be saved because of a database constraint.") from exc
    return _mutation_result(entity_id, path)


def update_attendance(
    entity_id: int,
    employee_id: int,
    attendance_date: date | str,
    status: str,
    notes: str,
    actor: Actor,
    path: str | Path | None = None,
) -> MutationResult:
    if status not in ATTENDANCE_STATUSES:
        raise ValidationError("Invalid attendance status.")
    day = _require_date(attendance_date, "Attendance date")
    note = (notes or "").strip()[:500]
    try:
        with transaction(path) as conn:
            before_row = conn.execute("SELECT * FROM attendance WHERE id = ?", (entity_id,)).fetchone()
            if before_row is None:
                raise NotFoundError("Attendance entry was not found.")
            employee = conn.execute(
                "SELECT id, name, status FROM employees WHERE id = ?", (employee_id,)
            ).fetchone()
            if employee is None or employee["status"] == "Archived":
                raise NotFoundError("Active employee was not found.")
            before = _row_dict(before_row)
            conn.execute(
                """
                UPDATE attendance
                SET employee_id = ?, attendance_date = ?, status = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (employee_id, day, status, note, utc_now(), entity_id),
            )
            after = _row_dict(conn.execute("SELECT * FROM attendance WHERE id = ?", (entity_id,)).fetchone())
            detail = f"Attendance #{entity_id} updated: {employee['name']} {status} on {day}."
            _log_activity(conn, "Attendance", "Attendance updated", detail)
            write_audit_log(conn, actor, "update", "attendance", entity_id, detail, before, after)
            _mark_excel_out_of_date(conn, "Attendance changed after the last sync.")
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc).upper():
            raise ConflictError("Attendance already exists for this employee and date.") from exc
        raise ValidationError("Attendance could not be updated because of a database constraint.") from exc
    return _mutation_result(entity_id, path)


def delete_attendance(entity_id: int, actor: Actor, path: str | Path | None = None) -> MutationResult:
    with transaction(path) as conn:
        before_row = conn.execute(
            """
            SELECT a.*, e.name AS employee_name
            FROM attendance a JOIN employees e ON e.id = a.employee_id
            WHERE a.id = ?
            """,
            (entity_id,),
        ).fetchone()
        if before_row is None:
            raise NotFoundError("Attendance entry was not found.")
        before = _row_dict(before_row)
        conn.execute("DELETE FROM attendance WHERE id = ?", (entity_id,))
        detail = f"Attendance #{entity_id} deleted: {before['employee_name']} on {before['attendance_date']}."
        _log_activity(conn, "Attendance", "Attendance deleted", detail)
        write_audit_log(conn, actor, "delete", "attendance", entity_id, detail, before_state=before)
        _mark_excel_out_of_date(conn, "Attendance changed after the last sync.")
    return _mutation_result(entity_id, path)


def attendance_history(
    employee_id: int | None = None,
    month: str | None = None,
    status: str | None = None,
    path: str | Path | None = None,
) -> pd.DataFrame:
    clauses: list[str] = []
    params: list[Any] = []
    if employee_id:
        clauses.append("a.employee_id = ?")
        params.append(employee_id)
    if month:
        clauses.append("substr(a.attendance_date, 1, 7) = ?")
        params.append(month)
    if status and status != "All":
        clauses.append("a.status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return fetch_df(
        f"""
        SELECT a.id, a.employee_id, e.name AS employee_name, a.attendance_date,
               a.status, a.notes, a.created_at, a.updated_at
        FROM attendance a
        JOIN employees e ON e.id = a.employee_id
        {where}
        ORDER BY a.attendance_date DESC, a.id DESC
        """,
        tuple(params), path,
    )


def attendance_monthly_totals(month: str, path: str | Path | None = None) -> pd.DataFrame:
    return fetch_df(
        """
        SELECT e.id AS employee_id, e.name,
               SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) AS present,
               SUM(CASE WHEN a.status = 'Absent' THEN 1 ELSE 0 END) AS absent,
               SUM(CASE WHEN a.status = 'Leave' THEN 1 ELSE 0 END) AS leave,
               SUM(CASE WHEN a.status = 'Late' THEN 1 ELSE 0 END) AS late,
               COUNT(a.id) AS recorded_days
        FROM employees e
        LEFT JOIN attendance a
          ON a.employee_id = e.id AND substr(a.attendance_date, 1, 7) = ?
        WHERE e.status <> 'Archived'
        GROUP BY e.id, e.name
        ORDER BY e.name
        """,
        (month,), path,
    )


def cleanup_duplicate_seed_activities(actor: Actor, path: str | Path | None = None) -> int:
    known = [
        ("Production", "Daily production posted", "M-01 completed rib collar batch."),
        ("Expense", "Maintenance expense logged", "Machine M-03 parts added."),
        ("Machine", "Machine status updated", "M-05 moved to running."),
        ("Employee", "Attendance updated", "24 active staff days recorded."),
    ]
    removed = 0
    with transaction(path) as conn:
        for activity_type, title, detail in known:
            ids = [
                row["id"] for row in conn.execute(
                    """
                    SELECT id FROM activities
                    WHERE activity_type = ? AND title = ? AND detail = ?
                    ORDER BY id
                    """,
                    (activity_type, title, detail),
                )
            ]
            for duplicate_id in ids[1:]:
                conn.execute("DELETE FROM activities WHERE id = ?", (duplicate_id,))
                removed += 1
        write_audit_log(
            conn, actor, "cleanup", "activity", None,
            f"Removed {removed} clearly duplicated legacy seed activities.",
        )
    return removed


def operational_record_count(path: str | Path | None = None) -> int:
    row = fetch_one(
        """
        SELECT
          (SELECT COUNT(*) FROM production_entries) +
          (SELECT COUNT(*) FROM expenses) +
          (SELECT COUNT(*) FROM employees) +
          (SELECT COUNT(*) FROM machines) +
          (SELECT COUNT(*) FROM attendance) AS total
        """,
        path=path,
    )
    return int(row["total"] if row else 0)


def initialize_demo_data(actor: Actor, path: str | Path | None = None) -> MutationResult:
    if operational_record_count(path) > 0:
        raise ConflictError("Demo data cannot be inserted because operational records already exist.")
    with transaction(path) as conn:
        employee_id = int(conn.execute(
            """
            INSERT INTO employees(name, role, phone, salary, advance, performance_score, status, joined_on)
            VALUES ('Demo Operator', 'Operator', '', 30000, 0, 85, 'Active', ?)
            """,
            (date.today().isoformat(),),
        ).lastrowid)
        conn.execute(
            """
            INSERT INTO machines(machine_number, status, assigned_operator, maintenance_notes, installed_on)
            VALUES ('DEMO-01', 'Running', 'Demo Operator', 'Demo mode machine', ?)
            """,
            (date.today().isoformat(),),
        )
        conn.execute(
            """
            INSERT INTO production_entries
            (production_date, machine_number, operator_name, product_type, quantity,
             rate_per_unit, total_amount, created_at)
            VALUES (?, 'DEMO-01', 'Demo Operator', 'Demo Sample', 100, 10, 1000, ?)
            """,
            (date.today().isoformat(), utc_now()),
        )
        conn.execute(
            """
            INSERT INTO expenses(expense_type, amount, description, expense_date, created_at)
            VALUES ('Demo Expense', 100, 'Demo mode only', ?, ?)
            """,
            (date.today().isoformat(), utc_now()),
        )
        detail = "Explicit demo dataset initialized after confirmation."
        _log_activity(conn, "System", "Demo data initialized", detail)
        write_audit_log(conn, actor, "create", "demo_data", employee_id, detail)
        _mark_excel_out_of_date(conn, "Demo data created after the last sync.")
    return _mutation_result(employee_id, path)


# Backward-compatible aliases for imports while pages migrate to v1.1 names.
add_production = create_production
add_expense = create_expense
add_employee = create_employee
add_machine = save_machine
record_attendance = create_attendance
