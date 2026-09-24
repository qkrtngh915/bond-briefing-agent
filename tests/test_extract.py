"""agent/extract.py 단위 테스트: MockLLMBackend로 실제 모델 호출 없이 다중 회차
추출 흐름, 코드 레벨 금액 환산(_to_eok_won), 회차별 검증(_validate_tranche)을 검증한다.
"""

from __future__ import annotations

import pytest

import agent.extract as extract_module
from agent.extract import (
    EXTRACT_PROMPT_VERSION,
    EXTRACTION_TOOL,
    _to_eok_won,
    extract_filing,
    extract_filing_cached,
    load_cached_extraction,
    save_cached_extraction,
)
from agent.llm_backend import LLMResponse, MockLLMBackend, ToolUseBlock


def _mock_backend_returning(fields: dict) -> MockLLMBackend:
    response = LLMResponse(
        content=[ToolUseBlock(id="extract_1", name=EXTRACTION_TOOL["name"], input=fields)],
        stop_reason="tool_use",
    )
    return MockLLMBackend([response])


def _tranche(
    label: str,
    offering: dict | None,
    participation: dict | None,
    competition: float | None,
    band: dict | None,
    final_spread: float | None,
    final_issue: dict | None,
    upsized: bool | None = True,
) -> dict:
    return {
        "series_label": label,
        "maturity": "2년",
        "initial_offering_amount": offering,
        "demand_participation_amount": participation,
        "competition_ratio": competition,
        "coupon_guidance_band_bp": band,
        "final_spread_bp": final_spread,
        "upsized": upsized,
        "final_issue_amount": final_issue,
    }


# 400억원 모집, 650억원 참여, 경쟁률 1.63배(650/400=1.625), 밴드 ±40bp, 확정 38bp,
# 최종 발행 650억(650/400=1.625배, 0.5~2.5 범위 안).
_CONSISTENT_TRANCHE_1 = _tranche(
    "제26-1회",
    {"value": 400, "unit": "억원"},
    {"value": 650, "unit": "억원"},
    1.63,
    {"low_bp": -40, "high_bp": 40},
    38,
    {"value": 650, "unit": "억원"},
)

# 300억원 모집, 750억원 참여, 경쟁률 2.5배, 밴드 ±60bp, 확정 55bp, 최종 750억.
_CONSISTENT_TRANCHE_2 = _tranche(
    "제26-2회",
    {"value": 300, "unit": "억원"},
    {"value": 750, "unit": "억원"},
    2.5,
    {"low_bp": -60, "high_bp": 60},
    55,
    {"value": 750, "unit": "억원"},
)

_CONSISTENT_FIELDS = {
    "issuer": "가나다전자",
    "credit_rating": "AA-",
    "tranches": [_CONSISTENT_TRANCHE_1, _CONSISTENT_TRANCHE_2],
}


def test_to_eok_won_converts_units_correctly():
    assert _to_eok_won({"value": 210, "unit": "억원"}) == 210.0
    assert _to_eok_won({"value": 21_000_000_000, "unit": "원"}) == 210.0  # 실제 사고 재현: 이게 21억이 아니라 210억이어야 함
    assert _to_eok_won({"value": 21000, "unit": "백만원"}) == 210.0
    assert _to_eok_won(None) is None
    assert _to_eok_won({"value": None, "unit": "억원"}) is None
    assert _to_eok_won({"value": 100, "unit": "알수없는단위"}) is None


def test_extract_filing_converts_amounts_in_code_not_llm():
    """LLM이 억원으로 환산하지 않고 원문 그대로(원 단위)를 줘도, 코드가 정확히
    억원으로 환산해야 한다 - 실제로 있었던 10배 축소 사고(21억 vs 210억) 재현."""
    tranche = _tranche(
        "제155-1회",
        {"value": 21_000_000_000, "unit": "원"},  # "이백일십억원 (₩21,000,000,000)"
        None,
        None,
        None,
        None,
        {"value": 21_000_000_000, "unit": "원"},
    )
    backend = _mock_backend_returning({"issuer": "대한전선", "credit_rating": None, "tranches": [tranche]})

    result = extract_filing("가짜 원문 텍스트", backend)

    t = result["tranches"][0]
    assert t["initial_offering_amount_billion_won"] == 210.0
    assert t["initial_offering_amount_raw"] == {"value": 21_000_000_000, "unit": "원"}
    assert t["final_issue_amount_billion_won"] == 210.0


