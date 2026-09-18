# Production Readiness Review — 2026-09-19

Scope: bring Al Sadi Knitwear Factory OS v1.2.0 to a production-ready state
for its real use case — **one Windows PC running the app for factory staff** —
without changing its local/offline architecture.

Branch: `claude/production-hardening`.
Baseline: 34 tests passing. Now: **48 tests passing**, `ruff check .` clean,
on Python 3.10 and 3.12.

Nothing in `SECURITY_NOTES.md` was weakened. PBKDF2 iteration count, the
"no plaintext secrets" rule, the Admin/Staff permission model, the audit-log
behaviour and the "SQLite commits first, Excel sync follows" ordering are all
unchanged.

---

## Fixed this run

### The app could not be started by its own launchers — `run_app.cmd`, `run_telegram_bot.cmd`, `start_app.vbs`

This was the highest-impact defect found.

Both `.cmd` files hardcoded
`C:\Users\imran\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`
— a per-user **AI tooling cache directory**, not a managed Python install.
That interpreter still exists but no longer has Streamlit installed, so both
launchers were broken at the time of this review. `streamlit.log` shows the app
last started successfully on 2026-07-22, before that cache was cleaned.

Worse, `start_app.vbs` ran the launcher with window style `0` and did not wait
for it, so double-clicking the desktop shortcut failed **completely silently** —
no window, no error, nothing.

- Both `.cmd` files now resolve the interpreter at run time (`py -3`, then
  `python` on `PATH`), verify the required packages are importable, and write an
  actionable message naming the interpreter and the exact `pip` command instead
  of dying into a log file.
- Paths now use `%~dp0` instead of a hardcoded `E:\al sadi`, so the folder can
  be moved or renamed.
- `run_telegram_bot.cmd` additionally checks that a token is configured before
  starting.
- `start_app.vbs` waits for the launcher and shows a message box with the exit
  code and where to look when startup fails.

Verified by executing the real scripts: the happy path resolves `py -3` and
exits 0; forcing an interpreter that genuinely lacks the packages exits 1
without launching and logs the fix.

### Database restore failed on a corrupted database — `data_safety.py`

`restore_database()` took its pre-restore snapshot by calling
`backup_database()`, which begins by validating the source and raises
`ValidationError` if it is not a readable SQLite file.

So restore refused to run **in exactly the situation it exists for**. With a
damaged `factory.db`, step 3 of the README's recovery procedure ("select a
known-good backup and type RESTORE") failed with
`Invalid SQLite database: file is not a database`, and the operator had no
in-app way back.

- New `_preserve_current_database()` snapshots the live file without requiring
  it to be valid: a proper SQLite backup when readable, a raw byte copy named
  `factory_corrupt_<timestamp>.db` when not — so the damaged file is still
  preserved for investigation instead of blocking recovery. A missing target is
  handled too.
- The failure rollback inside `restore_database()` now verifies the preserved
  snapshot is itself readable before copying it back, so a corrupt snapshot
  cannot be written over the target and mask the original error.

### Writes succeeded but the UI showed stale data — `pages/production.py`, `pages/expenses.py`

Both pages query their tables near the top of `render()`, before the form
submit handler further down runs. A freshly inserted row is therefore not in
the DataFrame already fetched on that pass, and neither page called
`st.rerun()`. The operator saw "Production entry saved" above a ledger and a
"Today's Output" tile that did not contain the row.

### Mutation and confirmation messages were unreachable — all mutation pages, `pages/settings.py`, `pages/telegram_admin.py`

Every edit/delete path called `show_mutation_result(...)` and then `st.rerun()`
immediately, which discards the render that had just drawn the message.

This silently defeated the Excel sync warning: **"sync failed — your data is
safe in the database"** is how an operator learns the workbook is stale, and it
could never appear. The same pattern hid "user created", "user status updated",
"Telegram access updated" and "Excel synchronization completed".

