from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import uuid
from datetime import date
from pathlib import Path

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database
from database import FactoryError, ValidationError, fetch_df, get_excel_sync_status
from gemini_intake import (
    extract_entry,
    natural_language_enabled,
    record_intake_audit,
)
from telegram_automation import (
    PendingOperation,
    TelegramAccessError,
    TelegramRateLimitError,
    TelegramUser,
    acquire_bot_lease,
    cancel_pending_operation,
    confirm_pending_operation,
    create_pending_operation,
    enforce_rate_limit,
    get_active_pending_operation,
    heartbeat_bot,
    is_chat_allowed,
    record_telegram_rejection,
    release_bot_lease,
    require_authorized_user,
    today_summary,
    update_pending_operation,
)
from telegram_config import TelegramConfig, load_telegram_config

ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger("al_sadi.telegram")
MENU = ReplyKeyboardMarkup(
    [
        ["/production", "/expense"],
        ["/attendance", "/machine"],
        ["/today", "/status"],
        ["/help", "/cancel"],
    ],
    resize_keyboard=True,
)


class RedactingFormatter(logging.Formatter):
    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        text = json.dumps(payload, ensure_ascii=True)
        return text.replace(self.token, "[REDACTED]") if self.token else text


def configure_logging(token: str) -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    formatter = RedactingFormatter(token)
    for handler in [logging.StreamHandler(), logging.FileHandler(ROOT / "telegram_bot.log", encoding="utf-8")]:
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def _reply(update: Update, text: str, reply_markup=None) -> None:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=reply_markup)


async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> TelegramUser | None:
    user_id = update.effective_user.id if update.effective_user else 0
    chat = update.effective_chat
    config: TelegramConfig = context.application.bot_data["config"]
    try:
        enforce_rate_limit(
            user_id,
            max_requests=config.rate_limit_requests,
            window_seconds=config.rate_limit_window_seconds,
            block_seconds=config.rate_limit_block_seconds,
        )
        if chat is None or not is_chat_allowed(chat.type, chat.id, config.approved_group_id):
            record_telegram_rejection(user_id, "Chat is not an approved private destination.")
            raise TelegramAccessError("This chat is not authorized for factory access.")
        return require_authorized_user(user_id)
    except (TelegramAccessError, TelegramRateLimitError, ValidationError) as exc:
        await _reply(update, str(exc))
        return None


def _operation_keyboard(operation_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Confirm and Save", callback_data=f"c|{operation_id}"),
            InlineKeyboardButton("Edit", callback_data=f"e|{operation_id}"),
            InlineKeyboardButton("Cancel", callback_data=f"x|{operation_id}"),
        ]]
    )


def _selection_keyboard(operation_id: str, field: str, rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label[:48], callback_data=f"p|{operation_id}|{field}|{value}")]
         for label, value in rows]
    )


def _today_text(summary: dict) -> str:
    attendance = summary["attendance"]
    machines = summary["machines"]
    return (
        f"Factory summary for {summary['date']}\n\n"
        f"Production quantity: {summary['production_quantity']}\n"
        f"Earnings: BDT {summary['earnings']:,.2f}\n"
        f"Expenses: BDT {summary['expenses']:,.2f}\n"
        f"Profit: BDT {summary['profit']:,.2f}\n\n"
        f"Attendance: Present {attendance.get('Present', 0)}, Absent {attendance.get('Absent', 0)}, "
        f"Leave {attendance.get('Leave', 0)}, Late {attendance.get('Late', 0)}\n"
        f"Machines: Running {machines.get('Running', 0)}, Idle {machines.get('Idle', 0)}, "
        f"Maintenance {machines.get('Maintenance', 0)}, "
        f"Out of Service {machines.get('Out of Service', 0) + machines.get('Offline', 0)}"
    )


