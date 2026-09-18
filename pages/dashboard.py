from __future__ import annotations

from datetime import date, timedelta
from html import escape

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from auth import AuthenticatedUser
from database import fetch_df
from ui import (
    CHART_COLORS,
    card,
    chart,
    compact_number,
    delta_html,
    empty_state,
    kpi_tile,
    money,
    page_header,
    section_head,
    spacer,
    status_chip,
    sync_bar,
)


TREND_DAYS = 30
RUNNING_STATUSES = ["Running", "Active"]


def calculate_monthly_profit(
    production: pd.DataFrame,
    expenses: pd.DataFrame,
    month: str,
) -> tuple[float, float, float]:
    """Earnings, spending and profit for a single YYYY-MM period.

    Covered by tests/test_database.py::test_monthly_profit_only_uses_selected_month.
    """
    target = pd.Period(month, freq="M")
    production_months = pd.to_datetime(production["production_date"]).dt.to_period("M")
    expense_months = pd.to_datetime(expenses["expense_date"]).dt.to_period("M")
    earnings = float(production.loc[production_months == target, "total_amount"].sum())
    spending = float(expenses.loc[expense_months == target, "amount"].sum())
    return earnings, spending, earnings - spending


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _day_totals(frame: pd.DataFrame, date_column: str, value_column: str, day: date) -> float:
    if frame.empty:
        return 0.0
    mask = frame[date_column].dt.date == day
    return float(frame.loc[mask, value_column].sum())


def _daily_series(production: pd.DataFrame, expenses: pd.DataFrame, days: int) -> pd.DataFrame:
    """One row per calendar day so the trend line has no gaps on idle days."""
    end = date.today()
    start = end - timedelta(days=days - 1)
    index = pd.date_range(start, end, freq="D")

    if production.empty:
        earnings = pd.Series(0.0, index=index)
        quantity = pd.Series(0.0, index=index)
    else:
        grouped = production.set_index("production_date").sort_index()
        earnings = grouped["total_amount"].resample("D").sum().reindex(index, fill_value=0.0)
        quantity = grouped["quantity"].resample("D").sum().reindex(index, fill_value=0.0)

    if expenses.empty:
        spend = pd.Series(0.0, index=index)
    else:
        spend = (
            expenses.set_index("expense_date").sort_index()["amount"]
            .resample("D").sum().reindex(index, fill_value=0.0)
        )

    frame = pd.DataFrame(
        {"day": index, "earnings": earnings.values, "expenses": spend.values, "quantity": quantity.values}
    )
    frame["profit"] = frame["earnings"] - frame["expenses"]
    return frame


