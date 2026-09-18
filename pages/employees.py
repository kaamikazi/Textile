from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
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
from ui import (
    CHART_COLORS,
    card,
    chart,
    empty_state,
    field_error,
    kpi_tile,
    money,
    page_header,
    section_head,
    show_factory_error,
    show_mutation_result,
    spacer,
    sync_bar,
)

EMPLOYEE_UI_STATUSES = ["Active", "On Leave", "Inactive"]

ATTENDANCE_COLUMNS = {
    "id": st.column_config.NumberColumn("ID", width="small", format="%d"),
    "attendance_date": st.column_config.DateColumn("Date", format="DD MMM YYYY", width="small"),
    "employee_name": st.column_config.TextColumn("Employee"),
    "status": st.column_config.TextColumn("Status", width="small"),
    "notes": st.column_config.TextColumn("Notes", width="large"),
}


def _month_options() -> list[str]:
    months = fetch_df(
        "SELECT DISTINCT substr(attendance_date, 1, 7) AS month FROM attendance ORDER BY month DESC"
    )
    current = date.today().strftime("%Y-%m")
    options = months["month"].dropna().tolist() if not months.empty else []
    return [current] + [month for month in options if month != current]


def _attendance_mix(totals: pd.DataFrame) -> go.Figure:
    """Per-employee attendance as a stacked bar, so an individual with a poor
    record stands out instead of being averaged away in a single figure."""
    ordered = totals.sort_values("present")
    fig = go.Figure()
    for column, label, color in [
        ("present", "Present", CHART_COLORS["green"]),
        ("late", "Late", CHART_COLORS["amber"]),
        ("leave", "Leave", CHART_COLORS["slate"]),
        ("absent", "Absent", CHART_COLORS["red"]),
    ]:
        fig.add_trace(
            go.Bar(
                x=ordered[column], y=ordered["name"], name=label, orientation="h",
                marker_color=color, marker_line_width=0,
                hovertemplate=f"{label}: %{{x}} days<extra></extra>",
            )
        )
    fig.update_layout(barmode="stack", bargap=0.35)
    return fig


def render(user: AuthenticatedUser) -> None:
    page_header(
        "Employees & Attendance",
        "Daily attendance and employee records. Attendance history is retained when staff are archived.",
        eyebrow="Daily Operations",
    )
    sync_bar(key="employees")

    employees = fetch_df("SELECT * FROM employees ORDER BY status, name")
    active = employees[employees["status"] != "Archived"].copy() if not employees.empty else employees

    if employees.empty:
        empty_state(
            "No employees yet",
            "An administrator must add employees before attendance can be recorded.",
            icon="○",
        )
        if not can(user, "manage_employees"):
            return

    # The month selectbox lives inside the Attendance tab but drives both
    # tabs, so read its value from session state and compute the totals once.
    # Changing the selectbox triggers a rerun, so this stays in step.
    month_options = _month_options()
    selected_month = st.session_state.get("attendance_summary_month", month_options[0])
    if selected_month not in month_options:
        selected_month = month_options[0]
    totals = attendance_monthly_totals(selected_month)

    # Attendance is the daily task, so it leads. Employee records change
    # rarely and sit behind the second tab.
    attendance_tab, people_tab = st.tabs(["Attendance", "Employee Records"])

    with attendance_tab:
        _render_attendance(user, active, totals, month_options)

    with people_tab:
        _render_people(user, active, totals)

    spacer("bottom")


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------

