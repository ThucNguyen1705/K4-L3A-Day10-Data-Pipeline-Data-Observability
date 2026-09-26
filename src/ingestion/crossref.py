from __future__ import annotations

from dataclasses import asdict, dataclass
import html
import os
from pathlib import Path
import re
import time
from typing import Any

import requests

from core.config import Settings
from core.utils import normalize_whitespace, now_utc, read_json, write_json

CROSSREF_WORKS_URL = "https://api.crossref.org/works"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 30
SELECT_FIELDS = "DOI,URL,title,abstract,author,subject,published,published-print,published-online,issued,created,deposited,link"

_TAG_RE = re.compile(r"<[^>]+>")
_ABSTRACT_LABEL_RE = re.compile(r"^abstract\s*[:.\-]?\s+", re.IGNORECASE)

# Metadata of the most recent `fetch_source_records` call (mode, attempts, errors...).
# Pipelines read it to document data lineage in their reports.
LAST_FETCH_INFO: dict[str, Any] = {}


@dataclass(frozen=True)
class PaperRecord:
    paper_id: str
    title: str
    summary: str
    authors: list[str]
    categories: list[str]
    primary_category: str
    published: str
    updated: str
    abs_url: str
    pdf_url: str
    comment: str


def clean_markup(value: str | None) -> str:
    """Strip JATS/HTML tags, unescape entities and collapse whitespace."""
    if not value:
        return ""
    text = html.unescape(str(value))
    text = _TAG_RE.sub(" ", text)
    text = normalize_whitespace(text)
    return _ABSTRACT_LABEL_RE.sub("", text)


def normalize_doi(value: str | None) -> str:
    doi = normalize_whitespace(str(value or "")).lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi


