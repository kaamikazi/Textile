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

## Free-Text Entry

When no workflow is in progress, a plain message is read as a production or
expense entry instead of the button flow. Send one message:

> machine 3, rafiq made 250 round-neck tshirts today at 12 taka each

> bought yarn for 4500 taka

It lands on the **same Confirm / Edit / Cancel screen** the slash commands
produce, and nothing is written until you tap **Confirm and Save**. The
buttons behave identically.

The message is only read this way when it is **complete and unambiguous**.
It falls back to `Choose a command from the menu first.` when:

- a quantity, rate or amount is missing - numbers are never guessed;
- the message could be either a production or an expense entry;
- the machine is not registered, or the name matches more than one machine;
- free-text entry is not configured, or the service is unreachable.

When that happens, use `/production` or `/expense` and answer step by step.

Notes:

- Free-text entry never applies mid-workflow. If you are answering a
  `/production` prompt, your reply answers that prompt as usual.
- It covers production and expenses only. Attendance and machine status
  still use `/attendance` and `/machine`.
- The same authorization and rate limits apply as to any command.
- Entries created this way are tagged `[natural-language]` in the audit log,
  alongside the message they came from, so an Admin can review how each was
  read. See **Settings > Telegram Automation > Recent Actions**.

## Confirmation Buttons

- **Confirm and Save** performs one validated SQLite transaction and then attempts Excel synchronization.
- **Edit** cancels the existing UUID and restarts the same deterministic workflow.
- **Cancel** records cancellation and creates no factory record.

Pending workflows survive service restarts because their state is stored in SQLite. They expire after 20 minutes.