def test_extract_filing_returns_multiple_tranches_with_validation():
    backend = _mock_backend_returning(_CONSISTENT_FIELDS)
    result = extract_filing("가짜 원문 텍스트", backend)

    assert result["issuer"] == "가나다전자"
    assert result["credit_rating"] == "AA-"
    assert len(result["tranches"]) == 2
    assert result["tranches"][0]["series_label"] == "제26-1회"
    assert result["tranches"][0]["initial_offering_amount_billion_won"] == 400.0
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


def test_extract_filing_flags_final_issue_amount_ratio_out_of_range():
    tranche1 = dict(_CONSISTENT_TRANCHE_1)
    # 모집 400억인데 최종 발행이 2000억 (5배) - 상식적 범위(0.5~2.5배) 밖.
    tranche1["final_issue_amount"] = {"value": 2000, "unit": "억원"}
    fields = {"issuer": "가나다전자", "credit_rating": "AA-", "tranches": [tranche1]}
    backend = _mock_backend_returning(fields)

    result = extract_filing("가짜 원문 텍스트", backend)

    flags = result["tranches"][0]["_validation"]["flags"]
    assert len(flags) == 1
    assert "최종발행금액/모집금액 비율" in flags[0]


def test_extract_filing_single_tranche_when_only_one_series():
    fields = {"issuer": "라마바건설", "credit_rating": "BBB+", "tranches": [_CONSISTENT_TRANCHE_1]}
    backend = _mock_backend_returning(fields)

    result = extract_filing("가짜 원문 텍스트", backend)
    assert len(result["tranches"]) == 1


def test_extract_filing_no_flags_when_values_are_null():
    tranche1 = dict(_CONSISTENT_TRANCHE_1)
    tranche1["competition_ratio"] = None
    tranche1["demand_participation_amount"] = None
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


# --- 영구 캐시 (data/extracted/{rcept_no}.json) 테스트 ---------------------


def test_load_cached_extraction_returns_none_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_module, "EXTRACTED_CACHE_DIR", tmp_path)
    assert load_cached_extraction("99999999999999") is None


def test_save_and_load_cached_extraction_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_module, "EXTRACTED_CACHE_DIR", tmp_path)
    result = {"issuer": "가나다전자", "credit_rating": "AA-", "tranches": []}

    save_cached_extraction("20260921000241", result)
    loaded = load_cached_extraction("20260921000241")

    assert loaded == result
    assert (tmp_path / "20260921000241.json").exists()


def test_load_cached_extraction_invalidated_by_prompt_version_change(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_module, "EXTRACTED_CACHE_DIR", tmp_path)
    save_cached_extraction("1", {"issuer": "구버전", "tranches": []})

    # 프롬프트 버전이 올라간 것처럼 시뮬레이션.
    monkeypatch.setattr(extract_module, "EXTRACT_PROMPT_VERSION", "999-different")

    assert load_cached_extraction("1") is None


def test_extract_filing_cached_second_call_does_not_touch_backend(tmp_path, monkeypatch):
    """캐시가 있으면 backend.create_message를 전혀 호출하지 않는다."""
    monkeypatch.setattr(extract_module, "EXTRACTED_CACHE_DIR", tmp_path)

    backend1 = _mock_backend_returning(_CONSISTENT_FIELDS)
    result1 = extract_filing_cached("20260921000241", "원문", backend1)
    assert backend1.call_count == 1

    # 두 번째 호출: 스크립트가 빈 백엔드 - 호출되면 즉시 RuntimeError.
    backend2 = MockLLMBackend([])
    result2 = extract_filing_cached("20260921000241", "원문", backend2)
    assert backend2.call_count == 0
    assert result2 == result1


def test_extract_filing_cached_stores_current_prompt_version(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_module, "EXTRACTED_CACHE_DIR", tmp_path)
    backend = _mock_backend_returning(_CONSISTENT_FIELDS)

    extract_filing_cached("20260921000404", "원문", backend)

    import json

    payload = json.loads((tmp_path / "20260921000404.json").read_text(encoding="utf-8"))
    assert payload["extract_prompt_version"] == EXTRACT_PROMPT_VERSION
    assert payload["rcept_no"] == "20260921000404"
