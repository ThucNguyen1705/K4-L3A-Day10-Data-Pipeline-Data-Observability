from __future__ import annotations

import pytest

from core.utils import read_json
from ingestion.cleaning import refresh_derived_columns
from ingestion.corruption import corrupt_clean_dataframe
from observability.quality import MAX_STALE_RATIO, build_freshness_report, run_data_quality_checks

MANDATORY = {
    "row_count_between_5_and_5000",
    "paper_id_not_null",
    "title_not_null",
    "text_for_embedding_not_null",
    "paper_id_unique",
    "summary_length_at_least_30",
}


def test_clean_data_passes_gx_gate(clean_df, settings):
    report = run_data_quality_checks(clean_df, settings, "baseline")
    assert report["success"] is True
    assert report["engine"].startswith("great_expectations 1.")
    assert report["context_mode"] == "ephemeral"
    assert {check["name"] for check in report["checks"] if check["mandatory"]} == MANDATORY
    assert all(check["error"] is None for check in report["checks"])
    assert read_json(settings.paths.baseline_quality_report)["success"] is True
    assert (settings.paths.gx_dir / "baseline_validation_result.json").exists()


@pytest.mark.parametrize(
    ("mutate", "failed_check"),
    [
        (lambda df: df.assign(summary=["short"] + list(df["summary"][1:])), "summary_length_at_least_30"),
        (lambda df: df.assign(title=["Tiny"] + list(df["title"][1:])), "title_length_at_least_8"),
        (lambda df: df.assign(summary=[df["summary"][0] + " " + chr(0xFFFD) * 3] + list(df["summary"][1:])), "summary_free_of_encoding_noise"),
        (lambda df: df.assign(published=["26/09/2026"] + list(df["published"][1:])), "published_is_iso_date"),
        (lambda df: df.assign(paper_id=[None] + list(df["paper_id"][1:])), "paper_id_not_null"),
        (lambda df: df.head(4), "row_count_between_5_and_5000"),
    ],
)
def test_each_expectation_catches_its_failure(clean_df, settings, mutate, failed_check):
    report = run_data_quality_checks(mutate(clean_df), settings, "scenario")
    assert report["success"] is False
    assert failed_check in report["failed_checks"]


def test_corrupted_data_is_blocked(clean_df, settings, tmp_path):
    corrupted = corrupt_clean_dataframe(clean_df, tmp_path / "log.json")
    report = run_data_quality_checks(corrupted, settings, "corrupted")
    assert report["success"] is False
    assert set(report["failed_checks"]) == {
        "paper_id_unique",
        "summary_length_at_least_30",
        "title_length_at_least_8",
        "summary_free_of_encoding_noise",
        "unique_papers_at_least_90%_of_raw",
    }
    assert report["freshness"]["is_fresh"] is False


def test_freshness_sla_threshold(clean_df, settings):
    fresh = build_freshness_report(clean_df, settings, settings.paths.freshness_report)
    assert fresh["is_fresh"] is True
    assert fresh["stale_rows"] == 1
    assert fresh["latest_published"] == "2026-07-22" and fresh["oldest_published"] == "2026-03-28"

    stale_count = int(len(clean_df) * MAX_STALE_RATIO) + 1
    aged = clean_df.copy()
    aged.loc[: stale_count - 1, "age_days"] = 400
    stale = build_freshness_report(refresh_derived_columns(aged), settings, settings.paths.quality_dir / "stale.json")
    assert stale["stale_ratio"] > MAX_STALE_RATIO
    assert stale["is_fresh"] is False and stale["status"] == "stale"
