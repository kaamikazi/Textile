from __future__ import annotations

import asyncio

import streamlit as st

from auth import AuthenticatedUser
from telegram_automation import (
    authorize_telegram_user,
    get_bot_status,
    list_pending_operations,
    list_telegram_users,
    recent_telegram_audit,
    set_telegram_user_status,
)
from telegram_config import telegram_config_problem, telegram_token_is_configured
from ui import card, empty_state, field_error, kpi_tile, show_factory_error, spacer


def _run_connection_test() -> tuple[bool, str]:
    from telegram_bot import test_bot_connection

    return asyncio.run(test_bot_connection())


def render_telegram_automation(user: AuthenticatedUser) -> None:
    status = get_bot_status()
    token_ready = telegram_token_is_configured()
    service_state = str(status["status"])

    # The bot runs as its own process, so "not running" is a normal state
    # rather than an error - it just means nobody started it.
    k1, k2, k3 = st.columns(3)
    with k1:
        kpi_tile(
            "Polling Service", service_state,
            "separate process",
            tone="positive" if service_state.lower() in {"running", "online", "active"} else "warn",
        )
    with k2:
        kpi_tile(
            "Bot Token", "Configured" if token_ready else "Missing",
            "from env or secrets",
            tone="positive" if token_ready else "negative",
        )
    with k3:
        kpi_tile("Last Heartbeat", str(status["heartbeat_at"] or "Never"), "from the bot process")

    spacer()

    problem = telegram_config_problem()
    if problem:
        st.error(
            f"Telegram configuration could not be read. {problem}",
            icon="⚠",
        )

    with card("Connection", key="telegram-connection"):
        st.markdown(
            f'<p class="field-hint">{status["message"] or "No bot status message."}</p>',
            unsafe_allow_html=True,
        )
        if st.button(
            "Test bot connection", disabled=not token_ready, width="stretch",
            key="telegram-test-connection",
        ):
            try:
                with st.spinner("Contacting Telegram..."):
                    connected, message = _run_connection_test()
                if connected:
                    st.success(message, icon="\u2705")
                else:
                    st.error(message, icon="\u26a0")
            except Exception:
                st.error(
                    "Telegram connection failed. Check the token and network access.",
                    icon="\u26a0",
                )
        st.info(
            "The token is read only from TELEGRAM_BOT_TOKEN or Streamlit secrets, and is never "
            "displayed here. Streamlit does not start the polling service.",
            icon="\u2139",
        )

    users_tab, activity_tab, rejected_tab, pending_tab, setup_tab = st.tabs(
        ["Authorized Users", "Recent Actions", "Rejected", "Pending", "Setup"]
    )

    with users_tab:
        _render_users(user)
    with activity_tab:
        _render_audit_table(
            recent_telegram_audit(),
            "No Telegram actions recorded",
            "Confirmed submissions and admin changes appear here.",
        )
    with rejected_tab:
        _render_audit_table(
            recent_telegram_audit(rejected_only=True),
            "No rejected submissions",
            "Unauthorized or invalid Telegram submissions are logged here.",
        )
    with pending_tab:
        _render_pending()
    with setup_tab:
        _render_setup()


