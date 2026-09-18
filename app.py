from __future__ import annotations

import streamlit as st

from auth import AuthenticatedUser, authenticate_user, create_first_admin, log_logout, user_count
from database import initialize_database
from pages import dashboard, employees, expenses, machines, production, reports, settings
from ui import configure_page, render_sidebar_sync, show_factory_error, sidebar_navigation
from version import __version__

PAGES = {
    "Dashboard": dashboard.render,
    "Daily Production": production.render,
    "Expenses": expenses.render,
    "Employees": employees.render,
    "Machines": machines.render,
    "Reports": reports.render,
    "Settings": settings.render,
}

# Settings holds user management, backups, restore and audit logs, so it is
# Admin-only. Every other page is readable by Staff, whose write access is
# enforced per action by auth.can().
ADMIN_ONLY_PAGES = {"Settings"}


def main() -> None:
    configure_page()
    initialize_database()

    if user_count() == 0:
        render_first_admin_setup()
        return

    user = get_session_user()
    if user is None:
        render_login()
        return

    page_names = [name for name in PAGES if user.role == "Admin" or name not in ADMIN_ONLY_PAGES]
    selected_page, logout, sync_slot = sidebar_navigation(page_names, user)

    if logout:
        log_logout(user)
        st.session_state.pop("auth_user", None)
        st.session_state.pop("nav_selection", None)
        st.rerun()

    # Guard the route itself, not just the menu, so a stale session value
    # cannot land a Staff user on an Admin page.
    if selected_page not in page_names:
        selected_page = "Dashboard"

    PAGES[selected_page](user)

    # Filled last so the sidebar chip reflects anything the page just did,
    # rather than the status as it was before the page ran.
    render_sidebar_sync(sync_slot)


def get_session_user() -> AuthenticatedUser | None:
    stored = st.session_state.get("auth_user")
    if not stored:
        return None
    return AuthenticatedUser(int(stored["id"]), stored["username"], stored["role"])


def set_session_user(user: AuthenticatedUser) -> None:
    st.session_state["auth_user"] = {
        "id": user.id,
        "username": user.username,
        "role": user.role,
    }


def _auth_shell(title: str, subtitle: str):
    """Centred card for the signed-out screens.

    Full-width inputs across a 1440px factory monitor read as a broken page;
    a fixed-width column keeps the form scannable.
    """
    st.markdown('<div class="auth-spacer"></div>', unsafe_allow_html=True)
    _, middle, _ = st.columns([1, 1.15, 1])
    with middle:
        st.markdown(
            f"""
            <div class="auth-brand">
                <div class="brand-mark">AS</div>
                <div>
                    <h1>Al Sadi Knitwear</h1>
                    <span>Factory Management OS &middot; v{__version__}</span>
                </div>
            </div>
            <div class="auth-head">
                <h2>{title}</h2>
                <p>{subtitle}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    return middle


def render_first_admin_setup() -> None:
    middle = _auth_shell(
        "Create the first administrator",
        "This account manages users, backups and factory records.",
    )
    with middle:
        with st.container(key="alsadi-card-firstadmin"):
            with st.form("first_admin_setup"):
                username = st.text_input("Admin username", placeholder="e.g. factory_admin")
                password = st.text_input("Password", type="password")
                confirm = st.text_input("Confirm password", type="password")
                st.markdown(
                    '<p class="field-hint">At least 10 characters, including one letter and one number. '
                    'Stored as a salted PBKDF2 hash - it is never written to logs.</p>',
                    unsafe_allow_html=True,
                )
                submitted = st.form_submit_button("Create administrator", type="primary", width="stretch")

            if submitted:
                if password != confirm:
                    st.error("Passwords do not match.", icon="⚠")
                    return
                try:
                    user = create_first_admin(username, password)
                    set_session_user(user)
                    st.success("Administrator created. Opening the factory console...")
                    st.rerun()
                except Exception as exc:
                    show_factory_error(exc)

        st.caption(
            "Local application authentication for a trusted factory network. "
            "Not enterprise identity, and not safe to expose to the public internet."
        )


def render_login() -> None:
    middle = _auth_shell("Sign in", "Local factory console access.")
    with middle:
        with st.container(key="alsadi-card-login"):
            with st.form("login_form"):
                username = st.text_input("Username", autocomplete="username")
                password = st.text_input("Password", type="password", autocomplete="current-password")
                submitted = st.form_submit_button("Sign in", type="primary", width="stretch")

            if submitted:
                if not username.strip() or not password:
                    st.error("Enter both a username and a password.", icon="⚠")
                    return
                user = authenticate_user(username, password)
                if user is None:
                    # Deliberately does not say which field was wrong.
                    st.error("Invalid username or password.", icon="⚠")
                else:
                    set_session_user(user)
                    st.rerun()

        st.caption("Ask an administrator if your account is disabled or you need a password reset.")


if __name__ == "__main__":
    main()
