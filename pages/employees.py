from __future__ import annotations

from datetime import date

import plotly.express as px
import streamlit as st

from database import add_employee, fetch_df, record_attendance
from ui import glass_close, glass_open, page_header, plotly_layout


def render() -> None:
    page_header("Employee Management", "People, attendance, salary, advances, and performance statistics.", "HR Console")

    employees = fetch_df("SELECT * FROM employees ORDER BY status, name")

    c1, c2 = st.columns([1, 1])
    with c1:
        glass_open("Add Employee")
        with st.form("employee_form", clear_on_submit=True):
            name = st.text_input("Employee Name")
            role = st.text_input("Role", placeholder="Operator, Mechanic, Quality Lead")
            phone = st.text_input("Phone")
            salary = st.number_input("Salary", min_value=0.0, step=1000.0)
            advance = st.number_input("Advance", min_value=0.0, step=500.0)
            attendance_days = st.number_input("Attendance Days", min_value=0, max_value=31, step=1)
            performance_score = st.slider("Performance Score", 0, 100, 85)
            status = st.selectbox("Status", ["Active", "On Leave", "Inactive"])
            joined_on = st.date_input("Joined On", value=date.today())
            submitted = st.form_submit_button("Save Employee")
            if submitted:
                if not name.strip() or not role.strip():
                    st.error("Name and role are required.")
                else:
                    add_employee(name.strip(), role.strip(), phone.strip(), salary, advance, attendance_days, performance_score, status, joined_on)
                    st.success("Employee saved.")
        glass_close()

    with c2:
        glass_open("Attendance")
        if employees.empty:
            st.info("Add employees before recording attendance.")
        else:
            with st.form("attendance_form", clear_on_submit=True):
                employee_lookup = {f"{row['name']} - {row['role']}": int(row["id"]) for _, row in employees.iterrows()}
                selected = st.selectbox("Employee", list(employee_lookup.keys()))
                attendance_date = st.date_input("Attendance Date", value=date.today())
                attendance_status = st.selectbox("Status", ["Present", "Absent", "Late", "Half Day"])
                notes = st.text_input("Notes")
                submitted = st.form_submit_button("Record Attendance")
                if submitted:
                    record_attendance(employee_lookup[selected], attendance_date, attendance_status, notes.strip())
                    st.success("Attendance recorded.")
        glass_close()

    left, right = st.columns([1.2, 1])
    with left:
        glass_open("Employee List")
        st.dataframe(
            employees[["name", "role", "phone", "salary", "advance", "attendance_days", "performance_score", "status"]],
            use_container_width=True,
            hide_index=True,
        )
        glass_close()

    with right:
        glass_open("Performance Stats")
        fig = px.bar(
            employees.sort_values("performance_score"),
            x="performance_score",
            y="name",
            orientation="h",
            color="performance_score",
            color_continuous_scale=["#0e7490", "#18d7ff", "#38e6a1"],
        )
        st.plotly_chart(plotly_layout(fig, 330), use_container_width=True)
        glass_close()
