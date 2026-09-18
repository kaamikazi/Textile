from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from auth import AuthenticatedUser, can
from database import create_production, delete_production, fetch_df, update_production
from ui import (
    card,
    compact_number,
    empty_state,
    field_error,
    flash_mutation,
    form_total,
    kpi_tile,
    money,
    page_header,
    section_head,
    show_factory_error,
    show_flash,
    spacer,
    sync_bar,
)

PRODUCT_TYPES = ["Rib Collar", "Cuff", "Jacquard Panel", "Flat Knit Body", "Neck Tape", "Other"]

LEDGER_COLUMNS = {
    "id": st.column_config.NumberColumn("ID", width="small", format="%d"),
    "production_date": st.column_config.DateColumn("Date", format="DD MMM YYYY", width="small"),
    "machine_number": st.column_config.TextColumn("Machine", width="small"),
    "operator_name": st.column_config.TextColumn("Operator"),
    "product_type": st.column_config.TextColumn("Product"),
    "quantity": st.column_config.NumberColumn("Qty", format="%d", width="small"),
    "rate_per_unit": st.column_config.NumberColumn("Rate", format="%.2f", width="small"),
    "total_amount": st.column_config.NumberColumn("Total (BDT)", format="%.2f"),
}


def _validate(operator_name: str, quantity: int, rate_per_unit: float) -> list[str]:
    """Client-side checks that mirror database.py's validators.

    These exist to catch mistakes before a round trip; database.py remains
    the authority and its messages are shown verbatim if it rejects.
    """
    problems = []
    if not operator_name.strip():
        problems.append("Operator name is required.")
    if quantity <= 0:
        problems.append("Quantity must be greater than zero.")
    if rate_per_unit <= 0:
        problems.append("Rate per unit must be greater than zero.")
    return problems


