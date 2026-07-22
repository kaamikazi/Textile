from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from auth import AuthenticatedUser, create_user, list_users, set_user_active
from data_safety import (
    backup_both,
    backup_database,
    backup_excel,
    list_backups,
    record_export,
    restore_database,
)
from database import (
    cleanup_duplicate_seed_activities,
    fetch_df,
    initialize_demo_data,
    set_excel_sync_status,
)
from spreadsheet_sync import sync_factory_workbook
from ui import glass_close, glass_open, page_header, show_factory_error, sync_status_panel
from pages.telegram_admin import render_telegram_automation


def _demo_mode_enabled() -> bool:
    env_enabled = os.getenv("AL_SADI_DEMO_MODE", "").lower() in {"1", "true", "yes", "on"}
    try:
        secret_enabled = bool(st.secrets.get("demo_mode", False))
    except Exception:
        secret_enabled = False
    return env_enabled or secret_enabled


def _remember_download(path: Path) -> None:
    st.session_state.setdefault("generated_backups", [])
    if str(path) not in st.session_state["generated_backups"]:
        st.session_state["generated_backups"].append(str(path))


def _download_file(path: Path, key: str) -> None:
    if path.exists():
        mime = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if path.suffix.lower() == ".xlsx"
            else "application/octet-stream"
        )
        st.download_button(
            f"Download {path.name}", path.read_bytes(), path.name, mime, key=key, width="stretch"
        )


