# v1.1 Migration

## Safety

Validated copies of the live files were created before migration:

- `backups/factory_pre_v1.1_2026-07-22_21-37-39.db`
- `backups/factory_records_pre_v1.1_2026-07-22_21-37-39.xlsx`

The migration is additive and never resets operational tables.

## Schema Changes

- Adds `updated_at` to production, expenses, employees, attendance, and machines where absent.
- Adds `archived_at` to employees while retaining legacy `attendance_days` for compatibility; it is no longer editable or authoritative.
- Adds `audit_logs`, `auth_users`, `excel_sync_status`, `attendance_duplicate_archive`, and `schema_migrations`.
- Adds attendance uniqueness and date, employee, machine, activity, and audit indexes.
- Archives only duplicate attendance rows found during migration, retaining the highest ID as active.

## First Launch

1. Start the app normally and create the first Admin account.
2. Review **Settings > Data Safety**.
3. Use **Retry Excel Sync** to regenerate the six-sheet workbook.
4. Keep the pre-migration backups until the release is accepted.

Startup creates or migrates schema only. It creates no operational or activity records.
