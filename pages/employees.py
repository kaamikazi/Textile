from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

from auth import AuthenticatedUser, can
from database import (
    ATTENDANCE_STATUSES,
    archive_employee,
    attendance_history,
    attendance_monthly_totals,
    create_attendance,
    create_employee,
    delete_attendance,
    fetch_df,
    update_attendance,
    update_employee,
)
from ui import glass_close, glass_open, page_header, plotly_layout, show_factory_error, show_mutation_result


EMPLOYEE_UI_STATUSES = ["Active", "On Leave", "Inactive"]


def _month_options() -> list[str]:
    months = fetch_df(
        "SELECT DISTINCT substr(attendance_date, 1, 7) AS month FROM attendance ORDER BY month DESC"
    )
    current = date.today().strftime("%Y-%m")
    options = months["month"].dropna().tolist() if not months.empty else []
    return [current] + [month for month in options if month != current]


def render(user: AuthenticatedUser) -> None:
    page_header("Employee & Attendance", "Employee records and attendance calculated from daily entries.", "HR Console")
    employees = fetch_df("SELECT * FROM employees ORDER BY status, name")
    active = employees[employees["status"] != "Archived"].copy() if not employees.empty else employees

    if can(user, "manage_employees"):
        _render_employee_management(user, employees)

    _render_attendance_entry(user, active)
    selected_month = st.selectbox("Attendance Summary Month", _month_options(), key="attendance_summary_month")
    totals = attendance_monthly_totals(selected_month)
    _render_attendance_summary(totals, selected_month)
    _render_employee_list(user, active, totals)
    _render_attendance_history(user, active, selected_month)


def _render_employee_management(user: AuthenticatedUser, employees: pd.DataFrame) -> None:
    left, right = st.columns(2)
    with left:
        glass_open("Add Employee")
        with st.form("employee_form", clear_on_submit=True):
            name = st.text_input("Employee Name")
            role = st.text_input("Role", placeholder="Operator, Mechanic, Quality Lead")
            phone = st.text_input("Phone")
            salary = st.number_input("Salary", min_value=0.0, step=1000.0)
            advance = st.number_input("Advance", min_value=0.0, step=500.0)
            performance_score = st.slider("Performance Score", 0, 100, 85)
            status = st.selectbox("Status", EMPLOYEE_UI_STATUSES)
            joined_on = st.date_input("Joined On", value=date.today())
            submitted = st.form_submit_button("Save Employee", width="stretch")
        if submitted:
            try:
                result = create_employee(
                    name, role, phone, salary, advance, performance_score,
                    status, joined_on, user.actor,
                )
                show_mutation_result(result, "Employee saved.")
                st.rerun()
            except Exception as exc:
                show_factory_error(exc)
        glass_close()

    with right:
        glass_open("Edit or Archive Employee")
        editable = employees[employees["status"] != "Archived"] if not employees.empty else employees
        if editable.empty:
            st.info("No active employee records to manage.")
            glass_close()
            return
        labels = {f"#{int(row.id)} · {row.name} · {row.role}": int(row.id) for row in editable.itertuples()}
        selected_label = st.selectbox("Employee", list(labels), key="manage_employee")
        selected = editable[editable["id"] == labels[selected_label]].iloc[0]
        with st.form("edit_employee_form"):
            name = st.text_input("Employee Name", value=str(selected["name"]), key="edit_employee_name")
            role = st.text_input("Role", value=str(selected["role"]), key="edit_employee_role")
            phone = st.text_input("Phone", value=str(selected["phone"] or ""), key="edit_employee_phone")
            salary = st.number_input("Salary", min_value=0.0, value=float(selected["salary"]), key="edit_employee_salary")
            advance = st.number_input("Advance", min_value=0.0, value=float(selected["advance"]), key="edit_employee_advance")
            performance = st.slider("Performance Score", 0, 100, int(selected["performance_score"]), key="edit_employee_performance")
            status = st.selectbox(
                "Status", EMPLOYEE_UI_STATUSES,
                index=EMPLOYEE_UI_STATUSES.index(selected["status"]) if selected["status"] in EMPLOYEE_UI_STATUSES else 0,
                key="edit_employee_status",
            )
            joined_on = st.date_input(
                "Joined On", value=date.fromisoformat(str(selected["joined_on"])), key="edit_employee_joined"
            )
            update_clicked = st.form_submit_button("Update Employee", width="stretch")
        if update_clicked:
            try:
                result = update_employee(
                    int(selected["id"]), name, role, phone, salary, advance,
                    performance, status, joined_on, user.actor,
                )
                show_mutation_result(result, "Employee updated.")
                st.rerun()
            except Exception as exc:
                show_factory_error(exc)
        confirm_archive = st.checkbox(
            f"I confirm archiving {selected['name']}. Attendance history will be retained.",
            key="confirm_archive_employee",
        )
        if st.button("Archive Employee", disabled=not confirm_archive, width="stretch"):
            try:
                result = archive_employee(int(selected["id"]), user.actor)
                show_mutation_result(result, "Employee archived safely.")
                st.rerun()
            except Exception as exc:
                show_factory_error(exc)
        glass_close()