def render(user: AuthenticatedUser) -> None:
    if user.role != "Admin":
        st.error("Administrator access is required.")
        return
    page_header("Settings & Data Safety", "Authentication, backups, restore, sync, and audit logs.", "Admin")

    sync_status_panel()
    if st.button("Retry Excel Sync", width="stretch"):
        try:
            path = sync_factory_workbook()
            set_excel_sync_status("Synced", f"Manual sync completed: {path.name}.")
            record_export(user.actor, "Manual Excel synchronization completed from Settings.")
            st.success("Excel synchronization completed.")
        except PermissionError:
            set_excel_sync_status("Failed", "Close factory_records.xlsx in Excel and retry.")
            st.error("Close factory_records.xlsx in Excel and retry.")
        except Exception as exc:
            set_excel_sync_status("Failed", f"Manual sync failed: {exc}")
            show_factory_error(exc)

    safety_tab, users_tab, telegram_tab, audit_tab, maintenance_tab = st.tabs(
        ["Data Safety", "Local Users", "Telegram Automation", "Audit Logs", "Maintenance"]
    )

    with safety_tab:
        glass_open("Create Backups")
        c1, c2, c3 = st.columns(3)
        if c1.button("Backup SQLite Database", width="stretch"):
            try:
                path = backup_database(user.actor)
                _remember_download(path)
                st.success(f"Database backup created: {path.name}")
            except Exception as exc:
                show_factory_error(exc)
        if c2.button("Backup Excel Workbook", width="stretch"):
            try:
                path = backup_excel(user.actor)
                _remember_download(path)
                st.success(f"Excel backup created: {path.name}")
            except Exception as exc:
                show_factory_error(exc)
        if c3.button("Backup Both", width="stretch"):
            try:
                db_backup, excel_backup = backup_both(user.actor)
                _remember_download(db_backup)
                _remember_download(excel_backup)
                st.success("Database and Excel backups created.")
            except Exception as exc:
                show_factory_error(exc)
        for index, stored_path in enumerate(st.session_state.get("generated_backups", [])):
            _download_file(Path(stored_path), f"generated_backup_{index}")
        glass_close()

        backups = list_backups()
        glass_open("Existing Backups")
        backup_frame = pd.DataFrame(backups)
        if backup_frame.empty:
            st.info("No backups found.")
        else:
            st.dataframe(
                backup_frame[["name", "type", "size_kb", "modified_at"]],
                width="stretch",
                hide_index=True,
            )
            selected_download = st.selectbox(
                "Download existing backup", backup_frame["name"].tolist(), key="download_existing"
            )
            selected_row = next(row for row in backups if row["name"] == selected_download)
            _download_file(Path(str(selected_row["path"])), "download_existing_backup")
        glass_close()

        database_backups = [row for row in backups if row["type"] == "SQLite"]
        glass_open("Restore SQLite Database")
        st.warning("Restore replaces live database contents. A pre-restore backup is created automatically.")
        if database_backups:
            selected_restore = st.selectbox(
                "Database backup", [row["name"] for row in database_backups], key="restore_backup"
            )
            confirmation = st.text_input("Type RESTORE to confirm", key="restore_confirmation")
            if st.button("Restore Selected Database", width="stretch"):
                try:
                    row = next(item for item in database_backups if item["name"] == selected_restore)
                    _, pre_restore = restore_database(
                        str(row["path"]), confirmation, user.actor
                    )
                    _remember_download(pre_restore)
                    st.success(
                        f"Database restored. Pre-restore backup: {pre_restore.name}. Sign in again if needed."
                    )
                except Exception as exc:
                    show_factory_error(exc)
        else:
            st.info("Create a SQLite backup before using restore.")
        glass_close()

    with users_tab:
        glass_open("Create Local User")
        with st.form("create_local_user", clear_on_submit=True):
            username = st.text_input("Username")
            password = st.text_input("Temporary password", type="password")
            role = st.selectbox("Role", ["Staff", "Admin"])
            submitted = st.form_submit_button("Create User", width="stretch")
        if submitted:
            try:
                create_user(username, password, role, user.actor)
                st.success(f"{role} user created.")
            except Exception as exc:
                show_factory_error(exc)
        glass_close()

        users = list_users()
        st.dataframe(users, width="stretch", hide_index=True)
        if not users.empty:
            selected_user = st.selectbox(
                "Manage user", users["username"].tolist(), key="manage_user"
            )
            row = users[users["username"] == selected_user].iloc[0]
            enable = not bool(row["is_active"])
            if st.button(f"{'Enable' if enable else 'Disable'} {selected_user}"):
                try:
                    set_user_active(int(row["id"]), enable, user.actor)
                    st.success("User status updated.")
                    st.rerun()
                except Exception as exc:
                    show_factory_error(exc)

    with telegram_tab:
        render_telegram_automation(user)

    with audit_tab:
        logs = fetch_df("SELECT * FROM audit_logs ORDER BY timestamp DESC, id DESC")
        if logs.empty:
            st.info("No audit records yet.")
        else:
            c1, c2, c3 = st.columns(3)
            username_filter = c1.selectbox("Username", ["All"] + sorted(logs["username"].unique().tolist()))
            action_filter = c2.selectbox("Action", ["All"] + sorted(logs["action"].unique().tolist()))
            entity_filter = c3.selectbox("Entity", ["All"] + sorted(logs["entity_type"].unique().tolist()))
            search = st.text_input("Search audit descriptions")
            filtered = logs.copy()
            if username_filter != "All":
                filtered = filtered[filtered["username"] == username_filter]
            if action_filter != "All":
                filtered = filtered[filtered["action"] == action_filter]
            if entity_filter != "All":
                filtered = filtered[filtered["entity_type"] == entity_filter]
            if search.strip():
                filtered = filtered[
                    filtered["description"].str.contains(search.strip(), case=False, na=False)
                ]
            st.dataframe(filtered, width="stretch", hide_index=True)

    with maintenance_tab:
        glass_open("Legacy Activity Cleanup")
        st.caption("Removes only repeated copies of four known legacy seed activity messages; legitimate activities are untouched.")
        if st.button("Clean Duplicate Seed Activities"):
            try:
                removed = cleanup_duplicate_seed_activities(user.actor)
                st.success(f"Removed {removed} duplicated seed activities.")
            except Exception as exc:
                show_factory_error(exc)
        glass_close()

        glass_open("Demo Mode")
        if not _demo_mode_enabled():
            st.info("Demo mode is disabled. Enable AL_SADI_DEMO_MODE or demo_mode in Streamlit secrets.")
        else:
            st.warning("Demo data is blocked whenever any operational record exists.")
            demo_confirmation = st.text_input("Type DEMO to confirm", key="demo_confirmation")
            if st.button("Initialize Demo Data", disabled=demo_confirmation != "DEMO"):
                try:
                    result = initialize_demo_data(user.actor)
                    st.success(result.sync_message)
                except Exception as exc:
                    show_factory_error(exc)
        glass_close()
