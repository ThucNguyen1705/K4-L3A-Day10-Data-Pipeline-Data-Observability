from __future__ import annotations

from dataclasses import replace
import json

import pytest
import requests

from conftest import REPO_ROOT, RUN_DATE
from core.utils import read_json, write_json
from ingestion import crossref
from ingestion.cleaning import build_clean_dataframe, dataframe_for_csv, dataframe_from_records, dataframe_to_records
from ingestion.corruption import MAX_TRUNCATED_TITLE_CHARS, STALE_SHIFT_DAYS, corrupt_clean_dataframe
from ingestion.crossref import PaperRecord, fetch_source_records, load_raw_records, parse_crossref_payload
from ingestion.repair import dataframe_fingerprint, repair_from_raw


def _item(**overrides):
    item = {
        "DOI": "10.1000/ABC",
        "URL": "https://doi.org/10.1000/abc",
        "title": ["  A   Title  "],
        "abstract": "<jats:title>Abstract</jats:title><jats:p>First sentence &amp; more.  Second one.</jats:p>",
        "author": [{"given": "Ada", "family": "Lovelace"}, {"name": "Consortium X"}, {"given": "Ada", "family": "Lovelace"}],
        "subject": ["AI", "AI", "Databases"],
        "published": {"date-parts": [[2026, 5]]},
        "deposited": {"date-parts": [[2026, 6, 2]]},
        "link": [{"URL": "https://example.org/paper.pdf", "content-type": "application/pdf"}],
    }
    item.update(overrides)
    return item


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.url = "https://api.crossref.org/works?query=test"

    def json(self):
        return self._payload


def test_parse_crossref_payload_normalizes_fields():
    records = parse_crossref_payload({"message": {"items": [_item()]}})
    assert len(records) == 1
    record = records[0]
    assert record.paper_id == "10.1000/abc"
    assert record.title == "A Title"
    assert record.summary == "First sentence & more. Second one."
    assert record.authors == ["Ada Lovelace", "Consortium X"]
    assert record.categories == ["AI", "Databases"]
    assert record.primary_category == "AI"
    assert record.published == "2026-05-01"
    assert record.updated == "2026-06-02"
    assert record.pdf_url == "https://example.org/paper.pdf"


def test_parse_crossref_payload_drops_invalid_and_duplicate_items():
    payload = {
        "message": {
            "items": [
                _item(),
                _item(),  # duplicate DOI
                _item(DOI="10.1000/no-title", title=[]),
                _item(DOI=None),
                _item(DOI="10.1000/no-date", published=None, created=None),
                "not-a-dict",
            ]
        }
    }
    assert [record.paper_id for record in parse_crossref_payload(payload)] == ["10.1000/abc"]


def test_snapshot_parse_reproduces_committed_records(settings):
    records = fetch_source_records(settings)
    assert len(records) == 24
    assert crossref.LAST_FETCH_INFO["mode"] == "snapshot"
    committed = read_json(REPO_ROOT / "data" / "raw" / "crossref_records.json")
    assert read_json(settings.paths.raw_records_json) == committed
    assert load_raw_records(settings.paths.raw_records_json) == records


