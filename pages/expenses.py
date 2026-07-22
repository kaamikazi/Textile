from __future__ import annotations

from datetime import date

import plotly.express as px
import streamlit as st

from auth import AuthenticatedUser, can
from database import create_expense, delete_expense, fetch_df, update_expense
from ui import glass_close, glass_open, money, page_header, plotly_layout, show_factory_error, show_mutation_result


EXPENSE_TYPES = ["Yarn", "Electricity", "Salary", "Maintenance", "Transport", "Rent", "Other"]


def render(user: AuthenticatedUser) -> None:
    page_header("Expense Tracking", "Track factory costs, maintenance spend, salary, and materials.", "Cost Control")
    left, right = st.columns([0.85, 1.25])
    with left:
        glass_open("New Expense")
        with st.form("expense_form", clear_on_submit=True):
            expense_type = st.selectbox("Expense Type", EXPENSE_TYPES)
            amount = st.number_input("Amount", min_value=0.01, step=500.0, format="%.2f")
            description = st.text_area("Description", placeholder="Short expense note")
            expense_date = st.date_input("Date", value=date.today())
            submitted = st.form_submit_button("Record Expense", width="stretch")
        if submitted:
            try:
                result = create_expense(expense_type, amount, description, expense_date, user.actor)
                show_mutation_result(result, "Expense recorded.")
            except Exception as exc:
                show_factory_error(exc)
        glass_close()

    expenses = fetch_df(
        """
        SELECT id, expense_type, amount, description, expense_date, created_at, updated_at
        FROM expenses ORDER BY expense_date DESC, id DESC
        """
    )
    with right:
        glass_open("Expense Mix")
        if expenses.empty:
            st.info("No expenses recorded yet.")
        else:
            by_type = expenses.groupby("expense_type", as_index=False)["amount"].sum()
            fig = px.bar(
                by_type, x="expense_type", y="amount", color="expense_type",
                color_discrete_sequence=px.colors.qualitative.Safe,
            )
            st.plotly_chart(plotly_layout(fig, 320), width="stretch")
        glass_close()

    glass_open(f"Expense Ledger - {money(expenses['amount'].sum() if not expenses.empty else 0)}")
    st.dataframe(expenses, width="stretch", hide_index=True)
    st.download_button("Export Expenses CSV", expenses.to_csv(index=False).encode("utf-8"), "expenses.csv", "text/csv")
    glass_close()

    if can(user, "manage_records") and not expenses.empty:
        glass_open("Edit or Delete Expense")
        labels = {
            f"#{int(row.id)} · {row.expense_date} · {row.expense_type} · BDT {row.amount:,.2f}": int(row.id)
            for row in expenses.itertuples()
        }
        selected_label = st.selectbox("Expense", list(labels), key="manage_expense")
        selected = expenses[expenses["id"] == labels[selected_label]].iloc[0]
        with st.form("edit_expense_form"):
            edit_type = st.selectbox(
                "Expense Type", EXPENSE_TYPES,
                index=EXPENSE_TYPES.index(selected["expense_type"]) if selected["expense_type"] in EXPENSE_TYPES else len(EXPENSE_TYPES) - 1,
            )
            edit_amount = st.number_input("Amount", min_value=0.01, value=float(selected["amount"]), format="%.2f")
            edit_description = st.text_area("Description", value=str(selected["description"] or ""))
            edit_date = st.date_input("Date", value=date.fromisoformat(str(selected["expense_date"])), key="edit_expense_date")
            update_clicked = st.form_submit_button("Update Expense", width="stretch")
        if update_clicked:
            try:
                result = update_expense(
                    int(selected["id"]), edit_type, edit_amount, edit_description, edit_date, user.actor
                )
                show_mutation_result(result, "Expense updated.")
                st.rerun()
            except Exception as exc:
                show_factory_error(exc)
        confirm_delete = st.checkbox(
            f"I confirm deletion of expense #{int(selected['id'])}", key="confirm_delete_expense"
        )
        if st.button("Delete Expense", disabled=not confirm_delete, width="stretch"):
            try:
                result = delete_expense(int(selected["id"]), user.actor)
                show_mutation_result(result, "Expense deleted.")
                st.rerun()
            except Exception as exc:
                show_factory_error(exc)
        glass_close()
