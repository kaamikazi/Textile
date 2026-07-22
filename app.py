from __future__ import annotations

import streamlit as st

from auth import AuthenticatedUser, authenticate_user, create_first_admin, log_logout, user_count
from database import initialize_database
from pages import dashboard, employees, expenses, machines, production, reports, settings
from ui import configure_page, page_header, sidebar_navigation, show_factory_error
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

    page_names = list(PAGES.keys()) if user.role == "Admin" else [
        "Dashboard", "Daily Production", "Expenses", "Employees", "Machines", "Reports"
    ]
    selected_page, logout = sidebar_navigation(page_names, user)
    if logout:
        log_logout(user)
        st.session_state.pop("auth_user", None)
        st.rerun()
    PAGES[selected_page](user)


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


def render_first_admin_setup() -> None:
    page_header("First Admin Setup", "Create the first local administrator account.", f"v{__version__}")
    st.info("This is local application authentication, not enterprise identity or internet-facing security.")
    with st.form("first_admin_setup"):
        username = st.text_input("Admin username")
        password = st.text_input("Password", type="password")
        confirm = st.text_input("Confirm password", type="password")
        submitted = st.form_submit_button("Create First Admin", width="stretch")
    if submitted:
        if password != confirm:
            st.error("Passwords do not match.")
            return
        try:
            user = create_first_admin(username, password)
            set_session_user(user)
            st.success("Administrator created. Opening the factory console...")
            st.rerun()
        except Exception as exc:
            show_factory_error(exc)


def render_login() -> None:
    page_header("Factory Login", "Sign in to the local Al Sadi Knitwear console.", f"v{__version__}")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign In", width="stretch")
    if submitted:
        user = authenticate_user(username, password)
        if user is None:
            st.error("Invalid username or password.")
        else:
            set_session_user(user)
            st.rerun()


if __name__ == "__main__":
    main()