def test_live_fetch_retries_on_429_then_saves_raw(settings, monkeypatch):
    payload = read_json(settings.paths.raw_api_response)
    responses = [FakeResponse(429, headers={"Retry-After": "0"}), FakeResponse(200, payload)]
    monkeypatch.setattr(crossref.requests, "get", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(crossref.time, "sleep", lambda seconds: None)
    settings.paths.raw_api_response.unlink()

    records = fetch_source_records(replace(settings, refresh_source=True))

    assert len(records) == 24
    assert crossref.LAST_FETCH_INFO["mode"] == "live"
    assert crossref.LAST_FETCH_INFO["attempts"] == 2
    assert read_json(settings.paths.raw_api_response) == payload


def test_live_fetch_falls_back_to_snapshot(settings, monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(crossref.requests, "get", boom)
    monkeypatch.setattr(crossref.time, "sleep", lambda seconds: None)
    records = fetch_source_records(replace(settings, refresh_source=True))
    assert len(records) == 24
    assert crossref.LAST_FETCH_INFO["mode"] == "snapshot-fallback"


def test_live_fetch_without_snapshot_raises(settings, monkeypatch):
    monkeypatch.setattr(crossref.requests, "get", lambda *args, **kwargs: FakeResponse(404))
    settings.paths.raw_api_response.unlink()
    with pytest.raises(RuntimeError, match="no snapshot"):
        fetch_source_records(settings)


def test_build_clean_dataframe_schema_and_rules(clean_df):
    assert len(clean_df) == 24
    assert clean_df["paper_id"].is_unique
    assert list(clean_df["published"]) == sorted(clean_df["published"], reverse=True)
    oldest = clean_df.iloc[-1]
    assert oldest["published"] == "2026-03-28" and oldest["age_days"] == 182
    text = clean_df.iloc[0]["text_for_embedding"].splitlines()
    assert [line.split(":")[0] for line in text] == ["Title", "Authors", "Published", "Categories", "Summary"]
    assert clean_df.attrs["cleaning_stats"]["output_rows"] == 24


def test_build_clean_dataframe_dedup_and_filters():
    base = dict(
        title="Paper <b>One</b>",
        summary="A long enough abstract about retrieval systems.",
        authors=["A B"],
        categories=["IR"],
        primary_category="IR",
        published="2026-01-01",
        abs_url="u",
        pdf_url="u",
        comment="c",
    )
    records = [
        PaperRecord(paper_id="10.1/X", updated="2026-01-01", **base),
        PaperRecord(paper_id="10.1/x", updated="2026-02-01", **{**base, "title": "Paper One v2"}),
        PaperRecord(paper_id="10.1/y", updated="2026-01-01", **{**base, "summary": ""}),
        PaperRecord(paper_id="", updated="2026-01-01", **base),
        PaperRecord(paper_id="10.1/z", updated="2026-01-01", **{**base, "published": "not-a-date"}),
    ]
    df = build_clean_dataframe(records, RUN_DATE)
    assert df["paper_id"].tolist() == ["10.1/x"]
    assert df.iloc[0]["title"] == "Paper One v2"
    stats = df.attrs["cleaning_stats"]
    assert stats["dropped_duplicate_paper_id"] == 1
    assert stats["dropped_missing_summary"] == 1
    assert stats["dropped_missing_id_title_or_date"] == 2


def test_dataset_roundtrip_helpers(clean_df):
    restored = dataframe_from_records(json.loads(json.dumps(dataframe_to_records(clean_df))))
    assert dataframe_fingerprint(restored) == dataframe_fingerprint(clean_df)
    assert dataframe_for_csv(clean_df).iloc[0]["authors"] == "; ".join(clean_df.iloc[0]["authors"])


def test_corruption_applies_six_disjoint_scenarios(clean_df, tmp_path):
    log_path = tmp_path / "corruption_log.json"
    corrupted = corrupt_clean_dataframe(clean_df, str(log_path))
    log = read_json(log_path)
    by_type = {item["type"]: item for item in log["corruptions"]}

    assert list(by_type) == [
        "drop_latest_records",
        "blank_summary",
        "inject_noise",
        "truncate_title",
        "stale_date",
        "duplicate_rows",
    ]
    dropped = set(by_type["drop_latest_records"]["affected_paper_ids"])
    assert len(dropped) == 5 and dropped.isdisjoint(set(corrupted["paper_id"]))
    newest = set(clean_df.sort_values("published", ascending=False).head(4)["paper_id"])
    assert newest <= dropped

    in_place = [set(by_type[name]["affected_paper_ids"]) for name in ("blank_summary", "inject_noise", "truncate_title", "stale_date")]
    assert sum(len(ids) for ids in in_place) == len(set().union(*in_place))

    rows = corrupted.drop_duplicates("paper_id").set_index("paper_id")
    assert all(rows.loc[pid, "summary"] == "" for pid in by_type["blank_summary"]["affected_paper_ids"])
    assert all(len(rows.loc[pid, "title"]) <= MAX_TRUNCATED_TITLE_CHARS for pid in by_type["truncate_title"]["affected_paper_ids"])
    original = clean_df.set_index("paper_id")
    for pid in by_type["stale_date"]["affected_paper_ids"]:
        assert rows.loc[pid, "age_days"] == original.loc[pid, "age_days"] + STALE_SHIFT_DAYS
        assert f"Published: {rows.loc[pid, 'published']}" in rows.loc[pid, "text_for_embedding"]
    assert len(corrupted) == log["output_rows"] == 19 + by_type["duplicate_rows"]["affected_count"]
    assert not corrupted["paper_id"].is_unique


def test_corruption_is_deterministic(clean_df, tmp_path):
    first = corrupt_clean_dataframe(clean_df, tmp_path / "a.json")
    second = corrupt_clean_dataframe(clean_df, tmp_path / "b.json")
    assert dataframe_fingerprint(first) == dataframe_fingerprint(second)


def test_repair_is_idempotent_and_matches_baseline(settings, clean_df):
    write_json(settings.paths.clean_json, dataframe_to_records(clean_df))
    repaired, info = repair_from_raw(settings, RUN_DATE)
    assert info["idempotent"] and info["matches_baseline_content"]
    assert info["repair_source"] == "raw_records_json"
    assert dataframe_fingerprint(repaired) == dataframe_fingerprint(clean_df)


def test_repair_restores_tampered_records_from_raw_response(settings, records):
    tampered = read_json(settings.paths.raw_records_json)[:10]
    write_json(settings.paths.raw_records_json, tampered)
    repaired, info = repair_from_raw(settings, RUN_DATE)
    assert info["repair_source"].startswith("raw_api_response")
    assert len(repaired) == 24
    assert len(read_json(settings.paths.raw_records_json)) == 24
