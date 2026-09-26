# Corruption Report — Baseline vs Corrupted vs Repaired

_Sinh tự động bởi `script/run_corruption_flow.py` lúc 2026-09-26T04:25:35+00:00. Ba trạng thái được đánh giá trên cùng `data/eval/test_set.json`, cùng embedding model và cùng `top_k`._

## 1. Tóm tắt

- **Silent failure:** pipeline index + QA trên dữ liệu bẩn chạy hết mà không raise lỗi nào, nhưng hit rate giảm 0.300 (1.000 → 0.700) và token F1 giảm 0.414 (1.000 → 0.586).
- **Observability:** Quality gate trên dữ liệu bẩn: **FAIL (5/10)**; freshness: **stale (35% stale)**.
- **Repair:** khôi phục 100% baseline (hit rate 1.000, token F1 1.000), quality gate **PASS (10/10)**.

## 2. Bảng đối chiếu 3 trạng thái

| Metric / Signal | Baseline | Corrupted | Repaired | Δ do corruption | Mức phục hồi |
| --- | --- | --- | --- | --- | --- |
| `retrieval_hit_rate` | 1.000 | 0.700 | 1.000 | -0.300 | 100% |
| `mean_token_f1` | 1.000 | 0.586 | 1.000 | -0.414 | 100% |
| `judge_accuracy` | 1.000 | 0.600 | 1.000 | -0.400 | 100% |
| `mean_judge_score` | 5.000 | 3.200 | 5.000 | -1.800 | 100% |
| Quality gate (GX 1.x) | PASS (10/10) | FAIL (5/10) | PASS (10/10) | 5 check fail | PASS |
| Freshness SLA | fresh (4% stale) | stale (35% stale) | fresh (4% stale) | +31% stale | fresh |
| Rows / unique paper_id | 24 / 24 | 23 / 19 | 24 / 24 |  |  |

_Mức phục hồi = (Repaired − Corrupted) / (Baseline − Corrupted)._

### Theo loại câu hỏi (hit rate / token F1)

| question_type | Baseline | Corrupted | Repaired |
| --- | --- | --- | --- |
| authors | 1.00 / 1.00 | 0.67 / 0.67 | 1.00 / 1.00 |
| categories | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| date | 1.00 / 1.00 | 0.50 / 0.00 | 1.00 / 1.00 |
| summary | 1.00 / 1.00 | 0.67 / 0.62 | 1.00 / 1.00 |

## 3. Data Quality Gate theo từng expectation

| Check | Baseline | Corrupted | Repaired |
| --- | --- | --- | --- |
| row_count_between_5_and_5000 | PASS | PASS | PASS |
| paper_id_not_null | PASS | PASS | PASS |
| title_not_null | PASS | PASS | PASS |
| text_for_embedding_not_null | PASS | PASS | PASS |
| paper_id_unique | PASS | **FAIL** (8 rows) | PASS |
| summary_length_at_least_30 | PASS | **FAIL** (4 rows) | PASS |
| title_length_at_least_8 | PASS | **FAIL** (3 rows) | PASS |
| summary_free_of_encoding_noise | PASS | **FAIL** (3 rows) | PASS |
| published_is_iso_date | PASS | PASS | PASS |
| unique_papers_at_least_90%_of_raw | PASS | **FAIL** | PASS |

## 4. Nhật ký tiêm lỗi & khả năng phát hiện

Seed `42` — 24 dòng vào → 23 dòng ra (19 paper_id duy nhất). Chi tiết: `data/results/corruption_log.json`.

| # | Corruption | Bản ghi bị tác động | Câu hỏi test chạm tới | Câu mất đúng→sai | Phát hiện bởi |
| --- | --- | --- | --- | --- | --- |
| 1 | `drop_latest_records` | 5 | 3 | 3 | ✅ `unique_papers_at_least_90%_of_raw` |
| 2 | `blank_summary` | 3 | 1 | 0 | ✅ `summary_length_at_least_30` |
| 3 | `inject_noise` | 3 | 2 | 0 | ✅ `summary_free_of_encoding_noise` |
| 4 | `truncate_title` | 3 | 0 | 0 | ✅ `title_length_at_least_8` |
| 5 | `stale_date` | 6 | 2 | 1 | ✅ Freshness SLA |
| 6 | `duplicate_rows` | 4 | 1 | 0 | ✅ `paper_id_unique` |