def _render_attendance(
    user: AuthenticatedUser,
    employees: pd.DataFrame,
    totals: pd.DataFrame,
    month_options: list[str],
) -> None:
    today = date.today()

    month = st.selectbox(
        "Summary month", month_options, key="attendance_summary_month",
        help="Drives the totals, the per-employee chart and the employee list.",
    )

    present = int(totals["present"].sum()) if not totals.empty else 0
    absent = int(totals["absent"].sum()) if not totals.empty else 0
    leave = int(totals["leave"].sum()) if not totals.empty else 0
    late = int(totals["late"].sum()) if not totals.empty else 0
    recorded = present + absent + leave + late
    rate = (present + late) / recorded * 100 if recorded else 0.0

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        kpi_tile(
            "Attendance Rate", f"{rate:.0f}%", f"{recorded} days recorded",
            tone="positive" if rate >= 85 else "warn", progress=rate,
        )
    with k2:
        kpi_tile("Present", str(present), f"{late} arrived late", tone="positive")
    with k3:
        kpi_tile("Absent", str(absent), "unplanned", tone="negative" if absent else "positive")
    with k4:
        kpi_tile("Leave", str(leave), "approved leave")

    spacer()

    left, right = st.columns([0.85, 1.35])

    with left:
        with card("Record Attendance", key="attendance-form"):
            if employees.empty:
                st.info("Add an employee first.", icon="ℹ")
            elif not can(user, "add_attendance"):
                st.info("Your role cannot add attendance.", icon="ℹ")
            else:
                lookup = {f"{row.name} · {row.role}": int(row.id) for row in employees.itertuples()}
                with st.form("attendance_form", clear_on_submit=True):
                    selected = st.selectbox("Employee", list(lookup), key="attendance_employee")
                    attendance_date = st.date_input(
                        "Date", value=today, max_value=today, key="attendance_date_input"
                    )
                    status = st.selectbox("Status", sorted(ATTENDANCE_STATUSES))
                    notes = st.text_input("Notes", placeholder="Optional")
                    st.markdown(
                        '<p class="field-hint">One entry per employee per day. '
                        "Edit the existing record to change it.</p>",
                        unsafe_allow_html=True,
                    )
                    submitted = st.form_submit_button(
                        "Record attendance", type="primary", width="stretch"
                    )
                if submitted:
                    try:
                        result = create_attendance(
                            lookup[selected], attendance_date, status, notes, user.actor
                        )
                        show_mutation_result(result, "Attendance recorded.")
                        st.rerun()
                    except Exception as exc:
                        show_factory_error(exc)

    with right:
        with card("Attendance by Employee", key="attendance-chart", note=month):
            if totals.empty or totals[["present", "absent", "leave", "late"]].to_numpy().sum() == 0:
                empty_state(
                    "No attendance for this month",
                    "Recorded days will break down per employee here.",
                )
            else:
                chart(_attendance_mix(totals), max(200, 40 * len(totals)), key="chart-attendance-mix")

    _render_attendance_history(user, employees, month)


def _render_attendance_history(user: AuthenticatedUser, employees: pd.DataFrame, default_month: str) -> None:
    section_head("Attendance History")

    employee_options = {"All employees": None}
    employee_options.update({row.name: int(row.id) for row in employees.itertuples()})

    with card(key="attendance-history"):
        c1, c2, c3 = st.columns(3)
        employee_label = c1.selectbox(
            "Employee", list(employee_options), key="attendance_filter_employee"
        )
        month_options = ["All months", *_month_options()]
        default_index = month_options.index(default_month) if default_month in month_options else 0
        month = c2.selectbox(
            "Month", month_options, index=default_index, key="attendance_filter_month"
        )
        status = c3.selectbox(
            "Status", ["All", *sorted(ATTENDANCE_STATUSES)], key="attendance_filter_status"
        )

        history = attendance_history(
            employee_options[employee_label], None if month == "All months" else month, status
        )

        if history.empty:
            empty_state(
                "No matching records",
                "No attendance matches these filters. Try a different month or status.",
            )
        else:
            table = history.copy()
            table["attendance_date"] = pd.to_datetime(table["attendance_date"])
            st.dataframe(
                table[list(ATTENDANCE_COLUMNS)],
                column_config=ATTENDANCE_COLUMNS,
                width="stretch",
                hide_index=True,
                height=320,
            )
            st.download_button(
                "Export filtered attendance as CSV",
                history.to_csv(index=False).encode("utf-8"),
                "attendance.csv",
                "text/csv",
                key="export-attendance",
            )

    if can(user, "manage_records") and not history.empty:
        with card("Edit or Delete Attendance", key="attendance-manage"):
            labels = {
                f"#{int(row.id)}  ·  {row.attendance_date}  ·  {row.employee_name}"
                f"  ·  {row.status}": int(row.id)
                for row in history.itertuples()
            }
            label = st.selectbox("Select a record", list(labels), key="manage_attendance")
            selected = history[history["id"] == labels[label]].iloc[0]
            employee_lookup = {row.name: int(row.id) for row in employees.itertuples()}
            names = list(employee_lookup)

            edit_tab, delete_tab = st.tabs(["Edit", "Delete"])

            with edit_tab:
                with st.form("edit_attendance_form"):
                    employee_name = st.selectbox(
                        "Employee", names,
                        index=names.index(selected["employee_name"])
                        if selected["employee_name"] in names else 0,
                        key="edit_attendance_employee",
                    )
                    day_col, status_col = st.columns(2)
                    with day_col:
                        day = st.date_input(
                            "Date", value=date.fromisoformat(str(selected["attendance_date"])),
                            key="edit_attendance_date",
                        )
                    with status_col:
                        edit_status = st.selectbox(
                            "Status", sorted(ATTENDANCE_STATUSES),
                            index=sorted(ATTENDANCE_STATUSES).index(selected["status"]),
                            key="edit_attendance_status",
                        )
                    notes = st.text_input(
                        "Notes", value=str(selected["notes"] or ""), key="edit_attendance_notes"
                    )
                    update_clicked = st.form_submit_button(
                        "Update attendance", type="primary", width="stretch"
                    )
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

            with delete_tab:
                confirm_delete = st.checkbox(
                    f"I confirm deletion of attendance #{int(selected['id'])}",
                    key="confirm_delete_attendance",
                )
                with st.container(key="alsadi-danger-attendance"):
                    if st.button(
                        "Delete attendance", disabled=not confirm_delete, width="stretch",
                        key="delete_attendance_button",
                    ):
                        try:
                            result = delete_attendance(int(selected["id"]), user.actor)
                            show_mutation_result(result, "Attendance deleted.")
                            st.rerun()
                        except Exception as exc:
                            show_factory_error(exc)


