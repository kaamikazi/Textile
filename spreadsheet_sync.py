from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill, Protection
from openpyxl.utils import get_column_letter


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "factory.db"
WORKBOOK_PATH = ROOT / "factory_records.xlsx"
EXPORTS_PATH = ROOT / "exports"
SHEET_PASSWORD = "alsadi"


def _read_sql(query: str, conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(query, conn)


def _currency(value: float) -> float:
    return round(float(value or 0), 2)


def build_summary(production: pd.DataFrame, expenses: pd.DataFrame, machines: pd.DataFrame) -> dict[str, pd.DataFrame]:
    total_earnings = _currency(production["total_amount"].sum()) if not production.empty else 0
    total_expenses = _currency(expenses["amount"].sum()) if not expenses.empty else 0
    profit = _currency(total_earnings - total_expenses)

    kpis = pd.DataFrame(
        [
            {"Metric": "Total Earnings", "Value": total_earnings},
            {"Metric": "Total Expenses", "Value": total_expenses},
            {"Metric": "Profit", "Value": profit},
            {"Metric": "Machines", "Value": len(machines)},
        ]
    )

    if production.empty:
        machine_wise = pd.DataFrame(columns=["Machine Number", "Total Quantity", "Total Earnings"])
        monthly_income = pd.DataFrame(columns=["Month", "Total Quantity", "Earnings"])
    else:
        machine_wise = (
            production.groupby("machine_number", as_index=False)
            .agg(total_quantity=("quantity", "sum"), total_earnings=("total_amount", "sum"))
            .rename(
                columns={
                    "machine_number": "Machine Number",
                    "total_quantity": "Total Quantity",
                    "total_earnings": "Total Earnings",
                }
            )
        )
        production_dates = pd.to_datetime(production["production_date"])
        monthly_income = (
            production.assign(month=production_dates.dt.to_period("M").astype(str))
            .groupby("month", as_index=False)
            .agg(total_quantity=("quantity", "sum"), earnings=("total_amount", "sum"))
            .rename(columns={"month": "Month", "total_quantity": "Total Quantity", "earnings": "Earnings"})
        )

    if expenses.empty:
        monthly_expenses = pd.DataFrame(columns=["Month", "Expenses"])
    else:
        expense_dates = pd.to_datetime(expenses["expense_date"])
        monthly_expenses = (
            expenses.assign(month=expense_dates.dt.to_period("M").astype(str))
            .groupby("month", as_index=False)
            .agg(expenses=("amount", "sum"))
            .rename(columns={"month": "Month", "expenses": "Expenses"})
        )

    monthly_totals = monthly_income.merge(monthly_expenses, on="Month", how="outer").fillna(0)
    if monthly_totals.empty:
        monthly_totals = pd.DataFrame(columns=["Month", "Total Quantity", "Earnings", "Expenses", "Profit"])
    else:
        monthly_totals["Profit"] = monthly_totals["Earnings"] - monthly_totals["Expenses"]
        monthly_totals = monthly_totals.sort_values("Month")

    return {
        "kpis": kpis,
        "machine_wise": machine_wise,
        "monthly_totals": monthly_totals,
    }


def _format_worksheet(writer: pd.ExcelWriter, sheet_name: str) -> None:
    ws = writer.book[sheet_name]
    header_fill = PatternFill("solid", fgColor="0B5D7A")
    header_font = Font(color="FFFFFF", bold=True)
    title_font = Font(color="0B5D7A", bold=True, size=13)

    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=False)

    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    if sheet_name == "Summary":
        for row in (1, 7, 14):
            for cell in ws[row]:
                if cell.value:
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = Alignment(horizontal="center", vertical="center")
        for cell_ref in ("A1", "A7", "A14"):
            ws[cell_ref].font = title_font

    ws.freeze_panes = "A2"
    ws.sheet_view.showGridLines = False
    if ws.max_row and ws.max_column:
        ws.auto_filter.ref = ws.dimensions
    for column_cells in ws.columns:
        letter = get_column_letter(column_cells[0].column)
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[letter].width = min(max(max_length + 3, 13), 34)


