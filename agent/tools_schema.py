"""Anthropic Messages API용 tool 정의(JSON Schema) + 디스패치.

기존 bond_agent.tools / bond_agent.analytics 의 순수 함수를 그대로 호출해서
결과를 반환한다. 이 파일도 숫자를 계산하지 않는다 - 여러 함수의 결과를
합쳐서 하나의 dict로 묶는 것("get_market_snapshot")까지만 하고, 값 자체는
절대 건드리지 않는다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from bond_agent.analytics import curve
from bond_agent.tools import dart, fred, news

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_market_snapshot",
        "description": (
            "지정한 한국 영업일의 채권시장 스냅샷을 반환한다. 국고채(기준금리, 3/5/10/30년) "
            "및 회사채(AA-/BBB- 3년)의 레벨과 전일 대비 변동(bp), 그리고 국고 3/10·10/30 "
            "커브 스프레드, 크레딧 스프레드(AA-/BBB-, 국고3년 대비), 한미 10년 금리차의 "
            "레벨(bp)과 전일 대비 변동(bp)을 모두 포함한다. 모닝 브리핑 작성 시 가장 먼저 "
            "호출해야 하는 툴이다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "기준일 YYYY-MM-DD (ECOS 상 한국 영업일이어야 함)",
                },
            },
            "required": ["date"],
        },
    },
    {
        "name": "get_anomalies",
        "description": (
            "지정한 날짜의 각 지표(국고채 금리, 회사채 금리, 커브/크레딧/한미 스프레드)에 "
            "대해 일간 변동을 최근 60영업일 변동의 표준편차로 나눈 z-score를 계산하고, "
            "|z|>2인 지표를 이상치(is_anomaly=true)로 표시한다. 브리핑의 '특이사항' "
            "섹션을 쓰기 전에 반드시 호출해야 한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "기준일 YYYY-MM-DD"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "get_us_yields",
        "description": (
            "미국 국채 수익률(DGS2, DGS10) 및 SOFR의 일별 시계열을 FRED에서 가져온다. "
            "get_market_snapshot에 이미 한미 10년 금리차가 포함되어 있으므로, 미국 금리 "
            "자체의 추이를 별도로 설명해야 할 때만 호출한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "search_news",
        "description": (
            "연합뉴스/한국경제 경제 뉴스에서 최근 N일 안에 제목 또는 요약에 검색어가 "
            "들어간 기사를 찾는다(제목+요약만, 본문 전체는 제공하지 않음). 이상치가 "
            "발견되었을 때 원인이 될 만한 뉴스를 찾거나, 당일/주간 주목할 이벤트를 "
            "확인할 때 사용한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "검색어. 공백으로 여러 단어를 넣으면 그중 하나라도 매칭되면 포함됨",
                },
                "days": {
                    "type": "integer",
                    "description": "오늘로부터 며칠 전까지 볼지 (생략 시 1일)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_bond_demand_forecasts",
        "description": (
            "최근 N일 이내 회사채 증권신고서(채무증권) 공시에서 수요예측 결과(발행사, "
            "신용등급, 경쟁률, 참여금액, 공모희망금리 밴드, 확정 가산금리, 증액 여부, "
            "최종 발행금액)를 추출한다. OpenDART 공시 원문을 LLM으로 구조화 추출한 뒤 "
            "경쟁률/가산금리를 코드로 검증한 값이며, 검증에 실패한 항목은 _validation.flags에 "
            "표시되어 있다. '크레딧 발행시장' 섹션을 쓸 때 사용한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "기준일 YYYY-MM-DD"},
                "days": {
                    "type": "integer",
                    "description": "기준일로부터 며칠 전까지 공시를 볼지 (생략 시 5일)",
                },
            },
            "required": ["date"],
        },
    },
]


def get_bond_demand_forecasts(date: str, days: int, backend: Any) -> list[dict[str, Any]]:
    """최근 days일 내 회사채 수요예측 공시를 찾아 각각 원문을 받고 LLM으로 구조화 추출한다.

    공시별로 fetch_filing_text (원문+수요예측 섹션 추출, 코드) ->
    agent.extract.extract_filing (구조화 추출, LLM) 순서로 처리한다.
    """
    from agent.extract import extract_filing  # 지연 임포트: 순환 임포트 방지

    end = dt.date.fromisoformat(date)
    start = end - dt.timedelta(days=days)
    filings = dart.list_bond_filings(start.isoformat(), end.isoformat())

    results: list[dict[str, Any]] = []
    for filing in filings:
        doc = dart.fetch_filing_text(filing["rcept_no"])
        section = doc["demand_forecast_section"]
        if not section:
            results.append({**filing, "extraction": None, "note": "수요예측 섹션을 찾지 못함"})
            continue
        extraction = extract_filing(section, backend)
        results.append({**filing, "extraction": extraction})
    return results


def dispatch(name: str, tool_input: dict[str, Any], backend: Any = None) -> Any:
    """tool_use 블록의 name/input을 받아 해당 함수를 호출하고 결과를 반환한다.

    실패 시 예외를 그대로 올린다 - 호출부(agent/loop.py)가 잡아서 tool_result의
    is_error로 감싼다.

    Args:
        backend: get_bond_demand_forecasts처럼 내부적으로 LLM 추출을 한 번 더
            해야 하는 툴에만 필요 (agent.llm_backend.LLMBackend 구현체).
    """
    if name == "get_market_snapshot":
        date = tool_input["date"]
        return {
            "daily_changes": curve.calc_daily_changes(date),
            "spreads": curve.calc_spreads(date),
        }
    if name == "get_anomalies":
        return curve.flag_anomalies(tool_input["date"])
    if name == "get_us_yields":
        return fred.get_us_yields(tool_input["start_date"], tool_input["end_date"])
    if name == "search_news":
        days = tool_input.get("days", 1)
        return news.search_news(tool_input["query"], days=days)
    if name == "get_bond_demand_forecasts":
        if backend is None:
            raise RuntimeError("get_bond_demand_forecasts는 LLM backend가 필요합니다.")
        days = tool_input.get("days", 5)
        return get_bond_demand_forecasts(tool_input["date"], days, backend)
    raise ValueError(f"알 수 없는 툴: {name}")