# ---------------------------------------------------------------------------
# Employee records
# ---------------------------------------------------------------------------

def _render_people(
    user: AuthenticatedUser,
    active: pd.DataFrame,
    totals: pd.DataFrame,
) -> None:
    if can(user, "manage_employees"):
        left, right = st.columns(2)
        with left:
            _render_add_employee(user)
        with right:
            _render_edit_employee(user, active)
        spacer()

    _render_employee_list(user, active, totals)


def _render_add_employee(user: AuthenticatedUser) -> None:
    with card("Add Employee", key="employee-add"):
        with st.form("employee_form", clear_on_submit=True):
            name = st.text_input("Name")
            role = st.text_input("Role", placeholder="Operator, Mechanic, Quality Lead")
            phone = st.text_input("Phone", placeholder="01XXXXXXXXX")

            salary_col, advance_col = st.columns(2)
            with salary_col:
                salary = st.number_input("Salary (BDT)", min_value=0.0, step=1000.0)
            with advance_col:
                advance = st.number_input("Advance (BDT)", min_value=0.0, step=500.0)

            performance_score = st.slider("Performance score", 0, 100, 85)
            status_col, joined_col = st.columns(2)
            with status_col:
                status = st.selectbox("Status", EMPLOYEE_UI_STATUSES)
            with joined_col:
                joined_on = st.date_input("Joined on", value=date.today())

            submitted = st.form_submit_button("Save employee", type="primary", width="stretch")

        if submitted:
            problems = []
            if not name.strip():
                problems.append("Employee name is required.")
            if not role.strip():
                problems.append("Role is required.")
            if problems:
                for problem in problems:
                    field_error(problem)
            else:
                try:
                    result = create_employee(
                        name, role, phone, salary, advance, performance_score,
                        status, joined_on, user.actor,
                    )
                    show_mutation_result(result, "Employee saved.")
                    st.rerun()
                except Exception as exc:
                    show_factory_error(exc)


