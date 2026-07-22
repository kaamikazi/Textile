from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from auth import AuthenticatedUser, can
from data_safety import backup_excel, record_export
from database import FactoryError, fetch_df, set_excel_sync_status
from spreadsheet_sync import WORKBOOK_PATH, export_month_workbook, sync_factory_workbook
from ui import glass_close, glass_open, money, page_header, plotly_layout, show_factory_error, sync_status_panel


EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
    st.error(message)


def _download_excel(
    label: str, path: Path, key: str, user: AuthenticatedUser, description: str
) -> None:
    st.download_button(
        label, data=path.read_bytes(), file_name=path.name, mime=EXCEL_MIME,
        key=key, width="stretch", on_click=record_export,
        args=(user.actor, description),
    )


def render(user: AuthenticatedUser) -> None:
    page_header("Reports", "Daily, weekly, and monthly reporting with controlled exports.", "Export Ready")

    glass_open("Excel Workflow")
    st.markdown(
        """
        1. The app updates the local `factory_records.xlsx` file on this computer.
        2. Download or export the latest workbook from this page.
        3. Upload or sync that file to OneDrive to view it in Excel Web.
        4. Excel Web does not update automatically without a OneDrive/Microsoft Graph API connection.

        Microsoft Graph integration is intentionally not enabled in this release.
        """
    )
    sync_status_panel()

    action_columns = st.columns(2)
    with action_columns[0]:
        if st.button("Export / Update Spreadsheet", type="primary", width="stretch"):
            try:
                workbook_path = sync_factory_workbook()
                _mark_sync_success(workbook_path, user, "Full Excel workbook generated from SQLite.")
                st.success(f"Spreadsheet updated: {workbook_path.name}")
            except Exception as exc:
                _mark_sync_failure(exc)
    with action_columns[1]:
        if WORKBOOK_PATH.exists():
            _download_excel(
                "Download Latest Excel", WORKBOOK_PATH, "download-latest-excel", user,
                "Latest factory Excel workbook downloaded.",
            )
        else:
            st.info("Generate the workbook before downloading it.")

    months = _available_months()
    selected_month = st.selectbox(
        "Selected Month", months if months else ["No records available"], disabled=not months
    )
    if st.button("Export Selected Month", disabled=not months, width="stretch"):
        try:
            month_path = export_month_workbook(selected_month)
            record_export(user.actor, f"Selected-month Excel export generated for {selected_month}.")
            st.session_state["month_export_path"] = str(month_path)
            st.success(f"Monthly Excel exported: {month_path.name}")
        except Exception as exc:
            show_factory_error(exc)
    month_export = Path(st.session_state.get("month_export_path", ""))
    if str(month_export) not in {"", "."} and month_export.exists():
        _download_excel(
            f"Download {month_export.name}", month_export, "download-month-excel", user,
            f"Monthly Excel workbook downloaded: {month_export.name}.",
        )

    if can(user, "manage_backups"):
        if st.button("Backup Excel File", width="stretch"):
            try:
                backup_path = backup_excel(user.actor)
                st.session_state["excel_backup_path"] = str(backup_path)
                st.success(f"Backup created successfully: {backup_path.name}")
            except FactoryError as exc:
                show_factory_error(exc)
        backup_path = Path(st.session_state.get("excel_backup_path", ""))
        if str(backup_path) not in {"", "."} and backup_path.exists():
            _download_excel(
                "Download Backup File", backup_path, "download-excel-backup", user,
                f"Excel backup downloaded: {backup_path.name}.",
            )
    glass_close()

    daily_tab, weekly_tab, monthly_tab, future_tab = st.tabs(
        ["Daily", "Weekly", "Monthly", "Future Modules"]
    )
    for tab, period in [(daily_tab, "Daily"), (weekly_tab, "Weekly"), (monthly_tab, "Monthly")]:
        with tab:
            report = _report_frame(period)
            c1, c2, c3 = st.columns(3)
            c1.metric("Earnings", money(float(report["earnings"].sum())))
            c2.metric("Expenses", money(float(report["expenses"].sum())))
            c3.metric("Profit", money(float(report["profit"].sum())))

            glass_open(f"{period} Profit Trend")
            if report.empty:
                st.info(f"No {period.lower()} records are available yet.")
            else:
                fig = px.bar(
                    report, x="period", y=["earnings", "expenses", "profit"], barmode="group",
                    color_discrete_sequence=["#18d7ff", "#ff637d", "#38e6a1"],
                )
                st.plotly_chart(plotly_layout(fig, 360), width="stretch")
            glass_close()

            glass_open(f"{period} Report Table")
            st.dataframe(report.sort_values("period", ascending=False), width="stretch", hide_index=True)
            st.download_button(
                f"Export {period} CSV", data=report.to_csv(index=False).encode("utf-8"),
                file_name=f"{period.lower()}-report.csv", mime="text/csv", width="stretch",
                on_click=record_export,
                args=(user.actor, f"{period} CSV report downloaded."),
            )
            glass_close()

    with future_tab:
        glass_open("Prepared Architecture")
        st.markdown(
            """
            - **WhatsApp integration:** reserved for a future webhook intake module.
            - **AI message parsing:** reserved for validated production-entry drafts.
            - **PDF reports:** reserved for branded daily, weekly, and monthly packets.
            """
        )
        glass_close()