def _first_text(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            text = clean_markup(item)
            if text:
                return text
        return ""
    return clean_markup(value)


def _date_from_parts(field: Any) -> str:
    """Crossref dates look like {"date-parts": [[2026, 5, 20]]}; missing month/day default to 1."""
    if not isinstance(field, dict):
        return ""
    parts = (field.get("date-parts") or [[]])[0] or []
    if not parts or parts[0] is None:
        date_time = field.get("date-time")
        return str(date_time)[:10] if date_time else ""
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 and parts[1] else 1
    day = int(parts[2]) if len(parts) > 2 and parts[2] else 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def _first_date(item: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _date_from_parts(item.get(key))
        if value:
            return value
    return ""


def _authors(item: dict) -> list[str]:
    authors: list[str] = []
    for author in item.get("author") or []:
        if not isinstance(author, dict):
            continue
        name = normalize_whitespace(f"{author.get('given', '')} {author.get('family', '')}")
        name = name or normalize_whitespace(str(author.get("name", "")))
        if name and name not in authors:
            authors.append(name)
    return authors


def _unique_texts(values: Any) -> list[str]:
    items: list[str] = []
    for value in values or []:
        text = clean_markup(value)
        if text and text not in items:
            items.append(text)
    return items


def _pdf_url(item: dict, fallback: str) -> str:
    for link in item.get("link") or []:
        if isinstance(link, dict) and "pdf" in str(link.get("content-type", "")).lower() and link.get("URL"):
            return str(link["URL"])
    return fallback


def parse_crossref_payload(payload: dict) -> list[PaperRecord]:
    """Parse a Crossref `/works` payload into `PaperRecord`s.

    Records without DOI, title or a parseable publication date are dropped because
    they cannot be identified, displayed or checked for freshness downstream.
    """
    items = ((payload or {}).get("message") or {}).get("items") or []
    records: list[PaperRecord] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        paper_id = normalize_doi(item.get("DOI"))
        title = _first_text(item.get("title"))
        published = _first_date(item, ("published", "published-print", "published-online", "issued", "created"))
        if not paper_id or not title or not published or paper_id in seen:
            continue
        seen.add(paper_id)
        updated = _first_date(item, ("deposited", "created")) or published
        categories = _unique_texts(item.get("subject"))
        abs_url = str(item.get("URL") or f"https://doi.org/{paper_id}")
        records.append(
            PaperRecord(
                paper_id=paper_id,
                title=title,
                summary=clean_markup(item.get("abstract")),
                authors=_authors(item),
                categories=categories,
                primary_category=categories[0] if categories else "",
                published=published,
                updated=max(updated, published),
                abs_url=abs_url,
                pdf_url=_pdf_url(item, abs_url),
                comment=f"Crossref record {paper_id}",
            )
        )
    return records


def _request_crossref(settings: Settings) -> tuple[dict, dict[str, Any]]:
    params = {
        "query": settings.source_query,
        "filter": settings.source_filter,
        "rows": settings.max_results,
        "select": SELECT_FIELDS,
    }
    mailto = os.getenv("CROSSREF_MAILTO")
    if mailto:
        params["mailto"] = mailto
    headers = {"User-Agent": "day10-data-observability-lab/0.1 (educational lab pipeline)"}
    errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(CROSSREF_WORKS_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
            response = None
        if response is not None:
            if response.status_code == 200:
                return response.json(), {"attempts": attempt, "errors": errors, "url": response.url}
            errors.append(f"attempt {attempt}: HTTP {response.status_code}")
            if response.status_code not in RETRYABLE_STATUS_CODES:
                break
        if attempt < MAX_ATTEMPTS:
            retry_after = response.headers.get("Retry-After") if response is not None else None
            delay = float(retry_after) if retry_after and retry_after.isdigit() else BACKOFF_SECONDS * 2 ** (attempt - 1)
            time.sleep(min(delay, 30.0))
    raise RuntimeError("Crossref request failed: " + "; ".join(errors))


def _save_records(records: list[PaperRecord], path: Path) -> None:
    write_json(path, [asdict(record) for record in records])


def fetch_source_records(settings: Settings) -> list[PaperRecord]:
    """Load source records, preserving the raw payload for lineage.

    - Offline/dev mode (default): parse the snapshot `data/raw/crossref_response.json`.
    - Live mode (`REFRESH_SOURCE=1` or no snapshot yet): call Crossref with retry/backoff
      for 429/5xx, save the untouched response, and fall back to the snapshot on failure.
    The parsed records are always written to `data/raw/crossref_records.json`.
    """
    paths = settings.paths
    snapshot_exists = paths.raw_api_response.exists()
    info: dict[str, Any] = {"source_api": settings.source_api, "fetched_at": now_utc().isoformat()}

    payload: dict | None = None
    if settings.refresh_source or not snapshot_exists:
        try:
            payload, request_info = _request_crossref(settings)
            write_json(paths.raw_api_response, payload)
            info.update(mode="live", **request_info)
        except (RuntimeError, ValueError) as exc:
            if not snapshot_exists:
                raise RuntimeError(f"Crossref is unavailable and no snapshot exists at {paths.raw_api_response}") from exc
            info.update(mode="snapshot-fallback", error=str(exc))

    if payload is None:
        payload = read_json(paths.raw_api_response)
        info.setdefault("mode", "snapshot")

    records = parse_crossref_payload(payload)
    if not records:
        raise RuntimeError("Crossref payload did not contain any valid records.")
    _save_records(records, paths.raw_records_json)

    info.update(
        raw_items=len(((payload.get("message") or {}).get("items") or [])),
        parsed_records=len(records),
        raw_api_response=paths.raw_api_response.relative_to(paths.project_dir).as_posix(),
        raw_records_json=paths.raw_records_json.relative_to(paths.project_dir).as_posix(),
    )
    LAST_FETCH_INFO.clear()
    LAST_FETCH_INFO.update(info)
    return records


def load_raw_records(path: Path) -> list[PaperRecord]:
    """Read the raw records snapshot and map every row back to `PaperRecord`."""
    rows = read_json(Path(path))
    records: list[PaperRecord] = []
    for row in rows:
        records.append(
            PaperRecord(
                paper_id=str(row.get("paper_id") or ""),
                title=str(row.get("title") or ""),
                summary=str(row.get("summary") or ""),
                authors=[str(item) for item in row.get("authors") or []],
                categories=[str(item) for item in row.get("categories") or []],
                primary_category=str(row.get("primary_category") or ""),
                published=str(row.get("published") or ""),
                updated=str(row.get("updated") or row.get("published") or ""),
                abs_url=str(row.get("abs_url") or ""),
                pdf_url=str(row.get("pdf_url") or ""),
                comment=str(row.get("comment") or ""),
            )
        )
    return records
