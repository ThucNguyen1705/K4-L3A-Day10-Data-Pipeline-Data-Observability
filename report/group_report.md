# Group Report — Day 10: Data Pipeline & Data Observability (thực hiện cá nhân)

## 1. Thông tin bài nộp

| Thông tin         | Nội dung                  |
| ------------------ | -------------------------- |
| Khóa/Lớp         | K4                         |
| Hình thức        | Cá nhân (1 thành viên)     |
| Tên nhóm         | NguyenDangThuc             |
| Repository         | https://github.com/ThucNguyen1705/K4-L3A-Day10-Data-Pipeline-Data-Observability |
| Ngày hoàn thành | 2026-09-26                 |

### Thành viên và phân công

| STT | Họ và tên | MSSV (MHV) | Vai trò chính | Module/deliverable sở hữu |
| --: | --- | --- | --- | --- |
| 1 | Nguyễn Đăng Thực | 2A202603014 | Toàn bộ các vai trò: source, data model & evaluation set, observability, corruption & integration | Toàn bộ `src/`, `script/`, `tests/`, artifacts trong `data/` và báo cáo trong `report/` |

Bài được thực hiện cá nhân, nên một người sở hữu toàn bộ contract giữa các module (raw schema → clean schema → test set → metrics → repair) thay vì chia owner theo khối.

## 2. Tóm tắt kết quả

Bài làm hoàn thành toàn bộ 7 tầng của pipeline: ingestion Crossref (live API có retry/backoff + fallback snapshot), cleaning & data modeling, Quality Gate Great Expectations 1.x + Freshness SLA, embedding MiniLM + ChromaDB, đánh giá RAG, tiêm 6 loại lỗi, và repair idempotent từ raw lineage. Hai entrypoint `script/run_phase1.py` và `script/run_corruption_flow.py` chạy end-to-end với exit code 0.

Baseline sinh đủ artifact: `data/clean/papers_clean.{csv,json}` (24 dòng), `data/eval/test_set.json` (10 câu, 4 dạng), 3 collection ChromaDB, `baseline_metrics.json` (hit rate 1.000, token F1 1.000) và `phase1_report.md`.

Khi tiêm lỗi, pipeline vẫn chạy trơn tru và agent vẫn trả lời tự tin, nhưng hit rate giảm xuống 0.700, token F1 xuống 0.586. Đây chính là silent failure. Lỗi gây hại nhiều nhất là `drop_latest_records`: 3/10 câu hỏi chuyển từ đúng sang sai, vì agent lấy nhầm bài khác và trả lời sai tác giả/ngày mà không báo lỗi. Quality Gate phát hiện đủ 6/6 loại lỗi (5 expectation fail + Freshness SLA `stale` 34.8%).

Repair dựng lại dữ liệu từ `data/raw/`, được kích hoạt tự động khi gate fail. Sau repair, cả 4 metric trở về đúng baseline (mức phục hồi 100%), gate PASS 10/10, và hai lần rebuild cho cùng fingerprint SHA-256. Giới hạn chính: chưa có LLM API key nên judge đang dùng heuristic fallback dựa trên token F1.

## 3. Kiến trúc và luồng dữ liệu

### Luồng end-to-end

```text
Crossref API (live, REFRESH_SOURCE=1)  ──┐
data/raw/crossref_response.json (snapshot)┴─> parse_crossref_payload ─> data/raw/crossref_records.json
    -> build_clean_dataframe            -> data/clean/papers_clean.{csv,json}
    -> QUALITY GATE (GX 1.x, blocking) + Freshness SLA (warning) -> data/quality/
    -> MiniLM embedding + ChromaDB 'papers-baseline'            -> data/chroma/, data/embeddings/
    -> test set (frozen) + evaluate                              -> data/eval/, data/results/baseline_*
    -> phase1_report.md
    -> corrupt (6 scenarios, seed 42)   -> papers_clean_corrupted.*, corruption_log.json
    -> GX + freshness on corrupted (FAIL) ; index 'papers-corrupted' with gate bypassed -> corrupted_metrics.json
    -> AUTO self-heal: repair_from_raw (x2, fingerprint) -> papers_clean_repaired.* -> gate PASS
    -> index 'papers-repaired' -> repaired_metrics.json -> corruption_report.md (3 trạng thái)
```

### Trách nhiệm của từng khối