def render(user: AuthenticatedUser) -> None:
    page_header(
        "Daily Production",
        "Record machine output. Totals are calculated and saved to the database first.",
        eyebrow="Daily Operations",
    )
    sync_bar(key="production")
    show_flash()

    machines = fetch_df("SELECT machine_number FROM machines ORDER BY machine_number")
    machine_options = machines["machine_number"].tolist()

    production = fetch_df(
        """
        SELECT id, production_date, machine_number, operator_name, product_type,
               quantity, rate_per_unit, total_amount, created_at, updated_at
        FROM production_entries ORDER BY production_date DESC, id DESC
        """
    )

    # --- Today at a glance ----------------------------------------------
    today = date.today()
    if not production.empty:
        dates = pd.to_datetime(production["production_date"]).dt.date
        today_rows = production[dates == today]
    else:
        today_rows = production

    t1, t2, t3 = st.columns(3)
    with t1:
        kpi_tile("Today's Output", compact_number(today_rows["quantity"].sum() if not today_rows.empty else 0), "units")
    with t2:
        kpi_tile("Today's Value", money(today_rows["total_amount"].sum() if not today_rows.empty else 0), f"{len(today_rows)} entries")
    with t3:
        kpi_tile("Machines Reporting", f"{today_rows['machine_number'].nunique() if not today_rows.empty else 0}/{len(machine_options)}", "logged output today")

    spacer()

    left, right = st.columns([0.95, 1.25])

    # --- Entry form ------------------------------------------------------
    with left:
        with card("New Entry", key="production-form"):
            if not can(user, "add_production"):
                st.info("Your role cannot add production entries.", icon="ℹ")
            elif not machine_options:
                st.warning(
                    "An administrator must add a machine before production can be recorded.",
                    icon="⚠",
                )
            else:
                with st.form("production_form", clear_on_submit=True):
                    production_date = st.date_input("Date", value=today, max_value=today)
                    machine_number = st.selectbox("Machine", machine_options)
                    operator_name = st.text_input("Operator", placeholder="Operator name")
                    product_type = st.selectbox("Product type", PRODUCT_TYPES)

                    qty_col, rate_col = st.columns(2)
                    with qty_col:
                        quantity = st.number_input("Quantity", min_value=1, step=1)
                    with rate_col:
                        rate_per_unit = st.number_input(
                            "Rate per unit", min_value=0.01, step=0.5, format="%.2f"
                        )

                    form_total("Total amount", money(quantity * rate_per_unit))
                    submitted = st.form_submit_button(
                        "Save production", type="primary", width="stretch"
                    )

                if submitted:
                    problems = _validate(operator_name, int(quantity), float(rate_per_unit))
                    if problems:
                        for problem in problems:
                            field_error(problem)
                    else:
                        try:
                            with st.spinner("Saving..."):
                                result = create_production(
                                    production_date, machine_number, operator_name, product_type,
                                    int(quantity), rate_per_unit, user.actor,
                                )
                            flash_mutation(result, "Production entry saved.")
                        except Exception as exc:
                            show_factory_error(exc)

    # --- Ledger ----------------------------------------------------------
    with right:
        with card(
            "Recent Production",
            key="production-ledger",
            note=f"{len(production):,} total entries",
        ):
            if production.empty:
                empty_state(
                    "No production recorded",
                    "Saved entries appear here, newest first.",
                    icon="▤",
                )
            else:
                recent = production.head(25).copy()
                recent["production_date"] = pd.to_datetime(recent["production_date"])
                st.dataframe(
                    recent[list(LEDGER_COLUMNS)],
                    column_config=LEDGER_COLUMNS,
                    width="stretch",
                    hide_index=True,
                    height=380,
                )
                st.download_button(
                    "Export recent as CSV",
                    production.head(25).to_csv(index=False).encode("utf-8"),
                    "recent-production.csv",
                    "text/csv",
                    key="export-recent-production",
                )

    # --- Manage ----------------------------------------------------------
    if can(user, "manage_records") and not production.empty:
        section_head("Manage Entries", "Admin only")
        with card(key="production-manage"):
            labels = {
                f"#{int(row.id)}  ·  {row.production_date}  ·  {row.machine_number}"
                f"  ·  {int(row.quantity):,} units": int(row.id)
                for row in production.itertuples()
            }
            selected_label = st.selectbox("Select an entry", list(labels), key="manage_production")
            selected = production[production["id"] == labels[selected_label]].iloc[0]

            edit_tab, delete_tab = st.tabs(["Edit", "Delete"])

            with edit_tab:
                with st.form("edit_production_form"):
                    edit_date = st.date_input(
                        "Date",
                        value=date.fromisoformat(str(selected["production_date"])),
                        key="edit_prod_date",
                    )
                    machine_col, product_col = st.columns(2)
                    with machine_col:
                        edit_machine = st.selectbox(
                            "Machine", machine_options,
                            index=machine_options.index(selected["machine_number"])
                            if selected["machine_number"] in machine_options else 0,
                            key="edit_prod_machine",
                        )
                    with product_col:
                        edit_product = st.selectbox(
                            "Product type", PRODUCT_TYPES,
                            index=PRODUCT_TYPES.index(selected["product_type"])
                            if selected["product_type"] in PRODUCT_TYPES else len(PRODUCT_TYPES) - 1,
                        )
                    edit_operator = st.text_input("Operator", value=str(selected["operator_name"]))

                    qty_col, rate_col = st.columns(2)
                    with qty_col:
                        edit_quantity = st.number_input(
                            "Quantity", min_value=1, step=1, value=int(selected["quantity"])
                        )
                    with rate_col:
                        edit_rate = st.number_input(
                            "Rate per unit", min_value=0.01,
                            value=float(selected["rate_per_unit"]), format="%.2f",
                        )

                    form_total("Updated total", money(edit_quantity * edit_rate))
                    update_clicked = st.form_submit_button(
                        "Update entry", type="primary", width="stretch"
                    )

                if update_clicked:
                    problems = _validate(edit_operator, int(edit_quantity), float(edit_rate))
                    if problems:
                        for problem in problems:
                            field_error(problem)
                    else:
                        try:
                            result = update_production(
                                int(selected["id"]), edit_date, edit_machine, edit_operator,
                                edit_product, int(edit_quantity), edit_rate, user.actor,
                            )
                            flash_mutation(result, "Production entry updated.")
                        except Exception as exc:
                            show_factory_error(exc)

            with delete_tab:
                st.markdown(
                    f'<p class="field-hint">Deleting entry <strong>#{int(selected["id"])}</strong> '
                    f'({int(selected["quantity"]):,} units, {money(float(selected["total_amount"]))}) '
                    "removes it permanently. The action is written to the audit log.</p>",
                    unsafe_allow_html=True,
                )
                confirm_delete = st.checkbox(
                    f"I confirm deletion of production #{int(selected['id'])}",
                    key="confirm_delete_production",
                )
                with st.container(key="alsadi-danger-production"):
                    if st.button(
                        "Delete entry", disabled=not confirm_delete, width="stretch",
                        key="delete_production_button",
                    ):
                        try:
                            result = delete_production(int(selected["id"]), user.actor)
                            flash_mutation(result, "Production entry deleted.")
                        except Exception as exc:
                            show_factory_error(exc)

    spacer("bottom")
