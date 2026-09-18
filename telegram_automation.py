from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import database
from database import (
    Actor,
    ConflictError,
    NotFoundError,
    ValidationError,
    fetch_df,
    fetch_one,
    get_excel_sync_status,
    round_money,
    transaction,
    utc_now,
    write_audit_log,
)

TELEGRAM_ROLES = {"Admin", "Staff"}
TELEGRAM_USER_STATUSES = {"Active", "Suspended", "Removed"}
TELEGRAM_MACHINE_STATUSES = {"Running", "Idle", "Maintenance", "Out of Service"}
PENDING_TTL_MINUTES = 20


class TelegramAccessError(ValidationError):
    pass


class TelegramRateLimitError(ValidationError):
    pass


@dataclass(frozen=True)
class TelegramUser:
    telegram_user_id: int
    display_name: str
    application_role: str
    status: str

    @property
    def actor(self) -> Actor:
        return Actor(f"telegram:{self.telegram_user_id}", self.application_role)


@dataclass(frozen=True)
class PendingOperation:
    operation_id: str
    telegram_user_id: int
    chat_id: int
    operation_type: str
    step: str
    payload: dict[str, Any]
    status: str
    expires_at: str


@dataclass(frozen=True)
class ConfirmationResult:
    entity_type: str
    entity_id: int
    sync_status: str
    sync_message: str
    already_confirmed: bool = False


def _utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _require_admin(actor: Actor) -> None:
    if actor.role != "Admin":
        raise TelegramAccessError("Administrator access is required.")


def _positive_telegram_id(value: Any) -> int:
    try:
        telegram_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Telegram user ID must be numeric.") from exc
    if telegram_id <= 0:
        raise ValidationError("Telegram user ID must be a positive number.")
    return telegram_id


def authorize_telegram_user(
    telegram_user_id: Any,
    display_name: str,
    application_role: str,
    actor: Actor,
    path: str | Path | None = None,
) -> int:
    _require_admin(actor)
    user_id = _positive_telegram_id(telegram_user_id)
    name = database._require_text(display_name, "Display name", 120)
    if application_role not in TELEGRAM_ROLES:
        raise ValidationError("Telegram role must be Admin or Staff.")
    with transaction(path) as conn:
        existing = conn.execute(
            "SELECT * FROM telegram_users WHERE telegram_user_id = ?", (user_id,)
        ).fetchone()
        if existing:
            before = dict(existing)
            conn.execute(
                """
                UPDATE telegram_users
                SET display_name = ?, application_role = ?, status = 'Active', created_by = ?
                WHERE telegram_user_id = ?
                """,
                (name, application_role, actor.username, user_id),
            )
            record_id = int(existing["id"])
            action = "update"
        else:
            cursor = conn.execute(
                """
                INSERT INTO telegram_users
                (telegram_user_id, display_name, application_role, status, created_at, created_by)
                VALUES (?, ?, ?, 'Active', ?, ?)
                """,
                (user_id, name, application_role, utc_now(), actor.username),
            )
            record_id = int(cursor.lastrowid)
            before = None
            action = "create"
        after = dict(
            conn.execute("SELECT * FROM telegram_users WHERE id = ?", (record_id,)).fetchone()
        )
        write_audit_log(
            conn, actor, action, "telegram_user", user_id,
            f"Telegram user ID {user_id} authorized as {application_role}.", before, after,
        )
    return record_id


