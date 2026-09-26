from __future__ import annotations

from core.config import load_settings
from core.utils import now_utc, read_json, write_json
from evaluation.metrics import evaluate_pipeline
from ingestion.corruption import corrupt_clean_dataframe
from ingestion.repair import repair_from_raw
from observability.quality import build_freshness_report, run_data_quality_checks
from observability.reporting import generate_corruption_report
from pipelines.common import configure_console, format_comparison_table, load_dataset, relative, save_dataset, step
from retrieval.index import LocalEmbeddingIndex

TOTAL_STEPS = 7


def main() -> None:
    """Corruption -> evaluate -> detect -> self-heal from raw -> evaluate -> 3-state comparison."""
    configure_console()
    settings = load_settings()
    paths = settings.paths
    run_date = now_utc()

    step(1, TOTAL_STEPS, "Load baseline artifacts from phase 1")
    required = [paths.clean_json, paths.eval_testset, paths.baseline_metrics]
    missing = [relative(path, settings) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing baseline artifacts {missing}; run `python script/run_phase1.py` first.")
    baseline_metrics = read_json(paths.baseline_metrics)
    baseline_quality = read_json(paths.baseline_quality_report) if paths.baseline_quality_report.exists() else None
    baseline_freshness = read_json(paths.freshness_report) if paths.freshness_report.exists() else None
    clean_df = load_dataset(paths.clean_json)
    print(f"      clean rows={len(clean_df)} test_set={relative(paths.eval_testset, settings)}")

    step(2, TOTAL_STEPS, "Inject 6 corruption scenarios -> data/results/corruption_log.json")
    corrupted = corrupt_clean_dataframe(clean_df, paths.corruption_log)
    save_dataset(corrupted, paths.corrupted_clean_csv, paths.corrupted_clean_json)
    corruption_log = read_json(paths.corruption_log)
    for item in corruption_log["corruptions"]:
        print(f"      - {item['type']}: {item['affected_count']} rows")

    step(3, TOTAL_STEPS, "Observability on corrupted data (GX 1.x + Freshness SLA)")
    corrupted_quality = run_data_quality_checks(corrupted, settings, "corrupted")
    corrupted_freshness = build_freshness_report(corrupted, settings, paths.quality_dir / "corrupted_freshness_report.json")
    print(f"      gx success={corrupted_quality['success']} failed={corrupted_quality['failed_checks']}")
    print(f"      freshness is_fresh={corrupted_freshness['is_fresh']} ({corrupted_freshness['message']})")

    step(4, TOTAL_STEPS, "Silent-failure simulation: index + evaluate corrupted data with the gate bypassed")
    corrupted_index = LocalEmbeddingIndex.build(corrupted, settings, paths.corrupted_embeddings_json)
    corrupted_metrics = evaluate_pipeline(
        settings, corrupted_index, paths.eval_testset, paths.corrupted_metrics, paths.corrupted_answers
    ).summary
    print(f"      hit_rate={corrupted_metrics['retrieval_hit_rate']:.3f} token_f1={corrupted_metrics['mean_token_f1']:.3f}")

    step(5, TOTAL_STEPS, "Self-healing: rebuild from raw lineage (idempotent repair)")
    gate_failed = not corrupted_quality["success"] or not corrupted_freshness["is_fresh"]
    repaired, repair_info = repair_from_raw(settings, run_date)
    repair_info = {
        "trigger": "auto (quality gate / freshness alert)" if gate_failed else "manual verification",
        "failed_checks_detected": ", ".join(corrupted_quality["failed_checks"]) or "none",
        "freshness_alert": not corrupted_freshness["is_fresh"],
        **repair_info,
    }
    save_dataset(repaired, paths.repaired_clean_csv, paths.repaired_clean_json)
    repaired_quality = run_data_quality_checks(repaired, settings, "repaired")
    repaired_freshness = build_freshness_report(repaired, settings, paths.quality_dir / "repaired_freshness_report.json")
    repair_info["post_repair_gate"] = "PASS" if repaired_quality["success"] else "FAIL"
    write_json(paths.quality_dir / "repair_summary.json", repair_info)
    print(f"      trigger={repair_info['trigger']} idempotent={repair_info['idempotent']} gate={repair_info['post_repair_gate']}")
    if not repaired_quality["success"]:
        raise RuntimeError(f"Repaired data still fails the quality gate: {repaired_quality['failed_checks']}")

    step(6, TOTAL_STEPS, "Re-index repaired data + evaluate on the same test set")
    repaired_index = LocalEmbeddingIndex.build(repaired, settings, paths.repaired_embeddings_json)
    repaired_metrics = evaluate_pipeline(
        settings, repaired_index, paths.eval_testset, paths.repaired_metrics, paths.repaired_answers
    ).summary

    step(7, TOTAL_STEPS, "3-state comparison report -> data/reports/corruption_report.md")
    generate_corruption_report(
        paths.comparison_report,
        baseline_metrics,
        corrupted_metrics,
        repaired_metrics,
        corrupted_quality,
        repaired_quality,
        corrupted_freshness,
        repaired_freshness,
        baseline_quality=baseline_quality,
        baseline_freshness=baseline_freshness,
        corruption_log=corruption_log,
        answers={
            "baseline": read_json(paths.baseline_answers) if paths.baseline_answers.exists() else [],
            "corrupted": read_json(paths.corrupted_answers),
            "repaired": read_json(paths.repaired_answers),
        },
        repair_info=repair_info,
    )

    states = (baseline_metrics, corrupted_metrics, repaired_metrics)
    rows = [(key, *(float(state[key]) for state in states)) for key in ("retrieval_hit_rate", "mean_token_f1", "judge_accuracy", "mean_judge_score")]
    rows.append(
        (
            "quality_gate",
            "PASS" if (baseline_quality or {}).get("success") else "N/A" if baseline_quality is None else "FAIL",
            "PASS" if corrupted_quality["success"] else "FAIL",
            "PASS" if repaired_quality["success"] else "FAIL",
        )
    )
    rows.append(
        (
            "freshness",
            (baseline_freshness or {}).get("status", "N/A"),
            corrupted_freshness["status"],
            repaired_freshness["status"],
        )
    )
    print("\n" + format_comparison_table(rows))
    print(f"\nReport: {relative(paths.comparison_report, settings)}")
