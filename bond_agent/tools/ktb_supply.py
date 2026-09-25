"""국고채 발행 규모 (ECOS 191Y001 기반 fallback).

1차 후보였던 기획재정부 국채시장 공식 사이트(ktb.moef.go.kr/bidResult.do)는
페이지가 이름 없는 범용 JS로 데이터를 주입하는 방식이라 1.5시간 타임박스
안에 실제 데이터 엔드포인트를 특정하지 못했다. data.go.kr의
"기획재정부_국채시장_국고채입찰결과"는 fileData 유형이라 별도 API 키
등록이 필요해 보류했다 (새 자격증명 발급은 이번 작업 범위 밖).

fallback으로 ECOS 통계표 191Y001("주요 국공채 발행액/잔액") 월별 항목
0200000(국고채권)을 쓴다. 이 표는 월별 발행액/잔액만 제공하고 개별
입찰의 낙찰금리/응찰률/응찰금액은 없다. 따라서 이 모듈은
get_issuance_plan(month)만 제공하고, get_auction_results(start, end)는
구현하지 않는다 (WORK_LOG.md에 기록된 타임박스 fallback 결정).

순수 함수: 입력은 월 문자열, 출력은 JSON 직렬화 가능한 dict.
"""

from __future__ import annotations

import time

import requests

from bond_agent.config import ECOS_API_KEY, ECOS_BASE_URL
from bond_agent.tools._cache import cached_call

_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 1.5

KTB_TABLE_CODE = "191Y001"
KTB_ITEM_CODE = "0200000"  # 국고채권, 월별, 십억원


def _check_configured() -> None:
    if not ECOS_API_KEY:
        raise RuntimeError("ECOS_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")


def _request_rows(start_month: str, end_month: str) -> list[dict]:
    url = (
        f"{ECOS_BASE_URL}/StatisticSearch/{ECOS_API_KEY}/json/kr/1/1000/"
        f"{KTB_TABLE_CODE}/M/{start_month}/{end_month}/{KTB_ITEM_CODE}"
    )
    last_error: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            if "StatisticSearch" not in payload:
                result = payload.get("RESULT", {})
                if result.get("CODE") == "INFO-200":
                    # 요청 구간 전체에 데이터가 없음 (예: 미래 월) - 정상적인 빈 결과.
                    return []
                raise RuntimeError(
                    f"ECOS API 오류 (table={KTB_TABLE_CODE}, item={KTB_ITEM_CODE}): "
                    f"{result.get('CODE', '?')} {result.get('MESSAGE', payload)}"
                )
            return payload["StatisticSearch"].get("row", [])
        except (requests.exceptions.RequestException, RuntimeError) as exc:
            last_error = exc
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"ECOS API 호출이 {_MAX_RETRIES}번 모두 실패했습니다: {last_error}") from last_error


def _fetch_monthly_series(start_month: str, end_month: str) -> dict[str, float]:
    """{"YYYY-MM": 십억원 float} 시계열."""
    rows = _request_rows(start_month.replace("-", ""), end_month.replace("-", ""))
    series: dict[str, float] = {}
    for row in rows:
        raw_month = row.get("TIME", "")
        value = row.get("DATA_VALUE")
        if not raw_month or len(raw_month) != 6 or value in (None, "", "-"):
            continue
        try:
            numeric_value = float(value)
        except ValueError:
            continue
        iso_month = f"{raw_month[0:4]}-{raw_month[4:6]}"
        series[iso_month] = numeric_value
    return series


def get_issuance_plan(month: str) -> dict:
    """국고채권 월별 발행액과 전년 동월 대비 비교.

    이 ECOS 표에는 "계획(plan)" 수치가 따로 없고 실제 발행액(실적)만 있어,
    스펙에서 말한 "월 발행계획 대비 누적 발행 진행률" 대신 "전년 동월 대비
    발행액 증감"으로 대체했다 (WORK_LOG.md에 기록된 판단).

    Args:
        month: "YYYY-MM"

    Returns:
        {
          "source": "ECOS",
          "table_code": "191Y001",
          "item_code": "0200000",
          "month": "YYYY-MM",
          "issuance_billion_won": float | None,
          "prior_year_month": "YYYY-MM",
          "prior_year_issuance_billion_won": float | None,
          "yoy_change_pct": float | None,
          "note": "..."
        }
    """
    _check_configured()
    cache_key = f"issuance_plan:{month}"

    def _fetch() -> dict:
        year_str, mon_str = month.split("-")
        prior_year_month = f"{int(year_str) - 1}-{mon_str}"
        start_month = min(prior_year_month, month)
        end_month = max(prior_year_month, month)
        series = _fetch_monthly_series(start_month, end_month)

        current = series.get(month)
        prior = series.get(prior_year_month)
        yoy_change_pct: float | None = None
        if current is not None and prior is not None and prior != 0:
            yoy_change_pct = round((current - prior) / prior * 100, 2)

        return {
            "source": "ECOS",
            "table_code": KTB_TABLE_CODE,
            "item_code": KTB_ITEM_CODE,
            "month": month,
            "issuance_billion_won": current,
            "prior_year_month": prior_year_month,
            "prior_year_issuance_billion_won": prior,
            "yoy_change_pct": yoy_change_pct,
            "note": (
                "ECOS 191Y001은 월별 발행 실적만 제공하며 발행계획 수치는 없음. "
                "낙찰금리/응찰률 등 개별 입찰 상세는 이 fallback에 없음 "
                "(get_auction_results는 구현하지 않음, WORK_LOG.md 참고)."
            ),
        }

    return cached_call("ktb_supply", cache_key, _fetch)
