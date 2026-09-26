from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import random
from typing import Any

import pandas as pd

from core.utils import now_utc, write_json
from ingestion.cleaning import refresh_derived_columns

CORRUPTION_SEED = 42
DROP_LATEST_FRACTION = 0.20
BLANK_SUMMARY_FRACTION = 0.15
NOISE_FRACTION = 0.15
TRUNCATE_TITLE_FRACTION = 0.15
STALE_DATE_FRACTION = 0.30
DUPLICATE_FRACTION = 0.20
STALE_SHIFT_DAYS = 365
MAX_TRUNCATED_TITLE_CHARS = 7
# Mojibake / replacement characters / symbol bursts typical of broken scrapers and encodings.
REPLACEMENT_CHAR = chr(0xFFFD)
MOJIBAKE_QUOTE = chr(0x2019).encode("utf-8").decode("cp1252")  # a curly quote decoded with the wrong codec
NOISE_TOKENS = [REPLACEMENT_CHAR * 3, "#@$%^&", "~|~|~", MOJIBAKE_QUOTE * 2, "{{{}}}", "&&**&&"]


def _count(fraction: float, total: int) -> int:
    return max(1, round(fraction * total)) if total else 0


def _inject_noise(text: str, rng: random.Random, tokens: int = 3) -> str:
    words = text.split(" ")
    for _ in range(tokens):
        words.insert(rng.randint(0, len(words)), rng.choice(NOISE_TOKENS))
    return " ".join(words)


