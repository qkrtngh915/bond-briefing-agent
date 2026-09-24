"""agent/verify.py 단위 테스트: 가짜 데이터로 숫자 대조 로직을 검증한다."""

from __future__ import annotations

from agent.verify import annotate_with_warnings, collect_known_numbers, verify_report


def test_collect_known_numbers_buckets_percent_vs_bp():
    tool_outputs = [
        {
            "daily_changes": {"levels": {"ktb_3y": 4.006}, "changes_bp": {"ktb_3y": -3.4}},
            "spreads": {"levels_bp": {"ktb_3_10": 38.6}, "changes_bp": {"ktb_3_10": -3.5}},
        },
        {"metrics": {"ktb_10y": {"z_score": -1.85, "std_bp": 4.59, "change_bp": -6.9}}},
    ]

    known = collect_known_numbers(tool_outputs)

    assert 4.006 in known["%"]
    assert -3.4 in known["bp"]
    assert 38.6 in known["bp"]
    assert -3.5 in known["bp"]
    assert -1.85 in known["bp"]
    assert 4.59 in known["bp"]
    assert -6.9 in known["bp"]


def test_verify_report_no_mismatch_for_values_present_in_tool_outputs():
    known = {"%": {4.006}, "bp": {-3.4, 38.6}}
    report = "국고채 3년은 4.006%로 전일 대비 -3.4bp, 국고 3/10 스프레드는 38.6bp 입니다."

    mismatches = verify_report(report, known)
    assert mismatches == []


def test_verify_report_flags_fabricated_number():
    known = {"%": {4.006}, "bp": {-3.4}}
    report = "국고채 3년은 4.006%지만 국고채 10년은 999.9%로 급등했습니다."

    mismatches = verify_report(report, known)
    assert len(mismatches) == 1
    assert mismatches[0]["value"] == 999.9
    assert mismatches[0]["unit"] == "%"


def test_verify_report_respects_tolerance_for_rounding():
    known = {"%": set(), "bp": {-3.412345}}
    report = "전일 대비 -3.4bp 하락했습니다."

    mismatches = verify_report(report, known)
    assert mismatches == []


def test_collect_known_numbers_extracts_numbers_embedded_in_strings():
    """실제 실행에서 search_news 기사 제목("美 10년물 국채금리 5.079%로 폭등")의
    숫자를 모델이 그대로 인용했는데, 숫자형 필드가 아니라는 이유로 오탐 처리된
    문제가 있었다 - 문자열 값에 박힌 %/bp 숫자도 known_values에 들어가야 한다."""
    tool_outputs = [
        [
            {
                "title": "美 10년물 국채금리 5.079%로 폭등…PMI호조로 인플레압력 부각",
                "source": "한국경제",
                "summary": "이 기사는 -12bp 급락을 언급한다",
            }
        ]
    ]

    known = collect_known_numbers(tool_outputs)

    assert 5.079 in known["%"]
    assert -12.0 in known["bp"]


def test_verify_report_no_mismatch_for_number_quoted_from_news_headline():
    known = collect_known_numbers(
        [[{"title": "美 30년물 국채 수익률 5.44%…저금리시대 종말 신호", "source": "한국경제"}]]
    )
    report = "미 국채 30년물 수익률 5.44% 기록, '저금리시대 종말 신호'로 보도됨 (한국경제)"

    mismatches = verify_report(report, known)
    assert mismatches == []


def test_annotate_with_warnings_appends_only_when_mismatch_exists():
    report = "본문입니다."
    assert annotate_with_warnings(report, []) == report

    annotated = annotate_with_warnings(report, [{"raw": "999.9%", "value": 999.9, "unit": "%"}])
    assert "검증 경고" in annotated
    assert "999.9%" in annotated
    assert annotated.startswith(report)
