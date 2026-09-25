"""agent/tools_schema.py 단위 테스트: 실제 API/모델 호출 없이 스키마와 dispatch를 검증한다."""

from __future__ import annotations

import pytest

from agent import tools_schema
from agent.tools_schema import TOOLS, dispatch


def test_tools_have_expected_names_and_required_fields():
    by_name = {t["name"]: t for t in TOOLS}
    assert set(by_name.keys()) == {
        "get_market_snapshot",
        "get_anomalies",
        "get_us_yields",
        "search_news",
        "get_bond_demand_forecasts",
        "get_credit_snapshot",
        "get_issuance_market_summary",
        "get_ktb_supply",
    }
    assert by_name["get_market_snapshot"]["input_schema"]["required"] == ["date"]
    assert by_name["get_anomalies"]["input_schema"]["required"] == ["date"]
    assert by_name["get_us_yields"]["input_schema"]["required"] == ["start_date", "end_date"]
    assert by_name["search_news"]["input_schema"]["required"] == ["query"]
    assert by_name["get_bond_demand_forecasts"]["input_schema"]["required"] == ["date"]
    assert by_name["get_credit_snapshot"]["input_schema"]["required"] == ["date"]
    assert by_name["get_issuance_market_summary"]["input_schema"]["required"] == ["date"]
    assert by_name["get_ktb_supply"]["input_schema"]["required"] == ["month"]
    # days/window_business_days는 선택 항목이어야 한다 (required에 없음).
    assert "days" not in by_name["search_news"]["input_schema"]["required"]
    assert "days" not in by_name["get_bond_demand_forecasts"]["input_schema"]["required"]
    assert (
        "window_business_days"
        not in by_name["get_issuance_market_summary"]["input_schema"]["required"]
    )


def test_dispatch_get_market_snapshot_merges_daily_changes_and_spreads(monkeypatch):
    monkeypatch.setattr(tools_schema.curve, "calc_daily_changes", lambda date: {"as_of_date": date, "kind": "changes"})
    monkeypatch.setattr(tools_schema.curve, "calc_spreads", lambda date: {"as_of_date": date, "kind": "spreads"})

    result = dispatch("get_market_snapshot", {"date": "2026-09-23"})

    assert result["daily_changes"]["kind"] == "changes"
    assert result["spreads"]["kind"] == "spreads"


def test_dispatch_get_anomalies_calls_flag_anomalies(monkeypatch):
    seen = {}

    def fake(date):
        seen["date"] = date
        return {"as_of_date": date}

    monkeypatch.setattr(tools_schema.curve, "flag_anomalies", fake)
    result = dispatch("get_anomalies", {"date": "2026-09-23"})
    assert seen["date"] == "2026-09-23"
    assert result["as_of_date"] == "2026-09-23"


def test_dispatch_get_us_yields_calls_fred(monkeypatch):
    seen = {}

    def fake(start_date, end_date):
        seen["args"] = (start_date, end_date)
        return {"source": "FRED"}

    monkeypatch.setattr(tools_schema.fred, "get_us_yields", fake)
    result = dispatch("get_us_yields", {"start_date": "2026-09-01", "end_date": "2026-09-23"})
    assert seen["args"] == ("2026-09-01", "2026-09-23")
    assert result["source"] == "FRED"


def test_dispatch_search_news_defaults_days_to_one(monkeypatch):
    seen = {}

    def fake(query, days=1):
        seen["query"] = query
        seen["days"] = days
        return []

    monkeypatch.setattr(tools_schema.news, "search_news", fake)
    dispatch("search_news", {"query": "기준금리"})
    assert seen["days"] == 1

    dispatch("search_news", {"query": "기준금리", "days": 7})
    assert seen["days"] == 7


def test_dispatch_get_credit_snapshot_uses_default_lookback(monkeypatch):
    seen = {}

    def fake(date, lookback_days):
        seen["args"] = (date, lookback_days)
        return {"as_of_date": date}

    monkeypatch.setattr(tools_schema.credit_analytics, "calc_credit_context", fake)
    result = dispatch("get_credit_snapshot", {"date": "2026-09-23"})
    assert seen["args"] == ("2026-09-23", tools_schema._DEFAULT_CREDIT_LOOKBACK_DAYS)
    assert result["as_of_date"] == "2026-09-23"


def test_dispatch_get_issuance_market_summary_defaults_window(monkeypatch):
    seen = {}

    def fake(date, window_business_days):
        seen["args"] = (date, window_business_days)
        return {"as_of_date": date}

    monkeypatch.setattr(tools_schema.issuance_analytics, "calc_issuance_summary", fake)
    dispatch("get_issuance_market_summary", {"date": "2026-09-23"})
    assert seen["args"] == ("2026-09-23", tools_schema._DEFAULT_ISSUANCE_WINDOW_BUSINESS_DAYS)

    dispatch("get_issuance_market_summary", {"date": "2026-09-23", "window_business_days": 10})
    assert seen["args"] == ("2026-09-23", 10)