def _render_edit_employee(user: AuthenticatedUser, editable: pd.DataFrame) -> None:
    with card("Edit or Archive", key="employee-edit"):
        if editable.empty:
            empty_state("No active employees", "Add an employee to manage their record here.")
            return

        labels = {
            f"#{int(row.id)}  ·  {row.name}  ·  {row.role}": int(row.id)
            for row in editable.itertuples()
        }
        selected_label = st.selectbox("Employee", list(labels), key="manage_employee")
        selected = editable[editable["id"] == labels[selected_label]].iloc[0]

        edit_tab, archive_tab = st.tabs(["Edit", "Archive"])

        with edit_tab:
            with st.form("edit_employee_form"):
                name = st.text_input("Name", value=str(selected["name"]), key="edit_employee_name")
                role = st.text_input("Role", value=str(selected["role"]), key="edit_employee_role")
                phone = st.text_input(
                    "Phone", value=str(selected["phone"] or ""), key="edit_employee_phone"
                )

                salary_col, advance_col = st.columns(2)
                with salary_col:
                    salary = st.number_input(
                        "Salary (BDT)", min_value=0.0, value=float(selected["salary"]),
                        key="edit_employee_salary",
                    )
                with advance_col:
                    advance = st.number_input(
                        "Advance (BDT)", min_value=0.0, value=float(selected["advance"]),
                        key="edit_employee_advance",
                    )

                performance = st.slider(
                    "Performance score", 0, 100, int(selected["performance_score"]),
                    key="edit_employee_performance",
                )
                status_col, joined_col = st.columns(2)
                with status_col:
                    status = st.selectbox(
                        "Status", EMPLOYEE_UI_STATUSES,
                        index=EMPLOYEE_UI_STATUSES.index(selected["status"])
                        if selected["status"] in EMPLOYEE_UI_STATUSES else 0,
                        key="edit_employee_status",
                    )
                with joined_col:
                    joined_on = st.date_input(
                        "Joined on", value=date.fromisoformat(str(selected["joined_on"])),
                        key="edit_employee_joined",
                    )

                update_clicked = st.form_submit_button(
                    "Update employee", type="primary", width="stretch"
                )

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

        with archive_tab:
            st.markdown(
                f'<p class="field-hint">Archiving <strong>{selected["name"]}</strong> removes them '
                "from active lists and attendance entry, but keeps all of their attendance "
                "history for reporting. Employees are never hard-deleted.</p>",
                unsafe_allow_html=True,
            )
            confirm_archive = st.checkbox(
                f"I confirm archiving {selected['name']}",
                key="confirm_archive_employee",
            )
            with st.container(key="alsadi-danger-employee"):
                if st.button(
                    "Archive employee", disabled=not confirm_archive, width="stretch",
                    key="archive_employee_button",
                ):
                    try:
                        result = archive_employee(int(selected["id"]), user.actor)
                        show_mutation_result(result, "Employee archived. Attendance history retained.")
                        st.rerun()
                    except Exception as exc:
                        show_factory_error(exc)


def _render_employee_list(
    user: AuthenticatedUser, employees: pd.DataFrame, totals: pd.DataFrame
) -> None:
    section_head("Employee List", f"{len(employees)} active")

    with card(key="employee-list"):
        if employees.empty:
            empty_state("No active employees", "Added employees appear here.")
            return

        view = employees.merge(
            totals, left_on="id", right_on="employee_id", how="left", suffixes=("", "_attendance")
        )
        for column in ["present", "absent", "leave", "late", "recorded_days"]:
            if column not in view:
                view[column] = 0
            view[column] = view[column].fillna(0).astype(int)

        columns = {
            "name": st.column_config.TextColumn("Name"),
            "role": st.column_config.TextColumn("Role"),
            "phone": st.column_config.TextColumn("Phone", width="small"),
            "status": st.column_config.TextColumn("Status", width="small"),
            "present": st.column_config.NumberColumn("Present", format="%d", width="small"),
            "absent": st.column_config.NumberColumn("Absent", format="%d", width="small"),
            "leave": st.column_config.NumberColumn("Leave", format="%d", width="small"),
            "late": st.column_config.NumberColumn("Late", format="%d", width="small"),
        }
        # Salary and advance are pay data, so they stay on the Admin view only.
        if user.role == "Admin":
            columns["salary"] = st.column_config.NumberColumn("Salary (BDT)", format="%.2f")
            columns["advance"] = st.column_config.NumberColumn("Advance (BDT)", format="%.2f")
            columns["performance_score"] = st.column_config.ProgressColumn(
                "Performance", format="%d", min_value=0, max_value=100
            )

        st.dataframe(
            view[list(columns)],
            column_config=columns,
            width="stretch",
            hide_index=True,
        )

        if user.role == "Admin":
            payroll = float(employees["salary"].sum())
            advances = float(employees["advance"].sum())
            st.markdown(
                f'<p class="field-hint">Monthly payroll commitment: <strong>{money(payroll)}</strong> '
                f"&middot; outstanding advances: <strong>{money(advances)}</strong></p>",
                unsafe_allow_html=True,
            )