def set_telegram_user_status(
    telegram_user_id: Any,
    status: str,
    actor: Actor,
    path: str | Path | None = None,
) -> None:
    _require_admin(actor)
    user_id = _positive_telegram_id(telegram_user_id)
    if status not in TELEGRAM_USER_STATUSES:
        raise ValidationError("Invalid Telegram user status.")
    with transaction(path) as conn:
        row = conn.execute(
            "SELECT * FROM telegram_users WHERE telegram_user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("Telegram user was not found.")
        before = dict(row)
        conn.execute(
            "UPDATE telegram_users SET status = ? WHERE telegram_user_id = ?",
            (status, user_id),
        )
        if status != "Active":
            pending_rows = conn.execute(
                """
                SELECT operation_id FROM telegram_pending_operations
                WHERE telegram_user_id = ? AND status = 'Pending'
                """,
                (user_id,),
            ).fetchall()
            for pending in pending_rows:
                conn.execute(
                    """
                    UPDATE telegram_pending_operations
                    SET status = 'Cancelled', updated_at = ?, error_message = 'Access disabled by Admin.'
                    WHERE operation_id = ?
                    """,
                    (utc_now(), pending["operation_id"]),
                )
                write_audit_log(
                    conn, actor, "telegram_cancelled", "telegram_operation",
                    pending["operation_id"],
                    f"Pending Telegram operation cancelled because user ID {user_id} was set to {status}.",
                )
        after = dict(
            conn.execute(
                "SELECT * FROM telegram_users WHERE telegram_user_id = ?", (user_id,)
            ).fetchone()
        )
        write_audit_log(
            conn, actor, "update", "telegram_user", user_id,
            f"Telegram user ID {user_id} set to {status}.", before, after,
        )


def list_telegram_users(path: str | Path | None = None):
    return fetch_df(
        """
        SELECT id, telegram_user_id, display_name, application_role, status,
               created_at, created_by, last_seen_at
        FROM telegram_users ORDER BY status, display_name, telegram_user_id
        """,
        path=path,
    )


def get_telegram_user(
    telegram_user_id: Any, path: str | Path | None = None
) -> TelegramUser | None:
    user_id = _positive_telegram_id(telegram_user_id)
    row = fetch_one(
        "SELECT * FROM telegram_users WHERE telegram_user_id = ?", (user_id,), path
    )
    if row is None:
        return None
    return TelegramUser(user_id, row["display_name"], row["application_role"], row["status"])


def record_telegram_rejection(
    telegram_user_id: int,
    reason: str,
    role: str = "Unknown",
    operation_id: str | None = None,
    path: str | Path | None = None,
) -> None:
    safe_reason = (reason or "Request rejected.").replace("\n", " ")[:240]
    with transaction(path) as conn:
        write_audit_log(
            conn, Actor(f"telegram:{telegram_user_id}", role), "telegram_rejected",
            "telegram_operation", operation_id,
            f"Telegram request rejected for user ID {telegram_user_id}: {safe_reason}",
        )


def require_authorized_user(
    telegram_user_id: Any, path: str | Path | None = None
) -> TelegramUser:
    user_id = _positive_telegram_id(telegram_user_id)
    rejected_status: str | None = None
    rejected_role = "Unknown"
    authorized: TelegramUser | None = None
    with transaction(path) as conn:
        row = conn.execute(
            "SELECT * FROM telegram_users WHERE telegram_user_id = ?", (user_id,)
        ).fetchone()
        if row is None or row["status"] != "Active":
            rejected_status = row["status"] if row else "Unauthorized"
            rejected_role = row["application_role"] if row else "Unknown"
            write_audit_log(
                conn, Actor(f"telegram:{user_id}", rejected_role), "telegram_rejected",
                "telegram_authorization", user_id,
                f"Telegram access rejected for user ID {user_id}: {rejected_status}.",
            )
        else:
            conn.execute(
                "UPDATE telegram_users SET last_seen_at = ? WHERE telegram_user_id = ?",
                (utc_now(), user_id),
            )
            authorized = TelegramUser(
                user_id, row["display_name"], row["application_role"], row["status"]
            )
    if rejected_status is not None:
        raise TelegramAccessError("This Telegram account is not authorized for factory access.")
    return authorized


def is_chat_allowed(chat_type: str, chat_id: int, approved_group_id: int | None) -> bool:
    return chat_type == "private" or (
        approved_group_id is not None and int(chat_id) == int(approved_group_id)
    )


def enforce_rate_limit(
    telegram_user_id: Any,
    path: str | Path | None = None,
    max_requests: int = 25,
    window_seconds: int = 60,
    block_seconds: int = 300,
) -> None:
    user_id = _positive_telegram_id(telegram_user_id)
    now = _utc_datetime()
    newly_blocked = False
    with transaction(path) as conn:
        row = conn.execute(
            "SELECT * FROM telegram_rate_limits WHERE telegram_user_id = ?", (user_id,)
        ).fetchone()
        blocked_until = _parse_timestamp(row["blocked_until"]) if row else None
        if blocked_until and blocked_until > now:
            raise TelegramRateLimitError("Too many requests. Try again later.")
        window_start = _parse_timestamp(row["window_started_at"]) if row else None
        if not window_start or (now - window_start).total_seconds() >= window_seconds:
            count = 1
            window_start = now
        else:
            count = int(row["request_count"]) + 1
        new_block = now + timedelta(seconds=block_seconds) if count > max_requests else None
        conn.execute(
            """
            INSERT INTO telegram_rate_limits
            (telegram_user_id, window_started_at, request_count, blocked_until, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                window_started_at = excluded.window_started_at,
                request_count = excluded.request_count,
                blocked_until = excluded.blocked_until,
                updated_at = excluded.updated_at
            """,
            (
                user_id, window_start.isoformat(timespec="seconds"), count,
                new_block.isoformat(timespec="seconds") if new_block else None, utc_now(),
            ),
        )
        if new_block:
            write_audit_log(
                conn, Actor(f"telegram:{user_id}", "Unknown"), "telegram_rejected",
                "telegram_rate_limit", user_id,
                f"Telegram user ID {user_id} temporarily rate limited.",
            )
            newly_blocked = True
    if newly_blocked:
        raise TelegramRateLimitError("Too many requests. Try again later.")


def _pending_from_row(row: sqlite3.Row) -> PendingOperation:
    return PendingOperation(
        row["operation_id"], int(row["telegram_user_id"]), int(row["chat_id"]),
        row["operation_type"], row["step"], json.loads(row["payload_json"] or "{}"),
        row["status"], row["expires_at"],
    )


def create_pending_operation(
    user: TelegramUser,
    chat_id: int,
    operation_type: str,
    first_step: str,
    payload: dict[str, Any] | None = None,
    path: str | Path | None = None,
    ttl_minutes: int = PENDING_TTL_MINUTES,
) -> PendingOperation:
    if operation_type not in {"production", "expense", "attendance", "machine"}:
        raise ValidationError("Unsupported Telegram operation.")
    if operation_type == "machine" and user.application_role != "Admin":
        record_telegram_rejection(
            user.telegram_user_id, "Machine changes require Admin.", user.application_role,
            path=path,
        )
        raise TelegramAccessError("Only Admin users can change machine status.")
    operation_id = str(uuid.uuid4())
    now = _utc_datetime()
    expires = now + timedelta(minutes=ttl_minutes)
    with transaction(path) as conn:
        existing = conn.execute(
            """
            SELECT operation_id FROM telegram_pending_operations
            WHERE telegram_user_id = ? AND status = 'Pending'
            """,
            (user.telegram_user_id,),
        ).fetchall()
        for row in existing:
            conn.execute(
                """
                UPDATE telegram_pending_operations
                SET status = 'Cancelled', updated_at = ?, error_message = 'Replaced by a new command.'
                WHERE operation_id = ?
                """,
                (now.isoformat(timespec="seconds"), row["operation_id"]),
            )
            write_audit_log(
                conn, user.actor, "telegram_cancelled", "telegram_operation", row["operation_id"],
                "Pending Telegram operation cancelled because a new command was started.",
            )
        conn.execute(
            """
            INSERT INTO telegram_pending_operations
            (operation_id, telegram_user_id, chat_id, operation_type, step, payload_json,
             status, idempotency_key, created_at, updated_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?, ?)
            """,
            (
                operation_id, user.telegram_user_id, int(chat_id), operation_type, first_step,
                json.dumps(payload or {}, sort_keys=True), operation_id,
                now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"),
                expires.isoformat(timespec="seconds"),
            ),
        )
        write_audit_log(
            conn, user.actor, "telegram_accepted", "telegram_operation", operation_id,
            f"Telegram {operation_type} workflow accepted for user ID {user.telegram_user_id}.",
        )
        row = conn.execute(
            "SELECT * FROM telegram_pending_operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
    return _pending_from_row(row)


def get_active_pending_operation(
    telegram_user_id: Any,
    operation_id: str | None = None,
    path: str | Path | None = None,
) -> PendingOperation | None:
    user_id = _positive_telegram_id(telegram_user_id)
    expire_pending_operations(path)
    clauses = ["telegram_user_id = ?", "status = 'Pending'"]
    params: list[Any] = [user_id]
    if operation_id:
        clauses.append("operation_id = ?")
        params.append(operation_id)
    row = fetch_one(
        f"""
        SELECT * FROM telegram_pending_operations
        WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT 1
        """,
        tuple(params), path,
    )
    return _pending_from_row(row) if row else None


def update_pending_operation(
    operation_id: str,
    telegram_user_id: Any,
    step: str,
    payload: dict[str, Any],
    path: str | Path | None = None,
) -> PendingOperation:
    user_id = _positive_telegram_id(telegram_user_id)
    expire_pending_operations(path)
    with transaction(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM telegram_pending_operations
            WHERE operation_id = ? AND telegram_user_id = ? AND status = 'Pending'
            """,
            (operation_id, user_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("Pending Telegram operation was not found.")
        conn.execute(
            """
            UPDATE telegram_pending_operations
            SET step = ?, payload_json = ?, updated_at = ?
            WHERE operation_id = ?
            """,
            (step, json.dumps(payload, sort_keys=True), utc_now(), operation_id),
        )
        updated = conn.execute(
            "SELECT * FROM telegram_pending_operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
    return _pending_from_row(updated)


def cancel_pending_operation(
    user: TelegramUser,
    operation_id: str | None = None,
    path: str | Path | None = None,
    reason: str = "Cancelled by user.",
) -> int:
    with transaction(path) as conn:
        params: list[Any] = [utc_now(), reason[:240], user.telegram_user_id]
        clause = ""
        if operation_id:
            clause = " AND operation_id = ?"
            params.append(operation_id)
        rows = conn.execute(
            f"""
            SELECT operation_id FROM telegram_pending_operations
            WHERE telegram_user_id = ? AND status = 'Pending'{clause}
            """,
            tuple([user.telegram_user_id] + ([operation_id] if operation_id else [])),
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                UPDATE telegram_pending_operations
                SET status = 'Cancelled', updated_at = ?, error_message = ?
                WHERE operation_id = ?
                """,
                (utc_now(), reason[:240], row["operation_id"]),
            )
            write_audit_log(
                conn, user.actor, "telegram_cancelled", "telegram_operation", row["operation_id"],
                f"Telegram operation cancelled: {reason[:160]}",
            )
    return len(rows)


def expire_pending_operations(path: str | Path | None = None) -> int:
    now = utc_now()
    with transaction(path) as conn:
        rows = conn.execute(
            """
            SELECT p.operation_id, p.telegram_user_id, u.application_role
            FROM telegram_pending_operations p
            LEFT JOIN telegram_users u ON u.telegram_user_id = p.telegram_user_id
            WHERE p.status = 'Pending' AND p.expires_at <= ?
            """,
            (now,),
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                UPDATE telegram_pending_operations
                SET status = 'Expired', updated_at = ?, error_message = 'Incomplete workflow expired.'
                WHERE operation_id = ?
                """,
                (now, row["operation_id"]),
            )
            write_audit_log(
                conn,
                Actor(f"telegram:{row['telegram_user_id']}", row["application_role"] or "Unknown"),
                "telegram_cancelled", "telegram_operation", row["operation_id"],
                "Incomplete Telegram workflow expired without changing factory records.",
            )
    return len(rows)


def _confirmed_receipt(conn: sqlite3.Connection, operation_id: str):
    return conn.execute(
        "SELECT entity_type, entity_id FROM telegram_mutation_receipts WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()


def _production_mutation(conn, operation, payload, actor):
    production_date = database._require_date(payload.get("production_date"), "Production date")
    machine_number = database._require_text(payload.get("machine_number", ""), "Machine number", 50)
    operator = database._require_text(payload.get("operator_name", ""), "Operator", 120)
    product = database._require_text(payload.get("product_type", ""), "Product type", 120)
    try:
        quantity = int(payload.get("quantity"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("Quantity must be a positive whole number.") from exc
    if quantity <= 0:
        raise ValidationError("Quantity must be a positive whole number.")
    rate = round_money(payload.get("rate_per_unit"), "Rate per unit", allow_zero=False)
    total = round_money(Decimal(quantity) * Decimal(str(rate)), "Total amount")
    machine = conn.execute(
        "SELECT id FROM machines WHERE machine_number = ? COLLATE NOCASE", (machine_number,)
    ).fetchone()
    if machine is None:
        raise ValidationError("Select a registered machine.")
    cursor = conn.execute(
        """
        INSERT INTO production_entries
        (production_date, machine_number, operator_name, product_type, quantity,
         rate_per_unit, total_amount, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (production_date, machine_number, operator, product, quantity, rate, total, utc_now()),
    )
    entity_id = int(cursor.lastrowid)
    after = dict(conn.execute("SELECT * FROM production_entries WHERE id = ?", (entity_id,)).fetchone())
    detail = f"Telegram production #{entity_id}: {machine_number}, {quantity} {product} units by {operator}."
    database._log_activity(conn, "Production", "Telegram production confirmed", detail)
    write_audit_log(conn, actor, "telegram_confirmed", "production", entity_id, detail, after_state=after)
    return "production", entity_id


def _expense_mutation(conn, operation, payload, actor):
    expense_date = database._require_date(payload.get("expense_date"), "Expense date")
    category = database._require_text(payload.get("expense_type", ""), "Expense category", 100)
    amount = round_money(payload.get("amount"), "Amount", allow_zero=False)
    description = str(payload.get("description") or "").strip()[:500]
    cursor = conn.execute(
        """
        INSERT INTO expenses(expense_type, amount, description, expense_date, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (category, amount, description, expense_date, utc_now()),
    )
    entity_id = int(cursor.lastrowid)
    after = dict(conn.execute("SELECT * FROM expenses WHERE id = ?", (entity_id,)).fetchone())
    detail = f"Telegram expense #{entity_id}: {category}, BDT {amount:,.2f}."
    database._log_activity(conn, "Expense", "Telegram expense confirmed", detail)
    write_audit_log(conn, actor, "telegram_confirmed", "expense", entity_id, detail, after_state=after)
    return "expense", entity_id


def _attendance_mutation(conn, operation, payload, actor):
    attendance_date = database._require_date(payload.get("attendance_date"), "Attendance date")
    try:
        employee_id = int(payload.get("employee_id"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("Select an active employee.") from exc
    status = str(payload.get("status") or "")
    if status not in database.ATTENDANCE_STATUSES:
        raise ValidationError("Invalid attendance status.")
    employee = conn.execute(
        "SELECT id, name, status FROM employees WHERE id = ?", (employee_id,)
    ).fetchone()
    if employee is None or employee["status"] == "Archived":
        raise ValidationError("Select an active employee.")
    notes = str(payload.get("notes") or "").strip()[:500]
    try:
        cursor = conn.execute(
            """
            INSERT INTO attendance(employee_id, attendance_date, status, notes, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (employee_id, attendance_date, status, notes, utc_now()),
        )
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc).upper():
            raise ConflictError("Attendance already exists for this employee and date.") from exc
        raise
    entity_id = int(cursor.lastrowid)
    after = dict(conn.execute("SELECT * FROM attendance WHERE id = ?", (entity_id,)).fetchone())
    detail = f"Telegram attendance #{entity_id}: {employee['name']} {status} on {attendance_date}."
    database._log_activity(conn, "Attendance", "Telegram attendance confirmed", detail)
    write_audit_log(conn, actor, "telegram_confirmed", "attendance", entity_id, detail, after_state=after)
    return "attendance", entity_id


def _machine_mutation(conn, operation, payload, actor):
    if actor.role != "Admin":
        raise TelegramAccessError("Only Admin users can change machine status.")
    try:
        machine_id = int(payload.get("machine_id"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("Select a registered machine.") from exc
    new_status = str(payload.get("status") or "")
    if new_status not in TELEGRAM_MACHINE_STATUSES:
        raise ValidationError("Invalid machine status.")
    row = conn.execute("SELECT * FROM machines WHERE id = ?", (machine_id,)).fetchone()
    if row is None:
        raise NotFoundError("Machine was not found.")
    before = dict(row)
    conn.execute(
        "UPDATE machines SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, utc_now(), machine_id),
    )
    after = dict(conn.execute("SELECT * FROM machines WHERE id = ?", (machine_id,)).fetchone())
    detail = (
        f"Telegram machine update #{machine_id} {row['machine_number']}: "
        f"{before['status']} to {new_status}."
    )
    database._log_activity(conn, "Machine", "Telegram machine status confirmed", detail)
    write_audit_log(conn, actor, "telegram_confirmed", "machine", machine_id, detail, before, after)
    return "machine", machine_id


MUTATION_HANDLERS = {
    "production": _production_mutation,
    "expense": _expense_mutation,
    "attendance": _attendance_mutation,
    "machine": _machine_mutation,
}


def confirm_pending_operation(
    operation_id: str,
    telegram_user_id: Any,
    path: str | Path | None = None,
) -> ConfirmationResult:
    user_id = _positive_telegram_id(telegram_user_id)
    expire_pending_operations(path)
    already_confirmed = False
    with transaction(path) as conn:
        operation = conn.execute(
            "SELECT * FROM telegram_pending_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if operation is None or int(operation["telegram_user_id"]) != user_id:
            raise NotFoundError("Telegram operation was not found.")
        user_row = conn.execute(
            "SELECT * FROM telegram_users WHERE telegram_user_id = ?", (user_id,)
        ).fetchone()
        if user_row is None or user_row["status"] != "Active":
            raise TelegramAccessError("This Telegram account is not authorized for factory access.")
        actor = Actor(f"telegram:{user_id}", user_row["application_role"])
        receipt = _confirmed_receipt(conn, operation_id)
        if receipt:
            entity_type, entity_id = receipt["entity_type"], int(receipt["entity_id"])
            already_confirmed = True
        else:
            if operation["status"] == "Expired":
                raise ValidationError("This operation expired. Start again.")
            if operation["status"] != "Pending":
                raise ConflictError(f"This operation is already {operation['status'].lower()}.")
            payload = json.loads(operation["payload_json"] or "{}")
            entity_type, entity_id = MUTATION_HANDLERS[operation["operation_type"]](
                conn, operation, payload, actor
            )
            now = utc_now()
            conn.execute(
                """
                INSERT INTO telegram_mutation_receipts
                (operation_id, entity_type, entity_id, created_at) VALUES (?, ?, ?, ?)
                """,
                (operation_id, entity_type, entity_id, now),
            )
            conn.execute(
                """
                UPDATE telegram_pending_operations
                SET status = 'Confirmed', confirmed_at = ?, updated_at = ?, error_message = NULL
                WHERE operation_id = ?
                """,
                (now, now, operation_id),
            )
            database._mark_excel_out_of_date(
                conn, f"Confirmed Telegram {operation['operation_type']} changed factory data."
            )

    if already_confirmed:
        sync = get_excel_sync_status(path)
        return ConfirmationResult(
            entity_type, entity_id, sync["status"],
            "This operation was already saved; no duplicate record was created.", True,
        )
    sync_status, sync_message = database._sync_after_commit(path)
    return ConfirmationResult(entity_type, entity_id, sync_status, sync_message, False)


def today_summary(day: date | str | None = None, path: str | Path | None = None) -> dict[str, Any]:
    selected_day = database._require_date(day or date.today(), "Summary date")
    production = fetch_one(
        """
        SELECT COALESCE(SUM(quantity), 0) AS quantity,
               COALESCE(SUM(total_amount), 0) AS earnings
        FROM production_entries WHERE production_date = ?
        """,
        (selected_day,), path,
    )
    expenses = fetch_one(
        "SELECT COALESCE(SUM(amount), 0) AS expenses FROM expenses WHERE expense_date = ?",
        (selected_day,), path,
    )
    attendance = fetch_df(
        """
        SELECT status, COUNT(*) AS total FROM attendance
        WHERE attendance_date = ? GROUP BY status
        """,
        (selected_day,), path,
    )
    machines = fetch_df(
        "SELECT status, COUNT(*) AS total FROM machines GROUP BY status", path=path
    )
    attendance_counts = dict.fromkeys(sorted(database.ATTENDANCE_STATUSES), 0)
    attendance_counts.update(
        {str(row["status"]): int(row["total"]) for _, row in attendance.iterrows()}
    )
    machine_counts = dict.fromkeys(sorted(TELEGRAM_MACHINE_STATUSES), 0)
    machine_counts.update({str(row["status"]): int(row["total"]) for _, row in machines.iterrows()})
    earnings = float(production["earnings"])
    expense_total = float(expenses["expenses"])
    return {
        "date": selected_day,
        "production_quantity": int(production["quantity"]),
        "earnings": earnings,
        "expenses": expense_total,
        "profit": round(earnings - expense_total, 2),
        "attendance": attendance_counts,
        "machines": machine_counts,
    }


def list_pending_operations(path: str | Path | None = None):
    expire_pending_operations(path)
    return fetch_df(
        """
        SELECT operation_id, telegram_user_id, operation_type, step, status,
               created_at, updated_at, expires_at, confirmed_at, error_message
        FROM telegram_pending_operations ORDER BY updated_at DESC LIMIT 200
        """,
        path=path,
    )


def recent_telegram_audit(
    rejected_only: bool = False, path: str | Path | None = None
):
    clause = "AND action = 'telegram_rejected'" if rejected_only else ""
    return fetch_df(
        f"""
        SELECT id, timestamp, username, role, action, entity_type, entity_id, description
        FROM audit_logs
        WHERE username LIKE 'telegram:%' {clause}
        ORDER BY timestamp DESC, id DESC LIMIT 200
        """,
        path=path,
    )


def acquire_bot_lease(
    instance_id: str,
    path: str | Path | None = None,
    stale_seconds: int = 90,
) -> None:
    now = _utc_datetime()
    with transaction(path) as conn:
        row = conn.execute("SELECT * FROM telegram_bot_status WHERE id = 1").fetchone()
        heartbeat = _parse_timestamp(row["heartbeat_at"]) if row else None
        active = (
            row and row["status"] in {"Starting", "Running"} and heartbeat
            and (now - heartbeat).total_seconds() < stale_seconds
            and row["instance_id"] != instance_id
        )
        if active:
            raise ConflictError("Another Telegram bot instance is already running.")
        conn.execute(
            """
            UPDATE telegram_bot_status
            SET status = 'Starting', instance_id = ?, started_at = ?, heartbeat_at = ?,
                message = 'Polling service is starting.' WHERE id = 1
            """,
            (instance_id, now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")),
        )


def heartbeat_bot(instance_id: str, path: str | Path | None = None) -> None:
    with transaction(path) as conn:
        conn.execute(
            """
            UPDATE telegram_bot_status
            SET status = 'Running', heartbeat_at = ?, message = 'Polling service is healthy.'
            WHERE id = 1 AND instance_id = ?
            """,
            (utc_now(), instance_id),
        )


def release_bot_lease(
    instance_id: str,
    message: str = "Polling service stopped cleanly.",
    error: bool = False,
    path: str | Path | None = None,
) -> None:
    with transaction(path) as conn:
        conn.execute(
            """
            UPDATE telegram_bot_status
            SET status = ?, heartbeat_at = ?, message = ?, instance_id = NULL
            WHERE id = 1 AND instance_id = ?
            """,
            ("Error" if error else "Stopped", utc_now(), message[:240], instance_id),
        )


def get_bot_status(path: str | Path | None = None) -> dict[str, Any]:
    row = fetch_one("SELECT * FROM telegram_bot_status WHERE id = 1", path=path)
    return dict(row) if row else {
        "status": "Stopped", "instance_id": None, "started_at": None,
        "heartbeat_at": None, "message": "Telegram bot status is unavailable.",
    }
