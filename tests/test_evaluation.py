from __future__ import annotations

from collections import Counter

import pytest

from core.utils import first_sentence, read_json
from evaluation.metrics import _judge_answer, _token_f1
from evaluation.testset import QUESTION_TEMPLATES, build_test_set


def test_build_test_set_covers_four_types(clean_df, settings):
    test_set = build_test_set(clean_df, settings.paths.eval_testset)
    assert len(test_set) == 10
    assert Counter(item["question_type"] for item in test_set) == {"summary": 3, "authors": 3, "date": 2, "categories": 2}
    assert [item["id"] for item in test_set] == [f"eval_{i:03d}" for i in range(1, 11)]
    assert len({item["ground_truth_doc_ids"][0] for item in test_set}) == 10
    assert read_json(settings.paths.eval_testset) == test_set

    rows = clean_df.set_index("paper_id")
    expected = {
        "summary": lambda row: first_sentence(row["summary"]),
        "authors": lambda row: row["authors_joined"],
        "date": lambda row: row["published"],
        "categories": lambda row: row["categories_joined"],
    }
    for item in test_set:
        row = rows.loc[item["ground_truth_doc_ids"][0]]
        assert item["question"] == QUESTION_TEMPLATES[item["question_type"]].format(title=row["title"])
        assert item["ground_truth"] == expected[item["question_type"]](row)


def test_build_test_set_spans_newest_and_oldest(clean_df, settings):
    ids = {item["ground_truth_doc_ids"][0] for item in build_test_set(clean_df, settings.paths.eval_testset)}
    assert clean_df.iloc[0]["paper_id"] in ids
    assert clean_df.iloc[-1]["paper_id"] in ids


def test_build_test_set_requires_documents(clean_df, settings):
    with pytest.raises(ValueError):
        build_test_set(clean_df.head(3), settings.paths.eval_testset)


def test_small_corpus_reuses_papers_with_distinct_types(clean_df, settings):
    test_set = build_test_set(clean_df.head(6), settings.paths.eval_testset)
    assert len(test_set) == 10
    pairs = [(item["ground_truth_doc_ids"][0], item["question_type"]) for item in test_set]
    assert len(set(pairs)) == len(pairs)


def test_token_f1_and_fallback_judge(settings):
    assert _token_f1("a b c", "a b c") == 1.0
    assert _token_f1("a b c", "") == 0.0
    assert _token_f1("a b", "c d") == 0.0
    assert 0 < _token_f1("a b c d", "a b") < 1
    verdict = _judge_answer(settings, "q", "Kien Duong, Vy Ly", "Kien Duong, Vy Ly")
    assert verdict.score == 5 and verdict.correct and verdict.reasoning.startswith("Fallback heuristic judge")
    assert _judge_answer(settings, "q", "2026-06-12", "2025-06-12").correct is False
