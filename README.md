# Al Sadi Knitwear Factory OS

Version **v1.1.0** is a local Streamlit factory-management application backed by SQLite, with Plotly dashboards and protected Excel exports.

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

## First Admin

The first v1.1 launch shows a one-time Admin setup screen. Passwords require at least 10 characters with a letter and number. Passwords are salted and hashed with PBKDF2; no default password is supplied.

## Permissions

**Admin** can add and manage operational records, archive employees, manage machines, create/restore backups, manage local users, view audit logs, and export reports.

**Staff** can view factory pages and add production, expenses, and attendance. Staff cannot edit/delete records, manage users/machines/employees, create or restore backups, or view audit logs.

## Data Architecture

`factory.db` is authoritative. Each successful mutation commits to SQLite first and then rebuilds `factory_records.xlsx`. A failed Excel sync never rolls back a valid database save; the UI shows **Failed** or **Out of date** and offers a retry.

The workbook contains `Production`, `Expenses`, `Employees`, `Attendance`, `Machines`, and `Summary`. Attendance and summary data are protected. Only raw Production and Expenses rows are unlocked for controlled local editing. Workbook edits are not imported into SQLite.

## Backup And Restore

Open **Settings > Data Safety** as Admin to back up SQLite, Excel, or both. Backups are stored in `backups` with timestamped, non-overwriting names and are downloadable.

Restore accepts only a validated `.db` file from the configured backups folder and requires typing `RESTORE`. The app backs up the current database before replacement and validates SQLite integrity afterward.

## Excel And OneDrive

The app updates a local workbook only. Excel Web does not update automatically. Download the workbook and upload or sync it through OneDrive. Microsoft Graph integration is not included in v1.1.

Close `factory_records.xlsx` in desktop Excel before retrying a failed sync; Windows may lock the file while it is open.

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