| Khối             | Input          | Xử lý chính             | Output/artifact          | Owner          |
| ----------------- | -------------- | -------------------------- | ------------------------ | -------------- |
| Ingestion         | Crossref `/works` hoặc snapshot | Retry 429/5xx (backoff, `Retry-After`), fallback snapshot, parse DOI/title/abstract/authors/subject/dates, strip JATS | `data/raw/crossref_response.json`, `data/raw/crossref_records.json` | Nguyễn Đăng Thực |
| Cleaning          | `PaperRecord` list | Normalize text, parse date, drop thiếu id/title/date/abstract, dedup `paper_id`, `age_days`, `text_for_embedding` 5 phần | `data/clean/papers_clean.{csv,json}` | Nguyễn Đăng Thực |
| Embedding/index   | Clean dataframe | `all-MiniLM-L6-v2` (normalized), Chroma cosine HNSW, xoá & tạo lại collection, dọn segment mồ côi | `data/chroma/`, `data/embeddings/*.json` | Nguyễn Đăng Thực |
| Evaluation        | Clean dataframe, index | Test set 10 câu (3 summary, 3 authors, 2 date, 2 categories), hit rate, token F1, judge | `data/eval/test_set.json`, `data/results/*_metrics.json` | Nguyễn Đăng Thực |
| Observability     | Dataframe bất kỳ | GX 1.x ephemeral context, 10 expectation (6 bắt buộc + 4 mở rộng), Freshness SLA | `data/quality/*.json`, `data/quality/gx/` | Nguyễn Đăng Thực |
| Corruption/repair | Clean dataframe, raw snapshot | 6 kịch bản lỗi trên tập bản ghi rời nhau; repair từ raw + fingerprint | `corruption_log.json`, `papers_clean_{corrupted,repaired}.*`, `repair_summary.json` | Nguyễn Đăng Thực |
| Orchestration     | Settings `core/config.py` | Thứ tự bước, gate chặn index ở phase 1, tự động repair ở phase 2 | `phase1_report.md`, `corruption_report.md` | Nguyễn Đăng Thực |

## 4. Cách tái hiện kết quả

### Cấu hình không chứa secret

| Biến/cấu hình             | Giá trị sử dụng |
| ---------------------------- | ------------------- |
| `LLM_PROVIDER`             | `mock` (không có API key; đổi sang `gemini` + `GOOGLE_API_KEY` để bật LLM judge) |
| `LLM_MODEL`                | `mock-tool-calling` |
| Embedding model              | `sentence-transformers/all-MiniLM-L6-v2` |
| Số lượng Crossref records | 24 (snapshot `data/raw/crossref_response.json`) |
| Retrieval`top_k`           | 4 |
| Freshness threshold          | 180 ngày; tối đa 25% bản ghi stale |
| Random seed, nếu có        | 42 (corruption) |

### Lệnh cài đặt

```bash
uv sync --extra dev
```

Hoặc với Python 3.11–3.13:

```bash
python -m pip install -e ".[dev]"
```

### Lệnh chạy

```bash
python script/run_phase1.py
python script/run_corruption_flow.py
python script/run_tests.py        # pytest + coverage (bonus B3)
```

### Kết quả tái hiện

| Lệnh             | Trạng thái | Thời điểm chạy gần nhất | Bằng chứng |
| ----------------- | ---------- | ----------------------- | ---------- |
| Baseline pipeline | Thành công (exit 0) | 2026-09-26 | `data/results/baseline_metrics.json`, `data/reports/phase1_report.md` |
| Corruption flow   | Thành công (exit 0) | 2026-09-26 | `data/results/{corrupted,repaired}_metrics.json`, `data/reports/corruption_report.md` |
| Test suite        | 45 passed, coverage 95% | 2026-09-26 | `python script/run_tests.py` |

## 5. Ingestion, cleaning và data contract

### Nguồn dữ liệu

| Thuộc tính                | Giá trị                             |
| --------------------------- | ------------------------------------- |
| Source                      | Crossref REST API `https://api.crossref.org/works`; mặc định đọc snapshot `data/raw/crossref_response.json` |
| Query/filter                | `query=agentic retrieval augmented generation large language model`, `filter=from-pub-date:<hôm nay-180d>,has-abstract:true`, `rows=24` |
| Thời điểm lấy dữ liệu | Snapshot có sẵn trong repo; lần chạy gần nhất ở mode `snapshot` (xem `phase1_report.md`) |
| Số record nhận được    | 24 raw items → 24 records hợp lệ |
| Cơ chế retry/backoff      | Tối đa 4 lần, retry với 429/500/502/503/504 và lỗi mạng, chờ theo `Retry-After` hoặc 2s·2^(n-1) (tối đa 30s); hết lượt thì fallback về snapshot |

