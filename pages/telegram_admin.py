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
from telegram_config import telegram_token_is_configured
from ui import glass_close, glass_open, show_factory_error


def _run_connection_test() -> tuple[bool, str]:
    from telegram_bot import test_bot_connection

    return asyncio.run(test_bot_connection())


def render_telegram_automation(user: AuthenticatedUser) -> None:
    glass_open("Bot Connection Status")
    status = get_bot_status()
    token_ready = telegram_token_is_configured()
    c1, c2, c3 = st.columns(3)
    c1.metric("Polling Service", status["status"])
    c2.metric("Token Configuration", "Configured" if token_ready else "Missing")
    c3.metric("Last Heartbeat", status["heartbeat_at"] or "Never")
    st.caption(status["message"] or "No bot status message.")
    if st.button("Test Bot Connection", disabled=not token_ready, width="stretch"):
        try:
            connected, message = _run_connection_test()
            st.success(message) if connected else st.error(message)
        except Exception:
            st.error("Telegram connection failed. Check the token and network access.")
    st.info(
        "The token is only read from TELEGRAM_BOT_TOKEN or Streamlit secrets. "
        "It is never displayed here. Streamlit does not start the polling service."
    )
    glass_close()

    users_tab, activity_tab, rejected_tab, pending_tab, setup_tab = st.tabs(
        ["Authorized Users", "Recent Actions", "Rejected", "Pending", "Setup"]
    )
    with users_tab:
        glass_open("Authorize Telegram User")
        with st.form("authorize_telegram_user", clear_on_submit=True):
            telegram_id = st.text_input("Numeric Telegram user ID")
            display_name = st.text_input("Display name")
            role = st.selectbox("Application role", ["Staff", "Admin"])
            submitted = st.form_submit_button("Authorize User", width="stretch")
        if submitted:
            try:
                authorize_telegram_user(telegram_id, display_name, role, user.actor)
                st.success(f"Telegram user ID {int(telegram_id)} authorized as {role}.")
                st.rerun()
            except Exception as exc:
                show_factory_error(exc)
        glass_close()

        telegram_users = list_telegram_users()
        if telegram_users.empty:
            st.info("No Telegram user IDs are authorized.")
        else:
            st.dataframe(telegram_users, width="stretch", hide_index=True)
            labels = {
                f"{row.display_name} | {row.telegram_user_id} | {row.status}": row
                for row in telegram_users.itertuples()
            }
            selected_label = st.selectbox("Manage Telegram access", list(labels))
            selected = labels[selected_label]
            actions = ["Active", "Suspended", "Removed"]
            next_status = st.selectbox(
                "Access status", actions,
                index=actions.index(selected.status) if selected.status in actions else 0,
            )
            confirmation = st.checkbox(
                f"Confirm access change for numeric ID {selected.telegram_user_id}"
            )
            if st.button("Update Telegram Access", disabled=not confirmation, width="stretch"):
                try:
                    set_telegram_user_status(
                        int(selected.telegram_user_id), next_status, user.actor
                    )
                    st.success("Telegram access updated. Pending operations were cancelled when access was disabled.")
                    st.rerun()
                except Exception as exc:
                    show_factory_error(exc)

    with activity_tab:
        actions = recent_telegram_audit()
        if actions.empty:
            st.info("No Telegram actions have been recorded.")
        else:
            st.dataframe(actions, width="stretch", hide_index=True)

    with rejected_tab:
        rejected = recent_telegram_audit(rejected_only=True)
        if rejected.empty:
            st.info("No rejected Telegram submissions have been recorded.")
        else:
            st.dataframe(rejected, width="stretch", hide_index=True)

    with pending_tab:
        operations = list_pending_operations()
        if operations.empty:
            st.info("No Telegram operations have been created.")
        else:
            st.dataframe(operations, width="stretch", hide_index=True)
            st.caption("Pending payload values are intentionally not displayed in the administration table.")

    with setup_tab:
        st.markdown(
            """
            **Polling service**

            Run the Telegram bot in a separate terminal or with `run_telegram_bot.cmd`. Only one instance can hold the database lease.

            **Emergency stop**

            Stop the bot process, revoke the token with BotFather, and remove `TELEGRAM_BOT_TOKEN` from the environment or secrets file. Streamlit continues operating independently.
            """
        )
        st.code(
            'cd "E:\\al sadi"\n'
            '$env:TELEGRAM_BOT_TOKEN="token-from-BotFather"\n'
            '& "C:\\Users\\imran\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe" telegram_bot.py',
            language="powershell",
        )
        st.caption("Detailed BotFather, token rotation, and recovery instructions are in TELEGRAM_SETUP.md.")
