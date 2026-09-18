from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from auth import AuthenticatedUser, can
from database import create_expense, delete_expense, fetch_df, update_expense
from ui import (
    CHART_COLORS,
    CHART_SEQUENCE,
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


EXPENSE_TYPES = ["Yarn", "Electricity", "Salary", "Maintenance", "Transport", "Rent", "Other"]

LEDGER_COLUMNS = {
    "id": st.column_config.NumberColumn("ID", width="small", format="%d"),
    "expense_date": st.column_config.DateColumn("Date", format="DD MMM YYYY", width="small"),
    "expense_type": st.column_config.TextColumn("Type", width="small"),
    "amount": st.column_config.NumberColumn("Amount (BDT)", format="%.2f"),
    "description": st.column_config.TextColumn("Description", width="large"),
}


def _expense_mix(by_type: pd.DataFrame, total: float) -> go.Figure:
    """Ranked horizontal bars. The previous vertical rainbow bar chart gave
    every category its own colour, which implied a distinction that does not
    exist - the only thing that matters here is relative size."""
    ordered = by_type.sort_values("amount")
    share = ordered["amount"] / total * 100 if total else ordered["amount"] * 0
    fig = go.Figure(
        go.Bar(
            x=ordered["amount"], y=ordered["expense_type"], orientation="h",
            marker_color=CHART_COLORS["red"], marker_line_width=0,
            customdata=share,
            hovertemplate="<b>%{y}</b><br>BDT %{x:,.0f}<br>%{customdata:.0f}% of spend<extra></extra>",
        )
    )
    fig.update_layout(hovermode="closest", bargap=0.42)
    return fig


def _monthly_spend(expenses: pd.DataFrame) -> go.Figure:
    frame = expenses.copy()
    frame["month"] = pd.to_datetime(frame["expense_date"]).dt.to_period("M").astype(str)
    grouped = frame.groupby(["month", "expense_type"], as_index=False)["amount"].sum()
    fig = go.Figure()
    for index, expense_type in enumerate(sorted(grouped["expense_type"].unique())):
        subset = grouped[grouped["expense_type"] == expense_type]
        fig.add_trace(
            go.Bar(
                x=subset["month"], y=subset["amount"], name=expense_type,
                marker_color=CHART_SEQUENCE[index % len(CHART_SEQUENCE)],
                marker_line_width=0,
                hovertemplate=f"{expense_type}: BDT %{{y:,.0f}}<extra></extra>",
            )
        )
    fig.update_layout(barmode="stack", bargap=0.35)
    return fig


def _validate(amount: float) -> list[str]:
    return [] if amount > 0 else ["Amount must be greater than zero."]


def render(user: AuthenticatedUser) -> None:
    page_header(
        "Expenses",
        "Track yarn, power, salary, maintenance and other factory costs.",
        eyebrow="Daily Operations",
    )
    sync_bar(key="expenses")

    expenses = fetch_df(
        """
        SELECT id, expense_type, amount, description, expense_date, created_at, updated_at
        FROM expenses ORDER BY expense_date DESC, id DESC
        """
    )

    total_spend = float(expenses["amount"].sum()) if not expenses.empty else 0.0
    today = date.today()
    current_month = pd.Timestamp.today().strftime("%Y-%m")

    if not expenses.empty:
        months = pd.to_datetime(expenses["expense_date"]).dt.to_period("M").astype(str)
        month_spend = float(expenses.loc[months == current_month, "amount"].sum())
        dates = pd.to_datetime(expenses["expense_date"]).dt.date
        today_spend = float(expenses.loc[dates == today, "amount"].sum())
        top_type = expenses.groupby("expense_type")["amount"].sum().idxmax()
        top_amount = float(expenses.groupby("expense_type")["amount"].sum().max())
    else:
        month_spend = today_spend = top_amount = 0.0
        top_type = "None"

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        kpi_tile("Today", money(today_spend), "recorded today", tone="warn")
    with k2:
        kpi_tile("This Month", money(month_spend), pd.Timestamp.today().strftime("%B %Y"), tone="warn")
    with k3:
        kpi_tile("All Time", money(total_spend), f"{len(expenses):,} entries")
    with k4:
        kpi_tile(
            "Largest Category", top_type,
            f"{money(top_amount)} ({top_amount / total_spend * 100:.0f}% of spend)" if total_spend else "No spend yet",
        )

    spacer()

    left, right = st.columns([0.9, 1.3])

    with left:
        with card("New Expense", key="expense-form"):
            if not can(user, "add_expense"):
                st.info("Your role cannot record expenses.", icon="ℹ")
            else:
                with st.form("expense_form", clear_on_submit=True):
                    expense_type = st.selectbox("Expense type", EXPENSE_TYPES)
                    amount = st.number_input(
                        "Amount (BDT)", min_value=0.01, step=500.0, format="%.2f"
                    )
                    expense_date = st.date_input("Date", value=today, max_value=today)
                    description = st.text_area(
                        "Description", placeholder="Short note, e.g. supplier or invoice reference",
                        height=90,
                    )
                    submitted = st.form_submit_button(
                        "Record expense", type="primary", width="stretch"
                    )

                if submitted:
                    problems = _validate(float(amount))
                    if problems:
                        for problem in problems:
                            field_error(problem)
                    else:
                        try:
                            with st.spinner("Saving..."):
                                result = create_expense(
                                    expense_type, amount, description, expense_date, user.actor
                                )
                            show_mutation_result(result, "Expense recorded.")
                        except Exception as exc:
                            show_factory_error(exc)

    with right:
        with card("Expense Mix", key="expense-mix", note="Share of total spend"):
            if expenses.empty:
                empty_state("No expenses recorded", "Categories rank here once costs are entered.")
            else:
                by_type = expenses.groupby("expense_type", as_index=False)["amount"].sum()
                chart(_expense_mix(by_type, total_spend), max(200, 42 * len(by_type)), key="chart-expense-mix")

    if not expenses.empty and pd.to_datetime(expenses["expense_date"]).dt.to_period("M").nunique() > 1:
        with card("Monthly Spend", key="expense-monthly", note="Stacked by category"):
            chart(_monthly_spend(expenses), 280, key="chart-expense-monthly")

    section_head("Expense Ledger", money(total_spend) + " total")
    with card(key="expense-ledger"):
        if expenses.empty:
            empty_state("Nothing to show", "Recorded expenses appear here, newest first.", icon="▤")
        else:
            table = expenses.copy()
            table["expense_date"] = pd.to_datetime(table["expense_date"])
            st.dataframe(
                table[list(LEDGER_COLUMNS)],
                column_config=LEDGER_COLUMNS,
                width="stretch",
                hide_index=True,
                height=340,
            )
            st.download_button(
                "Export expenses as CSV",
                expenses.to_csv(index=False).encode("utf-8"),
                "expenses.csv",
                "text/csv",
                key="export-expenses",
            )

    if can(user, "manage_records") and not expenses.empty:
        section_head("Manage Entries", "Admin only")
        with card(key="expense-manage"):
            labels = {
                f"#{int(row.id)}  ·  {row.expense_date}  ·  {row.expense_type}"
                f"  ·  BDT {row.amount:,.2f}": int(row.id)
                for row in expenses.itertuples()
            }
            selected_label = st.selectbox("Select an expense", list(labels), key="manage_expense")
            selected = expenses[expenses["id"] == labels[selected_label]].iloc[0]

            edit_tab, delete_tab = st.tabs(["Edit", "Delete"])

            with edit_tab:
                with st.form("edit_expense_form"):
                    type_col, amount_col = st.columns(2)
                    with type_col:
                        edit_type = st.selectbox(
                            "Expense type", EXPENSE_TYPES,
                            index=EXPENSE_TYPES.index(selected["expense_type"])
                            if selected["expense_type"] in EXPENSE_TYPES else len(EXPENSE_TYPES) - 1,
                        )
                    with amount_col:
                        edit_amount = st.number_input(
                            "Amount (BDT)", min_value=0.01,
                            value=float(selected["amount"]), format="%.2f",
                        )
                    edit_date = st.date_input(
                        "Date", value=date.fromisoformat(str(selected["expense_date"])),
                        key="edit_expense_date",
                    )
                    edit_description = st.text_area(
                        "Description", value=str(selected["description"] or ""), height=90
                    )
                    update_clicked = st.form_submit_button(
                        "Update expense", type="primary", width="stretch"
                    )

                if update_clicked:
                    problems = _validate(float(edit_amount))
                    if problems:
                        for problem in problems:
                            field_error(problem)
                    else:
                        try:
                            result = update_expense(
                                int(selected["id"]), edit_type, edit_amount,
                                edit_description, edit_date, user.actor,
                            )
                            show_mutation_result(result, "Expense updated.")
                            st.rerun()
                        except Exception as exc:
                            show_factory_error(exc)

            with delete_tab:
                st.markdown(
                    f'<p class="field-hint">Deleting expense <strong>#{int(selected["id"])}</strong> '
                    f'({money(float(selected["amount"]))}) removes it permanently. '
                    "The action is written to the audit log.</p>",
                    unsafe_allow_html=True,
                )
                confirm_delete = st.checkbox(
                    f"I confirm deletion of expense #{int(selected['id'])}",
                    key="confirm_delete_expense",
                )
                with st.container(key="alsadi-danger-expense"):
                    if st.button(
                        "Delete expense", disabled=not confirm_delete, width="stretch",
                        key="delete_expense_button",
                    ):
                        try:
                            result = delete_expense(int(selected["id"]), user.actor)
                            show_mutation_result(result, "Expense deleted.")
                            st.rerun()
                        except Exception as exc:
                            show_factory_error(exc)

    spacer("bottom")
