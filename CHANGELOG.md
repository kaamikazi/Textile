# Changelog

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