def test_dispatch_get_ktb_supply_calls_ecos_fallback(monkeypatch):
    seen = {}

    def fake(month):
        seen["month"] = month
        return {"month": month}

    monkeypatch.setattr(tools_schema.ktb_supply, "get_issuance_plan", fake)
    result = dispatch("get_ktb_supply", {"month": "2026-07"})
    assert seen["month"] == "2026-07"
    assert result["month"] == "2026-07"


def test_dispatch_unknown_tool_raises():
    with pytest.raises(ValueError):
        dispatch("does_not_exist", {})


def test_dispatch_get_bond_demand_forecasts_requires_backend():
    with pytest.raises(RuntimeError):
        dispatch("get_bond_demand_forecasts", {"date": "2026-09-23"}, backend=None)


def test_dispatch_get_bond_demand_forecasts_wires_dart_and_extract(monkeypatch, tmp_path):
    import agent.extract as extract_module

    monkeypatch.setattr(extract_module, "EXTRACTED_CACHE_DIR", tmp_path)

    seen = {}

    def fake_list_bond_filings(start_date, end_date):
        seen["dates"] = (start_date, end_date)
        return [
            {"rcept_no": "1", "corp_name": "A사", "report_nm": "증권신고서(채무증권)", "rcept_dt": "2026-09-22", "is_correction": False},
            {"rcept_no": "2", "corp_name": "B사", "report_nm": "증권신고서(채무증권)", "rcept_dt": "2026-09-21", "is_correction": False},
        ]

    def fake_fetch_filing_text(rcept_no):
        if rcept_no == "1":
            return {"rcept_no": rcept_no, "full_text_length": 100, "demand_forecast_section": "수요예측 내용..."}
        return {"rcept_no": rcept_no, "full_text_length": 50, "demand_forecast_section": None}

    monkeypatch.setattr(tools_schema.dart, "list_bond_filings", fake_list_bond_filings)
    monkeypatch.setattr(tools_schema.dart, "fetch_filing_text", fake_fetch_filing_text)
    monkeypatch.setattr(extract_module, "extract_filing", lambda text, backend: {"issuer": "A사", "_validation": {"flags": []}})

    result = dispatch("get_bond_demand_forecasts", {"date": "2026-09-23", "days": 3}, backend=object())

    assert seen["dates"] == ("2026-09-20", "2026-09-23")
    assert len(result) == 2
    # rcept_dt 내림차순 정렬 -> rcept_no="1"(2026-09-22)이 먼저.
    assert result[0]["rcept_no"] == "1"
    assert result[0]["extraction"]["issuer"] == "A사"
    assert result[1]["rcept_no"] == "2"
    assert result[1]["extraction"] is None
    assert result[1]["note"] == "수요예측 섹션을 찾지 못함"


def test_get_bond_demand_forecasts_caps_new_extractions_but_not_cached(monkeypatch, tmp_path):
    """비용 보호: 캐시에 없는("신규") 필링만 max_new_extractions건까지 추출한다.
    이미 캐시된 건은 이 상한과 무관하게 전부 결과에 포함되어야 한다.

    2026-09-11 기준 실제 실행에서 5일 조회에 22건이 잡혀 LLM 호출 22회를
    유발한 것을 발견하고 추가한 안전장치."""
    import agent.extract as extract_module

    monkeypatch.setattr(extract_module, "EXTRACTED_CACHE_DIR", tmp_path)

    many_filings = [
        {"rcept_no": str(i), "corp_name": f"{i}사", "report_nm": "증권신고서(채무증권)", "rcept_dt": f"2026-09-{10 + i:02d}", "is_correction": False}
        for i in range(20)
    ]
    monkeypatch.setattr(tools_schema.dart, "list_bond_filings", lambda start, end: many_filings)
    monkeypatch.setattr(
        tools_schema.dart,
        "fetch_filing_text",
        lambda rcept_no: {"rcept_no": rcept_no, "full_text_length": 100, "demand_forecast_section": "내용"},
    )

    call_count = {"n": 0}

    def fake_extract(text, backend):
        call_count["n"] += 1
        return {"issuer": "테스트", "_validation": {"flags": []}}

    monkeypatch.setattr(extract_module, "extract_filing", fake_extract)

    # 미리 3건을 "캐시됨" 상태로 만들어 둔다 - 이 3건은 상한과 무관하게 결과에 포함되어야 한다.
    for i in [0, 1, 2]:
        extract_module.save_cached_extraction(str(i), {"issuer": f"캐시된{i}사", "_validation": {"flags": []}})

    result = tools_schema.get_bond_demand_forecasts("2026-09-23", 30, object())

    cached_results = [r for r in result if r["rcept_no"] in ("0", "1", "2")]
    assert len(cached_results) == 3
    assert all(r["extraction"]["issuer"].startswith("캐시된") for r in cached_results)

    # 캐시 안 된 17건 중 신규 추출은 5건까지만.
    newly_extracted = [r for r in result if r["extraction"] is not None and not r["extraction"]["issuer"].startswith("캐시된")]
    skipped = [r for r in result if r["extraction"] is None]
    assert call_count["n"] == tools_schema._MAX_NEW_EXTRACTIONS_PER_CALL
    assert len(newly_extracted) == tools_schema._MAX_NEW_EXTRACTIONS_PER_CALL
    assert len(skipped) == 20 - 3 - tools_schema._MAX_NEW_EXTRACTIONS_PER_CALL
    assert all("상한" in r["note"] for r in skipped)