def _confirmation_text(operation: PendingOperation) -> str:
    payload = operation.payload
    if operation.operation_type == "production":
        quantity = int(payload["quantity"])
        rate = float(payload["rate_per_unit"])
        return (
            "Confirm production entry\n\n"
            f"Date: {payload['production_date']}\nMachine: {payload['machine_number']}\n"
            f"Operator: {payload['operator_name']}\nProduct: {payload['product_type']}\n"
            f"Quantity: {quantity}\nRate: BDT {rate:,.2f}\n"
            f"Calculated total: BDT {quantity * rate:,.2f}"
        )
    if operation.operation_type == "expense":
        return (
            "Confirm expense entry\n\n"
            f"Date: {payload['expense_date']}\nCategory: {payload['expense_type']}\n"
            f"Amount: BDT {float(payload['amount']):,.2f}\n"
            f"Description: {payload.get('description') or 'None'}"
        )
    if operation.operation_type == "attendance":
        return (
            "Confirm attendance entry\n\n"
            f"Employee: {payload['employee_name']}\nDate: {payload['attendance_date']}\n"
            f"Status: {payload['status']}\nNotes: {payload.get('notes') or 'None'}"
        )
    return (
        "Confirm machine status change\n\n"
        f"Machine: {payload['machine_number']}\nPrevious: {payload['previous_status']}\n"
        f"New status: {payload['status']}"
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _guard(update, context)
    if user:
        await _reply(
            update,
            f"Welcome, {user.display_name}. Use the menu to submit confirmed factory records.",
            MENU,
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _guard(update, context)
    if user:
        await _reply(
            update,
            "/production - confirmed production entry\n"
            "/expense - confirmed expense entry\n"
            "/attendance - confirmed attendance entry\n"
            "/machine - view machines; Admin may change status\n"
            "/today - today's factory summary\n/status - Excel sync status\n"
            "/cancel - cancel an incomplete operation",
            MENU,
        )


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _guard(update, context):
        await _reply(update, _today_text(today_summary()), MENU)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _guard(update, context):
        status = get_excel_sync_status()
        await _reply(
            update,
            f"Excel sync: {status['status']}\nLast successful sync: "
            f"{status['last_success_at'] or 'Never'}",
            MENU,
        )


async def _start_operation(
    update: Update, user: TelegramUser, operation_type: str
) -> None:
    first_steps = {
        "production": "production_date",
        "expense": "expense_date",
        "attendance": "employee_select",
        "machine": "machine_select",
    }
    operation = create_pending_operation(
        user, update.effective_chat.id, operation_type, first_steps[operation_type]
    )
    if operation_type == "production":
        await _reply(update, "Enter production date as YYYY-MM-DD, or type Today.")
    elif operation_type == "expense":
        await _reply(update, "Enter expense date as YYYY-MM-DD, or type Today.")
    elif operation_type == "attendance":
        await _show_employee_selection(update, operation)
    else:
        await _show_machine_selection(update, operation)


async def production_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _guard(update, context)
    if user:
        await _start_operation(update, user, "production")


async def expense_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _guard(update, context)
    if user:
        await _start_operation(update, user, "expense")


async def attendance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _guard(update, context)
    if user:
        await _start_operation(update, user, "attendance")


async def machine_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _guard(update, context)
    if not user:
        return
    machines = fetch_df(
        "SELECT id, machine_number, status FROM machines ORDER BY machine_number"
    )
    if machines.empty:
        await _reply(update, "No machines are registered.")
        return
    lines = [f"{row.machine_number}: {row.status}" for row in machines.itertuples()]
    if user.application_role != "Admin":
        await _reply(update, "Machine status\n\n" + "\n".join(lines) + "\n\nAdmin is required to change status.")
        return
    await _reply(update, "Machine status\n\n" + "\n".join(lines))
    await _start_operation(update, user, "machine")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _guard(update, context)
    if user:
        cancelled = cancel_pending_operation(user)
        await _reply(update, "Pending operation cancelled." if cancelled else "No pending operation found.", MENU)


async def _show_employee_selection(update: Update, operation: PendingOperation) -> None:
    employees = fetch_df(
        "SELECT id, name FROM employees WHERE status <> 'Archived' ORDER BY name, id"
    )
    if employees.empty:
        await _reply(update, "No active employees are available.")
        return
    rows = [(str(row.name), str(row.id)) for row in employees.itertuples()]
    await _reply(update, "Select an employee.", _selection_keyboard(operation.operation_id, "e", rows))


async def _show_machine_selection(update: Update, operation: PendingOperation) -> None:
    machines = fetch_df("SELECT id, machine_number, status FROM machines ORDER BY machine_number")
    if machines.empty:
        await _reply(update, "No machines are registered.")
        return
    rows = [(f"{row.machine_number} - {row.status}", str(row.id)) for row in machines.itertuples()]
    field = "pm" if operation.operation_type == "production" else "m"
    await _reply(update, "Select a machine.", _selection_keyboard(operation.operation_id, field, rows))


def _parse_date_text(text: str) -> str:
    return database._require_date(date.today() if text.strip().lower() == "today" else text.strip(), "Date")


async def _handle_natural_language(update: Update, user: TelegramUser) -> None:
    """Try to read a free-text message as a production or expense entry.

    Anything less than a complete, validated entry falls back to the normal
    menu prompt. Nothing here writes a factory record: a successful parse
    only creates a pending operation and shows the same Confirm / Edit /
    Cancel screen the button flow produces, so the write still happens in
    confirm_pending_operation() when the operator taps Confirm.
    """
    text = (update.effective_message.text or "").strip()

    parsed = None
    if text and natural_language_enabled():
        try:
            # extract_entry() makes a blocking HTTPS call; keep it off the
            # event loop so the bot stays responsive to other chats.
            parsed = await asyncio.to_thread(extract_entry, text)
        except Exception:
            LOGGER.warning("Natural-language intake failed; using the button flow.")
            parsed = None

    if parsed is None:
        await _reply(update, "Choose a command from the menu first.", MENU)
        return

    operation_type, payload = parsed
    try:
        operation = create_pending_operation(
            user, update.effective_chat.id, operation_type, "confirm", payload
        )
    except (FactoryError, TelegramAccessError) as exc:
        record_telegram_rejection(user.telegram_user_id, str(exc), user.application_role)
        await _reply(update, "Choose a command from the menu first.", MENU)
        return

    record_intake_audit(user, operation.operation_id, operation_type, text)
    await _reply(
        update,
        _confirmation_text(operation),
        _operation_keyboard(operation.operation_id),
    )


async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _guard(update, context)
    if not user or not update.effective_message:
        return
    operation = get_active_pending_operation(user.telegram_user_id)
    if not operation:
        # No workflow in progress, so this is either a free-text entry or
        # noise. _guard() has already enforced the rate limit, chat
        # restriction and authorization, so a message only reaches the
        # parser if a button-driven one would have been allowed through.
        await _handle_natural_language(update, user)
        return
    text = update.effective_message.text.strip()
    payload = dict(operation.payload)
    next_step = operation.step
    prompt = ""
    try:
        if operation.step in {"production_date", "expense_date", "attendance_date"}:
            key = operation.step
            payload[key] = _parse_date_text(text)
            if operation.step == "production_date":
                next_step, prompt = "production_machine_select", ""
            elif operation.step == "expense_date":
                next_step, prompt = "expense_type", "Enter expense category."
            else:
                next_step, prompt = "attendance_notes", "Enter optional notes, or type - for none."
        elif operation.step == "operator_name":
            payload["operator_name"] = database._require_text(text, "Operator", 120)
            next_step, prompt = "product_type", "Enter product type."
        elif operation.step == "product_type":
            payload["product_type"] = database._require_text(text, "Product type", 120)
            next_step, prompt = "quantity", "Enter quantity as a positive whole number."
        elif operation.step == "quantity":
            quantity = int(text)
            if quantity <= 0:
                raise ValidationError("Quantity must be a positive whole number.")
            payload["quantity"] = quantity
            next_step, prompt = "rate_per_unit", "Enter rate per unit."
        elif operation.step == "rate_per_unit":
            payload["rate_per_unit"] = database.round_money(text, "Rate per unit", allow_zero=False)
            next_step, prompt = "confirm", ""
        elif operation.step == "expense_type":
            payload["expense_type"] = database._require_text(text, "Expense category", 100)
            next_step, prompt = "amount", "Enter expense amount."
        elif operation.step == "amount":
            payload["amount"] = database.round_money(text, "Amount", allow_zero=False)
            next_step, prompt = "description", "Enter a description, or type - for none."
        elif operation.step == "description":
            payload["description"] = "" if text == "-" else text[:500]
            next_step, prompt = "confirm", ""
        elif operation.step == "attendance_notes":
            payload["notes"] = "" if text == "-" else text[:500]
            next_step, prompt = "confirm", ""
        else:
            await _reply(update, "Use the displayed buttons to continue this operation.")
            return
    except (ValueError, FactoryError) as exc:
        record_telegram_rejection(
            user.telegram_user_id, str(exc), user.application_role, operation.operation_id
        )
        await _reply(update, str(exc))
        return

    updated = update_pending_operation(
        operation.operation_id, user.telegram_user_id, next_step, payload
    )
    if next_step == "production_machine_select":
        await _show_machine_selection(update, updated)
    elif next_step == "confirm":
        await _reply(update, _confirmation_text(updated), _operation_keyboard(updated.operation_id))
    else:
        await _reply(update, prompt)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _guard(update, context)
    if not user or not update.callback_query:
        return
    parts = (update.callback_query.data or "").split("|")
    action = parts[0]
    operation_id = parts[1] if len(parts) > 1 else ""
    try:
        if action == "x":
            cancel_pending_operation(user, operation_id)
            await _reply(update, "Operation cancelled. No factory record was changed.", MENU)
            return
        if action == "e":
            operation = get_active_pending_operation(user.telegram_user_id, operation_id)
            if not operation:
                raise ValidationError("This operation is no longer pending.")
            operation_type = operation.operation_type
            cancel_pending_operation(user, operation_id, reason="Edit selected; restarting workflow.")
            await _start_operation(update, user, operation_type)
            return
        if action == "c":
            result = confirm_pending_operation(operation_id, user.telegram_user_id)
            duplicate = " No duplicate was created." if result.already_confirmed else ""
            sync_note = (
                "Excel synchronized." if result.sync_status == "Synced"
                else f"Database saved, but Excel is {result.sync_status.lower()}: {result.sync_message}"
            )
            await _reply(
                update,
                f"Saved {result.entity_type} record #{result.entity_id}.{duplicate}\n{sync_note}",
                MENU,
            )
            return
        if action != "p" or len(parts) != 4:
            raise ValidationError("Unsupported button action.")
        _, operation_id, field, value = parts
        operation = get_active_pending_operation(user.telegram_user_id, operation_id)
        if not operation:
            raise ValidationError("This operation expired or was cancelled.")
        payload = dict(operation.payload)
        if field == "pm":
            machine = database.fetch_one("SELECT * FROM machines WHERE id = ?", (int(value),))
            if machine is None:
                raise ValidationError("Machine was not found.")
            payload["machine_number"] = machine["machine_number"]
            updated = update_pending_operation(operation_id, user.telegram_user_id, "operator_name", payload)
            await _reply(update, "Enter operator name.")
        elif field == "e":
            employee = database.fetch_one(
                "SELECT id, name, status FROM employees WHERE id = ?", (int(value),)
            )
            if employee is None or employee["status"] == "Archived":
                raise ValidationError("Active employee was not found.")
            payload.update({"employee_id": int(value), "employee_name": employee["name"]})
            updated = update_pending_operation(operation_id, user.telegram_user_id, "attendance_status", payload)
            statuses = [(status, status) for status in ["Present", "Absent", "Leave", "Late"]]
            await _reply(update, "Select attendance status.", _selection_keyboard(operation_id, "as", statuses))
        elif field == "as":
            if value not in database.ATTENDANCE_STATUSES:
                raise ValidationError("Invalid attendance status.")
            payload["status"] = value
            update_pending_operation(operation_id, user.telegram_user_id, "attendance_date", payload)
            await _reply(update, "Enter attendance date as YYYY-MM-DD, or type Today.")
        elif field == "m":
            if user.application_role != "Admin":
                raise TelegramAccessError("Only Admin users can change machine status.")
            machine = database.fetch_one("SELECT * FROM machines WHERE id = ?", (int(value),))
            if machine is None:
                raise ValidationError("Machine was not found.")
            payload.update({
                "machine_id": int(value), "machine_number": machine["machine_number"],
                "previous_status": machine["status"],
            })
            update_pending_operation(operation_id, user.telegram_user_id, "machine_status", payload)
            statuses = [("Running", "r"), ("Idle", "i"), ("Maintenance", "m"), ("Out of Service", "o")]
            await _reply(update, "Select the new machine status.", _selection_keyboard(operation_id, "ms", statuses))
        elif field == "ms":
            status_codes = {"r": "Running", "i": "Idle", "m": "Maintenance", "o": "Out of Service"}
            if user.application_role != "Admin" or value not in status_codes:
                raise TelegramAccessError("Only Admin users can set an approved machine status.")
            payload["status"] = status_codes[value]
            updated = update_pending_operation(operation_id, user.telegram_user_id, "confirm", payload)
            await _reply(update, _confirmation_text(updated), _operation_keyboard(operation_id))
        else:
            raise ValidationError("Unsupported selection.")
    except (FactoryError, ValueError) as exc:
        record_telegram_rejection(
            user.telegram_user_id, str(exc), user.application_role, operation_id or None
        )
        await _reply(update, str(exc), MENU)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.error("Telegram update failed with a sanitized application error.")
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "The Telegram service could not complete that request. No unconfirmed record was saved."
        )


async def _heartbeat_loop(application: Application) -> None:
    instance_id = application.bot_data["instance_id"]
    while True:
        heartbeat_bot(instance_id)
        await asyncio.sleep(30)


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "Open the factory bot menu"),
        BotCommand("help", "Show deterministic commands"),
        BotCommand("production", "Submit production"),
        BotCommand("expense", "Submit an expense"),
        BotCommand("attendance", "Submit attendance"),
        BotCommand("machine", "View or update machine status"),
        BotCommand("today", "View today's summary"),
        BotCommand("status", "View Excel sync status"),
        BotCommand("cancel", "Cancel a pending operation"),
    ])
    application.bot_data["heartbeat_task"] = asyncio.create_task(_heartbeat_loop(application))