def _render_attendance_entry(user: AuthenticatedUser, employees: pd.DataFrame) -> None:
    glass_open("Record Attendance")
    if employees.empty:
        st.info("An administrator must add an employee before attendance can be recorded.")
    elif not can(user, "add_attendance"):
        st.info("Your role cannot add attendance.")
    else:
        lookup = {f"{row.name} · {row.role}": int(row.id) for row in employees.itertuples()}
        with st.form("attendance_form", clear_on_submit=True):
            selected = st.selectbox("Employee", list(lookup), key="attendance_employee")
            attendance_date = st.date_input("Attendance Date", value=date.today())
            status = st.selectbox("Status", sorted(ATTENDANCE_STATUSES))
            notes = st.text_input("Optional Notes")
            submitted = st.form_submit_button("Record Attendance", width="stretch")
        if submitted:
            try:
                result = create_attendance(
                    lookup[selected], attendance_date, status, notes, user.actor
                )
                show_mutation_result(result, "Attendance recorded.")
                st.rerun()
            except Exception as exc:
                show_factory_error(exc)
    glass_close()


def _render_attendance_summary(totals: pd.DataFrame, month: str) -> None:
    present = int(totals["present"].sum()) if not totals.empty else 0
    absent = int(totals["absent"].sum()) if not totals.empty else 0
    leave = int(totals["leave"].sum()) if not totals.empty else 0
    late = int(totals["late"].sum()) if not totals.empty else 0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"Present · {month}", present)
    c2.metric(f"Absent · {month}", absent)
    c3.metric(f"Leave · {month}", leave)
    c4.metric(f"Late · {month}", late)


def _render_employee_list(user: AuthenticatedUser, employees: pd.DataFrame, totals: pd.DataFrame) -> None:
    left, right = st.columns([1.2, 1])
    view = employees.merge(totals, left_on="id", right_on="employee_id", how="left", suffixes=("", "_attendance"))
    for column in ["present", "absent", "leave", "late", "recorded_days"]:
        if column not in view:
            view[column] = 0
        view[column] = view[column].fillna(0).astype(int)
    with left:
        glass_open("Employee List")
        public_columns = ["name", "role", "phone", "status", "present", "absent", "leave", "late"]
        admin_columns = ["salary", "advance", "performance_score"]
        st.dataframe(view[public_columns + (admin_columns if user.role == "Admin" else [])], width="stretch", hide_index=True)
        glass_close()
    with right:
        glass_open("Performance Stats")
        if employees.empty:
            st.info("No employee performance data.")
        else:
            fig = px.bar(
                employees.sort_values("performance_score"), x="performance_score", y="name",
                orientation="h", color="performance_score",
                color_continuous_scale=["#0e7490", "#18d7ff", "#38e6a1"],
            )
            st.plotly_chart(plotly_layout(fig, 330), width="stretch")
        glass_close()


def _render_attendance_history(user: AuthenticatedUser, employees: pd.DataFrame, default_month: str) -> None:
    glass_open("Attendance History")
    employee_options = {"All Employees": None}
    employee_options.update({row.name: int(row.id) for row in employees.itertuples()})
    c1, c2, c3 = st.columns(3)
    employee_label = c1.selectbox("Filter Employee", list(employee_options), key="attendance_filter_employee")
    month_options = ["All Months"] + _month_options()
    default_index = month_options.index(default_month) if default_month in month_options else 0
    month = c2.selectbox("Filter Month", month_options, index=default_index, key="attendance_filter_month")
    status = c3.selectbox("Filter Status", ["All"] + sorted(ATTENDANCE_STATUSES), key="attendance_filter_status")
    history = attendance_history(
        employee_options[employee_label], None if month == "All Months" else month, status
    )
    st.dataframe(history, width="stretch", hide_index=True)
    glass_close()

    if can(user, "manage_records") and not history.empty:
        glass_open("Edit or Delete Attendance")
        labels = {
            f"#{int(row.id)} · {row.attendance_date} · {row.employee_name} · {row.status}": int(row.id)
            for row in history.itertuples()
        }
        label = st.selectbox("Attendance entry", list(labels), key="manage_attendance")
        selected = history[history["id"] == labels[label]].iloc[0]
        employee_lookup = {row.name: int(row.id) for row in employees.itertuples()}
        names = list(employee_lookup)
        with st.form("edit_attendance_form"):
            employee_name = st.selectbox(
                "Employee", names,
                index=names.index(selected["employee_name"]) if selected["employee_name"] in names else 0,
                key="edit_attendance_employee",
            )
            day = st.date_input(
                "Date", value=date.fromisoformat(str(selected["attendance_date"])), key="edit_attendance_date"
            )
            edit_status = st.selectbox(
                "Status", sorted(ATTENDANCE_STATUSES),
                index=sorted(ATTENDANCE_STATUSES).index(selected["status"]),
                key="edit_attendance_status",
            )
            notes = st.text_input("Notes", value=str(selected["notes"] or ""), key="edit_attendance_notes")
            update_clicked = st.form_submit_button("Update Attendance", width="stretch")
        if update_clicked:
            try:
                result = update_attendance(
                    int(selected["id"]), employee_lookup[employee_name], day,
                    edit_status, notes, user.actor,
                )
                show_mutation_result(result, "Attendance updated.")
                st.rerun()
            except Exception as exc:
                show_factory_error(exc)
        confirm_delete = st.checkbox(
            f"I confirm deletion of attendance #{int(selected['id'])}", key="confirm_delete_attendance"
        )
        if st.button("Delete Attendance", disabled=not confirm_delete, width="stretch"):
            try:
                result = delete_attendance(int(selected["id"]), user.actor)
                show_mutation_result(result, "Attendance deleted.")
                st.rerun()
            except Exception as exc:
                show_factory_error(exc)
        glass_close()
