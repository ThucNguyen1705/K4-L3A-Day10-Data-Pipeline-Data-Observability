"""End-to-end: both entrypoints on an isolated copy of the snapshot (same code path as the scripts)."""

from __future__ import annotations

import pytest

from core.config import load_settings
from core.utils import read_json
from pipelines import corruption_flow, phase1

from conftest import make_project

METRICS = ("retrieval_hit_rate", "mean_token_f1", "judge_accuracy", "mean_judge_score")


@pytest.fixture
def e2e_settings(tmp_path, monkeypatch):
    settings = load_settings(make_project(tmp_path / "project"))
    monkeypatch.setattr(phase1, "load_settings", lambda: settings)
    monkeypatch.setattr(corruption_flow, "load_settings", lambda: settings)
    return settings


def test_corruption_flow_requires_phase1(e2e_settings):
    with pytest.raises(RuntimeError, match="run_phase1"):
        corruption_flow.main()


def test_phase1_then_corruption_flow(e2e_settings, capsys):
    paths = e2e_settings.paths
    phase1.main()
    for path in (paths.clean_csv, paths.clean_json, paths.eval_testset, paths.baseline_metrics, paths.baseline_report):
        assert path.exists(), path
    baseline = read_json(paths.baseline_metrics)
    assert baseline["retrieval_hit_rate"] == 1.0
    assert read_json(paths.demo_answers)["status"] == "ok"

    corruption_flow.main()
    corrupted = read_json(paths.corrupted_metrics)
    repaired = read_json(paths.repaired_metrics)
    assert corrupted["retrieval_hit_rate"] < baseline["retrieval_hit_rate"]
    assert corrupted["mean_token_f1"] < baseline["mean_token_f1"]
    assert all(repaired[key] == baseline[key] for key in METRICS)
    assert baseline["test_set_sha256"] == corrupted["test_set_sha256"] == repaired["test_set_sha256"]

    assert read_json(paths.corruption_log)["corruption_types"] == 6
    assert read_json(paths.corrupted_quality_report)["success"] is False
    assert read_json(paths.quality_dir / "repaired_quality_report.json")["success"] is True
    repair = read_json(paths.quality_dir / "repair_summary.json")
    assert repair["idempotent"] and repair["trigger"].startswith("auto")

    report = paths.comparison_report.read_text(encoding="utf-8")
    assert "| Metric / Signal | Baseline | Corrupted | Repaired |" in report
    output = capsys.readouterr().out
    assert "| Metric             | Baseline | Corrupted | Repaired |" in output

    # Idempotency of the whole flow: a second run reproduces the same numbers.
    corruption_flow.main()
    assert read_json(paths.corrupted_metrics) == corrupted
    assert read_json(paths.repaired_metrics) == repaired


def test_phase1_gate_blocks_bad_data(e2e_settings, monkeypatch):
    def broken_quality(df, settings, name):
        return {"success": False, "failed_checks": ["paper_id_unique"]}

    monkeypatch.setattr(phase1, "run_data_quality_checks", broken_quality)
    with pytest.raises(RuntimeError, match="refusing to index"):
        phase1.main()
    assert not e2e_settings.paths.embeddings_json.exists()
