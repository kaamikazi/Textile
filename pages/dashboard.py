from __future__ import annotations

from html import escape

import pandas as pd
import plotly.express as px
import streamlit as st

from database import fetch_df
from auth import AuthenticatedUser
from ui import glass_close, glass_open, metric_card, money, page_header, plotly_layout


def calculate_monthly_profit(
    production: pd.DataFrame,
    expenses: pd.DataFrame,
    month: str,
) -> tuple[float, float, float]:
    target = pd.Period(month, freq="M")
    production_months = pd.to_datetime(production["production_date"]).dt.to_period("M")
    expense_months = pd.to_datetime(expenses["expense_date"]).dt.to_period("M")
    earnings = float(production.loc[production_months == target, "total_amount"].sum())
    spending = float(expenses.loc[expense_months == target, "amount"].sum())
    return earnings, spending, earnings - spending


def render(user: AuthenticatedUser) -> None:
    page_header("Operations Dashboard", "Live production, cost, machine, and profitability overview.", "Premium Console")

    production = fetch_df("SELECT * FROM production_entries")
    expenses = fetch_df("SELECT * FROM expenses")
    machines = fetch_df("SELECT * FROM machines")
    activities = fetch_df("SELECT * FROM activities ORDER BY created_at DESC LIMIT 8")

    current_month = pd.Timestamp.today().strftime("%Y-%m")
    monthly_earnings, monthly_expenses, monthly_profit = calculate_monthly_profit(
        production, expenses, current_month
    )
    production["production_date"] = pd.to_datetime(production["production_date"])
    expenses["expense_date"] = pd.to_datetime(expenses["expense_date"])
    active_machines = machines[machines["status"].isin(["Running", "Active"])]["id"].count()

    c1, c2, c3, c4 = st.columns(4, gap="medium")
    with c1:
        metric_card("Total Earnings", money(production["total_amount"].sum()), "All recorded production")
    with c2:
        metric_card("Total Expenses", money(expenses["amount"].sum()), "Operational spend")
    with c3:
        metric_card("Monthly Profit", money(monthly_profit), "Current month net")
    with c4:
        metric_card("Active Machines", f"{active_machines}/{len(machines)}", "Running production lines")

    daily = production.groupby("production_date", as_index=False).agg(
        earnings=("total_amount", "sum"),
        quantity=("quantity", "sum"),
    )
    expense_daily = expenses.groupby("expense_date", as_index=False).agg(expenses=("amount", "sum"))

    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)
    left, right = st.columns([1.45, 1], gap="large")
    with left:
        glass_open("Earnings Trend")
        fig = px.area(daily, x="production_date", y="earnings", color_discrete_sequence=["#18d7ff"])
        fig.update_traces(line=dict(width=3), fillcolor="rgba(24,215,255,.18)")
        st.plotly_chart(plotly_layout(fig, 360), width="stretch")
        glass_close()

    with right:
        glass_open("Machine Status")
        if machines.empty:
            st.info("No machine records yet.")
        else:
            machine_status = machines["status"].value_counts().reset_index()
            machine_status.columns = ["status", "count"]
            fig = px.pie(
                machine_status, names="status", values="count", hole=0.58,
                color_discrete_sequence=["#18d7ff", "#3b82f6", "#38e6a1", "#ff637d"],
            )
            st.plotly_chart(plotly_layout(fig, 360), width="stretch")
        glass_close()

    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)
    left, right = st.columns([1.08, 1], gap="large")
    with left:
        glass_open("Production by Product")
        product_df = production.groupby("product_type", as_index=False)["quantity"].sum().sort_values("quantity")
        if product_df.empty:
            st.info("No production records yet.")
        else:
            fig = px.bar(product_df, x="quantity", y="product_type", orientation="h", color_discrete_sequence=["#3b82f6"])
            st.plotly_chart(plotly_layout(fig, 320), width="stretch")
        glass_close()

    with right:
        glass_open("Recent Activities")
        for _, row in activities.iterrows():
            st.markdown(
                f"""
                <div class="activity">
                    <div class="activity-dot"></div>
                    <div>
                        <p class="activity-title">{escape(str(row['title']))}</p>
                        <p class="activity-detail">{escape(str(row['detail'] or row['activity_type']))}</p>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        glass_close()

    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)
    glass_open("Expense Pulse")
    if expense_daily.empty:
        st.info("No expense records yet.")
    else:
        fig = px.line(expense_daily, x="expense_date", y="expenses", markers=True, color_discrete_sequence=["#ff637d"])
        st.plotly_chart(plotly_layout(fig, 280), width="stretch")
    glass_close()
    st.markdown('<div class="bottom-safe-space"></div>', unsafe_allow_html=True)
