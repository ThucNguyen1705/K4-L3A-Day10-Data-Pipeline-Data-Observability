from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("GX_ANALYTICS_ENABLED", "False")

import great_expectations as gx  # noqa: E402
import great_expectations.expectations as gxe  # noqa: E402
import pandas as pd  # noqa: E402

from core.config import Settings  # noqa: E402
from core.utils import now_utc, read_json, safe_slug, write_json  # noqa: E402

MIN_ROWS = 5
MAX_ROWS = 5000
MIN_SUMMARY_CHARS = 30
MIN_TITLE_CHARS = 8
MAX_STALE_RATIO = 0.25
MIN_LINEAGE_COMPLETENESS = 0.90
# U+FFFD replacement chars, UTF-8-read-as-cp1252 mojibake (e.g. "A-tilde + copyright",
# "a-circumflex + euro") and bursts of 3+ symbols.
# Built with chr() so the pattern holds literal characters: pandas' pyarrow backend uses RE2,
# which rejects Python-only "\uXXXX" escapes.
NOISE_REGEX = "|".join(
    [
        chr(0xFFFD),
        r"[#@$%^&*~|<>{}\\]{3,}",
        chr(0xC3) + "[" + chr(0xA0) + "-" + chr(0xBF) + "]",
        chr(0xE2) + chr(0x20AC),
    ]
)


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _relative(path: Path, settings: Settings) -> str:
    try:
        return path.resolve().relative_to(settings.paths.project_dir).as_posix()
    except ValueError:
        return str(path)


def _quality_report_path(settings: Settings, report_name: str) -> Path:
    known = {
        "baseline": settings.paths.baseline_quality_report,
        "corrupted": settings.paths.corrupted_quality_report,
    }
    return known.get(report_name, settings.paths.quality_dir / f"{safe_slug(report_name)}_quality_report.json")


def _lineage_record_count(settings: Settings) -> int | None:
    """Number of records in the raw snapshot — the reference for completeness checks."""
    try:
        return len({row["paper_id"] for row in read_json(settings.paths.raw_records_json)})
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _expectation_specs(lineage_count: int | None) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        {
            "name": "row_count_between_5_and_5000",
            "dimension": "volume",
            "mandatory": True,
            "expectation": gxe.ExpectTableRowCountToBeBetween(min_value=MIN_ROWS, max_value=MAX_ROWS),
        },
    ]
    for column in ("paper_id", "title", "text_for_embedding"):
        specs.append(
            {
                "name": f"{column}_not_null",
                "dimension": "completeness",
                "mandatory": True,
                "expectation": gxe.ExpectColumnValuesToNotBeNull(column=column),
            }
        )
    specs += [
        {
            "name": "paper_id_unique",
            "dimension": "uniqueness",
            "mandatory": True,
            "expectation": gxe.ExpectColumnValuesToBeUnique(column="paper_id"),
        },
        {
            "name": f"summary_length_at_least_{MIN_SUMMARY_CHARS}",
            "dimension": "validity",
            "mandatory": True,
            "expectation": gxe.ExpectColumnValueLengthsToBeBetween(column="summary", min_value=MIN_SUMMARY_CHARS),
        },
        {
            "name": f"title_length_at_least_{MIN_TITLE_CHARS}",
            "dimension": "validity",
            "mandatory": False,
            "expectation": gxe.ExpectColumnValueLengthsToBeBetween(column="title", min_value=MIN_TITLE_CHARS),
        },
        {
            "name": "summary_free_of_encoding_noise",
            "dimension": "accuracy",
            "mandatory": False,
            "expectation": gxe.ExpectColumnValuesToNotMatchRegex(column="summary", regex=NOISE_REGEX),
        },
        {
            "name": "published_is_iso_date",
            "dimension": "validity",
            "mandatory": False,
            "expectation": gxe.ExpectColumnValuesToMatchStrftimeFormat(column="published", strftime_format="%Y-%m-%d"),
        },
    ]
    if lineage_count:
        min_unique = math.ceil(MIN_LINEAGE_COMPLETENESS * lineage_count)
        specs.append(
            {
                "name": f"unique_papers_at_least_{MIN_LINEAGE_COMPLETENESS:.0%}_of_raw",
                "dimension": "completeness",
                "mandatory": False,
                "expectation": gxe.ExpectColumnUniqueValueCountToBeBetween(column="paper_id", min_value=min_unique),
            }
        )
    return specs