## 5. Truy vết nguyên nhân theo từng câu hỏi

Mỗi ô Baseline/Corrupted/Repaired = retrieval hit + token F1.

| ID | Type | Corruption chạm tài liệu đúng | Baseline | Corrupted | Repaired | Câu trả lời khi corrupted |
| --- | --- | --- | --- | --- | --- | --- |
| eval_001 | summary | drop_latest_records | ✅ 1.00 | ❌ 0.00 | ✅ 1.00 | An extended empirical study on valuating open-domain QA req… |
| eval_002 | authors | drop_latest_records | ✅ 1.00 | ❌ 0.00 | ✅ 1.00 | Anh Tran, Quoc Pham |
| eval_003 | date | drop_latest_records | ✅ 1.00 | ❌ 0.00 | ✅ 1.00 | 2026-06-05 |
| eval_004 | categories | — | ✅ 1.00 | ✅ 1.00 | ✅ 1.00 | Information Retrieval, Natural Language Processing |
| eval_005 | summary | inject_noise | ✅ 1.00 | ✅ 0.97 | ✅ 1.00 | An extended empirical study on single LLM is susceptible to… |
| eval_006 | authors | duplicate_rows | ✅ 1.00 | ✅ 1.00 | ✅ 1.00 | Tuan Phan, Mai Bui |
| eval_007 | date | stale_date | ✅ 1.00 | ✅ 0.00 | ✅ 1.00 | 2025-06-03 |
| eval_008 | categories | blank_summary | ✅ 1.00 | ✅ 1.00 | ✅ 1.00 | Artificial Intelligence, Information Retrieval |
| eval_009 | summary | inject_noise | ✅ 1.00 | ✅ 0.89 | ✅ 1.00 | Connecting LLMs directly ��� to raw warehouse ~\|~\|~ tables … |
| eval_010 | authors | stale_date | ✅ 1.00 | ✅ 1.00 | ✅ 1.00 | Tuan Phan, Mai Bui |

## 6. Bằng chứng Idempotent Repair / Self-healing

| Thuộc tính | Giá trị |
| --- | --- |
| trigger | auto (quality gate / freshness alert) |
| failed_checks_detected | paper_id_unique, summary_length_at_least_30, title_length_at_least_8, summary_free_of_encoding_noise, unique_papers_at_least_90%_of_raw |
| freshness_alert | True |
| repair_source | raw_records_json |
| raw_records | 24 |
| repaired_rows | 24 |
| fingerprint_run_1 | 99e503a895b7be96ee7847e62e713dc1b3c3a013f07e0903ebccc5b88b4b064c |
| fingerprint_run_2 | 99e503a895b7be96ee7847e62e713dc1b3c3a013f07e0903ebccc5b88b4b064c |
| idempotent | True |
| matches_baseline_content | True |
| post_repair_gate | PASS |

Repair không vá dữ liệu bẩn mà dựng lại toàn bộ từ raw lineage (`data/raw/`), rồi xoá và tạo lại collection `papers-repaired` trong ChromaDB. Hai lần rebuild cho cùng fingerprint SHA-256 ⇒ chạy lại bao nhiêu lần kết quả vẫn như nhau.

## 7. Phân tích

- Corruption gây hại lớn nhất trên test set: `drop_latest_records` — làm 3 câu hỏi chuyển từ đúng sang sai, tổng token F1 mất 3.00.
- Không làm giảm metric của agent trên test set: `blank_summary`, `truncate_title`, `duplicate_rows`. Các lỗi này vẫn bị quality gate/freshness phát hiện — đây chính là lý do cần observability ở tầng dữ liệu, vì metric của agent không đủ để lộ ra mọi lỗi.
- Chuỗi nhân quả 1: dữ liệu bị tiêm 6 loại lỗi → 5 expectation fail + freshness `stale` → hit rate -0.300, token F1 -0.414.
- Chuỗi nhân quả 2: rebuild từ raw lineage → quality gate PASS (10/10), freshness `fresh` → hit rate 1.000, token F1 1.000 (bằng baseline).
