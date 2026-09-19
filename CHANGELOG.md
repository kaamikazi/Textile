# Changelog

## Unreleased - 2026-09-19 - Natural-language Telegram intake

- Added optional free-text Telegram entry (`gemini_intake.py`): one message such as "machine 3, rafiq made 250 round-neck tshirts today at 12 taka each" now produces the same Confirm / Edit / Cancel screen as `/production`.
- Parsing never writes to SQLite and never skips confirmation; it only produces a payload that `create_pending_operation` accepts, so the write still happens in `confirm_pending_operation`.
- Refuses rather than guesses: a missing quantity, rate or amount, an ambiguous production/expense message, or an unregistered or ambiguous machine all fall back to the existing menu prompt.
- Hooked at the point where a free-text message previously had nowhere to go, so authorization, chat restriction and rate limiting are enforced before any API call, and a workflow already in progress is never interrupted.
- Entries from this path are tagged `[natural-language]` in the audit log with the source message, so an Admin can review how each was read.
- Added `google-genai` to requirements, `GEMINI_API_KEY` to the env and secrets examples, and an optional-dependency warning to `run_telegram_bot.cmd`.
- Model pinned to `gemini-3.5-flash-lite`, checked against Google's current list: the 2.0 Flash models are shut down.
- Added 25 tests that mock the client entirely, so no API key or network access is needed in CI.

## Unreleased - 2026-09-19 - Production hardening

- Fixed `run_app.cmd`, `run_telegram_bot.cmd` and `start_app.vbs`, which hardcoded a per-user AI tooling cache interpreter that no longer has Streamlit installed; both launchers were broken, and `start_app.vbs` failed silently. They now resolve Python at run time, verify dependencies, and report failures.
- Fixed `restore_database()` refusing to run when the live database was corrupted - the exact case it exists for - because its pre-restore snapshot required the live file to be valid.
- Fixed production and expense pages showing stale ledgers and totals after a save, because both query their tables before the submit handler runs.
- Fixed mutation and confirmation messages being discarded by an immediate `st.rerun()`, which made the "Excel sync failed - your data is safe in the database" warning unreachable.
- Fixed a malformed `.streamlit/secrets.toml` being reported as "token not configured" instead of naming the parse error.
- Declared the previously undeclared `toml` dependency used by the Python 3.10 `tomllib` fallback.
- Added `.github/workflows/ci.yml`: ruff plus pytest on Windows and Linux for Python 3.10, 3.11 and 3.12.
- Added `pyproject.toml` pinning an explicit ruff rule set so lint is reproducible in CI.
- Added `tests/test_failure_paths.py` (14 tests) covering locked Excel files, corrupted and truncated backups, a full corrupt-and-restore recovery drill, Telegram confirmations racing expiry, and transaction rollback. 34 tests before, 48 after.
- Hardened `database.connect()` against leaking a handle when a PRAGMA fails.
- Corrected the README's Python requirement (3.10+, not 3.11+) to match the code and the factory PC.
- Added `PRODUCTION_READINESS.md` recording what was fixed, what needs Imran's decision, and what was out of scope.

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
