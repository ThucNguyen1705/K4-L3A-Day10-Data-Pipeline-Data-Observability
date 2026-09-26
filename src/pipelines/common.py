from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

from core.config import Settings, normalized_provider
from core.utils import read_json, write_csv, write_json
from evaluation.testset import build_test_set
from ingestion.cleaning import dataframe_for_csv, dataframe_from_records, dataframe_to_records
from retrieval.index import LocalEmbeddingIndex


def configure_console() -> None:
    """Avoid UnicodeEncodeError on Windows consoles that default to cp1252."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def step(number: int, total: int, message: str) -> None:
    print(f"[{number}/{total}] {message}", flush=True)


def relative(path: Path, settings: Settings) -> str:
    try:
        return Path(path).resolve().relative_to(settings.paths.project_dir).as_posix()
    except ValueError:
        return str(path)


def save_dataset(df: pd.DataFrame, csv_path: Path, json_path: Path) -> None:
    write_csv(dataframe_for_csv(df), csv_path)
    write_json(json_path, dataframe_to_records(df))


def load_dataset(json_path: Path) -> pd.DataFrame:
    return dataframe_from_records(read_json(json_path))


def load_or_build_test_set(df: pd.DataFrame, settings: Settings) -> tuple[list[dict[str, Any]], str]:
    """Reuse the frozen test set unless it is missing, refreshed on purpose or points at unknown papers."""
    path = settings.paths.eval_testset
    if path.exists() and not settings.refresh_test_set:
        existing = read_json(path)
        known_ids = set(df["paper_id"])
        if existing and all(set(item["ground_truth_doc_ids"]) <= known_ids for item in existing):
            return existing, "reused"
    return build_test_set(df, path), "built"


def run_agent_demo(settings: Settings, index: LocalEmbeddingIndex, questions: list[str]) -> dict[str, Any]:
    """Ask the LangChain tool-calling agent a few questions; never fails the pipeline."""
    from retrieval.agent import build_agent, run_agent_question

    payload: dict[str, Any] = {"provider": normalized_provider(settings), "model": settings.model_name}
    try:
        agent = build_agent(settings, index)
        results = []
        for question in questions:
            answer = run_agent_question(agent, question)
            results.append({"question": question, "answer": answer if isinstance(answer, str) else json.dumps(answer)})
        payload.update(status="ok", results=results)
    except Exception as exc:  # the demo is optional: missing keys or quota must not break the baseline
        payload.update(status="skipped", reason=f"{type(exc).__name__}: {str(exc)[:300]}")
    write_json(settings.paths.demo_answers, payload)
    return payload


def format_comparison_table(rows: list[tuple[str, Any, Any, Any]]) -> str:
    headers = ("Metric", "Baseline", "Corrupted", "Repaired")
    cells = [headers] + [tuple(f"{value:.3f}" if isinstance(value, float) else str(value) for value in row) for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    line = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    out = [line]
    for number, row in enumerate(cells):
        out.append("| " + " | ".join(value.ljust(width) for value, width in zip(row, widths)) + " |")
        if number == 0:
            out.append(line)
    out.append(line)
    return "\n".join(out)
