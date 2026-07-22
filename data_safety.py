from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

from database import Actor, DB_PATH, ValidationError, initialize_database, transaction, write_audit_log
from spreadsheet_sync import BACKUPS_PATH, WORKBOOK_PATH, backup_excel_file


REQUIRED_FACTORY_TABLES = {"production_entries", "expenses", "employees", "machines"}


def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    candidate = directory / f"{stem}_{timestamp}{suffix}"
    index = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{timestamp}_{index}{suffix}"
        index += 1
    return candidate


def validate_sqlite_database(path: str | Path) -> tuple[bool, str]:
    database_path = Path(path)
    if not database_path.exists() or database_path.stat().st_size == 0:
        return False, "Database file is missing or empty."
    try:
        with closing(sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                return False, f"SQLite integrity check failed: {integrity}"
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            missing = REQUIRED_FACTORY_TABLES - tables
            if missing:
                return False, f"Database is missing required tables: {', '.join(sorted(missing))}."
    except sqlite3.DatabaseError as exc:
        return False, f"Invalid SQLite database: {exc}"
    return True, "SQLite integrity check passed."


def backup_database(
    actor: Actor,
    source_path: str | Path | None = None,
    backup_dir: str | Path | None = None,
    audit: bool = True,
) -> Path:
    source = Path(source_path) if source_path is not None else DB_PATH
    valid, message = validate_sqlite_database(source)
    if not valid:
        raise ValidationError(message)
    destination = _unique_path(
        Path(backup_dir) if backup_dir is not None else BACKUPS_PATH,
        "factory_backup",
        ".db",
    )
    with closing(sqlite3.connect(source)) as source_conn, closing(sqlite3.connect(destination)) as backup_conn:
        source_conn.backup(backup_conn)
    valid, message = validate_sqlite_database(destination)
    if not valid:
        destination.unlink(missing_ok=True)
        raise ValidationError(f"Generated database backup is invalid: {message}")
    if audit:
        with transaction(source) as conn:
            write_audit_log(
                conn, actor, "backup", "database", destination.name,
                f"SQLite backup created: {destination.name}.",
            )
    return destination


def backup_excel(
    actor: Actor,
    source_path: str | Path | None = None,
    backup_dir: str | Path | None = None,
    db_path: str | Path | None = None,
) -> Path:
    backup_path, message = backup_excel_file(source_path, backup_dir)
    if backup_path is None:
        raise ValidationError(message)
    destination = Path(backup_path)
    with transaction(db_path) as conn:
        write_audit_log(
            conn, actor, "backup", "excel", destination.name,
            f"Excel backup created: {destination.name}.",
        )
    return destination


def backup_both(
    actor: Actor,
    db_path: str | Path | None = None,
    workbook_path: str | Path | None = None,
    backup_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    database_path = Path(db_path) if db_path is not None else DB_PATH
    db_backup = backup_database(actor, database_path, backup_dir, audit=False)
    excel_path, message = backup_excel_file(workbook_path, backup_dir)
    if excel_path is None:
        raise ValidationError(message)
    excel_backup = Path(excel_path)
    with transaction(database_path) as conn:
        write_audit_log(
            conn, actor, "backup", "database_and_excel", None,
            f"Combined backup created: {db_backup.name} and {excel_backup.name}.",
        )
    return db_backup, excel_backup


def list_backups(backup_dir: str | Path | None = None) -> list[dict[str, object]]:
    directory = Path(backup_dir) if backup_dir is not None else BACKUPS_PATH
    if not directory.exists():
        return []
    rows: list[dict[str, object]] = []
    for path in directory.iterdir():
        if path.is_file() and path.suffix.lower() in {".db", ".xlsx"}:
            stat = path.stat()
            rows.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "type": "SQLite" if path.suffix.lower() == ".db" else "Excel",
                    "size_bytes": stat.st_size,
                    "size_kb": round(stat.st_size / 1024, 1),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                }
            )
    return sorted(rows, key=lambda row: str(row["modified_at"]), reverse=True)


def restore_database(
    backup_path: str | Path,
    typed_confirmation: str,
    actor: Actor,
    target_path: str | Path | None = None,
    backup_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    if typed_confirmation != "RESTORE":
        raise ValidationError("Type RESTORE exactly to confirm database restoration.")
    source = Path(backup_path).resolve()
    directory = (Path(backup_dir) if backup_dir is not None else BACKUPS_PATH).resolve()
    if directory not in source.parents or source.suffix.lower() != ".db":
        raise ValidationError("Select a database backup from the configured backups folder.")
    valid, message = validate_sqlite_database(source)
    if not valid:
        raise ValidationError(message)

    target = Path(target_path) if target_path is not None else DB_PATH
    pre_restore = backup_database(actor, target, directory, audit=False)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".restore_", suffix=".db", delete=False
        ) as handle:
            temp_path = Path(handle.name)
        with closing(sqlite3.connect(source)) as source_conn, closing(sqlite3.connect(temp_path)) as temp_conn:
            source_conn.backup(temp_conn)
        valid, message = validate_sqlite_database(temp_path)
        if not valid:
            raise ValidationError(message)
        os.replace(temp_path, target)
        temp_path = None
        initialize_database(target)
        valid, message = validate_sqlite_database(target)
        if not valid:
            raise ValidationError(message)
        with transaction(target) as conn:
            write_audit_log(
                conn, actor, "restore", "database", source.name,
                f"Database restored from {source.name}; pre-restore backup is {pre_restore.name}.",
            )
        return target, pre_restore
    except Exception:
        if pre_restore.exists():
            with closing(sqlite3.connect(pre_restore)) as source_conn, closing(sqlite3.connect(target)) as target_conn:
                source_conn.backup(target_conn)
        raise
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def record_export(actor: Actor, description: str, db_path: str | Path | None = None) -> None:
    with transaction(db_path) as conn:
        write_audit_log(conn, actor, "export", "excel", None, description)
