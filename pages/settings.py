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
from ui import (
    card,
    empty_state,
    field_error,
    kpi_tile,
    page_header,
    section_head,
    show_factory_error,
    spacer,
    sync_status_panel,
)
from pages.telegram_admin import render_telegram_automation


BACKUP_COLUMNS = {
    "name": st.column_config.TextColumn("File"),
    "type": st.column_config.TextColumn("Type", width="small"),
    "size_kb": st.column_config.NumberColumn("Size (KB)", format="%.1f", width="small"),
    "modified_at": st.column_config.TextColumn("Created", width="medium"),
}

AUDIT_COLUMNS = {
    "timestamp": st.column_config.TextColumn("When", width="medium"),
    "username": st.column_config.TextColumn("User", width="small"),
    "role": st.column_config.TextColumn("Role", width="small"),
    "action": st.column_config.TextColumn("Action", width="small"),
    "entity_type": st.column_config.TextColumn("Entity", width="small"),
    "entity_id": st.column_config.NumberColumn("ID", format="%d", width="small"),
    "description": st.column_config.TextColumn("Description", width="large"),
}


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
        st.error("Administrator access is required.", icon="⚠")
        return

    page_header(
        "Settings & Data Safety",
        "Backups, restore, local users, Telegram access and the audit trail.",
        eyebrow="Administration",
    )

    _render_sync_header(user)

    safety_tab, users_tab, telegram_tab, audit_tab, maintenance_tab = st.tabs(
        ["Data Safety", "Local Users", "Telegram", "Audit Logs", "Maintenance"]
    )

    with safety_tab:
        _render_data_safety(user)
    with users_tab:
        _render_users(user)
    with telegram_tab:
        render_telegram_automation(user)
    with audit_tab:
        _render_audit()
    with maintenance_tab:
        _render_maintenance(user)

    spacer("bottom")


# ---------------------------------------------------------------------------
# Excel sync
# ---------------------------------------------------------------------------

def _render_sync_header(user: AuthenticatedUser) -> None:
    sync_status_panel()
    if st.button("Retry Excel sync", width="stretch", key="settings-retry-sync"):
        try:
            with st.spinner("Rebuilding workbook..."):
                path = sync_factory_workbook()
            set_excel_sync_status("Synced", f"Manual sync completed: {path.name}.")
            record_export(user.actor, "Manual Excel synchronization completed from Settings.")
            st.success("Excel synchronization completed.", icon="✅")
            st.rerun()
        except PermissionError:
            set_excel_sync_status("Failed", "Close factory_records.xlsx in Excel and retry.")
            st.error("Close factory_records.xlsx in Excel and retry.", icon="⚠")
        except Exception as exc:
            set_excel_sync_status("Failed", f"Manual sync failed: {exc}")
            show_factory_error(exc)


# ---------------------------------------------------------------------------
# Data safety
# ---------------------------------------------------------------------------

