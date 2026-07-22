# v1.2 Migration

## Pre-Migration Backups

- `backups/factory_pre_v1.2_2026-07-22_22-16-25.db`
- `backups/factory_records_pre_v1.2_2026-07-22_22-16-25.xlsx`

## Additive Tables

- `telegram_users`: numeric-ID allowlist, role, status, creator, and last-seen time.
- `telegram_pending_operations`: persisted deterministic workflow state and expiry.
- `telegram_mutation_receipts`: operation UUID idempotency receipts.
- `telegram_rate_limits`: persistent abuse controls.
- `telegram_bot_status`: single-instance lease and heartbeat state.

The migration adds supporting indexes and schema version `1.2.0`. It does not alter existing production, expense, employee, attendance, machine, activity, authentication, or audit records.

The Telegram bot does not start during migration or Streamlit startup. Configure the token and launch `telegram_bot.py` separately after authorizing numeric user IDs.
