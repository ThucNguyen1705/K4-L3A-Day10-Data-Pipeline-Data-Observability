from __future__ import annotations

from typing import Any

from core.utils import now_utc, write_text

METRIC_ROWS = [
    ("retrieval_hit_rate", "Tỷ lệ câu hỏi có tài liệu đúng nằm trong top-k"),
    ("mean_token_f1", "Độ trùng token giữa câu trả lời và ground truth"),
    ("judge_accuracy", "Tỷ lệ câu trả lời được judge chấm là đúng"),
    ("mean_judge_score", "Điểm judge trung bình (1-5)"),
]

# Which quality signal is expected to catch each corruption type.
DETECTED_BY = {
    "drop_latest_records": "unique_papers_at_least",
    "blank_summary": "summary_length_at_least",
    "inject_noise": "summary_free_of_encoding_noise",
    "truncate_title": "title_length_at_least",
    "stale_date": "freshness",
    "duplicate_rows": "paper_id_unique",
}


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _signed(value: float) -> str:
    return f"{value:+.3f}"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|").replace("\n", " ") for cell in row) + " |")
    return "\n".join(lines)


def _short(text: Any, limit: int = 70) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _gate_label(quality: dict[str, Any] | None) -> str:
    if not quality:
        return "N/A"
    stats = quality.get("statistics", {})
    verdict = "PASS" if quality.get("success") else "FAIL"
    return f"{verdict} ({stats.get('successful', 0)}/{stats.get('evaluated', 0)})"


def _freshness_label(freshness: dict[str, Any] | None) -> str:
    if not freshness:
        return "N/A"
    return f"{freshness.get('status', 'unknown')} ({freshness.get('stale_ratio', 0):.0%} stale)"


def _quality_table(quality: dict[str, Any]) -> str:
    rows = []
    for check in quality.get("checks", []):
        observed = check.get("observed_value")
        if observed is None and check.get("unexpected_count") is not None:
            observed = f"{check['unexpected_count']} unexpected ({(check.get('unexpected_percent') or 0):.1f}%)"
        rows.append(
            [
                check["name"],
                check["dimension"],
                f"`{check['expectation']}`",
                "Bắt buộc" if check.get("mandatory") else "Mở rộng",
                "PASS" if check["success"] else "**FAIL**",
                _short(observed, 60),
            ]
        )
    return _table(["Check", "Dimension", "GX Expectation", "Loại", "Kết quả", "Observed"], rows)


def _ragas_label(ragas: Any) -> str:
    if isinstance(ragas, dict) and "skipped" in ragas:
        return "không chạy (đặt `RUN_RAGAS=1` để bật, cần LLM thật)"
    if isinstance(ragas, dict) and "error" in ragas:
        return f"lỗi — {_short(ragas['error'], 120)}"
    return _fmt(ragas)


def _freshness_table(freshness: dict[str, Any]) -> str:
    keys = [
        ("threshold_days", "Ngưỡng tuổi bài báo (ngày)"),
        ("max_stale_ratio", "Tỷ lệ stale tối đa cho phép"),
        ("total_rows", "Tổng số dòng"),
        ("stale_rows", "Số dòng stale"),
        ("stale_ratio", "Tỷ lệ stale"),
        ("latest_published", "Bài mới nhất"),
        ("oldest_published", "Bài cũ nhất"),
        ("median_age_days", "Tuổi trung vị (ngày)"),
        ("is_fresh", "is_fresh"),
    ]
    return _table(["Thuộc tính", "Giá trị"], [[label, _fmt(freshness.get(key))] for key, label in keys])


