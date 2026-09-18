from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from auth import AuthenticatedUser, can
from data_safety import backup_excel, record_export
from database import FactoryError, fetch_df, set_excel_sync_status
from spreadsheet_sync import WORKBOOK_PATH, export_month_workbook, sync_factory_workbook
from ui import (
    CHART_COLORS,
    card,
    chart,
    empty_state,
    kpi_tile,
    money,
    page_header,
    section_head,
    show_factory_error,
    spacer,
    sync_status_panel,
)


EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

REPORT_COLUMNS = {
    "period": st.column_config.TextColumn("Period", width="small"),
    "quantity": st.column_config.NumberColumn("Units", format="%d"),
    "earnings": st.column_config.NumberColumn("Income (BDT)", format="%.2f"),
    "expenses": st.column_config.NumberColumn("Expenses (BDT)", format="%.2f"),
    "profit": st.column_config.NumberColumn("Profit (BDT)", format="%.2f"),
}


def _report_frame(period: str) -> pd.DataFrame:
    production = fetch_df("SELECT production_date, quantity, total_amount FROM production_entries")
    expenses = fetch_df("SELECT expense_date, amount FROM expenses")
    freq = {"Daily": "D", "Weekly": "W", "Monthly": "M"}[period]

    if production.empty:
        income = pd.DataFrame(columns=["quantity", "earnings"])
    else:
        dates = pd.to_datetime(production["production_date"], errors="coerce")
        income = production.groupby(dates.dt.to_period(freq)).agg(
            quantity=("quantity", "sum"), earnings=("total_amount", "sum")
        )

    if expenses.empty:
        spend = pd.DataFrame(columns=["expenses"])
    else:
        dates = pd.to_datetime(expenses["expense_date"], errors="coerce")
        spend = expenses.groupby(dates.dt.to_period(freq)).agg(expenses=("amount", "sum"))

    report = income.join(spend, how="outer").fillna(0)
    if report.empty:
        return pd.DataFrame(columns=["period", "quantity", "earnings", "expenses", "profit"])
    report = report.reset_index(names="date")
    report["period"] = report["date"].astype(str)
    report["profit"] = report["earnings"] - report["expenses"]
    return report[["period", "quantity", "earnings", "expenses", "profit"]]


def _available_months() -> list[str]:
    frames = [
        fetch_df("SELECT production_date AS entry_date FROM production_entries"),
        fetch_df("SELECT expense_date AS entry_date FROM expenses"),
        fetch_df("SELECT attendance_date AS entry_date FROM attendance"),
    ]
    dates = pd.concat(frames, ignore_index=True)
    if dates.empty:
        return []
    months = pd.to_datetime(dates["entry_date"], errors="coerce").dt.to_period("M").astype(str)
    return sorted([month for month in months.dropna().unique().tolist() if month != "NaT"], reverse=True)


def _mark_sync_success(path: Path, user: AuthenticatedUser, description: str) -> None:
    set_excel_sync_status("Synced", f"Workbook updated: {path.name}")
    record_export(user.actor, description)


def _mark_sync_failure(error: Exception) -> None:
    message = (
        "Close factory_records.xlsx in Excel, then retry the spreadsheet sync."
        if isinstance(error, PermissionError)
        else f"Spreadsheet sync failed: {error}"
    )
    set_excel_sync_status("Failed", message)
    st.error(message, icon="⚠")


def _download_excel(
    label: str, path: Path, key: str, user: AuthenticatedUser, description: str
) -> None:
    st.download_button(
        label, data=path.read_bytes(), file_name=path.name, mime=EXCEL_MIME,
        key=key, width="stretch", on_click=record_export,
        args=(user.actor, description),
    )


def _profit_chart(report: pd.DataFrame) -> go.Figure:
    """Income and spend as opposing bars with profit as a line.

    The previous grouped bar chart put earnings, expenses and profit
    side by side, which made profit look like a third income stream
    rather than the difference between the other two.
    """
    tail = report.tail(24)
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=tail["period"], y=tail["earnings"], name="Income",
            marker_color="rgba(24,215,255,.45)", marker_line_width=0,
            hovertemplate="Income: BDT %{y:,.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            x=tail["period"], y=-tail["expenses"], name="Expenses",
            marker_color="rgba(248,113,113,.45)", marker_line_width=0,
            customdata=tail["expenses"],
            hovertemplate="Expenses: BDT %{customdata:,.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=tail["period"], y=tail["profit"], name="Profit", mode="lines+markers",
            line=dict(color=CHART_COLORS["green"], width=2.5),
            marker=dict(size=5),
            hovertemplate="Profit: BDT %{y:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(barmode="relative", bargap=0.3)
    return fig


def render(user: AuthenticatedUser) -> None:
    page_header(
        "Reports",
        "Daily, weekly and monthly performance with controlled Excel and CSV exports.",
        eyebrow="Analysis",
    )

    period_tab_names = ["Daily", "Weekly", "Monthly"]
    tabs = st.tabs(period_tab_names + ["Excel Export", "Roadmap"])

    for tab, period in zip(tabs[:3], period_tab_names):
        with tab:
            _render_period(period, user)

    with tabs[3]:
        _render_excel(user)

    with tabs[4]:
        _render_roadmap()

    spacer("bottom")