def _protect_worksheet(writer: pd.ExcelWriter, sheet_name: str) -> None:
    ws = writer.book[sheet_name]
    for row in ws.iter_rows():
        for cell in row:
            cell.protection = Protection(locked=True)

    if sheet_name in {"Production", "Expenses"}:
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.protection = Protection(locked=False)

    ws.protection.sheet = True
    ws.protection.password = SHEET_PASSWORD
    ws.protection.selectLockedCells = False
    ws.protection.selectUnlockedCells = True
    ws.protection.formatCells = False
    ws.protection.formatColumns = False
    ws.protection.formatRows = False
    ws.protection.insertRows = False
    ws.protection.deleteRows = False
    ws.protection.sort = False
    ws.protection.autoFilter = False


def _write_factory_workbook(
    path: Path,
    production: pd.DataFrame,
    expenses: pd.DataFrame,
    employees: pd.DataFrame,
    machines: pd.DataFrame,
) -> Path:
    summary = build_summary(production, expenses, machines)

    with pd.ExcelWriter(path, engine="openpyxl", mode="w") as writer:
        production.to_excel(writer, sheet_name="Production", index=False)
        expenses.to_excel(writer, sheet_name="Expenses", index=False)
        employees.to_excel(writer, sheet_name="Employees", index=False)
        machines.to_excel(writer, sheet_name="Machines", index=False)

        summary["kpis"].to_excel(writer, sheet_name="Summary", index=False, startrow=0)
        summary["machine_wise"].to_excel(writer, sheet_name="Summary", index=False, startrow=6)
        summary["monthly_totals"].to_excel(writer, sheet_name="Summary", index=False, startrow=13)

        for sheet_name in writer.book.sheetnames:
            _format_worksheet(writer, sheet_name)
            _protect_worksheet(writer, sheet_name)

    return path


def sync_factory_workbook(path: Path = WORKBOOK_PATH) -> Path:
    with sqlite3.connect(DB_PATH) as conn:
        production = _read_sql("SELECT * FROM production_entries ORDER BY production_date, id", conn)
        expenses = _read_sql("SELECT * FROM expenses ORDER BY expense_date, id", conn)
        employees = _read_sql("SELECT * FROM employees ORDER BY name, id", conn)
        machines = _read_sql("SELECT * FROM machines ORDER BY machine_number, id", conn)

    return _write_factory_workbook(path, production, expenses, employees, machines)


def export_month_workbook(month: str) -> Path:
    EXPORTS_PATH.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        production = _read_sql("SELECT * FROM production_entries ORDER BY production_date, id", conn)
        expenses = _read_sql("SELECT * FROM expenses ORDER BY expense_date, id", conn)
        employees = _read_sql("SELECT * FROM employees ORDER BY name, id", conn)
        machines = _read_sql("SELECT * FROM machines ORDER BY machine_number, id", conn)

    if not production.empty:
        production = production[pd.to_datetime(production["production_date"]).dt.to_period("M").astype(str) == month]
    if not expenses.empty:
        expenses = expenses[pd.to_datetime(expenses["expense_date"]).dt.to_period("M").astype(str) == month]

    export_path = EXPORTS_PATH / f"factory_records_{month}.xlsx"
    return _write_factory_workbook(export_path, production, expenses, employees, machines)


def backup_excel_file(source_file: str = "factory_records.xlsx") -> tuple[str | None, str]:
    source_path = Path(source_file)
    if not source_path.is_absolute():
        source_path = ROOT / source_path

    if not os.path.exists(source_path):
        return None, "Excel file not found."

    backup_dir = ROOT / "backups"
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    backup_name = f"factory_records_backup_{timestamp}.xlsx"
    backup_path = backup_dir / backup_name

    suffix = 1
    while backup_path.exists():
        backup_path = backup_dir / f"factory_records_backup_{timestamp}_{suffix}.xlsx"
        suffix += 1

    shutil.copy2(source_path, backup_path)

    return str(backup_path), "Backup created successfully."
