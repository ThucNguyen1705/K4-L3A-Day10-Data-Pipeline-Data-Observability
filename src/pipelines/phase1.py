from __future__ import annotations

from core.config import load_settings, normalized_provider
from core.utils import now_utc
from evaluation.metrics import evaluate_pipeline
from ingestion import crossref
from ingestion.cleaning import build_clean_dataframe
from observability.quality import build_freshness_report, run_data_quality_checks
from observability.reporting import generate_phase1_report
from pipelines.common import configure_console, load_or_build_test_set, relative, run_agent_demo, save_dataset, step
from retrieval.index import LocalEmbeddingIndex

TOTAL_STEPS = 8
DEMO_QUESTIONS = ["Which papers discuss quality gates or data observability for RAG systems?"]


def main() -> None:
    """Baseline pipeline: ingest -> clean -> quality gate -> index -> evaluate -> report."""
    configure_console()
    settings = load_settings()
    paths = settings.paths
    run_date = now_utc()

    step(1, TOTAL_STEPS, "Ingestion: Crossref source -> data/raw/")
    records = crossref.fetch_source_records(settings)
    fetch_info = dict(crossref.LAST_FETCH_INFO)
    print(f"      mode={fetch_info.get('mode')} raw_items={fetch_info.get('raw_items')} parsed={len(records)}")

    step(2, TOTAL_STEPS, "Cleaning & pre-embed modeling -> data/clean/")
    df = build_clean_dataframe(records, run_date)
    save_dataset(df, paths.clean_csv, paths.clean_json)
    cleaning_stats = df.attrs.get("cleaning_stats", {})
    print(f"      clean rows={len(df)} stats={cleaning_stats}")

    step(3, TOTAL_STEPS, "Quality gate (Great Expectations 1.x) + Freshness SLA -> data/quality/")
    quality = run_data_quality_checks(df, settings, "baseline")
    freshness = build_freshness_report(df, settings, paths.freshness_report)
    print(f"      gx success={quality['success']} failed={quality['failed_checks']}")
    print(f"      freshness is_fresh={freshness['is_fresh']} ({freshness['message']})")
    if not quality["success"]:
        raise RuntimeError(
            f"Quality gate failed ({', '.join(quality['failed_checks'])}); refusing to index. "
            f"See {relative(paths.baseline_quality_report, settings)}."
        )
    if not freshness["is_fresh"]:
        print("      WARNING: freshness SLA violated - re-fetch the source with REFRESH_SOURCE=1.")

    step(4, TOTAL_STEPS, f"Embedding ({settings.embedding_model}) + ChromaDB collection '{settings.baseline_collection_name}'")
    index = LocalEmbeddingIndex.build(df, settings, paths.embeddings_json)
    print(f"      indexed documents={index.collection.count()}")

    step(5, TOTAL_STEPS, "Evaluation set -> data/eval/test_set.json")
    test_set, test_set_status = load_or_build_test_set(df, settings)
    print(f"      {test_set_status} {len(test_set)} questions")

    step(6, TOTAL_STEPS, "Baseline evaluation (hit rate, token F1, judge)")
    bundle = evaluate_pipeline(settings, index, paths.eval_testset, paths.baseline_metrics, paths.baseline_answers)
    metrics = bundle.summary
    print(
        f"      hit_rate={metrics['retrieval_hit_rate']:.3f} token_f1={metrics['mean_token_f1']:.3f} "
        f"judge_acc={metrics['judge_accuracy']:.3f} judge={metrics['judge_backend']}"
    )

    step(7, TOTAL_STEPS, f"Agent demo (LLM_PROVIDER={normalized_provider(settings)})")
    demo = run_agent_demo(settings, index, [test_set[0]["question"], *DEMO_QUESTIONS])
    print(f"      status={demo['status']}{'' if demo['status'] == 'ok' else ' - ' + demo.get('reason', '')}")

    step(8, TOTAL_STEPS, "Phase 1 report -> data/reports/phase1_report.md")
    source_summary = {
        "Source API": f"{settings.source_api} ({crossref.CROSSREF_WORKS_URL})",
        "Ingestion mode": fetch_info.get("mode"),
        "Query": settings.source_query,
        "Filter": f"{settings.source_filter}" + (" (chỉ áp dụng khi gọi live API)" if fetch_info.get("mode") != "live" else ""),
        "Fetched at (UTC)": fetch_info.get("fetched_at"),
        "Raw items / parsed records": f"{fetch_info.get('raw_items')} / {len(records)}",
        "Raw artifacts": f"`{fetch_info.get('raw_api_response')}`, `{fetch_info.get('raw_records_json')}`",
        "Clean rows": len(df),
        "Cleaning drops": ", ".join(f"{key}={value}" for key, value in cleaning_stats.items() if key.startswith("dropped")),
        "Run date (age_days reference)": df.attrs.get("run_date"),
        "Embedding model": settings.embedding_model,
        "Chroma collection": f"{settings.baseline_collection_name} ({index.collection.count()} docs, top_k={settings.top_k})",
        "Test set": f"`{relative(paths.eval_testset, settings)}` ({len(test_set)} questions, {test_set_status})",
        "LLM provider / model": f"{normalized_provider(settings)} / {settings.model_name}",
        "Agent demo": demo["status"],
    }
    generate_phase1_report(paths.baseline_report, source_summary, metrics, quality, freshness, bundle.answers)

    print("\nPhase 1 completed. Artifacts:")
    for path in (paths.clean_csv, paths.clean_json, paths.eval_testset, paths.baseline_metrics, paths.baseline_report):
        print(f"  - {relative(path, settings)}")
