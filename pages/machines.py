from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from auth import AuthenticatedUser, can
from database import delete_machine, fetch_df, save_machine
from ui import (
    CHART_COLORS,
    card,
    chart,
    compact_number,
    empty_state,
    field_error,
    flash_mutation,
    kpi_tile,
    money,
    page_header,
    section_head,
    show_factory_error,
    show_flash,
    spacer,
    status_chip,
    sync_bar,
)

MACHINE_STATUSES = ["Running", "Idle", "Maintenance", "Out of Service", "Offline"]
PRODUCTIVE_STATUSES = ["Running"]

STATUS_COLORS = {
    "Running": CHART_COLORS["accent"],
    "Idle": CHART_COLORS["slate"],
    "Maintenance": CHART_COLORS["amber"],
    "Out of Service": CHART_COLORS["red"],
    "Offline": CHART_COLORS["red"],
}

REGISTER_COLUMNS = {
    "machine_number": st.column_config.TextColumn("Machine", width="small"),
    "status": st.column_config.TextColumn("Status", width="small"),
    "assigned_operator": st.column_config.TextColumn("Operator"),
    "total_production": st.column_config.NumberColumn("Units", format="%d"),
    "earnings": st.column_config.NumberColumn("Earnings (BDT)", format="%.2f"),
    "installed_on": st.column_config.DateColumn("Installed", format="DD MMM YYYY", width="small"),
    "maintenance_notes": st.column_config.TextColumn("Notes", width="large"),
}


def _output_chart(machine_view: pd.DataFrame) -> go.Figure:
    ordered = machine_view.sort_values("total_production")
    fig = go.Figure(
        go.Bar(
            x=ordered["total_production"], y=ordered["machine_number"], orientation="h",
            marker_color=[STATUS_COLORS.get(s, CHART_COLORS["slate"]) for s in ordered["status"]],
            marker_line_width=0,
            customdata=ordered[["status", "earnings"]],
            hovertemplate="<b>%{y}</b><br>%{x:,.0f} units<br>BDT %{customdata[1]:,.0f}"
                          "<br>Status: %{customdata[0]}<extra></extra>",
        )
    )
    fig.update_layout(hovermode="closest", bargap=0.42)
    return fig


