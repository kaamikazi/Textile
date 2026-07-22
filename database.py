from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from spreadsheet_sync import sync_factory_workbook


DB_PATH = Path(__file__).resolve().parent / "factory.db"


@contextmanager
def get_connection() -> Iterable[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def execute(query: str, params: tuple[Any, ...] = ()) -> None:
    with get_connection() as conn:
        conn.execute(query, params)


def fetch_df(query: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(query, params).fetchone()


def initialize_database() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS production_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                production_date TEXT NOT NULL,
                machine_number TEXT NOT NULL,
                operator_name TEXT NOT NULL,
                product_type TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity >= 0),
                rate_per_unit REAL NOT NULL CHECK(rate_per_unit >= 0),
                total_amount REAL NOT NULL CHECK(total_amount >= 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expense_type TEXT NOT NULL,
                amount REAL NOT NULL CHECK(amount >= 0),
                description TEXT,
                expense_date TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                phone TEXT,
                salary REAL NOT NULL DEFAULT 0,
                advance REAL NOT NULL DEFAULT 0,
                attendance_days INTEGER NOT NULL DEFAULT 0,
                performance_score REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'Active',
                joined_on TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                attendance_date TEXT NOT NULL,
                status TEXT NOT NULL,
                notes TEXT,
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            );

            CREATE TABLE IF NOT EXISTS machines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_number TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                assigned_operator TEXT,
                maintenance_notes TEXT,
                installed_on TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_type TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        migrate_legacy_production_table(conn)
    seed_database()


def migrate_legacy_production_table(conn: sqlite3.Connection) -> None:
    legacy_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'production'"
    ).fetchone()
    if not legacy_table:
        return

    conn.executescript(
        """
        INSERT INTO production_entries
        (production_date, machine_number, operator_name, product_type, quantity, rate_per_unit, total_amount, created_at)
        SELECT production_date, machine_number, operator_name, product_type, quantity, rate_per_unit, total_amount, created_at
        FROM production
        WHERE NOT EXISTS (
            SELECT 1
            FROM production_entries
            WHERE production_entries.production_date = production.production_date
              AND production_entries.machine_number = production.machine_number
              AND production_entries.operator_name = production.operator_name
              AND production_entries.product_type = production.product_type
              AND production_entries.quantity = production.quantity
              AND production_entries.rate_per_unit = production.rate_per_unit
              AND production_entries.total_amount = production.total_amount
              AND production_entries.created_at = production.created_at
        );
        DROP TABLE production;
        """
    )


