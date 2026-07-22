from __future__ import annotations

from pathlib import Path

import pytest

import database
import spreadsheet_sync
from database import Actor


@pytest.fixture
def actor() -> Actor:
    return Actor("test_admin", "Admin")


@pytest.fixture
def isolated_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    database_path = tmp_path / "factory_test.db"
    workbook_path = tmp_path / "factory_records_test.xlsx"
    monkeypatch.setattr(spreadsheet_sync, "WORKBOOK_PATH", workbook_path)
    database.initialize_database(database_path)
    return database_path, workbook_path
