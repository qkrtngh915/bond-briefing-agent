"""agent/extract.py 단위 테스트: MockLLMBackend로 실제 모델 호출 없이 다중 회차
추출 흐름과 회차별 코드 검증(_validate_tranche) 로직을 검증한다.
"""

from __future__ import annotations

import pytest

from agent.extract import EXTRACTION_TOOL, extract_filing
from agent.llm_backend import LLMResponse, MockLLMBackend, ToolUseBlock


def _mock_backend_returning(fields: dict) -> MockLLMBackend:
    response = LLMResponse(
        content=[ToolUseBlock(id="extract_1", name=EXTRACTION_TOOL["name"], input=fields)],
        stop_reason="tool_use",
    )
    return MockLLMBackend([response])


_CONSISTENT_TRANCHE_1 = {
    "series_label": "제26-1회",
    "maturity": "2년",
    "initial_offering_amount_billion_won": 400,
    "demand_participation_amount_billion_won": 650,
    "competition_ratio": 1.63,
    "coupon_guidance_band_bp": {"low_bp": -40, "high_bp": 40},
    "final_spread_bp": 38,
    "upsized": True,
    "final_issue_amount_billion_won": 650,
}

_CONSISTENT_TRANCHE_2 = {
    "series_label": "제26-2회",
    "maturity": "3년",
    "initial_offering_amount_billion_won": 300,
    "demand_participation_amount_billion_won": 750,
    "competition_ratio": 2.5,
    "coupon_guidance_band_bp": {"low_bp": -60, "high_bp": 60},
    "final_spread_bp": 55,
    "upsized": True,
    "final_issue_amount_billion_won": 750,
}

_CONSISTENT_FIELDS = {
    "issuer": "가나다전자",
    "credit_rating": "AA-",
    "tranches": [_CONSISTENT_TRANCHE_1, _CONSISTENT_TRANCHE_2],
}


def test_extract_filing_returns_multiple_tranches_with_validation():
    backend = _mock_backend_returning(_CONSISTENT_FIELDS)
    result = extract_filing("가짜 원문 텍스트", backend)

    assert result["issuer"] == "가나다전자"
    assert result["credit_rating"] == "AA-"
    assert len(result["tranches"]) == 2
    assert result["tranches"][0]["series_label"] == "제26-1회"
    assert result["tranches"][0]["_validation"]["flags"] == []
    assert result["tranches"][1]["series_label"] == "제26-2회"
    assert result["tranches"][1]["_validation"]["flags"] == []


def test_extract_filing_flags_competition_ratio_mismatch_for_one_tranche_only():
    tranche1 = dict(_CONSISTENT_TRANCHE_1)
    tranche1["competition_ratio"] = 5.0  # 실제로는 650/400=1.625인데 5.0이라고 우김
    fields = {"issuer": "가나다전자", "credit_rating": "AA-", "tranches": [tranche1, _CONSISTENT_TRANCHE_2]}
    backend = _mock_backend_returning(fields)

    result = extract_filing("가짜 원문 텍스트", backend)

    flags1 = result["tranches"][0]["_validation"]["flags"]
    flags2 = result["tranches"][1]["_validation"]["flags"]
    assert len(flags1) == 1
    assert "제26-1회" in flags1[0]
    assert "경쟁률 불일치" in flags1[0]
    assert flags2 == []  # 두 번째 회차는 정상이어야 함


def test_extract_filing_flags_final_spread_outside_band():
    tranche2 = dict(_CONSISTENT_TRANCHE_2)
    tranche2["final_spread_bp"] = 100  # 밴드는 -60~60인데 100으로 확정됐다고 함
    fields = {"issuer": "가나다전자", "credit_rating": "AA-", "tranches": [_CONSISTENT_TRANCHE_1, tranche2]}
    backend = _mock_backend_returning(fields)

    result = extract_filing("가짜 원문 텍스트", backend)

    flags2 = result["tranches"][1]["_validation"]["flags"]
    assert len(flags2) == 1
    assert "제26-2회" in flags2[0]
    assert "밴드" in flags2[0]


def test_extract_filing_single_tranche_when_only_one_series():
    fields = {"issuer": "라마바건설", "credit_rating": "BBB+", "tranches": [_CONSISTENT_TRANCHE_1]}
    backend = _mock_backend_returning(fields)

    result = extract_filing("가짜 원문 텍스트", backend)
    assert len(result["tranches"]) == 1


def test_extract_filing_no_flags_when_values_are_null():
    tranche1 = dict(_CONSISTENT_TRANCHE_1)
    tranche1["competition_ratio"] = None
    tranche1["demand_participation_amount_billion_won"] = None
    fields = {"issuer": "가나다전자", "credit_rating": "AA-", "tranches": [tranche1]}
    backend = _mock_backend_returning(fields)

    result = extract_filing("가짜 원문 텍스트", backend)
    assert result["tranches"][0]["_validation"]["flags"] == []


def test_extract_filing_raises_if_no_tool_call_returned():
    from agent.llm_backend import TextBlock

    backend = MockLLMBackend(
        [LLMResponse(content=[TextBlock(text="그냥 텍스트로만 답함")], stop_reason="end_turn")]
    )
    with pytest.raises(RuntimeError):
        extract_filing("가짜 원문 텍스트", backend)
