from __future__ import annotations

from datetime import date

import plotly.express as px
import streamlit as st

from database import add_expense, fetch_df
from ui import glass_close, glass_open, money, page_header, plotly_layout


def render() -> None:
    page_header("Expense Tracking", "Track factory costs, maintenance spend, salary, and materials.", "Cost Control")

    left, right = st.columns([0.85, 1.25])
    with left:
        glass_open("New Expense")
        with st.form("expense_form", clear_on_submit=True):
            expense_type = st.selectbox("Expense Type", ["Yarn", "Electricity", "Salary", "Maintenance", "Transport", "Rent", "Other"])
            amount = st.number_input("Amount", min_value=0.0, step=500.0, format="%.2f")
            description = st.text_area("Description", placeholder="Short expense note")
            expense_date = st.date_input("Date", value=date.today())
            submitted = st.form_submit_button("Record Expense")
            if submitted:
                if amount <= 0:
                    st.error("Amount must be greater than zero.")
                else:
                    add_expense(expense_type, float(amount), description.strip(), expense_date)
                    st.success("Expense recorded.")
        glass_close()

    with right:
        expenses = fetch_df("SELECT expense_type, amount, description, expense_date FROM expenses ORDER BY expense_date DESC, id DESC")
        glass_open("Expense Mix")
        by_type = expenses.groupby("expense_type", as_index=False)["amount"].sum()
        fig = px.bar(by_type, x="expense_type", y="amount", color="expense_type", color_discrete_sequence=px.colors.qualitative.Safe)
        st.plotly_chart(plotly_layout(fig, 320), use_container_width=True)
        glass_close()

    glass_open(f"Expense Ledger - {money(expenses['amount'].sum())}")
    st.dataframe(expenses, use_container_width=True, hide_index=True)
    st.download_button("Export Expenses CSV", expenses.to_csv(index=False).encode("utf-8"), "expenses.csv", "text/csv")
    glass_close()