def corrupt_clean_dataframe(df: pd.DataFrame, output_log_path, seed: int = CORRUPTION_SEED) -> pd.DataFrame:
    """Simulate six realistic production data failures on a clean dataframe.

    1. drop_latest_records  – newest 20% of papers never arrive (stale knowledge).
    2. blank_summary        – scraper returns empty abstracts.
    3. inject_noise         – encoding garbage inserted into abstracts.
    4. truncate_title       – titles cut to < 8 characters.
    5. stale_date           – publication dates shifted back 365 days.
    6. duplicate_rows       – the same paper ingested twice.

    Scenarios 2-5 touch disjoint rows so every degradation can be traced back to exactly
    one corruption type. `text_for_embedding` is rebuilt so the corruption reaches the index.
    A detailed log is written to `output_log_path`.
    """
    rng = random.Random(seed)
    work = df.copy().reset_index(drop=True)
    work["authors"] = work["authors"].map(list)
    work["categories"] = work["categories"].map(list)
    corruptions: list[dict[str, Any]] = []

    # 1. Drop the latest records.
    n_drop = _count(DROP_LATEST_FRACTION, len(work))
    latest = work.sort_values(["published", "paper_id"], ascending=[False, False]).head(n_drop)
    corruptions.append(
        {
            "type": "drop_latest_records",
            "description": f"Dropped the {n_drop} most recently published papers ({DROP_LATEST_FRACTION:.0%}).",
            "params": {"fraction": DROP_LATEST_FRACTION},
            "affected_paper_ids": latest["paper_id"].tolist(),
            "details": latest[["paper_id", "title", "published"]].to_dict(orient="records"),
            "expected_signal": "paper_id unique count below raw lineage count; freshness (latest_published moves back)",
        }
    )
    work = work.drop(index=latest.index).reset_index(drop=True)

    # 2-5. Disjoint row subsets for the in-place corruptions.
    candidates = list(work.index)
    rng.shuffle(candidates)
    plan = [
        ("blank_summary", _count(BLANK_SUMMARY_FRACTION, len(work))),
        ("inject_noise", _count(NOISE_FRACTION, len(work))),
        ("truncate_title", _count(TRUNCATE_TITLE_FRACTION, len(work))),
        ("stale_date", _count(STALE_DATE_FRACTION, len(work))),
    ]
    subsets: dict[str, list[int]] = {}
    cursor = 0
    for name, size in plan:
        subsets[name] = sorted(candidates[cursor : cursor + size])
        cursor += size

    details = []
    for idx in subsets["blank_summary"]:
        details.append({"paper_id": work.at[idx, "paper_id"], "summary_before": work.at[idx, "summary"][:80], "summary_after": ""})
        work.at[idx, "summary"] = ""
    corruptions.append(
        {
            "type": "blank_summary",
            "description": "Replaced the abstract with an empty string.",
            "params": {"fraction": BLANK_SUMMARY_FRACTION},
            "affected_paper_ids": [item["paper_id"] for item in details],
            "details": details,
            "expected_signal": "ExpectColumnValueLengthsToBeBetween(summary, min 30) fails",
        }
    )

    details = []
    for idx in subsets["inject_noise"]:
        noisy = _inject_noise(work.at[idx, "summary"], rng)
        details.append({"paper_id": work.at[idx, "paper_id"], "summary_after": noisy[:120]})
        work.at[idx, "summary"] = noisy
    corruptions.append(
        {
            "type": "inject_noise",
            "description": "Inserted 3 garbage tokens (mojibake, U+FFFD, symbol bursts) into the abstract.",
            "params": {"tokens_per_row": 3, "token_pool": NOISE_TOKENS},
            "affected_paper_ids": [item["paper_id"] for item in details],
            "details": details,
            "expected_signal": "ExpectColumnValuesToNotMatchRegex(summary, noise pattern) fails",
        }
    )

    details = []
    for idx in subsets["truncate_title"]:
        before = work.at[idx, "title"]
        after = before[: rng.randint(4, MAX_TRUNCATED_TITLE_CHARS)].rstrip() or before[:1]
        details.append({"paper_id": work.at[idx, "paper_id"], "title_before": before, "title_after": after})
        work.at[idx, "title"] = after
    corruptions.append(
        {
            "type": "truncate_title",
            "description": f"Cut the title to at most {MAX_TRUNCATED_TITLE_CHARS} characters.",
            "params": {"max_chars": MAX_TRUNCATED_TITLE_CHARS},
            "affected_paper_ids": [item["paper_id"] for item in details],
            "details": details,
            "expected_signal": "ExpectColumnValueLengthsToBeBetween(title, min 8) fails",
        }
    )

    details = []
    for idx in subsets["stale_date"]:
        before = work.at[idx, "published"]
        after = (pd.Timestamp(before) - timedelta(days=STALE_SHIFT_DAYS)).strftime("%Y-%m-%d")
        details.append({"paper_id": work.at[idx, "paper_id"], "published_before": before, "published_after": after})
        work.at[idx, "published"] = after
        work.at[idx, "age_days"] = int(work.at[idx, "age_days"]) + STALE_SHIFT_DAYS
    corruptions.append(
        {
            "type": "stale_date",
            "description": f"Shifted the publication date back {STALE_SHIFT_DAYS} days.",
            "params": {"fraction": STALE_DATE_FRACTION, "shift_days": STALE_SHIFT_DAYS},
            "affected_paper_ids": [item["paper_id"] for item in details],
            "details": details,
            "expected_signal": "Freshness SLA: stale ratio (age_days > 180) exceeds 25%",
        }
    )

    # 6. Duplicate rows (sampled after the other corruptions, like a replayed ingestion batch).
    n_dup = _count(DUPLICATE_FRACTION, len(work))
    dup_idx = sorted(rng.sample(list(work.index), n_dup))
    duplicates = work.loc[dup_idx]
    corruptions.append(
        {
            "type": "duplicate_rows",
            "description": f"Appended {n_dup} exact copies of existing rows.",
            "params": {"fraction": DUPLICATE_FRACTION},
            "affected_paper_ids": duplicates["paper_id"].tolist(),
            "details": duplicates[["paper_id", "title"]].to_dict(orient="records"),
            "expected_signal": "ExpectColumnValuesToBeUnique(paper_id) fails",
        }
    )
    work = pd.concat([work, duplicates], ignore_index=True)

    # 7. Rebuild derived columns so corrupted text is what gets embedded.
    corrupted = refresh_derived_columns(work)[list(df.columns)]

    for position, item in enumerate(corruptions, start=1):
        item["id"] = position
        item["affected_count"] = len(item["affected_paper_ids"])
    write_json(
        Path(output_log_path),
        {
            "generated_at": now_utc().isoformat(),
            "seed": seed,
            "input_rows": len(df),
            "output_rows": len(corrupted),
            "input_unique_paper_ids": int(df["paper_id"].nunique()),
            "output_unique_paper_ids": int(corrupted["paper_id"].nunique()),
            "corruption_types": len(corruptions),
            "corruptions": [{"id": item.pop("id"), **item} for item in corruptions],
        },
    )
    return corrupted
