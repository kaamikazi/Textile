from __future__ import annotations

from datetime import date

import plotly.express as px
import streamlit as st

from auth import AuthenticatedUser, can
from database import delete_machine, fetch_df, save_machine
from ui import glass_close, glass_open, page_header, plotly_layout, show_factory_error, show_mutation_result


MACHINE_STATUSES = ["Running", "Idle", "Maintenance", "Out of Service", "Offline"]


def render(user: AuthenticatedUser) -> None:
    page_header("Machine Management", "Machine status, production totals, operators, and maintenance notes.", "Line Control")
    machines = fetch_df("SELECT * FROM machines ORDER BY machine_number")
    production = fetch_df(
        """
        SELECT machine_number, SUM(quantity) AS total_production,
               SUM(total_amount) AS earnings
        FROM production_entries GROUP BY machine_number
        """
    )
    machine_view = machines.merge(production, on="machine_number", how="left").fillna(
        {"total_production": 0, "earnings": 0}
    )

    left, right = st.columns([0.85, 1.25])
    with left:
        glass_open("Machine Profile")
        if not can(user, "manage_machines"):
            st.info("Staff can view machine status but cannot change machine records.")
        else:
            mode = st.radio("Mode", ["New Machine", "Update Existing"], horizontal=True)
            selected_row = None
            if mode == "Update Existing" and not machines.empty:
                selected_number = st.selectbox("Existing Machine", machines["machine_number"].tolist())
                selected_row = machines[machines["machine_number"] == selected_number].iloc[0]
            elif mode == "Update Existing":
                st.info("No machine exists yet.")

            with st.form("machine_form", clear_on_submit=mode == "New Machine"):
                machine_number = st.text_input(
                    "Machine Number", value=str(selected_row["machine_number"]) if selected_row is not None else "",
                    disabled=selected_row is not None,
                )
                status = st.selectbox(
                    "Machine Status", MACHINE_STATUSES,
                    index=MACHINE_STATUSES.index(selected_row["status"]) if selected_row is not None and selected_row["status"] in MACHINE_STATUSES else 0,
                )
                assigned_operator = st.text_input(
                    "Assigned Operator", value=str(selected_row["assigned_operator"] or "") if selected_row is not None else ""
                )
                maintenance_notes = st.text_area(
                    "Maintenance Notes", value=str(selected_row["maintenance_notes"] or "") if selected_row is not None else ""
                )
                installed_on = st.date_input(
                    "Installed On",
                    value=date.fromisoformat(str(selected_row["installed_on"])) if selected_row is not None else date.today(),
                )
                submitted = st.form_submit_button("Save Machine", width="stretch")
            if submitted:
                try:
                    result = save_machine(
                        machine_number, status, assigned_operator, maintenance_notes,
                        installed_on, user.actor,
                    )
                    show_mutation_result(result, "Machine saved without replacing its database ID.")
                    st.rerun()
                except Exception as exc:
                    show_factory_error(exc)
        glass_close()

    with right:
        glass_open("Production by Machine")
        if machine_view.empty:
            st.info("No machines available.")
        else:
            fig = px.bar(
                machine_view.sort_values("total_production"), x="total_production",
                y="machine_number", orientation="h", color="status",
                color_discrete_map={
                    "Running": "#18d7ff", "Idle": "#64748b",
                    "Maintenance": "#f59e0b", "Out of Service": "#ff637d", "Offline": "#ff637d",
                },
            )
            st.plotly_chart(plotly_layout(fig, 330), width="stretch")
        glass_close()

    glass_open("Machine Register")
    display_columns = [
        "id", "machine_number", "status", "assigned_operator", "total_production",
        "earnings", "maintenance_notes", "installed_on",
    ]
    st.dataframe(machine_view[display_columns] if not machine_view.empty else machine_view, width="stretch", hide_index=True)
    st.download_button(
        "Export Machines CSV", machine_view.to_csv(index=False).encode("utf-8"),
        "machines.csv", "text/csv",
    )
    glass_close()

    if can(user, "manage_machines") and not machines.empty:
        glass_open("Delete Machine")
        labels = {f"#{int(row.id)} · {row.machine_number} · {row.status}": int(row.id) for row in machines.itertuples()}
        label = st.selectbox("Machine", list(labels), key="delete_machine")
        entity_id = labels[label]
        confirm = st.checkbox(
            f"I confirm deletion of machine #{entity_id}. Historical production text remains intact.",
            key="confirm_delete_machine",
        )
        if st.button("Delete Machine", disabled=not confirm, width="stretch"):
            try:
                result = delete_machine(entity_id, user.actor)
                show_mutation_result(result, "Machine deleted.")
                st.rerun()
            except Exception as exc:
                show_factory_error(exc)
        glass_close()
