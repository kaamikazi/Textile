from __future__ import annotations

from datetime import date

import streamlit as st

from auth import AuthenticatedUser, can
from database import create_production, delete_production, fetch_df, update_production
from ui import glass_close, glass_open, money, page_header, show_factory_error, show_mutation_result


PRODUCT_TYPES = ["Rib Collar", "Cuff", "Jacquard Panel", "Flat Knit Body", "Neck Tape", "Other"]


def render(user: AuthenticatedUser) -> None:
    page_header("Daily Production Entry", "Capture machine output and auto-calculate production value.", "Auto Total")
    machines = fetch_df("SELECT machine_number FROM machines ORDER BY machine_number")
    machine_options = machines["machine_number"].tolist()
    left, right = st.columns([0.9, 1.2])
    with left:
        glass_open("New Production")
        if not can(user, "add_production"):
            st.info("Your role cannot add production entries.")
        elif not machine_options:
            st.warning("An administrator must add a machine before production can be recorded.")
        else:
            with st.form("production_form", clear_on_submit=True):
                production_date = st.date_input("Date", value=date.today())
                machine_number = st.selectbox("Machine Number", machine_options)
                operator_name = st.text_input("Operator Name", placeholder="Operator name")
                product_type = st.selectbox("Product Type", PRODUCT_TYPES)
                quantity = st.number_input("Quantity", min_value=1, step=1)
                rate_per_unit = st.number_input("Rate Per Unit", min_value=0.01, step=0.5, format="%.2f")
                st.markdown(f"**Total Amount:** `{money(quantity * rate_per_unit)}`")
                submitted = st.form_submit_button("Save Production", width="stretch")
            if submitted:
                try:
                    result = create_production(
                        production_date, machine_number, operator_name, product_type,
                        int(quantity), rate_per_unit, user.actor,
                    )
                    show_mutation_result(result, "Production entry saved.")
                except Exception as exc:
                    show_factory_error(exc)
        glass_close()

    production = fetch_df(
        """
        SELECT id, production_date, machine_number, operator_name, product_type,
               quantity, rate_per_unit, total_amount, created_at, updated_at
        FROM production_entries ORDER BY production_date DESC, id DESC
        """
    )
    with right:
        glass_open("Recent Production")
        st.dataframe(production.head(25), width="stretch", hide_index=True)
        st.download_button(
            "Export Recent CSV", production.head(25).to_csv(index=False).encode("utf-8"),
            "recent-production.csv", "text/csv",
        )
        glass_close()

    if can(user, "manage_records") and not production.empty:
        glass_open("Edit or Delete Production")
        labels = {
            f"#{int(row.id)} · {row.production_date} · {row.machine_number} · {int(row.quantity)} units": int(row.id)
            for row in production.itertuples()
        }
        selected_label = st.selectbox("Production entry", list(labels), key="manage_production")
        selected = production[production["id"] == labels[selected_label]].iloc[0]
        with st.form("edit_production_form"):
            edit_date = st.date_input("Date", value=date.fromisoformat(str(selected["production_date"])), key="edit_prod_date")
            edit_machine = st.selectbox(
                "Machine Number", machine_options,
                index=machine_options.index(selected["machine_number"]) if selected["machine_number"] in machine_options else 0,
                key="edit_prod_machine",
            )
            edit_operator = st.text_input("Operator Name", value=str(selected["operator_name"]))
            edit_product = st.selectbox(
                "Product Type", PRODUCT_TYPES,
                index=PRODUCT_TYPES.index(selected["product_type"]) if selected["product_type"] in PRODUCT_TYPES else len(PRODUCT_TYPES) - 1,
            )
            edit_quantity = st.number_input("Quantity", min_value=1, step=1, value=int(selected["quantity"]))
            edit_rate = st.number_input("Rate Per Unit", min_value=0.01, value=float(selected["rate_per_unit"]), format="%.2f")
            update_clicked = st.form_submit_button("Update Production", width="stretch")
        if update_clicked:
            try:
                result = update_production(
                    int(selected["id"]), edit_date, edit_machine, edit_operator,
                    edit_product, int(edit_quantity), edit_rate, user.actor,
                )
                show_mutation_result(result, "Production entry updated.")
                st.rerun()
            except Exception as exc:
                show_factory_error(exc)
        confirm_delete = st.checkbox(
            f"I confirm deletion of production #{int(selected['id'])}", key="confirm_delete_production"
        )
        if st.button("Delete Production Entry", disabled=not confirm_delete, width="stretch"):
            try:
                result = delete_production(int(selected["id"]), user.actor)
                show_mutation_result(result, "Production entry deleted.")
                st.rerun()
            except Exception as exc:
                show_factory_error(exc)
        glass_close()
