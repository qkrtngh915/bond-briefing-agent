"""scripts/eval_report.py 단위 테스트: 가짜 리포트 텍스트/로그로 4개 채점 함수를 검증한다."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import eval_report


def test_numbers_without_baseline_flags_bare_level():
    text = "국고 10년 금리는 3.05%입니다."
    result = eval_report.numbers_without_baseline(text)
    assert result["total_sentences_with_numbers"] == 1
    assert result["sentences_without_baseline"] == 1
    assert result["pct_without_baseline"] == 100.0


def test_numbers_without_baseline_accepts_comparison_keyword():
    text = "국고 10년 금리는 3.05%로 전일 대비 2.0bp 상승했습니다."
    result = eval_report.numbers_without_baseline(text)
    assert result["total_sentences_with_numbers"] == 1
    assert result["sentences_without_baseline"] == 0
    assert result["pct_without_baseline"] == 0.0


def test_numbers_without_baseline_percentile_keyword_counts_as_baseline():
    text = "AA- 스프레드는 66.8bp로 최근 percentile 6.6%(regime: 타이트)입니다."
    result = eval_report.numbers_without_baseline(text)
    assert result["sentences_without_baseline"] == 0


def test_sections_missing_insight_detects_missing_and_present():
    text = (
        "## 2. 금리 동향\n"
        "본문. 운용 시사점: 지켜봐야 함.\n\n"
        "## 3. 국채수급\n"
        "본문만 있고 시사점 없음.\n\n"
        "## 4. 크레딧\n"
        "운용 시사점 있음.\n"
    )
    result = eval_report.sections_missing_insight(text)
    assert "금리 동향" not in result["sections_missing_insight"]
    assert "국채수급" in result["sections_missing_insight"]
    assert "크레딧" not in result["sections_missing_insight"]


def test_sections_missing_insight_skips_sections_not_present():
    text = "## 2. 금리 동향\n운용 시사점 있음.\n"
    result = eval_report.sections_missing_insight(text)
    assert result["sections_checked"] == ["금리 동향"]
    assert result["sections_missing_insight"] == []


def test_unsourced_causal_statements_flags_bare_causal_sentence():
    text = "AA- 스프레드가 확대됐는데, 이는 최근 경기 둔화 우려 때문으로 보입니다."
    result = eval_report.unsourced_causal_statements(text)
    assert result["count"] == 1


def test_unsourced_causal_statements_allows_disclaimer_phrase():
    text = "AA- 스프레드가 확대됐지만 수급/뉴스상 뚜렷한 원인 확인 안 됨."
    result = eval_report.unsourced_causal_statements(text)
    assert result["count"] == 0


def test_unsourced_causal_statements_allows_cited_source():
    text = '금리 상승은 "美 국채금리 급등" 기사 영향으로 추정됩니다.'
    result = eval_report.unsourced_causal_statements(text)
    assert result["count"] == 0


def test_verify_mismatches_from_log_reads_last_run_end(tmp_path):
    log_path = tmp_path / "2026-09-23.jsonl"
    with log_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "run_end", "verification_mismatches": [{"raw": "100.0%"}]}) + "\n")
        f.write(json.dumps({"type": "run_end", "verification_mismatches": []}) + "\n")

    result = eval_report.verify_mismatches_from_log(log_path)
    assert result["found_log"] is True
    assert result["count"] == 0  # 마지막 run_end를 써야 함


def test_verify_mismatches_from_log_missing_file(tmp_path):
    result = eval_report.verify_mismatches_from_log(tmp_path / "does_not_exist.jsonl")
    assert result["found_log"] is False
    assert result["count"] is None


def test_evaluate_report_ignores_appended_verify_warning_section(tmp_path):
    # verify.py의 annotate_with_warnings가 붙이는 경고 문구 자체("...추정했거나
    # 잘못 인용했을 가능성...")가 인과 서술로 오탐되면 안 된다.
    text = (
        "## 2. 금리 동향\n"
        "국고 10년 3.05%로 전일 대비 2.0bp 상승. 운용 시사점: 지켜봐야 함.\n\n"
        "## ⚠ 검증 경고\n"
        "다음 숫자가 이번 실행에서 호출한 툴 결과에서 확인되지 않았습니다 "
        "(모델이 계산/추정했거나 잘못 인용했을 가능성이 있습니다):\n\n"
        "- `7bp`\n"
    )
    log_path = tmp_path / "log.jsonl"
    log_path.write_text(
        json.dumps({"type": "run_end", "verification_mismatches": [{"raw": "7bp"}]}) + "\n",
        encoding="utf-8",
    )

    result = eval_report.evaluate_report(text, log_path)
    assert result["unsourced_causal_statements"]["count"] == 0


def test_evaluate_report_combines_all_four_metrics(tmp_path):
    text = (
        "## 2. 금리 동향\n"
        "국고 10년 3.05%. 운용 시사점: 지켜봐야 함.\n"
    )
    log_path = tmp_path / "log.jsonl"
    log_path.write_text(
        json.dumps({"type": "run_end", "verification_mismatches": []}) + "\n", encoding="utf-8"
    )

    result = eval_report.evaluate_report(text, log_path)
    assert set(result.keys()) == {
        "numbers_without_baseline",
        "sections_missing_insight",
        "unsourced_causal_statements",
        "verify_mismatches",
    }
    assert result["verify_mismatches"]["count"] == 0