### Raw và clean schema

| Trường        | Kiểu dữ liệu | Bắt buộc?  | Ý nghĩa   | Xử lý khi thiếu/sai |
| --------------- | --------------- | ------------ | ----------- | ---------------------- |
| `paper_id` | str (DOI lowercase) | Có | Document identity | Bỏ record; dedup giữ bản `updated` mới nhất |
| `title` | str | Có | Tiêu đề | Bỏ record nếu rỗng |
| `summary` | str | Có | Abstract đã bỏ JATS/HTML | Bỏ record nếu rỗng |
| `authors`, `categories` | list[str] | Không | Tác giả, subject | List rỗng, loại trùng |
| `published` | str `YYYY-MM-DD` | Có | Ngày xuất bản (thiếu tháng/ngày thì lấy 01) | Bỏ record nếu không parse được |
| `updated` | str `YYYY-MM-DD` | Không | `deposited` → `created` → `published` | Mặc định = `published` |
| `age_days` | int | Có | `(run_date - published).days` | Tính lại mỗi lần chạy |
| `authors_joined`, `categories_joined`, `summary_chars` | str/int | Có | Cột phụ trợ cho metadata và QA | Sinh tự động |
| `text_for_embedding` | str | Có | 5 phần: Title / Authors / Published / Categories / Summary | Sinh lại sau mọi thay đổi |

### Quy tắc cleaning

| Quy tắc                                 | Quality dimension liên quan | Số record bị tác động | Cách xác minh      |
| ---------------------------------------- | ---------------------------- | -------------------------: | -------------------- |
| Strip JATS/HTML tag, unescape entity, gộp khoảng trắng | Validity | 24 (mọi abstract có `<jats:p>`) | `data/clean/papers_clean.json` không còn `<jats` |
| Bỏ record thiếu `paper_id`/`title`/`published` hợp lệ | Completeness | 0 | `cleaning_stats` trong `phase1_report.md` |
| Bỏ record không có abstract | Completeness | 0 | `cleaning_stats` |
| Dedup theo `paper_id`, giữ bản mới nhất | Uniqueness | 0 | GX `paper_id_unique` PASS |

`paper_id` là DOI chuẩn hoá (lowercase, bỏ prefix `https://doi.org/`), giữ ổn định giữa các lần chạy nên test set và 3 collection dùng chung một định danh. `age_days` tính theo ngày chạy (UTC). `text_for_embedding` ghép 5 dòng `Title/Authors/Published/Categories/Summary`, nên mọi lỗi ở từng trường đều đi vào vector và context.

## 6. Evaluation setup

| Thành phần                             | Cấu hình thực tế          |
| ---------------------------------------- | ----------------------------- |
| Số câu hỏi                            | 10 |
| Các`question_type`                    | `summary` (3), `authors` (3), `date` (2), `categories` (2) |
| Ground-truth document ID                 | `paper_id` của bài được hỏi; ground truth = giá trị trường tương ứng (câu đầu của summary) |
| Embedding model                          | `all-MiniLM-L6-v2` |
| Vector store/collection                  | ChromaDB `papers-baseline`, `papers-corrupted`, `papers-repaired` (cosine) |
| Retrieval`top_k`                       | 4 |
| LLM provider/model                       | `mock` → judge dùng heuristic fallback (`judge_backend=heuristic-fallback`) |
| Test set dùng chung cho ba trạng thái | `data/eval/test_set.json`, SHA-256 `c735aa9aff78984a3d9b6d9835778866bd7a863f05a48ab9aa2705c887da6aa4` (ghi trong cả 3 file metrics) |

Bài được chọn ở các vị trí cách đều nhau theo ngày xuất bản, nên test set có cả bài mới nhất lẫn bài cũ nhất. Test set chỉ sinh ở phase 1 và được đóng băng: corruption flow chỉ đọc file này, không sinh lại. Vì vậy chênh lệch metric giữa 3 trạng thái chỉ đến từ dữ liệu, không đến từ đề thi. Hash giống nhau trong 3 file metrics chứng minh điều này.

## 7. Kết quả baseline

### Artifact checklist