def _attendance_rate(attendance: pd.DataFrame, day: date) -> tuple[float, int, int]:
    if attendance.empty:
        return 0.0, 0, 0
    today_rows = attendance[attendance["attendance_date"].dt.date == day]
    if today_rows.empty:
        return 0.0, 0, 0
    present = int(today_rows["status"].isin(["Present", "Late"]).sum())
    total = int(len(today_rows))
    return (present / total * 100) if total else 0.0, present, total


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _profit_trend(frame: pd.DataFrame) -> go.Figure:
    """Income against spend, with profit as the readable line on top.

    The previous chart plotted earnings alone, which cannot answer the one
    question the owner actually has: did we make money.
    """
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=frame["day"], y=frame["earnings"], name="Income",
            marker_color="rgba(24,215,255,.42)", marker_line_width=0,
            hovertemplate="Income: BDT %{y:,.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            x=frame["day"], y=-frame["expenses"], name="Expenses",
            marker_color="rgba(248,113,113,.42)", marker_line_width=0,
            hovertemplate="Expenses: BDT %{customdata:,.0f}<extra></extra>",
            customdata=frame["expenses"],
        )
    )
    fig.add_trace(
        go.Scatter(
            x=frame["day"], y=frame["profit"], name="Profit",
            mode="lines", line=dict(color=CHART_COLORS["green"], width=2.5, shape="spline"),
            hovertemplate="Profit: BDT %{y:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(barmode="relative", bargap=0.25)
    return fig


def _utilisation_chart(machine_view: pd.DataFrame) -> go.Figure:
    """Output per machine, coloured by current status, so an idle or broken
    machine that is also producing nothing is obvious in one glance."""
    colors = {
        "Running": CHART_COLORS["accent"], "Idle": CHART_COLORS["slate"],
        "Maintenance": CHART_COLORS["amber"], "Offline": CHART_COLORS["red"],
        "Out of Service": CHART_COLORS["red"],
    }
    ordered = machine_view.sort_values("quantity")
    fig = go.Figure(
        go.Bar(
            x=ordered["quantity"], y=ordered["machine_number"], orientation="h",
            marker_color=[colors.get(s, CHART_COLORS["slate"]) for s in ordered["status"]],
            marker_line_width=0,
            customdata=ordered[["status", "earnings"]],
            hovertemplate="<b>%{y}</b><br>%{x:,.0f} units<br>BDT %{customdata[1]:,.0f}<br>%{customdata[0]}<extra></extra>",
        )
    )
    fig.update_layout(hovermode="closest", bargap=0.42)
    return fig


def _attendance_trend(attendance: pd.DataFrame) -> go.Figure:
    daily = (
        attendance.set_index("attendance_date")
        .groupby([pd.Grouper(freq="D"), "status"]).size().unstack(fill_value=0)
    )
    for column in ["Present", "Late", "Absent", "Leave"]:
        if column not in daily.columns:
            daily[column] = 0
    daily = daily.tail(TREND_DAYS)

    fig = go.Figure()
    for name, color in [
        ("Present", CHART_COLORS["green"]), ("Late", CHART_COLORS["amber"]),
        ("Leave", CHART_COLORS["slate"]), ("Absent", CHART_COLORS["red"]),
    ]:
        fig.add_trace(
            go.Bar(
                x=daily.index, y=daily[name], name=name, marker_color=color,
                marker_line_width=0,
                hovertemplate=f"{name}: %{{y}}<extra></extra>",
            )
        )
    fig.update_layout(barmode="stack", bargap=0.3)
    return fig


def _product_value_chart(production: pd.DataFrame) -> go.Figure:
    """Ranked by value, not unit count: 200 jacquard panels can be worth more
    than 2,000 neck tapes, and the old quantity chart hid that."""
    by_product = (
        production.groupby("product_type", as_index=False)
        .agg(earnings=("total_amount", "sum"), quantity=("quantity", "sum"))
        .sort_values("earnings")
    )
    fig = go.Figure(
        go.Bar(
            x=by_product["earnings"], y=by_product["product_type"], orientation="h",
            marker_color=CHART_COLORS["blue"], marker_line_width=0,
            customdata=by_product["quantity"],
            hovertemplate="<b>%{y}</b><br>BDT %{x:,.0f}<br>%{customdata:,.0f} units<extra></extra>",
        )
    )
    fig.update_layout(hovermode="closest", bargap=0.42)
    return fig


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def render(user: AuthenticatedUser) -> None:
    today = date.today()
    page_header(
        "Operations Dashboard",
        f"Factory performance for {today.strftime('%A, %d %B %Y')}.",
        eyebrow="Overview",
    )
    sync_bar(key="dashboard")

    production = fetch_df("SELECT * FROM production_entries")
    expenses = fetch_df("SELECT * FROM expenses")
    machines = fetch_df("SELECT * FROM machines")
    attendance = fetch_df("SELECT * FROM attendance")
    activities = fetch_df("SELECT * FROM activities ORDER BY created_at DESC LIMIT 7")

    current_month = pd.Timestamp.today().strftime("%Y-%m")
    monthly_earnings, monthly_expenses, monthly_profit = calculate_monthly_profit(
        production, expenses, current_month
    )

    if not production.empty:
        production["production_date"] = pd.to_datetime(production["production_date"])
    if not expenses.empty:
        expenses["expense_date"] = pd.to_datetime(expenses["expense_date"])
    if not attendance.empty:
        attendance["attendance_date"] = pd.to_datetime(attendance["attendance_date"])

    if production.empty and expenses.empty and machines.empty:
        empty_state(
            "No factory data yet",
            "Add machines first, then record daily production and expenses. "
            "This dashboard fills in as soon as entries exist.",
            icon="▦",
        )
        return

    yesterday = today - timedelta(days=1)
    today_income = _day_totals(production, "production_date", "total_amount", today)
    today_units = _day_totals(production, "production_date", "quantity", today)
    today_spend = _day_totals(expenses, "expense_date", "amount", today)
    yesterday_income = _day_totals(production, "production_date", "total_amount", yesterday)
    yesterday_profit = yesterday_income - _day_totals(expenses, "expense_date", "amount", yesterday)
    today_profit = today_income - today_spend

    # Before the first entry of the day - and on the Friday weekend - a
    # "-100%" delta is technically true but reads as a crash. Treat a day
    # with no rows as pending rather than as a collapse.
    has_today_production = (
        not production.empty and bool((production["production_date"].dt.date == today).any())
    )
    has_today_expense = (
        not expenses.empty and bool((expenses["expense_date"].dt.date == today).any())
    )
    day_started = has_today_production or has_today_expense

    running = int(machines["status"].isin(RUNNING_STATUSES).sum()) if not machines.empty else 0
    total_machines = int(len(machines))
    utilisation = (running / total_machines * 100) if total_machines else 0.0
    rate, present, headcount = _attendance_rate(attendance, today)

    # --- Today -----------------------------------------------------------
    section_head("Today", today.strftime("%d %B %Y"))
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        kpi_tile(
            "Production Value",
            money(today_income) if has_today_production else "Awaiting entry",
            f"{compact_number(today_units)} units" if has_today_production
            else f"Yesterday: {money(yesterday_income)}",
            tone="accent" if has_today_production else "warn",
            delta=delta_html(today_income, yesterday_income) if has_today_production else None,
        )
    with c2:
        if day_started:
            kpi_tile(
                "Profit Today", money(today_profit),
                f"{money(today_spend)} spent",
                tone="positive" if today_profit >= 0 else "negative",
                delta=delta_html(today_profit, yesterday_profit),
            )
        else:
            kpi_tile(
                "Profit Today", "No entries yet",
                f"Yesterday: {money(yesterday_profit)}", tone="warn",
            )
    with c3:
        kpi_tile(
            "Machine Utilisation", f"{utilisation:.0f}%",
            f"{running} of {total_machines} running",
            tone="accent" if utilisation >= 60 else "warn",
            progress=utilisation,
        )
    with c4:
        if headcount:
            kpi_tile(
                "Attendance", f"{rate:.0f}%", f"{present} of {headcount} marked in",
                tone="positive" if rate >= 85 else "warn", progress=rate,
            )
        else:
            kpi_tile("Attendance", "Not marked", "No attendance recorded today", tone="warn")

    # --- Month to date ---------------------------------------------------
    section_head("Month to Date", pd.Timestamp.today().strftime("%B %Y"))
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        kpi_tile("Income", money(monthly_earnings), "Production value this month")
    with m2:
        kpi_tile("Expenses", money(monthly_expenses), "Operational spend", tone="warn")
    with m3:
        kpi_tile(
            "Net Profit", money(monthly_profit),
            f"Margin {(monthly_profit / monthly_earnings * 100):.0f}%" if monthly_earnings else "No income yet",
            tone="positive" if monthly_profit >= 0 else "negative",
        )
    with m4:
        kpi_tile(
            "All-Time Income", money(float(production["total_amount"].sum()) if not production.empty else 0),
            f"{len(production):,} entries recorded",
        )

    spacer()

    # --- Trend -----------------------------------------------------------
    with card("Profit Trend", key="profit-trend", note=f"Last {TREND_DAYS} days"):
        if production.empty and expenses.empty:
            empty_state("Nothing to plot yet", "Record production or expenses to see the trend.")
        else:
            chart(_profit_trend(_daily_series(production, expenses, TREND_DAYS)), 300, key="chart-profit")

    left, right = st.columns([1.3, 1])
    with left:
        with card("Machine Output", key="machine-output", note="Units produced, coloured by status"):
            if machines.empty:
                empty_state("No machines registered", "An administrator can add machines from the Machines page.")
            else:
                totals = (
                    production.groupby("machine_number", as_index=False)
                    .agg(quantity=("quantity", "sum"), earnings=("total_amount", "sum"))
                    if not production.empty
                    else pd.DataFrame(columns=["machine_number", "quantity", "earnings"])
                )
                machine_view = machines.merge(totals, on="machine_number", how="left").fillna(
                    {"quantity": 0, "earnings": 0}
                )
                chart(_utilisation_chart(machine_view), max(220, 46 * len(machine_view)), key="chart-machines")

    with right:
        with card("Machine Status", key="machine-status"):
            if machines.empty:
                empty_state("No machines registered", "Add a machine to track its status.")
            else:
                counts = machines["status"].value_counts()
                for status, count in counts.items():
                    st.markdown(
                        f'<div class="status-row">{status_chip(status)}'
                        f'<span class="status-count">{count}</span></div>',
                        unsafe_allow_html=True,
                    )

    left, right = st.columns([1, 1])
    with left:
        with card("Product Value", key="product-value", note="Revenue by product type"):
            if production.empty:
                empty_state("No production yet", "Recorded entries will rank here by value.")
            else:
                chart(_product_value_chart(production), 260, key="chart-products")

    with right:
        with card("Attendance", key="attendance-trend", note=f"Last {TREND_DAYS} days"):
            if attendance.empty:
                empty_state("No attendance recorded", "Mark attendance from the Employees page.")
            else:
                chart(_attendance_trend(attendance), 260, key="chart-attendance")

    with card("Recent Activity", key="recent-activity"):
        if activities.empty:
            empty_state("No activity yet", "Saves, edits and deletions appear here as they happen.")
        else:
            for row in activities.itertuples():
                stamp = str(row.created_at)[:16].replace("T", " ")
                detail = escape(str(row.detail or row.activity_type))
                st.markdown(
                    f"""
                    <div class="activity">
                        <div class="activity-dot"></div>
                        <div style="flex:1;min-width:0">
                            <p class="activity-title">{escape(str(row.title))}</p>
                            <p class="activity-detail">{detail}</p>
                        </div>
                        <span class="activity-time">{escape(stamp)}</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    spacer("bottom")