def _render_users(user: AuthenticatedUser) -> None:
    with card("Authorize a Telegram User", key="telegram-authorize"):
        with st.form("authorize_telegram_user", clear_on_submit=True):
            telegram_id = st.text_input(
                "Numeric Telegram user ID", placeholder="e.g. 123456789"
            )
            display_name = st.text_input("Display name")
            role = st.selectbox("Application role", ["Staff", "Admin"])
            st.markdown(
                '<p class="field-hint">Access is granted by numeric Telegram ID only. '
                "Telegram Staff can submit production, expenses and attendance; "
                "Telegram Admin can also change machine status.</p>",
                unsafe_allow_html=True,
            )
            submitted = st.form_submit_button("Authorize user", type="primary", width="stretch")

        if submitted:
            if not str(telegram_id).strip().lstrip("-").isdigit():
                field_error("Telegram user ID must be numeric.")
            elif not display_name.strip():
                field_error("Display name is required.")
            else:
                try:
                    authorize_telegram_user(telegram_id, display_name, role, user.actor)
                    st.success(
                        f"Telegram user ID {int(telegram_id)} authorized as {role}.",
                        icon="\u2705",
                    )
                    st.rerun()
                except Exception as exc:
                    show_factory_error(exc)

    telegram_users = list_telegram_users()

    with card("Authorized Users", key="telegram-users",
              note=f"{len(telegram_users)} authorized" if not telegram_users.empty else None):
        if telegram_users.empty:
            empty_state(
                "No authorized Telegram IDs",
                "Nobody can use the bot until their numeric Telegram ID is added above.",
                icon="\u25cb",
            )
            return

        st.dataframe(telegram_users, width="stretch", hide_index=True)

        labels = {
            f"{row.display_name}  \u00b7  {row.telegram_user_id}  \u00b7  {row.status}": row
            for row in telegram_users.itertuples()
        }
        selected_label = st.selectbox("Manage access", list(labels), key="telegram-manage")
        selected = labels[selected_label]
        actions = ["Active", "Suspended", "Removed"]
        next_status = st.selectbox(
            "Access status", actions,
            index=actions.index(selected.status) if selected.status in actions else 0,
            key="telegram-next-status",
        )
        st.markdown(
            '<p class="field-hint">Suspending or removing access immediately cancels that '
            "user's pending operations. Nothing they had not yet confirmed is saved.</p>",
            unsafe_allow_html=True,
        )
        confirmation = st.checkbox(
            f"Confirm access change for numeric ID {selected.telegram_user_id}",
            key="telegram-confirm-status",
        )
        if st.button(
            "Update access", disabled=not confirmation, width="stretch",
            key="telegram-update-access",
        ):
            try:
                set_telegram_user_status(
                    int(selected.telegram_user_id), next_status, user.actor
                )
                st.success("Telegram access updated.", icon="\u2705")
                st.rerun()
            except Exception as exc:
                show_factory_error(exc)


def _render_audit_table(frame, empty_title: str, empty_message: str) -> None:
    if frame.empty:
        empty_state(empty_title, empty_message, icon="\u25a4")
    else:
        st.dataframe(frame, width="stretch", hide_index=True, height=380)


def _render_pending() -> None:
    operations = list_pending_operations()
    if operations.empty:
        empty_state(
            "No pending operations",
            "Workflows awaiting confirmation appear here and expire after 20 minutes.",
            icon="\u25a4",
        )
        return
    st.dataframe(operations, width="stretch", hide_index=True, height=380)
    st.markdown(
        '<p class="field-hint">Payload values are intentionally not shown in this table.</p>',
        unsafe_allow_html=True,
    )


def _render_setup() -> None:
    with card("Running the Bot", key="telegram-setup"):
        st.markdown(
            """
            The Telegram bot is a **separate process**. Streamlit never starts it, and stopping
            Streamlit does not stop the bot.

            - Start it with `run_telegram_bot.cmd`, or the command below.
            - Only one instance can hold the database lease at a time.
            - **Emergency stop:** stop the bot process, revoke the token with BotFather, then remove
              `TELEGRAM_BOT_TOKEN` from the environment or secrets file. Streamlit keeps working.
            """
        )
        st.code(
            'cd "E:\\al sadi"\n'
            '$env:TELEGRAM_BOT_TOKEN="token-from-BotFather"\n'
            ".\\run_telegram_bot.cmd",
            language="powershell",
        )
        st.markdown(
            '<p class="field-hint">BotFather setup, token rotation and recovery steps are in '
            "TELEGRAM_SETUP.md, TELEGRAM_SECURITY.md and TELEGRAM_COMMANDS.md.</p>",
            unsafe_allow_html=True,
        )
