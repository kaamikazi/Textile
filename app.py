from __future__ import annotations

import streamlit as st

from database import initialize_database
from pages import dashboard, employees, expenses, machines, production, reports
from ui import configure_page, sidebar_navigation


PAGES = {
    "Dashboard": dashboard.render,
    "Daily Production": production.render,
    "Expenses": expenses.render,
    "Employees": employees.render,
    "Machines": machines.render,
    "Reports": reports.render,
}


def main() -> None:
    configure_page()
    initialize_database()
    selected_page = sidebar_navigation(list(PAGES.keys()))
    PAGES[selected_page]()


if __name__ == "__main__":
    main()
