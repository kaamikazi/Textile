from __future__ import annotations

from datetime import date

import streamlit as st

from database import add_production, fetch_df
from ui import glass_close, glass_open, money, page_header


def render() -> None:
    page_header("Daily Production Entry", "Capture machine output and auto-calculate production value.", "Auto Total")

    machines = fetch_df("SELECT machine_number FROM machines ORDER BY machine_number")
    machine_options = machines["machine_number"].tolist() or ["M-01"]

    left, right = st.columns([0.9, 1.2])
    with left:
        glass_open("New Production")
        with st.form("production_form", clear_on_submit=True):
            production_date = st.date_input("Date", value=date.today())
            machine_number = st.selectbox("Machine Number", machine_options)
            operator_name = st.text_input("Operator Name", placeholder="Operator name")
            product_type = st.selectbox("Product Type", ["Rib Collar", "Cuff", "Jacquard Panel", "Flat Knit Body", "Neck Tape", "Other"])
            quantity = st.number_input("Quantity", min_value=0, step=1)
            rate_per_unit = st.number_input("Rate Per Unit", min_value=0.0, step=0.5, format="%.2f")
            st.markdown(f"**Total Amount:** `{money(quantity * rate_per_unit)}`")
            submitted = st.form_submit_button("Save Production")
            if submitted:
                if not operator_name.strip() or quantity <= 0 or rate_per_unit <= 0:
                    st.error("Operator, quantity, and rate are required.")
                else:
                    add_production(production_date, machine_number, operator_name.strip(), product_type, int(quantity), float(rate_per_unit))
                    st.success("Production entry saved.")
        glass_close()

    with right:
        glass_open("Recent Production")
        production = fetch_df("SELECT production_date, machine_number, operator_name, product_type, quantity, rate_per_unit, total_amount FROM production_entries ORDER BY production_date DESC, id DESC LIMIT 25")
        st.dataframe(production, use_container_width=True, hide_index=True)
        csv = production.to_csv(index=False).encode("utf-8")
        st.download_button("Export Recent CSV", csv, "recent-production.csv", "text/csv")
        glass_close()