- `ui.flash_mutation()` / `ui.flash_notice()` store the outcome in session state
  and rerun; `ui.show_flash()` draws it at the top of the next run. The page
  re-reads the database **and** the message survives.
- Applied across production, expenses, machines, employees, settings and the
  Telegram panel. No remaining `st.success(...)` immediately followed by
  `st.rerun()`.

Durability is unaffected — the SQLite commit has already happened by the time a
`MutationResult` exists.

### A broken `secrets.toml` reported the wrong problem — `telegram_config.py`

A `secrets.toml` that existed but did not parse was swallowed and surfaced as
"token not configured", sending the admin to check the environment variable
instead of the syntax error in their file.

- Parse failures now raise `TelegramConfigError` naming the parse error.
- `telegram_config_problem()` exposes it without breaking page rendering;
  `telegram_token_is_configured()` still never raises.
- The Telegram admin view displays it.
- A missing `secrets.toml` remains normal (the token may come from the
  environment).

### Undeclared dependency — `requirements.txt`

`telegram_config.py` falls back to the third-party `toml` package when `tomllib`
is unavailable (Python < 3.11), but `requirements.txt` never declared it. It
worked only because the package happened to be present on this machine.
Declared as `toml>=0.10; python_version < "3.11"`.

### Static analysis — `pyproject.toml` and 20 source files

`ruff check .` was not reproducible: with no config in the repo it picked up
settings from outside it. Added `pyproject.toml` pinning an explicit rule set
(`E4/E7/E9`, `F`, `B`, `I`, `UP`, `C4`, `SIM`, `RUF`), with a written reason
for every ignored family.

Real fixes, not just style:
- `pages/reports.py`: `zip()` had no `strict=`, so if the tab list and period
  list ever drifted apart one report tab would silently render empty.
- `telegram_bot.py`: `raise SystemExit(1)` inside an `except` discarded the
  original traceback; now chains with `from exc`. `try/except/pass` around task
  cancellation replaced with `contextlib.suppress`.
- Removed 4 unused imports; dropped 2 unused unpacked variables in `app.py`.
- `database.py`: `connect()` now closes the handle if a `PRAGMA` fails on a
  damaged file, instead of leaking it.

Style-only changes (import ordering across 17 files, `dict.fromkeys`, list
unpacking) were verified behaviour-neutral by the test suite.

**All 33 `except Exception` sites were reviewed by hand rather than silenced.**
Each one surfaces the failure through `show_factory_error`, records it as an
Excel sync status, or re-raises. None discard an error. There are no bare
`except:`, no mutable default arguments, and no unclosed file handles.

### Tests — `tests/test_failure_paths.py` (new, 14 tests)

Prioritised failure paths a factory user actually hits:

| Test | Guarantee |
|---|---|
| Locked workbook during production save | Row still committed, status `Failed`, message mentions Excel |
| Locked workbook across expense + attendance | Same for every mutation type |
| Sync recovers after the lock clears | Later write returns to `Synced`; earlier row never lost |
| Mid-write failure | Atomic workbook write leaves no temp file behind |
| Full recovery drill | Back up → corrupt the live DB → restore → integrity passes, data intact, restore audited |
| Truncated backup | Rejected; live DB untouched and still usable |
| Wrong confirmation word | Restore refused |
| Telegram confirm after expiry | Nothing saved, with and without a prior expiry sweep |
| Expiry scope | One user's expiry does not cancel another's workflow |
| Transaction rollback | Mid-transaction failure leaves no partial rows |
| Rejected validation | Writes neither a row nor an audit entry |
| Malformed `secrets.toml` | Reports itself instead of looking like "no token" |

### CI — `.github/workflows/ci.yml` (new)

The one piece of infrastructure missing entirely.

- `lint`: `ruff check .` with ruff pinned to `0.16.8`, rules from
  `pyproject.toml` so CI matches local output.
