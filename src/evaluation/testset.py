from __future__ import annotations

from typing import Any

import pandas as pd

from core.utils import first_sentence, write_json

TEST_SET_SIZE = 10
MIN_DOCUMENTS = 5
# 3 summary + 3 authors + 2 date + 2 categories, interleaved so every type spans old and new papers.
QUESTION_TYPE_PLAN = ["summary", "authors", "date", "categories"] * 2 + ["summary", "authors"]

# Wording must match the intent rules in `retrieval.qa._extract_answer`.
QUESTION_TEMPLATES = {
    "summary": "What is the summary of the paper '{title}'?",
    "authors": "Who authored the paper '{title}'?",
    "date": "When was the paper '{title}' published?",
    "categories": "What categories does the paper '{title}' belong to?",
}


def _ground_truth(row: pd.Series, question_type: str) -> str:
    if question_type == "summary":
        return first_sentence(row["summary"])
    if question_type == "authors":
        return row["authors_joined"]
    if question_type == "date":
        return row["published"]
    return row["categories_joined"]


def _eligible(df: pd.DataFrame) -> pd.DataFrame:
    """Papers that can be asked about unambiguously."""
    trigger_words = ("who authored", "when was", "publication date", "published on", "what categories")
    title_counts = df["title"].str.lower().value_counts()
    mask = (
        ~df["title"].str.contains("'", regex=False)
        & ~df["title"].str.lower().map(lambda title: any(word in title for word in trigger_words))
        & df["title"].str.lower().map(lambda title: title_counts[title] == 1)
        & (df["summary"].map(lambda text: len(first_sentence(text))) >= 20)
        & (df["authors_joined"] != "")
        & (df["categories_joined"] != "")
    )
    return df[mask].drop_duplicates("paper_id")


def build_test_set(df: pd.DataFrame, output_path) -> list[dict[str, Any]]:
    """Build a fixed 10-question benchmark with ground truth from the clean dataframe.

    Papers are picked at evenly spaced positions of the publication-date ordering, so the
    set covers both the newest and the oldest documents. Ground truth is the exact field
    value, which lets Token F1 reach 1.0 when retrieval and extraction are correct.
    """
    candidates = _eligible(df).sort_values(["published", "paper_id"], ascending=[False, True]).reset_index(drop=True)
    if len(candidates) < MIN_DOCUMENTS:
        raise ValueError(f"Need at least {MIN_DOCUMENTS} eligible documents to build a test set, got {len(candidates)}.")

    if len(candidates) >= TEST_SET_SIZE:
        step = (len(candidates) - 1) / (TEST_SET_SIZE - 1)
        positions = [round(i * step) for i in range(TEST_SET_SIZE)]
    else:
        positions = [i % len(candidates) for i in range(TEST_SET_SIZE)]

    test_set: list[dict[str, Any]] = []
    for number, (position, question_type) in enumerate(zip(positions, QUESTION_TYPE_PLAN, strict=True), start=1):
        row = candidates.iloc[position]
        test_set.append(
            {
                "id": f"eval_{number:03d}",
                "question_type": question_type,
                "question": QUESTION_TEMPLATES[question_type].format(title=row["title"]),
                "ground_truth": _ground_truth(row, question_type),
                "ground_truth_doc_ids": [row["paper_id"]],
            }
        )

    write_json(output_path, test_set)
    return test_set