def _render_period(period: str, user: AuthenticatedUser) -> None:
    report = _report_frame(period)

    if report.empty:
        empty_state(
            f"No {period.lower()} data yet",
            "Record production and expenses to build this report.",
            icon="▤",
        )
        return

    income = float(report["earnings"].sum())
    spend = float(report["expenses"].sum())
    profit = float(report["profit"].sum())
    units = float(report["quantity"].sum())

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        kpi_tile("Income", money(income), f"{units:,.0f} units")
    with k2:
        kpi_tile("Expenses", money(spend), "total spend", tone="warn")
    with k3:
        kpi_tile(
            "Profit", money(profit),
            f"Margin {(profit / income * 100):.0f}%" if income else "No income",
            tone="positive" if profit >= 0 else "negative",
        )
    with k4:
        best = report.loc[report["profit"].idxmax()]
        kpi_tile("Best Period", str(best["period"]), money(float(best["profit"])), tone="positive")

    spacer()

    with card(f"{period} Performance", key=f"report-chart-{period.lower()}",
              note="Most recent 24 periods"):
        chart(_profit_chart(report), 320, key=f"chart-report-{period.lower()}")

    with card(f"{period} Breakdown", key=f"report-table-{period.lower()}",
              note=f"{len(report)} periods"):
        st.dataframe(
            report.sort_values("period", ascending=False),
            column_config=REPORT_COLUMNS,
            width="stretch",
            hide_index=True,
            height=320,
        )
        st.download_button(
            f"Export {period.lower()} report as CSV",
            data=report.to_csv(index=False).encode("utf-8"),
            file_name=f"{period.lower()}-report.csv",
            mime="text/csv",
            width="stretch",
            key=f"export-{period.lower()}-csv",
            on_click=record_export,
            args=(user.actor, f"{period} CSV report downloaded."),
        )


def _render_excel(user: AuthenticatedUser) -> None:
    section_head("Workbook Status")
    sync_status_panel()

    with card("Generate and Download", key="report-excel-actions"):
        action_columns = st.columns(2)
        with action_columns[0]:
            if st.button(
                "Rebuild workbook from database", type="primary", width="stretch",
                key="rebuild-workbook",
            ):
                try:
                    with st.spinner("Rebuilding workbook..."):
                        workbook_path = sync_factory_workbook()
                    _mark_sync_success(workbook_path, user, "Full Excel workbook generated from SQLite.")
                    st.success(f"Spreadsheet updated: {workbook_path.name}", icon="✅")
                except Exception as exc:
                    _mark_sync_failure(exc)
        with action_columns[1]:
            if WORKBOOK_PATH.exists():
                _download_excel(
                    "Download latest workbook", WORKBOOK_PATH, "download-latest-excel", user,
                    "Latest factory Excel workbook downloaded.",
                )
            else:
                st.info("Generate the workbook before downloading it.", icon="ℹ")

    with card("Monthly Export", key="report-excel-month"):
        months = _available_months()
        month_col, button_col = st.columns([2, 1])
        with month_col:
            selected_month = st.selectbox(
                "Month", months if months else ["No records available"], disabled=not months,
                label_visibility="collapsed",
            )
        with button_col:
            export_clicked = st.button(
                "Export month", disabled=not months, width="stretch", key="export-month",
            )
        if export_clicked:
            try:
                with st.spinner(f"Building {selected_month} workbook..."):
                    month_path = export_month_workbook(selected_month)
                record_export(user.actor, f"Selected-month Excel export generated for {selected_month}.")
                st.session_state["month_export_path"] = str(month_path)
                st.success(f"Monthly Excel exported: {month_path.name}", icon="✅")
            except Exception as exc:
                show_factory_error(exc)

        month_export = Path(st.session_state.get("month_export_path", ""))
        if str(month_export) not in {"", "."} and month_export.exists():
            _download_excel(
                f"Download {month_export.name}", month_export, "download-month-excel", user,
                f"Monthly Excel workbook downloaded: {month_export.name}.",
            )

    if can(user, "manage_backups"):
        with card("Workbook Backup", key="report-excel-backup"):
            if st.button("Back up Excel file", width="stretch", key="backup-excel"):
                try:
                    backup_path = backup_excel(user.actor)
                    st.session_state["excel_backup_path"] = str(backup_path)
                    st.success(f"Backup created: {backup_path.name}", icon="✅")
                except FactoryError as exc:
                    show_factory_error(exc)
            backup_path = Path(st.session_state.get("excel_backup_path", ""))
            if str(backup_path) not in {"", "."} and backup_path.exists():
                _download_excel(
                    "Download backup file", backup_path, "download-excel-backup", user,
                    f"Excel backup downloaded: {backup_path.name}.",
                )

    with card("How Excel Sync Works", key="report-excel-help"):
        st.markdown(
            """
            1. Every save commits to SQLite first, then rebuilds `factory_records.xlsx` on this computer.
            2. A failed workbook rebuild never rolls back a save - the status just shows **Out of date** or **Failed**.
            3. Download the workbook here, then upload or sync it to OneDrive to view it in Excel Web.
            4. Excel Web does not refresh on its own; Microsoft Graph integration is not part of this release.

            If a rebuild fails with a permission error, close `factory_records.xlsx` in desktop Excel
            and retry - Windows locks the file while it is open.
            """
        )


def _render_roadmap() -> None:
    with card("Prepared Architecture", key="report-roadmap"):
        st.markdown(
            """
            - **WhatsApp integration:** reserved for a future webhook intake module.
            - **AI message parsing:** reserved for validated production-entry drafts.
            - **PDF reports:** reserved for branded daily, weekly and monthly packets.
            """
        )
