# Danh Sách Thành Viên & Báo Cáo Phân Công

- **Hình thức thực hiện:** Cá nhân (1 thành viên, đảm nhận toàn bộ các vai trò)
- **Tên Nhóm:** `NguyenDangThuc`
- **Mã Nhóm / Lớp:** `K4-L3-DAY10`
- **Repository Nộp Bài:** `https://github.com/ThucNguyen1705/K4-L3A-Day10-Data-Pipeline-Data-Observability`

---

## # Thành viên

| STT | Họ và tên | MSSV (MHV) | Email | Vai trò & Phân công công việc | Báo cáo cá nhân |
|---:|---|---|---|---|---|
| 1 | Nguyễn Đăng Thực | 2A202603014 | [Điền email] | Toàn bộ các vai trò: Pipeline Integrator (`core/`, `pipelines/`), Data Foundation & Recovery (`crossref.py`, `cleaning.py`, `repair.py`), RAG & Vector Index (`retrieval/`), Observability & Evaluation (`quality.py`, `testset.py`, `reporting.py`), Corruption (`corruption.py`) | `report/2A202603014_NguyenDangThuc.md` |

---

## # Cá nhân

### ## NguyenDangThuc-2A202603014
- **Vai trò:** Thực hiện cá nhân toàn bộ bài lab (ingestion → cleaning → quality gate → index → evaluation → corruption → repair → báo cáo).
- **Công việc chi tiết đã hoàn thành:**
  - **Ingestion & lineage** (`src/ingestion/crossref.py`): gọi Crossref REST API có retry/backoff cho 429/5xx và `Retry-After`, fallback về snapshot `data/raw/crossref_response.json`, parse DOI/title/abstract/authors/subject/dates, bỏ thẻ JATS, lưu 2 raw artifact.
  - **Cleaning & data model** (`src/ingestion/cleaning.py`): chuẩn hoá text và ngày, loại record thiếu id/title/date/abstract, khử trùng lặp theo `paper_id`, tính `age_days`, ghép `text_for_embedding` 5 phần; xuất `data/clean/papers_clean.{csv,json}` (24 dòng).
  - **Data Observability** (`src/observability/quality.py`): Quality Gate theo chuẩn GX 1.x (`gx.get_context(mode="ephemeral")`, `add_pandas`, `add_dataframe_asset`, `add_batch_definition_whole_dataframe`) gồm 4 nhóm expectation bắt buộc và 4 expectation mở rộng (độ dài title, nhiễu mã hoá, định dạng ngày, độ đầy đủ so với raw); Freshness SLA (`age_days > 180`, cảnh báo khi > 25%).
  - **Embedding & vector store** (`src/retrieval/index.py`): 3 collection ChromaDB tách biệt (`papers-baseline`, `papers-corrupted`, `papers-repaired`); manifest dùng đường dẫn tương đối; dọn segment HNSW mồ côi sau khi xoá collection.
  - **QA Agent đa provider** (`src/retrieval/llm.py`): thêm mock LLM có gọi tool để agent chạy được offline; router hỗ trợ gemini/openai/anthropic/openrouter/ollama/custom/mock.
  - **Evaluation** (`src/evaluation/testset.py`, `metrics.py`): test set 10 câu thuộc 4 dạng `summary`/`authors`/`date`/`categories`; thêm breakdown theo loại câu hỏi và hash của test set vào metrics.
  - **Corruption & repair** (`src/ingestion/corruption.py`, `src/ingestion/repair.py`): 6 kịch bản lỗi (seed 42, các tập bản ghi rời nhau) có log chi tiết; repair idempotent dựng lại từ raw lineage, được kích hoạt tự động khi gate fail, kiểm chứng bằng fingerprint SHA-256.
  - **Orchestration & báo cáo** (`src/pipelines/`, `src/observability/reporting.py`): `script/run_phase1.py`, `script/run_corruption_flow.py`, `phase1_report.md`, `corruption_report.md` (đối chiếu 3 trạng thái, truy vết nguyên nhân theo từng câu hỏi).
  - **Kiểm thử** (`tests/`, `script/run_tests.py`, `.github/workflows/ci.yml`): 45 test từ unit đến end-to-end, coverage 95%.
- **Kết quả chính:** hit rate 1.000 → 0.700 → 1.000; token F1 1.000 → 0.586 → 1.000; Quality Gate PASS → FAIL (5/10) → PASS; Freshness fresh → stale → fresh (`data/reports/corruption_report.md`).
- **Sử dụng trợ lý AI:** Có sử dụng Claude Code để hỗ trợ viết code, gỡ lỗi và soạn báo cáo; kết quả đã được chạy lại và kiểm chứng trên máy cá nhân.
- **Điều học được / Đóng góp chính:**
  - [Tự viết bằng lời của bạn: 2–3 điều học được về data lineage, quality gate, silent failure, idempotent repair…]
