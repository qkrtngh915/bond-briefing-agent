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
    }
    assert by_name["get_market_snapshot"]["input_schema"]["required"] == ["date"]
    assert by_name["get_anomalies"]["input_schema"]["required"] == ["date"]
    assert by_name["get_us_yields"]["input_schema"]["required"] == ["start_date", "end_date"]
    assert by_name["search_news"]["input_schema"]["required"] == ["query"]
    assert by_name["get_bond_demand_forecasts"]["input_schema"]["required"] == ["date"]
    # days는 선택 항목이어야 한다 (required에 없음).
    assert "days" not in by_name["search_news"]["input_schema"]["required"]
    assert "days" not in by_name["get_bond_demand_forecasts"]["input_schema"]["required"]


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


def test_dispatch_unknown_tool_raises():
    with pytest.raises(ValueError):
        dispatch("does_not_exist", {})


def test_dispatch_get_bond_demand_forecasts_requires_backend():
    with pytest.raises(RuntimeError):
        dispatch("get_bond_demand_forecasts", {"date": "2026-09-23"}, backend=None)


def test_dispatch_get_bond_demand_forecasts_wires_dart_and_extract(monkeypatch):
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

    import agent.extract as extract_module

    monkeypatch.setattr(extract_module, "extract_filing", lambda text, backend: {"issuer": "A사", "_validation": {"flags": []}})

    result = dispatch("get_bond_demand_forecasts", {"date": "2026-09-23", "days": 3}, backend=object())

    assert seen["dates"] == ("2026-09-20", "2026-09-23")
    assert len(result) == 2
    assert result[0]["extraction"]["issuer"] == "A사"
    assert result[1]["extraction"] is None
    assert result[1]["note"] == "수요예측 섹션을 찾지 못함"


def test_get_bond_demand_forecasts_caps_extraction_count(monkeypatch):
    """비용 보호: 기간 내 필링이 많아도 max_filings건까지만 실제로 추출한다.

    2026-09-11 기준 실제 실행에서 5일 조회에 22건이 잡혀 LLM 호출 22회를
    유발한 것을 발견하고 추가한 안전장치."""
    many_filings = [
        {"rcept_no": str(i), "corp_name": f"{i}사", "report_nm": "증권신고서(채무증권)", "rcept_dt": "2026-09-2" + str(i % 3), "is_correction": False}
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

    import agent.extract as extract_module

    monkeypatch.setattr(extract_module, "extract_filing", fake_extract)

    result = tools_schema.get_bond_demand_forecasts("2026-09-23", 5, object())

    assert len(result) == tools_schema._MAX_FORECAST_FILINGS
    assert call_count["n"] == tools_schema._MAX_FORECAST_FILINGS