def test_get_bond_demand_forecasts_sorted_by_rcept_dt_descending(monkeypatch, tmp_path):
    import agent.extract as extract_module

    monkeypatch.setattr(extract_module, "EXTRACTED_CACHE_DIR", tmp_path)

    filings = [
        {"rcept_no": "1", "corp_name": "먼저접수", "report_nm": "증권신고서(채무증권)", "rcept_dt": "2026-09-10", "is_correction": False},
        {"rcept_no": "2", "corp_name": "나중접수", "report_nm": "증권신고서(채무증권)", "rcept_dt": "2026-09-20", "is_correction": False},
    ]
    monkeypatch.setattr(tools_schema.dart, "list_bond_filings", lambda start, end: filings)
    monkeypatch.setattr(
        tools_schema.dart,
        "fetch_filing_text",
        lambda rcept_no: {"rcept_no": rcept_no, "full_text_length": 10, "demand_forecast_section": "내용"},
    )
    monkeypatch.setattr(extract_module, "extract_filing", lambda text, backend: {"issuer": "테스트", "_validation": {"flags": []}})

    result = tools_schema.get_bond_demand_forecasts("2026-09-23", 30, object())

    assert [r["corp_name"] for r in result] == ["나중접수", "먼저접수"]


def test_get_bond_demand_forecasts_second_call_same_date_makes_zero_llm_calls(monkeypatch, tmp_path):
    """같은 검색 기간으로 두 번째 실행하면, 첫 실행에서 캐시된 결과 덕에
    실제 LLM(backend.create_message) 호출이 0회여야 한다."""
    import agent.extract as extract_module

    monkeypatch.setattr(extract_module, "EXTRACTED_CACHE_DIR", tmp_path)

    filings = [
        {"rcept_no": "1", "corp_name": "A사", "report_nm": "증권신고서(채무증권)", "rcept_dt": "2026-09-22", "is_correction": False},
        {"rcept_no": "2", "corp_name": "B사", "report_nm": "증권신고서(채무증권)", "rcept_dt": "2026-09-21", "is_correction": False},
    ]
    monkeypatch.setattr(tools_schema.dart, "list_bond_filings", lambda start, end: filings)
    monkeypatch.setattr(
        tools_schema.dart,
        "fetch_filing_text",
        lambda rcept_no: {"rcept_no": rcept_no, "full_text_length": 10, "demand_forecast_section": "내용"},
    )

    from agent.extract import EXTRACTION_TOOL
    from agent.llm_backend import LLMResponse, MockLLMBackend, ToolUseBlock

    def make_scripted_backend(n: int) -> MockLLMBackend:
        script = [
            LLMResponse(
                content=[ToolUseBlock(id=f"x{i}", name=EXTRACTION_TOOL["name"], input={"issuer": "테스트", "credit_rating": None, "tranches": []})],
                stop_reason="tool_use",
            )
            for i in range(n)
        ]
        return MockLLMBackend(script)

    backend1 = make_scripted_backend(2)
    result1 = tools_schema.get_bond_demand_forecasts("2026-09-23", 5, backend1)
    assert backend1.call_count == 2  # 둘 다 신규 추출이라 LLM 호출 2회

    # 두 번째 실행: 스크립트가 빈 MockLLMBackend를 줘서, 호출되는 즉시 실패하게 만든다.
    backend2 = MockLLMBackend([])
    result2 = tools_schema.get_bond_demand_forecasts("2026-09-23", 5, backend2)
    assert backend2.call_count == 0

    assert [r["extraction"] for r in result1] == [r["extraction"] for r in result2]