| Artifact                 | Đường dẫn thực tế                | Trạng thái | Ghi chú   |
| ------------------------ | -------------------------------------- | ------------ | ---------- |
| Raw response/records     | `data/raw/`                          | Có | Parse lại từ response cho ra records giống hệt file gốc |
| Cleaned dataset          | `data/clean/`                        | Có | 24 dòng, 16 cột |
| Embedding manifest/index | `data/embeddings/`, `data/chroma/`   | Có | Đường dẫn tương đối `data/chroma`, 3 collection |
| Evaluation set           | `data/eval/`                         | Có | 10 câu |
| Baseline metrics         | `data/results/baseline_metrics.json` | Có | Có breakdown theo `question_type` |
| Quality/freshness        | `data/quality/`                      | Có | `baseline_quality_report.json`, `freshness_report.json`, `gx/` |
| Baseline report          | `data/reports/phase1_report.md`      | Có | |

### Baseline metrics

| Metric                 |       Giá trị | Diễn giải                             |
| ---------------------- | --------------: | --------------------------------------- |
| `retrieval_hit_rate` | 1.000 | 10/10 câu có tài liệu đúng trong top-4 |
| `mean_token_f1`      | 1.000 | Câu trả lời trích xuất khớp chính xác ground truth |
| `judge_accuracy`     | 1.000 | Heuristic judge (F1 ≥ 0.5 → đúng) |
| `mean_judge_score`   | 5.000 | F1 ≥ 0.95 → 5 điểm |
| Ragas, nếu có        | N/A | Không chạy: cần LLM thật (`RUN_RAGAS=1`) |

## 8. Data quality và freshness

### Quality checks

| Check        | Quality dimension | Ngưỡng/kỳ vọng | Kết quả baseline      | Bằng chứng |
| ------------ | ----------------- | ------------------ | ----------------------- | ------------ |
| `ExpectTableRowCountToBeBetween` | Volume | 5–5000 dòng | PASS (24) | `data/quality/baseline_quality_report.json` |
| `ExpectColumnValuesToNotBeNull` (`paper_id`, `title`, `text_for_embedding`) | Completeness | 0 null | PASS | như trên |
| `ExpectColumnValuesToBeUnique` (`paper_id`) | Uniqueness | Không trùng | PASS | như trên |
| `ExpectColumnValueLengthsToBeBetween` (`summary`) | Validity | ≥ 30 ký tự | PASS | như trên |
| `ExpectColumnValueLengthsToBeBetween` (`title`) — mở rộng | Validity | ≥ 8 ký tự | PASS | như trên |
| `ExpectColumnValuesToNotMatchRegex` (`summary`) — mở rộng | Accuracy | Không có U+FFFD/mojibake/chuỗi ký hiệu | PASS | như trên |
| `ExpectColumnValuesToMatchStrftimeFormat` (`published`) — mở rộng | Validity | `%Y-%m-%d` | PASS | như trên |
| `ExpectColumnUniqueValueCountToBeBetween` (`paper_id`) — mở rộng | Completeness vs lineage | ≥ 90% số record raw | PASS (24/24) | như trên |

Context được tạo đúng chuẩn GX 1.x: `gx.get_context(mode="ephemeral")` → `data_sources.add_pandas` → `add_dataframe_asset` → `add_batch_definition_whole_dataframe` → `batch.validate(suite)`. Gate có tính **chặn**: phase 1 raise lỗi và không index nếu có expectation fail.

### Freshness

| Thuộc tính               | Giá trị                           |
| -------------------------- | ----------------------------------- |
| Freshness được đo tại | Clean dataset (`age_days`), trước khi index |
| Timestamp mới nhất       | 2026-07-22 (cũ nhất 2026-03-28) |
| Ngưỡng freshness         | `age_days > 180` là stale; `is_fresh=False` khi > 25% stale |
| Trạng thái baseline      | Fresh |
| Lý do                     | 1/24 bài (4.2%) quá 180 ngày, dưới ngưỡng 25% |

Freshness là cảnh báo (không chặn), vì dữ liệu sạch vẫn có thể cũ đi theo thời gian. Với snapshot này, từ ngày chạy 2026-11-29 trở đi baseline sẽ báo `stale`, và cách khắc phục là kéo lại dữ liệu bằng `REFRESH_SOURCE=1`.

## 9. Corruption scenarios và repair

