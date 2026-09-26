from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Mapping

import pandas as pd

from ingestion.crossref import PaperRecord, clean_markup, normalize_doi

CLEAN_COLUMNS = [
    "paper_id",
    "title",
    "summary",
    "authors",
    "categories",
    "primary_category",
    "published",
    "updated",
    "abs_url",
    "pdf_url",
    "comment",
    "authors_joined",
    "categories_joined",
    "summary_chars",
    "age_days",
    "text_for_embedding",
]
LIST_COLUMNS = ("authors", "categories")


def _clean_list(values: Any) -> list[str]:
    items: list[str] = []
    for value in values if isinstance(values, (list, tuple)) else []:
        text = clean_markup(value)
        if text and text not in items:
            items.append(text)
    return items


def _to_iso_date(value: Any) -> str:
    parsed = pd.to_datetime(str(value or ""), errors="coerce", utc=True)
    return "" if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def build_text_for_embedding(row: Mapping[str, Any]) -> str:
    """Five-part document used both for embeddings and as retrieval context."""
    return "\n".join(
        [
            f"Title: {row['title']}",
            f"Authors: {row['authors_joined']}",
            f"Published: {row['published']}",
            f"Categories: {row['categories_joined']}",
            f"Summary: {row['summary']}",
        ]
    )


def refresh_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Recompute helper columns after `summary`/`title`/`published` change (used by corruption too)."""
    df = df.copy()
    df["authors_joined"] = df["authors"].map(lambda items: ", ".join(items))
    df["categories_joined"] = df["categories"].map(lambda items: ", ".join(items))
    df["summary_chars"] = df["summary"].map(len).astype(int)
    df["text_for_embedding"] = [build_text_for_embedding(row) for row in df.to_dict(orient="records")]
    return df


def build_clean_dataframe(records: list[PaperRecord], run_date: datetime) -> pd.DataFrame:
    """Turn raw `PaperRecord`s into a deduplicated dataframe ready for embedding.

    Rules (counts are exposed in `df.attrs["cleaning_stats"]`):
    1. Strip JATS/HTML markup and extra whitespace from text fields; normalize DOI casing.
    2. Parse `published`/`updated` to ISO dates; drop rows without id, title or valid date.
    3. Drop rows without an abstract (nothing meaningful to embed).
    4. Deduplicate on `paper_id`, keeping the most recently updated version.
    5. Compute `age_days = (run_date - published).days` and the `text_for_embedding` document.
    6. Sort by newest publication first, then `paper_id`, for deterministic output.
    """
    rows = []
    for record in records:
        row = asdict(record) if isinstance(record, PaperRecord) else dict(record)
        authors = _clean_list(row.get("authors"))
        categories = _clean_list(row.get("categories"))
        published = _to_iso_date(row.get("published"))
        rows.append(
            {
                "paper_id": normalize_doi(row.get("paper_id")),
                "title": clean_markup(row.get("title")),
                "summary": clean_markup(row.get("summary")),
                "authors": authors,
                "categories": categories,
                "primary_category": clean_markup(row.get("primary_category")) or (categories[0] if categories else ""),
                "published": published,
                "updated": _to_iso_date(row.get("updated")) or published,
                "abs_url": str(row.get("abs_url") or "").strip(),
                "pdf_url": str(row.get("pdf_url") or row.get("abs_url") or "").strip(),
                "comment": clean_markup(row.get("comment")),
            }
        )

    df = pd.DataFrame(rows, columns=CLEAN_COLUMNS[:11])
    stats: dict[str, int] = {"input_records": len(df)}

    valid_identity = (df["paper_id"] != "") & (df["title"] != "") & (df["published"] != "")
    stats["dropped_missing_id_title_or_date"] = int((~valid_identity).sum())
    df = df[valid_identity]

    has_summary = df["summary"] != ""
    stats["dropped_missing_summary"] = int((~has_summary).sum())
    df = df[has_summary]

    before_dedup = len(df)
    df = df.sort_values(["updated", "paper_id"], ascending=[False, True]).drop_duplicates("paper_id", keep="first")
    stats["dropped_duplicate_paper_id"] = before_dedup - len(df)

    run_day = pd.Timestamp(run_date.date())
    df = df.assign(age_days=(run_day - pd.to_datetime(df["published"])).dt.days.astype(int))
    df = refresh_derived_columns(df)
    df = df.sort_values(["published", "paper_id"], ascending=[False, True]).reset_index(drop=True)[CLEAN_COLUMNS]

    stats["output_rows"] = len(df)
    df.attrs["cleaning_stats"] = stats
    df.attrs["run_date"] = run_day.strftime("%Y-%m-%d")
    return df


def dataframe_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """JSON-safe list of rows (native Python types, lists kept as lists)."""
    records = df.to_dict(orient="records")
    for record in records:
        for key, value in record.items():
            if hasattr(value, "item") and not isinstance(value, (list, str)):
                record[key] = value.item()
    return records


def dataframe_from_records(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Inverse of `dataframe_to_records`, preserving the clean schema column order."""
    df = pd.DataFrame(rows)
    for column in LIST_COLUMNS:
        df[column] = df[column].map(lambda items: list(items) if isinstance(items, (list, tuple)) else [])
    return df[[column for column in CLEAN_COLUMNS if column in df.columns]]


def dataframe_for_csv(df: pd.DataFrame) -> pd.DataFrame:
    """CSV cannot hold lists; store them as '; '-joined strings."""
    out = df.copy()
    for column in LIST_COLUMNS:
        out[column] = out[column].map(lambda items: "; ".join(items))
    return out
