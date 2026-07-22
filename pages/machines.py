from __future__ import annotations

from datetime import date

import plotly.express as px
import streamlit as st

from database import add_machine, fetch_df
from ui import glass_close, glass_open, page_header, plotly_layout


def render() -> None:
    page_header("Machine Management", "Machine status, production totals, assigned operators, and maintenance notes.", "Line Control")

    machines = fetch_df("SELECT * FROM machines ORDER BY machine_number")
    production = fetch_df("SELECT machine_number, SUM(quantity) AS total_production, SUM(total_amount) AS earnings FROM production_entries GROUP BY machine_number")
    machine_view = machines.merge(production, on="machine_number", how="left").fillna({"total_production": 0, "earnings": 0})

    left, right = st.columns([0.85, 1.25])
    with left:
        glass_open("Machine Profile")
        with st.form("machine_form", clear_on_submit=True):
            machine_number = st.text_input("Machine Number", placeholder="M-06")
            status = st.selectbox("Machine Status", ["Running", "Idle", "Maintenance", "Offline"])
            assigned_operator = st.text_input("Assigned Operator")
            maintenance_notes = st.text_area("Maintenance Notes")
            installed_on = st.date_input("Installed On", value=date.today())
            submitted = st.form_submit_button("Save Machine")
            if submitted:
                if not machine_number.strip():
                    st.error("Machine number is required.")
                else:
                    add_machine(machine_number.strip().upper(), status, assigned_operator.strip(), maintenance_notes.strip(), installed_on)
                    st.success("Machine saved.")
        glass_close()

    with right:
        glass_open("Production by Machine")
        fig = px.bar(
            machine_view.sort_values("total_production"),
            x="total_production",
            y="machine_number",
            orientation="h",
            color="status",
            color_discrete_map={"Running": "#18d7ff", "Idle": "#64748b", "Maintenance": "#f59e0b", "Offline": "#ff637d"},
        )
        st.plotly_chart(plotly_layout(fig, 330), use_container_width=True)
        glass_close()

    glass_open("Machine Register")
    st.dataframe(
        machine_view[["machine_number", "status", "assigned_operator", "total_production", "earnings", "maintenance_notes", "installed_on"]],
        use_container_width=True,
        hide_index=True,
    )
    st.download_button("Export Machines CSV", machine_view.to_csv(index=False).encode("utf-8"), "machines.csv", "text/csv")
    glass_close()
