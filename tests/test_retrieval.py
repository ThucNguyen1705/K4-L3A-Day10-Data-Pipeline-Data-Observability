from __future__ import annotations

from dataclasses import replace

import pytest

from core.config import require_llm_credentials
from core.utils import read_json
from evaluation.metrics import evaluate_pipeline
from evaluation.testset import build_test_set
from retrieval.agent import build_agent, run_agent_question
from retrieval.index import LocalEmbeddingIndex
from retrieval.llm import MockToolCallingChatModel, build_llm
from retrieval.qa import answer_question


@pytest.fixture
def index(clean_df, settings):
    return LocalEmbeddingIndex.build(clean_df, settings, settings.paths.embeddings_json)


def test_index_build_persists_portable_manifest(index, settings, clean_df):
    assert index.collection.name == "papers-baseline"
    assert index.collection.count() == len(clean_df)
    manifest = read_json(settings.paths.embeddings_json)
    assert manifest["persist_path"] == "data/chroma"
    reloaded = LocalEmbeddingIndex.load(settings)
    assert reloaded.collection.count() == len(clean_df)


def test_rebuild_prunes_orphan_segments(index, clean_df, settings):
    LocalEmbeddingIndex.build(clean_df, settings, settings.paths.embeddings_json)
    LocalEmbeddingIndex.build(clean_df, settings, settings.paths.corrupted_embeddings_json)
    segment_dirs = [child for child in settings.paths.chroma_dir.iterdir() if child.is_dir()]
    assert len(segment_dirs) <= 3  # baseline + corrupted (+ one folder still locked on Windows at most)


def test_search_and_lookup(index, clean_df):
    title = clean_df.iloc[3]["title"]
    results = index.search(title, top_k=3)
    assert len(results) == 3
    assert clean_df.iloc[3]["paper_id"] in [result.paper_id for result in results]
    assert index.lookup(title.upper())["paper_id"] == clean_df.iloc[3]["paper_id"]
    assert index.lookup("no such paper") is None


@pytest.mark.parametrize("field", ["summary", "authors", "date", "categories"])
def test_answer_question_per_type(index, clean_df, settings, field):
    test_set = build_test_set(clean_df, settings.paths.eval_testset)
    item = next(entry for entry in test_set if entry["question_type"] == field)
    result = answer_question(item["question"], settings=settings, index=index)
    assert result.retrieved_doc_ids[0] == item["ground_truth_doc_ids"][0]
    assert result.answer == item["ground_truth"]


def test_evaluate_pipeline_on_clean_index(index, clean_df, settings):
    build_test_set(clean_df, settings.paths.eval_testset)
    bundle = evaluate_pipeline(
        settings, index, settings.paths.eval_testset, settings.paths.baseline_metrics, settings.paths.baseline_answers
    )
    assert bundle.summary["retrieval_hit_rate"] == 1.0
    assert bundle.summary["mean_token_f1"] == 1.0
    assert bundle.summary["judge_backend"] == "heuristic-fallback"
    assert set(bundle.summary["by_question_type"]) == {"summary", "authors", "date", "categories"}
    assert len(bundle.summary["test_set_sha256"]) == 64


def test_mock_agent_uses_tools(index, clean_df, settings):
    agent = build_agent(settings, index)
    title = clean_df.iloc[0]["title"]
    answer = run_agent_question(agent, f"Who authored the paper '{title}'?")
    assert title in answer and clean_df.iloc[0]["authors_joined"] in answer
    fallback = run_agent_question(agent, "Which paper covers 'nothing that exists'?")
    assert fallback.startswith("[mock] Top match")


def test_mock_model_without_tools_and_structured_output():
    model = MockToolCallingChatModel()
    assert "mock response" in model.invoke("hello").content
    with pytest.raises(NotImplementedError):
        model.with_structured_output(dict)
    assert "could not find" in MockToolCallingChatModel._answer_from_tool_output("No exact paper match found.")


@pytest.mark.parametrize(
    ("provider", "key_field"),
    [("gemini", "google_api_key"), ("openai", "openai_api_key"), ("anthropic", "anthropic_api_key"), ("openrouter", "openrouter_api_key")],
)
def test_provider_router_requires_credentials(settings, provider, key_field):
    configured = replace(settings, llm_provider=provider, **{key_field: None})
    with pytest.raises(RuntimeError, match="required"):
        require_llm_credentials(configured)
    client = build_llm(replace(configured, **{key_field: "test-key"}, model_name="test-model"))
    assert client is not None


def test_provider_router_rejects_unknown(settings):
    with pytest.raises(RuntimeError, match="Unsupported"):
        build_llm(replace(settings, llm_provider="nope"))
    assert build_llm(replace(settings, llm_provider="ollama")) is not None
    assert build_llm(replace(settings, llm_provider="custom", custom_llm_base_url="http://localhost:1")) is not None