def _freshness_summary(df: pd.DataFrame, settings: Settings) -> dict[str, Any]:
    total = len(df)
    ages = pd.to_numeric(df["age_days"], errors="coerce") if total else pd.Series(dtype=float)
    stale_rows = int((ages > settings.freshness_threshold_days).sum())
    stale_ratio = stale_rows / total if total else 1.0
    published = pd.to_datetime(df["published"], errors="coerce") if total else pd.Series(dtype="datetime64[ns]")
    latest, oldest = published.max(), published.min()
    is_fresh = bool(total) and stale_ratio <= MAX_STALE_RATIO
    return {
        "threshold_days": settings.freshness_threshold_days,
        "max_stale_ratio": MAX_STALE_RATIO,
        "total_rows": total,
        "stale_rows": stale_rows,
        "stale_ratio": round(stale_ratio, 4),
        "latest_published": None if pd.isna(latest) else latest.strftime("%Y-%m-%d"),
        "oldest_published": None if pd.isna(oldest) else oldest.strftime("%Y-%m-%d"),
        "min_age_days": None if ages.empty else int(ages.min()),
        "median_age_days": None if ages.empty else float(ages.median()),
        "max_age_days": None if ages.empty else int(ages.max()),
        "is_fresh": is_fresh,
        "status": "fresh" if is_fresh else "stale",
        "message": (
            f"{stale_rows}/{total} rows ({stale_ratio:.1%}) are older than {settings.freshness_threshold_days} days; "
            f"SLA allows at most {MAX_STALE_RATIO:.0%}."
        ),
    }


def _disable_progress_bars(context: Any) -> None:
    try:
        from great_expectations.data_context.types.base import ProgressBarsConfig

        context.variables.progress_bars = ProgressBarsConfig(globally=False)
    except Exception:  # pragma: no cover - cosmetic only
        pass


def run_data_quality_checks(df: pd.DataFrame, settings: Settings, report_name: str) -> dict[str, Any]:
    """Validate a clean/corrupted/repaired dataframe with Great Expectations 1.x.

    Uses the GX 1.x fluent API on an ephemeral (in-memory) context. `success` is the
    blocking gate verdict (every expectation must pass); freshness is attached as a
    separate, non-blocking SLA signal. Writes `data/quality/<name>_quality_report.json`
    plus the raw GX validation result under `data/quality/gx/`.
    """
    context = gx.get_context(mode="ephemeral")
    _disable_progress_bars(context)
    data_source = context.data_sources.add_pandas(name="papers_source")
    data_asset = data_source.add_dataframe_asset(name="papers_asset")
    batch_def = data_asset.add_batch_definition_whole_dataframe("papers_batch")
    batch = batch_def.get_batch(batch_parameters={"dataframe": df})

    specs = _expectation_specs(_lineage_record_count(settings))
    suite = context.suites.add(gx.ExpectationSuite(name=f"papers_suite_{safe_slug(report_name)}"))
    for spec in specs:
        spec["expectation"].meta = {"check_name": spec["name"]}
        suite.add_expectation(spec["expectation"])
    validation = batch.validate(suite)

    # GX does not return results in insertion order, so match them back through `meta`.
    results_by_name = {result.expectation_config.meta["check_name"]: result for result in validation.results}
    checks = []
    for spec in specs:
        result = results_by_name[spec["name"]]
        details = result.result or {}
        errors = [info for info in (result.exception_info or {}).values() if isinstance(info, dict)]
        error = next((info.get("exception_message") for info in errors if info.get("raised_exception")), None)
        checks.append(
            {
                "name": spec["name"],
                "expectation": result.expectation_config.type,
                "column": result.expectation_config.kwargs.get("column"),
                "dimension": spec["dimension"],
                "mandatory": spec["mandatory"],
                "success": bool(result.success),
                "observed_value": details.get("observed_value"),
                "unexpected_count": details.get("unexpected_count"),
                "unexpected_percent": details.get("unexpected_percent"),
                "sample_unexpected": (details.get("partial_unexpected_list") or [])[:5],
                "error": error,
            }
        )

    gx_result_path = settings.paths.gx_dir / f"{safe_slug(report_name)}_validation_result.json"
    write_json(gx_result_path, _jsonable(validation.to_json_dict()))

    failed = [check["name"] for check in checks if not check["success"]]
    report = _jsonable(
        {
            "report_name": report_name,
            "generated_at": now_utc().isoformat(),
            "engine": f"great_expectations {gx.__version__}",
            "context_mode": "ephemeral",
            "rows": len(df),
            "unique_paper_ids": int(df["paper_id"].nunique()) if "paper_id" in df else 0,
            "success": bool(validation.success),
            "statistics": {
                "evaluated": len(checks),
                "successful": len(checks) - len(failed),
                "unsuccessful": len(failed),
            },
            "failed_checks": failed,
            "checks": checks,
            "freshness": _freshness_summary(df, settings),
            "gx_validation_result": _relative(gx_result_path, settings),
        }
    )
    write_json(_quality_report_path(settings, report_name), report)
    return report


def build_freshness_report(df: pd.DataFrame, settings: Settings, report_path) -> dict[str, Any]:
    """Freshness SLA: flag `is_fresh=False` when > 25% of rows are older than 180 days."""
    payload = {"generated_at": now_utc().isoformat(), **_freshness_summary(df, settings)}
    write_json(Path(report_path), payload)
    return payload
