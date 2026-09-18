# Changelog

## Unreleased - UI/UX pass

- Replaced the `glass_open`/`glass_close` card pair with a real `card()` container. The old helpers emitted an unclosed `<div>` into their own markdown block, so every card rendered empty and its content fell outside it.
- Rebuilt the sidebar as grouped button navigation; two independent radio groups could previously both show a selected row.
- Added a token-driven stylesheet: 4px spacing scale, 1.2 type scale, tabular figures, and semantic colour (green gain, red loss, amber attention) with cyan reserved for interaction.
- Loaded an offline-safe system font stack; the previous CSS requested Inter but never loaded it.
- Stopped forcing `position: fixed` on the sidebar while zeroing the app container margin, which let main content slide underneath at some widths.
- Surfaced Excel sync state on every data page with an inline Retry, instead of only in Settings and Reports.
- Rebuilt the dashboard around today: production value, profit vs yesterday, machine utilisation and attendance rate, with month-to-date and all-time below.
- Replaced the earnings-only area chart with income/expense/profit trends, ranked product value, and per-machine output coloured by status.
- Split Employees into Attendance and Employee Records tabs; salary and advance stay on the Admin view.
- Added empty states, inline field validation, loading spinners, formatted table columns and distinct styling for destructive actions.
- Made layouts wrap rather than crush at 1024px and 768px for tablet use.

## v1.2.0 - 2026-07-22

- Added a separate `python-telegram-bot` polling service with deterministic button-guided workflows.
- Added numeric Telegram ID allowlisting with Admin/Staff roles, suspension, removal, and Admin-only management.
- Added persisted pending operations, 20-minute expiry, confirmation gates, cancellation, and idempotency receipts.
- Added transactional Telegram production, expense, attendance, and Admin machine updates with activities and audit logs.
- Added private-chat/approved-group restrictions, persistent rate limiting, duplicate bot-instance leasing, and token-redacted structured logging.
- Added `/today` factory summaries and `/status` Excel synchronization reporting without sensitive employee or configuration data.
- Added Admin Telegram Automation views for connection health, users, pending actions, rejected submissions, and setup guidance.
- Added BotFather, command, token rotation, emergency disable, polling, recovery, and security documentation.
- Added disposable and mocked Telegram tests while retaining all v1.1 safety coverage.

## v1.1.0 - 2026-07-22

- Removed automatic startup demo seeding and added explicit guarded demo initialization.
- Added local Admin/Staff authentication, secure password hashing, logout, and role enforcement.
- Added attendance history, filters, monthly totals, duplicate prevention, editing, and protected export.
- Added safe CRUD, employee archiving, transactions, validation, money rounding, and audit logging.
- Added atomic six-sheet Excel generation, selected-month exports, sync status, and manual retry.
- Added validated SQLite/Excel backups, downloads, listing, and confirmation-protected restore.
- Replaced deprecated Streamlit width usage while preserving the dark cyan dashboard design.
- Added isolated tests, migration notes, recovery guidance, and security documentation.
