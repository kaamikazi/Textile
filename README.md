# Al Sadi Knitwear Factory OS

Version **v1.2.0** is a local Streamlit factory-management application backed by SQLite, with Plotly dashboards, protected Excel exports, and a separately operated secure Telegram submission bot.

## Installation

For Python 3.11 or newer:

```powershell
cd "E:\al sadi"
python -m pip install -r requirements.txt
```

## Running

```powershell
python -m streamlit run app.py --server.port 8501
```

On this computer, run `run_app.cmd` or double-click `start_app.vbs`, then open `http://localhost:8501/`.

The Telegram polling service is a separate process and is never started by Streamlit:

```powershell
$env:TELEGRAM_BOT_TOKEN="token-from-BotFather"
.\run_telegram_bot.cmd
```

## First Admin

The first launch shows a one-time Admin setup screen. Passwords require at least 10 characters with a letter and number. Passwords are salted and hashed with PBKDF2; no default password is supplied.

## Permissions

**Admin** can add and manage operational records, archive employees, manage machines, create/restore backups, manage local users, view audit logs, and export reports.

**Staff** can view factory pages and add production, expenses, and attendance. Staff cannot edit/delete records, manage users/machines/employees, create or restore backups, or view audit logs.

Telegram access is independently allowlisted by numeric Telegram user ID in **Settings > Telegram Automation**. Telegram Staff can submit confirmed production, expenses, and attendance. Telegram Admin can also change machine status. No Telegram submission reaches SQLite before confirmation.

## Data Architecture

`factory.db` is authoritative. Each successful mutation commits to SQLite first and then rebuilds `factory_records.xlsx`. A failed Excel sync never rolls back a valid database save; the UI shows **Failed** or **Out of date** and offers a retry.

The workbook contains `Production`, `Expenses`, `Employees`, `Attendance`, `Machines`, and `Summary`. Attendance and summary data are protected. Only raw Production and Expenses rows are unlocked for controlled local editing. Workbook edits are not imported into SQLite.

## Backup And Restore

Open **Settings > Data Safety** as Admin to back up SQLite, Excel, or both. Backups are stored in `backups` with timestamped, non-overwriting names and are downloadable.

Restore accepts only a validated `.db` file from the configured backups folder and requires typing `RESTORE`. The app backs up the current database before replacement and validates SQLite integrity afterward.

## Excel And OneDrive

The app updates a local workbook only. Excel Web does not update automatically. Download the workbook and upload or sync it through OneDrive. Microsoft Graph integration is not included in v1.2.

Close `factory_records.xlsx` in desktop Excel before retrying a failed sync; Windows may lock the file while it is open.

## Telegram Automation

The bot supports `/start`, `/help`, `/production`, `/expense`, `/attendance`, `/machine`, `/today`, `/status`, and `/cancel`. Workflow state, expiry, authorization, and idempotency receipts are persisted in SQLite. Telegram never writes directly to Excel; confirmed transactions trigger the normal post-commit Excel synchronization.

Read `TELEGRAM_SETUP.md`, `TELEGRAM_SECURITY.md`, and `TELEGRAM_COMMANDS.md` before enabling the bot.

## Recovery

1. Stop Streamlit and keep the damaged file unchanged for investigation.
2. Start the app and open **Settings > Data Safety**.
3. Select a known-good database backup and type `RESTORE`.
4. Confirm the integrity success message, sign in again if requested, and regenerate Excel.

Pre-v1.1 migration backups are retained in `backups`.

## Testing

Tests always use temporary databases and workbooks:

```powershell
python -m pytest -q
```

## Troubleshooting

- **No module named streamlit/plotly:** install `requirements.txt` with the same interpreter used to launch the app.
- **Excel sync failed:** close Excel, verify write access, then use **Retry Excel Sync**.
- **Duplicate attendance:** one entry per employee/date is enforced; edit the existing entry.
- **Login unavailable:** complete First Admin Setup, or ask an Admin to enable the account.
- **Port 8501 busy:** stop the old process or choose another `--server.port` value.

Local authentication is for trusted factory networks and is not enterprise identity or internet-edge security. See `SECURITY_NOTES.md`.
