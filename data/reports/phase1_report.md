# Phase 1 Report — Baseline Data Pipeline

_Sinh tự động bởi `script/run_phase1.py` lúc 2026-09-26T04:25:03+00:00._

## 1. Nguồn dữ liệu & Data Lineage

| Thuộc tính | Giá trị |
| --- | --- |
| Source API | Crossref REST API (https://api.crossref.org/works) |
| Ingestion mode | snapshot |
| Query | agentic retrieval augmented generation large language model |
| Filter | from-pub-date:2026-03-30,has-abstract:true (chỉ áp dụng khi gọi live API) |
| Fetched at (UTC) | 2026-09-26T04:24:54.296958+00:00 |
| Raw items / parsed records | 24 / 24 |
| Raw artifacts | `data/raw/crossref_response.json`, `data/raw/crossref_records.json` |
| Clean rows | 24 |
| Cleaning drops | dropped_missing_id_title_or_date=0, dropped_missing_summary=0, dropped_duplicate_paper_id=0 |
| Run date (age_days reference) | 2026-09-26 |
| Embedding model | sentence-transformers/all-MiniLM-L6-v2 |
| Chroma collection | papers-baseline (24 docs, top_k=4) |
| Test set | `data/eval/test_set.json` (10 questions, reused) |
| LLM provider / model | mock / mock-tool-calling |
| Agent demo | ok |

## 2. Baseline metrics

| Metric | Giá trị | Ý nghĩa |
| --- | --- | --- |
| `retrieval_hit_rate` | 1.000 | Tỷ lệ câu hỏi có tài liệu đúng nằm trong top-k |
| `mean_token_f1` | 1.000 | Độ trùng token giữa câu trả lời và ground truth |
| `judge_accuracy` | 1.000 | Tỷ lệ câu trả lời được judge chấm là đúng |
| `mean_judge_score` | 5 | Điểm judge trung bình (1-5) |

- Số câu hỏi: **10** — judge backend: `heuristic-fallback` (`heuristic-fallback` = chấm theo token F1 khi không có LLM judge).
- Ragas: không chạy (đặt `RUN_RAGAS=1` để bật, cần LLM thật)
- Test set SHA-256: `c735aa9aff78984a3d9b6d9835778866bd7a863f05a48ab9aa2705c887da6aa4`

### Theo loại câu hỏi

| question_type | samples | hit_rate | token_f1 | judge_accuracy |
| --- | --- | --- | --- | --- |
| authors | 3 | 1.000 | 1.000 | 1.000 |
| categories | 2 | 1.000 | 1.000 | 1.000 |
| date | 2 | 1.000 | 1.000 | 1.000 |
| summary | 3 | 1.000 | 1.000 | 1.000 |

## 3. Data Quality Gate (Great Expectations 1.x)

- Engine: `great_expectations 1.18.0` — context `ephemeral` (`gx.get_context(mode="ephemeral")` → `data_sources.add_pandas` → `add_dataframe_asset` → `add_batch_definition_whole_dataframe`).

- Kết quả gate: **PASS (10/10)** trên 24 dòng / 24 paper_id.

| Check | Dimension | GX Expectation | Loại | Kết quả | Observed |
| --- | --- | --- | --- | --- | --- |
| row_count_between_5_and_5000 | volume | `expect_table_row_count_to_be_between` | Bắt buộc | PASS | 24 |
| paper_id_not_null | completeness | `expect_column_values_to_not_be_null` | Bắt buộc | PASS | 0 unexpected (0.0%) |
| title_not_null | completeness | `expect_column_values_to_not_be_null` | Bắt buộc | PASS | 0 unexpected (0.0%) |
| text_for_embedding_not_null | completeness | `expect_column_values_to_not_be_null` | Bắt buộc | PASS | 0 unexpected (0.0%) |
| paper_id_unique | uniqueness | `expect_column_values_to_be_unique` | Bắt buộc | PASS | 0 unexpected (0.0%) |
| summary_length_at_least_30 | validity | `expect_column_value_lengths_to_be_between` | Bắt buộc | PASS | 0 unexpected (0.0%) |
| title_length_at_least_8 | validity | `expect_column_value_lengths_to_be_between` | Mở rộng | PASS | 0 unexpected (0.0%) |
| summary_free_of_encoding_noise | accuracy | `expect_column_values_to_not_match_regex` | Mở rộng | PASS | 0 unexpected (0.0%) |
| published_is_iso_date | validity | `expect_column_values_to_match_strftime_format` | Mở rộng | PASS | 0 unexpected (0.0%) |
| unique_papers_at_least_90%_of_raw | completeness | `expect_column_unique_value_count_to_be_between` | Mở rộng | PASS | 24 |

## 4. Freshness SLA

- Trạng thái: **fresh (4% stale)** — 1/24 rows (4.2%) are older than 180 days; SLA allows at most 25%.

| Thuộc tính | Giá trị |
| --- | --- |
| Ngưỡng tuổi bài báo (ngày) | 180 |
| Tỷ lệ stale tối đa cho phép | 0.250 |
| Tổng số dòng | 24 |
| Số dòng stale | 1 |
| Tỷ lệ stale | 0.042 |
| Bài mới nhất | 2026-07-22 |
| Bài cũ nhất | 2026-03-28 |
| Tuổi trung vị (ngày) | 111.500 |
| is_fresh | True |

## 5. Chi tiết từng câu hỏi

| ID | Type | Hit | Token F1 | Judge | Câu trả lời |
| --- | --- | --- | --- | --- | --- |
| eval_001 | summary | ✅ | 1.000 | 5 | Static benchmarks fail to capture domain drift in enterprise knowledg… |
| eval_002 | authors | ✅ | 1.000 | 5 | Kien Duong, Vy Ly |
| eval_003 | date | ✅ | 1.000 | 5 | 2026-06-12 |
| eval_004 | categories | ✅ | 1.000 | 5 | Information Retrieval, Natural Language Processing |
| eval_005 | summary | ✅ | 1.000 | 5 | An extended empirical study on single LLM is susceptible to confirmat… |
| eval_006 | authors | ✅ | 1.000 | 5 | Tuan Phan, Mai Bui |
| eval_007 | date | ✅ | 1.000 | 5 | 2026-06-03 |
| eval_008 | categories | ✅ | 1.000 | 5 | Artificial Intelligence, Information Retrieval |
| eval_009 | summary | ✅ | 1.000 | 5 | Connecting LLMs directly to raw warehouse tables often results in sch… |
| eval_010 | authors | ✅ | 1.000 | 5 | Tuan Phan, Mai Bui |

## 6. Nhận xét

- Quality gate đạt: dữ liệu được phép nạp vào ChromaDB.
- Freshness: 1/24 bài quá 180 ngày → `is_fresh=True`.
- Baseline đạt hit rate 1.000 và token F1 1.000; đây là mốc so sánh cho corrupted/repaired trong `corruption_report.md`.