- `test`: `pytest -q` on `ubuntu-latest` and `windows-latest` for 3.11 and 3.12,
  **plus `windows-latest` on 3.10** because that is the interpreter the factory
  PC actually runs. An explicit import check runs first, so `requirements.txt`
  drifting from what the code imports fails loudly.
- Triggers on push and pull_request for both `main` and `master` (see below).

### Documentation — `README.md`, `.gitignore`

- README claimed "Python 3.11 or newer" while the factory PC runs 3.10 and
  `telegram_config.py` has an explicit 3.10 code path. Now documents 3.10+ with
  3.11+ recommended.
- Added the interpreter-matching warning and a troubleshooting entry for the
  previously silent `start_app.vbs` failure. Added a CI badge.
- `.gitignore` now excludes virtualenvs and `.ruff_cache`.

### Verified, not changed

- **Transaction handling in `database.py` is correct.** `BEGIN IMMEDIATE`,
  commit on success, rollback and re-raise on any exception, connection always
  closed in `finally`. Every mutation goes through it.
- **`_sync_after_commit()` can never roll back a committed save.** It catches
  everything, records the status, and returns — it does not raise.
- **The `restore_database()` failure path already restored from its pre-restore
  backup and re-raised.**
- **Git history is clean.** `git log --all --full-history` over `factory.db`,
  `.env` and `.streamlit/secrets.toml` returns nothing; no data or secret file
  was ever committed, and no token patterns appear in any commit. **No history
  rewrite is needed.**
- **A real from-scratch install works.** Built a clean `git archive` checkout,
  followed only the README, and got 48 passing tests. That install resolves to
  pandas 3.0.6, plotly 7.1.0 and streamlit 1.64.0 — major versions ahead of this
  machine — and everything still imports and passes.

---

## Needs Imran's decision

These were deliberately left alone. They are scope or architecture calls, not
defects.

### 1. The app is currently reachable from the public internet

`run_app.cmd` passes `--server.address 0.0.0.0`, which binds every network
interface. `streamlit.log` from the last successful run records:

```
External URL: http://27.147.150.254:8501
```

That is a **public IP**, not a LAN address. `SECURITY_NOTES.md` states plainly:
*"Do not expose Streamlit directly to the public internet."* The app has local
password auth with PBKDF2 and role checks, but no TLS, no rate limiting on
login, no MFA and no lockout — it is not built to sit on the open internet.

I did not change the bind address, because narrowing it could cut off factory
tablets that legitimately use it, and that is an access decision, not a defect.

**Options:**
- **Keep LAN access, close the internet path.** Leave `0.0.0.0` but block
  inbound 8501 at the router / Windows Firewall so only the factory LAN reaches
  it. Lowest disruption; recommended.
- **Bind to the LAN IP only** (`--server.address 192.168.1.134`). Stops
  accidental exposure if the firewall changes, but breaks if the PC's IP moves.
- **Localhost only** (`127.0.0.1`). Most secure; ends tablet access entirely.

This is the one item I would act on first regardless of everything else below.

### 2. Stay single-machine/local, or move toward multi-user/hosted

Today the architecture is deliberately local: SQLite on one PC, app auth for a
trusted network, Excel as the exchange format. That is coherent and it works.

Moving to multi-user/hosted is not an incremental change. It forces, together:
- **A real concurrent-write database.** SQLite with `BEGIN IMMEDIATE` and a
  15-second busy timeout is fine for one machine; it is the wrong tool for
  several writers over a network. That means PostgreSQL and a migration.
- **A different auth model.** Local `auth_users` with PBKDF2 is sound for a
  trusted LAN. Internet-facing multi-user needs sessions/TLS/lockout/MFA and
  probably an identity provider.
- **Real deployment infrastructure.** Hosting, backups off the single box,
  monitoring, update path, TLS certificates.
- **Rethinking the Excel workflow.** "Rebuild the whole workbook after every
  commit" does not survive concurrent writers.

