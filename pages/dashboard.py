from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from database import fetch_df
from ui import glass_close, glass_open, metric_card, money, page_header, plotly_layout


def render() -> None:
    page_header("Operations Dashboard", "Live production, cost, machine, and profitability overview.", "Premium Console")

    production = fetch_df("SELECT * FROM production_entries")
    expenses = fetch_df("SELECT * FROM expenses")
    machines = fetch_df("SELECT * FROM machines")
    activities = fetch_df("SELECT * FROM activities ORDER BY created_at DESC LIMIT 8")

    production["production_date"] = pd.to_datetime(production["production_date"])
    expenses["expense_date"] = pd.to_datetime(expenses["expense_date"])
    today = pd.Timestamp.today().normalize()
    month_start = today.replace(day=1)
    next_month_start = month_start + pd.offsets.MonthBegin(1)
    current_month_production = production[
        (production["production_date"] >= month_start)
        & (production["production_date"] < next_month_start)
    ]
    current_month_expenses = expenses[
        (expenses["expense_date"] >= month_start)
        & (expenses["expense_date"] < next_month_start)
    ]
    monthly_earnings = current_month_production["total_amount"].sum()
    monthly_expenses = current_month_expenses["amount"].sum()
    monthly_profit = monthly_earnings - monthly_expenses
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
        st.plotly_chart(plotly_layout(fig, 360), use_container_width=True)
        glass_close()

    with right:
        glass_open("Machine Status")
        machine_status = machines["status"].value_counts().reset_index()
        machine_status.columns = ["status", "count"]
        fig = px.pie(
            machine_status,
            names="status",
            values="count",
            hole=0.58,
            color_discrete_sequence=["#18d7ff", "#3b82f6", "#38e6a1", "#ff637d"],
        )
        st.plotly_chart(plotly_layout(fig, 360), use_container_width=True)
        glass_close()

    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)
    left, right = st.columns([1.08, 1], gap="large")
    with left:
        glass_open("Production by Product")
        product_df = production.groupby("product_type", as_index=False)["quantity"].sum().sort_values("quantity")
        fig = px.bar(product_df, x="quantity", y="product_type", orientation="h", color_discrete_sequence=["#3b82f6"])
        st.plotly_chart(plotly_layout(fig, 320), use_container_width=True)
        glass_close()

    with right:
        glass_open("Recent Activities")
        for _, row in activities.iterrows():
            st.markdown(
                f"""
                <div class="activity">
                    <div class="activity-dot"></div>
                    <div>
                        <p class="activity-title">{row['title']}</p>
                        <p class="activity-detail">{row['detail'] or row['activity_type']}</p>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        glass_close()

    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)
    glass_open("Expense Pulse")
    fig = px.line(expense_daily, x="expense_date", y="expenses", markers=True, color_discrete_sequence=["#ff637d"])
    st.plotly_chart(plotly_layout(fig, 280), use_container_width=True)
    glass_close()
    st.markdown('<div class="bottom-safe-space"></div>', unsafe_allow_html=True)
