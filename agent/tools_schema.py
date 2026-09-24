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
            "[선택 - 필요할 때만] 미국 국채 수익률(DGS2, DGS10) 및 SOFR의 일별 시계열을 "
            "FRED에서 가져온다. get_market_snapshot에 이미 한미 10년 금리차가 포함되어 "
            "있으므로, 한미 금리차 쪽에 이상치가 있어서 미국 금리 레벨을 직접 보여줘야 "
            "할 때만 호출한다. 평범한 날에는 호출하지 않아도 된다."
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
            "[선택 - 이상치가 있을 때만] 연합뉴스/한국경제 경제 뉴스에서 최근 N일 안에 "
            "제목 또는 요약에 검색어가 들어간 기사를 찾는다(제목+요약만, 본문 전체는 "
            "제공하지 않음). get_anomalies에서 |z|>2인 지표를 하나도 못 찾았으면 호출하지 "
            "말 것 - 조용한 날에 억지로 뉴스를 찾을 필요는 없다. 이상치가 있을 때는 그 "
            "지표 종류에 맞는 구체적인 검색어를 쓴다 (국고채 커브 이상치 → '국고채'/'국채 "
            "금리', 크레딧 스프레드 이상치 → '크레딧 스프레드'/'회사채', 한미 금리차 "
            "이상치 → '미국 국채'/'연준'/'FOMC')."
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
            "[섹션 6 작성 시 매번 호출 - 선택 아님] 최근 N일 이내 회사채 증권신고서"
            "(채무증권) 공시에서 수요예측 결과(발행사, 신용등급, 경쟁률, 참여금액, "
            "공모희망금리 밴드, 확정 가산금리, 증액 여부, 최종 발행금액)를 추출한다. "
            "OpenDART 공시 원문을 LLM으로 구조화 추출한 뒤 경쟁률/가산금리를 코드로 "
            "검증한 값이며, 검증에 실패한 항목은 _validation.flags에 표시되어 있다. "
            "'크레딧 발행시장' 섹션을 쓰기 전에는 이상치 유무와 무관하게 항상 호출해야 "
            "한다 - 호출 없이 '최근 발행 없음'이라고 쓰면 안 된다. 실제로 빈 리스트가 "
            "나오면 그때 '최근 회사채 수요예측 공시 없음'으로 짧게 쓴다. 비용 보호를 "
            "위해 기간 내 최신 5건까지만 추출한다 (기간에 더 많은 공시가 있어도 5건까지)."
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


_MAX_FORECAST_FILINGS = 5


def get_bond_demand_forecasts(
    date: str, days: int, backend: Any, max_filings: int = _MAX_FORECAST_FILINGS
) -> list[dict[str, Any]]:
    """최근 days일 내 회사채 수요예측 공시를 찾아 각각 원문을 받고 LLM으로 구조화 추출한다.

    공시별로 fetch_filing_text (원문+수요예측 섹션 추출, 코드) ->
    agent.extract.extract_filing (구조화 추출, LLM) 순서로 처리한다.

    비용 주의: extract_filing 호출은 필링 1건당 실제 LLM(Anthropic) 호출 1회다.
    days=5 기본값 기준으로도 검색 기간에 회사채 증권신고서/정정신고서가 20건
    넘게 잡히는 경우가 실제로 있었다 (2026-09-11 기준 5일 조회에 22건) -
    그대로 두면 브리핑 1회가 LLM 호출 20회 이상을 유발한다. 그래서 최신
    max_filings건만 추출한다 (기본 5건). 매일 도는 GitHub Actions 워크플로가
    이 비용을 반복적으로 발생시킨다는 점을 감안해서 비용을 제한하는 것.
    """
    from agent.extract import extract_filing  # 지연 임포트: 순환 임포트 방지

    end = dt.date.fromisoformat(date)
    start = end - dt.timedelta(days=days)
    filings = dart.list_bond_filings(start.isoformat(), end.isoformat())[:max_filings]

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
