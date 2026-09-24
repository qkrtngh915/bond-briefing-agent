"""agent/extract.py 단위 테스트: MockLLMBackend로 실제 모델 호출 없이 추출 흐름과
코드 검증(_validate) 로직을 검증한다.
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


_CONSISTENT_FIELDS = {
    "issuer": "가나다전자",
    "credit_rating": "AA-",
    "maturity": "3년",
    "initial_offering_amount_billion_won": 500,
    "demand_participation_amount_billion_won": 1250,
    "competition_ratio": 2.5,
    "coupon_guidance_band_bp": {"low_bp": -30, "high_bp": 30},
    "final_spread_bp": 10,
    "upsized": True,
    "final_issue_amount_billion_won": 700,
}


def test_extract_filing_returns_fields_plus_validation_when_consistent():
    backend = _mock_backend_returning(_CONSISTENT_FIELDS)
    result = extract_filing("가짜 원문 텍스트", backend)

    assert result["issuer"] == "가나다전자"
    assert result["competition_ratio"] == 2.5
    assert result["_validation"]["flags"] == []


def test_extract_filing_flags_competition_ratio_mismatch():
    fields = dict(_CONSISTENT_FIELDS)
    fields["competition_ratio"] = 5.0  # 실제로는 1250/500=2.5인데 5.0이라고 우김
    backend = _mock_backend_returning(fields)

    result = extract_filing("가짜 원문 텍스트", backend)

    flags = result["_validation"]["flags"]
    assert len(flags) == 1
    assert "경쟁률 불일치" in flags[0]


def test_extract_filing_flags_final_spread_outside_band():
    fields = dict(_CONSISTENT_FIELDS)
    fields["final_spread_bp"] = 50  # 밴드는 -30~30인데 50으로 확정됐다고 함
    backend = _mock_backend_returning(fields)

    result = extract_filing("가짜 원문 텍스트", backend)

    flags = result["_validation"]["flags"]
    assert len(flags) == 1
    assert "밴드" in flags[0]


def test_extract_filing_no_flags_when_values_are_null():
    fields = dict(_CONSISTENT_FIELDS)
    fields["competition_ratio"] = None
    fields["demand_participation_amount_billion_won"] = None
    backend = _mock_backend_returning(fields)

    result = extract_filing("가짜 원문 텍스트", backend)
    assert result["_validation"]["flags"] == []


def test_extract_filing_raises_if_no_tool_call_returned():
    from agent.llm_backend import TextBlock

    backend = MockLLMBackend(
        [LLMResponse(content=[TextBlock(text="그냥 텍스트로만 답함")], stop_reason="end_turn")]
    )
    with pytest.raises(RuntimeError):
        extract_filing("가짜 원문 텍스트", backend)
