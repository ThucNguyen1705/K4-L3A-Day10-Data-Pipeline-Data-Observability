from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import shutil

import pytest

from core.config import Settings, load_settings
from ingestion.cleaning import build_clean_dataframe
from ingestion.crossref import fetch_source_records

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = REPO_ROOT / "data" / "raw" / "crossref_response.json"
# Pinned so freshness assertions do not drift with the calendar (snapshot spans 2026-03-28..2026-07-22).
RUN_DATE = datetime(2026, 9, 26, tzinfo=UTC)


def make_project(root: Path) -> Path:
    """Isolated project layout containing only the raw Crossref snapshot."""
    raw_dir = root / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(SNAPSHOT, raw_dir / "crossref_response.json")
    return root


@pytest.fixture(autouse=True)
def offline_mock_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "mock-tool-calling")
    for name in ("REFRESH_SOURCE", "REFRESH_TEST_SET", "RUN_RAGAS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return load_settings(make_project(tmp_path / "project"))


@pytest.fixture
def records(settings):
    return fetch_source_records(settings)


@pytest.fixture
def clean_df(records):
    return build_clean_dataframe(records, RUN_DATE)
