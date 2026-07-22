# Telegram Commands

## General

- `/start` displays the secure menu after numeric-ID authorization.
- `/help` lists commands and permissions.
- `/cancel` cancels the current incomplete operation without changing factory records.
- `/today` reports today's production quantity, earnings, expenses, profit, attendance totals, and machine counts.
- `/status` reports Excel sync state and last successful synchronization time.

## Mutations

- `/production` collects date, registered machine, operator, product, positive quantity, and positive rate. It shows the calculated total. Admin and Staff may submit.
- `/expense` collects date, category, positive amount, and optional description. Admin and Staff may submit; Telegram cannot edit or delete existing expenses.
- `/attendance` uses buttons for an active employee and Present, Absent, Leave, or Late, then collects date and optional notes. Duplicate employee/date entries are rejected. Admin and Staff may submit.
- `/machine` shows status to authorized users. Admin receives buttons for Running, Idle, Maintenance, or Out of Service. Staff access is read-only.

## Confirmation Buttons

- **Confirm and Save** performs one validated SQLite transaction and then attempts Excel synchronization.
- **Edit** cancels the existing UUID and restarts the same deterministic workflow.
- **Cancel** records cancellation and creates no factory record.

Pending workflows survive service restarts because their state is stored in SQLite. They expire after 20 minutes.