def generate_phase1_report(
    report_path,
    source_summary: dict[str, Any],
    metrics: dict[str, Any],
    quality: dict[str, Any],
    freshness: dict[str, Any],
    answers: list[dict[str, Any]] | None = None,
) -> None:
    """Write the baseline (phase 1) Markdown report from real pipeline outputs."""
    sections = [
        "# Phase 1 Report — Baseline Data Pipeline",
        f"_Sinh tự động bởi `script/run_phase1.py` lúc {now_utc().isoformat(timespec='seconds')}._",
        "## 1. Nguồn dữ liệu & Data Lineage",
        _table(["Thuộc tính", "Giá trị"], [[key, _fmt(value)] for key, value in source_summary.items()]),
        "## 2. Baseline metrics",
        _table(
            ["Metric", "Giá trị", "Ý nghĩa"],
            [[f"`{key}`", _fmt(metrics.get(key)), label] for key, label in METRIC_ROWS],
        ),
        "\n".join(
            [
                f"- Số câu hỏi: **{metrics.get('samples')}** — judge backend: `{metrics.get('judge_backend', 'unknown')}` "
                "(`heuristic-fallback` = chấm theo token F1 khi không có LLM judge).",
                f"- Ragas: {_ragas_label(metrics.get('ragas'))}",
                f"- Test set SHA-256: `{metrics.get('test_set_sha256', 'N/A')}`",
            ]
        ),
    ]

    by_type = metrics.get("by_question_type") or {}
    if by_type:
        sections += [
            "### Theo loại câu hỏi",
            _table(
                ["question_type", "samples", "hit_rate", "token_f1", "judge_accuracy"],
                [
                    [name, row["samples"], _fmt(row["retrieval_hit_rate"]), _fmt(row["mean_token_f1"]), _fmt(row["judge_accuracy"])]
                    for name, row in by_type.items()
                ],
            ),
        ]

    sections += [
        "## 3. Data Quality Gate (Great Expectations 1.x)",
        f"- Engine: `{quality.get('engine')}` — context `{quality.get('context_mode')}` "
        f"(`gx.get_context(mode=\"ephemeral\")` → `data_sources.add_pandas` → `add_dataframe_asset` → "
        f"`add_batch_definition_whole_dataframe`).",
        f"- Kết quả gate: **{_gate_label(quality)}** trên {quality.get('rows')} dòng / {quality.get('unique_paper_ids')} paper_id.",
        _quality_table(quality),
        "## 4. Freshness SLA",
        f"- Trạng thái: **{_freshness_label(freshness)}** — {freshness.get('message', '')}",
        _freshness_table(freshness),
    ]

    if answers:
        sections += [
            "## 5. Chi tiết từng câu hỏi",
            _table(
                ["ID", "Type", "Hit", "Token F1", "Judge", "Câu trả lời"],
                [
                    [
                        item["id"],
                        item["question_type"],
                        "✅" if item["retrieval_hit"] else "❌",
                        _fmt(item["token_f1"]),
                        item["judge"]["score"],
                        _short(item["answer"]),
                    ]
                    for item in answers
                ],
            ),
        ]

    verdict = "đạt" if quality.get("success") else "KHÔNG đạt"
    sections += [
        "## 6. Nhận xét",
        "\n".join(
            [
                f"- Quality gate {verdict}: dữ liệu {'được' if quality.get('success') else 'không được'} phép nạp vào ChromaDB.",
                f"- Freshness: {freshness.get('stale_rows')}/{freshness.get('total_rows')} bài quá "
                f"{freshness.get('threshold_days')} ngày → `is_fresh={freshness.get('is_fresh')}`.",
                f"- Baseline đạt hit rate {_fmt(metrics.get('retrieval_hit_rate'))} và token F1 "
                f"{_fmt(metrics.get('mean_token_f1'))}; đây là mốc so sánh cho corrupted/repaired trong `corruption_report.md`.",
            ]
        ),
    ]
    write_text(report_path, "\n\n".join(sections) + "\n")


def _recovery(baseline: float, corrupted: float, repaired: float) -> str:
    drop = baseline - corrupted
    if abs(drop) < 1e-9:
        return "không đổi"
    return f"{(repaired - corrupted) / drop:.0%}"


def _answers_by_id(items: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items or []}