| Corruption         | Cách tạo | Record bị tác động | Quality signal kỳ vọng | Tác động thực tế | Cách repair   |
| ------------------ | ---------- | ---------------------: | ------------------------ | --------------------- | -------------- |
| `drop_latest_records` | Bỏ 20% bài mới nhất | 5 | Unique `paper_id` < 90% raw | FAIL (19 < 22); 3 câu đúng → sai | Rebuild từ raw |
| `blank_summary` | Summary = `""` | 3 | Summary length ≥ 30 | FAIL (4 dòng, tính cả bản duplicate); không câu test nào đổi | Rebuild từ raw |
| `inject_noise` | Chèn 3 token rác (U+FFFD, mojibake, ký hiệu) | 3 | Regex noise | FAIL (3 dòng); F1 hai câu summary còn 0.97 và 0.89 | Rebuild từ raw |
| `truncate_title` | Cắt title còn ≤ 7 ký tự | 3 | Title length ≥ 8 | FAIL (3 dòng); không chạm test set | Rebuild từ raw |
| `stale_date` | Lùi `published` 365 ngày | 6 | Freshness SLA | `stale` 34.8%; câu `eval_007` trả sai ngày (2025-06-03) | Rebuild từ raw |
| `duplicate_rows` | Nhân bản 4 dòng | 4 | `paper_id` unique | FAIL (8 dòng); không đổi metric | Rebuild từ raw |

Corruption log:

- Đường dẫn: `data/results/corruption_log.json`
- Trạng thái: Có
- Nhận xét: log ghi seed, số dòng vào/ra, và với từng loại lỗi: tham số, `paper_id` bị tác động, giá trị trước/sau, cùng signal kỳ vọng. Bốn lỗi in-place tác động lên các tập bản ghi rời nhau, nên mỗi mức sụt giảm truy ngược được về đúng một loại lỗi.

Repair không sửa từng ô bị hỏng. `repair_from_raw` dựng lại toàn bộ bằng cùng hàm `build_clean_dataframe` từ `data/raw/crossref_records.json`. Nếu file records lệch với response gốc, nó parse lại `crossref_response.json` để khôi phục file records; nếu mất cả hai thì re-fetch. Sau đó hàm chạy lại lần hai và so fingerprint SHA-256: hai lần cho cùng hash `99e503a8…064c`, và nội dung khớp baseline (`matches_baseline_content=True`). Collection `papers-repaired` được xoá và tạo lại, còn segment HNSW mồ côi trên đĩa bị dọn, nên không còn "ghost vector".

## 10. So sánh baseline, corrupted và repaired

| Metric/signal            | Baseline | Corrupted | Repaired | Thay đổi do corruption | Mức phục hồi | Nhận xét   |
| ------------------------ | -------: | --------: | -------: | -----------------------: | --------------: | ------------ |
| `retrieval_hit_rate`   | 1.000 | 0.700 | 1.000 | −0.300 | 100% | 3 bài bị drop không còn trong index |
| `mean_token_f1`        | 1.000 | 0.586 | 1.000 | −0.414 | 100% | Drop, stale date và noise đều kéo F1 xuống |
| `judge_accuracy`       | 1.000 | 0.600 | 1.000 | −0.400 | 100% | 4 câu sai: 3 do drop, 1 do stale date |
| `mean_judge_score`     | 5.000 | 3.200 | 5.000 | −1.800 | 100% | |
| Quality checks pass/fail | 10/10 PASS | 5/10 FAIL | 10/10 PASS | −5 check | 100% | Gate phát hiện 5/6 lỗi, lỗi còn lại do freshness bắt |
| Freshness status         | fresh (4%) | stale (35%) | fresh (4%) | +31% stale | 100% | |

Kết luận nhân quả (từ `corruption_report.md` §4–5):

1. `drop_latest_records` bỏ 5 bài mới nhất → `unique_papers_at_least_90%_of_raw` FAIL (19/24) → 3 câu hỏi về các bài đó mất retrieval hit. Agent không báo "không biết" mà trả lời bằng bài gần giống nhất, ví dụ `eval_002` trả "Anh Tran, Quoc Pham" thay vì "Kien Duong, Vy Ly". Đây là silent failure điển hình.
2. `stale_date` → Freshness SLA `stale` (34.8% > 25%) → `eval_007` vẫn retrieval hit nhưng trả sai ngày `2025-06-03` (F1 = 0). Hit rate không lộ ra lỗi này; chỉ answer-level metric và freshness mới bắt được.
3. Gate fail tự động kích hoạt repair từ raw lineage → gate PASS 10/10, freshness `fresh` → cả 4 metric về đúng baseline (phục hồi 100%).