My read: the current design fits a single factory well. I would only take this
on if there is a concrete need (a second site, or office access from outside the
factory). If that need is real, it is a project in its own right, not a
hardening pass.

### 3. Packaged Windows installer vs the current `.cmd`/`.vbs` launchers

The launchers now work and fail loudly, so this is no longer urgent — but the
underlying fragility is that startup depends on whatever Python happens to be on
`PATH`.

**Options:**
- **Keep the scripts** (now hardened). Zero extra tooling. Still depends on a
  correct Python install.
- **Ship a bundled app** (PyInstaller / an embedded Python). Double-click, no
  interpreter to manage, no "wrong interpreter" failure ever again. Costs a
  build step and makes updates a rebuild.
- **Pin a dedicated virtualenv** under the app folder and have the launchers use
  it unconditionally. Much of the benefit, far less work than packaging.

I would pick the third if this PC is touched by anyone non-technical.

### 4. Branch naming: `master` vs `main`

The repository's default branch is **`master`**; `main` does not exist. The
instruction for this run referenced `main`, so the CI workflow triggers on both
and this PR targets `master` (the real default). Renaming is a one-line settings
change if you want it — I did not do it, since renaming the default branch
affects anyone with an existing clone.

### 5. Dependency pinning

`requirements.txt` uses lower bounds only (`>=`). A fresh install today pulls
pandas 3.x / plotly 7.x / streamlit 1.64 — major versions ahead of this machine's
pandas 2.3.3 / plotly 6.9.0 / streamlit 1.59.2. Everything passes on both, so
there is no bug today, but the factory PC and a new install are not running the
same code.

**Options:** pin exact versions (reproducible, manual security updates), add
upper bounds like `pandas>=2.2,<4` (a middle ground), or leave as-is and rely on
CI to catch breakage early. Given this machine is rarely touched, I lean toward
upper bounds.

### 6. Python version on the factory PC

The PC's default `python` is **3.10.11**, while `py -3` resolves to 3.12. The
hardened launchers prefer `py -3`, so after this change the app will start on
3.12 rather than 3.10. Both are tested in CI and all 48 tests pass on both, so
this is safe — but it is a change in which interpreter runs in production, and
you should know about it. If you would rather pin 3.10, change `py -3` to
`py -3.10` in both `.cmd` files.

### 7. Backup retention and off-machine copies

Backups are timestamped, never overwritten, and integrity-checked — but they
live in `backups/` **on the same disk as the live database**. A disk failure or
ransomware takes both. There is also no retention policy, so the folder grows
without limit.

Worth deciding: a scheduled copy to a USB drive or network share, and whether to
prune backups older than N months.

---

## Out of scope this run

- **The planned Gemini natural-language intake layer.** Not built yet. The
  Telegram payload keys (`production_date`, `machine_number`, `operator_name`,
  `product_type`, `quantity`, `rate_per_unit`; `expense_date`, `expense_type`,
  `amount`, `description`) and the `create_pending_operation` /
  `confirm_pending_operation` signatures are unchanged, so that integration can
  be built against them as planned.
- **WhatsApp integration, AI message parsing and PDF reports** — listed as
  future modules in the Reports "Roadmap" tab and explicitly deferred in
  `SECURITY_NOTES.md`.
- **Microsoft Graph / OneDrive sync.** The README documents the manual
  download-and-upload workflow; automating it was excluded from v1.2 by design.
- **Migrating existing factory data.** Nothing in this pass touches the schema,
  table names or column names, so no migration is required.
- **UI/visual design.** A separate pass already covered that; this run changed
  frontend files only where behaviour was wrong (stale reads, discarded
  messages, unreported config errors).
- **Load and performance testing.** With one machine, a handful of users and
  180 KB of data, there is no performance problem to solve.
- **Automated end-to-end browser tests.** The failure-path tests cover the
  data-layer guarantees. Driving Streamlit through a browser in CI would add
  significant fragility for little extra signal at this size.