def seed_database() -> None:
    table_counts = fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM production_entries) AS production_total,
            (SELECT COUNT(*) FROM expenses) AS expense_total,
            (SELECT COUNT(*) FROM employees) AS employee_total,
            (SELECT COUNT(*) FROM machines) AS machine_total
        """
    )
    if (
        table_counts["production_total"]
        and table_counts["expense_total"]
        and table_counts["employee_total"]
        and table_counts["machine_total"]
    ):
        return

    today = date.today()
    employees = [
        ("Md. Hasan", "Senior Operator", "+880171100001", 38000, 4000, 24, 94, "Active", "2024-02-01"),
        ("Arif Rahman", "Operator", "+880171100002", 32000, 2000, 22, 88, "Active", "2024-04-18"),
        ("Nusrat Jahan", "Quality Lead", "+880171100003", 42000, 0, 25, 96, "Active", "2023-11-09"),
        ("Tanvir Ahmed", "Mechanic", "+880171100004", 36000, 1500, 21, 84, "Active", "2025-01-16"),
    ]
    machines = [
        ("M-01", "Running", "Md. Hasan", "Needle set replaced last week.", "2023-08-15"),
        ("M-02", "Running", "Arif Rahman", "Stable output.", "2023-08-15"),
        ("M-03", "Maintenance", "Tanvir Ahmed", "Oil leak inspection pending.", "2023-09-02"),
        ("M-04", "Idle", "Unassigned", "Awaiting next order.", "2024-01-12"),
        ("M-05", "Running", "Nusrat Jahan", "Quality calibration completed.", "2024-05-03"),
    ]

    product_types = ["Rib Collar", "Cuff", "Jacquard Panel", "Flat Knit Body", "Neck Tape"]
    operators = [row[0] for row in employees]
    machine_numbers = [row[0] for row in machines]

    with get_connection() as conn:
        if not table_counts["employee_total"]:
            conn.executemany(
                """
                INSERT INTO employees
                (name, role, phone, salary, advance, attendance_days, performance_score, status, joined_on)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                employees,
            )
        if not table_counts["machine_total"]:
            conn.executemany(
                """
                INSERT INTO machines
                (machine_number, status, assigned_operator, maintenance_notes, installed_on)
                VALUES (?, ?, ?, ?, ?)
                """,
                machines,
            )

        if not table_counts["production_total"]:
            for offset in range(60):
                day = today - timedelta(days=offset)
                qty_base = 340 + (offset % 9) * 24
                rate = 8.5 + (offset % 4) * 1.25
                quantity = qty_base + (offset % 5) * 17
                total = quantity * rate
                conn.execute(
                    """
                    INSERT INTO production_entries
                    (production_date, machine_number, operator_name, product_type, quantity, rate_per_unit, total_amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        day.isoformat(),
                        machine_numbers[offset % len(machine_numbers)],
                        operators[offset % len(operators)],
                        product_types[offset % len(product_types)],
                        quantity,
                        rate,
                        total,
                    ),
                )

        expenses = [
            ("Yarn", 92000, "Fine cotton yarn purchase", today.isoformat()),
            ("Electricity", 28000, "Factory power bill", (today - timedelta(days=2)).isoformat()),
            ("Maintenance", 12500, "Machine M-03 service parts", (today - timedelta(days=4)).isoformat()),
            ("Salary", 148000, "Operator salary provision", (today - timedelta(days=7)).isoformat()),
            ("Transport", 7500, "Local delivery and loading", (today - timedelta(days=9)).isoformat()),
        ]
        if not table_counts["expense_total"]:
            conn.executemany(
                "INSERT INTO expenses (expense_type, amount, description, expense_date) VALUES (?, ?, ?, ?)",
                expenses,
            )

        activities = [
            ("Production", "Daily production posted", "M-01 completed rib collar batch."),
            ("Expense", "Maintenance expense logged", "Machine M-03 parts added."),
            ("Machine", "Machine status updated", "M-05 moved to running."),
            ("Employee", "Attendance updated", "24 active staff days recorded."),
        ]
        conn.executemany(
            "INSERT INTO activities (activity_type, title, detail) VALUES (?, ?, ?)",
            activities,
        )


def log_activity(activity_type: str, title: str, detail: str = "") -> None:
    execute(
        "INSERT INTO activities (activity_type, title, detail, created_at) VALUES (?, ?, ?, ?)",
        (activity_type, title, detail, datetime.now().isoformat(timespec="seconds")),
    )


def sync_spreadsheet_safely() -> None:
    try:
        sync_factory_workbook()
    except PermissionError:
        # Excel may have the workbook open. The database remains the source of truth.
        pass


def add_production(
    production_date: date,
    machine_number: str,
    operator_name: str,
    product_type: str,
    quantity: int,
    rate_per_unit: float,
) -> None:
    total_amount = quantity * rate_per_unit
    execute(
        """
        INSERT INTO production_entries
        (production_date, machine_number, operator_name, product_type, quantity, rate_per_unit, total_amount)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            production_date.isoformat(),
            machine_number,
            operator_name,
            product_type,
            quantity,
            rate_per_unit,
            total_amount,
        ),
    )
    log_activity("Production", "Production entry saved", f"{machine_number} produced {quantity} units.")
    sync_spreadsheet_safely()


def add_expense(expense_type: str, amount: float, description: str, expense_date: date) -> None:
    execute(
        "INSERT INTO expenses (expense_type, amount, description, expense_date) VALUES (?, ?, ?, ?)",
        (expense_type, amount, description, expense_date.isoformat()),
    )
    log_activity("Expense", "Expense recorded", f"{expense_type}: BDT {amount:,.0f}")
    sync_spreadsheet_safely()


def add_employee(
    name: str,
    role: str,
    phone: str,
    salary: float,
    advance: float,
    attendance_days: int,
    performance_score: float,
    status: str,
    joined_on: date,
) -> None:
    execute(
        """
        INSERT INTO employees
        (name, role, phone, salary, advance, attendance_days, performance_score, status, joined_on)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, role, phone, salary, advance, attendance_days, performance_score, status, joined_on.isoformat()),
    )
    log_activity("Employee", "Employee added", f"{name} joined as {role}.")
    sync_spreadsheet_safely()


def add_machine(
    machine_number: str,
    status: str,
    assigned_operator: str,
    maintenance_notes: str,
    installed_on: date,
) -> None:
    execute(
        """
        INSERT OR REPLACE INTO machines
        (machine_number, status, assigned_operator, maintenance_notes, installed_on)
        VALUES (?, ?, ?, ?, ?)
        """,
        (machine_number, status, assigned_operator, maintenance_notes, installed_on.isoformat()),
    )
    log_activity("Machine", "Machine profile saved", f"{machine_number} is {status}.")
    sync_spreadsheet_safely()


def record_attendance(employee_id: int, attendance_date: date, status: str, notes: str) -> None:
    execute(
        "INSERT INTO attendance (employee_id, attendance_date, status, notes) VALUES (?, ?, ?, ?)",
        (employee_id, attendance_date.isoformat(), status, notes),
    )
    log_activity("Attendance", "Attendance recorded", f"Employee #{employee_id}: {status}.")