Ba lỗi `blank_summary`, `truncate_title` và `duplicate_rows` **không** làm giảm metric trên test set: bài bị blank chỉ được hỏi câu `categories`, không bài bị cắt title nào nằm trong test set, và bản duplicate không đẩy tài liệu đúng ra khỏi top-4. Dù vậy, cả ba đều bị GX bắt. Điều này cho thấy không thể dựa vào metric của agent để phát hiện lỗi dữ liệu.

## 11. Vấn đề tích hợp quan trọng

- **Triệu chứng:** Trên dữ liệu sạch, gate báo `row_count_between_5_and_5000` FAIL với `observed_value=None`.
- **Nguyên nhân:** Có hai lỗi chồng lên nhau. (1) `validation.results` của GX 1.x **không** trả theo thứ tự `add_expectation`, trong khi code đang `zip` kết quả với danh sách spec, nên kết quả bị gán nhầm tên. (2) Expectation thật sự fail là regex noise: pandas 3 dùng pyarrow (engine RE2), mà RE2 không hiểu escape `\uXXXX` của Python, nên metric raise exception.
- **Cách xử lý:** Gắn `meta={"check_name": ...}` cho từng expectation rồi map kết quả theo meta. Regex được dựng bằng `chr()` để chứa ký tự thật. Report ghi thêm trường `error` lấy từ `exception_info`.
- **Cách xác minh:** `tests/test_quality.py` (mỗi expectation bắt đúng lỗi của nó), `data/quality/baseline_quality_report.json` 10/10 PASS và không có `error`.

Các vấn đề khác đã xử lý: manifest embedding lưu đường dẫn tuyệt đối máy local (đã đổi sang đường dẫn tương đối `data/chroma`); ChromaDB giữ thư mục HNSW của collection đã xoá (đã dọn các segment không còn tham chiếu); mock LLM gốc không hỗ trợ `bind_tools` nên agent không chạy được (đã viết `MockToolCallingChatModel`).

## 12. Giới hạn và hướng cải thiện

| Giới hạn hiện tại | Ảnh hưởng   | Hướng cải thiện có thể kiểm chứng |
| --------------------- | -------------- | ----------------------------------------- |
| Chưa có LLM API key, judge dùng heuristic theo token F1 | `judge_accuracy` gần như trùng với F1, chưa đánh giá được ngữ nghĩa | Đặt `LLM_PROVIDER=gemini` + key; khi đó `judge_backend=llm` trong metrics, chạy thêm `RUN_RAGAS=1` |
| QA trích xuất theo luật (`qa.py`), không sinh câu trả lời | Metric đo retrieval + extraction, chưa đo hallucination của generation | Đánh giá thêm agent LLM trên cùng test set, so faithfulness |
| Freshness phụ thuộc ngày chạy | Từ 2026-11-29 baseline của snapshot sẽ báo `stale` | Lịch re-fetch `REFRESH_SOURCE=1`; theo dõi `latest_published` qua thời gian |
| Snapshot có các cặp bài gần trùng ("Advanced Perspectives on …") | Bài bị drop vẫn "được trả lời" bằng bài anh em, che bớt lỗi | Thêm check near-duplicate theo title/embedding; test set gồm câu hỏi phân biệt các bài gần trùng |
| Corruption dùng tỷ lệ và seed cố định | Chỉ phản ánh một kịch bản | Chạy nhiều seed, báo cáo trung bình và phương sai của mức sụt giảm |

## 13. Checklist trước khi nộp

- [x] Thông tin nhóm và repository chính xác.
- [x] Phân công khớp với module, artifact và kết quả thực tế.
- [x] Lệnh tái hiện đã được chạy lại trên phiên bản dùng để nộp.
- [x] Baseline, corrupted và repaired dùng cùng evaluation set.
- [x] Bảng metrics khớp với các file trong `data/results/`.
- [x] Quality/freshness conclusions khớp với `data/quality/`.
- [x] Các đường dẫn báo cáo và artifact truy cập được.
- [ ] Đã hoàn thành báo cáo cá nhân `report/2A202603014_NguyenDangThuc.md`.
- [x] Không có `.env`, API key, token hoặc secret trong source, report, log hay ảnh.