def render(user: AuthenticatedUser) -> None:
    page_header(
        "Machines",
        "Line status, output per machine, operators and maintenance notes.",
        eyebrow="Daily Operations",
    )
    sync_bar(key="machines")
    show_flash()

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

    if machines.empty:
        empty_state(
            "No machines registered",
            "Add the factory's knitting machines to start tracking output, status and maintenance.",
            icon="⚙",
        )
        if not can(user, "manage_machines"):
            return

    if not machines.empty:
        running = int(machines["status"].isin(PRODUCTIVE_STATUSES).sum())
        down = int(machines["status"].isin(["Offline", "Out of Service"]).sum())
        maintenance = int((machines["status"] == "Maintenance").sum())
        utilisation = running / len(machines) * 100

        k1, k2, k3, k4 = st.columns(4)
        with k1:
            kpi_tile(
                "Utilisation", f"{utilisation:.0f}%", f"{running} of {len(machines)} running",
                tone="accent" if utilisation >= 60 else "warn", progress=utilisation,
            )
        with k2:
            kpi_tile("In Maintenance", str(maintenance), "scheduled service", tone="warn")
        with k3:
            kpi_tile(
                "Down", str(down), "offline or out of service",
                tone="negative" if down else "positive",
            )
        with k4:
            kpi_tile(
                "Total Output", compact_number(machine_view["total_production"].sum()),
                f"{money(float(machine_view['earnings'].sum()))} earned",
            )

        spacer()

    left, right = st.columns([0.9, 1.3])

    # --- Machine profile -------------------------------------------------
    with left:
        with card("Machine Profile", key="machine-form"):
            if not can(user, "manage_machines"):
                st.info(
                    "Staff can view machine status but cannot change machine records.",
                    icon="ℹ",
                )
            else:
                mode = st.radio(
                    "Mode", ["New Machine", "Update Existing"],
                    horizontal=True, label_visibility="collapsed",
                )
                selected_row = None
                if mode == "Update Existing" and not machines.empty:
                    selected_number = st.selectbox("Existing machine", machines["machine_number"].tolist())
                    selected_row = machines[machines["machine_number"] == selected_number].iloc[0]
                elif mode == "Update Existing":
                    st.info("No machine exists yet.", icon="ℹ")

                with st.form("machine_form", clear_on_submit=mode == "New Machine"):
                    machine_number = st.text_input(
                        "Machine number",
                        value=str(selected_row["machine_number"]) if selected_row is not None else "",
                        disabled=selected_row is not None,
                        placeholder="e.g. M-07",
                    )
                    status = st.selectbox(
                        "Status", MACHINE_STATUSES,
                        index=MACHINE_STATUSES.index(selected_row["status"])
                        if selected_row is not None and selected_row["status"] in MACHINE_STATUSES else 0,
                    )
                    assigned_operator = st.text_input(
                        "Assigned operator",
                        value=str(selected_row["assigned_operator"] or "") if selected_row is not None else "",
                        placeholder="Operator name",
                    )
                    maintenance_notes = st.text_area(
                        "Maintenance notes",
                        value=str(selected_row["maintenance_notes"] or "") if selected_row is not None else "",
                        height=90,
                    )
                    installed_on = st.date_input(
                        "Installed on",
                        value=date.fromisoformat(str(selected_row["installed_on"]))
                        if selected_row is not None else date.today(),
                    )
                    submitted = st.form_submit_button(
                        "Save machine", type="primary", width="stretch"
                    )

                if submitted:
                    if not machine_number.strip():
                        field_error("Machine number is required.")
                    else:
                        try:
                            result = save_machine(
                                machine_number, status, assigned_operator,
                                maintenance_notes, installed_on, user.actor,
                            )
                            flash_mutation(
                                result, "Machine saved. Its database ID was preserved."
                            )
                        except Exception as exc:
                            show_factory_error(exc)

    # --- Output ----------------------------------------------------------
    with right:
        with card("Output by Machine", key="machine-output", note="Coloured by current status"):
            if machine_view.empty:
                empty_state("No machines available", "Register a machine to see its output here.")
            else:
                chart(
                    _output_chart(machine_view),
                    max(200, 44 * len(machine_view)),
                    key="chart-machine-output",
                )

    if machines.empty:
        spacer("bottom")
        return

    # --- Register --------------------------------------------------------
    section_head("Machine Register", f"{len(machines)} machines")

    status_cols = st.columns(min(len(MACHINE_STATUSES), 5))
    counts = machines["status"].value_counts()
    for index, status_name in enumerate(MACHINE_STATUSES[:5]):
        with status_cols[index]:
            st.markdown(
                f'<div class="status-tile">{status_chip(status_name)}'
                f'<span class="status-count">{int(counts.get(status_name, 0))}</span></div>',
                unsafe_allow_html=True,
            )

    with card(key="machine-register"):
        table = machine_view.copy()
        table["installed_on"] = pd.to_datetime(table["installed_on"], errors="coerce")
        st.dataframe(
            table[list(REGISTER_COLUMNS)],
            column_config=REGISTER_COLUMNS,
            width="stretch",
            hide_index=True,
        )
        st.download_button(
            "Export machines as CSV",
            machine_view.to_csv(index=False).encode("utf-8"),
            "machines.csv",
            "text/csv",
            key="export-machines",
        )

    # --- Delete ----------------------------------------------------------
    if can(user, "manage_machines"):
        section_head("Remove a Machine", "Admin only")
        with card(key="machine-delete"):
            labels = {
                f"#{int(row.id)}  ·  {row.machine_number}  ·  {row.status}": int(row.id)
                for row in machines.itertuples()
            }
            label = st.selectbox("Select a machine", list(labels), key="delete_machine")
            entity_id = labels[label]
            st.markdown(
                '<p class="field-hint">Historical production rows reference the machine number as '
                "text, so past entries stay intact after removal.</p>",
                unsafe_allow_html=True,
            )
            confirm = st.checkbox(
                f"I confirm deletion of machine #{entity_id}",
                key="confirm_delete_machine",
            )
            with st.container(key="alsadi-danger-machine"):
                if st.button(
                    "Delete machine", disabled=not confirm, width="stretch",
                    key="delete_machine_button",
                ):
                    try:
                        result = delete_machine(entity_id, user.actor)
                        flash_mutation(result, "Machine deleted.")
                    except Exception as exc:
                        show_factory_error(exc)

    spacer("bottom")
