from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from database import fetch_df
from spreadsheet_sync import WORKBOOK_PATH, backup_excel_file, export_month_workbook, sync_factory_workbook
from ui import glass_close, glass_open, money, page_header, plotly_layout


def _report_frame(period: str) -> pd.DataFrame:
    production = fetch_df("SELECT production_date, quantity, total_amount FROM production_entries")
    expenses = fetch_df("SELECT expense_date, amount FROM expenses")
    production["date"] = pd.to_datetime(production["production_date"])
    expenses["date"] = pd.to_datetime(expenses["expense_date"])

    freq = {"Daily": "D", "Weekly": "W", "Monthly": "M"}[period]
    income = production.groupby(production["date"].dt.to_period(freq)).agg(
        quantity=("quantity", "sum"),
        earnings=("total_amount", "sum"),
    )
    spend = expenses.groupby(expenses["date"].dt.to_period(freq)).agg(expenses=("amount", "sum"))
    report = income.join(spend, how="outer").fillna(0).reset_index()
    report["period"] = report["date"].astype(str)
    report["profit"] = report["earnings"] - report["expenses"]
    return report[["period", "quantity", "earnings", "expenses", "profit"]]


def _available_months() -> list[str]:
    production = fetch_df("SELECT production_date AS entry_date FROM production_entries")
    expenses = fetch_df("SELECT expense_date AS entry_date FROM expenses")
    dates = pd.concat([production, expenses], ignore_index=True)
    if dates.empty:
        return []
    months = pd.to_datetime(dates["entry_date"]).dt.to_period("M").astype(str)
    return sorted(months.dropna().unique().tolist(), reverse=True)


def render() -> None:
    page_header("Reports", "Daily, weekly, and monthly reporting with CSV export.", "Export Ready")

    glass_open("Excel Workflow")
    st.markdown(
        """
        - The app updates the local `factory_records.xlsx` file on this computer.
        - You can download or export the latest Excel file from here.
        - To view it in Excel Web, upload or sync the file to OneDrive.
        - Excel Web will not update automatically unless this app is connected through OneDrive/Microsoft Graph API.
        """
    )

    if st.button("Export / Update Spreadsheet", type="primary"):
        try:
            workbook_path = sync_factory_workbook()
            st.success(f"Spreadsheet updated: {workbook_path.name}")
        except PermissionError:
            st.error("Close factory_records.xlsx in Excel, then try again.")

    if WORKBOOK_PATH.exists():
        st.download_button(
            "Download latest Excel",
            WORKBOOK_PATH.read_bytes(),
            file_name="factory_records.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.info("No Excel export exists yet. Use Export / Update Spreadsheet first.")

    months = _available_months()
    selected_month = st.selectbox(
        "Selected Month",
        months if months else ["No records available"],
        disabled=not months,
    )
    if st.button("Export selected month", disabled=not months):
        month_path = export_month_workbook(selected_month)
        st.success(f"Monthly Excel exported: {month_path.name}")
        st.download_button(
            f"Download {month_path.name}",
            month_path.read_bytes(),
            file_name=month_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    if st.button("Backup Excel"):
        backup_path, message = backup_excel_file()
        if backup_path is None:
            st.error(message)
        else:
            st.success(message)
            backup_file_path = Path(backup_path)
            with open(backup_file_path, "rb") as backup_file:
                st.download_button(
                    "Download Backup File",
                    backup_file.read(),
                    file_name=backup_file_path.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
    glass_close()

    daily_tab, weekly_tab, monthly_tab, future_tab = st.tabs(["Daily", "Weekly", "Monthly", "Future Modules"])

    for tab, period in [(daily_tab, "Daily"), (weekly_tab, "Weekly"), (monthly_tab, "Monthly")]:
        with tab:
            report = _report_frame(period)
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("Earnings", money(report["earnings"].sum()))
            with c2:
                st.metric("Expenses", money(report["expenses"].sum()))
            with c3:
                st.metric("Profit", money(report["profit"].sum()))

            glass_open(f"{period} Profit Trend")
            fig = px.bar(report, x="period", y=["earnings", "expenses", "profit"], barmode="group", color_discrete_sequence=["#18d7ff", "#ff637d", "#38e6a1"])
            st.plotly_chart(plotly_layout(fig, 360), use_container_width=True)
            glass_close()

            glass_open(f"{period} Report Table")
            st.dataframe(report.sort_values("period", ascending=False), use_container_width=True, hide_index=True)
            st.download_button(
                f"Export {period} CSV",
                report.to_csv(index=False).encode("utf-8"),
                f"{period.lower()}-report.csv",
                "text/csv",
            )
            glass_close()

    with future_tab:
        glass_open("Prepared Architecture")
        st.markdown(
            """
            - **WhatsApp message integration:** add webhook intake and map sender/order metadata into production drafts.
            - **AI parsing of production messages:** parse operator messages into validated production records before saving.
            - **PDF report generation:** render report data into branded daily, weekly, and monthly PDF packets.
            """
        )
        glass_close()