async def post_shutdown(application: Application) -> None:
    task = application.bot_data.get("heartbeat_task")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def build_application(config: TelegramConfig, instance_id: str) -> Application:
    application = (
        ApplicationBuilder().token(config.bot_token).post_init(post_init).post_shutdown(post_shutdown).build()
    )
    application.bot_data.update({"config": config, "instance_id": instance_id})
    for command, handler in {
        "start": start_command,
        "help": help_command,
        "production": production_command,
        "expense": expense_command,
        "attendance": attendance_command,
        "machine": machine_command,
        "today": today_command,
        "status": status_command,
        "cancel": cancel_command,
    }.items():
        application.add_handler(CommandHandler(command, handler))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_handler))
    application.add_error_handler(error_handler)
    return application


async def test_bot_connection() -> tuple[bool, str]:
    config = load_telegram_config()
    from telegram import Bot

    try:
        async with Bot(config.bot_token) as bot:
            identity = await bot.get_me()
            return True, f"Connected to @{identity.username or identity.first_name}."
    except Exception:
        return False, "Telegram connection failed. Check the token and network access."


def main() -> None:
    config = load_telegram_config()
    configure_logging(config.bot_token)
    database.initialize_database()
    instance_id = str(uuid.uuid4())
    try:
        acquire_bot_lease(instance_id)
        application = build_application(config, instance_id)
        LOGGER.info("Telegram polling service starting.")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
            bootstrap_retries=-1,
            stop_signals=(signal.SIGINT, signal.SIGTERM),
        )
    except Exception as exc:
        release_bot_lease(
            instance_id, "Telegram polling service stopped after an API or runtime failure.", True
        )
        LOGGER.error("Telegram polling service stopped after a sanitized runtime failure.")
        raise SystemExit(1) from exc
    else:
        release_bot_lease(instance_id)
        LOGGER.info("Telegram polling service stopped cleanly.")


if __name__ == "__main__":
    main()