def _render_data_safety(user: AuthenticatedUser) -> None:
    with card("Create Backups", key="settings-backup-create",
              note="Timestamped, never overwritten"):
        c1, c2, c3 = st.columns(3)
        if c1.button("SQLite database", width="stretch", key="backup-db"):
            try:
                path = backup_database(user.actor)
                _remember_download(path)
                st.success(f"Database backup created: {path.name}", icon="✅")
            except Exception as exc:
                show_factory_error(exc)
        if c2.button("Excel workbook", width="stretch", key="backup-xlsx"):
            try:
                path = backup_excel(user.actor)
                _remember_download(path)
                st.success(f"Excel backup created: {path.name}", icon="✅")
            except Exception as exc:
                show_factory_error(exc)
        if c3.button("Both", width="stretch", type="primary", key="backup-both"):
            try:
                db_backup, excel_backup = backup_both(user.actor)
                _remember_download(db_backup)
                _remember_download(excel_backup)
                st.success("Database and Excel backups created.", icon="✅")
            except Exception as exc:
                show_factory_error(exc)

        generated = st.session_state.get("generated_backups", [])
        if generated:
            st.markdown('<p class="field-hint">Created this session:</p>', unsafe_allow_html=True)
            for index, stored_path in enumerate(generated):
                _download_file(Path(stored_path), f"generated_backup_{index}")

    backups = list_backups()

    with card("Existing Backups", key="settings-backup-list",
              note=f"{len(backups)} files" if backups else None):
        backup_frame = pd.DataFrame(backups)
        if backup_frame.empty:
            empty_state(
                "No backups yet",
                "Create a backup above. Files are stored in the configured backups folder.",
                icon="▤",
            )
        else:
            st.dataframe(
                backup_frame[list(BACKUP_COLUMNS)],
                column_config=BACKUP_COLUMNS,
                width="stretch",
                hide_index=True,
                height=260,
            )
            selected_download = st.selectbox(
                "Download an existing backup", backup_frame["name"].tolist(), key="download_existing"
            )
            selected_row = next(row for row in backups if row["name"] == selected_download)
            _download_file(Path(str(selected_row["path"])), "download_existing_backup")

    database_backups = [row for row in backups if row["type"] == "SQLite"]

    section_head("Restore Database", "Destructive")
    with card(key="settings-restore"):
        st.warning(
            "Restore replaces the live database. A pre-restore backup is created automatically, "
            "and the restored file is integrity-checked before it is accepted.",
            icon="⚠",
        )
        if not database_backups:
            st.info("Create a SQLite backup before using restore.", icon="ℹ")
            return

        selected_restore = st.selectbox(
            "Database backup", [row["name"] for row in database_backups], key="restore_backup"
        )
        confirmation = st.text_input(
            "Type RESTORE to confirm", key="restore_confirmation",
            placeholder="RESTORE",
        )
        ready = confirmation.strip() == "RESTORE"
        if confirmation.strip() and not ready:
            field_error("Type RESTORE exactly, in capitals, to enable the button.")

        with st.container(key="alsadi-danger-restore"):
            if st.button(
                "Restore selected database", disabled=not ready, width="stretch",
                key="restore-button",
            ):
                try:
                    row = next(item for item in database_backups if item["name"] == selected_restore)
                    with st.spinner("Restoring and verifying database..."):
                        _, pre_restore = restore_database(
                            str(row["path"]), confirmation, user.actor
                        )
                    _remember_download(pre_restore)
                    st.success(
                        f"Database restored. Pre-restore backup: {pre_restore.name}. "
                        "Sign in again if prompted.",
                        icon="✅",
                    )
                except Exception as exc:
                    show_factory_error(exc)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def _render_users(user: AuthenticatedUser) -> None:
    users = list_users()

    if not users.empty:
        active_count = int(users["is_active"].sum())
        admin_count = int((users["role"] == "Admin").sum())
        k1, k2, k3 = st.columns(3)
        with k1:
            kpi_tile("Accounts", str(len(users)), "local users")
        with k2:
            kpi_tile("Active", str(active_count), f"{len(users) - active_count} disabled",
                     tone="positive")
        with k3:
            kpi_tile("Administrators", str(admin_count), "full access")
        spacer()

    left, right = st.columns([0.9, 1.3])

    with left:
        with card("Create Local User", key="settings-user-create"):
            with st.form("create_local_user", clear_on_submit=True):
                username = st.text_input("Username")
                password = st.text_input("Temporary password", type="password")
                role = st.selectbox("Role", ["Staff", "Admin"])
                st.markdown(
                    '<p class="field-hint">At least 10 characters with a letter and a number. '
                    "Staff can view pages and add production, expenses and attendance. "
                    "Admin can also edit, delete, manage users and restore backups.</p>",
                    unsafe_allow_html=True,
                )
                submitted = st.form_submit_button("Create user", type="primary", width="stretch")
            if submitted:
                try:
                    create_user(username, password, role, user.actor)
                    st.success(f"{role} user created.", icon="✅")
                    st.rerun()
                except Exception as exc:
                    show_factory_error(exc)

    with right:
        with card("Accounts", key="settings-user-list"):
            if users.empty:
                empty_state("No users", "Create a local user to get started.")
            else:
                st.dataframe(
                    users,
                    column_config={
                        "id": st.column_config.NumberColumn("ID", format="%d", width="small"),
                        "username": st.column_config.TextColumn("Username"),
                        "role": st.column_config.TextColumn("Role", width="small"),
                        "is_active": st.column_config.CheckboxColumn("Active", width="small"),
                        "created_at": st.column_config.TextColumn("Created"),
                        "last_login_at": st.column_config.TextColumn("Last login"),
                    },
                    width="stretch",
                    hide_index=True,
                )
                selected_user = st.selectbox(
                    "Manage user", users["username"].tolist(), key="manage_user"
                )
                row = users[users["username"] == selected_user].iloc[0]
                enable = not bool(row["is_active"])
                if selected_user == user.username and not enable:
                    st.info("You cannot disable the account you are signed in with.", icon="ℹ")
                elif st.button(
                    f"{'Enable' if enable else 'Disable'} {selected_user}",
                    width="stretch", key="toggle-user",
                ):
                    try:
                        set_user_active(int(row["id"]), enable, user.actor)
                        st.success("User status updated.", icon="✅")
                        st.rerun()
                    except Exception as exc:
                        show_factory_error(exc)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def _render_audit() -> None:
    logs = fetch_df("SELECT * FROM audit_logs ORDER BY timestamp DESC, id DESC")

    if logs.empty:
        empty_state(
            "No audit records yet",
            "Every save, edit, delete and sign-in is recorded here as it happens.",
            icon="▤",
        )
        return

    with card("Audit Trail", key="settings-audit", note=f"{len(logs):,} records"):
        c1, c2, c3 = st.columns(3)
        username_filter = c1.selectbox(
            "User", ["All"] + sorted(logs["username"].unique().tolist())
        )
        action_filter = c2.selectbox(
            "Action", ["All"] + sorted(logs["action"].unique().tolist())
        )
        entity_filter = c3.selectbox(
            "Entity", ["All"] + sorted(logs["entity_type"].unique().tolist())
        )
        search = st.text_input("Search descriptions", placeholder="Filter by text")

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

        if filtered.empty:
            empty_state("No matching records", "Adjust the filters to widen the search.")
            return

        display_columns = [c for c in AUDIT_COLUMNS if c in filtered.columns]
        st.dataframe(
            filtered[display_columns],
            column_config=AUDIT_COLUMNS,
            width="stretch",
            hide_index=True,
            height=420,
        )
        st.markdown(
            f'<p class="field-hint">Showing {len(filtered):,} of {len(logs):,} records. '
            "Passwords, tokens and secrets are never written to this log.</p>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def _render_maintenance(user: AuthenticatedUser) -> None:
    with card("Legacy Activity Cleanup", key="settings-cleanup"):
        st.markdown(
            '<p class="field-hint">Removes only repeated copies of four known legacy seed '
            "activity messages. Legitimate activity records are untouched.</p>",
            unsafe_allow_html=True,
        )
        if st.button("Clean duplicate seed activities", width="stretch", key="cleanup-seed"):
            try:
                removed = cleanup_duplicate_seed_activities(user.actor)
                st.success(f"Removed {removed} duplicated seed activities.", icon="✅")
            except Exception as exc:
                show_factory_error(exc)

    with card("Demo Data", key="settings-demo"):
        if not _demo_mode_enabled():
            st.info(
                "Demo mode is disabled. Set AL_SADI_DEMO_MODE, or demo_mode in Streamlit secrets, "
                "to enable it.",
                icon="ℹ",
            )
            return
        st.warning(
            "Demo data is blocked whenever any operational record already exists.",
            icon="⚠",
        )
        demo_confirmation = st.text_input("Type DEMO to confirm", key="demo_confirmation")
        if st.button(
            "Initialize demo data", disabled=demo_confirmation.strip() != "DEMO",
            width="stretch", key="init-demo",
        ):
            try:
                result = initialize_demo_data(user.actor)
                st.success(result.sync_message, icon="✅")
            except Exception as exc:
                show_factory_error(exc)