def _impact_by_type(corruption_log: dict[str, Any], answers: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    baseline = _answers_by_id(answers.get("baseline"))
    corrupted = _answers_by_id(answers.get("corrupted"))
    rows = []
    for item in corruption_log.get("corruptions", []):
        touched_ids = set(item["affected_paper_ids"])
        questions = [qid for qid, answer in baseline.items() if touched_ids & set(answer["ground_truth_doc_ids"])]
        lost = [
            qid
            for qid in questions
            if qid in corrupted and baseline[qid]["judge"]["correct"] and not corrupted[qid]["judge"]["correct"]
        ]
        f1_drop = sum(baseline[qid]["token_f1"] - corrupted[qid]["token_f1"] for qid in questions if qid in corrupted)
        rows.append({"type": item["type"], "questions": questions, "lost": lost, "f1_drop": f1_drop})
    return rows


def _detected(corruption_type: str, quality: dict[str, Any], freshness: dict[str, Any]) -> str:
    prefix = DETECTED_BY.get(corruption_type)
    if prefix == "freshness":
        return "✅ Freshness SLA" if not freshness.get("is_fresh", True) else "❌ không phát hiện"
    for check in quality.get("checks", []):
        if prefix and check["name"].startswith(prefix):
            return f"✅ `{check['name']}`" if not check["success"] else f"❌ `{check['name']}` vẫn pass"
    return "❌ không có check"


def generate_corruption_report(
    report_path,
    baseline_metrics: dict[str, Any],
    corrupted_metrics: dict[str, Any],
    repaired_metrics: dict[str, Any],
    corrupted_quality: dict[str, Any],
    repaired_quality: dict[str, Any],
    corrupted_freshness: dict[str, Any],
    repaired_freshness: dict[str, Any],
    baseline_quality: dict[str, Any] | None = None,
    baseline_freshness: dict[str, Any] | None = None,
    corruption_log: dict[str, Any] | None = None,
    answers: dict[str, list[dict[str, Any]]] | None = None,
    repair_info: dict[str, Any] | None = None,
) -> None:
    """Write the Baseline vs Corrupted vs Repaired comparison report (analysis derived from data)."""
    answers = answers or {}
    corruption_log = corruption_log or {}
    states = [baseline_metrics, corrupted_metrics, repaired_metrics]

    comparison_rows = []
    for key, _label in METRIC_ROWS:
        base, corr, rep = (float(state.get(key, 0.0)) for state in states)
        comparison_rows.append([f"`{key}`", _fmt(base), _fmt(corr), _fmt(rep), _signed(corr - base), _recovery(base, corr, rep)])
    comparison_rows += [
        [
            "Quality gate (GX 1.x)",
            _gate_label(baseline_quality),
            _gate_label(corrupted_quality),
            _gate_label(repaired_quality),
            f"{len(corrupted_quality.get('failed_checks', []))} check fail",
            "PASS" if repaired_quality.get("success") else "FAIL",
        ],
        [
            "Freshness SLA",
            _freshness_label(baseline_freshness),
            _freshness_label(corrupted_freshness),
            _freshness_label(repaired_freshness),
            f"{(corrupted_freshness.get('stale_ratio', 0) - (baseline_freshness or {}).get('stale_ratio', 0)):+.0%} stale",
            repaired_freshness.get("status", "N/A"),
        ],
        [
            "Rows / unique paper_id",
            f"{(baseline_quality or {}).get('rows', 'N/A')} / {(baseline_quality or {}).get('unique_paper_ids', 'N/A')}",
            f"{corrupted_quality.get('rows')} / {corrupted_quality.get('unique_paper_ids')}",
            f"{repaired_quality.get('rows')} / {repaired_quality.get('unique_paper_ids')}",
            "",
            "",
        ],
    ]

    hit_drop = baseline_metrics["retrieval_hit_rate"] - corrupted_metrics["retrieval_hit_rate"]
    f1_drop = baseline_metrics["mean_token_f1"] - corrupted_metrics["mean_token_f1"]
    recovered = all(
        abs(float(repaired_metrics.get(key, 0)) - float(baseline_metrics.get(key, 0))) < 1e-9 for key, _ in METRIC_ROWS
    )
    sections = [
        "# Corruption Report — Baseline vs Corrupted vs Repaired",
        f"_Sinh tự động bởi `script/run_corruption_flow.py` lúc {now_utc().isoformat(timespec='seconds')}. "
        "Ba trạng thái được đánh giá trên cùng `data/eval/test_set.json`, cùng embedding model và cùng `top_k`._",
        "## 1. Tóm tắt",
        "\n".join(
            [
                f"- **Silent failure:** pipeline index + QA trên dữ liệu bẩn chạy hết mà không raise lỗi nào, nhưng "
                f"hit rate giảm {hit_drop:.3f} ({_fmt(baseline_metrics['retrieval_hit_rate'])} → "
                f"{_fmt(corrupted_metrics['retrieval_hit_rate'])}) và token F1 giảm {f1_drop:.3f} "
                f"({_fmt(baseline_metrics['mean_token_f1'])} → {_fmt(corrupted_metrics['mean_token_f1'])}).",
                f"- **Observability:** Quality gate trên dữ liệu bẩn: **{_gate_label(corrupted_quality)}**; "
                f"freshness: **{_freshness_label(corrupted_freshness)}**.",
                f"- **Repair:** {'khôi phục 100% baseline' if recovered else 'chưa khôi phục hoàn toàn baseline'} "
                f"(hit rate {_fmt(repaired_metrics['retrieval_hit_rate'])}, token F1 {_fmt(repaired_metrics['mean_token_f1'])}), "
                f"quality gate **{_gate_label(repaired_quality)}**.",
            ]
        ),
        "## 2. Bảng đối chiếu 3 trạng thái",
        _table(["Metric / Signal", "Baseline", "Corrupted", "Repaired", "Δ do corruption", "Mức phục hồi"], comparison_rows),
        "_Mức phục hồi = (Repaired − Corrupted) / (Baseline − Corrupted)._",
    ]

    by_type_rows = []
    for question_type in sorted((baseline_metrics.get("by_question_type") or {}).keys()):
        cells = [question_type]
        for state in states:
            row = (state.get("by_question_type") or {}).get(question_type, {})
            cells.append(f"{_fmt(row.get('retrieval_hit_rate'), 2)} / {_fmt(row.get('mean_token_f1'), 2)}")
        by_type_rows.append(cells)
    if by_type_rows:
        sections += [
            "### Theo loại câu hỏi (hit rate / token F1)",
            _table(["question_type", "Baseline", "Corrupted", "Repaired"], by_type_rows),
        ]

    check_rows = []
    quality_states = [baseline_quality or {}, corrupted_quality, repaired_quality]
    names = [check["name"] for check in corrupted_quality.get("checks", [])]
    for name in names:
        cells = [name]
        for quality in quality_states:
            check = next((item for item in quality.get("checks", []) if item["name"] == name), None)
            if check is None:
                cells.append("N/A")
            elif check["success"]:
                cells.append("PASS")
            else:
                count = check.get("unexpected_count")
                cells.append(f"**FAIL**{f' ({count} rows)' if count is not None else ''}")
        check_rows.append(cells)
    sections += [
        "## 3. Data Quality Gate theo từng expectation",
        _table(["Check", "Baseline", "Corrupted", "Repaired"], check_rows),
    ]

    if corruption_log:
        impact = {row["type"]: row for row in _impact_by_type(corruption_log, answers)}
        log_rows = []
        for item in corruption_log.get("corruptions", []):
            row = impact.get(item["type"], {"questions": [], "lost": []})
            log_rows.append(
                [
                    item["id"],
                    f"`{item['type']}`",
                    item["affected_count"],
                    len(row["questions"]),
                    len(row["lost"]),
                    _detected(item["type"], corrupted_quality, corrupted_freshness),
                ]
            )
        sections += [
            "## 4. Nhật ký tiêm lỗi & khả năng phát hiện",
            f"Seed `{corruption_log.get('seed')}` — {corruption_log.get('input_rows')} dòng vào → "
            f"{corruption_log.get('output_rows')} dòng ra ({corruption_log.get('output_unique_paper_ids')} paper_id duy nhất). "
            "Chi tiết: `data/results/corruption_log.json`.",
            _table(
                ["#", "Corruption", "Bản ghi bị tác động", "Câu hỏi test chạm tới", "Câu mất đúng→sai", "Phát hiện bởi"],
                log_rows,
            ),
        ]

    base_answers = _answers_by_id(answers.get("baseline"))
    corr_answers = _answers_by_id(answers.get("corrupted"))
    rep_answers = _answers_by_id(answers.get("repaired"))
    if base_answers and corruption_log:
        doc_to_types: dict[str, list[str]] = {}
        for item in corruption_log.get("corruptions", []):
            for paper_id in item["affected_paper_ids"]:
                doc_to_types.setdefault(paper_id, [])
                if item["type"] not in doc_to_types[paper_id]:
                    doc_to_types[paper_id].append(item["type"])
        trace_rows = []
        for qid, base in base_answers.items():
            doc = base["ground_truth_doc_ids"][0]
            corr = corr_answers.get(qid, {})
            rep = rep_answers.get(qid, {})
            trace_rows.append(
                [
                    qid,
                    base["question_type"],
                    ", ".join(doc_to_types.get(doc, [])) or "—",
                    f"{'✅' if base['retrieval_hit'] else '❌'} {_fmt(base['token_f1'], 2)}",
                    f"{'✅' if corr.get('retrieval_hit') else '❌'} {_fmt(corr.get('token_f1'), 2)}",
                    f"{'✅' if rep.get('retrieval_hit') else '❌'} {_fmt(rep.get('token_f1'), 2)}",
                    _short(corr.get("answer"), 60),
                ]
            )
        sections += [
            "## 5. Truy vết nguyên nhân theo từng câu hỏi",
            "Mỗi ô Baseline/Corrupted/Repaired = retrieval hit + token F1.",
            _table(
                ["ID", "Type", "Corruption chạm tài liệu đúng", "Baseline", "Corrupted", "Repaired", "Câu trả lời khi corrupted"],
                trace_rows,
            ),
        ]

    if repair_info:
        sections += [
            "## 6. Bằng chứng Idempotent Repair / Self-healing",
            _table(["Thuộc tính", "Giá trị"], [[key, _fmt(value)] for key, value in repair_info.items()]),
            "Repair không vá dữ liệu bẩn mà dựng lại toàn bộ từ raw lineage (`data/raw/`), rồi xoá và tạo lại collection "
            "`papers-repaired` trong ChromaDB. Hai lần rebuild cho cùng fingerprint SHA-256 ⇒ chạy lại bao nhiêu lần "
            "kết quả vẫn như nhau.",
        ]

    analysis = []
    if corruption_log and answers:
        impact_rows = sorted(_impact_by_type(corruption_log, answers), key=lambda row: (len(row["lost"]), row["f1_drop"]), reverse=True)
        worst = impact_rows[0] if impact_rows else None
        if worst and (worst["lost"] or worst["f1_drop"] > 0):
            analysis.append(
                f"- Corruption gây hại lớn nhất trên test set: `{worst['type']}` — làm {len(worst['lost'])} câu hỏi "
                f"chuyển từ đúng sang sai, tổng token F1 mất {worst['f1_drop']:.2f}."
            )
        harmless = [row["type"] for row in impact_rows if not row["lost"] and row["f1_drop"] <= 1e-9]
        if harmless:
            analysis.append(
                f"- Không làm giảm metric của agent trên test set: {', '.join(f'`{name}`' for name in harmless)}. "
                "Các lỗi này vẫn bị quality gate/freshness phát hiện — đây chính là lý do cần observability ở tầng dữ liệu, "
                "vì metric của agent không đủ để lộ ra mọi lỗi."
            )
    analysis += [
        f"- Chuỗi nhân quả 1: dữ liệu bị tiêm 6 loại lỗi → {len(corrupted_quality.get('failed_checks', []))} expectation "
        f"fail + freshness `{corrupted_freshness.get('status')}` → hit rate {_signed(-hit_drop)}, token F1 {_signed(-f1_drop)}.",
        f"- Chuỗi nhân quả 2: rebuild từ raw lineage → quality gate {_gate_label(repaired_quality)}, freshness "
        f"`{repaired_freshness.get('status')}` → hit rate {_fmt(repaired_metrics['retrieval_hit_rate'])}, token F1 "
        f"{_fmt(repaired_metrics['mean_token_f1'])} ({'bằng' if recovered else 'khác'} baseline).",
    ]
    if not repaired_freshness.get("is_fresh", True):
        analysis.append(
            "- Freshness của bản repaired vẫn stale: repair chỉ khôi phục được những gì raw snapshot có. "
            "Muốn hết stale phải kéo dữ liệu mới (`REFRESH_SOURCE=1`)."
        )
    sections += ["## 7. Phân tích", "\n".join(analysis)]
    write_text(report_path, "\n\n".join(sections) + "\n")
