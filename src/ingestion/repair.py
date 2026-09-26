from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from typing import Any

import pandas as pd

from core.config import Settings
from core.utils import read_json, write_json
from ingestion.cleaning import build_clean_dataframe, dataframe_to_records
from ingestion.crossref import fetch_source_records, load_raw_records, parse_crossref_payload


def dataframe_fingerprint(df: pd.DataFrame, exclude: tuple[str, ...] = ()) -> str:
    """Order-sensitive SHA-256 of the dataframe content (used to prove idempotency)."""
    columns = [column for column in df.columns if column not in exclude]
    payload = json.dumps(dataframe_to_records(df[columns]), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_trusted_records(settings: Settings) -> tuple[list, str]:
    """Pick the most trustworthy raw source that is still available.

    1. `crossref_records.json` if it parses and matches the untouched API response.
    2. Re-parse `crossref_response.json` (restoring the records file) if they diverge.
    3. Re-fetch from the source as a last resort.
    """
    paths = settings.paths
    records = load_raw_records(paths.raw_records_json) if paths.raw_records_json.exists() else []
    if paths.raw_api_response.exists():
        reparsed = parse_crossref_payload(read_json(paths.raw_api_response))
        if reparsed and reparsed != records:
            write_json(paths.raw_records_json, [asdict(record) for record in reparsed])
            return reparsed, "raw_api_response (records snapshot restored)"
    if records:
        return records, "raw_records_json"
    return fetch_source_records(settings), "source re-fetch"


def repair_from_raw(settings: Settings, run_date: datetime) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Rebuild the clean dataset from raw lineage, never from the corrupted copy.

    The rebuild is a pure function of the raw snapshot and `run_date`, so running it any
    number of times yields the same dataframe (verified with a fingerprint on a second run).
    """
    records, source = _load_trusted_records(settings)
    repaired = build_clean_dataframe(records, run_date)
    second_pass = build_clean_dataframe(load_raw_records(settings.paths.raw_records_json), run_date)
    first_hash = dataframe_fingerprint(repaired)
    second_hash = dataframe_fingerprint(second_pass)
    info: dict[str, Any] = {
        "repair_source": source,
        "raw_records": len(records),
        "repaired_rows": len(repaired),
        "fingerprint_run_1": first_hash,
        "fingerprint_run_2": second_hash,
        "idempotent": first_hash == second_hash,
    }
    if settings.paths.clean_json.exists():
        baseline = pd.DataFrame(read_json(settings.paths.clean_json))
        # age_days depends on the run date, so compare the run-independent content only.
        info["matches_baseline_content"] = dataframe_fingerprint(baseline, exclude=("age_days",)) == dataframe_fingerprint(
            repaired, exclude=("age_days",)
        )
    return repaired, info
